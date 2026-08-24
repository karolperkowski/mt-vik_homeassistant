"""Base entity for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from homeassistant.const import CONF_HOST
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MTVikiConnectionError, MTVikiError
from .const import DEFAULT_MODEL, DOMAIN, MANUFACTURER
from .coordinator import MTVikiCoordinator


class MTVikiEntity(CoordinatorEntity[MTVikiCoordinator]):
    """Base entity: all entities belong to the single matrix device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MTVikiCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        state = coordinator.data
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model=(state.model if state else None) or DEFAULT_MODEL,
            sw_version=state.firmware if state else None,
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )

    @property
    def available(self) -> bool:
        """Entity is available only while the TCP link to the matrix is up."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.connected
        )

    async def _async_client_call(
        self, coro: Coroutine[Any, Any, Any], action: str
    ) -> None:
        """Await a client command, wrapping library errors in HomeAssistantError."""
        try:
            await coro
        except MTVikiConnectionError as err:
            raise HomeAssistantError(
                f"Cannot {action}: not connected to the MT-VIKI matrix ({err})"
            ) from err
        except MTVikiError as err:
            raise HomeAssistantError(f"Failed to {action}: {err}") from err
