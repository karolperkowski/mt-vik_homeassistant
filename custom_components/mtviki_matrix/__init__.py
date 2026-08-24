"""The MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.setup import async_when_setup

from .api import MATRIX_SIZES, MTVikiClient, MTVikiConnectionError, MTVikiError
from .const import (
    ALL_OUTPUTS,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_COUNT,
    ATTR_DEVICE_ID,
    ATTR_EDID,
    ATTR_INPUT,
    ATTR_INTERVAL,
    ATTR_MODE,
    ATTR_OUTPUT,
    ATTR_OUTPUTS,
    ATTR_SCENE,
    CARD_FILENAME,
    CARD_URL_PATH,
    CONF_MATRIX_SIZE,
    DEFAULT_MATRIX_SIZE,
    DOMAIN,
    MAX_PORT_NUMBER,
    PLATFORMS,
    SERVICE_LOCATE,
    SERVICE_RECALL_SCENE,
    SERVICE_SAVE_SCENE,
    SERVICE_SET_INPUT_EDID,
    SERVICE_SET_OUTPUT_HDCP,
    SERVICE_SET_ROUTE,
)
from .coordinator import MTVikiConfigEntry, MTVikiCoordinator

_LOGGER = logging.getLogger(__name__)

_CARD_JS_PATH = Path(__file__).parent / "www" / CARD_FILENAME

# Guards _async_register_frontend_resources against running more than once
# per HA process. async_setup() below is itself only ever called once by HA
# per domain load, so in practice this is redundant with that guarantee, but
# it costs nothing and makes "register once" true by construction rather
# than by relying on a caller's behavior.
_DATA_FRONTEND_RESOURCE_REGISTERED = f"{DOMAIN}_frontend_resource_registered"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_TARGET_FIELDS = {
    vol.Optional(ATTR_DEVICE_ID): cv.string,
    vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
}


def _targeted_schema(fields: dict[vol.Marker, object]) -> vol.All:
    """Build a service schema that requires a device or config entry target."""
    return vol.All(
        vol.Schema({**_TARGET_FIELDS, **fields}),
        cv.has_at_least_one_key(ATTR_DEVICE_ID, ATTR_CONFIG_ENTRY_ID),
    )


_PORT_NUMBER = vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_PORT_NUMBER))

SET_ROUTE_SCHEMA = _targeted_schema(
    {
        vol.Required(ATTR_INPUT): _PORT_NUMBER,
        vol.Required(ATTR_OUTPUTS): vol.Any(
            # The literal string "all" routes the input to every output.
            vol.All(vol.Lower, ALL_OUTPUTS),
            vol.All(cv.ensure_list, [_PORT_NUMBER], vol.Length(min=1)),
        ),
    }
)
SCENE_SCHEMA = _targeted_schema(
    {vol.Required(ATTR_SCENE): vol.All(vol.Coerce(int), vol.Range(min=1, max=16))}
)
SET_OUTPUT_HDCP_SCHEMA = _targeted_schema(
    {
        vol.Required(ATTR_OUTPUT): _PORT_NUMBER,
        vol.Required(ATTR_MODE): vol.All(vol.Coerce(int), vol.Range(min=0, max=3)),
    }
)
SET_INPUT_EDID_SCHEMA = _targeted_schema(
    {
        vol.Required(ATTR_INPUT): _PORT_NUMBER,
        vol.Required(ATTR_EDID): vol.All(vol.Coerce(int), vol.Range(min=1, max=16)),
    }
)
LOCATE_SCHEMA = _targeted_schema(
    {
        vol.Optional(ATTR_COUNT, default=4): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=20)
        ),
        vol.Optional(ATTR_INTERVAL, default=0.35): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=2.0)
        ),
    }
)


@callback
def _async_get_coordinator(hass: HomeAssistant, call: ServiceCall) -> MTVikiCoordinator:
    """Resolve a service call target (device or config entry) to a coordinator."""
    entry: MTVikiConfigEntry | None = None
    if (entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID)) is not None:
        candidate = hass.config_entries.async_get_entry(entry_id)
        if candidate is None or candidate.domain != DOMAIN:
            raise ServiceValidationError(f"'{entry_id}' is not a {DOMAIN} config entry")
        entry = candidate
    else:
        device_id: str = call.data[ATTR_DEVICE_ID]
        device = dr.async_get(hass).async_get(device_id)
        if device is None:
            raise ServiceValidationError(f"Device '{device_id}' not found")
        for candidate_id in device.config_entries:
            candidate = hass.config_entries.async_get_entry(candidate_id)
            if candidate is not None and candidate.domain == DOMAIN:
                entry = candidate
                break
        if entry is None:
            raise ServiceValidationError(
                f"Device '{device.name or device_id}' is not a MT-VIKI matrix"
            )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            f"MT-VIKI config entry '{entry.title}' is not loaded"
        )
    return entry.runtime_data


async def _async_call_client(coro, action: str) -> None:
    """Await a client command, wrapping library errors in HomeAssistantError."""
    try:
        await coro
    except MTVikiConnectionError as err:
        raise HomeAssistantError(
            f"Cannot {action}: not connected to the MT-VIKI matrix ({err})"
        ) from err
    except MTVikiError as err:
        raise HomeAssistantError(f"Failed to {action}: {err}") from err


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services (once, at component setup)."""

    async def async_set_route(call: ServiceCall) -> None:
        coordinator = _async_get_coordinator(hass, call)
        input_port: int = call.data[ATTR_INPUT]
        outputs = call.data[ATTR_OUTPUTS]
        if input_port > coordinator.inputs:
            raise ServiceValidationError(
                f"Input {input_port} is out of range for this "
                f"{coordinator.inputs}x{coordinator.outputs} matrix"
            )
        if outputs == ALL_OUTPUTS:
            await _async_call_client(
                coordinator.client.async_switch_all(input_port),
                f"route input {input_port} to all outputs",
            )
            return
        if invalid := [port for port in outputs if port > coordinator.outputs]:
            raise ServiceValidationError(
                f"Output(s) {invalid} out of range for this "
                f"{coordinator.inputs}x{coordinator.outputs} matrix"
            )
        await _async_call_client(
            coordinator.client.async_switch(input_port, outputs),
            f"route input {input_port} to outputs {outputs}",
        )

    async def async_save_scene(call: ServiceCall) -> None:
        coordinator = _async_get_coordinator(hass, call)
        scene: int = call.data[ATTR_SCENE]
        await _async_call_client(
            coordinator.client.async_scene_save(scene), f"save scene {scene}"
        )

    async def async_recall_scene(call: ServiceCall) -> None:
        coordinator = _async_get_coordinator(hass, call)
        scene: int = call.data[ATTR_SCENE]
        # Goes through the coordinator (not the client directly) so the
        # current-scene sensor tracks recalls made via this service too.
        await _async_call_client(
            coordinator.async_recall_scene(scene), f"recall scene {scene}"
        )

    async def async_set_output_hdcp(call: ServiceCall) -> None:
        coordinator = _async_get_coordinator(hass, call)
        output: int = call.data[ATTR_OUTPUT]
        mode: int = call.data[ATTR_MODE]
        if output > coordinator.outputs:
            raise ServiceValidationError(
                f"Output {output} is out of range for this "
                f"{coordinator.inputs}x{coordinator.outputs} matrix"
            )
        await _async_call_client(
            coordinator.client.async_set_output_hdcp(output, mode),
            f"set HDCP mode {mode} on output {output}",
        )

    async def async_set_input_edid(call: ServiceCall) -> None:
        coordinator = _async_get_coordinator(hass, call)
        input_port: int = call.data[ATTR_INPUT]
        edid: int = call.data[ATTR_EDID]
        if input_port > coordinator.inputs:
            raise ServiceValidationError(
                f"Input {input_port} is out of range for this "
                f"{coordinator.inputs}x{coordinator.outputs} matrix"
            )
        await _async_call_client(
            coordinator.client.async_set_input_edid(input_port, edid),
            f"set EDID preset {edid} on input {input_port}",
        )

    async def async_locate(call: ServiceCall) -> None:
        coordinator = _async_get_coordinator(hass, call)
        await _async_call_client(
            coordinator.client.async_locate(
                count=call.data[ATTR_COUNT], interval=call.data[ATTR_INTERVAL]
            ),
            "locate the matrix",
        )

    hass.services.async_register(
        DOMAIN, SERVICE_SET_ROUTE, async_set_route, schema=SET_ROUTE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SAVE_SCENE, async_save_scene, schema=SCENE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RECALL_SCENE, async_recall_scene, schema=SCENE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_OUTPUT_HDCP,
        async_set_output_hdcp,
        schema=SET_OUTPUT_HDCP_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_INPUT_EDID,
        async_set_input_edid,
        schema=SET_INPUT_EDID_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LOCATE, async_locate, schema=LOCATE_SCHEMA
    )


