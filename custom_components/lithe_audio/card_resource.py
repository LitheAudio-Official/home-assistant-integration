"""Auto-register the Lithe Audio Lovelace card as an HTTP-served resource.

On HA startup:
  1. Serve /lithe_audio_card.js from the integration directory
  2. Register that URL as a Lovelace resource (so users don't have to
     add it manually under Settings → Dashboards → Resources)

Inspired by the Wake Alarm thread approach — ship the card with the
integration so HACS install is one step (no separate card install).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_URL = "/lithe_audio_card.js"
CARD_FILENAME = "lithe-audio-card.js"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card JS and register it as a Lovelace resource.

    Safe to call multiple times — checks idempotently.
    """
    integration_dir = Path(__file__).parent
    card_path = integration_dir / CARD_FILENAME
    if not card_path.exists():
        _LOGGER.warning("Lithe Audio card JS not found at %s", card_path)
        return

    # 1) Serve the file at /lithe_audio_card.js
    try:
        await hass.http.async_register_static_paths([
            StaticPathConfig(CARD_URL, str(card_path), cache_headers=False),
        ])
        _LOGGER.info("Lithe Audio card served at %s", CARD_URL)
    except Exception as e:
        # Already registered (idempotent) — ignore
        _LOGGER.debug("Card static path registration: %s", e)

    # 2) Register as Lovelace resource (so users get auto-import)
    try:
        # Lovelace resources live in hass.data["lovelace"]["resources"] —
        # API has evolved across HA versions, try the modern path first.
        lovelace = hass.data.get("lovelace")
        if lovelace is None:
            _LOGGER.debug("Lovelace not yet initialized — skipping resource registration")
            return

        # Newer HA: lovelace is a dict with a 'resources' key
        resources = None
        if hasattr(lovelace, "resources"):
            resources = lovelace.resources
        elif isinstance(lovelace, dict):
            resources = lovelace.get("resources")

        if resources is None:
            _LOGGER.debug("Lovelace resources object not found — user can add manually")
            return

        # Load existing resources
        if hasattr(resources, "async_load"):
            await resources.async_load()

        # Check if our resource is already registered
        existing = []
        if hasattr(resources, "async_items"):
            existing = resources.async_items() or []
        elif hasattr(resources, "data") and isinstance(resources.data, dict):
            existing = list(resources.data.get("items", []))

        already_present = any(
            (r.get("url") if isinstance(r, dict) else getattr(r, "url", "")) == CARD_URL
            for r in existing
        )
        if already_present:
            _LOGGER.debug("Lithe Audio card resource already registered")
            return

        # Add as new resource
        if hasattr(resources, "async_create_item"):
            await resources.async_create_item({
                "url":           CARD_URL,
                "res_type":      "module",
            })
            _LOGGER.info("Registered Lithe Audio card as Lovelace resource")
        else:
            _LOGGER.info(
                "Lovelace resources API unavailable — to use the card, add "
                "this resource manually under Settings → Dashboards → "
                "Resources: URL=%s, Type=JavaScript Module", CARD_URL,
            )
    except Exception as e:
        _LOGGER.info(
            "Could not auto-register Lovelace card resource (%s) — "
            "users can add %s manually under Settings → Dashboards → "
            "Resources as a JavaScript Module.", e, CARD_URL,
        )
