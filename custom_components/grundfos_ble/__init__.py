"""Grundfos Bluetooth: Grundfos ALPHA2 GO circulator pumps over GENIbus-on-BLE."""

from __future__ import annotations

import logging

from bleak_retry_connector import close_stale_connections_by_address
from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from grundfos_ble import profile_for

from .const import (
    CONF_COLLECT_ONLY,
    CONF_FAMILY,
    CONF_TYPE,
    DOMAIN,
    ISSUE_COLLECT_ONLY,
    ISSUE_NOW_SUPPORTED,
    NEW_PUMP_URL,
)
from .coordinator import GrundfosConfigEntry, GrundfosCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: GrundfosConfigEntry) -> bool:
    """Set up a pump from a config entry.

    The pump may be powered only while a thermostat calls for it, so setup does not
    require it to be reachable: the coordinator polls when it advertises and the entities
    stay unavailable until then (quality scale: test-before-setup, temporary unavailability).
    """
    if entry.data.get(CONF_COLLECT_ONLY):
        _async_raise_collect_issue(hass, entry)
        return True
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
    if not isinstance(getattr(entry, "runtime_data", None), GrundfosCoordinator):
        # Set up for data collection; the data may already say otherwise after a fix.
        ir.async_delete_issue(hass, DOMAIN, _collect_issue_id(entry))
        return True
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop_polling()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: GrundfosConfigEntry) -> None:
    """Offer the pump for discovery again once its entry is gone."""
    ir.async_delete_issue(hass, DOMAIN, _collect_issue_id(entry))
    bluetooth.async_rediscover_address(hass, entry.data[CONF_ADDRESS])


@callback
def _migrate_unique_id(entity: er.RegistryEntry) -> dict[str, str] | None:
    """Rename the "shaft power" sensor to "DC power" (0.2.0), keeping its history."""
    if entity.unique_id.endswith("_shaft_power"):
        return {"new_unique_id": entity.unique_id.removesuffix("_shaft_power") + "_dc_power"}
    return None


def _collect_issue_id(entry: GrundfosConfigEntry) -> str:
    return f"{ISSUE_COLLECT_ONLY}_{entry.entry_id}"


@callback
def _async_raise_collect_issue(hass: HomeAssistant, entry: GrundfosConfigEntry) -> None:
    """Tell the owner of a data-collection entry what to do with it.

    Until this integration supports the pump: download diagnostics and attach them to an issue.
    Once an update supports it, the issue is fixable: the fix reads the pump with the bond the
    proxy kept and turns the entry into a normal one (Reconfigure does the same with a new pairing).
    """
    family, type_ = entry.data.get(CONF_FAMILY), entry.data.get(CONF_TYPE)
    supported = (
        isinstance(family, int)
        and isinstance(type_, int)
        and profile_for(family, type_) is not None
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        _collect_issue_id(entry),
        data={"entry_id": entry.entry_id},
        is_fixable=supported,
        is_persistent=False,
        learn_more_url=NEW_PUMP_URL,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_NOW_SUPPORTED if supported else ISSUE_COLLECT_ONLY,
        translation_placeholders={"name": entry.title, "family": str(family), "type": str(type_)},
    )
