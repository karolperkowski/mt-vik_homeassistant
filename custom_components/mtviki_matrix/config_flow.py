"""Config flow for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

import logging
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

from .api import MATRIX_SIZES, MTVikiClient, MTVikiConnectionError, MTVikiError
from .const import (
    CONF_ENABLE_POLLING,
    CONF_MATRIX_SIZE,
    CONF_POLL_INTERVAL,
    DEFAULT_ENABLE_POLLING,
    DEFAULT_MATRIX_SIZE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
    MAX_POLL_INTERVAL,
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
        try:
            await client.async_full_refresh()
        except MTVikiError:
            pass
        model = client.state.model
    finally:
        await client.stop()
    return f"{model or 'MT-VIKI HDMI Matrix'} ({data[CONF_HOST]})"


class MTVikiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for the MT-VIKI HDMI Matrix."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial (and only) user step."""
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
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA, user_input
            ),
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
