"""Switch platform for Polyconnect — heat pump power on/off.

In the Polyconnect app the main power button (ON/OFF) controls whether the
heat pump is active. This switch maps directly to that button via the
bridge /on and /off endpoints.

The climate entity also exposes TURN_ON / TURN_OFF for the same button;
having a dedicated switch makes it easy to toggle power from dashboards
and automations without going through the climate card.
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
    async_add_entities([PolyconnectPowerSwitch(coordinator)])


class PolyconnectPowerSwitch(PolyconnectEntity, SwitchEntity):
    """Switch that turns the heat pump on or off (the main power button)."""

    _attr_name = "Power"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator: PolyconnectCoordinator) -> None:
        super().__init__(coordinator, "power_switch")

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get("heatPumpActive")
        if val is None:
            return None
        return bool(val)

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.api.turn_on()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.api.turn_off()
        await self.coordinator.async_request_refresh()
