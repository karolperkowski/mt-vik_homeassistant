"""Media player platform for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MTVikiConfigEntry, MTVikiCoordinator
from .entity import MTVikiEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MTVikiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one media player per output (disabled by default)."""
    coordinator = entry.runtime_data
    async_add_entities(
        MTVikiOutputMediaPlayer(coordinator, output)
        for output in range(1, coordinator.outputs + 1)
    )


class MTVikiOutputMediaPlayer(MTVikiEntity, MediaPlayerEntity):
    """Source-selection-only media player view of one matrix output.

    Useful for dashboards/voice assistants that understand source selection;
    disabled by default since the select entities cover the same function.
    """

    _attr_translation_key = "output_player"
    _attr_entity_registry_enabled_default = False
    _attr_supported_features = MediaPlayerEntityFeature.SELECT_SOURCE

    def __init__(self, coordinator: MTVikiCoordinator, output: int) -> None:
        """Initialize the media player for one output."""
        super().__init__(coordinator)
        self._output = output
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_output_{output}_player"
        )
        self._attr_translation_placeholders = {"output_number": str(output)}
        self._attr_source_list = coordinator.input_names()

    @property
    def state(self) -> MediaPlayerState:
        """The matrix has no play state; report IDLE while connected."""
        return MediaPlayerState.IDLE

    @property
    def source(self) -> str | None:
        """Return the (named) input currently routed to this output."""
        route = self.coordinator.data.routes.get(self._output)
        if route is None or not 1 <= route <= self.coordinator.inputs:
            return None
        return self.coordinator.input_name(route)

    async def async_select_source(self, source: str) -> None:
        """Route the selected input to this output."""
        input_port = self.coordinator.input_port_for_name(source)
        if input_port is None:
            raise HomeAssistantError(f"Unknown source: {source}")
        await self._async_client_call(
            self.coordinator.client.async_switch(input_port, self._output),
            f"switch output {self._output} to input {input_port}",
        )