async def _async_register_frontend_resources(hass: HomeAssistant) -> None:
    """Serve the crosspoint Lovelace card and register it as a frontend resource.

    Runs once from async_setup() (component-level, independent of how many
    config entries exist) rather than async_setup_entry(), so it happens
    exactly once no matter how many matrices are configured.

    The static path only needs `http`, which is a hard manifest dependency
    (async_setup() cannot run before it), so hass.http is guaranteed to
    exist here. Registering it as a frontend *resource* additionally needs
    `frontend` itself to have already run its own async_setup (it is what
    creates the hass.data bucket add_extra_js_url() writes into) -- but
    `frontend` is deliberately NOT a hard dependency, so that a headless
    installation that never loads the UI at all is not forced to load it
    just for this integration. Instead, async_when_setup() defers the call
    until `frontend` finishes setting up: it fires immediately if frontend
    is already up (the common case, since frontend normally loads early),
    or later if not, and does nothing at all if frontend is never loaded.
    """
    if hass.data.get(_DATA_FRONTEND_RESOURCE_REGISTERED):
        return
    hass.data[_DATA_FRONTEND_RESOURCE_REGISTERED] = True

    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_PATH, str(_CARD_JS_PATH), cache_headers=True)]
    )

    async def _async_add_resource(hass: HomeAssistant, _component: str) -> None:
        add_extra_js_url(hass, CARD_URL_PATH)

    async_when_setup(hass, "frontend", _async_add_resource)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the MT-VIKI HDMI Matrix component (register services once)."""
    _async_register_services(hass)
    await _async_register_frontend_resources(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MTVikiConfigEntry) -> bool:
    """Set up a MT-VIKI HDMI matrix from a config entry."""
    host: str = entry.data[CONF_HOST]
    port: int = entry.data[CONF_PORT]
    inputs, outputs = MATRIX_SIZES.get(
        entry.data.get(CONF_MATRIX_SIZE, DEFAULT_MATRIX_SIZE),
        MATRIX_SIZES[DEFAULT_MATRIX_SIZE],
    )
    client = MTVikiClient(host, port, inputs=inputs, outputs=outputs)

    # Validate connectivity with a single attempt so a dead host surfaces as
    # ConfigEntryNotReady (with retry) instead of silently reconnecting forever.
    try:
        await client.async_connect()
        await client.async_refresh()
    except MTVikiError as err:
        await client.stop()
        raise ConfigEntryNotReady(
            f"Cannot connect to MT-VIKI matrix at {host}:{port}: {err}"
        ) from err

    # Create the coordinator (registers the push callback) before starting the
    # keep-alive task so no state change is missed, then seed initial data.
    coordinator = MTVikiCoordinator(hass, entry, client)
    await client.start()
    coordinator.async_set_updated_data(client.state)

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Reload on options change so polling settings take effect.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: MTVikiConfigEntry) -> None:
    """Reload the config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MTVikiConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator = entry.runtime_data
        coordinator.client.set_state_callback(None)
        await coordinator.client.stop()
    return unload_ok
