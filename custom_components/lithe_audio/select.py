"""Select entities for Lithe Audio DSP selectors."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PRODUCT, DATA_COORDINATOR, DOMAIN,
    DSP_EQ, DSP_HIGHPASS, DSP_OUTPUT, DSP_TUNING,
    EQ_PRESETS, HP_OPTIONS, OUT_OPTIONS, caps,
)
from .coordinator import LitheAudioCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LitheAudioCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    product = entry.data[CONF_PRODUCT]
    c = caps(product)

    entities: list[SelectEntity] = []
    if c["eq_select"]:
        entities.append(LitheEqSelect(coordinator, entry))
    if c["output_select"]:
        entities.append(LitheOutputSelect(coordinator, entry))
    if c["highpass_select"]:
        entities.append(LitheHighPassSelect(coordinator, entry))
    if c["tuning_select"]:
        entities.append(LitheTuningSelect(coordinator, entry))

    if entities:
        async_add_entities(entities)


class _LitheBaseSelect(CoordinatorEntity[LitheAudioCoordinator], SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = coordinator.client
        self._current: str = self._attr_options[0] if hasattr(self, '_attr_options') else ""

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    @property
    def current_option(self) -> str:
        return self._current

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class LitheEqSelect(_LitheBaseSelect):
    """EQ Preset selector."""

    _attr_name = "EQ Preset"
    _attr_options = EQ_PRESETS
    _attr_icon = "mdi:equalizer"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_eq"
        self._current = "Normal"

    async def async_select_option(self, option: str) -> None:
        idx = EQ_PRESETS.index(option) if option in EQ_PRESETS else 0
        self._current = option
        await self._client.async_dsp_command(DSP_EQ, idx)
        self.async_write_ha_state()


class LitheOutputSelect(_LitheBaseSelect):
    """Speaker Output selector."""

    _attr_name = "Speaker Output"
    _attr_options = OUT_OPTIONS
    _attr_icon = "mdi:speaker"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_output"
        self._current = "Stereo"

    async def async_select_option(self, option: str) -> None:
        idx = OUT_OPTIONS.index(option) if option in OUT_OPTIONS else 0
        self._current = option
        await self._client.async_dsp_command(DSP_OUTPUT, idx)
        self.async_write_ha_state()


class LitheHighPassSelect(_LitheBaseSelect):
    """High Pass Filter selector — PRO 2 only."""

    _attr_name = "High Pass Filter"
    _attr_options = HP_OPTIONS
    _attr_icon = "mdi:filter"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_highpass"
        self._current = "OFF"

    async def async_select_option(self, option: str) -> None:
        idx = HP_OPTIONS.index(option) if option in HP_OPTIONS else 0
        self._current = option
        await self._client.async_dsp_command(DSP_HIGHPASS, idx)
        self.async_write_ha_state()


class LitheTuningSelect(_LitheBaseSelect):
    """Speaker Tuning selector — PRO 2 only."""

    _attr_name = "Speaker Tuning"
    _attr_options = ["Enclosure 13L", "Open Back"]
    _attr_icon = "mdi:tune"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_tuning"
        self._current = "Enclosure 13L"

    async def async_select_option(self, option: str) -> None:
        idx = 0 if option == "Enclosure 13L" else 1
        self._current = option
        await self._client.async_dsp_command(DSP_TUNING, idx)
        self.async_write_ha_state()
