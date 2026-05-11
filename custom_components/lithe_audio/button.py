"""Button platform — exposes chimes, preset slots, and reboot as buttons."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LitheAudioConfigEntry
from .const import CONF_MODEL, MODEL_GENERIC, PRODUCT_CHIMES
from .coordinator import LitheAudioCoordinator
from .entity import LitheAudioEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LitheAudioConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    coord = runtime.coordinator
    model = entry.data.get(CONF_MODEL, MODEL_GENERIC)
    chime_count = PRODUCT_CHIMES.get(model, 15)

    entities: list[ButtonEntity] = []
    # Chimes — one button per embedded cue. Default labels follow the
    # common Lithe portal convention; users can rename via the UI.
    chime_labels = {
        1: "Doorbell", 2: "Doorbell Alt", 3: "Alarm", 4: "Alarm Alt",
        5: "Chime", 6: "Chime Alt", 7: "Notification", 8: "Notification Alt",
    }
    for i in range(1, chime_count + 1):
        entities.append(ChimeButton(coord, i, chime_labels.get(i, f"Chime {i}")))
    # Preset recalls 1..9
    for slot in range(1, 10):
        entities.append(PresetPlayButton(coord, slot))
    # Reboot
    entities.append(RebootButton(coord))
    async_add_entities(entities)


class ChimeButton(LitheAudioEntity, ButtonEntity):
    """Triggers an embedded /system/usr/songN.mp3 cue."""

    _attr_icon = "mdi:bell-ring"

    def __init__(
        self,
        coordinator: LitheAudioCoordinator,
        index: int,
        label: str,
    ) -> None:
        super().__init__(coordinator)
        self._index = index
        self._attr_unique_id = f"{self._device_unique_id}_chime_{index}"
        self._attr_name = f"Chime {index} ({label})"

    async def async_press(self) -> None:
        await self._client.async_play_chime(self._index)


class PresetPlayButton(LitheAudioEntity, ButtonEntity):
    """Recall a saved favourite from MB#70 slot N."""

    _attr_icon = "mdi:star-outline"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: LitheAudioCoordinator, slot: int) -> None:
        super().__init__(coordinator)
        self._slot = slot
        self._attr_unique_id = f"{self._device_unique_id}_preset_play_{slot}"
        self._attr_name = f"Preset {slot}"

    async def async_press(self) -> None:
        await self._client.async_preset_play(self._slot)


class RebootButton(LitheAudioEntity, ButtonEntity):
    """Reboot the speaker (MB#37)."""

    _attr_icon = "mdi:restart"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = None  # avoid the 'restart' class so it's not hidden

    def __init__(self, coordinator: LitheAudioCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._device_unique_id}_reboot"
        self._attr_name = "Reboot"

    async def async_press(self) -> None:
        await self._client.async_reboot()
