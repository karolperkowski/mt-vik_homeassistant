"""Text platform for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.text import TextEntity, TextEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import MatrixState, MTVikiClient
from .coordinator import MTVikiConfigEntry, MTVikiCoordinator
from .entity import MTVikiEntity


@dataclass(frozen=True, kw_only=True)
class MTVikiTextDescription(TextEntityDescription):
    """Describes a MT-VIKI text entity."""

    value_fn: Callable[[MatrixState], str | None]
    set_fn: Callable[[MTVikiClient, str], Coroutine[Any, Any, None]]


# The device does not document maximum string lengths for these commands
# (spec just shows "xxxxx"), so no native_max is enforced beyond HA's default.
TEXTS: tuple[MTVikiTextDescription, ...] = (
    MTVikiTextDescription(
        key="title_label",
        translation_key="title_label",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.title,
        set_fn=lambda client, value: client.async_set_title(value),
    ),
    # LCD readout line 1.
    MTVikiTextDescription(
        key="service_type",
        translation_key="service_type",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.service_type,
        set_fn=lambda client, value: client.async_set_service_type(value),
    ),
    # LCD readout line 2.
    MTVikiTextDescription(
        key="service_number",
        translation_key="service_number",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=lambda state: state.service_num,
        set_fn=lambda client, value: client.async_set_service_num(value),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MTVikiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the text entities."""
    coordinator = entry.runtime_data
    async_add_entities(MTVikiText(coordinator, description) for description in TEXTS)


class MTVikiText(MTVikiEntity, TextEntity):
    """A MT-VIKI matrix text entity (device labels / LCD lines)."""

    entity_description: MTVikiTextDescription

    def __init__(
        self, coordinator: MTVikiCoordinator, description: MTVikiTextDescription
    ) -> None:
        """Initialize the text entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> str | None:
        """Return the current value (None until the device has answered)."""
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_set_value(self, value: str) -> None:
        """Set the value; state updates via the device echo."""
        await self._async_client_call(
            self.entity_description.set_fn(self.coordinator.client, value),
            f"set {self.entity_description.key}",
        )
