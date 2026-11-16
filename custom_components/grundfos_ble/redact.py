"""Shortening Bluetooth addresses and proxy names, for logs and diagnostics.

An address keeps its last two bytes, enough to tell devices apart in a log. A proxy's name is
the user's own label, and Home Assistant's reachability text quotes it next to its address;
it becomes the shortened address.
"""

from __future__ import annotations

import re

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

REDACTED = "**REDACTED**"
"""What a value that cannot be shortened becomes (the diagnostics integration's marker)."""
_ADDRESS = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")


def redact_address(address: str | None) -> str:
    """Keep the last two bytes of a Bluetooth address, enough to tell devices apart."""
    if address is None:
        return "unknown"
    parts = address.split(":")
    if len(parts) != 6:
        return REDACTED
    return "**:**:**:**:" + ":".join(parts[4:])


def scrub(text: str | None) -> str | None:
    """Shorten every Bluetooth address inside free text such as an error message."""
    if text is None:
        return None
    return _ADDRESS.sub(lambda match: redact_address(match.group(0)), text)


def scrub_scanners(hass: HomeAssistant, text: str | None) -> str | None:
    """Replace every adapter's or proxy's name by its shortened address, then scrub addresses.

    Home Assistant names a proxy "<name> (<source>)"; either form may appear.
    """
    if text is None:
        return None
    names: list[tuple[str, str]] = []
    for scanner in bluetooth.async_current_scanners(hass):
        replacement = f"scanner {redact_address(scanner.source)}"
        full = str(scanner.name or "")
        bare = full.removesuffix(f" ({scanner.source})")
        names += [(name, replacement) for name in (full, bare) if name]
    for name, replacement in sorted(names, key=lambda pair: -len(pair[0])):
        text = text.replace(name, replacement)
    return scrub(text)
