"""Entities fed by every optional object, via a pump whose reads are mocked with real types."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.grundfos_ble.const import DOMAIN
from custom_components.grundfos_ble.sensor import _enum_name, _fault
from grundfos_ble import (
    ControlMode,
    ErrorCode,
    Faults,
    FlowLimiter,
    NotSupported,
    OperationMode,
    OperationState,
    Statistics,
    Temperatures,
)
from grundfos_ble.genibus.objects import parse_get_reply
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.pump import decode_measurements
from grundfos_ble.testing import load_fixture

from .conftest import LIB_FIXTURES, PUMP_ADDRESS, FakeProxyScanner


def _measurements() -> Any:
    exchanges = load_fixture(LIB_FIXTURES / "measured-2026-11-09.hex")
    process = parse_get_reply(Telegram.decode(exchanges[0].reply).apdu)[0].data
    motor = parse_get_reply(Telegram.decode(exchanges[1].reply).apdu)[0].data
    return decode_measurements(process, motor)


def _mock_pump(**overrides: Any) -> MagicMock:
    pump = MagicMock()
    pump.read_measurements = AsyncMock(return_value=_measurements())
    pump.read_operation = AsyncMock(
        return_value=OperationState(1, 0, int(ControlMode.CONSTANT_PRESSURE), 24510.0)
    )
    pump.read_faults = AsyncMock(return_value=Faults((int(ErrorCode.MOTOR_BLOCKED),), ()))
    pump.read_autonight = AsyncMock(return_value=True)
    pump.read_flow_limiters = AsyncMock(
        return_value=(FlowLimiter(1, False, 2.0), FlowLimiter(2, True, 0.7))
    )
    pump.read_statistics = AsyncMock(return_value=Statistics(16, 1, 3, 7200, 3600, 1800, 6, 7200))
    pump.read_temperatures = AsyncMock(return_value=Temperatures(21.5, 39.0, 18.0))
    pump.read_energy_kwh = AsyncMock(return_value=12.5)
    pump.read_user_setpoint = AsyncMock(side_effect=NotSupported("no such object"))
    for name, value in overrides.items():
        setattr(pump, name, value)
    return pump


def _enable(
    hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str, object_id: str
) -> None:
    er.async_get(hass).async_get_or_create(
        platform,
        DOMAIN,
        f"{PUMP_ADDRESS}_{key}",
        suggested_object_id=object_id,
        config_entry=entry,
        disabled_by=None,
    )


async def test_all_optional_objects_feed_entities(
    hass: HomeAssistant, config_entry_full: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    config_entry_full.add_to_hass(hass)
    for platform, key, object_id in (
        ("sensor", "operation_mode", "test_pump_operation_mode"),
        ("sensor", "max_flow_limit", "test_pump_maximum_flow_limit"),
        ("sensor", "min_flow_limit", "test_pump_minimum_flow_limit"),
        ("sensor", "starts_last_24h", "test_pump_starts_24h"),
        ("sensor", "pcb_temperature", "test_pump_pcb_temperature"),
        ("sensor", "signal_strength", "test_pump_signal_strength"),
        ("binary_sensor", "autonight", "test_pump_autonight"),
        ("binary_sensor", "max_flow_limiter", "test_pump_maximum_flow_limiter"),
        ("binary_sensor", "min_flow_limiter", "test_pump_minimum_flow_limiter"),
    ):
        _enable(hass, config_entry_full, platform, key, object_id)
    transport = MagicMock()
    transport.connect = AsyncMock()
    transport.disconnect = AsyncMock()
    transport.last_frames = []
    with (
        patch("custom_components.grundfos_ble.coordinator.BleTransport", return_value=transport),
        patch("custom_components.grundfos_ble.coordinator.GrundfosPump", return_value=_mock_pump()),
    ):
        assert await hass.config_entries.async_setup(config_entry_full.entry_id)
        await hass.async_block_till_done()
        proxy.inject()
        await hass.async_block_till_done()

    def state(entity_id: str) -> str:
        found = hass.states.get(entity_id)
        assert found is not None, entity_id
        return found.state

    assert state("sensor.test_pump_control_mode") == "constant_pressure"
    assert state("sensor.test_pump_operation_mode") == "auto"
    setpoint = hass.states.get("sensor.test_pump_setpoint")
    assert setpoint is not None
    assert float(setpoint.state) == pytest.approx(2.4993, abs=0.001)
    assert setpoint.attributes["unit_of_measurement"] == "m"
    assert state("sensor.test_pump_alarm_code") == "motor_blocked"
    assert state("sensor.test_pump_warning_code") == "none"
    alarm = hass.states.get("sensor.test_pump_alarm_code")
    assert alarm is not None
    assert alarm.attributes["codes"] == [51]
    assert float(state("sensor.test_pump_maximum_flow_limit")) == 2.0
    assert float(state("sensor.test_pump_minimum_flow_limit")) == 0.7
    assert state("sensor.test_pump_start_count") == "16"
    assert float(state("sensor.test_pump_operating_time_since_commissioning")) == pytest.approx(
        2.0
    )  # 7200 s shown in h
    assert state("sensor.test_pump_starts_24h") == "3"
    assert float(state("sensor.test_pump_pcb_temperature")) == 39.0
    assert float(state("sensor.test_pump_energy")) == 12.5
    assert float(state("sensor.test_pump_media_temperature")) == 21.5
    assert state("sensor.test_pump_signal_strength") == "-67"
    assert state("binary_sensor.test_pump_autonight") == "on"
    assert float(state("sensor.test_pump_current_run_time")) == pytest.approx(0.5)
    assert config_entry_full.runtime_data.data.stored_setpoint is None
    registry = er.async_get(hass)
    for object_id in ("start_count", "operating_time_since_commissioning"):
        entry = registry.async_get(f"sensor.test_pump_{object_id}")
        assert entry is not None
        assert entry.entity_category is EntityCategory.DIAGNOSTIC
    assert state("binary_sensor.test_pump_maximum_flow_limiter") == "off"
    assert state("binary_sensor.test_pump_minimum_flow_limiter") == "on"


async def test_recovery_after_failures_is_logged_once(
    hass: HomeAssistant,
    config_entry_full: MockConfigEntry,
    proxy: FakeProxyScanner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_entry_full.add_to_hass(hass)
    failing = True

    async def connect() -> None:
        if failing:
            raise TimeoutError

    transport = MagicMock()
    transport.connect = connect
    transport.disconnect = AsyncMock()
    transport.last_frames = []
    with (
        patch("custom_components.grundfos_ble.coordinator.BleTransport", return_value=transport),
        patch("custom_components.grundfos_ble.coordinator.GrundfosPump", return_value=_mock_pump()),
    ):
        assert await hass.config_entries.async_setup(config_entry_full.entry_id)
        await hass.async_block_till_done()
        proxy.inject()
        await hass.async_block_till_done()
        coordinator = config_entry_full.runtime_data
        for _ in range(3):
            if not coordinator.poll_ok:
                break
            await coordinator._async_poll()
        assert not coordinator.poll_ok
        flow = hass.states.get("sensor.test_pump_flow")
        assert flow is not None
        assert flow.state == "unavailable"
        failing = False
        await coordinator._async_poll()
        await hass.async_block_till_done()
    assert coordinator.poll_ok
    assert coordinator.consecutive_failures == 0
    assert caplog.text.count("cannot read the pump") == 1
    assert caplog.text.count("reachable again") == 1
    flow = hass.states.get("sensor.test_pump_flow")
    assert flow is not None
    assert flow.state != "unavailable"


def test_enum_helpers() -> None:
    assert _enum_name(ControlMode, None) is None
    assert _enum_name(ControlMode, 8) == "constant_flow"
    assert _enum_name(OperationMode, 99) == "unrecognised"
    assert _fault(None) is None
    assert _fault(()) == "none"
    assert _fault((999,)) == "unrecognised"
    assert _fault((57, 51)) == "dry_running"
