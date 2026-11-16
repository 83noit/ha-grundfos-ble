"""Diagnostics download: entry data, Bluetooth paths, recent frames and poll statistics."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import CONF_PAIRED_SOURCE, CONF_SERIAL_NUMBER, CONF_UNIT_NAME
from .coordinator import GrundfosConfigEntry
from .redact import REDACTED, redact_address, scrub, scrub_scanners

TO_REDACT = {CONF_SERIAL_NUMBER, CONF_UNIT_NAME}


def _scrub_names(text: str | None, hass: HomeAssistant, entry: GrundfosConfigEntry) -> str | None:
    """Redact addresses, every adapter's or proxy's name, and the pump's name.

    Transport errors start with the pump's name, and Home Assistant's reachability text names
    each scanner. Diagnostics are files people attach to issues, so the pump's name goes too.
    """
    scrubbed = scrub_scanners(hass, text)
    if scrubbed is None:
        return None
    for pump_name in (entry.title, entry.data.get(CONF_UNIT_NAME)):
        if pump_name:
            scrubbed = scrubbed.replace(str(pump_name), REDACTED)
    return scrubbed


def _firmware(hass: HomeAssistant, source: str) -> dict[str, str | None]:
    """Return the adapter's or proxy's model and firmware version from its device entry.

    Home Assistant files each scanner under a Bluetooth config entry whose unique id is the
    scanner's source. A proxy's entry names the proxy's device, which is registered under its
    Wi-Fi MAC rather than the Bluetooth MAC the scanner uses; a local adapter's entry owns a
    device keyed by the adapter's Bluetooth address.
    """
    registry = dr.async_get(hass)
    device = None
    for scanner_entry in hass.config_entries.async_entries(bluetooth.DOMAIN):
        if scanner_entry.unique_id != source:
            continue
        if device_id := scanner_entry.data.get("source_device_id"):
            device = registry.async_get(device_id)
        else:
            device = registry.async_get_device_by_connection(
                (dr.CONNECTION_BLUETOOTH, source), scanner_entry.entry_id
            )
        break
    return {
        "model": getattr(device, "model", None),
        "sw_version": getattr(device, "sw_version", None),
    }


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _plain(v) for k, v in asdict(value).items()}
    if isinstance(value, bytes):
        return value.hex(" ")
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GrundfosConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    address: str = entry.data[CONF_ADDRESS]
    entry_data = async_redact_data(dict(entry.data), TO_REDACT)
    entry_data[CONF_ADDRESS] = redact_address(address)
    if isinstance(paired := entry_data.get(CONF_PAIRED_SOURCE), str):
        entry_data[CONF_PAIRED_SOURCE] = scrub(paired)
    scanners = [
        {
            "source": redact_address(device.scanner.source),
            "connectable": device.scanner.connectable,
            "rssi": device.advertisement.rssi,
            # ESPHome 2026.10 (ESP-IDF 5.5.6) proxies forget a pairing after one session.
            **_firmware(hass, device.scanner.source),
        }
        for device in bluetooth.async_scanner_devices_by_address(hass, address, connectable=False)
    ]
    advertisement = coordinator.advertisement
    return {
        "entry": {"data": entry_data, "options": dict(entry.options)},
        "bluetooth": {
            "available": coordinator.available,
            "last_rssi": coordinator.rssi,
            "last_source": scrub(coordinator.source),
            "scanners_hearing_the_pump": scanners,
            "reachability": _scrub_names(coordinator.reachability(), hass, entry),
            "advertisement": None
            if advertisement is None
            else {
                "raw": advertisement.raw.hex(" "),
                "family": advertisement.family,
                "type": advertisement.type,
                "flags": advertisement.flags,
                "button_pressed": advertisement.button_pressed,
                "alarm": advertisement.alarm,
                "warning": advertisement.warning,
                "client_connected": advertisement.client_connected,
            },
        },
        "polling": {
            "scan_interval": coordinator.scan_interval,
            "poll_count": coordinator.poll_count,
            "consecutive_failures": coordinator.consecutive_failures,
            "pairing_failures": coordinator.pairing_failures,
            "reauth_raised": coordinator.reauth_raised,
            "last_error": _scrub_names(coordinator.last_error, hass, entry),
            "capabilities": sorted(coordinator.capabilities),
            "read_last_poll": sorted(coordinator.last_read),
            "last_poll_seconds": coordinator.last_poll_seconds,
            "last_poll_source": scrub(coordinator.last_poll_source),
            "forgetful_proxy": coordinator.forgetful_proxy,
        },
        "data": _plain(coordinator.data),
        "last_frames": [{"direction": d, "frame": f} for d, f in coordinator.last_frames],
    }
