"""Media player entity for Lithe Audio speakers."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitheAudioConfigEntry
from .const import (
    INPUT_ONLY_SOURCES,
    SEEKABLE_SOURCES,
    SOURCE_NAMES,
)
from .entity import LitheAudioEntity

_LOGGER = logging.getLogger(__name__)

# Map LUCI play_state strings to HA's MediaPlayerState
_STATE_MAP = {
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "buffering": MediaPlayerState.BUFFERING,
    "idle": MediaPlayerState.IDLE,
    "stopped": MediaPlayerState.IDLE,
}

# Sources the user can pick from the UI (excludes streaming services
# which can only be initiated from their respective apps).
_SELECTABLE_SOURCES = {
    "AUX In": 13,
    "SPDIF": 14,
    "Bluetooth": 19,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LitheAudioConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the media player for this config entry."""
    runtime = entry.runtime_data
    async_add_entities([LitheAudioMediaPlayer(runtime.coordinator)])


class LitheAudioMediaPlayer(LitheAudioEntity, MediaPlayerEntity):
    """A Lithe Audio speaker as a HA media player."""

    _attr_name = None  # Use device name; this is THE primary entity
    _attr_device_class = None
    _attr_media_content_type = MediaType.MUSIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_unique_id}_media_player"
        self._attr_source_list = list(_SELECTABLE_SOURCES.keys())

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        feat = (
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.PLAY_MEDIA
        )
        s = self._client.state
        if s.source_id in SEEKABLE_SOURCES:
            feat |= MediaPlayerEntityFeature.SEEK
        return feat

    @property
    def state(self) -> MediaPlayerState | None:
        if not self._client.state.connected:
            return None
        return _STATE_MAP.get(self._client.state.play_state, MediaPlayerState.IDLE)

    @property
    def volume_level(self) -> float | None:
        return self._client.state.volume / 100.0

    @property
    def is_volume_muted(self) -> bool:
        return self._client.state.muted

    @property
    def source(self) -> str | None:
        return self._client.state.source_name or None

    @property
    def media_title(self) -> str | None:
        return self._client.state.title or None

    @property
    def media_artist(self) -> str | None:
        return self._client.state.artist or None

    @property
    def media_album_name(self) -> str | None:
        return self._client.state.album or None

    @property
    def media_image_url(self) -> str | None:
        return self._client.state.art_url or None

    @property
    def media_duration(self) -> int | None:
        ms = self._client.state.duration_ms
        return int(ms / 1000) if ms else None

    @property
    def media_position(self) -> int | None:
        ms = self._client.state.position_ms
        return int(ms / 1000) if ms else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._client.state
        return {
            "source_id": s.source_id,
            "host": self._client.host,
            "model": s.model,
            "firmware": s.firmware,
            "mac": s.mac,
        }

    # ── Commands ──────────────────────────────────────────────────────────

    async def async_media_play(self) -> None:
        await self._client.async_play()

    async def async_media_pause(self) -> None:
        await self._client.async_pause()

    async def async_media_stop(self) -> None:
        await self._client.async_stop_playback()

    async def async_media_next_track(self) -> None:
        await self._client.async_next()

    async def async_media_previous_track(self) -> None:
        await self._client.async_previous()

    async def async_media_seek(self, position: float) -> None:
        await self._client.async_seek(position)

    async def async_set_volume_level(self, volume: float) -> None:
        await self._client.async_set_volume(int(round(volume * 100)))

    async def async_volume_up(self) -> None:
        await self._client.async_set_volume(min(100, self._client.state.volume + 5))

    async def async_volume_down(self) -> None:
        await self._client.async_set_volume(max(0, self._client.state.volume - 5))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._client.async_set_mute(mute)

    async def async_select_source(self, source: str) -> None:
        if source not in _SELECTABLE_SOURCES:
            _LOGGER.warning("Source %s cannot be selected via API", source)
            return
        # The LUCI API switches input via MB#95 (start) for line/AUX sources.
        # Bluetooth is enabled via MB#209 ON.
        if source in ("AUX In", "SPDIF"):
            await self._client.async_input_start()
        elif source == "Bluetooth":
            from .const import BT_ON, MB_BLUETOOTH
            await self._client.async_send_raw(MB_BLUETOOTH, BT_ON)

    async def async_play_media(
        self,
        media_type: str,
        media_id: str,
        **kwargs: Any,
    ) -> None:
        """Play either a URL, a local chime path, or a preset.

        media_id formats recognised:
          - "http(s)://..."          → direct URL via MB#41 PLAYITEM:
          - "/system/usr/songN.mp3"  → direct on-device file
          - "chime:N"                → MB#80 indexed cue
          - "preset:N"               → MB#70 favourite recall
        """
        if media_id.startswith("chime:"):
            try:
                await self._client.async_play_chime(int(media_id.split(":", 1)[1]))
            except ValueError:
                _LOGGER.error("Invalid chime index in %s", media_id)
            return
        if media_id.startswith("preset:"):
            try:
                await self._client.async_preset_play(int(media_id.split(":", 1)[1]))
            except ValueError:
                _LOGGER.error("Invalid preset slot in %s", media_id)
            return
        # Default: treat as direct URL / path for MB#41 PLAYITEM
        if media_id.startswith("/system/"):
            await self._client.async_play_direct(f"DIRECT:{media_id}")
        else:
            await self._client.async_play_direct(media_id)
