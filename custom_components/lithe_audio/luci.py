"""LUCI protocol client for Lithe Audio speakers.

Implements the binary frame protocol described in the Lithe Audio Partner
Developer Guide (LUCI v15.x). One persistent TCP (or TLS for LS10) socket
per device; commands are sent as 0xAAAA-framed packets terminated by 0x00,
and the device pushes authoritative state updates which fan out to
registered listeners.

Frame layout (little-endian):

    +--------+----------+--------+--------+-----+----------+--------+-----+
    | RID(2) | CmdTy(1) | MBID(2)| Stat(1)|CRC(1)| DataLen(2)| Data...| 00 |
    +--------+----------+--------+--------+-----+----------+--------+-----+

The terminator byte is *appended* after the payload to mark message
boundaries on the TCP stream — readers must split on 0x00 to find packet
ends, then use DataLen for safe payload extraction.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import struct
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .const import (
    CMD_GET,
    CMD_SET,
    CONNECT_TIMEOUT,
    KEEPALIVE_INTERVAL,
    MB_BROWSE,
    MB_CHIME,
    MB_DEVICE_DETAILS,
    MB_DEVICE_INFO,
    MB_DEVICE_NAME,
    MB_FIRMWARE,
    MB_INPUT_START,
    MB_INPUT_STOP,
    MB_MUTE,
    MB_NETWORK_INFO,
    MB_NOW_PLAYING,
    MB_PLAY_STATE,
    MB_POSITION,
    MB_PRESET,
    MB_REBOOT,
    MB_REGISTER,
    MB_SOURCE,
    MB_TIMEZONE,
    MB_TRANSPORT,
    MB_VOLUME,
    MUTE_OFF,
    MUTE_ON,
    PLATFORM_LS10,
    PLATFORM_LS9,
    PLAY_STATES,
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_DELAY,
    REMOTE_ID,
    SOURCE_NAMES,
    TRANSPORT_NEXT,
    TRANSPORT_PAUSE,
    TRANSPORT_PLAY,
    TRANSPORT_PREV,
    TRANSPORT_RESUME,
    TRANSPORT_STOP,
)

_LOGGER = logging.getLogger(__name__)

PacketHandler = Callable[[int, str, bytes], Awaitable[None] | None]


@dataclass
class DeviceState:
    """Authoritative device state pushed by the speaker (MB#42/50/51/63/64)."""

    play_state: str = "idle"          # idle | playing | paused | buffering
    volume: int = 0                   # 0..100
    muted: bool = False
    source_id: int = 0
    source_name: str = "No Source"
    position_ms: int = 0
    duration_ms: int = 0
    title: str = ""
    artist: str = ""
    album: str = ""
    art_url: str = ""
    # Static identity (rarely changes)
    name: str = ""
    model: str = ""
    firmware: str = ""
    mac: str = ""
    serial: str = ""
    timezone: str = ""
    # Raw "now playing" JSON for advanced consumers
    raw_now_playing: dict[str, Any] = field(default_factory=dict)
    # Connection health
    connected: bool = False
    last_update_monotonic: float = 0.0


class LucIProtocolError(Exception):
    """Raised on unrecoverable protocol errors."""


class LitheAudioClient:
    """Async client for the LUCI protocol.

    Maintains a persistent connection with automatic reconnect/re-register,
    delivers pushed state updates to a single async listener, and exposes
    high-level commands for the entities/services to call.
    """

    def __init__(
        self,
        host: str,
        port: int = 7777,
        *,
        platform: str = PLATFORM_LS9,
        client_cert_pem: str | None = None,
        client_cert_key: str | None = None,
        client_app_id: str = "homeassistant.lithe_audio",
        client_app_version: str = "0.1.0",
        client_ip: str = "0.0.0.0",
    ) -> None:
        """Create a client; doesn't connect until ``async_start``."""
        self._host = host
        self._port = port
        self._platform = platform
        self._cert_pem = client_cert_pem
        self._cert_key = client_cert_key
        self._app_id = client_app_id
        self._app_version = client_app_version
        self._client_ip = client_ip

        self.state = DeviceState()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._rx_buffer = bytearray()
        self._run_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._listeners: list[Callable[[DeviceState], None]] = []
        self._raw_handlers: list[PacketHandler] = []
        # Tempfile-backed certificate paths (ssl module needs files)
        self._cert_files: list[Path] = []
        # Coalesce rapid volume sends
        self._volume_pending: int | None = None
        self._volume_task: asyncio.Task[None] | None = None

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def platform(self) -> str:
        return self._platform

    @property
    def is_ls10(self) -> bool:
        return self._platform == PLATFORM_LS10

    def add_listener(self, callback: Callable[[DeviceState], None]) -> Callable[[], None]:
        """Register a synchronous callback fired after each state update.

        Returns an unsubscribe function.
        """
        self._listeners.append(callback)
        def _remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)
        return _remove

    def add_raw_handler(self, handler: PacketHandler) -> Callable[[], None]:
        """Register a handler for raw incoming packets (advanced/diagnostics)."""
        self._raw_handlers.append(handler)
        def _remove() -> None:
            if handler in self._raw_handlers:
                self._raw_handlers.remove(handler)
        return _remove

    async def async_start(self) -> None:
        """Begin the connect/listen loop; safe to call multiple times."""
        if self._run_task and not self._run_task.done():
            return
        self._stopping = False
        self._run_task = asyncio.create_task(
            self._run_loop(), name=f"lithe_audio[{self._host}]"
        )

    async def async_stop(self) -> None:
        """Tear down the connection and worker task."""
        self._stopping = True
        if self._volume_task and not self._volume_task.done():
            self._volume_task.cancel()
        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        if self._run_task is not None:
            self._run_task.cancel()
            try:
                await self._run_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Clean up cert tempfiles
        for path in self._cert_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._cert_files.clear()

    # ── High-level commands ───────────────────────────────────────────────

    async def async_play(self) -> None:
        await self._send(CMD_SET, MB_TRANSPORT, TRANSPORT_PLAY)

    async def async_pause(self) -> None:
        await self._send(CMD_SET, MB_TRANSPORT, TRANSPORT_PAUSE)

    async def async_stop_playback(self) -> None:
        await self._send(CMD_SET, MB_TRANSPORT, TRANSPORT_STOP)

    async def async_resume(self) -> None:
        await self._send(CMD_SET, MB_TRANSPORT, TRANSPORT_RESUME)

    async def async_next(self) -> None:
        await self._send(CMD_SET, MB_TRANSPORT, TRANSPORT_NEXT)

    async def async_previous(self) -> None:
        await self._send(CMD_SET, MB_TRANSPORT, TRANSPORT_PREV)

    async def async_seek(self, position_seconds: float) -> None:
        ms = max(0, int(position_seconds * 1000))
        await self._send(CMD_SET, MB_TRANSPORT, f"SEEK:{ms}")

    async def async_set_volume(self, level: int) -> None:
        """Set volume 0..100, with debounce to avoid flooding the speaker."""
        level = max(0, min(100, int(level)))
        self._volume_pending = level
        # Optimistic local update so the UI feels snappy
        self.state.volume = level
        self._notify_listeners()
        if self._volume_task is None or self._volume_task.done():
            self._volume_task = asyncio.create_task(self._flush_volume())

    async def _flush_volume(self) -> None:
        await asyncio.sleep(0.25)  # debounce window
        if self._volume_pending is None:
            return
        value = self._volume_pending
        self._volume_pending = None
        await self._send(CMD_SET, MB_VOLUME, str(value))

    async def async_set_mute(self, mute: bool) -> None:
        await self._send(CMD_SET, MB_MUTE, MUTE_ON if mute else MUTE_OFF)

    async def async_play_chime(self, index: int) -> None:
        """Trigger an embedded cue (song1..song15) via MB#80."""
        index = max(1, min(15, int(index)))
        await self._send(CMD_SET, MB_CHIME, f"play {index}")

    async def async_play_direct(self, path: str) -> None:
        """Play an arbitrary URL or local /system/usr/songN.mp3 via MB#41."""
        await self._send(CMD_SET, MB_BROWSE, f"PLAYITEM:{path}")

    async def async_select_browse_item(self, item_id: int | str) -> None:
        await self._send(CMD_SET, MB_BROWSE, f"SELECTITEM:{item_id}")

    async def async_preset_play(self, slot: int) -> None:
        slot = max(1, min(9, int(slot)))
        await self._send(CMD_SET, MB_PRESET, f"FAV_PLAY:{slot}")

    async def async_preset_save(self, slot: int) -> None:
        slot = max(1, min(9, int(slot)))
        await self._send(CMD_SET, MB_PRESET, f"FAV_SAVE:{slot}")

    async def async_preset_delete(self, slot: int) -> None:
        slot = max(1, min(9, int(slot)))
        await self._send(CMD_SET, MB_PRESET, f"FAV_DELETE:{slot}")

    async def async_input_start(self) -> None:
        await self._send(CMD_SET, MB_INPUT_START, "START")

    async def async_input_stop(self) -> None:
        await self._send(CMD_SET, MB_INPUT_STOP, "STOP")

    async def async_reboot(self) -> None:
        # MB#37 reboot — payload "1" is the common convention
        await self._send(CMD_SET, MB_REBOOT, "1")

    async def async_send_raw(self, mbid: int, payload: str, cmd_type: int = CMD_SET) -> None:
        """Escape hatch for diagnostics / advanced automations."""
        await self._send(cmd_type, mbid, payload)

    async def async_request_state_refresh(self) -> None:
        """Re-request all baseline state values."""
        for mbid in (MB_SOURCE, MB_PLAY_STATE, MB_VOLUME, MB_MUTE,
                     MB_NOW_PLAYING, MB_DEVICE_INFO, MB_TIMEZONE, MB_POSITION):
            await self._send(CMD_GET, mbid, "")

    # ── Internal: connect/run loop ───────────────────────────────────────

    async def _run_loop(self) -> None:
        backoff = RECONNECT_BASE_DELAY
        while not self._stopping:
            try:
                await self._connect_and_register()
                backoff = RECONNECT_BASE_DELAY  # reset on success
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Lithe Audio %s connection lost (%s); reconnecting in %.1fs",
                    self._host, exc, backoff,
                )
            finally:
                self.state.connected = False
                self._notify_listeners()
                await self._close_writer()

            if self._stopping:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_DELAY)

    async def _connect_and_register(self) -> None:
        ssl_ctx = self._build_ssl_context() if self.is_ls10 else None
        _LOGGER.debug(
            "Connecting to %s:%s (%s, TLS=%s)",
            self._host, self._port, self._platform, ssl_ctx is not None,
        )
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port, ssl=ssl_ctx),
            timeout=CONNECT_TIMEOUT,
        )
        # Register
        if self.is_ls10:
            reg_payload = json.dumps({
                "APP_info": {
                    "id": self._app_id,
                    "version": self._app_version,
                    "ip": self._client_ip,
                }
            })
        else:
            # LS9: a plain IP/empty string is accepted
            reg_payload = self._client_ip or ""
        await self._send(CMD_SET, MB_REGISTER, reg_payload)
        # Request initial state
        await self.async_request_state_refresh()
        self.state.connected = True
        self._notify_listeners()
        _LOGGER.info("Lithe Audio %s connected and registered", self._host)

    def _build_ssl_context(self) -> ssl.SSLContext:
        """Build a TLS 1.2 mutual-auth context for LS10 devices.

        The speaker is self-signed under Lithe's CA which happens to be the
        same client.pem the integration presents — hence the cert is used
        as both client identity AND CA trust anchor, with hostname checks
        disabled (the cert isn't bound to the device's IP).
        """
        if not (self._cert_pem and self._cert_key):
            raise LucIProtocolError(
                "LS10 device requires client.pem and client.key contents"
            )
        # ssl.load_cert_chain needs files on disk
        cert_dir = Path(tempfile.mkdtemp(prefix="lithe_audio_"))
        pem_path = cert_dir / "client.pem"
        key_path = cert_dir / "client.key"
        pem_path.write_text(self._cert_pem)
        key_path.write_text(self._cert_key)
        # Restrictive permissions for the private key
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
        self._cert_files.extend([pem_path, key_path])

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
        ctx.load_cert_chain(certfile=str(pem_path), keyfile=str(key_path))
        ctx.load_verify_locations(cafile=str(pem_path))
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    async def _read_loop(self) -> None:
        assert self._reader is not None
        last_rx = asyncio.get_event_loop().time()
        while not self._stopping:
            try:
                # Generous read timeout — speaker pushes MB#51 ~every 30s
                chunk = await asyncio.wait_for(
                    self._reader.read(4096), timeout=KEEPALIVE_INTERVAL * 2,
                )
            except asyncio.TimeoutError:
                # No data in 50s — verify with a GET MB#51
                await self._send(CMD_GET, MB_PLAY_STATE, "")
                continue
            if not chunk:
                raise ConnectionResetError("Speaker closed the connection")
            last_rx = asyncio.get_event_loop().time()
            self._rx_buffer.extend(chunk)
            await self._drain_rx_buffer()

    async def _drain_rx_buffer(self) -> None:
        """Parse complete packets from the rx buffer.

        Packets are 10-byte header + DataLen bytes payload + 0x00 terminator.
        We trust DataLen for payload extraction (split-on-NUL is unreliable
        because JSON payloads may contain 0x00? — actually no, JSON is text,
        but binary safety still favours DataLen).
        """
        while True:
            if len(self._rx_buffer) < 10:
                return
            try:
                rid, cmd_type, mbid, status, crc, data_len = struct.unpack_from(
                    "<HBHBHH", self._rx_buffer, 0,
                )
            except struct.error:
                self._rx_buffer.clear()
                return
            if rid != REMOTE_ID:
                # Misalignment — resync to next AA AA
                idx = self._rx_buffer.find(b"\xaa\xaa", 1)
                if idx < 0:
                    self._rx_buffer.clear()
                    return
                del self._rx_buffer[:idx]
                continue
            total_needed = 10 + data_len + 1  # header + payload + 0x00 terminator
            if len(self._rx_buffer) < total_needed:
                return
            payload_bytes = bytes(self._rx_buffer[10:10 + data_len])
            # consume header + payload + terminator
            del self._rx_buffer[:total_needed]
            payload_str = payload_bytes.decode("utf-8", errors="replace").rstrip("\x00")
            try:
                await self._dispatch(mbid, payload_str, payload_bytes)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Error dispatching MB#%d packet", mbid)

    async def _dispatch(self, mbid: int, payload: str, raw: bytes) -> None:
        """Update internal state based on pushed packet."""
        changed = self._apply_state(mbid, payload)
        for handler in list(self._raw_handlers):
            try:
                result = handler(mbid, payload, raw)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                _LOGGER.exception("raw handler %s failed", handler)
        if changed:
            self.state.last_update_monotonic = asyncio.get_event_loop().time()
            self._notify_listeners()

    def _apply_state(self, mbid: int, payload: str) -> bool:
        """Translate a push packet into ``self.state`` mutations.

        Returns True if anything changed.
        """
        s = self.state
        if mbid == MB_VOLUME:
            try:
                new = max(0, min(100, int(payload.strip())))
            except ValueError:
                return False
            if new != s.volume:
                s.volume = new
                return True
            return False

        if mbid == MB_MUTE:
            new = payload.strip().upper() in ("MUTE", "1", "TRUE", "MUTED")
            if new != s.muted:
                s.muted = new
                return True
            return False

        if mbid == MB_SOURCE:
            try:
                sid = int(payload.strip())
            except ValueError:
                # Some firmwares return source NAME (e.g. "SPOTIFY") — keep both
                name = payload.strip() or "No Source"
                if name != s.source_name:
                    s.source_name = name
                    return True
                return False
            new_name = SOURCE_NAMES.get(sid, f"Source {sid}")
            if sid != s.source_id or new_name != s.source_name:
                s.source_id = sid
                s.source_name = new_name
                return True
            return False

        if mbid == MB_PLAY_STATE:
            new = PLAY_STATES.get(payload.strip(), "idle")
            if new != s.play_state:
                s.play_state = new
                return True
            return False

        if mbid == MB_POSITION:
            try:
                new = int(payload.strip())
            except ValueError:
                return False
            if new != s.position_ms:
                s.position_ms = new
                return True
            return False

        if mbid == MB_NOW_PLAYING:
            payload = payload.strip()
            if not payload.startswith("{"):
                return False
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                return False
            s.raw_now_playing = data
            # Common fields used by Lithe's UI JSON
            new_title = str(data.get("Title", data.get("title", "")))
            new_artist = str(data.get("Artist", data.get("artist", "")))
            new_album = str(data.get("Album", data.get("album", "")))
            new_dur = int(data.get("TotalTime", data.get("Duration", 0)) or 0)
            new_art = str(data.get("AlbumArtURI", data.get("AlbumArtUri",
                          data.get("art_url", "") or "")))
            changed = False
            if new_title != s.title:
                s.title = new_title
                changed = True
            if new_artist != s.artist:
                s.artist = new_artist
                changed = True
            if new_album != s.album:
                s.album = new_album
                changed = True
            if new_dur and new_dur != s.duration_ms:
                s.duration_ms = new_dur
                changed = True
            if new_art != s.art_url:
                s.art_url = new_art
                changed = True
            return changed

        if mbid == MB_DEVICE_INFO:
            # "Model:PRO2,LEDControl:1" style
            changed = False
            for part in payload.split(","):
                if ":" not in part:
                    continue
                key, _, val = part.partition(":")
                key = key.strip().lower()
                val = val.strip()
                if key == "model" and val != s.model:
                    s.model = val
                    changed = True
                elif key in ("fw_version", "firmware") and val != s.firmware:
                    s.firmware = val
                    changed = True
                elif key in ("mac", "mac_address") and val != s.mac:
                    s.mac = val.upper()
                    changed = True
            return changed

        if mbid == MB_DEVICE_DETAILS:
            # JSON with macaddress/serialnumber/versioninfo
            try:
                data = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                return False
            changed = False
            macs = data.get("macaddress", {})
            if isinstance(macs, dict):
                wlan = macs.get("wlan0") or macs.get("eth0")
                if wlan and wlan.upper() != s.mac:
                    s.mac = wlan.upper()
                    changed = True
            sn = data.get("serialnumber", {})
            if isinstance(sn, dict):
                dsn = sn.get("device_serialnumber")
                if dsn and dsn != s.serial:
                    s.serial = dsn
                    changed = True
            vi = data.get("versioninfo", {})
            if isinstance(vi, dict):
                fw = vi.get("devicefwversion")
                if fw and fw != s.firmware:
                    s.firmware = fw
                    changed = True
            return changed

        if mbid == MB_DEVICE_NAME:
            new = payload.strip()
            if new and new != s.name:
                s.name = new
                return True
            return False

        if mbid == MB_FIRMWARE:
            new = payload.strip()
            if new and new != s.firmware:
                s.firmware = new
                return True
            return False

        if mbid == MB_NETWORK_INFO:
            # e.g. "Wlan0:AA:BB:CC:DD:EE:FF"
            if ":" in payload:
                _, _, val = payload.partition(":")
                val = val.strip()
                if val and val.upper() != s.mac and len(val) >= 12:
                    s.mac = val.upper()
                    return True
            return False

        if mbid == MB_TIMEZONE:
            new = payload.strip()
            if new and new != s.timezone:
                s.timezone = new
                return True
            return False

        return False

    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            try:
                cb(self.state)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Listener callback raised")

    # ── Packet construction & transmission ───────────────────────────────

    @staticmethod
    def build_packet(cmd_type: int, mbid: int, payload: str) -> bytes:
        """Build a wire-format LUCI packet (header + payload + terminator).

        Layout (little-endian, 10-byte header):
            RemoteID (2) | CmdType (1) | MBID (2) | Status (1) |
            CRC (2)      | DataLen (2) | Payload (N) | 0x00 terminator
        """
        data = payload.encode("utf-8")
        data_len = len(data)
        # The CRC field is 2 bytes wide; reference implementations leave it
        # at 0x0000 (the speaker firmware doesn't validate it strictly).
        header = struct.pack("<HBHBHH", REMOTE_ID, cmd_type, mbid, 0, 0, data_len)
        return header + data + b"\x00"

    async def _send(self, cmd_type: int, mbid: int, payload: str) -> None:
        if self._writer is None:
            _LOGGER.debug("Drop MB#%d (%s) — no active connection", mbid, payload[:32])
            return
        packet = self.build_packet(cmd_type, mbid, payload)
        try:
            self._writer.write(packet)
            await self._writer.drain()
        except (ConnectionError, OSError) as exc:
            _LOGGER.debug("Send MB#%d failed: %s", mbid, exc)
            # Force the read loop to fall through to reconnect
            await self._close_writer()

    async def _close_writer(self) -> None:
        if self._writer is None:
            return
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        self._writer = None
        self._reader = None
