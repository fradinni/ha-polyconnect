"""Switch platform for Polyconnect — heat pump power on/off.

One switch per discovered heat pump.
"""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PolyconnectEntity
from .coordinator import PolyconnectCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PolyconnectCoordinator = entry.runtime_data
    async_add_entities(
        PolyconnectPowerSwitch(coordinator, pump["id"], pump["name"])
        for pump in coordinator.pumps
    )


class PolyconnectPowerSwitch(PolyconnectEntity, SwitchEntity):
    """Switch that turns this pump on or off (the main power button)."""

    _attr_name = "Power"
    _attr_icon = "mdi:power"

    def __init__(
        self,
        coordinator: PolyconnectCoordinator,
        pump_id: str,
        pump_name: str,
    ) -> None:
        super().__init__(coordinator, pump_id, pump_name, "power_switch")

    @property
    def is_on(self) -> bool | None:
        data = self._pump_data
        if not data:
            return None
        val = data.get("heatPumpActive")
        if val is None:
            return None
        return bool(val)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.api.turn_on(self._pump_id)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.api.turn_off(self._pump_id)
        await self.coordinator.async_request_refresh()
