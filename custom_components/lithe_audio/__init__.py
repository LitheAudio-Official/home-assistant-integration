"""Lithe Audio integration for Home Assistant."""
from __future__ import annotations

import logging
import socket as _sock

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .cast_group import async_register_cast_group_service
from .const import (
    BT_DISC, BT_PAIR, CONF_CERT_PATH, CONF_HOST, CONF_KEY_PATH,
    CONF_PORT, CONF_PRODUCT, CONF_USE_TLS, DATA_COORDINATOR, DOMAIN,
    DSP_BALANCE, DSP_EQ, DSP_HIGHPASS, DSP_LOUDNESS, DSP_NIGHTMODE, DSP_OUTPUT,
    EQ_PRESETS, HP_OPTIONS, LS9_PRODUCTS, OUT_OPTIONS, PRODUCT_CHIMES,
)
from .coordinator import LitheAudioCoordinator
from .lithe_client import LitheClient, LitheClientLS9
from .prayer import async_register_prayer_service, async_unload_prayer

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.MEDIA_PLAYER,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.SENSOR,
]


def _detect_local_ip() -> str:
    try:
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _infer_product(entry_data: dict) -> str:
    """Guess a product ID from legacy/incomplete config-entry data.

    Used when migrating entries created by older versions that didn't
    persist `product`. Defaults err on the conservative side.
    """
    from .const import (
        PRODUCT_MICRO, PRODUCT_PRO, PRODUCT_PRO2, PRODUCT_V2, PRODUCT_V3,
        PRODUCT_IO1,
    )
    # Sometimes older entries stored a 'platform' string instead
    plat = (entry_data.get("platform") or "").upper()
    title_hint = (entry_data.get("title") or entry_data.get("name") or "").upper()
    cert_present = bool(entry_data.get(CONF_CERT_PATH)) or entry_data.get(CONF_USE_TLS)

    # Title-based hint first
    if "PRO2" in title_hint or "PRO 2" in title_hint:  return PRODUCT_PRO2
    if "V3" in title_hint:                              return PRODUCT_V3
    if "IO1" in title_hint:                             return PRODUCT_IO1
    if "MICRO" in title_hint:                           return PRODUCT_MICRO
    if "V2" in title_hint:                              return PRODUCT_V2
    if "PRO" in title_hint:                             return PRODUCT_PRO

    # Platform-based fallback
    if plat == "LS10" or cert_present:                  return PRODUCT_PRO2
    return PRODUCT_V2


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Lithe Audio from a config entry."""
    # Migration: entries created by older versions may not have `product`.
    if CONF_PRODUCT not in entry.data:
        inferred = _infer_product({**entry.data, "title": entry.title})
        _LOGGER.warning(
            "Lithe Audio entry %s has no '%s' key — migrating with inferred value '%s'. "
            "Delete and re-add the entry if this is wrong.",
            entry.title, CONF_PRODUCT, inferred,
        )
        new_data = {**entry.data, CONF_PRODUCT: inferred}
        hass.config_entries.async_update_entry(entry, data=new_data)

    product = entry.data[CONF_PRODUCT]
    host    = entry.data[CONF_HOST]
    port    = entry.data.get(CONF_PORT, 7777)
    use_tls = entry.data.get(CONF_USE_TLS, product not in LS9_PRODUCTS)
    cert    = entry.data.get(CONF_CERT_PATH) or None
    key     = entry.data.get(CONF_KEY_PATH) or None

    # Fall back to bundled certs if a TLS product has no cert path
    if use_tls and not (cert and key):
        from .const import BUNDLED_CERT_KEY, BUNDLED_CERT_PEM
        cert = cert or BUNDLED_CERT_PEM
        key  = key  or BUNDLED_CERT_KEY

    local_ip = _detect_local_ip()

    ClientCls = LitheClientLS9 if product in LS9_PRODUCTS else LitheClient
    client = ClientCls(host, port, use_tls, cert, key, local_ip)

    coordinator = LitheAudioCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _register_services(hass)
    await async_register_prayer_service(hass)
    await async_register_cast_group_service(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        bucket = hass.data.get(DOMAIN, {})
        entry_data = bucket.pop(entry.entry_id, None)
        if entry_data:
            coordinator: LitheAudioCoordinator = entry_data[DATA_COORDINATOR]
            await coordinator.async_shutdown()

        # If this was the last config entry, tear down shared services too
        has_other_entries = any(
            isinstance(v, dict) and DATA_COORDINATOR in v
            for v in bucket.values()
        )
        if not has_other_entries:
            await async_unload_prayer(hass)
            for svc in (
                "play_chime", "play_url", "play_favourite",
                "set_dsp_eq", "set_dsp_output", "set_dsp_nightmode",
                "set_dsp_highpass", "set_dsp_balance", "set_dsp_loudness",
                "bluetooth_pair", "bluetooth_disconnect",
                "reboot", "set_name", "play_group", "set_prayer_schedule",
            ):
                if hass.services.has_service(DOMAIN, svc):
                    hass.services.async_remove(DOMAIN, svc)
    return unload_ok


# ── Service dispatch ────────────────────────────────────────────────────────

def _resolve_coordinators(hass: HomeAssistant, call: ServiceCall) -> list[LitheAudioCoordinator]:
    """Resolve a service call's targets to coordinator instances.

    Looks at entity_id (single or list) on the call data and matches each one
    to the integration's entry that owns it.
    """
    bucket = hass.data.get(DOMAIN, {})
    if not bucket:
        return []

    raw = call.data.get("entity_id")
    if isinstance(raw, str):
        targets = [raw]
    elif isinstance(raw, list):
        targets = list(raw)
    else:
        targets = []

    coordinators: list[LitheAudioCoordinator] = []
    seen: set[str] = set()

    if targets:
        ent_reg = er.async_get(hass)
        for eid in targets:
            ent = ent_reg.async_get(eid)
            if not ent or not ent.config_entry_id:
                continue
            entry_data = bucket.get(ent.config_entry_id)
            if entry_data and entry_data[DATA_COORDINATOR] not in coordinators:
                coordinators.append(entry_data[DATA_COORDINATOR])
                seen.add(ent.config_entry_id)
        if coordinators:
            return coordinators

    # Fallback: every configured speaker
    for entry_id, entry_data in bucket.items():
        if not isinstance(entry_data, dict) or DATA_COORDINATOR not in entry_data:
            continue
        if entry_id in seen:
            continue
        coordinators.append(entry_data[DATA_COORDINATOR])
    return coordinators


def _register_services(hass: HomeAssistant) -> None:
    """Register all Lithe Audio service actions."""

    if hass.services.has_service(DOMAIN, "play_chime"):
        return  # Already registered for another entry

    async def _for_each(call: ServiceCall, fn) -> None:
        coords = _resolve_coordinators(hass, call)
        if not coords:
            raise HomeAssistantError("No Lithe Audio speaker matched the service target")
        for coord in coords:
            try:
                await fn(coord.client)
            except Exception as e:
                _LOGGER.error("%s failed on %s: %s", call.service, coord.client.host, e)

    # ── Playback / chime ────────────────────────────────────────────────
    async def svc_play_chime(call: ServiceCall) -> None:
        n = int(call.data.get("chime_number", 1))
        await _for_each(call, lambda c: c.async_play_chime(max(1, min(n, 15))))

    async def svc_play_url(call: ServiceCall) -> None:
        url = str(call.data.get("url", "")).strip()
        if not url:
            return
        await _for_each(call, lambda c: c.async_play_url(url))

    async def svc_play_favourite(call: ServiceCall) -> None:
        slot = int(call.data.get("slot", 1))
        await _for_each(call, lambda c: c.async_play_favourite(slot))

    async def svc_set_name(call: ServiceCall) -> None:
        name = str(call.data.get("name", "")).strip()
        if not name:
            return
        await _for_each(call, lambda c: c.async_set_name(name))

    # ── DSP ─────────────────────────────────────────────────────────────
    async def svc_set_eq(call: ServiceCall) -> None:
        preset = call.data.get("preset", "Normal")
        idx = EQ_PRESETS.index(preset) if preset in EQ_PRESETS else 0
        await _for_each(call, lambda c: c.async_dsp_command(DSP_EQ, idx))

    async def svc_set_output(call: ServiceCall) -> None:
        mode = call.data.get("mode", "Stereo")
        idx = OUT_OPTIONS.index(mode) if mode in OUT_OPTIONS else 0
        await _for_each(call, lambda c: c.async_dsp_command(DSP_OUTPUT, idx))

    async def svc_set_nightmode(call: ServiceCall) -> None:
        val = 1 if call.data.get("enabled") else 0
        await _for_each(call, lambda c: c.async_dsp_command(DSP_NIGHTMODE, val))

    async def svc_set_highpass(call: ServiceCall) -> None:
        freq = call.data.get("frequency", "OFF")
        idx = HP_OPTIONS.index(freq) if freq in HP_OPTIONS else 0
        await _for_each(call, lambda c: c.async_dsp_command(DSP_HIGHPASS, idx))

    async def svc_set_balance(call: ServiceCall) -> None:
        val = max(-6, min(6, int(call.data.get("balance", 0))))
        await _for_each(call, lambda c: c.async_dsp_command(DSP_BALANCE, val))

    async def svc_set_loudness(call: ServiceCall) -> None:
        val = int(call.data.get("value", 0))
        await _for_each(call, lambda c: c.async_dsp_command(DSP_LOUDNESS, val))

    # ── Bluetooth / system ──────────────────────────────────────────────
    async def svc_bt_pair(call: ServiceCall) -> None:
        await _for_each(call, lambda c: c.async_bluetooth(BT_PAIR))

    async def svc_bt_disc(call: ServiceCall) -> None:
        await _for_each(call, lambda c: c.async_bluetooth(BT_DISC))

    async def svc_reboot(call: ServiceCall) -> None:
        await _for_each(call, lambda c: c.async_reboot())

    hass.services.async_register(DOMAIN, "play_chime",       svc_play_chime)
    hass.services.async_register(DOMAIN, "play_url",         svc_play_url)
    hass.services.async_register(DOMAIN, "play_favourite",   svc_play_favourite)
    hass.services.async_register(DOMAIN, "set_name",         svc_set_name)
    hass.services.async_register(DOMAIN, "set_dsp_eq",       svc_set_eq)
    hass.services.async_register(DOMAIN, "set_dsp_output",   svc_set_output)
    hass.services.async_register(DOMAIN, "set_dsp_nightmode", svc_set_nightmode)
    hass.services.async_register(DOMAIN, "set_dsp_highpass", svc_set_highpass)
    hass.services.async_register(DOMAIN, "set_dsp_balance",  svc_set_balance)
    hass.services.async_register(DOMAIN, "set_dsp_loudness", svc_set_loudness)
    hass.services.async_register(DOMAIN, "bluetooth_pair",   svc_bt_pair)
    hass.services.async_register(DOMAIN, "bluetooth_disconnect", svc_bt_disc)
    hass.services.async_register(DOMAIN, "reboot",           svc_reboot)
