"""Switch entities for Lithe Audio toggles."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BT_DISC, BT_OFF, BT_ON, CONF_PRODUCT, DATA_COORDINATOR, DOMAIN,
    DSP_LOUDNESS, DSP_NIGHTMODE, LS10_PRODUCTS, PRODUCT_IO1, PRODUCT_PRO2, PRODUCT_V3,
)
from .coordinator import LitheAudioCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LitheAudioCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    product = entry.data[CONF_PRODUCT]

    if product not in LS10_PRODUCTS:
        return

    entities: list[SwitchEntity] = [
        LitheNightModeSwitch(coordinator, entry),
    ]

    # V3 and iO1: loudness is on/off
    if product in (PRODUCT_V3, PRODUCT_IO1):
        entities.append(LitheLoudnessSwitch(coordinator, entry))

    # PRO2 and V3 have Bluetooth
    if product in (PRODUCT_PRO2, PRODUCT_V3):
        entities.append(LitheBluetoothSwitch(coordinator, entry))

    async_add_entities(entities)


class _LitheBaseSwitch(CoordinatorEntity[LitheAudioCoordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = coordinator.client
        self._state = False

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    @property
    def is_on(self) -> bool:
        return self._state

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class LitheNightModeSwitch(_LitheBaseSwitch):
    """Night Mode switch."""

    _attr_name = "Night Mode"
    _attr_icon = "mdi:weather-night"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_nightmode"

    async def async_turn_on(self, **kwargs) -> None:
        self._state = True
        await self._client.async_dsp_command(DSP_NIGHTMODE, 1)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._state = False
        await self._client.async_dsp_command(DSP_NIGHTMODE, 0)
        self.async_write_ha_state()


class LitheLoudnessSwitch(_LitheBaseSwitch):
    """Loudness ON/OFF switch for V3 and iO1."""

    _attr_name = "Loudness"
    _attr_icon = "mdi:volume-plus"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_loudness_sw"

    async def async_turn_on(self, **kwargs) -> None:
        self._state = True
        await self._client.async_dsp_command(DSP_LOUDNESS, 1)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._state = False
        await self._client.async_dsp_command(DSP_LOUDNESS, 0)
        self.async_write_ha_state()


class LitheBluetoothSwitch(_LitheBaseSwitch):
    """Bluetooth enable/disable switch."""

    _attr_name = "Bluetooth"
    _attr_icon = "mdi:bluetooth"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_bluetooth"

    async def async_turn_on(self, **kwargs) -> None:
        self._state = True
        await self._client.async_bluetooth(BT_ON)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._state = False
        await self._client.async_bluetooth(BT_OFF)
        self.async_write_ha_state()
