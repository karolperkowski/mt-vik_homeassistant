"""Sensor platform for the MT-VIKI HDMI Matrix integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import MatrixState
from .coordinator import MTVikiConfigEntry, MTVikiCoordinator
from .entity import MTVikiEntity


@dataclass(frozen=True, kw_only=True)
class MTVikiSensorDescription(SensorEntityDescription):
    """Describes a MT-VIKI diagnostic sensor."""

    value_fn: Callable[[MatrixState], str | None]


SENSORS: tuple[MTVikiSensorDescription, ...] = (
    MTVikiSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.firmware,
    ),
    MTVikiSensorDescription(
        key="model_id",
        translation_key="model_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.model,
    ),
    MTVikiSensorDescription(
        key="device_ip",
        translation_key="device_ip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.ip,
    ),
    MTVikiSensorDescription(
        key="ip_mask",
        translation_key="ip_mask",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.ip_mask,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MTVikiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor entities."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        MTVikiDiagnosticSensor(coordinator, description) for description in SENSORS
    ]
    entities.append(MTVikiCurrentSceneSensor(coordinator))
    entities.extend(
        MTVikiInputHdcpSensor(coordinator, input_port)
        for input_port in range(1, coordinator.inputs + 1)
    )
    async_add_entities(entities)


class MTVikiDiagnosticSensor(MTVikiEntity, SensorEntity):
    """A diagnostic sensor exposing a MatrixState field."""

    entity_description: MTVikiSensorDescription

    def __init__(
        self, coordinator: MTVikiCoordinator, description: MTVikiSensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> str | None:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)


class MTVikiCurrentSceneSensor(MTVikiEntity, SensorEntity):
    """Name of the most recently recalled scene, if it's still in effect.

    HONEST LIMITATION: the MT-VIKI protocol has no way to read back what a
    scene *contains*, and no "current scene" query at all -- only a
    fire-and-forget recall command (``SceneCall``). This sensor therefore
    cannot report "the scene the device currently considers active"; it can
    only report "the scene we last told the device to recall, as long as the
    routing table has not changed since". The coordinator clears the tracked
    scene (see ``_async_check_scene_divergence``) the moment routing diverges
    from what that recall produced -- from this integration, the front
    panel, the IR remote, or a service call -- at which point this sensor
    falls back to "none". It also reads "none" before any scene has ever
    been recalled through Home Assistant, even if the device happens to be
    sitting in a state that matches one of its stored scenes; we simply have
    no way to know that.
    """

    _attr_translation_key = "current_scene"

    def __init__(self, coordinator: MTVikiCoordinator) -> None:
        """Initialize the current-scene sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_current_scene"

    @property
    def native_value(self) -> str:
        """Return the last-recalled scene's name, or "none"."""
        return self.coordinator.current_scene_name or "none"


class MTVikiInputHdcpSensor(MTVikiEntity, SensorEntity):
    """Raw HDCP status value reported for one input.

    The value semantics of InPortHDCPS are undefined in the vendor docs, so
    the raw integer is exposed as-is. Disabled by default.
    """

    _attr_translation_key = "input_hdcp"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: MTVikiCoordinator, input_port: int) -> None:
        """Initialize the per-input HDCP sensor."""
        super().__init__(coordinator)
        self._input = input_port
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_input_{input_port}_hdcp"
        )
        self._attr_translation_placeholders = {"input_number": str(input_port)}

    @property
    def native_value(self) -> int | None:
        """Return the raw HDCP status value."""
        return self.coordinator.data.input_hdcp.get(self._input)
