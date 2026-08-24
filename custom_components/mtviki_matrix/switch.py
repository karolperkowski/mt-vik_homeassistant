"""Switch platform for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import MatrixState, MTVikiClient
from .coordinator import MTVikiConfigEntry, MTVikiCoordinator
from .entity import MTVikiEntity


@dataclass(frozen=True, kw_only=True)
class MTVikiSwitchDescription(SwitchEntityDescription):
    """Describes a MT-VIKI switch entity."""

    value_fn: Callable[[MatrixState], bool | None]
    set_fn: Callable[[MTVikiClient, bool], Coroutine[Any, Any, None]]


SWITCHES: tuple[MTVikiSwitchDescription, ...] = (
    MTVikiSwitchDescription(
        key="keylock",
        translation_key="keylock",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.keylock,
        set_fn=lambda client, on: client.async_set_keylock(on),
    ),
    # Front-panel key-click beep enable.
    MTVikiSwitchDescription(
        key="beep",
        translation_key="beep",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda state: state.beep_en,
        set_fn=lambda client, on: client.async_set_beep(on),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MTVikiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the switch entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        MTVikiSwitch(coordinator, description) for description in SWITCHES
    )


class MTVikiSwitch(MTVikiEntity, SwitchEntity):
    """A MT-VIKI matrix switch entity."""

    entity_description: MTVikiSwitchDescription

    def __init__(
        self, coordinator: MTVikiCoordinator, description: MTVikiSwitchDescription
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return the switch state (None until the device has answered)."""
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on; state updates via the device echo."""
        await self._async_client_call(
            self.entity_description.set_fn(self.coordinator.client, True),
            f"turn on {self.entity_description.key}",
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off; state updates via the device echo."""
        await self._async_client_call(
            self.entity_description.set_fn(self.coordinator.client, False),
            f"turn off {self.entity_description.key}",
        )
