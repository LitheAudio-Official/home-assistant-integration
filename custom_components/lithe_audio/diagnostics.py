"""Diagnostics support for Lithe Audio.

Returns a redacted snapshot of the integration state for inclusion in
GitHub issue reports — without leaking client certificates, private keys,
or precise host IPs.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import LitheAudioConfigEntry
from .const import CONF_CERT_KEY, CONF_CERT_PEM, CONF_MAC

REDACT_KEYS = {CONF_CERT_PEM, CONF_CERT_KEY, "host", CONF_MAC, "mac", "serial"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LitheAudioConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    state = runtime.client.state
    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), REDACT_KEYS),
            "unique_id": "**REDACTED**" if entry.unique_id else None,
        },
        "client": {
            "platform": runtime.client.platform,
            "port": runtime.client.port,
            "is_ls10": runtime.client.is_ls10,
            "has_cert": bool(entry.data.get(CONF_CERT_PEM)),
        },
        "state": async_redact_data(
            {
                "connected": state.connected,
                "play_state": state.play_state,
                "volume": state.volume,
                "muted": state.muted,
                "source_id": state.source_id,
                "source_name": state.source_name,
                "title": state.title,
                "artist": state.artist,
                "album": state.album,
                "duration_ms": state.duration_ms,
                "position_ms": state.position_ms,
                "name": state.name,
                "model": state.model,
                "firmware": state.firmware,
                "mac": state.mac,
                "serial": state.serial,
                "timezone": state.timezone,
            },
            REDACT_KEYS,
        ),
    }
