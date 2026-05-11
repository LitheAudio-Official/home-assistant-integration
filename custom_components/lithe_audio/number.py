"""Number platform — exposes volume 0-100 as a number entity.

The primary volume control is on the media player; this duplicate is
useful for automations that want to set an exact integer level without
dealing with the 0.0–1.0 float scale.
"""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, PERCENTAGE
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
    async_add_entities([VolumeNumber(entry.runtime_data.coordinator)])


class VolumeNumber(LitheAudioEntity, NumberEntity):
    """Volume 0-100 as a number entity."""

    _attr_icon = "mdi:volume-high"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False  # off by default; media_player has volume

    def __init__(self, coordinator: LitheAudioCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_unique_id}_volume_number"
        self._attr_name = "Volume"

    @property
    def native_value(self) -> float:
        return float(self._client.state.volume)

    async def async_set_native_value(self, value: float) -> None:
        await self._client.async_set_volume(int(value))
