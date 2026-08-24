"""Button platform for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SCENE_COUNT, SCENES_ENABLED_BY_DEFAULT
from .coordinator import MTVikiConfigEntry, MTVikiCoordinator
from .entity import MTVikiEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MTVikiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button entities."""
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [MTVikiLocateButton(coordinator)]
    entities.extend(
        MTVikiSceneButton(coordinator, scene) for scene in range(1, SCENE_COUNT + 1)
    )
    async_add_entities(entities)


class MTVikiLocateButton(MTVikiEntity, ButtonEntity):
    """Beeps the matrix a few times so it can be found in the rack."""

    _attr_device_class = ButtonDeviceClass.IDENTIFY
    _attr_translation_key = "locate"

    def __init__(self, coordinator: MTVikiCoordinator) -> None:
        """Initialize the locate button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_locate"

    async def async_press(self) -> None:
        """Fire the locate beep pattern (client handles BeepEn gating)."""
        await self._async_client_call(
            self.coordinator.client.async_locate(), "locate the matrix"
        )


class MTVikiSceneButton(MTVikiEntity, ButtonEntity):
    """Recalls a stored routing scene."""

    _attr_translation_key = "scene"

    def __init__(self, coordinator: MTVikiCoordinator, scene: int) -> None:
        """Initialize the scene recall button."""
        super().__init__(coordinator)
        self._scene = scene
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_scene_{scene}"
        self._attr_translation_placeholders = {"scene_number": str(scene)}
        # Scenes 9-16 exist but are disabled by default to avoid clutter.
        self._attr_entity_registry_enabled_default = scene <= SCENES_ENABLED_BY_DEFAULT

    async def async_press(self) -> None:
        """Recall the scene; routing updates arrive via the SWS echo."""
        await self._async_client_call(
            self.coordinator.client.async_scene_recall(self._scene),
            f"recall scene {self._scene}",
        )
