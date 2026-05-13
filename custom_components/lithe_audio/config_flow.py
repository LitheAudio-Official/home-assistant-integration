"""Config flow for Lithe Audio integration.

Two paths:
  - **Scan network**: LSSDP discovery, user picks from the list.
  - **Enter IP manually**: classic host + model entry.

Both paths auto-detect LS10 vs LS9 (LS10 = TLS 1.2, LS9 = plain TCP).
The TLS client certificate is **bundled with the integration** — users
never have to obtain or paste certs.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult

from .const import (
    BUNDLED_CERT_KEY, BUNDLED_CERT_PEM,
    CONF_CERT_PATH, CONF_KEY_PATH, CONF_PRODUCT, CONF_USE_TLS,
    DEFAULT_PORT, DOMAIN, LS10_PRODUCTS, LS9_PRODUCTS,
    PRODUCT_IO1, PRODUCT_MICRO, PRODUCT_NAMES,
    PRODUCT_PRO, PRODUCT_PRO2, PRODUCT_V2, PRODUCT_V3,
    ADHAN_PRESETS, QURAN_JUZ, quran_juz_label, all_preset_options,
)
from .discovery import DiscoveredDevice, async_discover

_LOGGER = logging.getLogger(__name__)

PRODUCT_OPTIONS = {k: v for k, v in PRODUCT_NAMES.items()}


def _guess_product(dev: DiscoveredDevice) -> str:
    """Map an LSSDP-discovered model string to one of our product IDs."""
    m = (dev.model or "").upper()
    if "PRO2" in m or "PRO 2" in m:
        return PRODUCT_PRO2
    if "V3" in m:
        return PRODUCT_V3
    if "IO1" in m:
        return PRODUCT_IO1
    if "MICRO" in m:
        return PRODUCT_MICRO
    if "V2" in m:
        return PRODUCT_V2
    if "PRO" in m:
        return PRODUCT_PRO
    # Fall back on platform classification from LSSDP headers
    return PRODUCT_PRO2 if dev.platform == "LS10" else PRODUCT_V2


class LitheAudioConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow for Lithe Audio."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: list[DiscoveredDevice] = []
        self._discovery_host: str = ""
        self._discovery_name: str = ""

    # ── Step 1: choose Scan vs Manual ──────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """First step: scan network automatically, or enter an IP manually."""
        if user_input is not None:
            mode = user_input.get("mode", "scan")
            if mode == "manual":
                return await self.async_step_manual()
            return await self.async_step_scan()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("mode", default="scan"): vol.In({
                    "scan":   "Scan network for speakers",
                    "manual": "Enter speaker IP manually",
                }),
            }),
            description_placeholders={
                "products": ", ".join(PRODUCT_NAMES.values()),
            },
        )

    # ── Step 2a: scan path ──────────────────────────────────────────────

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Run LSSDP discovery and let the user pick a speaker."""
        if user_input is not None:
            choice = user_input.get("selected")
            if choice == "__manual__":
                return await self.async_step_manual()
            dev = next(
                (d for d in self._discovered if d.unique_id == choice), None
            )
            if dev is None:
                return await self.async_step_manual()
            return await self._async_finish_from_discovery(dev)

        # Perform the scan
        try:
            self._discovered = await async_discover(timeout=3.0)
        except Exception as exc:
            _LOGGER.warning("LSSDP discovery failed: %s", exc)
            self._discovered = []

        # Filter out already-configured speakers by unique_id
        existing = {
            e.unique_id for e in self._async_current_entries() if e.unique_id
        }
        new_devices = [d for d in self._discovered if d.unique_id not in existing]

        if not new_devices:
            # Nothing new found — fall through to manual entry
            return await self.async_step_manual()

        options = {d.unique_id: f"{d.name} — {d.host} ({d.platform})"
                   for d in new_devices}
        options["__manual__"] = "Enter speaker IP manually instead…"

        return self.async_show_form(
            step_id="scan",
            data_schema=vol.Schema({
                vol.Required("selected"): vol.In(options),
            }),
            description_placeholders={
                "count": str(len(new_devices)),
            },
        )

    async def _async_finish_from_discovery(
        self, dev: DiscoveredDevice
    ) -> FlowResult:
        """Create a config entry directly from a discovered device."""
        unique = dev.unique_id or f"{dev.host}_{dev.port}"
        await self.async_set_unique_id(unique)
        self._abort_if_unique_id_configured()

        product  = _guess_product(dev)
        use_tls  = product in LS10_PRODUCTS
        cert     = BUNDLED_CERT_PEM if use_tls else ""
        key      = BUNDLED_CERT_KEY if use_tls else ""

        # Quick connectivity test
        try:
            from .lithe_client import LitheClient, LitheClientLS9
            cls = LitheClientLS9 if product in LS9_PRODUCTS else LitheClient
            client = cls(dev.host, dev.port, use_tls, cert or None, key or None)
            await asyncio.wait_for(client.async_connect(), timeout=6.0)
            await client.async_disconnect()
        except Exception as e:
            _LOGGER.warning("Discovered %s but cannot connect: %s", dev.host, e)
            # Don't block setup — saving the entry lets the coordinator retry

        return self.async_create_entry(
            title=f"{dev.name} ({dev.host})",
            data={
                CONF_HOST:      dev.host,
                CONF_PORT:      dev.port,
                CONF_PRODUCT:   product,
                CONF_USE_TLS:   use_tls,
                CONF_CERT_PATH: cert,
                CONF_KEY_PATH:  key,
            },
        )

    # ── Step 2b: manual path ────────────────────────────────────────────

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Enter host / port / model manually. Certs are bundled — no prompt."""
        errors: dict[str, str] = {}

        if user_input is not None:
            product = user_input[CONF_PRODUCT]
            host    = user_input[CONF_HOST].strip()
            port    = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            use_tls = product in LS10_PRODUCTS
            cert    = BUNDLED_CERT_PEM if use_tls else ""
            key     = BUNDLED_CERT_KEY if use_tls else ""

            await self.async_set_unique_id(f"{host}_{port}")
            self._abort_if_unique_id_configured()

            # Quick connectivity test
            try:
                from .lithe_client import LitheClient, LitheClientLS9
                cls = LitheClientLS9 if product in LS9_PRODUCTS else LitheClient
                client = cls(host, port, use_tls, cert or None, key or None)
                await asyncio.wait_for(client.async_connect(), timeout=6.0)
                await client.async_disconnect()
            except asyncio.TimeoutError:
                errors["base"] = "cannot_connect"
            except Exception as e:
                _LOGGER.debug("Manual flow connect error: %s", e)
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title=f"{PRODUCT_NAMES[product]} ({host})",
                    data={
                        CONF_HOST:      host,
                        CONF_PORT:      port,
                        CONF_PRODUCT:   product,
                        CONF_USE_TLS:   use_tls,
                        CONF_CERT_PATH: cert,
                        CONF_KEY_PATH:  key,
                    },
                )

        schema = vol.Schema({
            vol.Required(CONF_PRODUCT, default=PRODUCT_PRO2): vol.In(PRODUCT_OPTIONS),
            vol.Required(CONF_HOST):  str,
            vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
        })

        return self.async_show_form(
            step_id="manual",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "products": ", ".join(PRODUCT_NAMES.values()),
            },
        )

    # ── Zeroconf discovery (mDNS via _googlecast._tcp.local.) ──────────

    async def async_step_zeroconf(
        self, discovery_info: Any
    ) -> FlowResult:
        """Handle mDNS discovery via Cast."""
        host = discovery_info.host
        name = discovery_info.properties.get("fn", host)

        await self.async_set_unique_id(f"{host}_{DEFAULT_PORT}")
        self._abort_if_unique_id_configured()

        self.context["title_placeholders"] = {"name": name, "host": host}
        self._discovery_host = host
        self._discovery_name = name
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        host = self._discovery_host
        if user_input is not None:
            product = user_input[CONF_PRODUCT]
            use_tls = product in LS10_PRODUCTS
            cert    = BUNDLED_CERT_PEM if use_tls else ""
            key     = BUNDLED_CERT_KEY if use_tls else ""
            return self.async_create_entry(
                title=f"{PRODUCT_NAMES[product]} ({host})",
                data={
                    CONF_HOST:      host,
                    CONF_PORT:      DEFAULT_PORT,
                    CONF_PRODUCT:   product,
                    CONF_USE_TLS:   use_tls,
                    CONF_CERT_PATH: cert,
                    CONF_KEY_PATH:  key,
                },
            )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_PRODUCT, default=PRODUCT_PRO2): vol.In(PRODUCT_OPTIONS),
            }),
            description_placeholders={
                "host": host,
                "name": self._discovery_name,
            },
        )

    # ── Options flow entry point ───────────────────────────────────────
    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "LitheAudioOptionsFlow":
        """Return the options flow handler — provides Prayer Scheduler UI."""
        return LitheAudioOptionsFlow(config_entry)


# ── Constants used by the Options Flow (Prayer Scheduler UI) ────────────
PRAYER_NAMES_LIST = ["fajr", "dhuhr", "asr", "maghrib", "isha"]
DAYS_OPTIONS = ["daily", "weekdays", "weekends", "friday", "saturday", "sunday"]
CALC_METHODS = {
    1:  "University of Islamic Sciences, Karachi",
    2:  "Islamic Society of North America (ISNA)",
    3:  "Muslim World League",
    4:  "Umm Al-Qura University, Makkah",
    5:  "Egyptian General Authority of Survey",
    8:  "Gulf Region",
    12: "Union des Organisations Islamiques de France",
    13: "Diyanet İşleri Başkanlığı, Turkey",
    15: "Moonsighting Committee Worldwide (Moonsighting.com)",
}

# Defaults if user has not yet configured anything
_DEFAULT_ADHAN_URL = "https://www.islamcan.com/audio/adhan/azan1.mp3"
_DEFAULT_VOLUME = 70


class LitheAudioOptionsFlow(config_entries.OptionsFlow):
    """Per-speaker options flow.

    Provides a built-in Prayer Scheduler UI similar to the standalone
    Adhan/Prayer scheduler webapp — configure city/country/method, then
    per-prayer URL/volume/days, all within the standard HA integration
    Configure dialog.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # NOTE: In HA Core 2024.12+ `self.config_entry` is a read-only
        # property on the OptionsFlow base class — we MUST NOT assign to it.
        # Store it under a private attribute instead.
        self._entry = config_entry
        self._draft: dict[str, Any] = dict(config_entry.options or {})

    # ── Step 1: top-level menu ─────────────────────────────────────────
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "prayer_general":  "Prayer Scheduler — General settings",
                "prayer_entries":  "Prayer Scheduler — Per-prayer URLs & volumes",
                "prayer_disable":  "Disable Prayer Scheduler",
            },
        )

    # ── Step 2: General (city/country/method/volume/enable) ────────────
    async def async_step_prayer_general(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._draft.get("prayer", {})
        presets = all_preset_options()
        preset_choices = {"Custom URL (use field below)": ""} | presets

        if user_input is not None:
            chosen_preset = user_input.get("preset", "")
            # If user picked a real preset, use its URL — otherwise free text
            url = chosen_preset.strip() or user_input["default_url"].strip()
            self._draft["prayer"] = {
                **opts,
                "enabled":  True,
                "city":     user_input["city"].strip(),
                "country":  user_input["country"].strip(),
                "method":   int(user_input["method"]),
                "default_volume": int(user_input["default_volume"]),
                "default_url":    url,
            }
            return await self.async_step_prayer_entries()

        # Reverse-lookup current default_url against presets to set initial value
        current_url = opts.get("default_url", _DEFAULT_ADHAN_URL)
        matching_label = ""
        for label, url in presets.items():
            if url == current_url:
                matching_label = url
                break

        schema = vol.Schema({
            vol.Required("city",    default=opts.get("city", "London")): str,
            vol.Required("country", default=opts.get("country", "GB")): str,
            vol.Required("method",  default=opts.get("method", 2)): vol.In(CALC_METHODS),
            vol.Required("default_volume", default=opts.get("default_volume", _DEFAULT_VOLUME)):
                vol.All(int, vol.Range(min=0, max=100)),
            vol.Optional("preset", default=matching_label): vol.In({v: k for k, v in preset_choices.items()}),
            vol.Required("default_url", default=current_url): str,
        })
        return self.async_show_form(step_id="prayer_general", data_schema=schema)

    # ── Step 3: Per-prayer entries ─────────────────────────────────────
    async def async_step_prayer_entries(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._draft.get("prayer", {})
        entries = opts.get("entries", {})
        presets = all_preset_options()
        # The dropdown value IS the URL (or "" for Custom). Labels are the
        # friendly names.
        preset_value_to_label = {"": "Custom (use URL field)"}
        for label, url in presets.items():
            preset_value_to_label[url] = label

        if user_input is not None:
            new_entries: dict[str, dict[str, Any]] = {}
            default_url = opts.get("default_url", _DEFAULT_ADHAN_URL)
            for prayer in PRAYER_NAMES_LIST:
                enabled = user_input.get(f"{prayer}_enabled", False)
                if not enabled:
                    continue
                # If preset chosen for this prayer, use its URL; else free text
                preset_url = user_input.get(f"{prayer}_preset", "")
                url = preset_url.strip() or user_input.get(
                    f"{prayer}_url", default_url
                ).strip()
                new_entries[prayer] = {
                    "url":    url,
                    "volume": int(user_input.get(f"{prayer}_volume",
                                                 opts.get("default_volume", _DEFAULT_VOLUME))),
                    "days":   user_input.get(f"{prayer}_days", "daily"),
                }
            self._draft["prayer"] = {**opts, "entries": new_entries}
            return self.async_create_entry(title="", data=self._draft)

        # Build schema: one block per prayer
        schema_dict: dict[Any, Any] = {}
        for prayer in PRAYER_NAMES_LIST:
            existing = entries.get(prayer, {})
            current_url = existing.get("url", opts.get("default_url", _DEFAULT_ADHAN_URL))

            # Pre-select preset if current URL matches one
            matching_preset_value = ""
            for url in presets.values():
                if url == current_url:
                    matching_preset_value = url
                    break

            schema_dict[vol.Optional(
                f"{prayer}_enabled",
                default=bool(existing),
            )] = bool
            schema_dict[vol.Optional(
                f"{prayer}_preset",
                default=matching_preset_value,
            )] = vol.In(preset_value_to_label)
            schema_dict[vol.Optional(
                f"{prayer}_url",
                default=current_url,
            )] = str
            schema_dict[vol.Optional(
                f"{prayer}_volume",
                default=existing.get("volume", opts.get("default_volume", _DEFAULT_VOLUME)),
            )] = vol.All(int, vol.Range(min=0, max=100))
            schema_dict[vol.Optional(
                f"{prayer}_days",
                default=existing.get("days", "daily"),
            )] = vol.In(DAYS_OPTIONS)

        return self.async_show_form(
            step_id="prayer_entries",
            data_schema=vol.Schema(schema_dict),
        )

    # ── Step 4: Disable prayer scheduler ───────────────────────────────
    async def async_step_prayer_disable(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._draft.get("prayer", {})
        opts["enabled"] = False
        self._draft["prayer"] = opts
        return self.async_create_entry(title="", data=self._draft)
