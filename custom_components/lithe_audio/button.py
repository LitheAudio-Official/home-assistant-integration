"""Button entities for Lithe Audio — chimes, reboot, factory defaults."""
from __future__ import annotations

import logging

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

_LOGGER = logging.getLogger(__name__)


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

    # Save-to-favourite buttons (slots 1-9) — press to save currently playing
    for slot in range(1, 10):
        entities.append(LitheSaveFavouriteButton(coordinator, entry, slot))

    # Heart button: saves current track to the NEXT free favourite slot.
    # Press once to add current track to favourites without picking a slot.
    entities.append(LitheHeartButton(coordinator, entry))

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


class LitheChimeButton(ButtonEntity):
    """Play a single chime slot — standalone (not coordinator-gated) for
    minimum latency between press and wire."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:music-note"

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry, slot: int):
        self._coordinator = coordinator
        self._client = coordinator.client
        self._entry = entry
        self._slot = slot
        self._attr_name = f"Chime {slot}"
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_chime_{slot}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        # Always show as available — the chime fire path tolerates disconnection
        # and we don't want HA gating the press on coordinator state freshness.
        return True

    async def async_press(self) -> None:
        _LOGGER.info("Chime button %d pressed", self._slot)
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


class LitheSaveFavouriteButton(ButtonEntity):
    """Save currently-playing track to a favourite slot.

    Press to save whatever is playing right now (Spotify Connect, Airable,
    etc.) into one of the speaker's favourite slots, accessible via
    play_favourite later.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:heart-plus"

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry, slot: int) -> None:
        self._coord = coordinator
        self._client = coordinator.client
        self._entry = entry
        self._slot = slot
        self._attr_name = f"Save to Favourite {slot}"
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_save_fav_{slot}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    async def async_press(self) -> None:
        _LOGGER.info("Save to favourite slot %d pressed", self._slot)
        await self._client.async_save_favourite(self._slot)


class LitheHeartButton(ButtonEntity):
    """♥ Heart button — saves current track to the next free favourite slot.

    Press once to add the currently-playing track to favourites without
    needing to pick a slot. Auto-increments: first press writes slot 1,
    second writes slot 2, ... up to slot 9. After all 9 are filled, it
    wraps around to slot 1 (oldest gets overwritten).

    Slot state is tracked on the client; the next free slot is determined
    by scanning the speaker's reported favourites list for the lowest
    unused number 1-9.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:heart"

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        self._coord = coordinator
        self._client = coordinator.client
        self._entry = entry
        self._attr_name = "♥ Save Current Track"
        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_heart_save"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(identifiers={(DOMAIN, self._entry.data["host"])})

    @property
    def available(self) -> bool:
        return self._client.state.connected

    def _next_free_slot(self) -> int:
        """Find next free favourite slot (1-9). Wraps around after 9."""
        used = {f.get("slot") for f in self._client.state.favourites if f.get("slot")}
        for slot in range(1, 10):
            if slot not in used:
                return slot
        # All 9 slots full — overwrite slot 1 (or the next round-robin slot)
        # We keep a private counter for round-robin past-full behaviour.
        last = getattr(self._client, "_heart_last_slot", 0)
        next_slot = (last % 9) + 1
        self._client._heart_last_slot = next_slot  # type: ignore[attr-defined]
        return next_slot

    async def async_press(self) -> None:
        slot = self._next_free_slot()
        _LOGGER.info("Heart pressed — saving current track to favourite slot %d", slot)
        await self._client.async_save_favourite(slot)
        self._client._heart_last_slot = slot  # type: ignore[attr-defined]
