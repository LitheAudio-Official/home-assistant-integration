"""Lithe Audio media player entity."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
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
        # MEDIA_ANNOUNCE was added in HA 2022.12 — guard the import
        ann = getattr(MediaPlayerEntityFeature, "MEDIA_ANNOUNCE", None)
        if ann is not None:
            flags |= ann
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
        # Prefer real title from MB#42, fall back to filename of the URL
        # we last asked the speaker to play (helps when speaker hasn't
        # pushed metadata yet for Direct URL streams).
        if self._client.state.title:
            return self._client.state.title
        url = self._client.state.last_played_url
        if url:
            # Strip query string, take last path segment
            try:
                from urllib.parse import urlparse
                path = urlparse(url).path
                fname = path.rsplit("/", 1)[-1] or url
                # Strip extension for prettiness
                if "." in fname:
                    fname = fname.rsplit(".", 1)[0]
                return fname or None
            except Exception:
                return url
        return None

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
            # Hardware / firmware
            "product":         PRODUCT_NAMES.get(self._product, self._product),
            "firmware":        s.firmware,
            "mac_address":     s.mac,
            "wifi_band":       s.wifi_band,
            "timezone":        s.timezone,
            "cast_version":    s.cast_version,
            "net_mode":        s.net_mode,
            # Playback state for automation triggers (e.g. when artist changes)
            "source_id":       s.source_id,
            "source_name":     s.source_name,
            "position_ms":     s.position_ms,
            "is_live":         s.is_live,
            "title":           s.title,
            "artist":          s.artist,
            "album":           s.album,
            "duration_ms":     s.duration_ms,
            "last_played_url": s.last_played_url,
            "volume_percent":  s.volume,           # 0-100 not 0.0-1.0
            "shuffle":         s.shuffle,
            "repeat":          s.repeat,
            # Favourites — list of {slot, name} for picker UIs
            "favourites":      s.favourites,
            # Bluetooth
            "bt_status":       s.bt_status,
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
        """Play a URL or favourite on the speaker.

        Supports HA's standard play_media announce flow (per the
        media_player docs at https://www.home-assistant.io/integrations/
        media_player/#action-play-media). When ``announce=True`` is
        passed, the media is treated as a temporary announcement that
        interrupts current playback — this routes through the tannoy
        notify path which:

          1. Saves current volume + play state
          2. Pauses current source
          3. Sets announcement volume (from extra.volume or default 70)
          4. Plays the announcement URL via MB#41 PLAYITEM:DIRECT
          5. (Future) restores volume + resumes original source

        This is the standard way to do chimes / TTS / prayer / doorbell
        sounds in Home Assistant and works with the built-in voice
        assistant announcement UI, dashboard buttons, automations, etc.
        """
        # Favourite by content_id (no announce flow — favourites resume
        # the speaker's own playback engine)
        if media_id.startswith(_FAV_PREFIX):
            slot = int(media_id[len(_FAV_PREFIX):])
            await self._client.async_play_favourite(slot)
            return

        # Direct URL preset (Adhan/Quran/radio from our Browse Media tree)
        if media_id.startswith("lithe_url:"):
            media_id = media_id[len("lithe_url:"):]
            # Fall through to URL playback below

        # Resolve media_source:// URIs (Radio Browser, My Media, TTS, etc.)
        # into a real HTTP URL the speaker can stream.
        if media_id.startswith("media-source://") or media_source.is_media_source_id(media_id):
            try:
                resolved = await media_source.async_resolve_media(
                    self.hass, media_id, self.entity_id,
                )
                # Convert relative URLs (e.g. TTS) to absolute so the
                # speaker can reach them across the network.
                media_id = async_process_play_media_url(self.hass, resolved.url)
                if not media_type or media_type == "":
                    media_type = MediaType.MUSIC
                _LOGGER.debug("Resolved media_source → %s", media_id)
            except Exception as e:
                _LOGGER.error("Failed to resolve media_source %s: %s",
                              media_id, e)
                return

        # Detect announce intent (HA media_player standard)
        announce = bool(kwargs.get("announce") or False)
        extra: dict = kwargs.get("extra") or {}

        if announce and media_id.startswith(("http://", "https://")):
            # Route through tannoy notify service which handles
            # save/pause/volume/play. Use extra.volume if provided.
            volume = int(extra.get("volume", 70))
            try:
                await self.hass.services.async_call(
                    "notify", "lithe_tannoy",
                    {
                        "message": media_id,
                        "data": {
                            "mode":     "start",
                            "volume":   volume,
                            "speakers": [self._client.host],
                        },
                    },
                    blocking=False,
                )
                _LOGGER.info(
                    "play_media announce: routed to lithe_tannoy "
                    "(url=%s volume=%d)", media_id, volume,
                )
            except Exception as e:
                _LOGGER.error("Announce via tannoy failed: %s", e)
            return

        # Regular play (no announce) — direct URL via MB#41 PLAYITEM:DIRECT
        if media_type in _PLAYABLE_TYPES or media_id.startswith(("http://", "https://")):
            await self._client.async_play_url(media_id)
            return

        _LOGGER.warning("Unsupported play_media: type=%s id=%s", media_type, media_id)

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Browse Lithe favourites + HA media sources + Direct URL presets.

        Top-level tree:
          ├── Favourites          (speaker-side saved slots)
          ├── 🔗 Direct URL       (Adhan, Quran, custom HTTP URLs)
          ├── 📻 Radio Browser    (from HA media_source)
          ├── 📁 My Media         (from HA media_source)
          ├── 🔊 Text-to-speech   (from HA media_source)
          └── …other HA sources
        """
        # Direct URL folder — expand it to see Adhan/Quran/custom presets
        if media_content_id == "lithe_direct_url":
            return self._build_direct_url_folder()

        # A direct-url item: play immediately (handled by play_media)
        if media_content_id and media_content_id.startswith("lithe_url:"):
            url = media_content_id[len("lithe_url:"):]
            # Browse-into of a leaf node — HA will call play_media instead;
            # this branch shouldn't normally hit, but return self for safety.
            return BrowseMedia(
                title=url, media_class=MediaClass.URL,
                media_content_id=media_content_id,
                media_content_type=MediaType.MUSIC,
                can_play=True, can_expand=False,
            )

        # Drill-down into a media_source:// URI
        if media_content_id and media_content_id.startswith("media-source://"):
            return await media_source.async_browse_media(
                self.hass, media_content_id,
                content_filter=lambda item: item.media_content_type.startswith("audio/")
                                            or item.media_content_type in _PLAYABLE_TYPES,
            )

        # Build root: favourites + media sources
        children: list[BrowseMedia] = []

        # 1) Favourites (speaker-side, saved via Lithe app or our save_favourite)
        for fav in self._client.state.favourites:
            children.append(BrowseMedia(
                title=fav.get("name", f"Favourite {fav.get('slot')}"),
                media_class=MediaClass.MUSIC,
                media_content_id=f"{_FAV_PREFIX}{fav.get('slot')}",
                media_content_type=MediaType.MUSIC,
                can_play=True,
                can_expand=False,
            ))

        # 2) Direct URL folder (Adhan, Quran, internet radio presets)
        children.append(BrowseMedia(
            title="🔗 Direct URL — Adhan, Quran & Radio",
            media_class=MediaClass.DIRECTORY,
            media_content_id="lithe_direct_url",
            media_content_type="library",
            can_play=False,
            can_expand=True,
            thumbnail=None,
        ))

        # 3) HA media sources (Radio Browser, My Media, TTS, etc.)
        try:
            ms_root = await media_source.async_browse_media(
                self.hass, None,
                content_filter=lambda item: item.media_content_type.startswith("audio/")
                                            or item.media_content_type in _PLAYABLE_TYPES,
            )
            # Add each top-level source as an expandable child
            if ms_root and ms_root.children:
                for c in ms_root.children:
                    children.append(c)
        except Exception as e:
            _LOGGER.warning("Failed to browse HA media sources: %s", e)

        if not children:
            children.append(BrowseMedia(
                title="No favourites or media sources yet",
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

    def _build_direct_url_folder(self) -> BrowseMedia:
        """Build the 'Direct URL' sub-folder with Adhan + Quran presets.

        Each preset is a playable BrowseMedia item with the URL embedded
        in the content_id (prefix 'lithe_url:'). When the user taps one,
        HA calls async_play_media with that content_id; we strip the
        prefix and route to async_play_url → MB#41 PLAYITEM:DIRECT.

        Users can also call play_media with any HTTP URL directly (no
        browse needed) — this folder is just a convenience preset list.
        """
        from .const import ADHAN_PRESETS, QURAN_JUZ

        children: list[BrowseMedia] = []

        # Adhan presets first (most commonly played)
        for label, url in ADHAN_PRESETS.items():
            children.append(BrowseMedia(
                title=f"🕌 {label}",
                media_class=MediaClass.MUSIC,
                media_content_id=f"lithe_url:{url}",
                media_content_type=MediaType.MUSIC,
                can_play=True,
                can_expand=False,
            ))

        # Then all 30 Juz of the Quran
        for juz_num, url in QURAN_JUZ.items():
            children.append(BrowseMedia(
                title=f"📖 Juz {juz_num} — Quran",
                media_class=MediaClass.MUSIC,
                media_content_id=f"lithe_url:{url}",
                media_content_type=MediaType.MUSIC,
                can_play=True,
                can_expand=False,
            ))

        # A few common internet radio URLs as bonus
        radio_extras = {
            "🎵 BBC Radio 1":  "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_one",
            "🎵 BBC Radio 2":  "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_two",
            "🎵 BBC Radio 4":  "http://stream.live.vc.bbcmedia.co.uk/bbc_radio_fourfm",
            "🎵 Classic FM":   "http://media-ice.musicradio.com/ClassicFMMP3",
        }
        for label, url in radio_extras.items():
            children.append(BrowseMedia(
                title=label,
                media_class=MediaClass.MUSIC,
                media_content_id=f"lithe_url:{url}",
                media_content_type=MediaType.MUSIC,
                can_play=True,
                can_expand=False,
            ))

        return BrowseMedia(
            title="Direct URL Presets",
            media_class=MediaClass.DIRECTORY,
            media_content_id="lithe_direct_url",
            media_content_type="library",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.MUSIC,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
