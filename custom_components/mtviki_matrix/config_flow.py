"""Config flow for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import socket
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import (
    MATRIX_SIZES,
    DiscoveredMatrix,
    MTVikiClient,
    MTVikiConnectionError,
    MTVikiError,
    async_discover,
)
from .const import (
    CONF_DEVICE,
    CONF_ENABLE_POLLING,
    CONF_MATRIX_SIZE,
    CONF_NETWORK,
    CONF_POLL_INTERVAL,
    DEFAULT_ENABLE_POLLING,
    DEFAULT_MATRIX_SIZE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_NETWORK,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MAX_SCAN_HOSTS,
    MIN_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Required(CONF_MATRIX_SIZE, default=DEFAULT_MATRIX_SIZE): SelectSelector(
            SelectSelectorConfig(
                options=list(MATRIX_SIZES),
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="matrix_size",
            )
        ),
    }
)


STEP_SCAN_DATA_SCHEMA = vol.Schema({vol.Required(CONF_NETWORK): str})


def _guess_local_subnet() -> str:
    """Best-effort guess of the local /24, to pre-fill the scan step.

    Uses the classic UDP-connect trick: connecting a UDP socket never sends a
    packet by itself, it just asks the OS to pick the local address it would
    route through to reach the destination. 8.8.8.8 is only used as a
    plausible off-link destination for that route lookup -- no traffic is
    actually sent to it. Falls back to a common default when there is no
    route at all (e.g. no network interface up). Blocking, so callers must
    run this in an executor job.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
        return str(ipaddress.ip_network(f"{local_ip}/24", strict=False))
    except OSError:
        return DEFAULT_SCAN_NETWORK


def _label_for_discovered(device: DiscoveredMatrix) -> str:
    """Human-readable option label: ``host:port — model (NxM)``."""
    model = device.model or "unknown model"
    inputs = device.inputs if device.inputs else "?"
    outputs = device.outputs if device.outputs else "?"
    return f"{device.host}:{device.port} — {model} ({inputs}x{outputs})"


async def _async_validate_input(hass: HomeAssistant, data: dict[str, Any]) -> str:
    """Validate that we can talk to the matrix; return a suggested title.

    Raises MTVikiConnectionError if the device is unreachable.
    """
    inputs, outputs = MATRIX_SIZES[data[CONF_MATRIX_SIZE]]
    client = MTVikiClient(
        data[CONF_HOST], data[CONF_PORT], inputs=inputs, outputs=outputs
    )
    try:
        # Single connection attempt, then GetSW via async_refresh.
        await client.async_connect()
        await client.async_refresh()
        # Best effort: PING / GetMCUFWVer (via full refresh, which tolerates
        # per-command timeouts) to get a model string for the entry title.
        with contextlib.suppress(MTVikiError):
            await client.async_full_refresh()
        model = client.state.model
    finally:
        await client.stop()
    return f"{model or 'MT-VIKI HDMI Matrix'} ({data[CONF_HOST]})"


class MTVikiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the MT-VIKI HDMI Matrix."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise per-flow scratch state (nothing here does I/O)."""
        self._discovered: list[DiscoveredMatrix] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Entry point: let the user choose manual entry or a network scan."""
        return self.async_show_menu(step_id="user", menu_options=["manual", "scan"])

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle manual host/port/matrix-size entry (the original flow)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # No MAC/serial is available over the protocol; host:port is the
            # best stable unique id we have.
            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}",
                raise_on_progress=False,
            )
            self._abort_if_unique_id_configured()
            try:
                title = await _async_validate_input(self.hass, user_input)
            except MTVikiConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception validating MT-VIKI matrix")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Opt-in local-subnet scan: ask for a CIDR, then probe every host in it.

        Never runs unless the user explicitly picks this menu option. The
        subnet field is pre-filled with a best-effort guess of the local /24
        (computed off the event loop) but is always editable.
        """
        errors: dict[str, str] = {}
        if user_input is None:
            suggested = {
                CONF_NETWORK: await self.hass.async_add_executor_job(
                    _guess_local_subnet
                )
            }
        else:
            suggested = user_input
            try:
                network = ipaddress.ip_network(user_input[CONF_NETWORK], strict=False)
            except ValueError:
                errors["base"] = "invalid_network"
            else:
                hosts = [str(ip) for ip in network.hosts()]
                if len(hosts) > MAX_SCAN_HOSTS:
                    errors["base"] = "network_too_large"
                else:
                    self._discovered = await async_discover(hosts, DEFAULT_PORT)
                    if not self._discovered:
                        errors["base"] = "no_devices_found"
                    else:
                        return await self.async_step_pick()
        return self.async_show_form(
            step_id="scan",
            data_schema=self.add_suggested_values_to_schema(
                STEP_SCAN_DATA_SCHEMA, suggested
            ),
            errors=errors,
        )

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick one of the devices found by :meth:`async_step_scan`."""
        errors: dict[str, str] = {}
        options = {f"{d.host}:{d.port}": d for d in self._discovered}
        default_size = DEFAULT_MATRIX_SIZE
        if self._discovered:
            first = self._discovered[0]
            guessed = f"{first.inputs}x{first.outputs}"
            if first.inputs and first.outputs and guessed in MATRIX_SIZES:
                default_size = guessed

        if user_input is not None:
            device = options.get(user_input[CONF_DEVICE])
            if device is None:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(
                    f"{device.host}:{device.port}", raise_on_progress=False
                )
                self._abort_if_unique_id_configured()
                data = {
                    CONF_HOST: device.host,
                    CONF_PORT: device.port,
                    CONF_MATRIX_SIZE: user_input[CONF_MATRIX_SIZE],
                }
                try:
                    title = await _async_validate_input(self.hass, data)
                except MTVikiConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected exception validating MT-VIKI matrix")
                    errors["base"] = "unknown"
                else:
                    return self.async_create_entry(title=title, data=data)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_DEVICE, default=next(iter(options), None)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": key, "label": _label_for_discovered(device)}
                            for key, device in options.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_MATRIX_SIZE, default=default_size): SelectSelector(
                    SelectSelectorConfig(
                        options=list(MATRIX_SIZES),
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="matrix_size",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="pick",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MTVikiOptionsFlow:
        """Create the options flow."""
        return MTVikiOptionsFlow()


class MTVikiOptionsFlow(OptionsFlow):
    """Handle the options flow (polling settings)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            user_input[CONF_POLL_INTERVAL] = int(user_input[CONF_POLL_INTERVAL])
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_ENABLE_POLLING,
                    default=options.get(CONF_ENABLE_POLLING, DEFAULT_ENABLE_POLLING),
                ): BooleanSelector(),
                vol.Required(
                    CONF_POLL_INTERVAL,
                    default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_POLL_INTERVAL,
                        max=MAX_POLL_INTERVAL,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
