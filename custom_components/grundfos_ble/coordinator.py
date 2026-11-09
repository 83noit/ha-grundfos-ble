"""Polling coordinator: connect per poll, only while the pump is advertising."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import logging
import time

from bleak.exc import BleakError
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CALLBACK_TYPE, CoreState, HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.event import async_track_time_interval

from grundfos_ble import (
    GRUNDFOS_SERVICE_UUID,
    Advertisement,
    BleTransport,
    ConnectionLost,
    ControlMode,
    Faults,
    FlowLimiter,
    GrundfosConnectionError,
    GrundfosError,
    GrundfosPump,
    Measurements,
    NotSupported,
    OperationState,
    PairingRequired,
    ProtocolError,
    RequestTimeout,
    Statistics,
    Temperatures,
    UserSetpoint,
    parse_advertisement,
)

from .const import (
    ADVERT_STALE_SECONDS,
    CAP_ALARMS,
    CAP_AUTONIGHT,
    CAP_ENERGY,
    CAP_MAX_FLOW_LIMITER,
    CAP_MIN_FLOW_LIMITER,
    CAP_OPERATION,
    CAP_STATISTICS,
    CAP_TEMPERATURES,
    CAP_WARNINGS,
    CONF_BLE_SOFTWARE_VERSION,
    CONF_CAPABILITIES,
    CONF_MODEL,
    CONF_PAIRED_SOURCE,
    CONF_SERIAL_NUMBER,
    CONF_SOFTWARE_VERSION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FAILURES_BEFORE_UNAVAILABLE,
    MAX_NO_REPLY_POLLS,
    MAX_PAIRING_FAILURES,
    OPT_SCAN_INTERVAL,
    POLL_TIMEOUT,
    REAUTH_BACKOFF,
)

type GrundfosConfigEntry = ConfigEntry[GrundfosCoordinator]

POLL_TICK = timedelta(seconds=5)
"""How often the coordinator checks whether a poll is due.

