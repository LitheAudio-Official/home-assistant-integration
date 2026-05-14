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
from homeassistant.helpers import selector

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
PRAYER_NAMES_LIST = ["fajr", "sunrise", "dhuhr", "asr", "sunset", "maghrib", "isha"]
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
        # Per-prayer wizard state
        self._wizard_index: int = 0
        self._wizard_entries: dict[str, dict[str, Any]] = {}
        # Alarm edit state — None means "new alarm", dict means "editing"
        self._alarm_draft: dict[str, Any] | None = None

    # ── Step 1: top-level menu ─────────────────────────────────────────
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "prayer_general":   "📅  Prayer Schedule — Location & defaults",
                "prayer_entries":   "🕋  Prayer Schedule — Per-prayer settings",
                "prayer_test":      "▶️  Test play an Adhan / Quran URL",
                "prayer_view":      "📋  View today's schedule",
                "alarms":           "⏰  Alarms — view, add, edit",
                "logging":          "🐞  Debug logging (for support)",
                "prayer_disable":   "✖️  Disable Prayer Scheduler",
            },
        )

    # ── Alarms — list existing alarms + "Add new" ──────────────────────
    async def async_step_alarms(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Top-level Alarms menu: list existing alarms + add new."""
        from .alarms import get_manager
        mgr = get_manager(self.hass)
        if not mgr:
            return await self.async_step_init()

        alarms = mgr.list_alarms()

        # Selection from list
        if user_input is not None:
            choice = user_input.get("choice", "")
            if choice == "__add__":
                self._alarm_draft = None  # signals "new"
                return await self.async_step_alarm_edit()
            if choice and choice in {a["id"] for a in alarms}:
                self._alarm_draft = dict(mgr.get_alarm(choice) or {})
                return await self.async_step_alarm_edit()
            return await self.async_step_init()

        # Build choice dropdown: each alarm + add-new
        choices: dict[str, str] = {}
        for a in alarms:
            enabled_icon = "✅" if a.get("enabled") else "⚪"
            name = a.get("name", "Alarm")
            t = a.get("time", "--:--")
            repeat = a.get("repeat", "daily")
            if repeat == "weekly":
                days = ",".join(a.get("days", []) or [])
                repeat_str = f"weekly [{days}]"
            elif repeat == "one_off":
                repeat_str = f"once on {a.get('date','?')}"
            elif repeat == "monthly":
                repeat_str = f"day {a.get('day_of_month','?')}"
            else:
                repeat_str = "daily"
            choices[a["id"]] = f"{enabled_icon} {name} — {t} ({repeat_str})"
        choices["__add__"] = "➕  Add new alarm"

        schema = vol.Schema({
            vol.Required("choice", default="__add__"): vol.In(choices),
        })
        if alarms:
            description = (
                f"You have {len(alarms)} alarm(s). Pick one to edit/delete, "
                "or '➕ Add new alarm' to create one."
            )
        else:
            description = (
                "No alarms yet. Choose '➕ Add new alarm' to create your first."
            )
        return self.async_show_form(
            step_id="alarms",
            data_schema=schema,
            description_placeholders={"summary": description},
        )

    # ── Alarm edit (also handles create) ───────────────────────────────
    async def async_step_alarm_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create or edit an alarm.

        self._alarm_draft is either None (creating) or a dict (editing).
        """
        from .alarms import (
            get_manager, default_alarm, DAY_TOKENS,
            REPEAT_ONE_OFF, REPEAT_DAILY, REPEAT_WEEKLY, REPEAT_MONTHLY,
            SOURCE_PRESET, SOURCE_FAVOURITE, SOURCE_CHIME, SOURCE_URL,
        )
        mgr = get_manager(self.hass)
        if not mgr:
            return await self.async_step_init()

        editing = self._alarm_draft is not None
        existing = self._alarm_draft if editing else default_alarm()

        # Resolve the host of THIS speaker so it can be the default target
        my_host = self._entry.data.get("host", "")

        # Handle delete
        if user_input is not None and user_input.get("_action") == "delete":
            if editing and existing.get("id"):
                await mgr.async_delete_alarm(existing["id"])
                _LOGGER.info("Deleted alarm %s via UI", existing["id"])
            return await self.async_step_alarms()

        # Handle save
        if user_input is not None and user_input.get("_action") != "delete":
            # Build updated alarm dict
            patch = {
                "name":            (user_input.get("name") or existing["name"]).strip(),
                "time":            user_input.get("time", existing["time"]),
                "repeat":          user_input.get("repeat", existing["repeat"]),
                "days":            user_input.get("days", existing.get("days") or DAY_TOKENS),
                "day_of_month":    int(user_input.get("day_of_month",
                                                      existing.get("day_of_month", 1))),
                "date":            user_input.get("date", existing.get("date")),
                "speakers":        [s.strip() for s in
                                    (user_input.get("speakers") or my_host).split(",")
                                    if s.strip()],
                "source":          user_input.get("source", existing["source"]),
                "preset_url":      (user_input.get("preset_url") or existing.get("preset_url","")).strip(),
                "favourite_slot":  int(user_input.get("favourite_slot",
                                                      existing.get("favourite_slot", 1))),
                "chime_slot":      int(user_input.get("chime_slot",
                                                      existing.get("chime_slot", 1))),
                "custom_url":      (user_input.get("custom_url") or existing.get("custom_url","")).strip(),
                "volume":          int(user_input.get("volume", existing.get("volume", 60))),
                "fade_in_seconds": int(user_input.get("fade_in_seconds",
                                                     existing.get("fade_in_seconds", 0))),
                "snooze_minutes":  int(user_input.get("snooze_minutes",
                                                     existing.get("snooze_minutes", 9))),
                "enabled":         bool(user_input.get("enabled", True)),
            }
            # If a preset selected via the dropdown, override preset_url
            preset_choice = (user_input.get("preset_choice") or "").strip()
            if preset_choice and patch["source"] == SOURCE_PRESET:
                patch["preset_url"] = preset_choice

            if editing:
                await mgr.async_update_alarm(existing["id"], patch)
                _LOGGER.info("Updated alarm %s via UI", existing["id"])
            else:
                alarm = {**existing, **patch}
                alarm.pop("id", None)  # let manager assign new id
                aid = await mgr.async_add_alarm(alarm)
                _LOGGER.info("Created alarm %s via UI", aid)
            return await self.async_step_alarms()

        # Render the form
        from .const import all_preset_options
        presets = all_preset_options()
        preset_choices = {"": "(use URL field below)"}
        for label, url in presets.items():
            preset_choices[url] = label

        # Match current preset_url to dropdown
        match_preset = ""
        cur_url = existing.get("preset_url", "")
        for url in presets.values():
            if url == cur_url:
                match_preset = url
                break

        speakers_default = ", ".join(existing.get("speakers") or [my_host])

        repeat_options = {
            REPEAT_ONE_OFF: "One-off (single date)",
            REPEAT_DAILY:   "Daily",
            REPEAT_WEEKLY:  "Weekly (specific days)",
            REPEAT_MONTHLY: "Monthly (day of month)",
        }
        source_options = {
            SOURCE_PRESET:    "Preset URL (Adhan / Quran)",
            SOURCE_FAVOURITE: "Saved Favourite (1-9)",
            SOURCE_CHIME:     "Embedded Chime (1-10)",
            SOURCE_URL:       "Custom HTTP URL",
        }
        action_options = {
            "save":   "💾  Save",
        }
        if editing:
            action_options["delete"] = "🗑  Delete this alarm"

        schema_dict: dict[Any, Any] = {
            vol.Required("name", default=existing.get("name", "New Alarm")): str,
            vol.Required("time", default=existing.get("time", "07:00")):
                selector.TimeSelector() if hasattr(selector, "TimeSelector") else str,
            vol.Required("enabled", default=bool(existing.get("enabled", True))): bool,
            vol.Required("repeat", default=existing.get("repeat", REPEAT_DAILY)):
                vol.In(repeat_options),
            vol.Optional("days", default=existing.get("days") or DAY_TOKENS):
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "mon", "label": "Monday"},
                            {"value": "tue", "label": "Tuesday"},
                            {"value": "wed", "label": "Wednesday"},
                            {"value": "thu", "label": "Thursday"},
                            {"value": "fri", "label": "Friday"},
                            {"value": "sat", "label": "Saturday"},
                            {"value": "sun", "label": "Sunday"},
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
            vol.Optional("day_of_month", default=existing.get("day_of_month", 1)):
                vol.All(int, vol.Range(min=1, max=31)),
            vol.Optional("date", default=existing.get("date", "") or ""): str,
            vol.Required("speakers", default=speakers_default): str,
            vol.Required("source", default=existing.get("source", SOURCE_PRESET)):
                vol.In(source_options),
            vol.Optional("preset_choice", default=match_preset):
                vol.In(preset_choices),
            vol.Optional("preset_url", default=existing.get("preset_url", "")): str,
            vol.Optional("favourite_slot", default=existing.get("favourite_slot", 1)):
                vol.All(int, vol.Range(min=1, max=9)),
            vol.Optional("chime_slot", default=existing.get("chime_slot", 1)):
                vol.All(int, vol.Range(min=1, max=10)),
            vol.Optional("custom_url", default=existing.get("custom_url", "")): str,
            vol.Required("volume", default=existing.get("volume", 60)):
                vol.All(int, vol.Range(min=0, max=100)),
            vol.Optional("fade_in_seconds", default=existing.get("fade_in_seconds", 0)):
                vol.All(int, vol.Range(min=0, max=600)),
            vol.Optional("snooze_minutes", default=existing.get("snooze_minutes", 9)):
                vol.All(int, vol.Range(min=1, max=60)),
            vol.Required("_action", default="save"): vol.In(action_options),
        }
        title_word = "Edit Alarm" if editing else "New Alarm"
        return self.async_show_form(
            step_id="alarm_edit",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "title": title_word,
                "name":  existing.get("name", "New Alarm"),
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

    # ── Step 3: Per-prayer entries (wizard, one step per prayer) ───────
    async def async_step_prayer_entries(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Entry point — kick off the per-prayer wizard at the first prayer."""
        self._wizard_index = 0
        # Stash a working copy of entries to be mutated as we step through
        opts = self._draft.get("prayer", {})
        self._wizard_entries = dict(opts.get("entries", {}) or {})
        return await self._show_prayer_step(None)

    async def async_step_prayer_one(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Render one prayer's form; advance to next on submit."""
        return await self._show_prayer_step(user_input)

    async def _show_prayer_step(
        self, user_input: dict[str, Any] | None
    ) -> FlowResult:
        opts = self._draft.get("prayer", {})
        presets = all_preset_options()
        preset_value_to_label = {"": "Custom (use URL field)"}
        for label, url in presets.items():
            preset_value_to_label[url] = label

        default_url    = opts.get("default_url", _DEFAULT_ADHAN_URL)
        default_volume = opts.get("default_volume", _DEFAULT_VOLUME)

        # Save submitted values from the previous prayer (if any)
        if user_input is not None and self._wizard_index < len(PRAYER_NAMES_LIST):
            prayer = PRAYER_NAMES_LIST[self._wizard_index]
            enabled = user_input.get("enabled", False)
            if enabled:
                preset_url = (user_input.get("preset") or "").strip()
                url = preset_url or user_input.get("url", default_url).strip()
                self._wizard_entries[prayer] = {
                    "url":    url,
                    "volume": int(user_input.get("volume", default_volume)),
                    "days":   user_input.get("days", "daily"),
                }
            else:
                self._wizard_entries.pop(prayer, None)
            self._wizard_index += 1

        # Done? Save and return to menu
        if self._wizard_index >= len(PRAYER_NAMES_LIST):
            self._draft["prayer"] = {**opts, "entries": self._wizard_entries}
            return self.async_create_entry(title="", data=self._draft)

        # Render the next prayer
        prayer = PRAYER_NAMES_LIST[self._wizard_index]
        existing = self._wizard_entries.get(prayer, {})
        current_url = existing.get("url", default_url)
        matching_preset_value = ""
        for url in presets.values():
            if url == current_url:
                matching_preset_value = url
                break

        schema = vol.Schema({
            vol.Optional("enabled", default=bool(existing)): bool,
            vol.Optional("preset",  default=matching_preset_value):
                vol.In(preset_value_to_label),
            vol.Optional("url",     default=current_url): str,
            vol.Required("volume",  default=existing.get("volume", default_volume)):
                vol.All(int, vol.Range(min=0, max=100)),
            vol.Required("days",    default=existing.get("days", "daily")):
                vol.In(DAYS_OPTIONS),
        })

        # Pretty names for the step title placeholder
        pretty = {
            "fajr":    "Fajr (Pre-dawn)",
            "sunrise": "Sunrise (Shuruq)",
            "dhuhr":   "Dhuhr (Midday)",
            "asr":     "Asr (Afternoon)",
            "sunset":  "Sunset (Maghrib precursor)",
            "maghrib": "Maghrib (Sunset prayer)",
            "isha":    "Isha (Night)",
        }
        return self.async_show_form(
            step_id="prayer_one",
            data_schema=schema,
            description_placeholders={
                "prayer_name": pretty.get(prayer, prayer.capitalize()),
                "step_num":    str(self._wizard_index + 1),
                "step_total":  str(len(PRAYER_NAMES_LIST)),
            },
        )

    # ── Step 3b: Test play (immediately play a URL on THIS speaker) ─────
    async def async_step_prayer_test(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Play a chosen URL on this speaker right now via the tannoy flow.

        Lets the user verify their chosen Adhan / Quran URL actually plays
        before scheduling it. Uses the same notify.lithe_tannoy path that
        the scheduler uses at prayer time, so a working test means a
        working schedule.
        """
        opts = self._draft.get("prayer", {})
        presets = all_preset_options()
        # Reverse map: friendly label → URL. Add "Custom" sentinel.
        preset_label_to_url: dict[str, str] = {"Custom URL (use field below)": ""}
        for label, url in presets.items():
            preset_label_to_url[label] = url
        # voluptuous needs {value: label} for the dropdown to display labels
        preset_value_to_label = {v: k for k, v in preset_label_to_url.items()}

        errors: dict[str, str] = {}

        if user_input is not None:
            # Choose URL: preset wins if set, otherwise custom URL field
            preset_url = (user_input.get("preset") or "").strip()
            custom_url = (user_input.get("url") or "").strip()
            url = preset_url or custom_url or opts.get("default_url", _DEFAULT_ADHAN_URL)
            if not url:
                errors["base"] = "missing_url"
            else:
                volume = int(user_input.get("volume",
                                            opts.get("default_volume", _DEFAULT_VOLUME)))
                host = self._entry.data.get("host")
                # Fire-and-forget the tannoy notify with this speaker as target
                try:
                    self.hass.async_create_task(
                        self.hass.services.async_call(
                            "notify", "lithe_tannoy",
                            {
                                "message": url,
                                "data": {
                                    "mode":     "start",
                                    "volume":   volume,
                                    "speakers": [host],
                                },
                            },
                            blocking=False,
                        )
                    )
                    _LOGGER.info(
                        "Prayer test: playing %s on %s at volume %d", url, host, volume,
                    )
                except Exception as e:
                    _LOGGER.error("Prayer test failed: %s", e)
                    errors["base"] = "test_failed"

                if not errors:
                    # Return to menu so the user can pick another action
                    return await self.async_step_init()

        # Default URL/preset based on what's saved
        default_url = opts.get("default_url", _DEFAULT_ADHAN_URL)
        matching_preset_value = ""
        for url in presets.values():
            if url == default_url:
                matching_preset_value = url
                break

        schema = vol.Schema({
            vol.Optional("preset", default=matching_preset_value):
                vol.In(preset_value_to_label),
            vol.Optional("url", default=default_url): str,
            vol.Required("volume", default=opts.get("default_volume", _DEFAULT_VOLUME)):
                vol.All(int, vol.Range(min=0, max=100)),
        })
        return self.async_show_form(
            step_id="prayer_test",
            data_schema=schema,
            errors=errors,
        )

    # ── Step 3c: View today's schedule (read-only summary) ──────────────
    async def async_step_prayer_view(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show today's resolved prayer times + scheduled actions."""
        if user_input is not None:
            return await self.async_step_init()

        # Pull current schedule state from the global prayer data store
        prayer_state = self.hass.data.get(DOMAIN, {}).get("prayer", {}) or {}
        times: dict[str, str] = prayer_state.get("times", {}) or {}
        host = self._entry.data.get("host")

        opts = self._draft.get("prayer", {})
        entries = opts.get("entries", {}) or {}
        city = opts.get("city", "—")
        country = opts.get("country", "—")
        method = opts.get("method", "—")
        method_label = CALC_METHODS.get(int(method), str(method)) if isinstance(method, int) else str(method)
        enabled = opts.get("enabled", False)

        # Build a human-readable summary
        lines = [
            f"**Speaker:** {host}",
            f"**Status:** {'✅ Enabled' if enabled else '⚪ Disabled'}",
            f"**Location:** {city}, {country}",
            f"**Calculation:** {method_label}",
            "",
            "**Today's prayer times:**",
        ]
        if times:
            for p in PRAYER_NAMES_LIST:
                t = times.get(p, "—")
                e = entries.get(p, {})
                marker = "✅" if e else "⚪"
                if e:
                    days = e.get("days", "daily")
                    vol_v = e.get("volume", "?")
                    lines.append(f"  {marker} **{p.capitalize()}** at {t} — vol {vol_v}, {days}")
                else:
                    lines.append(f"  {marker} {p.capitalize()} at {t} (not scheduled)")
        else:
            lines.append("  _Times not yet fetched — submit the General step first._")

        description = "\n".join(lines)

        schema = vol.Schema({
            vol.Optional("acknowledge", default=True): bool,
        })
        return self.async_show_form(
            step_id="prayer_view",
            data_schema=schema,
            description_placeholders={"summary": description},
        )

    # ── Step 3d: Toggle debug logging ──────────────────────────────────
    async def async_step_logging(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Toggle debug logging for the lithe_audio integration.

        Saves the chosen level to logger.async_set_level() so the user
        doesn't have to edit configuration.yaml or restart HA.
        """
        if user_input is not None:
            level = user_input.get("level", "info")
            try:
                # HA service: logger.set_level — sets per-logger level live
                await self.hass.services.async_call(
                    "logger", "set_level",
                    {f"custom_components.lithe_audio": level},
                    blocking=True,
                )
                _LOGGER.info("Lithe Audio log level set to %s", level)
            except Exception as e:
                _LOGGER.error("Failed to set log level: %s", e)
            return await self.async_step_init()

        schema = vol.Schema({
            vol.Required("level", default="info"): vol.In({
                "debug":    "Debug   — verbose (for chime/protocol issues)",
                "info":     "Info    — normal (recommended)",
                "warning":  "Warning — quiet",
                "error":    "Error   — silent unless something breaks",
            }),
        })
        return self.async_show_form(step_id="logging", data_schema=schema)

    # ── Step 4: Disable prayer scheduler ───────────────────────────────
    async def async_step_prayer_disable(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        opts = self._draft.get("prayer", {})
        opts["enabled"] = False
        self._draft["prayer"] = opts
        return self.async_create_entry(title="", data=self._draft)
