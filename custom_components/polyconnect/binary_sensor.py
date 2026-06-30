"""Binary sensor platform for Polyconnect — running states and alarm.

One binary sensor of each kind per discovered heat pump.
"""
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
        key="fan_running",
        data_key="fanRunning",
        name="Fan",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:fan",
    ),
    PolyconnectBinarySensorDescription(
        key="filtration_running",
        data_key="filtrationRunning",
        name="Filtration Pump",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:pump",
    ),
    PolyconnectBinarySensorDescription(
        key="defrost_active",
        data_key="defrostActive",
        name="Defrost",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:snowflake-melt",
    ),
    PolyconnectBinarySensorDescription(
        key="alarm_active",
        data_key="alarmActive",
        name="Alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:alert-circle",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PolyconnectCoordinator = entry.runtime_data
    async_add_entities(
        PolyconnectBinarySensor(coordinator, pump["id"], pump["name"], desc)
        for pump in coordinator.pumps
        for desc in BINARY_SENSORS
    )


class PolyconnectBinarySensor(PolyconnectEntity, BinarySensorEntity):
    """A single Polyconnect binary sensor."""

    def __init__(
        self,
        coordinator: PolyconnectCoordinator,
        pump_id: str,
        pump_name: str,
        description: PolyconnectBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, pump_id, pump_name, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        data = self._pump_data
        if not data:
            return None
        return bool(data.get(self.entity_description.data_key))
