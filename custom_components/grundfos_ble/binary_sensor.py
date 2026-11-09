"""Binary sensors: connectivity, AutoNight and limiter state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CAP_AUTONIGHT, CAP_MAX_FLOW_LIMITER, CAP_MIN_FLOW_LIMITER
from .coordinator import GrundfosConfigEntry, GrundfosCoordinator
from .entity import GrundfosEntity

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class GrundfosBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor plus how to read it."""

    is_on_fn: Callable[[GrundfosCoordinator], bool | None]
    requires: tuple[str, ...] = ()
    always_available: bool = False
    """Connectivity-style sensors report off instead of going unavailable."""


BINARY_SENSORS: tuple[GrundfosBinarySensorDescription, ...] = (
    GrundfosBinarySensorDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        always_available=True,
        is_on_fn=lambda c: c.connected,
    ),
    GrundfosBinarySensorDescription(
        key="autonight",
        translation_key="autonight",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        requires=(CAP_AUTONIGHT,),
        is_on_fn=lambda c: c.data.autonight,
    ),
    GrundfosBinarySensorDescription(
        key="max_flow_limiter",
        translation_key="max_flow_limiter",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        requires=(CAP_MAX_FLOW_LIMITER, CAP_MIN_FLOW_LIMITER),
        is_on_fn=lambda c: None if c.data.max_limiter is None else c.data.max_limiter.enabled,
    ),
    GrundfosBinarySensorDescription(
        key="min_flow_limiter",
        translation_key="min_flow_limiter",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        requires=(CAP_MAX_FLOW_LIMITER, CAP_MIN_FLOW_LIMITER),
        is_on_fn=lambda c: None if c.data.min_limiter is None else c.data.min_limiter.enabled,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrundfosConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the binary sensors this pump supports."""
    coordinator = entry.runtime_data
    async_add_entities(
        GrundfosBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if all(coordinator.has(capability) for capability in description.requires)
    )


class GrundfosBinarySensor(GrundfosEntity, BinarySensorEntity):
    """A pump flag."""

    entity_description: GrundfosBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        """Current state."""
        return self.entity_description.is_on_fn(self.coordinator)

    @property
    def available(self) -> bool:
        """Connectivity is always available; the rest follow the pump."""
        if self.entity_description.always_available:
            return True
        return super().available
