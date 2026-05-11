"""Base entity for Lithe Audio entities."""
from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import LitheAudioCoordinator


class LitheAudioEntity(CoordinatorEntity[LitheAudioCoordinator]):
    """Base class — provides device info, identifiers, and availability."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LitheAudioCoordinator) -> None:
        super().__init__(coordinator)
        self._client = coordinator.client
        # Stable per-device identifier — prefer MAC (from device info /
        # network info pushes), fall back to host:port at first boot.
        self._device_unique_id = (
            coordinator.config_entry.unique_id
            or f"{self._client.host}_{self._client.port}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        s = self._client.state
        connections = set()
        if s.mac:
            connections = {(CONNECTION_NETWORK_MAC, s.mac.lower())}
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_unique_id)},
            connections=connections,
            name=s.name or self.coordinator.config_entry.title,
            manufacturer=MANUFACTURER,
            model=s.model or self.coordinator.config_entry.data.get("model"),
            sw_version=s.firmware or None,
            configuration_url=f"http://{self._client.host}/",
        )

    @property
    def available(self) -> bool:
        return super().available and self._client.state.connected
