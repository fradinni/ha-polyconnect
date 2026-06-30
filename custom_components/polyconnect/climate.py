"""Climate platform for Polyconnect — one thermostat entity per heat pump.

HVAC modes (shown in HA as the main mode selector):
    heat  → Chauffage   (heating)
    cool  → Climatisation  (cooling / "Froid" in the app)
    auto  → Auto           (automatic)

Preset modes (regulation sub-mode, layered on top of the HVAC mode):
    Eco / Smart / Boost.
"""
from __future__ import annotations

import asyncio

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import PolyconnectEntity
from .coordinator import PolyconnectCoordinator
from .const import (
    POLYCONNECT_TO_HA_MODE,
    HA_TO_POLYCONNECT_MODE,
    REGULATION_MODES,
)

# Only real operating modes — no OFF (handled by TURN_ON/TURN_OFF features)
HA_HVAC_MODES = [HVACMode.HEAT, HVACMode.COOL, HVACMode.AUTO]

# How long to wait after a mode change before polling the bridge for confirmation.
# The Blazor app re-renders asynchronously; reading too early returns stale data.
_MODE_REFRESH_DELAY = 8.0   # seconds


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PolyconnectCoordinator = entry.runtime_data
    async_add_entities(
        PolyconnectClimate(coordinator, pump["id"], pump["name"])
        for pump in coordinator.pumps
    )


class PolyconnectClimate(PolyconnectEntity, ClimateEntity):
    """Heat pump thermostat — controls setpoint, operating mode, and regulation preset."""

    _attr_name = "Heat Pump"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = 8.0
    _attr_max_temp = 32.0
    _attr_hvac_modes = HA_HVAC_MODES
    _attr_preset_modes = REGULATION_MODES   # Eco, Smart, Boost only
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
    )

    def __init__(
        self,
        coordinator: PolyconnectCoordinator,
        pump_id: str,
        pump_name: str,
    ) -> None:
        super().__init__(coordinator, pump_id, pump_name, "climate")

    @property
    def current_temperature(self) -> float | None:
        data = self._pump_data
        return data.get("waterTemperature") if data else None

    @property
    def target_temperature(self) -> float | None:
        data = self._pump_data
        return data.get("setpointTemperature") if data else None

    @property
    def hvac_mode(self) -> HVACMode:
        data = self._pump_data
        if not data:
            return HVACMode.AUTO
        raw_mode = data.get("operatingMode", "")
        ha_mode = POLYCONNECT_TO_HA_MODE.get(raw_mode, "auto")
        if ha_mode == "off":
            ha_mode = "auto"  # off is not a selectable mode
        try:
            return HVACMode(ha_mode)
        except ValueError:
            return HVACMode.AUTO

    @property
    def preset_mode(self) -> str | None:
        """Return the active regulation mode (Eco/Smart/Boost) or None."""
        data = self._pump_data
        if not data:
            return None
        reg = data.get("regulationMode")
        if reg and reg in REGULATION_MODES:
            return reg
        return None

    def _optimistic_update(self, key: str, value) -> None:
        """Mutate the cached status for this pump and push to HA immediately."""
        data = self._pump_data
        if data is not None:
            data[key] = value
            self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get("temperature")
        if temp is not None:
            await self.coordinator.api.set_setpoint(self._pump_id, float(temp))
            await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        polyconnect_mode = HA_TO_POLYCONNECT_MODE.get(hvac_mode.value, "Automatique")
        self._optimistic_update("operatingMode", {
            "heat": "Chauffage",
            "cool": "Froid",
            "auto": "Automatique",
        }.get(hvac_mode.value, "Automatique"))
        await self.coordinator.api.set_mode(self._pump_id, polyconnect_mode)
        await asyncio.sleep(_MODE_REFRESH_DELAY)
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in REGULATION_MODES:
            return
        self._optimistic_update("regulationMode", preset_mode)
        await self.coordinator.api.set_mode(self._pump_id, preset_mode)
        await asyncio.sleep(_MODE_REFRESH_DELAY)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        self._optimistic_update("heatPumpActive", True)
        await self.coordinator.api.turn_on(self._pump_id)
        await asyncio.sleep(3.0)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        self._optimistic_update("heatPumpActive", False)
        await self.coordinator.api.turn_off(self._pump_id)
        await asyncio.sleep(3.0)
        await self.coordinator.async_request_refresh()
