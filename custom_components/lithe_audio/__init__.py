"""The Lithe Audio integration."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.network import get_url

from .const import (
    ATTR_CHIME_INDEX,
    ATTR_DIRECT_PATH,
    ATTR_PRESET_SLOT,
    ATTR_RAW_CMD_TYPE,
    ATTR_RAW_MBID,
    ATTR_RAW_PAYLOAD,
    CMD_SET,
    CONF_CERT_KEY,
    CONF_CERT_PEM,
    CONF_MAC,
    CONF_MODEL,
    CONF_PLATFORM,
    DEFAULT_PORT,
    DOMAIN,
    PLATFORM_LS10,
    SERVICE_DELETE_PRESET,
    SERVICE_PLAY_CHIME,
    SERVICE_PLAY_DIRECT,
    SERVICE_PLAY_PRESET,
    SERVICE_REBOOT,
    SERVICE_SAVE_PRESET,
    SERVICE_SEND_RAW,
)
from .coordinator import LitheAudioCoordinator
from .luci import LitheAudioClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


@dataclass
class LitheAudioRuntimeData:
    """Holds runtime state attached to a ConfigEntry."""

    client: LitheAudioClient
    coordinator: LitheAudioCoordinator


type LitheAudioConfigEntry = ConfigEntry[LitheAudioRuntimeData]


# ── Service schemas ────────────────────────────────────────────────────────
SERVICE_PLAY_CHIME_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_ids,
    vol.Required(ATTR_CHIME_INDEX): vol.All(int, vol.Range(min=1, max=15)),
})
SERVICE_PRESET_SLOT_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_ids,
    vol.Required(ATTR_PRESET_SLOT): vol.All(int, vol.Range(min=1, max=9)),
})
SERVICE_PLAY_DIRECT_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_ids,
    vol.Required(ATTR_DIRECT_PATH): cv.string,
})
SERVICE_SEND_RAW_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_ids,
    vol.Required(ATTR_RAW_MBID): vol.All(int, vol.Range(min=0, max=65535)),
    vol.Required(ATTR_RAW_PAYLOAD): cv.string,
    vol.Optional(ATTR_RAW_CMD_TYPE, default=CMD_SET): vol.In([0x01, 0x02]),
})
SERVICE_ENTITY_ONLY_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_ids,
})


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register integration-wide services once, on first load."""
    if hass.services.has_service(DOMAIN, SERVICE_PLAY_CHIME):
        return True

    async def _resolve_clients(call: ServiceCall) -> list[LitheAudioClient]:
        """Map entity_ids in a service call back to their LitheAudioClient."""
        entity_ids = call.data["entity_id"]
        clients: list[LitheAudioClient] = []
        ent_reg = er.async_get(hass)
        for entity_id in entity_ids:
            entry = ent_reg.async_get(entity_id)
            if entry is None or entry.config_entry_id is None:
                continue
            config_entry = hass.config_entries.async_get_entry(entry.config_entry_id)
            if config_entry is None or not hasattr(config_entry, "runtime_data"):
                continue
            runtime: LitheAudioRuntimeData = config_entry.runtime_data
            if runtime.client not in clients:
                clients.append(runtime.client)
        return clients

    async def _svc_play_chime(call: ServiceCall) -> None:
        for client in await _resolve_clients(call):
            await client.async_play_chime(call.data[ATTR_CHIME_INDEX])

    async def _svc_play_preset(call: ServiceCall) -> None:
        for client in await _resolve_clients(call):
            await client.async_preset_play(call.data[ATTR_PRESET_SLOT])

    async def _svc_save_preset(call: ServiceCall) -> None:
        for client in await _resolve_clients(call):
            await client.async_preset_save(call.data[ATTR_PRESET_SLOT])

    async def _svc_delete_preset(call: ServiceCall) -> None:
        for client in await _resolve_clients(call):
            await client.async_preset_delete(call.data[ATTR_PRESET_SLOT])

    async def _svc_play_direct(call: ServiceCall) -> None:
        for client in await _resolve_clients(call):
            await client.async_play_direct(call.data[ATTR_DIRECT_PATH])

    async def _svc_send_raw(call: ServiceCall) -> None:
        for client in await _resolve_clients(call):
            await client.async_send_raw(
                call.data[ATTR_RAW_MBID],
                call.data[ATTR_RAW_PAYLOAD],
                call.data[ATTR_RAW_CMD_TYPE],
            )

    async def _svc_reboot(call: ServiceCall) -> None:
        for client in await _resolve_clients(call):
            await client.async_reboot()

    hass.services.async_register(DOMAIN, SERVICE_PLAY_CHIME, _svc_play_chime,
                                 schema=SERVICE_PLAY_CHIME_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_PLAY_PRESET, _svc_play_preset,
                                 schema=SERVICE_PRESET_SLOT_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SAVE_PRESET, _svc_save_preset,
                                 schema=SERVICE_PRESET_SLOT_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_DELETE_PRESET, _svc_delete_preset,
                                 schema=SERVICE_PRESET_SLOT_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_PLAY_DIRECT, _svc_play_direct,
                                 schema=SERVICE_PLAY_DIRECT_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SEND_RAW, _svc_send_raw,
                                 schema=SERVICE_SEND_RAW_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_REBOOT, _svc_reboot,
                                 schema=SERVICE_ENTITY_ONLY_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LitheAudioConfigEntry) -> bool:
    """Set up a Lithe Audio speaker from a config entry."""
    data = entry.data
    host = data[CONF_HOST]
    port = data.get(CONF_PORT, DEFAULT_PORT)
    platform = data.get(CONF_PLATFORM, "LS9")

    # The HA host IP is included in the LS10 registration JSON so the
    # speaker knows who's controlling it. Best-effort lookup — falls back
    # to 0.0.0.0 if HA can't determine its own URL.
    ha_ip = "0.0.0.0"
    try:
        import urllib.parse
        url = get_url(hass, prefer_external=False, allow_internal=True)
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname:
            ha_ip = parsed.hostname
    except Exception:  # noqa: BLE001
        pass

    client = LitheAudioClient(
        host=host,
        port=port,
        platform=platform,
        client_cert_pem=data.get(CONF_CERT_PEM) if platform == PLATFORM_LS10 else None,
        client_cert_key=data.get(CONF_CERT_KEY) if platform == PLATFORM_LS10 else None,
        client_app_id="homeassistant.lithe_audio",
        client_app_version="0.1.0",
        client_ip=ha_ip,
    )
    coordinator = LitheAudioCoordinator(hass, entry, client)

    try:
        await client.async_start()
        # Wait briefly for first connection — but don't block forever
        await coordinator.async_config_entry_first_refresh()
    except Exception as exc:
        await client.async_stop()
        raise ConfigEntryNotReady(
            f"Could not connect to Lithe Audio at {host}:{port}: {exc}"
        ) from exc

    entry.runtime_data = LitheAudioRuntimeData(client=client, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: LitheAudioConfigEntry,
) -> None:
    """Reload integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: LitheAudioConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime: LitheAudioRuntimeData = entry.runtime_data
        await runtime.client.async_stop()
    return unload_ok
