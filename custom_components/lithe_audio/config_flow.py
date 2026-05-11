"""Config flow for the Lithe Audio integration.

Three entry points:
  - User-initiated:      manual host/port entry, model picker, cert step if LS10
  - LSSDP discovery:     populated automatically by ``async_step_discovery_lssdp``
  - Zeroconf discovery:  Google Cast / generic mDNS catches the speaker first

LS10 platform speakers REQUIRE the Lithe-issued client.pem and client.key —
the user pastes the contents of both into text fields during the flow.
"""
from __future__ import annotations

import logging
import socket
import ssl
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_CERT_KEY,
    CONF_CERT_PEM,
    CONF_MAC,
    CONF_MODEL,
    CONF_NAME,
    CONF_PLATFORM,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
    LS10_MODELS,
    MODEL_GENERIC,
    MODEL_IO1,
    MODEL_MICRO,
    MODEL_PRO,
    MODEL_PRO2,
    MODEL_V2,
    MODEL_V3,
    PLATFORM_LS10,
    PLATFORM_LS9,
)
from .discovery import async_discover

_LOGGER = logging.getLogger(__name__)

_MODEL_OPTIONS = [
    SelectOptionDict(value=MODEL_PRO2, label="PRO2 (in-ceiling)"),
    SelectOptionDict(value=MODEL_V3, label="WiFi V3"),
    SelectOptionDict(value=MODEL_IO1, label="iO1"),
    SelectOptionDict(value=MODEL_V2, label="WiFi V2"),
    SelectOptionDict(value=MODEL_PRO, label="PRO"),
    SelectOptionDict(value=MODEL_MICRO, label="Micro Subwoofer"),
    SelectOptionDict(value=MODEL_GENERIC, label="Other / Generic"),
]


def _platform_from_model(model: str) -> str:
    return PLATFORM_LS10 if model in LS10_MODELS else PLATFORM_LS9


def _validate_pem(text: str, label: str) -> bool:
    """Cheap shape check — does the input look like a PEM block?"""
    if not text or "-----BEGIN" not in text or "-----END" not in text:
        return False
    return True


class LitheAudioConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lithe Audio."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._name: str | None = None
        self._model: str = MODEL_GENERIC
        self._platform: str = PLATFORM_LS9
        self._mac: str | None = None
        self._discovered: list = []   # populated by step_discover

    # ── User-initiated entry ──────────────────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """First step: scan, or jump straight to manual entry."""
        if user_input is not None:
            if user_input.get("mode") == "scan":
                return await self.async_step_scan()
            return await self.async_step_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("mode", default="scan"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value="scan",
                                             label="Scan network for speakers"),
                            SelectOptionDict(value="manual",
                                             label="Enter speaker IP manually"),
                        ],
                        mode=SelectSelectorMode.LIST,
                    ),
                ),
            }),
        )

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Run LSSDP discovery and let the user pick a speaker."""
        if user_input is not None and user_input.get("selected"):
            choice = user_input["selected"]
            if choice == "__manual__":
                return await self.async_step_manual()
            # selected is the device unique_id; look it up
            dev = next((d for d in self._discovered
                        if d.unique_id == choice), None)
            if dev is None:
                return await self.async_step_manual()
            self._host = dev.host
            self._port = dev.port
            self._name = dev.name
            self._model = dev.model or MODEL_GENERIC
            self._platform = dev.platform
            self._mac = dev.mac
            return await self._async_after_basic_info()

        try:
            self._discovered = await async_discover(timeout=3.0)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("LSSDP discovery failed: %s", exc)
            self._discovered = []

        # Filter out already-configured speakers by unique_id (MAC)
        existing = {
            entry.unique_id for entry in self._async_current_entries()
            if entry.unique_id
        }
        new_devices = [d for d in self._discovered if d.unique_id not in existing]

        if not new_devices:
            return await self.async_step_manual()

        options = [
            SelectOptionDict(
                value=d.unique_id,
                label=f"{d.name} — {d.host} ({d.model or 'Lithe speaker'})",
            )
            for d in new_devices
        ]
        options.append(SelectOptionDict(value="__manual__",
                                        label="Enter IP manually instead…"))

        return self.async_show_form(
            step_id="scan",
            data_schema=vol.Schema({
                vol.Required("selected"): SelectSelector(
                    SelectSelectorConfig(options=options,
                                         mode=SelectSelectorMode.LIST),
                ),
            }),
            description_placeholders={"count": str(len(new_devices))},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Manual host/port/name/model entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._port = user_input.get(CONF_PORT, DEFAULT_PORT)
            self._name = user_input.get(CONF_NAME) or self._host
            self._model = user_input.get(CONF_MODEL, MODEL_GENERIC)
            self._platform = _platform_from_model(self._model)
            return await self._async_after_basic_info()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    int, vol.Range(min=1, max=65535)),
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_MODEL, default=MODEL_GENERIC): SelectSelector(
                    SelectSelectorConfig(options=_MODEL_OPTIONS,
                                         mode=SelectSelectorMode.DROPDOWN),
                ),
            }),
            errors=errors,
        )

    async def _async_after_basic_info(self) -> ConfigFlowResult:
        """Branch: LS10 needs the cert step, LS9 can finish immediately."""
        # Use MAC if we have one, otherwise fall back to host:port
        unique_id = (self._mac or f"{self._host}:{self._port}").lower().replace(":", "")
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host})

        if self._platform == PLATFORM_LS10:
            return await self.async_step_certificate()
        return await self.async_step_confirm()

    # ── LS10 certificate step ─────────────────────────────────────────────

    async def async_step_certificate(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Collect the LS10 client.pem and client.key contents."""
        errors: dict[str, str] = {}
        if user_input is not None:
            pem = user_input[CONF_CERT_PEM].strip()
            key = user_input[CONF_CERT_KEY].strip()
            if not _validate_pem(pem, "client.pem"):
                errors[CONF_CERT_PEM] = "invalid_pem"
            if not _validate_pem(key, "client.key"):
                errors[CONF_CERT_KEY] = "invalid_key"
            if not errors:
                # Smoke test: try a TLS handshake before saving anything
                test_ok, test_err = await self.hass.async_add_executor_job(
                    _test_ls10_handshake, self._host, self._port, pem, key,
                )
                if not test_ok:
                    errors["base"] = "cannot_connect"
                    _LOGGER.warning("LS10 handshake test failed: %s", test_err)
                else:
                    self._pem = pem
                    self._key = key
                    return await self.async_step_confirm()

        return self.async_show_form(
            step_id="certificate",
            data_schema=vol.Schema({
                vol.Required(CONF_CERT_PEM): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.TEXT,
                        multiline=True,
                    ),
                ),
                vol.Required(CONF_CERT_KEY): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.TEXT,
                        multiline=True,
                    ),
                ),
            }),
            errors=errors,
            description_placeholders={"host": self._host or ""},
        )

    # ── Confirm & create entry ────────────────────────────────────────────

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Final confirmation before creating the entry."""
        if user_input is not None:
            data: dict[str, Any] = {
                CONF_HOST: self._host,
                CONF_PORT: self._port,
                CONF_NAME: self._name,
                CONF_MODEL: self._model,
                CONF_PLATFORM: self._platform,
            }
            if self._mac:
                data[CONF_MAC] = self._mac
            if self._platform == PLATFORM_LS10:
                data[CONF_CERT_PEM] = self._pem
                data[CONF_CERT_KEY] = self._key
            return self.async_create_entry(title=self._name or self._host, data=data)

        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "host": self._host or "",
                "port": str(self._port),
                "model": self._model,
                "platform": self._platform,
            },
        )

    # ── Zeroconf discovery ────────────────────────────────────────────────

    async def async_step_zeroconf(self, discovery_info) -> ConfigFlowResult:
        """Handle Google Cast / generic zeroconf discovery for Lithe speakers.

        Zeroconf gives us a host and a friendly name; we still need to do
        an LSSDP probe to determine LS9 vs LS10. To keep things simple
        we just trigger the LSSDP scan step.
        """
        host = discovery_info.host
        # If we can resolve the MAC via SSDP, use it as unique_id later
        self._host = host
        self._name = discovery_info.name.split(".", 1)[0]
        await self.async_set_unique_id(f"{host}:{DEFAULT_PORT}".replace(":", ""))
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        return await self.async_step_scan()


# ── Helpers ───────────────────────────────────────────────────────────────

def _test_ls10_handshake(
    host: str, port: int, pem: str, key: str,
) -> tuple[bool, str]:
    """Synchronous TLS handshake smoke test (runs in executor).

    Returns (success, error_message). Doesn't send any LUCI commands —
    just verifies the certificates are accepted.
    """
    import tempfile
    from pathlib import Path

    cert_dir = Path(tempfile.mkdtemp(prefix="lithe_audio_test_"))
    pem_path = cert_dir / "client.pem"
    key_path = cert_dir / "client.key"
    try:
        pem_path.write_text(pem)
        key_path.write_text(key)
        try:
            key_path.chmod(0o600)
        except OSError:
            pass
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
        ctx.load_cert_chain(certfile=str(pem_path), keyfile=str(key_path))
        ctx.load_verify_locations(cafile=str(pem_path))
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        with socket.create_connection((host, port), timeout=8) as raw:
            with ctx.wrap_socket(raw, server_hostname=None) as tls:
                tls.do_handshake()
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    finally:
        for p in (pem_path, key_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            cert_dir.rmdir()
        except OSError:
            pass
