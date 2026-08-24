"""Select platform for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EDID_PRESET_COUNT, HDCP_MODES
from .coordinator import MTVikiConfigEntry, MTVikiCoordinator
from .entity import MTVikiEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MTVikiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the select entities."""
    coordinator = entry.runtime_data
    entities: list[SelectEntity] = [
        MTVikiOutputRouteSelect(coordinator, output)
        for output in range(1, coordinator.outputs + 1)
    ]
    entities.extend(
        MTVikiOutputHdcpSelect(coordinator, output)
        for output in range(1, coordinator.outputs + 1)
    )
    entities.extend(
        MTVikiInputEdidSelect(coordinator, input_port)
        for input_port in range(1, coordinator.inputs + 1)
    )
    async_add_entities(entities)


class MTVikiOutputRouteSelect(MTVikiEntity, SelectEntity):
    """Which input is routed to a given output.

    Options are the user-configured input names (options flow "Name your
    inputs" step), defaulting to "Input N"; the option shown maps to the
    same port number either way, so writes are unaffected by naming.
    """

    _attr_translation_key = "output_route"

    def __init__(self, coordinator: MTVikiCoordinator, output: int) -> None:
        """Initialize the routing select for one output."""
        super().__init__(coordinator)
        self._output = output
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_output_{output}_route"
        )
        self._attr_translation_placeholders = {"output_number": str(output)}
        self._attr_options = coordinator.input_names()

    @property
    def current_option(self) -> str | None:
        """Return the (named) input currently routed to this output."""
        route = self.coordinator.data.routes.get(self._output)
        if route is None or not 1 <= route <= self.coordinator.inputs:
            return None
        return self.coordinator.input_name(route)

    async def async_select_option(self, option: str) -> None:
        """Route the selected input to this output."""
        input_port = self.coordinator.input_port_for_name(option)
        if input_port is None:
            raise HomeAssistantError(f"Unknown input option: {option}")
        await self._async_client_call(
            self.coordinator.client.async_switch(input_port, self._output),
            f"switch output {self._output} to input {input_port}",
        )
        # No optimistic update: the device echoes an SWS line which the client
        # parses and pushes back through the coordinator.


class MTVikiOutputHdcpSelect(MTVikiEntity, SelectEntity):
    """HDCP mode of a given output.

    NOTE: the vendor documentation self-contradicts on SetOutPortHDCP value
    semantics. We adopt 0=off, 1=HDCP 1.4, 2=HDCP 2.0, 3=HDCP 2.2 (matching
    the OutPortHDCPS status legend); the alternative reading
    (0=disable, 1=enable, 2=follow input) is possible. Unverified on hardware.
    """

    _attr_translation_key = "output_hdcp"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = HDCP_MODES

    def __init__(self, coordinator: MTVikiCoordinator, output: int) -> None:
        """Initialize the HDCP select for one output."""
        super().__init__(coordinator)
        self._output = output
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_output_{output}_hdcp"
        )
        self._attr_translation_placeholders = {"output_number": str(output)}

    @property
    def current_option(self) -> str | None:
        """Return the current HDCP mode; None for unknown raw values."""
        raw = self.coordinator.data.output_hdcp.get(self._output)
        if raw is None or not 0 <= raw < len(HDCP_MODES):
            return None
        return HDCP_MODES[raw]

    async def async_select_option(self, option: str) -> None:
        """Set the HDCP mode for this output."""
        mode = HDCP_MODES.index(option)
        await self._async_client_call(
            self.coordinator.client.async_set_output_hdcp(self._output, mode),
            f"set HDCP mode {option} on output {self._output}",
        )


class MTVikiInputEdidSelect(MTVikiEntity, SelectEntity):
    """EDID preset of a given input.

    NOTE: valid SetEDID <sel> values are unverified (vendor docs do not
    enumerate them); presets 1-16 are exposed. The device echoes
    "InPortEdid <in> <sel>": if the client exposes a parsed map we reflect it,
    otherwise the entity is optimistic (assumed state, last value we set).
    """

    _attr_translation_key = "input_edid"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_assumed_state = True

    def __init__(self, coordinator: MTVikiCoordinator, input_port: int) -> None:
        """Initialize the EDID select for one input."""
        super().__init__(coordinator)
        self._attr_options = [str(preset) for preset in range(1, EDID_PRESET_COUNT + 1)]
        self._input = input_port
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_input_{input_port}_edid"
        )
        self._attr_translation_placeholders = {"input_number": str(input_port)}
        self._optimistic_edid: int | None = None

    @property
    def current_option(self) -> str | None:
        """Return the EDID preset, preferring device-reported state."""
        # MatrixState is not guaranteed to carry an EDID map in the build
        # contract; tolerate its absence via getattr.
        reported = getattr(self.coordinator.data, "input_edid", None)
        if isinstance(reported, dict):
            value = reported.get(self._input)
            if value is not None:
                return str(value) if str(value) in self.options else None
        if self._optimistic_edid is not None:
            return str(self._optimistic_edid)
        return None

    async def async_select_option(self, option: str) -> None:
        """Set the EDID preset for this input."""
        edid = int(option)
        await self._async_client_call(
            self.coordinator.client.async_set_input_edid(self._input, edid),
            f"set EDID preset {edid} on input {self._input}",
        )
        self._optimistic_edid = edid
        self.async_write_ha_state()
