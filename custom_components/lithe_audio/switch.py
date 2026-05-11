"""Switch platform — mute, AUX input enable, Bluetooth power."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitheAudioConfigEntry
from .const import BT_OFF, BT_ON, MB_BLUETOOTH
from .coordinator import LitheAudioCoordinator
from .entity import LitheAudioEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LitheAudioConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coord = entry.runtime_data.coordinator
    async_add_entities([
        MuteSwitch(coord),
        LineInSwitch(coord),
        BluetoothSwitch(coord),
    ])


class MuteSwitch(LitheAudioEntity, SwitchEntity):
    """Mute switch — handy for automations that want a binary toggle."""

    _attr_icon = "mdi:volume-mute"

    def __init__(self, coordinator: LitheAudioCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_unique_id}_mute"
        self._attr_name = "Mute"

    @property
    def is_on(self) -> bool:
        return self._client.state.muted

    async def async_turn_on(self, **_kwargs) -> None:
        await self._client.async_set_mute(True)

    async def async_turn_off(self, **_kwargs) -> None:
        await self._client.async_set_mute(False)


class LineInSwitch(LitheAudioEntity, SwitchEntity):
    """Enable / disable AUX / line-in input."""

    _attr_icon = "mdi:audio-input-rca"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LitheAudioCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_unique_id}_linein"
        self._attr_name = "Line In"

    @property
    def is_on(self) -> bool:
        # Source IDs 13 (AUX) and 14 (SPDIF) indicate line-in is active
        return self._client.state.source_id in (13, 14)

    async def async_turn_on(self, **_kwargs) -> None:
        await self._client.async_input_start()

    async def async_turn_off(self, **_kwargs) -> None:
        await self._client.async_input_stop()


class BluetoothSwitch(LitheAudioEntity, SwitchEntity):
    """Enable Bluetooth receiver mode."""

    _attr_icon = "mdi:bluetooth"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: LitheAudioCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_unique_id}_bluetooth"
        self._attr_name = "Bluetooth"

    @property
    def is_on(self) -> bool:
        # Source 19 = Bluetooth is currently the active input
        return self._client.state.source_id == 19

    async def async_turn_on(self, **_kwargs) -> None:
        await self._client.async_send_raw(MB_BLUETOOTH, BT_ON)

    async def async_turn_off(self, **_kwargs) -> None:
        await self._client.async_send_raw(MB_BLUETOOTH, BT_OFF)
