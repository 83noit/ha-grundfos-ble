"""Polling coordinator: connect per poll, only while the pump is advertising."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time

from bleak.exc import BleakError
from bluetooth_data_tools import monotonic_time_coarse
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothReachabilityIntent,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CALLBACK_TYPE, CoreState, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

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
    PairingUnsupported,
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
    CONF_PAIRED_AT,
    CONF_PAIRED_SOURCE,
    CONF_SERIAL_NUMBER,
    CONF_SOFTWARE_VERSION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FAILURES_BEFORE_UNAVAILABLE,
    FORGETFUL_PROXY_WINDOW,
    ISSUE_PROXY_CANNOT_PAIR,
    ISSUE_PROXY_FORGETS_BOND,
    ISSUE_UNREACHABLE,
    MAX_PAIRING_FAILURES,
    MAX_SILENT_BACKOFF,
    OPT_SCAN_INTERVAL,
    OUTAGE_ISSUE_AFTER,
    POLL_ADVERT_MAX_AGE,
    POLL_TIMEOUT,
    REAUTH_BACKOFF,
    RETRY_PAUSE,
)

type GrundfosConfigEntry = ConfigEntry[GrundfosCoordinator]

POLL_TICK = timedelta(seconds=5)
"""How often the coordinator checks whether a poll is due.

