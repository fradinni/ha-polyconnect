"""Binary sensor platform for Polyconnect — running states and alarm."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PolyconnectEntity
from .coordinator import PolyconnectCoordinator


@dataclass(frozen=True, kw_only=True)
class PolyconnectBinarySensorDescription(BinarySensorEntityDescription):
    """Extended description with coordinator data key."""
    data_key: str = ""


BINARY_SENSORS: tuple[PolyconnectBinarySensorDescription, ...] = (
    PolyconnectBinarySensorDescription(
        key="compressor_running",
        data_key="compressorRunning",
        name="Compressor",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:heat-pump",
    ),
    PolyconnectBinarySensorDescription(
        key="alarm_active",
        data_key="alarmActive",
        name="Alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:alert-circle",
    ),
    PolyconnectBinarySensorDescription(
        key="filtration_running",
        data_key="filtrationRunning",
        name="Filtration Pump",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:pump",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PolyconnectCoordinator = entry.runtime_data
    async_add_entities(
        PolyconnectBinarySensor(coordinator, desc) for desc in BINARY_SENSORS
    )


class PolyconnectBinarySensor(PolyconnectEntity, BinarySensorEntity):
    """A single Polyconnect binary sensor."""

    def __init__(
        self,
        coordinator: PolyconnectCoordinator,
        description: PolyconnectBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return bool(self.coordinator.data.get(self.entity_description.data_key))
