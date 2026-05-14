"""Multi-room group support for Lithe Audio.

A LitheGroup is a virtual media_player entity that fans out commands
(play, pause, volume, source switch, play_url) to a set of member
speakers in parallel. Groups appear as their own media_player entity
(e.g. `media_player.lithe_group_downstairs`) and can be controlled
from HA dashboards, automations, and voice assistants like any other
media_player.

Design notes:

- Lithe's native firmware grouping (Cast groups, master/slave roles) is
  separate from this. This module implements *application-level* grouping
  in HA: each member speaker still streams its own copy of the audio
  independently. For tight clock-sync across rooms, use a Chromecast
  group instead and call lithe_audio.play_group.

- Application-level grouping is more flexible (any combination of
  speakers, supports Direct URL, no Cast dependency) but has slight
  inter-room drift (~100-300ms) since each speaker buffers separately.
  Most users won't notice unless rooms are open-plan.

- Groups are persistent — saved in .storage/lithe_audio.groups and
  recreated on HA restart.

Storage schema:
    {
        "groups": {
            "group_<id>": {
                "id":      "group_a1b2c3",
                "name":    "Downstairs",
                "members": ["192.168.50.67", "192.168.50.10"],
                "default_volume": 50,
            },
            ...
        }
    }
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store

from .const import DATA_COORDINATOR, DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.groups"


def new_group_id() -> str:
    return f"group_{uuid.uuid4().hex[:6]}"


class LitheGroupManager:
    """Singleton manager for Lithe groups.

    Held under hass.data[DOMAIN]["groups_mgr"].
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._groups: dict[str, dict[str, Any]] = {}
        self._listeners: list[callback] = []

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data and isinstance(data, dict):
            self._groups = data.get("groups", {}) or {}
        _LOGGER.info("Loaded %d Lithe groups from storage", len(self._groups))

    async def async_save(self) -> None:
        await self._store.async_save({"groups": self._groups})

    def list_groups(self) -> list[dict[str, Any]]:
        return list(self._groups.values())

    def get_group(self, group_id: str) -> dict[str, Any] | None:
        return self._groups.get(group_id)

    async def async_add_group(self, group: dict[str, Any]) -> str:
        if "id" not in group:
            group["id"] = new_group_id()
        self._groups[group["id"]] = group
        await self.async_save()
        self._notify_listeners()
        _LOGGER.info("Added group %s '%s' with %d members",
                     group["id"], group.get("name"), len(group.get("members", [])))
        return group["id"]

    async def async_update_group(self, group_id: str, patch: dict[str, Any]) -> None:
        if group_id not in self._groups:
            return
        self._groups[group_id] = {**self._groups[group_id], **patch}
        await self.async_save()
        self._notify_listeners()

    async def async_delete_group(self, group_id: str) -> None:
        self._groups.pop(group_id, None)
        await self.async_save()
        self._notify_listeners()

    def register_listener(self, cb: callback) -> None:
        self._listeners.append(cb)

    def _notify_listeners(self) -> None:
        for cb in self._listeners:
            try:
                cb()
            except Exception:
                pass


def get_group_manager(hass: HomeAssistant) -> LitheGroupManager | None:
    return hass.data.get(DOMAIN, {}).get("groups_mgr")


async def async_setup_group_manager(hass: HomeAssistant) -> LitheGroupManager:
    mgr = LitheGroupManager(hass)
    await mgr.async_load()
    hass.data.setdefault(DOMAIN, {})["groups_mgr"] = mgr
    return mgr


# ── Group media_player entity ──────────────────────────────────────────