The pump's advertisement is identical every time and habluetooth filters same-payload
adverts before callbacks, so adverts alone cannot pace the polling.
"""


@dataclass(slots=True)
class PumpData:
    """Everything one poll produced. None means not read (unsupported or failed)."""

    measurements: Measurements | None = None
    operation: OperationState | None = None
    faults: Faults | None = None
    autonight: bool | None = None
    max_limiter: FlowLimiter | None = None
    min_limiter: FlowLimiter | None = None
    statistics: Statistics | None = None
    temperatures: Temperatures | None = None
    energy_kwh: float | None = None
    stored_setpoint: UserSetpoint | None = None
    """The setpoint saved for the running mode; diagnostics only, for comparison with operation."""
    polled_at: float | None = None
    """time.monotonic() of the successful poll."""


class GrundfosCoordinator(ActiveBluetoothDataUpdateCoordinator[PumpData]):
    """Polls the pump over one short BLE connection whenever it is advertising and due."""

    def __init__(
        self, hass: HomeAssistant, logger: logging.Logger, entry: GrundfosConfigEntry
    ) -> None:
        """Set up the coordinator for ``entry``."""
        self.entry = entry
        address: str = entry.data[CONF_ADDRESS]
        super().__init__(
            hass=hass,
            logger=logger,
            address=address,
            mode=BluetoothScanningMode.ACTIVE,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_poll_pump,
            connectable=True,
        )
        self.data = PumpData()
        self.capabilities: frozenset[str] = frozenset(entry.data.get(CONF_CAPABILITIES, ()))
        self.advertisement: Advertisement | None = None
        self.rssi: int | None = None
        self.source: str | None = None
        self.poll_count = 0
        self.consecutive_failures = 0
        self.pairing_failures = 0
        self.no_reply_polls = 0
        self.reauth_raised = False
        self.advertising = False
        self.last_error: str | None = None
        self.last_frames: list[tuple[str, str]] = []
        self._unavailable_logged = False
        self._cancel_tick: CALLBACK_TYPE | None = None
        self._polling = False
        self._paused = False
        self._object_problems: set[str] = set()
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            manufacturer="Grundfos",
            model=entry.data.get(CONF_MODEL),
            name=entry.title,
            serial_number=entry.data.get(CONF_SERIAL_NUMBER),
            sw_version=entry.data.get(CONF_SOFTWARE_VERSION),
            hw_version=entry.data.get(CONF_BLE_SOFTWARE_VERSION),
        )

    @property
    def scan_interval(self) -> int:
        """Seconds between polls, from the options flow."""
        return int(self.entry.options.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    @property
    def poll_ok(self) -> bool:
        """False once several polls in a row have failed."""
        return self.consecutive_failures < FAILURES_BEFORE_UNAVAILABLE

    @property
    def connected(self) -> bool:
        """True when the pump advertises and the last polls succeeded with data."""
        return self.available and self.advertising and self.has_live_data

    @property
    def has_live_data(self) -> bool:
        """True when recent polls worked and the readings belong to this power-up."""
        return self.poll_ok and self.data.measurements is not None

    def has(self, capability: str) -> bool:
        """Return True when the pairing-time probe found this optional object on the pump."""
        return capability in self.capabilities

    @callback
    def _async_start(self) -> None:
        super()._async_start()
        self._cancel_tick = async_track_time_interval(self.hass, self._async_tick, POLL_TICK)

    @callback
    def _async_stop(self) -> None:
        if self._cancel_tick is not None:
            self._cancel_tick()
            self._cancel_tick = None
        super()._async_stop()

    @callback
    def _async_tick(self, _now: datetime) -> None:
        """Track the pump's adverts and schedule a poll when one is due."""
        heard = bluetooth.async_last_service_info(self.hass, self.address, connectable=False)
        now = monotonic_time_coarse()
        fresh = heard is not None and now - heard.time < ADVERT_STALE_SECONDS
        if heard is not None:
            self.rssi = heard.rssi
            self.source = heard.source
        if self.advertising and not fresh:
            self.advertising = False
            self._async_forget_data()
            self.async_update_listeners()
        if not fresh or not self.available:
            return
        self.advertising = True
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        if info is None:
            return
        age = None if self._last_poll is None else now - self._last_poll
        if self._needs_poll(info, age):
            self._last_service_info = info
            self._debounced_poll.async_schedule_call()

    @callback
    def pause_polling(self) -> None:
        """Hold polling while a pairing flow owns the pump's button window."""
        self._paused = True

    @callback
    def resume_polling(self) -> None:
        """Let polling continue."""
        self._paused = False

    @callback
    def _needs_poll(
        self, service_info: BluetoothServiceInfoBleak, seconds_since_last_poll: float | None
    ) -> bool:
        if self._polling or self._paused or self.hass.state is not CoreState.running:
            return False
        if self.advertisement is not None and self.advertisement.button_pressed:
            return False  # someone is pairing; a poll now could take the window from them
        interval = REAUTH_BACKOFF if self.reauth_raised else self.scan_interval
        if seconds_since_last_poll is not None and seconds_since_last_poll < interval:
            return False
        return (
            bluetooth.async_ble_device_from_address(
                self.hass, service_info.device.address, connectable=True
            )
            is not None
        )

    @callback
    def _async_forget_data(self) -> None:
        """Drop the last readings so that they cannot return as live on the next power-up."""
        if self.data.measurements is not None and not self._unavailable_logged:
            self.logger.info("%s: the pump stopped advertising", self.entry.title)
            self._unavailable_logged = True
        self.data = PumpData()
        self.consecutive_failures = 0

    @callback
    def _async_handle_unavailable(self, service_info: BluetoothServiceInfoBleak) -> None:
        self.advertising = False
        self._async_forget_data()
        super()._async_handle_unavailable(service_info)

    @callback
    def _async_handle_bluetooth_event(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        advertisement = parse_advertisement(
            service_info.service_data.get(GRUNDFOS_SERVICE_UUID, b"")
        )
        if advertisement is not None:
            self.advertisement = advertisement
        self.rssi = service_info.rssi
        self.source = service_info.source
        self.advertising = True
        super()._async_handle_bluetooth_event(service_info, change)

    async def _async_poll_pump(self, service_info: BluetoothServiceInfoBleak) -> PumpData:
        self.poll_count += 1
        self._polling = True
        try:
            async with asyncio.timeout(POLL_TIMEOUT):
                data = await self._async_attempt(service_info, retry=True)
                if data is None:
                    data = await self._async_attempt(service_info, retry=False)
                return data or self.data
        except Exception as err:
            self._reset_pairing_evidence()
            self._record_failure(err)
            return self.data
        finally:
            self._polling = False

    async def _async_attempt(
        self, service_info: BluetoothServiceInfoBleak, *, retry: bool
    ) -> PumpData | None:
        """Run one connection; None means "try once more on a fresh link"."""
        device = (
            bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
            or service_info.device
        )
        transport = BleTransport(device, name=self.entry.title, max_attempts=2)
        pump = GrundfosPump(transport)
        try:
            await transport.connect()
            data = await self._read(pump)
        except PairingRequired as err:
            self._note_refusal(err)
            return self.data
        except (RequestTimeout, ConnectionLost) as err:
            if retry:
                self.logger.debug("%s: retrying after %s", self.entry.title, err)
                return None
            self._note_silence(err, replied=transport.reply_seen)
            return self.data
        except (GrundfosError, BleakError, TimeoutError) as err:
            self._reset_pairing_evidence()
            self._record_failure(err)
            return self.data
        finally:
            self.last_frames = [
                (direction, frame.hex(" ")) for direction, frame in transport.last_frames
            ]
            await transport.disconnect()
        self._record_success()
        return data

    async def _read(self, pump: GrundfosPump) -> PumpData:
        """Read the measurements, then whatever optional objects can be had on this link."""
        data = replace(
            self.data, measurements=await pump.read_measurements(), polled_at=time.monotonic()
        )

        async def operation() -> None:
            data.operation = await pump.read_operation()
            data.stored_setpoint = await self._read_stored_setpoint(pump, data.operation)

        async def faults() -> None:
            data.faults = await pump.read_faults()

        async def autonight() -> None:
            data.autonight = await pump.read_autonight()

        async def limiters() -> None:
            data.max_limiter, data.min_limiter = await pump.read_flow_limiters()

        async def statistics() -> None:
            data.statistics = await pump.read_statistics()

        async def temperatures() -> None:
            data.temperatures = await pump.read_temperatures()

        async def energy() -> None:
            data.energy_kwh = await pump.read_energy_kwh()

        steps: tuple[tuple[tuple[str, ...], Callable[[], Awaitable[None]]], ...] = (
            ((CAP_OPERATION,), operation),
            ((CAP_ALARMS, CAP_WARNINGS), faults),
            ((CAP_AUTONIGHT,), autonight),
            ((CAP_MAX_FLOW_LIMITER, CAP_MIN_FLOW_LIMITER), limiters),
            ((CAP_STATISTICS,), statistics),
            ((CAP_TEMPERATURES,), temperatures),
            ((CAP_ENERGY,), energy),
        )
        for capabilities, step in steps:
            if not all(self.has(capability) for capability in capabilities):
                continue
            try:
                await step()
            except GrundfosConnectionError as err:
                self.logger.debug("%s: keeping a partial read: %s", self.entry.title, err)
                break  # the link is gone; what has been read so far is still good
            except (NotSupported, ProtocolError) as err:
                self._note_object_problem(capabilities[0], err)
        return data

    async def _read_stored_setpoint(
        self, pump: GrundfosPump, operation: OperationState
    ) -> UserSetpoint | None:
        """Read the running mode's saved setpoint; None when the pump has no such object."""
        try:
            return await pump.read_user_setpoint(ControlMode(operation.control_mode))
        except (NotSupported, ProtocolError, ValueError) as err:
            self.logger.debug("%s: no stored setpoint: %s", self.entry.title, err)
            return None

    def _note_object_problem(self, capability: str, err: Exception) -> None:
        if capability not in self._object_problems:
            self._object_problems.add(capability)
            self.logger.info(
                "%s: could not read %s, keeping the rest: %s", self.entry.title, capability, err
            )

    def _bonded_proxy_hears_pump(self) -> bool:
        paired = self.entry.data.get(CONF_PAIRED_SOURCE)
        if not paired or paired == "unknown":
            return True
        return any(
            device.scanner.source == paired
            for device in bluetooth.async_scanner_devices_by_address(
                self.hass, self.address, connectable=True
            )
        )

    def _reset_pairing_evidence(self) -> None:
        self.pairing_failures = 0
        self.no_reply_polls = 0

    def _note_refusal(self, err: PairingRequired) -> None:
        self.no_reply_polls = 0
        if self._bonded_proxy_hears_pump():
            self.pairing_failures += 1
        else:
            # Refused by a proxy that never held the bond: says nothing about the pump's bond.
            self.pairing_failures = 0
        self._record_failure(err)
        if self.pairing_failures >= MAX_PAIRING_FAILURES:
            self._raise_reauth(str(err))

    def _note_silence(self, err: GrundfosConnectionError, *, replied: bool) -> None:
        self.pairing_failures = 0
        self.no_reply_polls = 0 if replied else self.no_reply_polls + 1
        self._record_failure(err)
        if self.no_reply_polls >= MAX_NO_REPLY_POLLS and self._bonded_proxy_hears_pump():
            self._raise_reauth("the pump connects but no longer answers")

    def _raise_reauth(self, reason: str) -> None:
        if self.reauth_raised:
            return
        self.reauth_raised = True
        self.logger.warning(
            "%s: %s; it needs pairing again (press its Bluetooth button when the repair asks)",
            self.entry.title,
            reason,
        )
        self.entry.async_start_reauth(self.hass)

    def _close_open_reauth(self) -> None:
        """Close the repair after a good poll: re-pairing now would only move the bond."""
        self.reauth_raised = False
        flows = self.hass.config_entries.flow.async_progress_by_handler(
            DOMAIN, match_context={"source": SOURCE_REAUTH, "entry_id": self.entry.entry_id}
        )
        for flow in flows:
            self.hass.config_entries.flow.async_abort(flow["flow_id"])

    def _record_failure(self, err: BaseException) -> None:
        self.consecutive_failures += 1
        self.last_error = f"{type(err).__name__}: {err}"
        if (
            self.consecutive_failures == FAILURES_BEFORE_UNAVAILABLE
            and not self._unavailable_logged
        ):
            self.logger.info(
                "%s: cannot read the pump (%s); it will show as unavailable until a poll succeeds",
                self.entry.title,
                self.last_error,
            )
            self._unavailable_logged = True
        else:
            self.logger.debug("%s: poll failed: %s", self.entry.title, self.last_error)
        self.async_update_listeners()

    def _record_success(self) -> None:
        if self._unavailable_logged:
            self.logger.info("%s: the pump is reachable again", self.entry.title)
            self._unavailable_logged = False
        self.consecutive_failures = 0
        self.last_error = None
        self._reset_pairing_evidence()
        if self.reauth_raised:
            self._close_open_reauth()
