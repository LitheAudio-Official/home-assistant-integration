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
