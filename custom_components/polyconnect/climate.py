"""Climate platform for Polyconnect — heat pump thermostat.

HVAC modes (shown in HA as the main mode selector):
    heat  → Chauffage   (heating)
    cool  → Climatisation  (cooling / "Froid" in the app)
    auto  → Auto           (automatic)

    NOTE: HVACMode.OFF is intentionally excluded from hvac_modes.
    The heat pump on/off is handled by the TURN_ON / TURN_OFF features
    (the power button in the app). Showing "off" as a selectable mode
    causes confusion because it's a different action from changing the mode.

Preset modes (regulation sub-mode, layered on top of the HVAC mode):
    Eco    → Eco regulation
    Smart  → Smart regulation
    Boost  → Boost regulation

    NOTE: "Normal" is not a real app concept — omitted.
    When no regulation mode is active, preset_mode returns None (HA shows "None").
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
# The optimistic update handles the UI immediately; this delayed refresh confirms
# the real device state a few seconds later.
_MODE_REFRESH_DELAY = 8.0   # seconds


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PolyconnectCoordinator = entry.runtime_data
    async_add_entities([PolyconnectClimate(coordinator)])


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

    def __init__(self, coordinator: PolyconnectCoordinator) -> None:
        super().__init__(coordinator, "climate")

    @property
    def current_temperature(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("waterTemperature")

    @property
    def target_temperature(self) -> float | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("setpointTemperature")

    @property
    def hvac_mode(self) -> HVACMode:
        if not self.coordinator.data:
            return HVACMode.AUTO
        raw_mode = self.coordinator.data.get("operatingMode", "")
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
        if not self.coordinator.data:
            return None
        reg = self.coordinator.data.get("regulationMode")
        if reg and reg in REGULATION_MODES:
            return reg
        return None

    async def async_set_temperature(self, **kwargs) -> None:
        """Change the target temperature setpoint."""
        temp = kwargs.get("temperature")
        if temp is not None:
            await self.coordinator.api.set_setpoint(float(temp))
            await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Change the main operating mode (heat / cool / auto).

        Uses an optimistic update so the UI reflects the change immediately,
        then schedules a delayed refresh to confirm the real device state.
        """
        polyconnect_mode = HA_TO_POLYCONNECT_MODE.get(hvac_mode.value, "Automatique")

        # Optimistic update — UI reflects change instantly
        if self.coordinator.data is not None:
            self.coordinator.data["operatingMode"] = {
                "heat": "Chauffage",
                "cool": "Froid",
                "auto": "Automatique",
            }.get(hvac_mode.value, "Automatique")
            self.async_write_ha_state()

        # Send command to bridge (takes ~4s including Playwright navigation)
        await self.coordinator.api.set_mode(polyconnect_mode)

        # Delayed refresh: give Blazor time to re-render before reading state back
        await asyncio.sleep(_MODE_REFRESH_DELAY)
        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Change the regulation preset (Eco / Smart / Boost).

        Uses an optimistic update so the UI reflects the change immediately,
        then schedules a delayed refresh to confirm the real device state.
        """
        if preset_mode not in REGULATION_MODES:
            return

        # Optimistic update — UI reflects change instantly
        if self.coordinator.data is not None:
            self.coordinator.data["regulationMode"] = preset_mode
            self.async_write_ha_state()

        # Send command to bridge
        await self.coordinator.api.set_mode(preset_mode)

        # Delayed refresh
        await asyncio.sleep(_MODE_REFRESH_DELAY)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Turn the heat pump on."""
        if self.coordinator.data is not None:
            self.coordinator.data["heatPumpActive"] = True
            self.async_write_ha_state()
        await self.coordinator.api.turn_on()
        await asyncio.sleep(3.0)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        """Turn the heat pump off."""
        if self.coordinator.data is not None:
            self.coordinator.data["heatPumpActive"] = False
            self.async_write_ha_state()
        await self.coordinator.api.turn_off()
        await asyncio.sleep(3.0)
        await self.coordinator.async_request_refresh()
