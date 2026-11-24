"""Repairs: turn a data-collection entry into a normal one once the pump is supported.

The proxy that paired for data collection still holds the bond, so no button press is needed:
the fix connects, reads the identity, probes the optional objects and rewrites the entry, which
then reloads with its sensors. If the bond is gone, the user is sent to Reconfigure instead.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from grundfos_ble import (
    BleTransport,
    GrundfosError,
    GrundfosPump,
    PairingRequired,
    Recorder,
)

from .config_flow import capture_lines, supported_entry_data
from .const import CONF_CAPABILITIES, CONF_COLLECT_ONLY, PAIR_CONNECT_TIMEOUT
from .coordinator import GrundfosConfigEntry
from .redact import scrub


class NowSupportedFlow(RepairsFlow):
    """Read a pump that became supported and make its entry a normal one."""

    def __init__(self, entry: GrundfosConfigEntry) -> None:
        """Remember the entry to convert."""
        self._entry = entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> RepairsFlowResult:
        """Start at the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Explain, then connect and convert; show why when that fails."""
        errors: dict[str, str] = {}
        placeholders = {"name": self._entry.title, "reason": ""}
        if user_input is not None:
            try:
                data = await self._async_read()
            except PairingRequired:
                errors["base"] = "needs_pairing"
            except (GrundfosError, TimeoutError) as err:
                errors["base"] = "cannot_connect"
                placeholders["reason"] = scrub(str(err)) or type(err).__name__
            else:
                if data is None:
                    errors["base"] = "still_unsupported"
                else:
                    self.hass.config_entries.async_update_entry(
                        self._entry, data={**self._entry.data, **data}
                    )
                    self.hass.config_entries.async_schedule_reload(self._entry.entry_id)
                    return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm", errors=errors, description_placeholders=placeholders
        )

    async def _async_read(self) -> dict[str, Any] | None:
        """Connect with the existing bond, read and probe; None if the pump is still unknown."""
        address: str = self._entry.data[CONF_ADDRESS]
        device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
        if device is None:
            raise GrundfosError("no Bluetooth proxy that can connect hears the pump")
        transport = BleTransport(
            device,
            name=self._entry.title,
            connect_timeout=PAIR_CONNECT_TIMEOUT,
            device_callback=lambda: bluetooth.async_ble_device_from_address(
                self.hass, address, connectable=True
            ),
        )
        recorder = Recorder(transport)
        pump = GrundfosPump(recorder)
        try:
            await recorder.connect()
            recorder.recording = False  # the serial and the owner's name stay out
            identity = await pump.read_identity()
            if identity.profile is None:
                return None
            recorder.recording = True
            recorder.label = "probe"
            probe = await pump.probe_capabilities()
        finally:
            await recorder.disconnect()
        lines = capture_lines(identity, recorder)
        source = transport.last_source or "unknown"
        stored = self._entry.data.get(CONF_CAPABILITIES)
        return {
            CONF_COLLECT_ONLY: False,
            **supported_entry_data(identity, probe, address, source, stored, lines),
        }


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """Create the flow for a fixable issue: only "now supported" is fixable."""
    entry_id = str((data or {}).get("entry_id"))
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise ValueError(f"no config entry for issue {issue_id}")
    return NowSupportedFlow(entry)
