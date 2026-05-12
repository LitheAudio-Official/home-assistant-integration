"""Diagnostics support for Lithe Audio."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CERT_PATH, CONF_KEY_PATH, DATA_COORDINATOR, DOMAIN, PRODUCT_NAMES, caps,
)
from .coordinator import LitheAudioCoordinator

# Don't leak cert paths or device MAC in the downloadable diagnostic
TO_REDACT = {CONF_CERT_PATH, CONF_KEY_PATH, "mac", "mac_address"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coord: LitheAudioCoordinator | None = entry_data.get(DATA_COORDINATOR)

    product = entry.data.get("product", "")

    state_snapshot: dict[str, Any] = {}
    fav_count = 0
    if coord and coord.client:
        s = coord.client.state
        state_snapshot = {
            "connected":    s.connected,
            "name":         s.name,
            "firmware":     s.firmware,
            "model":        s.model,
            "wifi_band":    s.wifi_band,
            "timezone":     s.timezone,
            "cast_version": s.cast_version,
            "net_mode":     s.net_mode,
            "play_state":   s.play_state,
            "source_id":    s.source_id,
            "source_name":  s.source_name,
            "volume":       s.volume,
            "muted":        s.muted,
            "is_live":      s.is_live,
            "bt_status":    s.bt_status,
            "title":        s.title,
            "artist":       s.artist,
            "album":        s.album,
            "duration_ms":  s.duration_ms,
        }
        fav_count = len(s.favourites)

    return {
        "entry": {
            "title":   entry.title,
            "version": entry.version,
            "data":    async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "product": {
            "id":           product,
            "display_name": PRODUCT_NAMES.get(product, product),
            "capabilities": caps(product),
        },
        "state":           async_redact_data(state_snapshot, TO_REDACT),
        "favourite_count": fav_count,
    }
