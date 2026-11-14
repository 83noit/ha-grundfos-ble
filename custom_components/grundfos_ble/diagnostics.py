"""Diagnostics download: entry data, Bluetooth paths, recent frames and poll statistics."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import re
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import CONF_PAIRED_SOURCE, CONF_SERIAL_NUMBER, CONF_UNIT_NAME
from .coordinator import GrundfosConfigEntry

TO_REDACT = {CONF_SERIAL_NUMBER, CONF_UNIT_NAME}
_ADDRESS = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")


def redact_address(address: str) -> str:
    """Keep the last two bytes of a Bluetooth address, enough to tell devices apart."""
    parts = address.split(":")
    if len(parts) != 6:
        return REDACTED
    return "**:**:**:**:" + ":".join(parts[4:])


def _scrub(text: str | None) -> str | None:
    """Redact every Bluetooth address inside free text such as an error message."""
    if text is None:
        return None
    return _ADDRESS.sub(lambda match: redact_address(match.group(0)), text)


def _scrub_error(text: str | None, entry: GrundfosConfigEntry) -> str | None:
    """Redact addresses and the pump's name, which transport errors start with."""
    scrubbed = _scrub(text)
    if scrubbed is None:
        return None
    for name in (entry.title, entry.data.get(CONF_UNIT_NAME)):
        if name:
            scrubbed = scrubbed.replace(name, REDACTED)
    return scrubbed


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
        entry_data[CONF_PAIRED_SOURCE] = _scrub(paired)
    scanners = [
        {
            "source": redact_address(device.scanner.source),
            "connectable": device.scanner.connectable,
            "rssi": device.advertisement.rssi,
        }
        for device in bluetooth.async_scanner_devices_by_address(hass, address, connectable=False)
    ]
    advertisement = coordinator.advertisement
    return {
        "entry": {"data": entry_data, "options": dict(entry.options)},
        "bluetooth": {
            "available": coordinator.available,
            "last_rssi": coordinator.rssi,
            "last_source": _scrub(coordinator.source),
            "scanners_hearing_the_pump": scanners,
            "reachability": _scrub(coordinator.reachability()),
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
            "last_error": _scrub_error(coordinator.last_error, entry),
            "capabilities": sorted(coordinator.capabilities),
        },
        "data": _plain(coordinator.data),
        "last_frames": [{"direction": d, "frame": f} for d, f in coordinator.last_frames],
    }
