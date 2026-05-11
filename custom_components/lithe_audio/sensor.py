"""Sensor platform — exposes diagnostic & info sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitheAudioConfigEntry
from .coordinator import LitheAudioCoordinator
from .entity import LitheAudioEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LitheAudioConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord = entry.runtime_data.coordinator
    async_add_entities([
        SourceSensor(coord),
        NowPlayingSensor(coord),
        FirmwareSensor(coord),
        PlayStateSensor(coord),
    ])


class SourceSensor(LitheAudioEntity, SensorEntity):
    """Current audio source (Spotify / AirPlay / etc)."""

    _attr_icon = "mdi:music-circle"

    def __init__(self, coordinator: LitheAudioCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_unique_id}_source"
        self._attr_name = "Source"

    @property
    def native_value(self) -> str | None:
        return self._client.state.source_name or None


class NowPlayingSensor(LitheAudioEntity, SensorEntity):
    """Combined 'Artist — Title' for easy automation triggers."""

    _attr_icon = "mdi:music"

    def __init__(self, coordinator: LitheAudioCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_unique_id}_now_playing"
        self._attr_name = "Now Playing"

    @property
    def native_value(self) -> str | None:
        s = self._client.state
        if not s.title:
            return None
        if s.artist:
            return f"{s.artist} — {s.title}"
        return s.title

    @property
    def extra_state_attributes(self) -> dict:
        s = self._client.state
        return {
            "title": s.title,
            "artist": s.artist,
            "album": s.album,
            "duration_ms": s.duration_ms,
            "position_ms": s.position_ms,
            "art_url": s.art_url,
        }


class FirmwareSensor(LitheAudioEntity, SensorEntity):
    """Firmware version (diagnostic)."""

    _attr_icon = "mdi:chip"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LitheAudioCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_unique_id}_firmware"
        self._attr_name = "Firmware"

    @property
    def native_value(self) -> str | None:
        return self._client.state.firmware or None


class PlayStateSensor(LitheAudioEntity, SensorEntity):
    """Raw play state string — sometimes useful for automations."""

    _attr_icon = "mdi:play-circle"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LitheAudioCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_unique_id}_play_state"
        self._attr_name = "Play State"

    @property
    def native_value(self) -> str:
        return self._client.state.play_state
