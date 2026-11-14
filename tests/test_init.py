"""Setup, polling, availability, reauth on lost pairing, unload and options reload."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from bluetooth_data_tools import monotonic_time_coarse
from habluetooth import get_manager
from homeassistant.components import bluetooth
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.grundfos_ble.const import (
    ADVERT_STALE_SECONDS,
    DOMAIN,
    FAILURES_BEFORE_UNAVAILABLE,
    MAX_PAIRING_FAILURES,
    OPT_SCAN_INTERVAL,
    RETRY_PAUSE,
)
from grundfos_ble import ConnectionLost, GrundfosConnectionError, PairingRequired, RequestTimeout
from grundfos_ble.testing import ReplayTransport

from .conftest import (
    ENTRY_DATA,
    PUMP_ADDRESS,
    PUMP_NAME,
    SERVICE_DATA_BUTTON,
    FakeProxyScanner,
)


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, transport_factory: Callable[..., Any]
) -> None:
    entry.add_to_hass(hass)
    with patch(
        "custom_components.grundfos_ble.coordinator.BleTransport", side_effect=transport_factory
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_setup_without_pump_in_range_leaves_entities_unavailable(
    hass: HomeAssistant, config_entry: MockConfigEntry, replay_transport_factory: Callable[..., Any]
) -> None:
    await _setup(hass, config_entry, replay_transport_factory)
    assert config_entry.state is ConfigEntryState.LOADED
    state = hass.states.get("sensor.test_pump_flow")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    connected = hass.states.get("binary_sensor.test_pump_connected")
    assert connected is not None
    assert connected.state == "off"
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, PUMP_ADDRESS), config_entry.entry_id
    )
    assert device is not None
    assert device.manufacturer == "Grundfos"
    assert device.model == "ALPHA2 GO XX-75"
    assert device.serial_number == "12345678"


async def test_advert_triggers_poll_and_populates_sensors(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
) -> None:
    config_entry_basic.add_to_hass(hass)
    with patch(
        "custom_components.grundfos_ble.coordinator.BleTransport",
        side_effect=replay_transport_factory,
    ):
        assert await hass.config_entries.async_setup(config_entry_basic.entry_id)
        await hass.async_block_till_done()
        proxy.inject()
        await hass.async_block_till_done()

    coordinator = config_entry_basic.runtime_data
    assert coordinator.poll_count == 1
    assert coordinator.last_poll_successful
    flow = hass.states.get("sensor.test_pump_flow")
    assert flow is not None
    assert float(flow.state) == pytest.approx(0.995, abs=0.005)
    assert flow.attributes["unit_of_measurement"] == "m³/h"
    head = hass.states.get("sensor.test_pump_head")
    assert head is not None
    assert float(head.state) == pytest.approx(0.434, abs=0.001)
    power = hass.states.get("sensor.test_pump_power")
    assert power is not None
    assert float(power.state) == pytest.approx(4.01, abs=0.01)
    speed = hass.states.get("sensor.test_pump_speed")
    assert speed is not None
    assert float(speed.state) == pytest.approx(1658, abs=1)
    temp = hass.states.get("sensor.test_pump_electronics_temperature")
    assert temp is not None
    assert float(temp.state) == 39.0
    # Entities gated on capabilities this pump lacks are not created at all.
    assert hass.states.get("sensor.test_pump_control_mode") is None
    connected = hass.states.get("binary_sensor.test_pump_connected")
    assert connected is not None
    assert connected.state == "on"
    # Disabled-by-default entities are registered but not created.
    registry = er.async_get(hass)
    dc = registry.async_get("sensor.test_pump_dc_link_voltage")
    assert dc is not None
    assert dc.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get("sensor.test_pump_dc_link_voltage") is None
    assert coordinator.last_frames[0][0] == "tx"


async def test_partial_read_keeps_measurements(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
) -> None:
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.grundfos_ble.coordinator.BleTransport",
        side_effect=replay_transport_factory,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        proxy.inject()
        await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    # The replay cannot answer the operation object: the poll keeps the measurements.
    assert coordinator.consecutive_failures == 0
    assert coordinator.data.measurements is not None
    assert coordinator.data.operation is None


class _FailingTransport(ReplayTransport):
    def __init__(self, error: Exception) -> None:
        super().__init__([])
        self._error = error

    async def connect(self) -> None:
        raise self._error


async def test_repeated_failures_make_entities_unavailable_and_log_once(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    proxy: FakeProxyScanner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.grundfos_ble.coordinator.BleTransport",
        side_effect=lambda *_a, **_k: _FailingTransport(RequestTimeout("silent")),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = config_entry.runtime_data
        proxy.inject()
        await hass.async_block_till_done()
        for _ in range(FAILURES_BEFORE_UNAVAILABLE + 1):
            await coordinator._async_poll()
            await hass.async_block_till_done()
    assert coordinator.consecutive_failures >= FAILURES_BEFORE_UNAVAILABLE
    assert not coordinator.poll_ok
    assert caplog.text.count("cannot read the pump") == 1


async def test_lost_pairing_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.grundfos_ble.coordinator.BleTransport",
        side_effect=lambda *_a, **_k: _FailingTransport(PairingRequired("refused", 82)),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = config_entry.runtime_data
        proxy.inject()
        await hass.async_block_till_done()
        for _ in range(MAX_PAIRING_FAILURES - 1):
            await coordinator._async_poll()
        await hass.async_block_till_done()
    assert coordinator.pairing_failures >= MAX_PAIRING_FAILURES
    flows = [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"]["source"] == SOURCE_REAUTH
    ]
    assert len(flows) == 1


async def test_stored_setpoint_is_read_for_the_running_mode(
    hass: HomeAssistant,
    config_entry_full: MockConfigEntry,
    proxy: FakeProxyScanner,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from grundfos_ble import ControlMode, OperationState, UserSetpoint

    from .test_sensor import _mock_pump

    stored = UserSetpoint(6, 1.3889e-4, 0.6, 1.6, 0.4)
    pump = _mock_pump(read_user_setpoint=AsyncMock(return_value=stored))
    pump.read_operation = AsyncMock(
        return_value=OperationState(1, 0, int(ControlMode.CONSTANT_FLOW), 2.7778e-4)
    )
    transport = MagicMock()
    transport.connect = AsyncMock()
    transport.disconnect = AsyncMock()
    transport.last_frames = []
    config_entry_full.add_to_hass(hass)
    with (
        patch("custom_components.grundfos_ble.coordinator.BleTransport", return_value=transport),
        patch("custom_components.grundfos_ble.coordinator.GrundfosPump", return_value=pump),
    ):
        assert await hass.config_entries.async_setup(config_entry_full.entry_id)
        await hass.async_block_till_done()
        proxy.inject()
        await hass.async_block_till_done()
    assert config_entry_full.runtime_data.data.stored_setpoint is stored
    pump.read_user_setpoint.assert_awaited_once_with(ControlMode.CONSTANT_FLOW)


async def test_entry_updates_do_not_reload_and_interval_applies_live(
    hass: HomeAssistant, config_entry: MockConfigEntry, replay_transport_factory: Callable[..., Any]
) -> None:
    """Regression: discovery updating the entry reloaded the integration."""
    await _setup(hass, config_entry, replay_transport_factory)
    coordinator = config_entry.runtime_data
    hass.config_entries.async_update_entry(config_entry, options={OPT_SCAN_INTERVAL: 30})
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.runtime_data is coordinator
    assert coordinator.scan_interval == 30
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_no_second_poll_is_scheduled_during_a_poll(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
) -> None:
    config_entry_basic.add_to_hass(hass)
    with patch(
        "custom_components.grundfos_ble.coordinator.BleTransport",
        side_effect=replay_transport_factory,
    ):
        assert await hass.config_entries.async_setup(config_entry_basic.entry_id)
        await hass.async_block_till_done()
        proxy.inject()
        await hass.async_block_till_done()
        coordinator = config_entry_basic.runtime_data
        assert coordinator.poll_count == 1
        assert coordinator._polling is False
        coordinator._polling = True
        coordinator._last_poll = monotonic_time_coarse() - 120
        async_fire_time_changed(hass, utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done(wait_background_tasks=True)
        assert coordinator.poll_count == 1
        coordinator._polling = False


def test_translations_have_no_key_references() -> None:
    """Custom integrations get no build step; [%key:...] references would show verbatim."""
    import json
    from pathlib import Path

    base = Path(__file__).parent.parent / "custom_components" / "grundfos_ble"
    for name in ("strings.json", "translations/en.json"):
        text = (base / name).read_text(encoding="utf-8")
        assert "[%key:" not in text, name
        json.loads(text)
    assert (base / "strings.json").read_text() == (base / "translations/en.json").read_text()


async def test_timer_polls_although_identical_adverts_are_filtered(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
) -> None:
    """Regression: the pump's unchanging advert polled once per setup, then never again."""
    config_entry_basic.add_to_hass(hass)
    with patch(
        "custom_components.grundfos_ble.coordinator.BleTransport",
        side_effect=replay_transport_factory,
    ):
        assert await hass.config_entries.async_setup(config_entry_basic.entry_id)
        await hass.async_block_till_done()
        proxy.inject()
        await hass.async_block_till_done()
        coordinator = config_entry_basic.runtime_data
        assert coordinator.poll_count == 1

        # Not due yet: a tick changes nothing.
        async_fire_time_changed(hass, utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done()
        assert coordinator.poll_count == 1

        # Due: no new advert arrives, the tick alone polls.
        coordinator._last_poll = monotonic_time_coarse() - 61
        async_fire_time_changed(hass, utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done(wait_background_tasks=True)
        assert coordinator.poll_count == 2


async def test_first_advert_during_startup_is_polled_once_running(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
) -> None:
    """Regression: an advert seen while HA starts never polled, as repeats were filtered."""
    config_entry_basic.add_to_hass(hass)
    hass.set_state(CoreState.starting)
    with patch(
        "custom_components.grundfos_ble.coordinator.BleTransport",
        side_effect=replay_transport_factory,
    ):
        assert await hass.config_entries.async_setup(config_entry_basic.entry_id)
        await hass.async_block_till_done()
        proxy.inject()
        await hass.async_block_till_done()
        coordinator = config_entry_basic.runtime_data
        assert coordinator.poll_count == 0
        hass.set_state(CoreState.running)
        async_fire_time_changed(hass, utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done(wait_background_tasks=True)
        assert coordinator.poll_count == 1


async def test_tick_does_nothing_without_the_pump(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    replay_transport_factory: Callable[..., Any],
) -> None:
    await _setup(hass, config_entry_basic, replay_transport_factory)
    coordinator = config_entry_basic.runtime_data
    async_fire_time_changed(hass, utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    assert coordinator.poll_count == 0
    coordinator._available = True  # available, but nothing in the advert history
    async_fire_time_changed(hass, utcnow() + timedelta(seconds=12))
    await hass.async_block_till_done()
    assert coordinator.poll_count == 0


COORDINATOR_TRANSPORT = "custom_components.grundfos_ble.coordinator.BleTransport"


def _failing(error: Exception) -> Callable[..., Any]:
    return lambda *_a, **_k: _FailingTransport(error)


class _SilentTransport(ReplayTransport):
    """Connects, encrypts, then never answers."""


class _DroppingTransport(ReplayTransport):
    """Connects, encrypts, then loses the link on the first request."""

    async def request(self, telegram: Any, *, timeout: float | None = None) -> Any:
        del telegram, timeout
        raise ConnectionLost("disconnected")


async def _loaded(hass: HomeAssistant, entry: MockConfigEntry, proxy: FakeProxyScanner) -> Any:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    proxy.inject()
    await hass.async_block_till_done()
    return entry.runtime_data


def _reauth_flows(hass: HomeAssistant) -> list[Any]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"]["source"] == SOURCE_REAUTH
    ]


async def test_reauth_warns_once_backs_off_and_closes_when_polls_recover(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch(COORDINATOR_TRANSPORT, side_effect=_failing(PairingRequired("refused", 82))):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
        for _ in range(MAX_PAIRING_FAILURES + 3):
            await coordinator._async_poll()
        await hass.async_block_till_done()
    assert coordinator.reauth_raised
    assert caplog.text.count("needs pairing again") == 1
    assert len(_reauth_flows(hass)) == 1
    info = coordinator._last_service_info
    assert not coordinator._needs_poll(info, 120)
    assert coordinator._needs_poll(info, 601)

    with patch(COORDINATOR_TRANSPORT, side_effect=replay_transport_factory):
        await coordinator._async_poll()
        await hass.async_block_till_done()
    assert not coordinator.reauth_raised
    assert _reauth_flows(hass) == []
    assert coordinator.pairing_failures == 0


async def test_refusals_through_a_proxy_without_the_bond_raise_no_repair(
    hass: HomeAssistant, proxy: FakeProxyScanner
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PUMP_NAME,
        unique_id=PUMP_ADDRESS,
        data={**ENTRY_DATA, "capabilities": [], "paired_source": "11:22:33:44:55:66"},
    )
    with patch(COORDINATOR_TRANSPORT, side_effect=_failing(PairingRequired("refused", 82))):
        coordinator = await _loaded(hass, entry, proxy)
        for _ in range(MAX_PAIRING_FAILURES + 1):
            await coordinator._async_poll()
        await hass.async_block_till_done()
    assert coordinator.pairing_failures == 0
    assert _reauth_flows(hass) == []


async def test_other_failures_reset_the_refusal_count(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    errors = iter(
        [
            PairingRequired("refused", 82),
            PairingRequired("refused", 82),
            GrundfosConnectionError("no slot"),
            PairingRequired("refused", 82),
        ]
    )
    with patch(
        COORDINATOR_TRANSPORT, side_effect=lambda *_a, **_k: _FailingTransport(next(errors))
    ):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
        for _ in range(3):
            await coordinator._async_poll()
        await hass.async_block_till_done()
    assert coordinator.pairing_failures == 1
    assert _reauth_flows(hass) == []


@pytest.mark.parametrize("transport", [_SilentTransport, _DroppingTransport])
async def test_silent_or_dropped_links_never_ask_for_re_pairing(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    transport: type[ReplayTransport],
) -> None:
    """Regression (ha-grundfos-ble#3): a flaky local adapter raised the repair over and over.

    The bond was fine, since the next good poll worked without a button press.
    """
    with patch(COORDINATOR_TRANSPORT, side_effect=lambda *_a, **_k: transport([])):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
        for _ in range(MAX_PAIRING_FAILURES + 2):
            await coordinator._async_poll()
        await hass.async_block_till_done()
    assert coordinator.consecutive_failures == MAX_PAIRING_FAILURES + 3
    assert coordinator.pairing_failures == 0
    assert not coordinator.reauth_raised
    assert _reauth_flows(hass) == []


async def test_a_timeout_is_retried_once_on_a_fresh_connection(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
) -> None:
    transports = iter([_SilentTransport([]), replay_transport_factory()])
    pauses: list[float] = []
    real_sleep = asyncio.sleep

    def sleep(delay: float, *args: Any, **kwargs: Any) -> Any:
        pauses.append(delay)
        return real_sleep(delay, *args, **kwargs)

    with (
        patch(COORDINATOR_TRANSPORT, side_effect=lambda *_a, **_k: next(transports)),
        patch("asyncio.sleep", side_effect=sleep),
    ):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
    assert RETRY_PAUSE in pauses  # the stack settles before the fresh link
    assert coordinator.poll_count == 1
    assert coordinator.consecutive_failures == 0
    assert coordinator.data.measurements is not None


async def test_polling_waits_for_pairing_and_for_a_pressed_button(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
) -> None:
    with patch(COORDINATOR_TRANSPORT, side_effect=replay_transport_factory):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
    info = coordinator._last_service_info
    assert coordinator._needs_poll(info, 120)
    coordinator.pause_polling()
    assert not coordinator._needs_poll(info, 120)
    coordinator.resume_polling()
    proxy.inject(service_data=SERVICE_DATA_BUTTON)
    await hass.async_block_till_done()
    assert not coordinator._needs_poll(info, 120)


async def test_stale_adverts_clear_the_readings(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch(COORDINATOR_TRANSPORT, side_effect=replay_transport_factory):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
    assert coordinator.connected
    later = monotonic_time_coarse() + ADVERT_STALE_SECONDS + 1
    with patch(
        "custom_components.grundfos_ble.coordinator.monotonic_time_coarse", return_value=later
    ):
        async_fire_time_changed(hass, utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done()
    assert not coordinator.advertising
    assert coordinator.data.measurements is None
    assert caplog.text.count("stopped advertising") == 1
    state = hass.states.get("sensor.test_pump_flow")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_scanner_expiry_also_clears_the_readings(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
) -> None:
    with patch(COORDINATOR_TRANSPORT, side_effect=replay_transport_factory):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
    coordinator._async_handle_unavailable(coordinator._last_service_info)
    assert coordinator.data.measurements is None
    assert not coordinator.advertising


async def test_recovers_when_the_stack_sends_no_event_after_an_outage(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
) -> None:
    """Regression: after a long power-off the entities stayed unavailable with adverts flowing.

    When a non-connectable scanner hears the pump first, the connectable advert that follows
    is stored without a callback, and the unchanging payload filters every later one.
    """
    listener = FakeProxyScanner("AA:BB:CC:DD:EE:02", "listener", None, False)
    unregister = bluetooth.async_register_scanner(hass, listener)
    with patch(COORDINATOR_TRANSPORT, side_effect=replay_transport_factory):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
        polls = coordinator.poll_count

        # The pump loses power for long enough that Home Assistant forgets it.
        manager = get_manager()
        info = manager._connectable_history.pop(PUMP_ADDRESS)
        manager._all_history.pop(PUMP_ADDRESS)
        coordinator._async_handle_unavailable(info)
        assert not coordinator.available

        # Power returns: the listener is heard first, then the proxy at a weaker signal.
        listener.inject(rssi=-50)
        proxy.inject(rssi=-70)
        await hass.async_block_till_done()
        assert not coordinator.available  # no event reached the coordinator
        assert bluetooth.async_last_service_info(hass, PUMP_ADDRESS, connectable=True) is not None

        coordinator._last_poll = monotonic_time_coarse() - 61
        async_fire_time_changed(hass, utcnow() + timedelta(seconds=6))
        await hass.async_block_till_done()
        assert coordinator.available

        # The poll debouncer is still cooling down from the poll in _loaded.
        async_fire_time_changed(hass, utcnow() + timedelta(seconds=30))
        await hass.async_block_till_done(wait_background_tasks=True)
    unregister()
    assert coordinator.poll_count == polls + 1
    assert coordinator.connected
    state = hass.states.get("sensor.test_pump_flow")
    assert state is not None
    assert state.state != STATE_UNAVAILABLE


async def test_stays_unavailable_while_only_a_non_connectable_scanner_hears_the_pump(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    replay_transport_factory: Callable[..., Any],
) -> None:
    listener = FakeProxyScanner("AA:BB:CC:DD:EE:02", "listener", None, False)
    unregister = bluetooth.async_register_scanner(hass, listener)
    await _setup(hass, config_entry_basic, replay_transport_factory)
    coordinator = config_entry_basic.runtime_data
    listener.inject()
    await hass.async_block_till_done()
    async_fire_time_changed(hass, utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    unregister()
    assert not coordinator.available
    assert coordinator.poll_count == 0


async def test_unexpected_exception_counts_as_a_failure(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    with patch(COORDINATOR_TRANSPORT, side_effect=_failing(RuntimeError("boom"))):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
    assert coordinator.consecutive_failures == 1
    assert "RuntimeError" in (coordinator.last_error or "")
    assert coordinator._polling is False


async def test_ordinary_connection_failure_counts_as_a_failure(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    with patch(COORDINATOR_TRANSPORT, side_effect=_failing(GrundfosConnectionError("no slot"))):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
    assert coordinator.consecutive_failures == 1
    assert coordinator.pairing_failures == 0


async def test_two_pumps_run_independently(
    hass: HomeAssistant,
    proxy: FakeProxyScanner,
    replay_transport_factory: Callable[..., Any],
) -> None:
    other_address = "AA:BB:CC:DD:12:35"
    first = MockConfigEntry(
        domain=DOMAIN,
        title="Boiler",
        unique_id=PUMP_ADDRESS,
        data={**ENTRY_DATA, "capabilities": []},
    )
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Radiators",
        unique_id=other_address,
        data={**ENTRY_DATA, "address": other_address, "capabilities": []},
    )
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    with patch(COORDINATOR_TRANSPORT, side_effect=replay_transport_factory):
        assert await hass.config_entries.async_setup(first.entry_id)
        await hass.async_block_till_done()
        proxy.inject()
        proxy.inject(other_address, name="Radiators")
        await hass.async_block_till_done()
    assert first.state is ConfigEntryState.LOADED
    assert second.state is ConfigEntryState.LOADED
    assert first.runtime_data is not second.runtime_data
    assert first.runtime_data.poll_count == 1
    assert second.runtime_data.poll_count == 1
    assert hass.states.get("sensor.boiler_flow") is not None
    assert hass.states.get("sensor.radiators_flow") is not None
    first.runtime_data.pause_polling()
    assert not first.runtime_data._needs_poll(first.runtime_data._last_service_info, 120)
    assert second.runtime_data._needs_poll(second.runtime_data._last_service_info, 120)
