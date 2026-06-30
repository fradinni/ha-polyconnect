"""Base entity class shared by all Polyconnect platforms.

Each entity is bound to a single heat pump. ``device_info`` is derived from
the pump_id+name so each pump shows up as its own device in the HA UI.
"""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import PolyconnectCoordinator
from .const import DOMAIN


class PolyconnectEntity(CoordinatorEntity[PolyconnectCoordinator]):
    """Base class for all per-pump Polyconnect entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PolyconnectCoordinator,
        pump_id: str,
        pump_name: str,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._pump_id = pump_id
        self._pump_name = pump_name
        self._key = key
        # Unique-id includes pump_id so multiple pumps don't collide.
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{pump_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.config_entry.entry_id}_{pump_id}")},
            name=pump_name,
            manufacturer="Polytropic",
            model="Pool Heat Pump",
            configuration_url="https://polytropic.user-app.pool.mytech-connect.io",
        )

    @property
    def _pump_data(self) -> dict[str, Any] | None:
        """Shortcut: the latest status dict for this entity's pump (or None)."""
        return self.coordinator.get_pump_data(self._pump_id)

    @property
    def available(self) -> bool:
        """Entity is available only when coordinator has data for this pump."""
        return super().available and self._pump_data is not None
