"""Base entity class shared by all Polyconnect platforms."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import PolyconnectCoordinator
from .const import DOMAIN


class PolyconnectEntity(CoordinatorEntity[PolyconnectCoordinator]):
    """Base class for all Polyconnect entities.

    Provides shared device_info so all entities appear under the same device
    card in the HA UI, and a consistent unique_id scheme.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: PolyconnectCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name="Polyconnect Heat Pump",
            manufacturer="Polytropic",
            model="Pool Heat Pump",
            configuration_url="https://polytropic.user-app.pool.mytech-connect.io",
        )

    @property
    def available(self) -> bool:
        """Entity is available only when coordinator has valid data."""
        return super().available and self.coordinator.data is not None
