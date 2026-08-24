"""DataUpdateCoordinator for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MATRIX_SIZES, MatrixState, MTVikiClient, MTVikiError
from .const import (
    CONF_ENABLE_POLLING,
    CONF_MATRIX_SIZE,
    CONF_POLL_INTERVAL,
    DEFAULT_ENABLE_POLLING,
    DEFAULT_MATRIX_SIZE,
    DEFAULT_MODEL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class MTVikiCoordinator(DataUpdateCoordinator[MatrixState]):
    """Push-first coordinator for a MT-VIKI HDMI matrix.

    The client pushes every state change (solicited replies and unsolicited
    "SWS" lines alike) through set_state_callback; polling is optional and
    only enabled through the config entry options.
    """

    config_entry: MTVikiConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: MTVikiClient
    ) -> None:
        """Initialize the coordinator."""
        enable_polling: bool = entry.options.get(
            CONF_ENABLE_POLLING, DEFAULT_ENABLE_POLLING
        )
        poll_interval: int = entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_HOST]}",
            # No polling unless explicitly enabled in the options; entry is
            # reloaded on options change, so this is re-evaluated then.
            update_interval=(
                timedelta(seconds=poll_interval) if enable_polling else None
            ),
        )
        self.client = client
        self.inputs, self.outputs = MATRIX_SIZES.get(
            entry.data.get(CONF_MATRIX_SIZE, DEFAULT_MATRIX_SIZE),
            MATRIX_SIZES[DEFAULT_MATRIX_SIZE],
        )
        self._device_meta: tuple[str | None, str | None] | None = None
        # The api client fires this callback from within the event loop, so we
        # can hand the state straight to the coordinator without job scheduling.
        client.set_state_callback(self._async_handle_state)

    @callback
    def _async_handle_state(self, state: MatrixState) -> None:
        """Handle a pushed state snapshot from the client (event loop only)."""
        self.async_set_updated_data(state)
        self._async_update_device_registry(state)

    @callback
    def _async_update_device_registry(self, state: MatrixState) -> None:
        """Sync model/firmware into the device registry once they are known."""
        meta = (state.model, state.firmware)
        if meta == self._device_meta:
            return
        self._device_meta = meta
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, self.config_entry.entry_id)}
        )
        if device is not None:
            device_registry.async_update_device(
                device.id,
                model=state.model or DEFAULT_MODEL,
                sw_version=state.firmware,
            )

    async def _async_update_data(self) -> MatrixState:
        """Poll the device (only used when polling is enabled in options)."""
        try:
            return await self.client.async_refresh()
        except MTVikiError as err:
            raise UpdateFailed(f"Error refreshing MT-VIKI matrix state: {err}") from err


type MTVikiConfigEntry = ConfigEntry[MTVikiCoordinator]
