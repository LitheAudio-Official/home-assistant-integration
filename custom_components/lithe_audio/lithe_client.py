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
    DEFAULT_PORT, MB_BLUETOOTH, MB_BROWSE, MB_BT_STATUS, MB_CHIME,
    MB_DEVICE_INFO, MB_DEVICE_NAME, MB_DSP, MB_FACTORY_RESET, MB_FAVOURITES,
    MB_FIRMWARE, MB_MUTE, MB_NOW_PLAYING, MB_PLAY_STATE, MB_PLAYBACK_AUTH,
    MB_POSITION, MB_REBOOT_REQ, MB_REGISTER, MB_SOURCE, MB_TIMEZONE,
    MB_TRANSPORT, MB_VOLUME, MUTE_OFF, MUTE_ON, PLAY_STATES, SOURCES,
    TRANSPORT_NEXT, TRANSPORT_PAUSE, TRANSPORT_PLAY, TRANSPORT_PREV,
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

    # Now playing
    title: str = ""
    artist: str = ""
    album: str = ""
    artwork_url: str = ""
    duration_ms: int = 0
    is_live: bool = False  # True for radio/AirPlay streams (no SEEK)

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

        # Enable TCP keepalive
        try:
            sock = self._writer.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
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
        for mb in (MB_DEVICE_NAME, MB_FIRMWARE, MB_DEVICE_INFO,
                   MB_VOLUME, MB_MUTE, MB_SOURCE, MB_PLAY_STATE,
                   MB_NOW_PLAYING, MB_TIMEZONE, MB_BT_STATUS):
            await self._send(0x01, mb, "")
            await asyncio.sleep(0.05)
        # Favourites list
        await self._send(0x02, MB_FAVOURITES, "FAV_LIST")

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

    async def async_play_url(self, url: str) -> None:
        """Push a direct stream URL to the speaker (MB#41 DIRECT)."""
        await self._send(0x02, MB_BROWSE, f"PLAYITEM:DIRECT:{url}")

    async def async_play_favourite(self, slot: int) -> None:
        """Play a saved favourite by slot (MB#70)."""
        await self._send(0x02, MB_FAVOURITES, f"FAV_PLAY:{int(slot)}")

    async def async_set_name(self, name: str) -> None:
        await self._send(0x02, MB_DEVICE_NAME, name)

    async def async_play_chime(self, chime_number: int) -> None:
        await self._send(0x02, MB_CHIME, f"play {int(chime_number)}")

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
        # Build a raw MB#112 packet wrapping the sub-packet
        data = sub + b"\x00"
        data_len = len(data)
        crc = sum(data) & 0xFF
        header = struct.pack("<HBHBBH", 0xAAAA, 0x02, MB_DSP, 0, crc, data_len)
        pkt = header + data
        if self._writer and not self._writer.is_closing():
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
        """Parse all complete packets from buffer."""
        while len(self._buf) >= 9:
            # RX header: RemoteID(2) CmdType(1) MBID(2,BE) Status(1) CRC(2) DataLen(2,BE)
            try:
                data_len = struct.unpack_from(">H", self._buf, 7)[0]
            except struct.error:
                break
            total = 9 + data_len + 1  # +1 for NUL terminator
            if len(self._buf) < total:
                break

            mbid = struct.unpack_from(">H", self._buf, 3)[0]
            payload_bytes = self._buf[9:9 + data_len]
            try:
                payload = payload_bytes.decode("utf-8", "replace").rstrip("\x00")
            except Exception:
                payload = ""

            self._buf = self._buf[total:]
            self._handle_push(mbid, payload)

    def _handle_push(self, mbid: int, payload: str) -> None:
        """Handle an incoming message from the speaker."""
        changed = True

        if mbid == MB_PLAYBACK_AUTH:
            # HOST MCU only — NEVER respond
            return

        elif mbid == MB_DEVICE_NAME:
            self.state.name = payload

        elif mbid == MB_FIRMWARE:
            self.state.firmware = payload

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
            except ValueError:
                pass

        elif mbid == MB_NOW_PLAYING:
            self._parse_now_playing(payload)

        elif mbid == MB_DEVICE_INFO:
            self._parse_device_info(payload)

        elif mbid == MB_FAVOURITES:
            self._parse_favourites(payload)

        elif mbid == MB_BT_STATUS:
            self.state.bt_status = payload

        elif mbid == MB_TIMEZONE:
            self.state.timezone = payload

        else:
            changed = False

        if changed:
            self._notify()

    def _parse_now_playing(self, payload: str) -> None:
        try:
            data = json.loads(payload)
            w = data.get("Window CONTENTS", data)
            self.state.title       = w.get("Title", "") or ""
            self.state.artist      = w.get("Artist", "") or ""
            self.state.album       = w.get("Album", "") or ""
            self.state.artwork_url = w.get("AlbumArt", "") or w.get("Artwork", "") or ""
            try:
                self.state.duration_ms = int(w.get("TotalTime", 0) or 0)
            except (TypeError, ValueError):
                self.state.duration_ms = 0
            # Live streams report no duration
            self.state.is_live = (self.state.duration_ms == 0)
        except Exception:
            _LOGGER.debug("Could not parse now-playing JSON")

    def _parse_device_info(self, payload: str) -> None:
        # Two shapes seen: JSON dict, or "key: value" line
        try:
            data = json.loads(payload)
            attr_map = {
                "model":        "model",
                "fw_version":   "firmware",
                "mac":          "mac",
                "mac_address":  "mac",
                "wifi_band":    "wifi_band",
                "net_mode":     "net_mode",
                "cast_version": "cast_version",
                "network_name": "name",
            }
            for k, v in data.items():
                attr = attr_map.get(k.lower())
                if attr and v:
                    setattr(self.state, attr, str(v))
            return
        except Exception:
            pass
        if ":" in payload:
            key, _, val = payload.partition(":")
            key = key.strip().lower().replace(" ", "_")
            val = val.strip()
            attr_map = {
                "model":         "model",
                "fw_version":    "firmware",
                "mac":           "mac",
                "mac_address":   "mac",
                "wifi_band":     "wifi_band",
                "net_mode":      "net_mode",
                "cast_version":  "cast_version",
                "network_name":  "name",
            }
            attr = attr_map.get(key)
            if attr:
                setattr(self.state, attr, val)

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
        """Build a TX packet: RemoteID(2,LE) CmdType(1) MBID(2,LE) Status(1) CRC(1) DataLen(2,LE) Payload NUL."""
        data = payload.encode("utf-8") + b"\x00"
        data_len = len(data)
        crc = sum(data) & 0xFF
        header = struct.pack("<HBHBBH", 0xAAAA, cmd_type, mbid, 0, crc, data_len)
        return header + data

    async def _send(self, cmd_type: int, mbid: int, payload: str) -> None:
        if self._writer and not self._writer.is_closing():
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
                    while len(buf) >= 9:
                        try:
                            data_len = struct.unpack_from(">H", buf, 7)[0]
                        except struct.error:
                            break
                        total = 9 + data_len + 1
                        if len(buf) < total:
                            break
                        r_mbid = struct.unpack_from(">H", buf, 3)[0]
                        r_payload = buf[9:9 + data_len].decode("utf-8", "replace").rstrip("\x00")
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