The pump's advertisement is identical every time and habluetooth filters same-payload
adverts before callbacks, so adverts alone cannot pace the polling.
"""


@dataclass(slots=True)
class PumpData:
    """Everything one poll produced. None means not read (unsupported or failed) on that poll.

    Optional values are never carried over from an earlier poll: what a poll could not read is
    None, and ``read`` names the capabilities it did read.
    """

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
    read: frozenset[str] = frozenset()
    """The optional capabilities this poll read."""


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
        self.reauth_raised = False
        self.advertising = False
        self.last_error: str | None = None
        self.last_frames: list[tuple[str, str]] = []
        self.last_read: frozenset[str] = frozenset()
        """What the last successful poll read; kept when the readings are dropped."""
        self.last_poll_seconds: float | None = None
        self.last_poll_source: str | None = None
        self._unavailable_logged = False
        self._cancel_tick: CALLBACK_TYPE | None = None
        self._polling = False
        self._paused = False
        self._idle = asyncio.Event()
        self._idle.set()
        self._poll_task: asyncio.Task[object] | None = None
        self._object_problems: set[str] = set()
        self._silent: dict[str, int] = {}
        """Optional step -> how many polls in a row it went silent."""
        self._skip_until: dict[str, int] = {}
        """Optional step -> the poll count from which it is tried again."""
        self.good_poll_seen = False
        self.forgetful_proxy = False
        self._failing_since: float | None = None
        self._issues: set[str] = set()
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

    def reachability(self) -> str:
        """Home Assistant's account of the paths to the pump, for logs and diagnostics."""
        return bluetooth.async_address_reachability_diagnostics(
            self.hass, self.address.upper(), BluetoothReachabilityIntent.CONNECTION
        )

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
        if self._poll_task is not None and not self._poll_task.done():
            self._poll_task.cancel()  # a poll must not outlive the entry
        super()._async_stop()

    async def async_stop_polling(self) -> None:
        """Cancel a running poll, wait for it to end and drop any poll it queued."""
        task = self._poll_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({task})
        self._debounced_poll.async_cancel()

    async def async_wait_idle(self) -> None:
        """Wait, at most one poll's length, for a running poll to finish."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(POLL_TIMEOUT):
                await self._idle.wait()

    @callback
    def _async_tick(self, _now: datetime) -> None:
        """Track the pump's adverts and schedule a poll when one is due.

        Freshness is judged on connectable adverts only: a pump heard by a passive scanner alone
        cannot be polled, so it counts as gone.
        """
        heard = bluetooth.async_last_service_info(self.hass, self.address, connectable=False)
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        now = monotonic_time_coarse()
        fresh = info is not None and now - info.time < ADVERT_STALE_SECONDS
        if heard is not None:
            self.rssi = heard.rssi
            self.source = heard.source
        if self.advertising and not fresh:
            self.advertising = False
            self._async_forget_data()
            self.async_update_listeners()
        if not fresh:
            return
        if not self.available:
            # After an outage a connectable advert can be stored without a callback, when a
            # non-connectable scanner was heard first, and the unchanging payload filters the
            # rest. The connectable history is emptied by the outage, so an entry here is new.
            if info is not None:
                self._async_handle_bluetooth_event(info, BluetoothChange.ADVERTISEMENT)
            return
        self.advertising = True
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
        if monotonic_time_coarse() - service_info.time > POLL_ADVERT_MAX_AGE:
            return False  # most likely just lost power; the advert check will say so
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
        self._failing_since = None

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
        self._idle.clear()
        self._poll_task = asyncio.current_task()
        try:
            async with asyncio.timeout(POLL_TIMEOUT):
                data = await self._async_attempt(service_info, retry=True)
                if data is None:
                    await asyncio.sleep(RETRY_PAUSE)
                    data = await self._async_attempt(service_info, retry=False)
                return data or self.data
        except Exception as err:
            self._reset_pairing_evidence()
            self._record_failure(err)
            return self.data
        finally:
            self._polling = False
            self._poll_task = None
            self._idle.set()

    async def _async_attempt(
        self, service_info: BluetoothServiceInfoBleak, *, retry: bool
    ) -> PumpData | None:
        """Run one connection; None means "try once more on a fresh link"."""
        device = (
            bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
            or service_info.device
        )
        transport = BleTransport(
            device,
            name=self.entry.title,
            max_attempts=2,
            device_callback=lambda: bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            ),
        )
        pump = GrundfosPump(transport)
        started = time.monotonic()
        try:
            await transport.connect()
            self.logger.debug(
                "%s: connected via %s in %.2f s",
                self.entry.title,
                transport.last_source,
                time.monotonic() - started,
            )
            data = await self._read(pump)
        except PairingRequired as err:
            self._note_refusal(err, transport.last_source)
            return self.data
        except PairingUnsupported as err:
            self._reset_pairing_evidence()
            self._raise_issue(
                ISSUE_PROXY_CANNOT_PAIR, {"proxy": self._scanner_name(transport.last_source)}
            )
            self._record_failure(err)
            return self.data
        except (GrundfosError, BleakError, TimeoutError) as err:
            if retry and isinstance(err, (RequestTimeout, ConnectionLost)):
                self.logger.debug("%s: retrying after %s", self.entry.title, err)
                return None
            # Only a refusal at the encryption step counts against the bond. A link that
            # comes up and then goes silent or drops is the radio, and must not ask the
            # user to re-pair: a later poll on the same bond usually works again.
            self._reset_pairing_evidence()
            self._record_failure(err)
            return self.data
        finally:
            self.last_frames = [
                (direction, frame.hex(" ")) for direction, frame in transport.last_frames
            ]
            await transport.disconnect()
        self.last_read = data.read
        self.last_poll_seconds = round(time.monotonic() - started, 2)
        self.last_poll_source = transport.last_source
        self.logger.debug(
            "%s: poll via %s done and disconnected in %.2f s; read %s",
            self.entry.title,
            transport.last_source,
            self.last_poll_seconds,
            ", ".join(sorted(data.read)) or "measurements only",
        )
        self._record_success()
        return data

    async def _read(self, pump: GrundfosPump) -> PumpData:
        """Read the measurements, then whatever optional objects can be had.

        Nothing is carried over from the last poll. A link error during the optional reads
        brings the link up once more and goes on from the object that failed; an object that
        stays silent on the fresh link is skipped for 1, 2, 4 ... 32 polls.
        """
        data = PumpData(measurements=await pump.read_measurements(), polled_at=time.monotonic())
        read: set[str] = set()

        async def operation() -> None:
            data.operation = await pump.read_operation()

        async def stored_setpoint() -> None:
            if data.operation is not None:
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

        # (name, capabilities it needs and provides, read); the stored setpoint, diagnostics
        # only, goes last so that it cannot cost the others their turn.
        steps: tuple[tuple[str, tuple[str, ...], Callable[[], Awaitable[None]]], ...] = (
            (CAP_OPERATION, (CAP_OPERATION,), operation),
            (CAP_ALARMS, (CAP_ALARMS, CAP_WARNINGS), faults),
            (CAP_AUTONIGHT, (CAP_AUTONIGHT,), autonight),
            (CAP_MAX_FLOW_LIMITER, (CAP_MAX_FLOW_LIMITER, CAP_MIN_FLOW_LIMITER), limiters),
            (CAP_STATISTICS, (CAP_STATISTICS,), statistics),
            (CAP_TEMPERATURES, (CAP_TEMPERATURES,), temperatures),
            (CAP_ENERGY, (CAP_ENERGY,), energy),
            ("stored_setpoint", (), stored_setpoint),
        )
        reconnected = False
        for key, capabilities, step in steps:
            if not all(self.has(capability) for capability in capabilities):
                continue
            if self.poll_count < self._skip_until.get(key, 0):
                continue
            try:
                try:
                    await step()
                except GrundfosConnectionError as err:
                    if reconnected:
                        raise
                    reconnected = True
                    self.logger.debug(
                        "%s: %s failed (%s); retrying on a fresh link", self.entry.title, key, err
                    )
                    await pump.transport.connect()
                    try:
                        await step()
                    except RequestTimeout:
                        self._note_silence(key)
                        raise
            except GrundfosConnectionError as err:
                self.logger.debug("%s: keeping a partial read: %s", self.entry.title, err)
                break  # the rest of this poll's optional values stay unread (None)
            except (NotSupported, ProtocolError) as err:
                self._note_object_problem(key, err)
            else:
                read.update(capabilities)
                if self._silent.pop(key, None) is not None:
                    self._skip_until.pop(key, None)
                    self.logger.info("%s: %s answers again", self.entry.title, key)
        data.read = frozenset(read)
        return data

    def _note_silence(self, key: str) -> None:
        """Skip an object that went silent on a fresh link for 1, 2, 4 ... 32 polls."""
        count = self._silent.get(key, 0) + 1
        self._silent[key] = count
        skip = min(2 ** (count - 1), MAX_SILENT_BACKOFF)
        self._skip_until[key] = self.poll_count + 1 + skip
        if count == 1:
            self.logger.info(
                "%s: %s did not answer on a fresh link; skipping it for a while",
                self.entry.title,
                key,
            )

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
            raw = getattr(err, "raw", None)
            self.logger.info(
                "%s: could not read %s, keeping the rest: %s%s",
                self.entry.title,
                capability,
                err,
                f" (frame {raw.hex(' ')})" if isinstance(raw, bytes) else "",
            )

    def _scanner_name(self, source: str | None) -> str:
        """Return the name the user gave the adapter or proxy ``source``, for repair texts."""
        for scanner in bluetooth.async_current_scanners(self.hass):
            if scanner.source == source:
                return str(scanner.name)
        return source or "unknown"

    def _attempt_used_bonded_path(self, source: str | None) -> bool:
        """Whether a refused attempt went through the adapter or proxy that holds the bond.

        The transport reports the path Home Assistant chose for the attempt. Without it, the
        bonded path counts when it at least hears the pump.
        """
        paired = self.entry.data.get(CONF_PAIRED_SOURCE)
        if not paired or paired == "unknown":
            return True
        if source is not None:
            return bool(source == paired)
        return any(
            device.scanner.source == paired
            for device in bluetooth.async_scanner_devices_by_address(
                self.hass, self.address, connectable=True
            )
        )

    def _reset_pairing_evidence(self) -> None:
        self.pairing_failures = 0

    def _note_refusal(self, err: PairingRequired, source: str | None) -> None:
        self._record_failure(err)
        if self.forgetful_proxy:
            return  # the repair issue says what to do; re-pairing alone would loop
        if not self._attempt_used_bonded_path(source):
            # Refused through a path that never held the bond: says nothing about the bond.
            self.pairing_failures = 0
            return
        if self.good_poll_seen and self._recently_paired():
            # A bond that worked and was refused within minutes of pairing: the proxy forgot
            # it (ESP-IDF 5.5.6 does after one session). Re-pairing would only start the loop.
            self.forgetful_proxy = True
            self.pairing_failures = 0
            self._raise_issue(ISSUE_PROXY_FORGETS_BOND, {"proxy": self._scanner_name(source)})
            return
        self.pairing_failures += 1
        if self.pairing_failures >= MAX_PAIRING_FAILURES:
            self._raise_reauth(str(err))

    def _recently_paired(self) -> bool:
        paired_at = dt_util.parse_datetime(str(self.entry.data.get(CONF_PAIRED_AT) or ""))
        if paired_at is None:
            return False
        return (dt_util.utcnow() - paired_at).total_seconds() < FORGETFUL_PROXY_WINDOW

    def _raise_issue(self, issue_id: str, placeholders: dict[str, str]) -> None:
        if issue_id in self._issues:
            return
        self._issues.add(issue_id)
        self.logger.warning("%s: raising repair issue %s", self.entry.title, issue_id)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"{issue_id}_{self.entry.entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=issue_id,
            translation_placeholders={"name": self.entry.title, **placeholders},
        )

    def _clear_issues(self) -> None:
        for issue_id in (ISSUE_PROXY_FORGETS_BOND, ISSUE_PROXY_CANNOT_PAIR, ISSUE_UNREACHABLE):
            ir.async_delete_issue(self.hass, DOMAIN, f"{issue_id}_{self.entry.entry_id}")
        self._issues.clear()

    def _raise_reauth(self, reason: str) -> None:
        if self.reauth_raised or self.entry.state is not ConfigEntryState.LOADED:
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
        now = time.monotonic()
        if self._failing_since is None:
            self._failing_since = now
        elif now - self._failing_since >= OUTAGE_ISSUE_AFTER and self.advertising:
            self._raise_issue(ISSUE_UNREACHABLE, {"error": self.last_error})
        if (
            self.consecutive_failures == FAILURES_BEFORE_UNAVAILABLE
            and not self._unavailable_logged
        ):
            self.logger.info(
                "%s: cannot read the pump (%s); it will show as unavailable until a poll "
                "succeeds. Bluetooth: %s",
                self.entry.title,
                self.last_error,
                self.reachability(),
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
        self._failing_since = None
        if self._issues or not self.good_poll_seen:
            self._clear_issues()  # also those left from before a restart
        self.good_poll_seen = True
        self.forgetful_proxy = False
        self._reset_pairing_evidence()
        if self.reauth_raised:
            self._close_open_reauth()
