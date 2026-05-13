"""Lithe Audio speaker protocol client."""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import ssl
import struct
from dataclasses import dataclass, field
from typing import Callable, Optional

from .const import (
    DEFAULT_PORT, MB_AUDIOCUE, MB_BLUETOOTH, MB_BROWSE, MB_BT_STATUS, MB_CHIME,
    MB_DEVICE_INFO, MB_DEVICE_NAME, MB_DSP, MB_FACTORY_RESET, MB_FAVOURITES,
    MB_FIRMWARE, MB_INTERFACE_IP, MB_MUTE, MB_NETWORK_INFO, MB_NETWORK_STATUS,
    MB_NOW_PLAYING, MB_PLAY_STATE, MB_PLAYBACK_AUTH, MB_POSITION, MB_REBOOT_REQ,
    MB_REGISTER, MB_RSSI, MB_SOURCE, MB_TIMEZONE, MB_TRANSPORT, MB_VOLUME,
    MUTE_OFF, MUTE_ON, NETWORK_STATUS, PLAY_STATES, SOURCES, TRANSPORT_NEXT,
    TRANSPORT_PAUSE, TRANSPORT_PLAY, TRANSPORT_PREV, TRANSPORT_RESUME,
    TRANSPORT_STOP,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class SpeakerState:
    """Current state of a Lithe Audio speaker."""
    name: str = ""
    firmware: str = ""
    model: str = ""
    mac: str = ""
    wifi_band: str = ""
    timezone: str = ""
    cast_version: str = ""
    net_mode: str = ""

    # Network — populated from MB#123, MB#124, MB#151, MB#208(READ_ssid)
    ip_address: str = ""        # IP from MB#123 (Wlan or Eth interface)
    network_interface: str = "" # "Wlan" / "Eth"
    network_status: str = ""    # "WLAN" / "Ethernet" / "P2P" / "WAC/SAC/LS-Connect"
    wifi_rssi_dbm: int = 0      # RSSI in dBm (negative number, e.g. -55)
    ssid: str = ""              # Connected SSID (from NV item)
    speaker_status: str = ""    # "Standby" / "Connected" / "Active" etc

    # Playback
    play_state: str = "stopped"
    source_id: int = 0
    volume: int = 50
    muted: bool = False
    position_ms: int = 0
    position_updated_at: float = 0.0   # asyncio loop time when MB#49 last seen

    # Now playing
    title: str = ""
    artist: str = ""
    album: str = ""
    artwork_url: str = ""
    duration_ms: int = 0
    is_live: bool = False  # True for radio/AirPlay streams (no SEEK)
    shuffle: bool = False
    repeat: str = "off"  # "off" | "all" | "one"

    # Bluetooth
    bt_status: str = ""

    # Favourites
    favourites: list = field(default_factory=list)  # [{slot:int, name:str}]

    # Connection
    connected: bool = False

    @property
    def source_name(self) -> str:
        return SOURCES.get(self.source_id, f"Source {self.source_id}")


class LitheClient:
    """Asyncio client for the Lithe Audio speaker API (port 7777, LS10 = TLS)."""

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        use_tls: bool = True,
        cert_path: Optional[str] = None,
        key_path: Optional[str] = None,
        local_ip: str = "127.0.0.1",
    ) -> None:
        self.host = host
        self.port = port
        self.use_tls = use_tls
        self.cert_path = cert_path
        self.key_path = key_path
        self.local_ip = local_ip

        self.state = SpeakerState()
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._buf = b""
        self._read_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._callbacks: list[Callable] = []
        self._last_rx_time: float = 0.0
        self._last_chime_time: float = 0.0
        self._last_chime_mbid: int = 0
        # Track every RemoteID seen on the socket for diagnostic purposes
        # (multiple RemoteIDs can come from one speaker depending on source)
        self._seen_remote_ids: set[int] = set()
        # NV item being read via MB#208 READ_<item> — cleared on response
        self._pending_nv_read: str | None = None
        # Counter of consecutive resync events — triggers reconnect at 10
        self._resync_count: int = 0

    # ── Connection ─────────────────────────────────────────────────────────

    @staticmethod
    def _build_tls_context(cert_path: str | None, key_path: str | None) -> ssl.SSLContext:
        """Build the TLS context. Runs in an executor — touches the filesystem."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        # IMPORTANT order: disable check_hostname BEFORE lowering verify_mode —
        # PROTOCOL_TLS_CLIENT enables check_hostname by default, and Python
        # raises ValueError if verify_mode is lowered while it's still on.
        ctx.check_hostname = False
        # CERT_NONE: the Lithe speakers use self-signed server certs against
        # the same CA as the client cert, and Python's chain validation
        # rejects self-signed certs even when the CA is in the trust store.
        # Mutual auth is still preserved because we present our client cert.
        ctx.verify_mode = ssl.CERT_NONE
        if cert_path and key_path:
            ctx.load_cert_chain(cert_path, key_path)
        return ctx

    async def async_connect(self) -> None:
        """Open connection and register with the speaker."""
        ctx = None
        if self.use_tls:
            # load_cert_chain is a blocking file read — do it in an executor
            loop = asyncio.get_running_loop()
            ctx = await loop.run_in_executor(
                None, self._build_tls_context, self.cert_path, self.key_path
            )

        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port, ssl=ctx),
            timeout=8.0,
        )

        # Enable TCP keepalive with aggressive timing so we detect a
        # standby/zombie speaker fast (~10s) instead of the Linux default 2h.
        try:
            sock = self._writer.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # These options aren't always supported, wrap each individually
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                except (OSError, AttributeError):
                    pass
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
                except (OSError, AttributeError):
                    pass
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                except (OSError, AttributeError):
                    pass
                # Disable Nagle so chime packets hit the wire immediately
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except (OSError, AttributeError):
                    pass
        except Exception:
            pass

        # Register. Match the Control4 reference driver: send the host's
        # network address as a plain string for ALL platforms (LS9 + LS10).
        # We previously used a JSON {"APP_info": {...}} blob for LS10
        # speakers, but Control4 — which works reliably — uses plain IP
        # everywhere. The JSON format may put the speaker in a different
        # session mode that delays chime processing.
        reg = self.local_ip

        self._writer.write(self._build_packet(0x02, MB_REGISTER, reg))
        await self._writer.drain()
        await asyncio.sleep(0.4)  # speaker needs ~400ms before accepting commands

        self.state.connected = True
        _LOGGER.info("Connected to Lithe Audio speaker at %s:%s", self.host, self.port)

        # Start background reader and the 30-second re-registration heartbeat.
        # The Control4 reference driver re-registers every 30 seconds and
        # re-queries the device name. Without this the speaker eventually
        # demotes our session, causing chime/audio commands to be processed
        # slowly or not at all after idle periods.
        self._read_task = asyncio.create_task(self._read_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Request initial state
        await self.async_refresh()

    async def _heartbeat_loop(self) -> None:
        """Re-register every 30s — mirrors Control4 driver behaviour.

        The speaker treats long socket silence as session demotion. To stay
        in a fully-responsive state we re-send the registration plus a
        device-name GET every 30 seconds, exactly like the proven Control4
        Lua driver does.
        """
        try:
            while self.state.connected and self._writer:
                await asyncio.sleep(30.0)
                if not self.state.connected or not self._writer or self._writer.is_closing():
                    break
                try:
                    # Re-register with plain IP (matches Control4 driver)
                    self._writer.write(self._build_packet(0x02, MB_REGISTER, self.local_ip))
                    # Refresh device name (Control4 does this too)
                    self._writer.write(self._build_packet(0x01, MB_DEVICE_NAME, ""))
                    _LOGGER.debug("Heartbeat: re-registered with speaker")
                except Exception as e:
                    _LOGGER.debug("Heartbeat write failed: %s", e)
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            _LOGGER.debug("Heartbeat loop ended: %s", e)

    async def async_disconnect(self) -> None:
        """Disconnect from speaker."""
        self.state.connected = False
        self._seen_remote_ids.clear()
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
        if getattr(self, "_heartbeat_task", None) and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    # ── State refresh ──────────────────────────────────────────────────────

    async def async_refresh(self) -> None:
        """Request all state from speaker.

        NOTE: empirically this firmware responds to GETs on Tx_-only
        mailboxes (MB#42 Now Playing, MB#50 Source, MB#51 Play State,
        MB#49 Position, MB#63 Mute, MB#210 BT Status) even though the
        spec marks them as push-only. We send them because they're the
        only way to get the speaker's current state on connect or after
        a stale period — the spec-only "push on change" never fires if
        nothing changed.

        We previously tried removing these per a strict spec read and it
        broke track info / source display / play state, so they stay.
        """
        # Standard refresh — speaker responds to all of these
        for mb in (MB_DEVICE_NAME,      # 90  Device Name
                   MB_FIRMWARE,         # 5   Firmware Version
                   MB_INTERFACE_IP,     # 123 Interface IP
                   MB_NETWORK_STATUS,   # 124 Network Status
                   MB_RSSI,             # 151 RSSI
                   MB_VOLUME,           # 64  Volume
                   MB_MUTE,             # 63  Mute (Tx_ but responds)
                   MB_SOURCE,           # 50  Current Source (Tx_ but responds)
                   MB_PLAY_STATE,       # 51  Play State (Tx_ but responds)
                   MB_NOW_PLAYING,      # 42  Now Playing JSON (Tx_ but responds)
                   MB_POSITION,         # 49  Position (Tx_ but responds)
                   MB_TIMEZONE,         # 573 TimeZone
                   MB_BT_STATUS):       # 210 BT Status (Tx_ but responds)
            await self._send(0x01, mb, "")
            await asyncio.sleep(0.05)

        # MB#91 NETWORK INFO requires SET MACADDR payload (per spec §9.35)
        await self._send(0x02, MB_NETWORK_INFO, "MACADDR")
        await asyncio.sleep(0.05)

        # MB#70 Favourites — SET FAV_LIST is the documented query form
        await self._send(0x02, MB_FAVOURITES, "FAV_LIST")
        await asyncio.sleep(0.05)

        # NV read for SSID via MB#208 — Lithe's published NV-read protocol
        await self.async_read_nv("ssid")

    async def async_read_nv(self, item: str) -> None:
        """Read an NV item via MB#208 SET READ_<item>.

        Per LUCI spec §10.23, the speaker responds on the same MB#208 with
        the NV item's value as the payload.
        """
        self._pending_nv_read = item
        await self._send(0x02, MB_DEVICE_INFO, f"READ_{item}")

    async def _fetch_now_playing_burst(self) -> None:
        """Quickly fetch metadata when playback starts.

        Triggered when MB#49 position pushes start arriving but we don't
        yet have track info / source / play state. Fires off a tight set
        of GETs to populate the now-playing card without waiting for the
        next 30s coordinator cycle.
        """
        try:
            await self._send(0x01, MB_SOURCE, "")        # 50  Current Source
            await asyncio.sleep(0.05)
            await self._send(0x01, MB_PLAY_STATE, "")    # 51  Play State
            await asyncio.sleep(0.05)
            await self._send(0x01, MB_NOW_PLAYING, "")   # 42  Now Playing JSON
        except Exception as e:
            _LOGGER.debug("Now-playing burst fetch failed: %s", e)

    async def async_get_play_view(self) -> None:
        """Request the current Play View via MB#41 GETUI:PLAY.

        Per spec §9.15, the response arrives in MB#42 with the play view
        JSON (track info, artwork, transport state).
        """
        await self._send(0x02, MB_BROWSE, "GETUI:PLAY")

    async def async_get_home_view(self) -> None:
        """Request the Browse Home View via MB#41 GETUI:HOME.

        Per spec §9.15, the response arrives in MB#42 with the home view
        JSON listing available browseable sources (USB, Airable, DMR, etc).
        """
        await self._send(0x02, MB_BROWSE, "GETUI:HOME")

    async def async_request_favourites(self) -> None:
        await self._send(0x02, MB_FAVOURITES, "FAV_LIST")

    # ── Commands ───────────────────────────────────────────────────────────

    async def async_set_volume(self, level: int) -> None:
        await self._send(0x02, MB_VOLUME, str(max(0, min(100, level))))

    async def async_mute(self, mute: bool) -> None:
        """Mute / unmute speaker.

        Empirically this firmware accepts SET on MB#63 directly. The spec
        says MB#63 is Tx_ only and mute should go through MB#40 SET MUTE/
        UNMUTE — but MB#63 SET works and was the proven path in earlier
        working versions. Keep what works.
        """
        await self._send(0x02, MB_MUTE, MUTE_ON if mute else MUTE_OFF)

    async def async_play(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_PLAY)

    async def async_pause(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_PAUSE)

    async def async_resume(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_RESUME)

    async def async_stop(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_STOP)

    async def async_next_track(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_NEXT)

    async def async_prev_track(self) -> None:
        await self._send(0x02, MB_TRANSPORT, TRANSPORT_PREV)

    async def async_seek(self, position_ms: int) -> None:
        await self._send(0x02, MB_TRANSPORT, f"SEEK:{int(position_ms)}")

    async def async_set_shuffle(self, on: bool) -> None:
        """Toggle shuffle on/off via MB#40."""
        await self._send(0x02, MB_TRANSPORT, "SHUFFLE:ON" if on else "SHUFFLE:OFF")

    async def async_set_repeat(self, mode: str) -> None:
        """Set repeat mode via MB#40.

        mode: 'off' | 'all' | 'one'
        Per LUCI Tech Note: REPEAT:OFF, REPEAT:ALL, REPEAT:ONE.
        """
        m = (mode or "off").lower()
        cmd = {"off": "REPEAT:OFF", "all": "REPEAT:ALL", "one": "REPEAT:ONE"}.get(m, "REPEAT:OFF")
        await self._send(0x02, MB_TRANSPORT, cmd)

    async def async_play_url(self, url: str) -> None:
        """Push a direct stream URL to the speaker (MB#41 DIRECT)."""
        await self._send(0x02, MB_BROWSE, f"PLAYITEM:DIRECT:{url}")

    async def async_play_favourite(self, slot: int) -> None:
        """Play a saved favourite by slot (MB#70)."""
        await self._send(0x02, MB_FAVOURITES, f"FAV_PLAY:{int(slot)}")

    async def async_set_name(self, name: str) -> None:
        await self._send(0x02, MB_DEVICE_NAME, name)

    async def async_play_chime(self, chime_number: int) -> None:
        """Trigger an embedded audiocue via LUCI.

        Method per Lithe vendor docs:
          - Slots 1-9: MB#80 SET "play N"
          - Slots 10-15: MB#41 SET "PLAYITEM:DIRECT:/system/usr/songN.mp3"

        STANDBY WAKE: when source is 0 (NoSource) the speaker's audio path
        is in standby. We send MB#70 SET STANDBYOFF first, wait briefly
        for the DAC/AMP to come up, then fire the chime.
        """
        n = max(1, min(15, int(chime_number)))
        now = asyncio.get_event_loop().time()
        sock_state = "no_writer" if self._writer is None else (
            "closing" if self._writer.is_closing() else "open"
        )
        _LOGGER.info(
            "CHIME-DIAG slot=%d sock=%s connected=%s play_state=%s source=%d",
            n, sock_state, self.state.connected,
            self.state.play_state, self.state.source_id,
        )

        # Wake the audio subsystem out of standby if needed.
        # source=0 means NoSource — audio path is asleep.
        if self.state.source_id == 0:
            _LOGGER.info("CHIME-DIAG source=0 — sending STANDBYOFF to wake audio path")
            await self._send(0x02, MB_FAVOURITES, "STANDBYOFF")
            await asyncio.sleep(0.25)  # DAC/AMP wake time

        if n <= 9:
            await self._send(0x02, MB_CHIME, f"play {n}")
            self._last_chime_mbid = MB_CHIME
        else:
            await self._send(0x02, MB_BROWSE, f"PLAYITEM:DIRECT:/system/usr/song{n}.mp3")
            self._last_chime_mbid = MB_BROWSE

        self._last_chime_time = now

    async def async_bluetooth(self, command: str) -> None:
        """BT command: ON / OFF / ENTPAIR / DISCONNECT."""
        await self._send(0x02, MB_BLUETOOTH, command)

    async def async_reboot(self) -> None:
        """Request speaker reboot.

        Per LUCI spec §9.42–9.43, MB#114 and MB#115 form a request/grant
        pair where LSx asks the HOST to reboot it (during OTA). There is
        NO documented host-initiated "reboot now" command in the LUCI
        protocol.

        Sending MB#114 from us is a protocol violation that the speaker
        ignores. The only reliable way to reboot is via the Lithe app
        or HTTP Cast endpoint, neither of which is exposed via LUCI.
        """
        _LOGGER.warning(
            "Reboot via LUCI is not supported by the speaker firmware. "
            "Use the Lithe Audio app or power-cycle the speaker manually."
        )

    async def async_factory_reset(self) -> None:
        """Factory reset via MB#150."""
        await self._send(0x02, MB_FACTORY_RESET, "")

    async def async_dsp_command(self, sub_mb: int, value: int) -> None:
        """Send a DSP command via MB#112 tunnel (LS10 only).

        Sub-packet shape (6 bytes): 0x00 0x04 [sub_mb hi] [sub_mb lo] 0x02 [value]
        """
        byte_val = value & 0xFF if value >= 0 else (256 + value) & 0xFF
        sub = bytes([
            0x00, 0x04,
            (sub_mb >> 8) & 0xFF, sub_mb & 0xFF,
            0x02,
            byte_val,
        ])
        # DataLen counts payload bytes only — terminator is separate
        data_len = len(sub)
        header = struct.pack("<HBHBHH", 0xAAAA, 0x02, MB_DSP, 0, 0x0000, data_len)
        pkt = header + sub + b"\x00"  # terminator per vendor §10.2
        if self._writer and not self._writer.is_closing():
            _LOGGER.debug("TX DSP MB#112: sub=0x%02x(%d) val=%d", sub_mb, sub_mb, value)
            self._writer.write(pkt)
            await self._writer.drain()

    # ── Callbacks ──────────────────────────────────────────────────────────

    def register_callback(self, cb: Callable) -> None:
        if cb not in self._callbacks:
            self._callbacks.append(cb)

    def remove_callback(self, cb: Callable) -> None:
        if cb in self._callbacks:
            self._callbacks.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception:
                _LOGGER.debug("Callback error", exc_info=True)

    # ── Read loop and packet parsing ───────────────────────────────────────

    async def _read_loop(self) -> None:
        """Background task: read and parse incoming packets."""
        try:
            while self.state.connected and self._reader:
                try:
                    chunk = await asyncio.wait_for(self._reader.read(4096), timeout=300.0)
                    if not chunk:
                        break
                    self._buf += chunk
                    self._process_buffer()
                except asyncio.TimeoutError:
                    pass
        except Exception as e:
            _LOGGER.debug("Read loop ended: %s", e)
        finally:
            self.state.connected = False
            self._notify()

    def _process_buffer(self) -> None:
        """Parse all complete packets from buffer.

        Strategy:
          1. Read DataLen from offset 8-9 (BE — empirically the speaker
             uses BE on TX).
          2. Calculate packet end at 10 + data_len.
          3. SANITY CHECK: the byte immediately after this packet should
             be either a NUL terminator (0x00) followed by next packet's
             RID, OR the start of the next packet's RID directly (0xAA).
             If neither matches, the parser is misaligned — RESYNC by
             scanning forward for the next plausible packet start.
          4. After a successful sanity check, consume the packet AND any
             trailing NUL terminator before continuing.

        This protects against any single mis-parsed packet propagating
        forever — common cause of "chime command sent but no response"
        because all subsequent responses get glued onto a phantom packet.
        """
        while len(self._buf) >= 10:
            try:
                data_len = struct.unpack_from(">H", self._buf, 8)[0]
            except struct.error:
                break

            # Hard cap: legitimate LUCI payloads are well under 16KB.
            # Anything bigger means misalignment — resync now.
            if data_len > 16384:
                _LOGGER.warning(
                    "Implausible DataLen=%d at buf head — resyncing parser",
                    data_len,
                )
                self._resync_buffer()
                continue

            total = 10 + data_len
            if len(self._buf) < total:
                break  # need more bytes

            # Sanity-check: byte after this packet should be the high byte of
            # the next packet's RemoteID. Speakers use either 0x0000 (so high
            # byte 0x00) or 0xAAAA (high byte 0xAA). Anything else means we
            # mis-parsed the current packet's DataLen — resync.
            need_sanity = total < len(self._buf)
            if need_sanity:
                next_byte = self._buf[total]
                if next_byte not in (0x00, 0xAA):
                    _LOGGER.warning(
                        "Packet boundary mismatch (next byte 0x%02x after "
                        "DataLen=%d) — resyncing parser",
                        next_byte, data_len,
                    )
                    self._resync_buffer()
                    continue

            # Header fields
            try:
                remote_id = struct.unpack_from(">H", self._buf, 0)[0]
            except struct.error:
                remote_id = 0
            mbid = struct.unpack_from(">H", self._buf, 3)[0]

            # Sanity: MBID must be in the valid LUCI range. Per spec the
            # highest documented MB is around 600 (timezone is 573, cast
            # setup is 494). Anything beyond ~700 is parser garbage.
            if mbid > 700:
                _LOGGER.warning(
                    "Implausible MBID=%d (>700) — resyncing parser",
                    mbid,
                )
                self._resync_buffer()
                continue

            status = self._buf[5]
            payload_bytes = self._buf[10:10 + data_len]
            try:
                payload = payload_bytes.decode("utf-8", "replace").rstrip("\x00")
            except Exception:
                payload = ""

            # Consume this packet — do NOT skip trailing NUL.
            # The speaker's packet stream is back-to-back; any byte after
            # `total` is part of the next packet's RID. Skipping bytes here
            # offsets the parser forever.
            self._buf = self._buf[total:]
            # Reset resync counter — we successfully parsed a packet
            self._resync_count = 0

            self._last_rx_time = asyncio.get_event_loop().time()

            # Track RemoteIDs we see for diagnostics
            if remote_id not in self._seen_remote_ids:
                self._seen_remote_ids.add(remote_id)
                _LOGGER.info(
                    "RID-DIAG New RemoteID 0x%04x first seen on MB#%d: %s",
                    remote_id, mbid, payload[:80],
                )

            _LOGGER.debug(
                "RX MB#%d (rid=0x%04x, status=%d, %d bytes): %s",
                mbid, remote_id, status, data_len, payload[:200],
            )

            if status not in (0, 1):
                _LOGGER.warning(
                    "RX MB#%d returned status=%d (%s). RID=0x%04x payload=%r",
                    mbid, status,
                    {2: "Generic error", 3: "Device not ready",
                     4: "CRC error"}.get(status, f"unknown ({status})"),
                    remote_id, payload[:80],
                )

            self._handle_push(mbid, payload)

    def _resync_buffer(self) -> None:
        """Scan forward in self._buf for the next plausible packet header.

        Repeated misalignments indicate persistent corruption — likely
        from the speaker sending data we can't interpret. We track how
        often this happens; if it exceeds threshold, the parser
        disconnects and forces a reconnect.
        """
        self._resync_count += 1
        if self._resync_count > 10:
            _LOGGER.warning(
                "%d resyncs in a row — disconnecting to force clean reconnect",
                self._resync_count,
            )
            self._resync_count = 0
            self._buf = b""
            # Trigger reconnect by closing the writer
            if self._writer and not self._writer.is_closing():
                try:
                    self._writer.close()
                except Exception:
                    pass
            return

        # Plausible header starts with RemoteID = 0xAAAA or 0x0000.
        # We look for either pattern and discard bytes before it.
        i = 1
        while i < len(self._buf) - 1:
            b0 = self._buf[i]
            b1 = self._buf[i+1]
            if (b0 == 0xAA and b1 == 0xAA) or (b0 == 0x00 and b1 == 0x00):
                discarded = i
                self._buf = self._buf[i:]
                _LOGGER.debug(
                    "Resynced — discarded %d bytes to next packet header",
                    discarded,
                )
                return
            i += 1
        # No plausible start found — discard everything and start fresh
        _LOGGER.warning(
            "Resync failed — discarded %d bytes (no plausible packet header)",
            len(self._buf),
        )
        self._buf = b""

    def _handle_push(self, mbid: int, payload: str) -> None:
        """Handle an incoming message from the speaker.

        Note: every RX is already logged in _process_buffer with its
        RemoteID. We don't repeat the preview here.
        """
        changed = True

        if mbid == MB_PLAYBACK_AUTH:
            # HOST MCU only — NEVER respond (sending MB#11 stops playback)
            if self._last_chime_time:
                ms = (asyncio.get_event_loop().time() - self._last_chime_time) * 1000.0
                _LOGGER.info("CHIME-DIAG MB#10 (playback auth) +%.1fms: %r", ms, payload)
            return

        elif mbid == MB_DEVICE_NAME:
            new_name = payload.strip()
            # The PRO 2 reports two different names on MB#90:
            #   - Individual identity: "WiFi PRO 23503b8" (or similar)
            #   - Group/zone name: "Kitchen Sub" (when paired/grouped)
            # Both arrive on the same RemoteID, indistinguishable at the
            # protocol level. Prefer the individual name (matches the
            # speaker's hardcoded SSID-derived identity) and ignore group
            # name overwrites to keep HA's device name stable.
            if not self.state.name:
                self.state.name = new_name
            elif new_name and (
                new_name.startswith(("WiFi ", "iO1", "LS10", "LS9", "Lithe"))
                or new_name == self.state.name
            ):
                self.state.name = new_name
            else:
                _LOGGER.debug(
                    "Ignoring MB#90 group-name push %r (keeping %r)",
                    new_name, self.state.name,
                )

        elif mbid == MB_FIRMWARE:
            new_fw = payload.strip()
            # Same dual-response issue as MB#90: PRO 2 reports its own
            # firmware "CR443GP_3713" and also a paired peer's firmware
            # number "15244". Prefer the longer/CR-prefixed string.
            if not self.state.firmware:
                self.state.firmware = new_fw
            elif new_fw and (
                new_fw.startswith(("CR", "LS", "WP"))
                or new_fw == self.state.firmware
                or len(new_fw) > len(self.state.firmware)
            ):
                self.state.firmware = new_fw
            else:
                _LOGGER.debug(
                    "Ignoring MB#5 peer-firmware push %r (keeping %r)",
                    new_fw, self.state.firmware,
                )

        elif mbid == MB_VOLUME:
            try:
                self.state.volume = int(payload)
            except ValueError:
                pass

        elif mbid == MB_MUTE:
            self.state.muted = (payload == "1" or payload.upper() == "MUTE")

        elif mbid == MB_SOURCE:
            try:
                self.state.source_id = int(payload)
            except ValueError:
                pass

        elif mbid == MB_PLAY_STATE:
            self.state.play_state = PLAY_STATES.get(payload.strip(), "stopped")

        elif mbid == MB_POSITION:
            try:
                new_pos = int(payload)
            except ValueError:
                pass
            else:
                import time as _time
                # If position is moving but we don't know what's playing, fire
                # a fast metadata refresh. Spotify Connect / AirPlay starts
                # pushing MB#49 immediately but MB#42/50/51 don't always push
                # on their own — we have to GET them. Without this nudge the
                # user waits up to 30s (full coordinator cycle) for track
                # info and source to appear.
                if (new_pos > 0 and self.state.position_ms == 0
                        and (not self.state.title or self.state.source_id == 0)):
                    _LOGGER.debug(
                        "Position became active with no metadata — fetching now-playing"
                    )
                    asyncio.create_task(self._fetch_now_playing_burst())
                self.state.position_ms = new_pos
                self.state.position_updated_at = _time.time()

        elif mbid == MB_NOW_PLAYING:
            self._parse_now_playing(payload)

        elif mbid == MB_NETWORK_INFO:
            # MB#91 has the format: <Interface>:<MAC>
            # E.g. "Eth0:CC:90:93:35:03:BA" or "Wlan0:CC:90:93:10:2E:8C"
            # The speaker sends both — we prefer Wlan since LS10 speakers are wireless.
            p = payload.strip()
            if p and ":" in p:
                iface, _, mac = p.partition(":")
                iface_lower = iface.strip().lower()
                mac = mac.strip()
                # Wifi MAC takes precedence over Ethernet
                if iface_lower.startswith("wlan") or iface_lower == "wifi":
                    self.state.mac = mac
                    self.state.wifi_band = self.state.wifi_band or "Wi-Fi"
                elif iface_lower.startswith("eth"):
                    # Only set MAC if we haven't seen a wifi MAC yet
                    if not self.state.mac or self.state.mac.startswith("Eth"):
                        self.state.mac = mac

        elif mbid == MB_FAVOURITES:
            self._parse_favourites(payload)

        elif mbid == MB_CHIME:
            # Diagnostic: if we recently fired a chime via MB#80, log time-to-ack
            r = payload.strip()
            if self._last_chime_mbid == MB_CHIME and self._last_chime_time:
                ack_ms = (asyncio.get_event_loop().time() - self._last_chime_time) * 1000.0
                _LOGGER.info("CHIME-DIAG MB#80 ack in %.1fms: %r", ack_ms, r)
            elif r and r.upper() not in ("SUCCESS", "NI"):
                _LOGGER.debug("Chime MB#80 response: %s", r)

        elif mbid == MB_AUDIOCUE:
            # Newer-firmware audiocue lifecycle (per Lithe vendor docs).
            # Speaker→host notifications:
            #   AUDIOCUE_START — chime starting, speaker auto-pauses music
            #   SUCCESS        — chime finished playing, music will resume
            #   FAILURE / NI   — slot empty or playback failed
            r = payload.strip()
            ru = r.upper()
            # Always log MB#82 at info during diagnosis — vital signal
            if self._last_chime_time:
                ms = (asyncio.get_event_loop().time() - self._last_chime_time) * 1000.0
                _LOGGER.info("CHIME-DIAG MB#82 +%.1fms: %r", ms, r)
            else:
                _LOGGER.info("CHIME-DIAG MB#82 (unsolicited): %r", r)
            if ru in ("NI", "FILE_NOT_FOUND", "FAILURE", "FAIL"):
                _LOGGER.warning(
                    "Audiocue failed: '%s'. The slot may be empty or the "
                    "speaker is in a state that won't allow chime playback.",
                    r,
                )

        elif mbid == MB_DSP:
            # Payload is binary DSP sub-packet(s). Decode for visibility.
            # Push format (5 bytes): 00 03 <subMB_hi> <subMB_lo> <value>
            # SET response (6 bytes): 00 04 <subMB_hi> <subMB_lo> 02 <value>
            # Multiple sub-packets may be concatenated.
            try:
                raw = payload.encode("latin-1") if isinstance(payload, str) else payload
                if _LOGGER.isEnabledFor(logging.DEBUG):
                    parsed = []
                    i = 0
                    while i + 5 <= len(raw):
                        if raw[i] == 0x00 and raw[i+1] == 0x03 and i + 5 <= len(raw):
                            sub_mb = (raw[i+2] << 8) | raw[i+3]
                            val = raw[i+4]
                            parsed.append(f"sub=0x{sub_mb:02x}({sub_mb}) val={val}")
                            i += 5
                        elif raw[i] == 0x00 and raw[i+1] == 0x04 and i + 6 <= len(raw):
                            sub_mb = (raw[i+2] << 8) | raw[i+3]
                            val = raw[i+5]
                            parsed.append(f"SET sub=0x{sub_mb:02x}({sub_mb}) val={val}")
                            i += 6
                        else:
                            i += 1
                    if parsed:
                        _LOGGER.debug("DSP MB#112 decoded: %s", "; ".join(parsed))
            except Exception:
                pass

        elif mbid == MB_BT_STATUS:
            self.state.bt_status = payload.strip()

        elif mbid == MB_TIMEZONE:
            self.state.timezone = payload.strip()

        elif mbid == MB_INTERFACE_IP:
            # MB#123 — "Wlan:192.168.1.101" or "Eth:192.168.1.100"
            p = payload.strip()
            if ":" in p:
                iface, _, ip = p.partition(":")
                iface = iface.strip()
                ip = ip.strip()
                # Prefer Wlan over Eth when both come through
                if iface.lower().startswith("wlan") or not self.state.ip_address:
                    self.state.ip_address = ip
                    self.state.network_interface = iface

        elif mbid == MB_NETWORK_STATUS:
            # MB#124 — "<active>#WLAN,status#ETH,status#P2P,status#CONF,status"
            # active: 1=WLAN, 2=ETH, 3=P2P, 4=WAC/SAC/LS-Connect
            p = payload.strip()
            if p:
                active = p.split("#", 1)[0].strip()
                self.state.network_status = NETWORK_STATUS.get(active, "Unknown")
                # Set speaker_status based on whether any interface is active
                if active in NETWORK_STATUS:
                    self.state.speaker_status = "Connected"
                else:
                    self.state.speaker_status = "Standby"

        elif mbid == MB_RSSI:
            # MB#151 — payload is RSSI in dBm as a string (e.g. "-55" or "-55,-60" for dual antenna)
            p = payload.strip()
            if "," in p:
                # Multiple antennas — take the strongest (least negative)
                try:
                    vals = [int(v.strip()) for v in p.split(",") if v.strip()]
                    if vals:
                        self.state.wifi_rssi_dbm = max(vals)
                except ValueError:
                    pass
            else:
                try:
                    self.state.wifi_rssi_dbm = int(p)
                except ValueError:
                    pass

        elif mbid == MB_DEVICE_INFO:
            # MB#208 is dual-purpose: device info JSON OR NV-read response.
            # If the payload starts with a recognisable NV-read marker we treat
            # it specially. Otherwise fall through to the existing device-info
            # parser.
            p = payload.strip()
            if p and not p.startswith("{") and self._pending_nv_read:
                # We requested NV READ_<item> — this is the value
                nv_item = self._pending_nv_read
                self._pending_nv_read = None
                if nv_item.lower() == "ssid":
                    self.state.ssid = p
                _LOGGER.debug("NV read %r = %r", nv_item, p)
            else:
                self._parse_device_info(payload)

        else:
            changed = False

        if changed:
            self._notify()

    def _parse_now_playing(self, payload: str) -> None:
        """Parse MB#42 now-playing JSON.

        Different firmwares use different key names. We try a broad set of
        candidates for each field so this works across versions.
        """
        try:
            data = json.loads(payload)
        except Exception:
            _LOGGER.debug("Could not parse now-playing JSON")
            return

        # The Lithe firmware wraps real metadata inside "Window CONTENTS".
        # The OUTER "Title" is just the view name (e.g. "PlayView") — don't
        # read from there or we'll pick up the view name as the track title.
        w = None
        for wrapper in ("Window CONTENTS", "WindowContents", "window_contents", "data", "Data"):
            if isinstance(data.get(wrapper), dict):
                w = data[wrapper]
                break
        if w is None:
            _LOGGER.debug("MB#42 has no Window CONTENTS wrapper, skipping")
            return

        def _first(*keys: str) -> str:
            for k in keys:
                v = w.get(k)
                if v:
                    return str(v)
            return ""

        self.state.title  = _first(
            "TrackName", "trackname", "track_name",
            "Title", "title", "track", "Track", "TrackTitle", "track_title",
            "name", "Name", "currentTitle", "current_title", "song", "Song",
            "currentSong", "current_song",
        )
        self.state.artist = _first(
            "Artist", "artist", "Performer", "performer",
            "currentArtist", "current_artist", "ArtistName", "artist_name",
        )
        self.state.album  = _first(
            "Album", "album", "AlbumName", "album_name",
            "currentAlbum", "current_album",
        )

        # Some older LinkPlay-derived firmwares swap track name into the
        # "Artist" field for Spotify Connect. Newer Lithe firmware (this one)
        # exposes the proper "TrackName" so we don't need the swap, but it
        # remains as a fallback for older firmware.
        if not self.state.title and self.state.artist:
            self.state.title = self.state.artist
            self.state.artist = ""

        # ── Shuffle / Repeat state (MB#42 Window CONTENTS) ────────────────
        # Lithe firmware exposes:
        #   "Shuffle": 0 (off) or 1 (on)
        #   "Repeat":  0 (off), 1 (all), 2 (one)  — observed values
        if "Shuffle" in w:
            try:
                self.state.shuffle = bool(int(w.get("Shuffle", 0)))
            except (ValueError, TypeError):
                self.state.shuffle = False
        if "Repeat" in w:
            try:
                r = int(w.get("Repeat", 0))
                self.state.repeat = {0: "off", 1: "all", 2: "one"}.get(r, "off")
            except (ValueError, TypeError):
                self.state.repeat = "off"

        # Artwork — real key on Lithe firmware (CR443GP_3713) is "CoverArtUrl"
        art = _first(
            "CoverArtUrl", "CoverArtURL", "coverarturl",
            "AlbumArt", "Artwork", "ArtworkURI", "AlbumArtURI",
            "albumart", "artwork", "albumArt", "artworkUri", "AlbumArtUri",
            "CoverArt", "coverart", "cover", "Cover", "Image", "image",
            "logo", "Logo", "Icon", "icon",
        )
        # Some firmwares return a relative path — only accept if it looks like a URL
        if art and (art.startswith("http://") or art.startswith("https://")):
            self.state.artwork_url = art
        elif art:
            # Relative path — try to construct a URL using the speaker IP
            self.state.artwork_url = f"http://{self.host}{art}" if art.startswith("/") else f"http://{self.host}/{art}"
        else:
            self.state.artwork_url = ""

        # Duration — try numerous keys, accept ms or seconds
        duration_raw = None
        for k in ("TotalTime", "totaltime", "Duration", "duration", "track_duration", "Length", "length"):
            if k in w and w[k] not in (None, "", 0):
                duration_raw = w[k]
                break
        try:
            d = int(duration_raw) if duration_raw is not None else 0
            # If under 10000, the speaker probably reports seconds; convert to ms
            if 0 < d < 10000:
                d *= 1000
            self.state.duration_ms = d
        except (TypeError, ValueError):
            self.state.duration_ms = 0

        # Live streams report no duration
        self.state.is_live = (self.state.duration_ms == 0)

    def _parse_device_info(self, payload: str) -> None:
        """Parse MB#208 device info.

        Three shapes observed across firmwares:
          1) JSON dict
          2) Single "key: value" line
          3) Multi-line "key: value\nkey: value\n…" block
        """
        # Comprehensive key map — keys are normalised (lower, no spaces, no underscores)
        attr_map = {
            # name / friendly
            "devicename":     "name",
            "networkname":    "name",
            "groupname":      "name",
            "name":           "name",
            # model
            "model":          "model",
            "modelname":      "model",
            "modelid":        "model",
            "deviceid":       "model",
            "hardware":       "model",
            # firmware
            "fwversion":      "firmware",
            "firmware":       "firmware",
            "firmwareversion":"firmware",
            "swversion":      "firmware",
            "version":        "firmware",
            "release":        "firmware",
            # mac
            "mac":            "mac",
            "macaddress":     "mac",
            "macaddr":        "mac",
            "ethernet":       "mac",
            "ethermac":       "mac",
            "wifimac":        "mac",
            "wlanmac":        "mac",
            "bssid":          "mac",
            # wifi band / mode
            "wifiband":       "wifi_band",
            "band":           "wifi_band",
            "wifimode":       "wifi_band",
            "wlanmode":       "wifi_band",
            # net mode
            "netmode":        "net_mode",
            "networkmode":    "net_mode",
            # cast
            "castversion":    "cast_version",
            "castfwversion":  "cast_version",
        }

        def _norm(k: str) -> str:
            return k.strip().lower().replace(" ", "").replace("_", "").replace("-", "")

        def _apply(key: str, val: str) -> None:
            attr = attr_map.get(_norm(key))
            if attr and val:
                setattr(self.state, attr, str(val).strip())

        # JSON first
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                for k, v in data.items():
                    _apply(k, v)
                return
        except Exception:
            pass

        # Fallback: line-by-line key:value parsing
        for line in payload.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                _apply(key, val)

    def _parse_favourites(self, payload: str) -> None:
        """Parse MB#70 favourites payload.

        Accepts either JSON list/dict, or "FAV_LIST:1=Name1|2=Name2|..." text format.
        """
        try:
            data = json.loads(payload)
            favs = []
            if isinstance(data, list):
                for i, item in enumerate(data, 1):
                    if isinstance(item, dict):
                        favs.append({
                            "slot": int(item.get("slot", i)),
                            "name": str(item.get("name", f"Favourite {i}")),
                        })
                    else:
                        favs.append({"slot": i, "name": str(item)})
            elif isinstance(data, dict):
                for k, v in data.items():
                    try:
                        favs.append({"slot": int(k), "name": str(v)})
                    except ValueError:
                        continue
            self.state.favourites = sorted(favs, key=lambda x: x["slot"])
            return
        except Exception:
            pass
        # Fallback text format
        if payload.startswith("FAV_LIST"):
            body = payload.split(":", 1)[1] if ":" in payload else ""
            favs = []
            for chunk in body.split("|"):
                if "=" in chunk:
                    s, name = chunk.split("=", 1)
                    try:
                        favs.append({"slot": int(s), "name": name.strip()})
                    except ValueError:
                        continue
            if favs:
                self.state.favourites = sorted(favs, key=lambda x: x["slot"])

    # ── Packet builder ─────────────────────────────────────────────────────

    @staticmethod
    def _build_packet(cmd_type: int, mbid: int, payload: str) -> bytes:
        """Build a TX packet matching the LUCI spec.

        Per Lithe's official Python example (vendor docs §10.2):

          header = struct.pack("<H B H B H H",
              REMOTE_ID,    # 0xAAAA
              cmd_type,     # 0x01 GET or 0x02 SET
              mbid,
              0x00,         # CmdStatus
              0x0000,       # CRC
              len(payload)) # DataLen u16 little-endian
          sock.sendall(header + payload + b"\\x00")  # terminator

        Notes:
          - DataLen is LITTLE-endian (not BE as I mistakenly assumed)
          - DataLen excludes the trailing terminator
          - Terminator 0x00 IS appended after the packet
        """
        data = payload.encode("utf-8")
        data_len = len(data)
        header = struct.pack("<HBHBHH", 0xAAAA, cmd_type, mbid, 0, 0x0000, data_len)
        return header + data + b"\x00"

    async def _send(self, cmd_type: int, mbid: int, payload: str) -> None:
        if self._writer and not self._writer.is_closing():
            if _LOGGER.isEnabledFor(logging.DEBUG):
                op = "GET" if cmd_type == 0x01 else "SET"
                preview = payload[:80] + ("…" if len(payload) > 80 else "")
                _LOGGER.debug("TX %s MB#%d (%d bytes): %s", op, mbid, len(payload), preview)
            self._writer.write(self._build_packet(cmd_type, mbid, payload))
            await self._writer.drain()


class LitheClientLS9(LitheClient):
    """
    LS9 transactional client — connect, send, disconnect per command.
    LS9 firmware only allows one TCP connection at a time.
    """

    async def async_transact(
        self, mbid: int, payload: str, cmd_type: int = 0x02
    ) -> list[tuple[int, str]]:
        """Connect, send one command, collect responses, disconnect."""
        responses: list[tuple[int, str]] = []
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=4.0
            )
            try:
                sock = writer.get_extra_info("socket")
                if sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except Exception:
                pass

            # Register with plain IP string (LS9: no JSON)
            writer.write(self._build_packet(0x02, MB_REGISTER, self.local_ip))
            await writer.drain()
            await asyncio.sleep(0.15)

            # Send command
            writer.write(self._build_packet(cmd_type, mbid, payload))
            await writer.drain()

            # Read responses for up to 1.5s
            buf = b""
            deadline = asyncio.get_event_loop().time() + 1.5
            while asyncio.get_event_loop().time() < deadline:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=0.25)
                    if not chunk:
                        break
                    buf += chunk
                    while len(buf) >= 10:
                        try:
                            data_len = struct.unpack_from(">H", buf, 8)[0]
                        except struct.error:
                            break
                        total = 10 + data_len
                        if len(buf) < total:
                            break
                        r_mbid = struct.unpack_from(">H", buf, 3)[0]
                        r_payload = buf[10:10 + data_len].decode("utf-8", "replace").rstrip("\x00")
                        buf = buf[total:]
                        if r_mbid != MB_PLAYBACK_AUTH:
                            responses.append((r_mbid, r_payload))
                            self._handle_push(r_mbid, r_payload)
                except asyncio.TimeoutError:
                    if responses:
                        break
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        except Exception as e:
            _LOGGER.debug("LS9 transact %s MB#%d: %s", self.host, mbid, e)
        return responses

    async def _send(self, cmd_type: int, mbid: int, payload: str) -> None:
        await self.async_transact(mbid, payload, cmd_type)

    async def async_connect(self) -> None:
        """LS9: no persistent connection — just mark connected and prime state."""
        self.state.connected = True
        await self.async_refresh()

    async def async_disconnect(self) -> None:
        self.state.connected = False
