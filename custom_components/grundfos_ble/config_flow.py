"""Config flow: discover the pump, pair it inside the button window, probe what it offers.

Pairing is the whole difficulty. The pump accepts SMP pairing only for about three seconds
after its Bluetooth button is pressed, which it signals with a flag in its advertisement.
The ``pair`` progress step waits for that flag, connects at once through whichever proxy
Home Assistant picks (the strongest; the caller cannot choose), pairs, and uses the same
connection to read the identity and probe the optional objects. The same step serves
reauthentication (the bond was lost) and reconfiguration (re-pair on demand after moving a proxy).
It first makes local BlueZ adapters forget any bond they hold, so they pair rather than reconnect.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
import time
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
    async_process_advertisements,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util
import probatio

from grundfos_ble import (
    GRUNDFOS_SERVICE_UUID,
    BleTransport,
    ConnectionLost,
    GrundfosError,
    GrundfosPump,
    PairingRequired,
    RequestTimeout,
    parse_advertisement,
    profile_for,
    remove_bluez_bond,
)

from .const import (
    BUTTON_FLAG_MAX_AGE,
    BUTTON_WAIT_TIMEOUT,
    CONF_BLE_SOFTWARE_VERSION,
    CONF_CAPABILITIES,
    CONF_MODEL,
    CONF_PAIRED_AT,
    CONF_PAIRED_SOURCE,
    CONF_SERIAL_NUMBER,
    CONF_SOFTWARE_VERSION,
    CONF_UNIT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    OPT_SCAN_INTERVAL,
    PAIR_CONNECT_TIMEOUT,
)
from .coordinator import GrundfosCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PairOutcome:
    """What the pairing task found out."""

    error: str | None = None
    placeholders: dict[str, str] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    name: str | None = None


def _is_grundfos(service_info: BluetoothServiceInfoBleak) -> bool:
    advertisement = parse_advertisement(service_info.service_data.get(GRUNDFOS_SERVICE_UUID, b""))
    return (
        advertisement is not None
        and profile_for(advertisement.family, advertisement.type) is not None
    )


def _connectable_scanners(hass: HomeAssistant, address: str) -> list[str]:
    return [
        f"{device.scanner.name} ({device.advertisement.rssi} dBm)"
        for device in bluetooth.async_scanner_devices_by_address(hass, address, connectable=True)
    ]


def _passive_scanners(hass: HomeAssistant, address: str) -> list[str]:
    return [
        f"{device.scanner.name} ({device.advertisement.rssi} dBm)"
        for device in bluetooth.async_scanner_devices_by_address(hass, address, connectable=False)
        if not device.scanner.connectable
    ]


@callback
def _fresh_button_press(service_info: BluetoothServiceInfoBleak) -> bool:
    """Return True for an advertisement with the button flag that is not a stale replay."""
    advertisement = parse_advertisement(service_info.service_data.get(GRUNDFOS_SERVICE_UUID, b""))
    if advertisement is None or not advertisement.button_pressed:
        return False
    return time.monotonic() - service_info.time <= BUTTON_FLAG_MAX_AGE


def _likely_path(hass: HomeAssistant, address: str) -> str | None:
    """Return the proxy habluetooth will most likely use: the strongest connectable one."""
    devices = bluetooth.async_scanner_devices_by_address(hass, address, connectable=True)
    if not devices:
        return None
    best = max(devices, key=lambda device: device.advertisement.rssi or -127)
    return str(best.scanner.source)


class GrundfosConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle discovery, pairing, reauth and reconfigure."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise flow state."""
        self._address: str | None = None
        self._name: str | None = None
        self._discovered: dict[str, BluetoothServiceInfoBleak] = {}
        self._pair_task: asyncio.Task[PairOutcome] | None = None
        self._outcome: PairOutcome | None = None
        self._entry: ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GrundfosOptionsFlow:
        """Return the options flow."""
        return GrundfosOptionsFlow()

    # -- discovery -----------------------------------------------------------------------------

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a pump found by the Bluetooth integration."""
        advertisement = parse_advertisement(
            discovery_info.service_data.get(GRUNDFOS_SERVICE_UUID, b"")
        )
        if advertisement is None:
            return self.async_abort(reason="not_supported")
        if profile_for(advertisement.family, advertisement.type) is None:
            return self.async_abort(
                reason="unsupported_model",
                description_placeholders={
                    "family": str(advertisement.family),
                    "type": str(advertisement.type),
                },
            )
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address
        self._name = discovery_info.name or discovery_info.address
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Let the user pick a discovered pump."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            info = self._discovered[address]
            self._address = address
            self._name = info.name or address
            self.context["title_placeholders"] = {"name": self._name}
            return await self.async_step_bluetooth_confirm()

        current = self._async_current_ids(include_ignore=False)
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            if info.address in current or not _is_grundfos(info):
                continue
            self._discovered[info.address] = info
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_ADDRESS): probatio.In(
                        {
                            address: f"{info.name or address} ({address})"
                            for address, info in self._discovered.items()
                        }
                    )
                }
            ),
        )

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain the button press; refuse to start without a connectable proxy."""
        assert self._address is not None
        errors: dict[str, str] = {}
        connectable = _connectable_scanners(self.hass, self._address)
        if not connectable:
            errors["base"] = "no_connectable_proxy"
        elif user_input is not None:
            return await self.async_step_pair()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            errors=errors,
            description_placeholders={
                "name": self._name or self._address,
                "address": self._address,
                "connectable": ", ".join(connectable) or "none",
                "passive": ", ".join(_passive_scanners(self.hass, self._address)) or "none",
            },
        )

    # -- pairing -------------------------------------------------------------------------------

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Wait for the button, connect, pair and probe, as a progress step."""
        assert self._address is not None
        if self._pair_task is None:
            self._pair_task = self.hass.async_create_task(
                self._async_pair(self._address), eager_start=False
            )
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="pair",
                progress_task=self._pair_task,
                description_placeholders={"name": self._name or self._address},
            )
        self._outcome = self._pair_task.result()
        self._pair_task = None
        return self.async_show_progress_done(next_step_id="pair_done")

    async def async_step_pair_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create or update the entry, or show why pairing failed."""
        assert self._outcome is not None
        outcome = self._outcome
        if outcome.error is not None:
            return self.async_show_form(
                step_id="pair_failed",
                errors={"base": outcome.error},
                description_placeholders={"name": self._name or "", **outcome.placeholders},
            )
        if self._entry is not None:
            reason = "reauth_successful" if self.source == "reauth" else "reconfigure_successful"
            return self.async_update_reload_and_abort(
                self._entry, data_updates=outcome.data, reason=reason
            )
        title = outcome.name or self._name or self._address or "Grundfos pump"
        return self.async_create_entry(title=title, data=outcome.data)

    async def async_step_pair_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Run the pairing step again at the user's request."""
        return await self.async_step_pair()

    async def _async_pair(self, address: str) -> PairOutcome:
        """Run the pairing task with polling held off; it reports through the outcome."""
        coordinator: GrundfosCoordinator | None = None
        if self._entry is not None and self._entry.state is ConfigEntryState.LOADED:
            coordinator = self._entry.runtime_data
            coordinator.pause_polling()
        try:
            return await self._async_wait_and_pair(address)
        except Exception:
            _LOGGER.exception("Unexpected error while pairing with %s", address)
            return PairOutcome(error="unknown")
        finally:
            if coordinator is not None:
                coordinator.resume_polling()

    async def _async_wait_and_pair(self, address: str) -> PairOutcome:
        """Wait for the button, then connect, pair, read the identity and probe."""
        # A new bond is wanted. A local BlueZ adapter would reconnect with keys the pump may
        # have dropped and never pair, so it forgets them first; proxies are left alone.
        for scanner_device in bluetooth.async_scanner_devices_by_address(
            self.hass, address, connectable=True
        ):
            await remove_bluez_bond(scanner_device.ble_device)
        try:
            await async_process_advertisements(
                self.hass,
                _fresh_button_press,
                {"address": address},
                BluetoothScanningMode.ACTIVE,
                int(BUTTON_WAIT_TIMEOUT),
            )
        except TimeoutError:
            return PairOutcome(error="button_timeout")

        device = bluetooth.async_ble_device_from_address(self.hass, address, connectable=True)
        if device is None:
            return PairOutcome(error="no_connectable_proxy")
        source = _likely_path(self.hass, address) or "unknown"
        transport = BleTransport(
            device,
            name=self._name,
            max_attempts=1,
            connect_timeout=PAIR_CONNECT_TIMEOUT,
        )
        pump = GrundfosPump(transport)
        started = time.monotonic()
        try:
            await transport.connect()
            connect_seconds = time.monotonic() - started
            identity = await pump.read_identity()
            probe = await pump.probe_capabilities()
        except PairingRequired as err:
            _LOGGER.debug(
                "Pairing refused via %s after %.1fs: %s", source, time.monotonic() - started, err
            )
            return PairOutcome(
                error="window_missed",
                placeholders={"source": source, "seconds": f"{time.monotonic() - started:.1f}"},
            )
        except (ConnectionLost, RequestTimeout) as err:
            # The pump took the link and then would not talk: the usual sign of a missed window.
            _LOGGER.debug("No answer via %s: %s", source, err)
            if transport.reply_seen:
                return PairOutcome(
                    error="cannot_connect", placeholders={"source": source, "reason": str(err)}
                )
            return PairOutcome(
                error="window_missed",
                placeholders={"source": source, "seconds": f"{time.monotonic() - started:.1f}"},
            )
        except (GrundfosError, TimeoutError) as err:
            _LOGGER.debug("Pairing connection failed via %s: %s", source, err)
            return PairOutcome(
                error="cannot_connect", placeholders={"source": source, "reason": str(err)}
            )
        finally:
            await transport.disconnect()

        if identity.profile is None:
            return PairOutcome(
                error="unsupported_model",
                placeholders={"family": str(identity.family), "type": str(identity.type)},
            )
        _LOGGER.info(
            "Paired with %s via %s in %.1fs; optional objects: %s",
            address,
            source,
            connect_seconds,
            ", ".join(sorted(probe.supported)) or "none",
        )
        for key, reason in probe.errors.items():
            _LOGGER.debug("Object %s not available: %s", key, reason)
        return PairOutcome(
            name=identity.unit_name or self._name,
            data={
                CONF_ADDRESS: address,
                CONF_MODEL: identity.model or identity.profile.name,
                CONF_SERIAL_NUMBER: identity.serial_number,
                CONF_SOFTWARE_VERSION: identity.software_version,
                CONF_BLE_SOFTWARE_VERSION: identity.ble_software_version,
                CONF_UNIT_NAME: identity.unit_name,
                CONF_CAPABILITIES: sorted(probe.supported),
                CONF_PAIRED_SOURCE: source,
                CONF_PAIRED_AT: dt_util.utcnow().isoformat(),
            },
        )

    # -- reauth and reconfigure ----------------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-pairing after the pump refused encryption (no usable bond)."""
        self._entry = self._get_reauth_entry()
        self._address = self._entry.data[CONF_ADDRESS]
        self._name = self._entry.title
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Check the pump is in range, then run the pairing step."""
        return await self._async_step_repair("reauth_confirm", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-pair on demand (for example after moving the proxy)."""
        if self._entry is None:
            self._entry = self._get_reconfigure_entry()
            self._address = self._entry.data[CONF_ADDRESS]
            self._name = self._entry.title
        return await self._async_step_repair("reconfigure", user_input)

    async def _async_step_repair(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        assert self._address is not None
        errors: dict[str, str] = {}
        if bluetooth.async_last_service_info(self.hass, self._address, connectable=False) is None:
            errors["base"] = "no_longer_in_range"
        elif not _connectable_scanners(self.hass, self._address):
            errors["base"] = "no_connectable_proxy"
        elif user_input is not None:
            return await self.async_step_pair()
        return self.async_show_form(
            step_id=step_id,
            errors=errors,
            description_placeholders={
                "name": self._name or self._address,
                "connectable": ", ".join(_connectable_scanners(self.hass, self._address)) or "none",
            },
        )


class GrundfosOptionsFlow(OptionsFlow):
    """Poll interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show and store the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        OPT_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=5,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    )
                }
            ),
        )
