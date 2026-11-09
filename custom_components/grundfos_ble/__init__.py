"""Grundfos Bluetooth: Grundfos ALPHA2 GO circulator pumps over GENIbus-on-BLE."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import GrundfosConfigEntry, GrundfosCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: GrundfosConfigEntry) -> bool:
    """Set up a pump from a config entry.

    The pump may be powered only while a thermostat calls for it, so setup does not
    require it to be reachable: the coordinator polls when it advertises and the entities
    stay unavailable until then (quality scale: test-before-setup, temporary unavailability).
    """
    coordinator = GrundfosCoordinator(hass, _LOGGER, entry)
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_start())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GrundfosConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
