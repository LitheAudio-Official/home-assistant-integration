"""Sensor entities for Lithe Audio — read-only state."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT
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
        LitheIpAddressSensor(coordinator, entry),
        LitheSSIDSensor(coordinator, entry),
        LitheRSSISensor(coordinator, entry),
        LitheNetworkStatusSensor(coordinator, entry),
        LitheSpeakerStatusSensor(coordinator, entry),
        LithePlayerRoleSensor(coordinator, entry),
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


class LitheIpAddressSensor(_LitheBaseSensor):
    """Speaker's network IP address (from MB#123)."""
    _attr_name = "IP Address"
    _attr_icon = "mdi:ip-network"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_ip"

    @property
    def native_value(self) -> str:
        # Fall back to the host IP we connected to if speaker hasn't pushed yet
        return self._client.state.ip_address or self._entry.data.get("host", "Unknown")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "interface": self._client.state.network_interface or "Unknown",
        }


class LitheSSIDSensor(_LitheBaseSensor):
    """Connected Wi-Fi SSID (from NV item read via MB#208)."""
    _attr_name = "SSID"
    _attr_icon = "mdi:wifi-marker"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_ssid"

    @property
    def native_value(self) -> str:
        return self._client.state.ssid or "Unknown"


class LitheRSSISensor(_LitheBaseSensor):
    """Wi-Fi signal strength in dBm (from MB#151)."""
    _attr_name = "Wi-Fi Signal"
    _attr_icon = "mdi:wifi-strength-2"
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_rssi"

    @property
    def native_value(self) -> int | None:
        v = self._client.state.wifi_rssi_dbm
        return v if v != 0 else None


class LitheNetworkStatusSensor(_LitheBaseSensor):
    """Active network interface (WLAN/Ethernet/P2P/Config) from MB#124."""
    _attr_name = "Network"
    _attr_icon = "mdi:lan-connect"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_network_status"

    @property
    def native_value(self) -> str:
        return self._client.state.network_status or "Unknown"


class LitheSpeakerStatusSensor(_LitheBaseSensor):
    """Speaker status (Connected/Standby) — derived from MB#124."""
    _attr_name = "Speaker Status"
    _attr_icon = "mdi:speaker-wireless"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_speaker_status"

    @property
    def native_value(self) -> str:
        return self._client.state.speaker_status or ("Connected" if self._client.state.connected else "Disconnected")


class LithePlayerRoleSensor(_LitheBaseSensor):
    """Speaker's role in a grouped playback session (Free/Master/Slave).

    From Lithe API_NEW page 25 'getenv PlayerState':
      Free   — standalone, can trigger chimes itself
      Master — leads a group, can trigger chimes for the group
      Slave  — synced playback controlled by another device.
               Chime commands silently fail because audio is routed
               via the master. Trigger cues on the master instead.
    """
    _attr_name = "Player Role"
    _attr_icon = "mdi:account-music"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_player_role"

    @property
    def native_value(self) -> str:
        return self._client.state.player_role or "Unknown"
