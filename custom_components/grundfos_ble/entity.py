"""Base entity."""

from __future__ import annotations

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.helpers.entity import EntityDescription

from .coordinator import GrundfosCoordinator


class GrundfosEntity(PassiveBluetoothCoordinatorEntity[GrundfosCoordinator]):
    """An entity of one pump; available while it advertises and recent polls succeeded."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GrundfosCoordinator, description: EntityDescription) -> None:
        """Bind the entity to its coordinator and description."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        """Unavailable when the pump stopped advertising or there is no current reading."""
        coordinator = self.coordinator
        return super().available and coordinator.advertising and coordinator.has_live_data
