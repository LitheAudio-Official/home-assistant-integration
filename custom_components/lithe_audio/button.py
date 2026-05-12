"""Button entities for Lithe Audio — chimes, reboot, factory defaults."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PRODUCT, DATA_COORDINATOR, DOMAIN, PRODUCT_CHIMES,
)
from .coordinator import LitheAudioCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LitheAudioCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    product = entry.data[CONF_PRODUCT]

    entities: list[ButtonEntity] = []

    # Chime buttons — one per slot up to product's chime count
    chime_count = PRODUCT_CHIMES.get(product, 0)
    for slot in range(1, chime_count + 1):
        entities.append(LitheChimeButton(coordinator, entry, slot))

    # Diagnostics
    entities.append(LitheRebootButton(coordinator, entry))
    entities.append(LitheFactoryResetButton(coordinator, entry))

    async_add_entities(entities)


class _LitheBaseButton(CoordinatorEntity[LitheAudioCoordinator], ButtonEntity):
    _attr_has_entity_name = True

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


class LitheChimeButton(_LitheBaseButton):
    """Play a single chime slot."""

    _attr_icon = "mdi:music-note"

    def __init__(self, coordinator, entry, slot: int):
        super().__init__(coordinator, entry)
        self._slot = slot
        self._attr_name = f"Chime {slot}"
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_chime_{slot}"

    async def async_press(self) -> None:
        await self._client.async_play_chime(self._slot)


class LitheRebootButton(_LitheBaseButton):
    _attr_name = "Reboot"
    _attr_icon = "mdi:restart"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = None

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_reboot"

    async def async_press(self) -> None:
        await self._client.async_reboot()


class LitheFactoryResetButton(_LitheBaseButton):
    _attr_name = "Factory Reset"
    _attr_icon = "mdi:lock-reset"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_factory_reset"

    async def async_press(self) -> None:
        await self._client.async_factory_reset()
