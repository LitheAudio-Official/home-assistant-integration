"""Lithe Audio media player entity."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_PRODUCT, DATA_COORDINATOR, DOMAIN, PRODUCT_NAMES,
    PRODUCT_SOURCES, SOURCES,
)
from .coordinator import LitheAudioCoordinator

_LOGGER = logging.getLogger(__name__)

# Media content types we accept for play_media
_PLAYABLE_TYPES = {
    MediaType.MUSIC, MediaType.URL,
    "audio/mp3", "audio/mpeg", "audio/wav", "audio/x-wav",
    "audio/aac", "audio/flac", "audio/ogg", "audio/x-mpegurl",
}

# Internal content_id prefix for favourites
_FAV_PREFIX = "lithe_fav://"
# Internal content_id prefix for chimes
_CHIME_PREFIX = "lithe_chime://"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: LitheAudioCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    async_add_entities([LitheAudioMediaPlayer(coordinator, entry)])


class LitheAudioMediaPlayer(CoordinatorEntity[LitheAudioCoordinator], MediaPlayerEntity):
    """Lithe Audio speaker media player entity."""

    _attr_has_entity_name = True
    _attr_name = None  # Use device name

    def __init__(self, coordinator: LitheAudioCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._product = entry.data[CONF_PRODUCT]
        self._client = coordinator.client

        self._attr_unique_id = f"{entry.data['host']}_{entry.entry_id}_player"

        # Build source list from product capability matrix
        src_ids = PRODUCT_SOURCES.get(self._product, list(SOURCES.keys()))
        self._source_list = [SOURCES[s] for s in src_ids
                             if s in SOURCES and SOURCES[s] != "No Source"]
        # Reverse-lookup name → id
        self._source_id_by_name = {SOURCES[s]: s for s in src_ids if s in SOURCES}

    @property
    def device_info(self) -> DeviceInfo:
        state = self._client.state
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.data["host"])},
            name=state.name or PRODUCT_NAMES.get(self._product, "Lithe Audio"),
            manufacturer="Lithe Audio",
            model=state.model or PRODUCT_NAMES.get(self._product),
            sw_version=state.firmware or None,
            # Bare http:// to the speaker IP — most reliable URL across
            # firmware versions (the Cast eureka_info endpoint isn't always
            # exposed). Some users may need to use the Lithe app instead.
            configuration_url=f"http://{self._client.host}",
        )

    # ── Feature flags ──────────────────────────────────────────────────────

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        flags = (
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.NEXT_TRACK
            | MediaPlayerEntityFeature.PREVIOUS_TRACK
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.PLAY_MEDIA
            | MediaPlayerEntityFeature.BROWSE_MEDIA
            | MediaPlayerEntityFeature.SHUFFLE_SET
            | MediaPlayerEntityFeature.REPEAT_SET
        )
        # SEEK only when source is not a live stream
        if not self._client.state.is_live:
            flags |= MediaPlayerEntityFeature.SEEK
        return flags

    # ── State properties ───────────────────────────────────────────────────

    @property
    def state(self) -> MediaPlayerState:
        if not self._client.state.connected:
            return MediaPlayerState.OFF
        return {
            "playing":    MediaPlayerState.PLAYING,
            "paused":     MediaPlayerState.PAUSED,
            "stopped":    MediaPlayerState.IDLE,
            "connecting": MediaPlayerState.BUFFERING,
            "buffering":  MediaPlayerState.BUFFERING,
        }.get(self._client.state.play_state, MediaPlayerState.IDLE)

    @property
    def available(self) -> bool:
        return self._client.state.connected

    @property
    def volume_level(self) -> float:
        return self._client.state.volume / 100.0

    @property
    def is_volume_muted(self) -> bool:
        return self._client.state.muted

    @property
    def source(self) -> str | None:
        return self._client.state.source_name

    @property
    def source_list(self) -> list[str]:
        return self._source_list

    @property
    def media_title(self) -> str | None:
        return self._client.state.title or None

    @property
    def media_artist(self) -> str | None:
        return self._client.state.artist or None

    @property
    def media_album_name(self) -> str | None:
        return self._client.state.album or None

    @property
    def media_image_url(self) -> str | None:
        return self._client.state.artwork_url or None

    @property
    def media_duration(self) -> int | None:
        d = self._client.state.duration_ms
        return d // 1000 if d else None

    @property
    def media_position(self) -> int | None:
        p = self._client.state.position_ms
        return p // 1000 if p else None

    @property
    def media_position_updated_at(self):
        """When position was last fetched. Lets HA extrapolate live position."""
        ts = self._client.state.position_updated_at
        if not ts:
            return None
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc)

    @property
    def media_content_type(self) -> MediaType:
        return MediaType.MUSIC

    @property
    def shuffle(self) -> bool:
        return self._client.state.shuffle

    @property
    def repeat(self) -> RepeatMode:
        m = (self._client.state.repeat or "off").lower()
        return {
            "off": RepeatMode.OFF,
            "all": RepeatMode.ALL,
            "one": RepeatMode.ONE,
        }.get(m, RepeatMode.OFF)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self._client.state
        return {
            "product":       PRODUCT_NAMES.get(self._product, self._product),
            "firmware":      s.firmware,
            "mac_address":   s.mac,
            "wifi_band":     s.wifi_band,
            "timezone":      s.timezone,
            "cast_version":  s.cast_version,
            "net_mode":      s.net_mode,
            "source_id":     s.source_id,
            "position_ms":   s.position_ms,
            "is_live":       s.is_live,
            "favourites":    s.favourites,
            "bt_status":     s.bt_status,
        }

    # ── Commands ───────────────────────────────────────────────────────────

    async def async_set_volume_level(self, volume: float) -> None:
        await self._client.async_set_volume(int(volume * 100))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._client.async_mute(mute)

    async def async_media_play(self) -> None:
        # Use RESUME if previously paused, else PLAY
        if self._client.state.play_state == "paused":
            await self._client.async_resume()
        else:
            await self._client.async_play()

    async def async_media_pause(self) -> None:
        await self._client.async_pause()

    async def async_media_stop(self) -> None:
        await self._client.async_stop()

    async def async_media_next_track(self) -> None:
        await self._client.async_next_track()

    async def async_media_previous_track(self) -> None:
        await self._client.async_prev_track()

    async def async_media_seek(self, position: float) -> None:
        await self._client.async_seek(int(position * 1000))

    async def async_set_shuffle(self, shuffle: bool) -> None:
        """Toggle shuffle mode."""
        await self._client.async_set_shuffle(shuffle)

    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        """Set repeat mode (off / all / one)."""
        await self._client.async_set_repeat(str(repeat))

    async def async_select_source(self, source: str) -> None:
        """Switch source. Most sources require external triggering (Spotify Connect,
        AirPlay, Cast etc) but Bluetooth / AUX / SPDIF / Favourites can be set."""
        src_id = self._source_id_by_name.get(source)
        if src_id is None:
            _LOGGER.warning("Unknown source: %s", source)
            return

        # Send MB#50 SET — speaker will switch if the source allows it.
        # Use string ID payload per LUCI API.
        await self._client._send(0x02, 50, str(src_id))  # noqa: SLF001

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a URL or favourite on the speaker."""
        # Favourite by content_id
        if media_id.startswith(_FAV_PREFIX):
            slot = int(media_id[len(_FAV_PREFIX):])
            await self._client.async_play_favourite(slot)
            return

        if media_type in _PLAYABLE_TYPES or media_id.startswith(("http://", "https://")):
            await self._client.async_play_url(media_id)
            return

        _LOGGER.warning("Unsupported play_media: type=%s id=%s", media_type, media_id)

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Top-level browse.

        Full browseable tree (NAS, streaming services, etc.) is not yet
        implemented — that requires SELECTITEM-based navigation of the
        speaker's UI tree (MB#41 SELECTITEM → MB#42 ItemList response).
        Mapping that hierarchy reliably requires a packet capture from
        the Lithe Audio app for reference.

        For now: shows favourites the user has saved via the Lithe app.
        """
        children = []

        # Favourites first
        for fav in self._client.state.favourites:
            children.append(BrowseMedia(
                title=fav.get("name", f"Favourite {fav.get('slot')}"),
                media_class=MediaClass.MUSIC,
                media_content_id=f"{_FAV_PREFIX}{fav.get('slot')}",
                media_content_type=MediaType.MUSIC,
                can_play=True,
                can_expand=False,
            ))

        if not children:
            # Placeholder so the user sees something other than empty
            children.append(BrowseMedia(
                title="No favourites yet — save some in the Lithe Audio app",
                media_class=MediaClass.URL,
                media_content_id="lithe_empty",
                media_content_type="library",
                can_play=False,
                can_expand=False,
            ))

        return BrowseMedia(
            title="Lithe Audio",
            media_class=MediaClass.DIRECTORY,
            media_content_id="lithe_root",
            media_content_type="library",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.MUSIC,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
