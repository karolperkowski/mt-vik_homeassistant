"""DataUpdateCoordinator for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

import logging
from datetime import timedelta

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
    EVENT_ROUTE_CHANGED,
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
        # Last known output->input mapping, used to diff incoming states for
        # EVENT_ROUTE_CHANGED. None until the first MatrixState is seen at all
        # (that first state is a baseline sync and must not fire events).
        self._last_routes: dict[int, int] | None = None
        # The api client fires this callback from within the event loop, so we
        # can hand the state straight to the coordinator without job scheduling.
        client.set_state_callback(self._async_handle_state)

    @callback
    def _async_handle_state(self, state: MatrixState) -> None:
        """Handle a pushed state snapshot from the client (event loop only)."""
        self.async_set_updated_data(state)
        self._async_fire_route_change_events(state)
        self._async_update_device_registry(state)

    @callback
    def _async_fire_route_change_events(self, state: MatrixState) -> None:
        """Fire EVENT_ROUTE_CHANGED on the HA bus for each changed output.

        Compares ``state.routes`` against the last routes seen (from either
        the push or the polling path) and fires one event per output whose
        routed input differs. The very first MatrixState ever observed is a
        baseline sync -- there is nothing to diff against yet -- so it never
        produces events, and neither does a state that changed nothing. A
        reconnect resync that reveals a route which changed while
        disconnected does fire, with ``old_input`` set to the last routes we
        knew about before the disconnect.
        """
        new_routes = state.routes
        old_routes = self._last_routes
        self._last_routes = dict(new_routes)
        if old_routes is None:
            return
        changed = {
            output: new_input
            for output, new_input in new_routes.items()
            if old_routes.get(output) != new_input
        }
        if not changed:
            return
        # One device-registry lookup per state update (not per event) keeps
        # this cheap; it's a plain identifiers-index dict lookup.
        device_id = self._async_device_id()
        for output, new_input in changed.items():
            data: dict[str, str | int | None] = {
                "entry_id": self.config_entry.entry_id,
                "output": output,
                "old_input": old_routes.get(output),
                "new_input": new_input,
            }
            if device_id is not None:
                data["device_id"] = device_id
            self.hass.bus.async_fire(EVENT_ROUTE_CHANGED, data)

    @callback
    def _async_device_id(self) -> str | None:
        """Return this entry's device registry id, if it already exists."""
        device = dr.async_get(self.hass).async_get_device(
            identifiers={(DOMAIN, self.config_entry.entry_id)}
        )
        return device.id if device is not None else None

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
            state = await self.client.async_refresh()
        except MTVikiError as err:
            raise UpdateFailed(f"Error refreshing MT-VIKI matrix state: {err}") from err
        self._async_fire_route_change_events(state)
        return state


type MTVikiConfigEntry = ConfigEntry[MTVikiCoordinator]
