"""Base entity."""

from __future__ import annotations

from collections.abc import Iterable

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityDescription

from .const import DOMAIN
from .coordinator import GrundfosCoordinator


def supported[D: EntityDescription](
    hass: HomeAssistant,
    platform: str,
    coordinator: GrundfosCoordinator,
    descriptions: Iterable[D],
) -> list[D]:
    """Return the descriptions this pump supports; drop the registry entries of the rest.

    Capabilities are only ever dropped when the pump positively denied an object on re-pairing,
    so this removes the entities of objects the pump says it no longer has.
    """
    registry = er.async_get(hass)
    kept: list[D] = []
    for description in descriptions:
        requires: tuple[str, ...] = getattr(description, "requires", ())
        if all(coordinator.has(capability) for capability in requires):
            kept.append(description)
        elif entity_id := registry.async_get_entity_id(
            platform, DOMAIN, f"{coordinator.address}_{description.key}"
        ):
            registry.async_remove(entity_id)
    return kept


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
        """Unavailable when the pump stopped advertising or there is no current reading.

        An entity backed by an optional object is also unavailable when the last poll could not
        read that object, rather than showing an older poll's value as current.
        """
        coordinator = self.coordinator
        requires: tuple[str, ...] = getattr(self.entity_description, "requires", ())
        return (
            super().available
            and coordinator.advertising
            and coordinator.has_live_data
            and all(capability in coordinator.data.read for capability in requires)
        )