class LitheGroupMediaPlayer(MediaPlayerEntity):
    """A virtual media_player that controls multiple Lithe speakers.

    Forwards commands to all member coordinators in parallel using
    asyncio.gather. Aggregates state by inspecting member states.
    """

    _attr_has_entity_name = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
    )

    def __init__(self, hass: HomeAssistant, group: dict[str, Any]) -> None:
        self.hass = hass
        self._group_id = group["id"]
        self._attr_unique_id = f"lithe_group_{group['id']}"
        self._attr_name = f"Group: {group.get('name', 'Lithe Group')}"
        self._group_data = group

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"group_{self._group_id}")},
            name=f"Lithe Group: {self._group_data.get('name', 'Group')}",
            manufacturer="Lithe Audio",
            model="Multi-room Group",
        )

    # ── Member resolution ────────────────────────────────────────────
    def _member_coords(self) -> list:
        """Return coordinator objects for all member speakers.

        Members are stored as IP strings; we look them up in
        hass.data[DOMAIN][entry_id]['coordinator'].
        """
        members = (self._group_data or {}).get("members", []) or []
        coords = []
        bucket = self.hass.data.get(DOMAIN, {})
        for entry_id, entry_data in bucket.items():
            if not isinstance(entry_data, dict):
                continue
            coord = entry_data.get(DATA_COORDINATOR) or entry_data.get("coordinator")
            if coord and coord.client.host in members:
                coords.append(coord)
        return coords

    @property
    def available(self) -> bool:
        # Group is available if at least one member is connected
        return any(c.client.state.connected for c in self._member_coords())

    @property
    def state(self) -> MediaPlayerState:
        coords = self._member_coords()
        if not coords:
            return MediaPlayerState.OFF
        states = [c.client.state.play_state for c in coords]
        if any(s == "playing" for s in states):
            return MediaPlayerState.PLAYING
        if any(s == "paused" for s in states):
            return MediaPlayerState.PAUSED
        return MediaPlayerState.IDLE

    @property
    def volume_level(self) -> float | None:
        coords = self._member_coords()
        if not coords:
            return None
        # Average volume across connected members
        vols = [c.client.state.volume for c in coords if c.client.state.connected]
        if not vols:
            return None
        return sum(vols) / len(vols) / 100.0

    @property
    def is_volume_muted(self) -> bool:
        coords = self._member_coords()
        return all(c.client.state.muted for c in coords) if coords else False

    @property
    def media_title(self) -> str | None:
        # Show the title from the first playing member
        for c in self._member_coords():
            if c.client.state.play_state == "playing" and c.client.state.title:
                return c.client.state.title
        return None

    @property
    def media_artist(self) -> str | None:
        for c in self._member_coords():
            if c.client.state.play_state == "playing" and c.client.state.artist:
                return c.client.state.artist
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coords = self._member_coords()
        return {
            "group_id":       self._group_id,
            "member_count":   len(coords),
            "members":        [c.client.host for c in coords],
            "member_states":  {c.client.host: c.client.state.play_state for c in coords},
            "member_volumes": {c.client.host: c.client.state.volume for c in coords},
        }

    # ── Commands (fan out to all members in parallel) ────────────────

    async def _fan_out(self, method_name: str, *args) -> None:
        """Call coord.client.<method_name>(*args) on every member, in parallel."""
        coords = self._member_coords()
        if not coords:
            return
        async def call_one(c):
            try:
                fn = getattr(c.client, method_name)
                await fn(*args)
            except Exception as e:
                _LOGGER.warning("Group %s: %s on %s failed: %s",
                                self._group_id, method_name, c.client.host, e)
        await asyncio.gather(*(call_one(c) for c in coords))

    async def async_media_play(self) -> None:
        await self._fan_out("async_play")

    async def async_media_pause(self) -> None:
        await self._fan_out("async_pause")

    async def async_media_stop(self) -> None:
        await self._fan_out("async_stop")

    async def async_set_volume_level(self, volume: float) -> None:
        vol = int(max(0, min(100, volume * 100)))
        await self._fan_out("async_set_volume", vol)

    async def async_volume_up(self) -> None:
        # Step each member up by 5
        coords = self._member_coords()
        async def step(c):
            try:
                cur = c.client.state.volume
                await c.client.async_set_volume(min(100, cur + 5))
            except Exception:
                pass
        await asyncio.gather(*(step(c) for c in coords))

    async def async_volume_down(self) -> None:
        coords = self._member_coords()
        async def step(c):
            try:
                cur = c.client.state.volume
                await c.client.async_set_volume(max(0, cur - 5))
            except Exception:
                pass
        await asyncio.gather(*(step(c) for c in coords))

    async def async_mute_volume(self, mute: bool) -> None:
        await self._fan_out("async_mute", mute)

    async def async_media_next_track(self) -> None:
        await self._fan_out("async_next")

    async def async_media_previous_track(self) -> None:
        await self._fan_out("async_previous")

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs
    ) -> None:
        """Play the same URL on every member, simultaneously.

        For a Direct URL stream, each speaker fetches the URL on its
        own. For favourites/chimes, each speaker plays its own copy.
        """
        # Strip our internal prefixes
        if media_id.startswith("lithe_fav:"):
            slot = int(media_id[len("lithe_fav:"):])
            await self._fan_out("async_play_favourite", slot)
            return
        if media_id.startswith("lithe_url:"):
            media_id = media_id[len("lithe_url:"):]
        # Default: assume HTTP(S) URL → MB#41 PLAYITEM:DIRECT on each
        await self._fan_out("async_play_url", media_id)
