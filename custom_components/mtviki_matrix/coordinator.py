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
    CONF_INPUT_NAMES,
    CONF_MATRIX_SIZE,
    CONF_POLL_INTERVAL,
    CONF_SCENE_NAMES,
    DEFAULT_ENABLE_POLLING,
    DEFAULT_MATRIX_SIZE,
    DEFAULT_MODEL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    EVENT_ROUTE_CHANGED,
    default_input_name,
    default_scene_name,
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
        # Scene-recall tracking for the "current scene" sensor. The device
        # never exposes scene *contents*, only a recall command, so this is
        # only ever "the last scene we recalled, and the routing hasn't
        # changed since" -- see async_recall_scene() and
        # _async_check_scene_divergence() below, and MTVikiCurrentSceneSensor
        # in sensor.py for the user-facing caveat.
        self._last_recalled_scene: int | None = None
        self._recalled_routes_snapshot: dict[int, int] | None = None
        # The api client fires this callback from within the event loop, so we
        # can hand the state straight to the coordinator without job scheduling.
        client.set_state_callback(self._async_handle_state)

    @callback
    def _async_handle_state(self, state: MatrixState) -> None:
        """Handle a pushed state snapshot from the client (event loop only)."""
        self.async_set_updated_data(state)
        self._async_fire_route_change_events(state)
        self._async_check_scene_divergence(state)
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

    # ------------------------------------------------------------- naming

    def input_name(self, port: int) -> str:
        """User-configured label for an input, or the default "Input N"."""
        names: dict[str, str] = self.config_entry.options.get(CONF_INPUT_NAMES, {})
        return names.get(str(port)) or default_input_name(port)

    def input_names(self) -> list[str]:
        """Ordered labels for inputs 1..self.inputs (select/media_player options)."""
        return [self.input_name(port) for port in range(1, self.inputs + 1)]

    def input_port_for_name(self, name: str) -> int | None:
        """Reverse lookup: label -> input port number.

        If two inputs share a label (the user typed the same name twice) the
        lowest-numbered match wins; there's no way to do better with a plain
        name-keyed selector.
        """
        for port in range(1, self.inputs + 1):
            if self.input_name(port) == name:
                return port
        return None

    def scene_name(self, scene: int) -> str:
        """User-configured label for a scene, or the default "Scene N"."""
        names: dict[str, str] = self.config_entry.options.get(CONF_SCENE_NAMES, {})
        return names.get(str(scene)) or default_scene_name(scene)

    # --------------------------------------------------------- scene recall

    async def async_recall_scene(self, scene: int) -> None:
        """Recall a scene on the device and track it for the current-scene sensor.

        Both the scene button and the ``recall_scene`` service call this
        (rather than the client directly) so scene tracking works regardless
        of entry point. ``client.async_scene_recall`` awaits the device's
        ``SWS`` echo, which -- by the time this call returns -- has already
        been parsed and pushed through ``_async_handle_state`` above, so
        ``self.data.routes`` here is the freshly recalled routing table.
        """
        await self.client.async_scene_recall(scene)
        self._last_recalled_scene = scene
        self._recalled_routes_snapshot = (
            dict(self.data.routes) if self.data is not None else None
        )

    @property
    def current_scene_name(self) -> str | None:
        """Name of the last recalled scene, or None if none is tracked.

        ``_async_check_scene_divergence`` clears the tracked scene as soon as
        the routing no longer matches what the recall produced, so by the
        time this is read it is already known to still match.
        """
        if self._last_recalled_scene is None:
            return None
        return self.scene_name(self._last_recalled_scene)

    @callback
    def _async_check_scene_divergence(self, state: MatrixState) -> None:
        """Clear scene tracking once routing no longer matches the last recall.

        The device exposes no way to read back scene *contents* -- only a
        recall command -- so this integration can only ever claim "the
        routing still matches what the last recalled scene produced", not
        "the device currently has scene N active". Any routing change (from
        this integration, the front panel, the IR remote, or a service call)
        that differs from the snapshot taken right after the last recall
        clears the tracked scene, and the current-scene sensor reports "none"
        again until another scene is recalled.
        """
        if self._recalled_routes_snapshot is None:
            return
        if state.routes != self._recalled_routes_snapshot:
            self._last_recalled_scene = None
            self._recalled_routes_snapshot = None

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
        self._async_check_scene_divergence(state)
        return state


type MTVikiConfigEntry = ConfigEntry[MTVikiCoordinator]
