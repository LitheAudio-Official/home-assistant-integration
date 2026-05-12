"""Sensor entities for Lithe Audio — read-only state."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PRODUCT, DATA_COORDINATOR, DOMAIN, PRODUCT_NAMES, SOURCES
from .coordinator import LitheAudioCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LitheAudioCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([
        LitheSourceSensor(coordinator, entry),
        LitheFirmwareSensor(coordinator, entry),
        LitheMacSensor(coordinator, entry),
        LitheWifiBandSensor(coordinator, entry),
        LitheTimezoneSensor(coordinator, entry),
        LitheUptimeSensor(coordinator, entry),
    ])


class _LitheBaseSensor(CoordinatorEntity[LitheAudioCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._client = coordinator.client

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()


class LitheSourceSensor(_LitheBaseSensor):
    _attr_name = "Active Source"
    _attr_icon = "mdi:music-circle"
    _attr_entity_category = None  # Visible in main view

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_source"

    @property
    def native_value(self) -> str:
        return self._client.state.source_name


class LitheFirmwareSensor(_LitheBaseSensor):
    _attr_name = "Firmware"
    _attr_icon = "mdi:chip"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_firmware"

    @property
    def native_value(self) -> str:
        return self._client.state.firmware or "Unknown"


class LitheMacSensor(_LitheBaseSensor):
    _attr_name = "MAC Address"
    _attr_icon = "mdi:ethernet"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_mac"

    @property
    def native_value(self) -> str:
        return self._client.state.mac or "Unknown"


class LitheWifiBandSensor(_LitheBaseSensor):
    _attr_name = "Wi-Fi Band"
    _attr_icon = "mdi:wifi"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_wifi_band"

    @property
    def native_value(self) -> str:
        return self._client.state.wifi_band or "Unknown"


class LitheTimezoneSensor(_LitheBaseSensor):
    _attr_name = "Timezone"
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_timezone"

    @property
    def native_value(self) -> str:
        return self._client.state.timezone or "Unknown"


class LitheUptimeSensor(_LitheBaseSensor):
    """Uptime from Cast HTTP — read via coordinator extra data."""
    _attr_name = "Uptime"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "h"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_uptime"
        self._uptime_h: float = 0.0

    @property
    def native_value(self) -> float:
        return round(self._uptime_h, 1)

    def set_uptime(self, hours: float) -> None:
        self._uptime_h = hours
        self.async_write_ha_state()
