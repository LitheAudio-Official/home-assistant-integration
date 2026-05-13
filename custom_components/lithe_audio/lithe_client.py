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
    MB_FIRMWARE, MB_MUTE, MB_NETWORK_INFO, MB_NOW_PLAYING, MB_PLAY_STATE,
    MB_PLAYBACK_AUTH, MB_POSITION, MB_REBOOT_REQ, MB_REGISTER, MB_SOURCE,
    MB_TIMEZONE, MB_TRANSPORT, MB_VOLUME, MUTE_OFF, MUTE_ON, PLAY_STATES,
    SOURCES, TRANSPORT_NEXT, TRANSPORT_PAUSE, TRANSPORT_PLAY, TRANSPORT_PREV,
    TRANSPORT_RESUME, TRANSPORT_STOP,
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
        self._callbacks: list[Callable] = []
        self._last_rx_time: float = 0.0
        self._last_chime_time: float = 0.0
        self._last_chime_mbid: int = 0
        # RemoteID of "our" speaker — set on first RX. Packets from other
        # RemoteIDs (paired peer speakers relayed through master) are filtered.
        self._our_remote_id: int = 0

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

        # Register — APP_info key MUST be capitalised, lowercase silently fails
        if self.use_tls:
            reg = json.dumps({
                "APP_info": {
                    "id": "com.litheaudio.homeassistant",
                    "version": "1.1.0",
                    "ip": self.local_ip,
                }
            }, separators=(",", ":"))
        else:
            reg = self.local_ip

        self._writer.write(self._build_packet(0x02, MB_REGISTER, reg))
        await self._writer.drain()
        await asyncio.sleep(0.4)  # speaker needs ~400ms before accepting commands

        self.state.connected = True
        _LOGGER.info("Connected to Lithe Audio speaker at %s:%s", self.host, self.port)

        # Start background reader
        self._read_task = asyncio.create_task(self._read_loop())

        # Request initial state
        await self.async_refresh()

    async def async_disconnect(self) -> None:
        """Disconnect from speaker."""
        self.state.connected = False
        self._our_remote_id = 0
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass

    # ── State refresh ──────────────────────────────────────────────────────

    async def async_refresh(self) -> None:
        """Request all state from speaker."""
        for mb in (MB_DEVICE_NAME, MB_FIRMWARE, MB_DEVICE_INFO, MB_NETWORK_INFO,
                   MB_VOLUME, MB_MUTE, MB_SOURCE, MB_PLAY_STATE,
                   MB_NOW_PLAYING, MB_POSITION, MB_TIMEZONE, MB_BT_STATUS):
            await self._send(0x01, mb, "")
            await asyncio.sleep(0.05)
        # Favourites — try both known command variants in case firmware differs
        await self._send(0x02, MB_FAVOURITES, "FAV_LIST")
        await asyncio.sleep(0.05)
        await self._send(0x01, MB_FAVOURITES, "")

    async def async_request_favourites(self) -> None:
        await self._send(0x02, MB_FAVOURITES, "FAV_LIST")

    # ── Commands ───────────────────────────────────────────────────────────

    async def async_set_volume(self, level: int) -> None:
        await self._send(0x02, MB_VOLUME, str(max(0, min(100, level))))

    async def async_mute(self, mute: bool) -> None:
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
        """Trigger an embedded audiocue — minimum-latency path.

        Per Lithe vendor docs:
          - Slots 1-9: MB#80 "play N"
          - Slots 10-15: MB#41 "PLAYITEM:DIRECT:/system/usr/songN.mp3"

        No wake-up, no retry, no drain — this is the canonical wire format
        and the speaker is supposed to respond instantly. If it doesn't,
        that's a bug we need to identify (not patch around). Diagnostic
        logging tells us exactly what the speaker did with each command.
        """
        n = max(1, min(15, int(chime_number)))

        if n <= 9:
            mbid = MB_CHIME
            payload = f"play {n}".encode("utf-8")
        else:
            mbid = MB_BROWSE
            payload = f"PLAYITEM:DIRECT:/system/usr/song{n}.mp3".encode("utf-8")
        pkt = struct.pack("<HBHBHH", 0xAAAA, 0x02, mbid, 0, 0x0000, len(payload)) + payload + b"\x00"

        w = self._writer
        now = asyncio.get_event_loop().time()
        silence = now - (self._last_rx_time or 0.0)

        # Diagnostic: capture exact state at moment of press
        sock_state = "no_writer"
        if w is not None:
            sock_state = "closing" if w.is_closing() else "open"

        _LOGGER.info(
            "CHIME-DIAG slot=%d mbid=%d sock=%s silence_since_rx=%.1fs "
            "connected=%s play_state=%s source=%d",
            n, mbid, sock_state, silence,
            self.state.connected, self.state.play_state, self.state.source_id,
        )

        if w is None or w.is_closing():
            _LOGGER.error("CHIME-DIAG socket dead at press — packet NOT sent")
            return

        try:
            w.write(pkt)
        except Exception as e:
            _LOGGER.error("CHIME-DIAG write failed: %s", e)
            return

        _LOGGER.debug("TX SET MB#%d (%d bytes): %s", mbid, len(payload), payload.decode("utf-8", "replace"))
        # Remember when this chime was fired so the RX handler can log time-to-ack
        self._last_chime_time = now
        self._last_chime_mbid = mbid

    async def async_bluetooth(self, command: str) -> None:
        """BT command: ON / OFF / ENTPAIR / DISCONNECT."""
        await self._send(0x02, MB_BLUETOOTH, command)

    async def async_reboot(self) -> None:
        """Reboot via MB#114 (Reboot Request)."""
        await self._send(0x02, MB_REBOOT_REQ, "")

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
        # DataLen counts payload bytes only — NUL terminator is separate
        data_len = len(sub)
        header = struct.pack("<HBHBHH", 0xAAAA, 0x02, MB_DSP, 0, 0x0000, data_len)
        pkt = header + sub + b"\x00"
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

        RX header layout (10 bytes total, per LUCI spec):
            offset 0-1:  RemoteID  (2 bytes)
            offset 2:    CmdType   (1 byte)
            offset 3-4:  MBID      (2 bytes, BE on RX)
            offset 5:    Status    (1 byte)
            offset 6-7:  CRC       (2 bytes)
            offset 8-9:  DataLen   (2 bytes, BE on RX)

        IMPORTANT — the spec says TX and RX framing are asymmetric:
          - On TX we send LE, DataLen = payload length only, NUL appended.
          - On RX the speaker sends BE, and DataLen on incoming packets
            INCLUDES the trailing NUL byte. So the next packet starts at
            offset (10 + DataLen) with NO additional skip.
        """
        while len(self._buf) >= 10:
            try:
                data_len = struct.unpack_from(">H", self._buf, 8)[0]
            except struct.error:
                break
            total = 10 + data_len
            if len(self._buf) < total:
                break

            # Read RemoteID — identifies which device the packet is from when
            # multiple speakers are paired/grouped on the same LUCI connection.
            # The MASTER speaker relays peer status with the peer's RemoteID,
            # NOT its own. We must filter on this or our state will flip
            # between devices on every push.
            try:
                remote_id = struct.unpack_from(">H", self._buf, 0)[0]
            except struct.error:
                remote_id = 0

            mbid = struct.unpack_from(">H", self._buf, 3)[0]
            payload_bytes = self._buf[10:10 + data_len]
            try:
                payload = payload_bytes.decode("utf-8", "replace").rstrip("\x00")
            except Exception:
                payload = ""

            self._buf = self._buf[total:]
            self._last_rx_time = asyncio.get_event_loop().time()

            # Capture first RemoteID we observe — that's "our" speaker.
            # Discard packets from other RemoteIDs (paired peers).
            if self._our_remote_id == 0:
                self._our_remote_id = remote_id
                _LOGGER.info("Speaker RemoteID = 0x%04x", remote_id)
            elif remote_id != self._our_remote_id:
                _LOGGER.debug(
                    "Discarding MB#%d from peer device RemoteID=0x%04x "
                    "(ours=0x%04x): %s",
                    mbid, remote_id, self._our_remote_id, payload[:80],
                )
                continue

            self._handle_push(mbid, payload)

    def _handle_push(self, mbid: int, payload: str) -> None:
        """Handle an incoming message from the speaker."""
        # Debug visibility of every push — invaluable for diagnosing missing
        # state, wrong JSON keys, unexpected MB# numbers, etc.
        if _LOGGER.isEnabledFor(logging.DEBUG):
            preview = payload[:800] + ("…" if len(payload) > 800 else "")
            _LOGGER.debug("RX MB#%d (%d bytes): %s", mbid, len(payload), preview)

        changed = True

        if mbid == MB_PLAYBACK_AUTH:
            # HOST MCU only — NEVER respond (sending MB#11 stops playback)
            if self._last_chime_time:
                ms = (asyncio.get_event_loop().time() - self._last_chime_time) * 1000.0
                _LOGGER.info("CHIME-DIAG MB#10 (playback auth) +%.1fms: %r", ms, payload)
            return

        elif mbid == MB_DEVICE_NAME:
            self.state.name = payload.strip()

        elif mbid == MB_FIRMWARE:
            self.state.firmware = payload.strip()

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
                self.state.position_ms = int(payload)
                import time as _time
                self.state.position_updated_at = _time.time()
            except ValueError:
                pass

        elif mbid == MB_NOW_PLAYING:
            self._parse_now_playing(payload)

        elif mbid == MB_DEVICE_INFO:
            self._parse_device_info(payload)

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
        """Build a TX packet matching the LUCI spec exactly:

        Header (10 bytes):
            RemoteID  : 2 bytes, LE — always 0xAAAA
            CmdType   : 1 byte      — 0x01 GET, 0x02 SET
            MBID      : 2 bytes, LE
            Status    : 1 byte      — 0x00
            CRC       : 2 bytes, LE — 0x0000 (CRC disabled — speaker doesn't validate)
            DataLen   : 2 bytes, LE — length of payload EXCLUDING the trailing NUL
        Body:
            Payload (UTF-8) + 0x00 NUL terminator (terminator is OUTSIDE DataLen)
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
