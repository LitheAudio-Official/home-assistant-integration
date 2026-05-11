"""DataUpdateCoordinator for Lithe Audio.

The LUCI protocol is push-driven: the speaker emits state updates whenever
anything changes, and we treat those as authoritative. We use a coordinator
mainly to provide the standard ``async_config_entry_first_refresh`` pattern
and to bridge ``DeviceState`` updates into HA entity state changes via
``async_set_updated_data``.
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .luci import DeviceState, LitheAudioClient

_LOGGER = logging.getLogger(__name__)


class LitheAudioCoordinator(DataUpdateCoordinator[DeviceState]):
    """Coordinator wrapping the push-based LUCI client."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: LitheAudioClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}[{client.host}]",
            update_interval=None,   # purely push-driven
        )
        self.config_entry = entry
        self.client = client
        self._unsub = client.add_listener(self._on_client_state)

    @callback
    def _on_client_state(self, state: DeviceState) -> None:
        """Fan out client state updates to all subscribed entities."""
        # async_set_updated_data is safe to call from sync context
        self.async_set_updated_data(state)

    async def _async_update_data(self) -> DeviceState:
        """Called by ``async_config_entry_first_refresh``.

        We wait briefly for the client to establish a connection and
        receive at least the initial state burst.
        """
        # Give the connect loop a couple of seconds to complete the
        # registration → initial-state-sync round trip before the first
        # refresh resolves. If it doesn't, that's fine — entities will
        # just appear unavailable and update on the next push.
        for _ in range(20):           # ~2 seconds
            if self.client.state.connected:
                break
            await asyncio.sleep(0.1)
        return self.client.state

    async def async_shutdown(self) -> None:
        """Detach from the client and stop the coordinator."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await super().async_shutdown()
