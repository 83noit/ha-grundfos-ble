"""Grundfos Bluetooth: Grundfos ALPHA2 GO circulator pumps over GENIbus-on-BLE."""

from __future__ import annotations

import logging

from bleak_retry_connector import close_stale_connections_by_address
from homeassistant.const import CONF_ADDRESS, Platform
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
    # A link left open by an earlier run (a restart mid-poll on a local adapter) would hold the
    # pump's one client slot and fail every poll; yalexs_ble guards against the same.
    await close_stale_connections_by_address(entry.data[CONF_ADDRESS])
    coordinator = GrundfosCoordinator(hass, _LOGGER, entry)
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_start())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GrundfosConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
