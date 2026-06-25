"""Sensor platform for Polyconnect — temperature, power, mode, and status sensors."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfTemperature,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PolyconnectEntity
from .coordinator import PolyconnectCoordinator


@dataclass(frozen=True, kw_only=True)
class PolyconnectSensorDescription(SensorEntityDescription):
    """Extended description with the coordinator data dict key."""
    data_key: str = ""


SENSORS: tuple[PolyconnectSensorDescription, ...] = (
    # ── Temperature sensors ──────────────────────────────────────────────────
    PolyconnectSensorDescription(
        key="water_temperature",
        data_key="waterTemperature",
        name="Water Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    PolyconnectSensorDescription(
        key="outside_temperature",
        data_key="outsideTemperature",
        name="Outside Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    PolyconnectSensorDescription(
        key="setpoint_temperature",
        data_key="setpointTemperature",
        name="Setpoint Temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    # ── Power / efficiency sensors ───────────────────────────────────────────
    PolyconnectSensorDescription(
        key="power_consumption",
        data_key="powerConsumptionW",
        name="Power Consumption",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    PolyconnectSensorDescription(
        key="cop",
        data_key="cop",
        name="Coefficient of Performance",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=None,
        icon="mdi:heat-pump",
    ),
    # ── Mode / status sensors ────────────────────────────────────────────────
    PolyconnectSensorDescription(
        key="operating_mode",
        data_key="operatingMode",
        name="Operating Mode",
        icon="mdi:heat-pump-outline",
        native_unit_of_measurement=None,
    ),
    PolyconnectSensorDescription(
        key="regulation_mode",
        data_key="regulationMode",
        name="Regulation Mode",
        icon="mdi:tune",
        native_unit_of_measurement=None,
    ),
    # ── Alarm sensor ─────────────────────────────────────────────────────────
    PolyconnectSensorDescription(
        key="alarm_message",
        data_key="alarmMessage",
        name="Alarm Message",
        icon="mdi:alert-circle-outline",
        native_unit_of_measurement=None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Polyconnect sensor entities."""
    coordinator: PolyconnectCoordinator = entry.runtime_data
    async_add_entities(
        PolyconnectSensor(coordinator, description) for description in SENSORS
    )


class PolyconnectSensor(PolyconnectEntity, SensorEntity):
    """A single Polyconnect sensor (temperature, power, mode, etc.)."""

    def __init__(
        self,
        coordinator: PolyconnectCoordinator,
        description: PolyconnectSensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self.entity_description.data_key)
