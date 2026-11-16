"""Grundfos Bluetooth: Grundfos ALPHA2 GO circulator pumps over GENIbus-on-BLE."""

from __future__ import annotations

import logging

from bleak_retry_connector import close_stale_connections_by_address
from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

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
    await er.async_migrate_entries(hass, entry.entry_id, _migrate_unique_id)
    coordinator = GrundfosCoordinator(hass, _LOGGER, entry)
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_start())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GrundfosConfigEntry) -> bool:
    """Unload a config entry; a poll still running is cancelled, so it cannot outlive it."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop_polling()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: GrundfosConfigEntry) -> None:
    """Offer the pump for discovery again once its entry is gone."""
    bluetooth.async_rediscover_address(hass, entry.data[CONF_ADDRESS])


@callback
def _migrate_unique_id(entity: er.RegistryEntry) -> dict[str, str] | None:
    """Rename the "shaft power" sensor to "DC power" (0.2.0), keeping its history."""
    if entity.unique_id.endswith("_shaft_power"):
        return {"new_unique_id": entity.unique_id.removesuffix("_shaft_power") + "_dc_power"}
    return None
