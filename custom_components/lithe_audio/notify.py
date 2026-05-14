"""Tannoy / PA-override notify service for Lithe Audio.

Usage:
    service: notify.lithe_tannoy
    data:
      message: "http://server/announcement.mp3"
      data:
        mode: start              # or "end"
        volume: 80               # announcement volume (start only)
        speakers:                # list of host IPs OR entity_ids
          - 192.168.1.38
          - media_player.deck_v3

The service saves volume + play_state for each target speaker on `start`,
pauses them, raises volume, then plays the URL on the first speaker. On
`end` it restores volume and resumes if the speaker was previously playing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.notify import BaseNotificationService
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DATA_TANNOY_SAVED, DOMAIN
from .coordinator import LitheAudioCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_get_service(
    hass: HomeAssistant,
    config: dict,
    discovery_info: dict | None = None,
) -> "LitheTannoyNotify":
    """Return the Tannoy notify service."""
    return LitheTannoyNotify(hass)


def _resolve_coordinator(
    hass: HomeAssistant, target: str
) -> LitheAudioCoordinator | None:
    """Resolve a speaker reference (IP or entity_id) to its coordinator."""
    bucket = hass.data.get(DOMAIN, {})

    # Strip entity_id form (e.g. "media_player.foo" → look up its entry)
    if "." in target:
        ent_reg = None
        try:
            from homeassistant.helpers import entity_registry as er
            ent_reg = er.async_get(hass)
        except Exception:
            pass
        if ent_reg:
            ent = ent_reg.async_get(target)
            if ent and ent.config_entry_id and ent.config_entry_id in bucket:
                return bucket[ent.config_entry_id].get(DATA_COORDINATOR)
        return None

    # Otherwise treat as host IP
    for _entry_id, entry_data in bucket.items():
        coord: LitheAudioCoordinator = entry_data.get(DATA_COORDINATOR)
        if coord and coord.client.host == target:
            return coord
    return None


class LitheTannoyNotify(BaseNotificationService):
    """Tannoy/PA override service."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        # Per-tannoy-session saved state, keyed by host IP
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN].setdefault(DATA_TANNOY_SAVED, {})

    @property
    def _saved(self) -> dict[str, dict[str, Any]]:
        return self.hass.data[DOMAIN][DATA_TANNOY_SAVED]

    async def async_send_message(self, message: str = "", **kwargs: Any) -> None:
        data     = kwargs.get("data") or {}
        mode     = (data.get("mode") or "start").lower()
        speakers = data.get("speakers") or []
        volume   = int(data.get("volume", 80))

        if not isinstance(speakers, list):
            speakers = [speakers]

        if mode == "start":
            await self._tannoy_start(speakers, message, volume)
        elif mode == "end":
            await self._tannoy_end(speakers)
        else:
            _LOGGER.warning("Unknown tannoy mode: %s", mode)

    async def _tannoy_start(self, speakers: list[str], url: str, volume: int) -> None:
        """Save vol+play_state, pause, raise volume, play URL on first speaker.

        Note on PAUSE vs STOP: we use PAUSE because STOP causes this
        firmware to push SPEAKER_INACTIVE and then refuse PLAYITEM:DIRECT
        entirely. PAUSE keeps the audio subsystem alive while reducing
        the chance of Spotify Connect interfering with our PLAYITEM.
        """
        first_coord: LitheAudioCoordinator | None = None

        for target in speakers:
            coord = _resolve_coordinator(self.hass, target)
            if not coord:
                _LOGGER.warning("Tannoy: cannot resolve speaker %s", target)
                continue

            client = coord.client
            host   = client.host

            # Save current state
            self._saved[host] = {
                "volume":     client.state.volume,
                "play_state": client.state.play_state,
                "muted":      client.state.muted,
                "source":     client.state.source_id,
            }

            # Pause + unmute + raise volume
            try:
                if client.state.muted:
                    await client.async_mute(False)
                if client.state.play_state == "playing":
                    await client.async_pause()
                await asyncio.sleep(0.15)
                await client.async_set_volume(volume)
            except Exception as e:
                _LOGGER.error("Tannoy start failed for %s: %s", host, e)

            if first_coord is None:
                first_coord = coord

        # Play the URL on the first speaker
        if first_coord and url:
            try:
                await first_coord.client.async_play_url(url)
                # Verify source actually became 17 (Direct URL)
                await asyncio.sleep(1.5)
                src = first_coord.client.state.source_id
                if src != 17:
                    _LOGGER.warning(
                        "Tannoy: source did not switch to Direct URL "
                        "(still source=%d). Announcement may not be audible. "
                        "URL: %s",
                        src, url,
                    )
                else:
                    _LOGGER.info(
                        "Tannoy: source switched to Direct URL playing %s",
                        url,
                    )
            except Exception as e:
                _LOGGER.error("Tannoy play_url failed: %s", e)

    async def _tannoy_end(self, speakers: list[str]) -> None:
        """Restore volume and resume on each speaker."""
        for target in speakers:
            coord = _resolve_coordinator(self.hass, target)
            if not coord:
                continue
            client = coord.client
            host   = client.host
            sv = self._saved.pop(host, None)
            if not sv:
                continue
            try:
                # Stop the announcement first
                await client.async_stop()
                await asyncio.sleep(0.1)
                await client.async_set_volume(int(sv.get("volume", 50)))
                if sv.get("muted"):
                    await client.async_mute(True)
                # Resume if it was playing before
                if sv.get("play_state") == "playing":
                    await asyncio.sleep(0.2)
                    await client.async_resume()
            except Exception as e:
                _LOGGER.error("Tannoy end failed for %s: %s", host, e)
