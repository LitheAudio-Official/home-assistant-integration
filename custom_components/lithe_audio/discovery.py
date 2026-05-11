"""Lightweight SSDP (LSSDP) discovery for Lithe Audio speakers.

Lithe uses a proprietary SSDP variant on the standard 239.255.255.250
multicast group but port 1800 (not 1900) with simpler headers. We send an
M-SEARCH and harvest responses for ~3 seconds to enumerate speakers on the
local network without parsing SSDP XML.
"""
from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass
from typing import Any

from .const import (
    LS10_MODELS,
    LSSDP_MSEARCH,
    LSSDP_MULTICAST_ADDR,
    LSSDP_PORT,
    PLATFORM_LS10,
    PLATFORM_LS9,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class DiscoveredDevice:
    """One LSSDP response from a Lithe Audio speaker."""

    host: str
    port: int
    name: str
    model: str
    mac: str
    platform: str            # "LS9" or "LS10"
    firmware: str = ""
    cast_firmware: str = ""
    net_mode: str = ""
    speaker_type: str = ""
    raw_headers: dict[str, str] | None = None

    @property
    def unique_id(self) -> str:
        """Stable per-device identifier (MAC preferred)."""
        return (self.mac or f"{self.host}:{self.port}").lower().replace(":", "")


class _LSSDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, responses: list[tuple[bytes, tuple[str, int]]]) -> None:
        self._responses = responses

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._responses.append((data, addr))

    def error_received(self, exc: Exception) -> None:  # noqa: D401
        _LOGGER.debug("LSSDP socket error: %s", exc)


def _parse_response(data: bytes, src_host: str) -> DiscoveredDevice | None:
    """Parse one LSSDP response/NOTIFY datagram."""
    try:
        text = data.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or not (
        lines[0].startswith("HTTP/1.1") or lines[0].startswith("NOTIFY")
    ):
        return None
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip().upper()] = value.strip()

    # Required hint: this is a LSSDP packet
    if "LSSDP" not in headers.get("VERSION", "") and "LSSDP" not in text:
        return None

    mac = headers.get("USN", "").lower()
    if not mac:
        return None
    try:
        port = int(headers.get("PORT", "7777"))
    except ValueError:
        port = 7777

    model = headers.get("CAST_MODEL", "")
    speaker_type = headers.get("SPEAKERTYPE", "")
    # Crude platform inference: SOURCE_LIST starting with "LS10::" or
    # CAST_MODEL strings that match the LS10 product family.
    source_list = headers.get("SOURCE_LIST", "")
    if source_list.startswith("LS10") or any(m in model for m in LS10_MODELS):
        platform = PLATFORM_LS10
    else:
        platform = PLATFORM_LS9

    # Format MAC nicely (12 hex chars -> AA:BB:CC:DD:EE:FF)
    mac_clean = "".join(c for c in mac if c in "0123456789abcdefABCDEF")
    if len(mac_clean) == 12:
        mac_pretty = ":".join(mac_clean[i:i + 2] for i in range(0, 12, 2)).upper()
    else:
        mac_pretty = mac.upper()

    return DiscoveredDevice(
        host=src_host,
        port=port,
        name=headers.get("DEVICENAME", model or src_host),
        model=model,
        mac=mac_pretty,
        platform=platform,
        firmware=headers.get("FWVERSION", ""),
        cast_firmware=headers.get("CAST_FWVERSION", ""),
        net_mode=headers.get("NETMODE", ""),
        speaker_type=speaker_type,
        raw_headers=headers,
    )


async def async_discover(timeout: float = 3.0) -> list[DiscoveredDevice]:
    """Send LSSDP M-SEARCH and collect responses.

    Returns one ``DiscoveredDevice`` per unique MAC address. Safe to call
    from the Home Assistant event loop.
    """
    loop = asyncio.get_running_loop()
    responses: list[tuple[bytes, tuple[str, int]]] = []

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # We bind to an ephemeral port and join the LSSDP group so NOTIFYs
    # arrive too. Multicast TTL of 4 is generous for typical home LANs.
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
    sock.setblocking(False)
    sock.bind(("", 0))

    transport: asyncio.BaseTransport | None = None
    try:
        transport, _proto = await loop.create_datagram_endpoint(
            lambda: _LSSDPProtocol(responses), sock=sock,
        )
        sock.sendto(LSSDP_MSEARCH, (LSSDP_MULTICAST_ADDR, LSSDP_PORT))
        await asyncio.sleep(timeout)
    finally:
        if transport is not None:
            transport.close()

    seen: dict[str, DiscoveredDevice] = {}
    for data, addr in responses:
        dev = _parse_response(data, addr[0])
        if dev is None:
            continue
        # Prefer LS10 detection over LS9 if we get duplicate frames
        key = dev.unique_id
        if key not in seen or dev.platform == PLATFORM_LS10:
            seen[key] = dev
    return list(seen.values())
