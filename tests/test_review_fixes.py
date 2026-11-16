"""Fixes from the 2026-11 review: stale optional data, proxies, repair issues, unload, privacy."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import time
from typing import Any
from unittest.mock import patch

from bluetooth_data_tools import monotonic_time_coarse
from homeassistant import config_entries
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant.common import MockConfigEntry

from custom_components.grundfos_ble.const import (
    CONF_CAPABILITIES,
    CONF_PAIRED_AT,
    DOMAIN,
    FAILURES_BEFORE_UNAVAILABLE,
    ISSUE_PROXY_CANNOT_PAIR,
    ISSUE_PROXY_FORGETS_BOND,
    ISSUE_UNREACHABLE,
    MAX_PAIRING_FAILURES,
    OUTAGE_ISSUE_AFTER,
    POLL_ADVERT_MAX_AGE,
)
from custom_components.grundfos_ble.diagnostics import async_get_config_entry_diagnostics
from grundfos_ble import PairingRequired, PairingUnsupported, RequestTimeout
from grundfos_ble.genibus.apdu import Apdu
from grundfos_ble.genibus.objects import ObjectRef, get_request
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.testing import FIXTURES as LIB_FIXTURES
from grundfos_ble.testing import Exchange, load_fixture

from .conftest import (
    ALL_CAPABILITIES,
    ENTRY_DATA,
    PROXY_NAME,
    PROXY_SOURCE,
    PUMP_ADDRESS,
    PUMP_NAME,
    SERVICE_DATA_OTHER_PUMP,
    FakeProxyScanner,
    ReplayTransport,
)
from .test_init import COORDINATOR_TRANSPORT, _FailingTransport, _loaded, _reauth_flows

# The pump's recorded answers, plus the constant-flow setpoint object (86/40) the pump runs in
# that recording, with its data as captured from the GO app.
STORED_SETPOINT = Exchange(
    Telegram.request(get_request(ObjectRef(86, 40))).encode(),
    Telegram(
        0x24,
        0xF8,
        0xE7,
        (
            Apdu(
                10,
                0,
                bytes.fromhex(
                    "00 01 2e 01 00 00 12 00 06 3a 11 a2 b4 3f 19 99 9a 3f cc cc cd 3e cc cc cd"
                ),
            ),
        ),
    ).encode(),
)
FULL = [*load_fixture(LIB_FIXTURES / "measured-2026-11-09-full.hex"), STORED_SETPOINT]
ALARMS_REQUEST = Telegram.request(get_request(ObjectRef(88, 0))).encode()


def _full(**faults: Any) -> Callable[..., ReplayTransport]:
    def factory(*_args: Any, **_kwargs: Any) -> ReplayTransport:
        transport = ReplayTransport(list(FULL))
        for name, value in faults.items():
            setattr(transport, name, value)
        return transport

    return factory


def _issue(hass: HomeAssistant, entry: MockConfigEntry, issue_id: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{issue_id}_{entry.entry_id}")


def test_two_misses_are_still_noise() -> None:
    assert FAILURES_BEFORE_UNAVAILABLE == 3


# --- D1: no stale optional data ---


async def test_an_object_that_goes_silent_shows_unavailable_not_its_old_value(
    hass: HomeAssistant, config_entry_full: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        coordinator = await _loaded(hass, config_entry_full, proxy)
    alarm = hass.states.get("sensor.test_pump_alarm_code")
    assert alarm is not None
    assert alarm.state == "none"
    assert {"alarms", "statistics", "energy"} <= coordinator.data.read

    with patch(COORDINATOR_TRANSPORT, side_effect=_full(silent_on={ALARMS_REQUEST})):
        await coordinator._async_poll()
        await hass.async_block_till_done()
    assert coordinator.consecutive_failures == 0  # the measurements were read
    assert coordinator.data.faults is None
    assert "alarms" not in coordinator.data.read
    alarm = hass.states.get("sensor.test_pump_alarm_code")
    assert alarm is not None
    assert alarm.state == STATE_UNAVAILABLE
    # Silent on a fresh link too: the rest of this poll stays unread, not stale.
    assert coordinator.data.energy_kwh is None
    energy = hass.states.get("sensor.test_pump_energy")
    assert energy is not None
    assert energy.state == STATE_UNAVAILABLE

    with patch(COORDINATOR_TRANSPORT, side_effect=_full(silent_on={ALARMS_REQUEST})):
        await coordinator._async_poll()
        await hass.async_block_till_done()
    # Next poll the silent object is skipped and everything else is read again.
    assert coordinator.data.energy_kwh is not None
    assert coordinator.data.faults is None


async def test_a_silent_object_is_skipped_with_a_growing_back_off(
    hass: HomeAssistant,
    config_entry_full: MockConfigEntry,
    proxy: FakeProxyScanner,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sent: list[list[bytes]] = []

    def factory(*_args: Any, **_kwargs: Any) -> ReplayTransport:
        transport = _full(silent_on={ALARMS_REQUEST})()
        sent.append(transport.sent)
        return transport

    with patch(COORDINATOR_TRANSPORT, side_effect=factory):
        coordinator = await _loaded(hass, config_entry_full, proxy)
        for _ in range(4):
            await coordinator._async_poll()
    asked = [ALARMS_REQUEST in frames for frames in sent]
    assert asked == [True, False, True, False, False]  # skip 1 poll, then 2
    assert caplog.text.count("did not answer on a fresh link") == 1


async def test_a_lost_link_is_brought_back_once_and_the_read_goes_on(
    hass: HomeAssistant, config_entry_full: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    class SilentOnce(ReplayTransport):
        async def request(self, telegram: Telegram, **kwargs: Any) -> Telegram:
            if telegram.encode() == ALARMS_REQUEST and not self.connects:
                self.closed = True
                raise RequestTimeout("silent")
            return await super().request(telegram, **kwargs)

    with patch(COORDINATOR_TRANSPORT, side_effect=lambda *_a, **_k: SilentOnce(list(FULL))):
        coordinator = await _loaded(hass, config_entry_full, proxy)
    assert coordinator.data.faults is not None
    assert "alarms" in coordinator.data.read


# --- C2: refusals count only through the path that made the attempt ---


async def test_a_refusal_through_another_proxy_does_not_count(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    """The bonded proxy hears the pump, but the attempt went elsewhere (live M2)."""

    class Elsewhere(_FailingTransport):
        last_source = "66:77:88:99:AA:BB"

    with patch(
        COORDINATOR_TRANSPORT,
        side_effect=lambda *_a, **_k: Elsewhere(PairingRequired("refused", 82)),
    ):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
        for _ in range(MAX_PAIRING_FAILURES + 1):
            await coordinator._async_poll()
        await hass.async_block_till_done()
    assert coordinator.pairing_failures == 0
    assert _reauth_flows(hass) == []


# --- C1: a proxy that forgets the bond after one session ---


async def test_a_bond_forgotten_right_after_pairing_raises_an_issue_not_a_reauth(
    hass: HomeAssistant, proxy: FakeProxyScanner
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PUMP_NAME,
        unique_id=PUMP_ADDRESS,
        data={**ENTRY_DATA, CONF_CAPABILITIES: [], CONF_PAIRED_AT: dt_util.utcnow().isoformat()},
    )
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        coordinator = await _loaded(hass, entry, proxy)
    assert coordinator.good_poll_seen
    with patch(
        COORDINATOR_TRANSPORT,
        side_effect=lambda *_a, **_k: _FailingTransport(PairingRequired("refused", 82)),
    ):
        for _ in range(MAX_PAIRING_FAILURES + 2):
            await coordinator._async_poll()
        await hass.async_block_till_done()
    assert coordinator.forgetful_proxy
    assert _reauth_flows(hass) == []
    issue = _issue(hass, entry, ISSUE_PROXY_FORGETS_BOND)
    assert issue is not None
    assert not issue.is_fixable
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["proxy"].startswith(PROXY_NAME)

    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        await coordinator._async_poll()
    assert _issue(hass, entry, ISSUE_PROXY_FORGETS_BOND) is None


async def test_an_old_pairing_refused_later_still_asks_to_re_pair(
    hass: HomeAssistant, proxy: FakeProxyScanner
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PUMP_NAME,
        unique_id=PUMP_ADDRESS,
        data={**ENTRY_DATA, CONF_CAPABILITIES: [], CONF_PAIRED_AT: "2026-01-01T00:00:00+00:00"},
    )
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        coordinator = await _loaded(hass, entry, proxy)
    with patch(
        COORDINATOR_TRANSPORT,
        side_effect=lambda *_a, **_k: _FailingTransport(PairingRequired("refused", 82)),
    ):
        for _ in range(MAX_PAIRING_FAILURES):
            await coordinator._async_poll()
        await hass.async_block_till_done()
    assert not coordinator.forgetful_proxy
    assert len(_reauth_flows(hass)) == 1


# --- B5 and C3: repair issues ---


async def test_a_proxy_that_cannot_pair_raises_an_issue(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    with patch(
        COORDINATOR_TRANSPORT,
        side_effect=lambda *_a, **_k: _FailingTransport(PairingUnsupported("no pairing")),
    ):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
    issue = _issue(hass, config_entry_basic, ISSUE_PROXY_CANNOT_PAIR)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["proxy"].startswith(PROXY_NAME)
    assert coordinator.pairing_failures == 0
    assert _reauth_flows(hass) == []


async def test_an_hour_of_failures_while_heard_raises_an_issue(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    with patch(
        COORDINATOR_TRANSPORT,
        side_effect=lambda *_a, **_k: _FailingTransport(RequestTimeout("silent")),
    ):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
        assert _issue(hass, config_entry_basic, ISSUE_UNREACHABLE) is None
        coordinator._failing_since = time.monotonic() - OUTAGE_ISSUE_AFTER - 1
        await coordinator._async_poll()
    issue = _issue(hass, config_entry_basic, ISSUE_UNREACHABLE)
    assert issue is not None
    assert not issue.is_fixable
    assert _reauth_flows(hass) == []
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        await coordinator._async_poll()
    assert _issue(hass, config_entry_basic, ISSUE_UNREACHABLE) is None


# --- E1: unload and the pairing flow ---


async def test_unload_cancels_a_running_poll_and_raises_no_reauth(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    started = asyncio.Event()

    class Hanging(ReplayTransport):
        async def connect(self) -> None:
            started.set()
            await asyncio.sleep(3600)

    with patch(COORDINATOR_TRANSPORT, side_effect=lambda *_a, **_k: Hanging(list(FULL))):
        config_entry_basic.add_to_hass(hass)
        assert await hass.config_entries.async_setup(config_entry_basic.entry_id)
        await hass.async_block_till_done()
        coordinator = config_entry_basic.runtime_data
        proxy.inject()
        await asyncio.wait_for(started.wait(), 5)
        assert not coordinator._idle.is_set()
        assert await hass.config_entries.async_unload(config_entry_basic.entry_id)
        await hass.async_block_till_done()
    assert coordinator._idle.is_set()
    coordinator._raise_reauth("late")
    assert not coordinator.reauth_raised
    assert _reauth_flows(hass) == []


async def test_wait_idle_returns_at_once_when_no_poll_runs(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
    await asyncio.wait_for(coordinator.async_wait_idle(), 1)


# --- E2: diagnostics never name the user's devices ---


async def test_diagnostics_name_no_scanner_and_not_the_pump(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
    assert PROXY_NAME in coordinator.reachability()  # what HA itself reports
    coordinator.last_error = f"BleakError: {PROXY_NAME}: no slot for {PUMP_NAME}"
    result = await async_get_config_entry_diagnostics(hass, config_entry_basic)
    text = str(result)
    assert PROXY_NAME not in text
    assert PUMP_NAME not in text
    assert "scanner **:**:**:**:AA:01" in result["bluetooth"]["reachability"]
    scanner = result["bluetooth"]["scanners_hearing_the_pump"][0]
    assert scanner.keys() >= {"model", "sw_version"}


# --- E3, E6 and the registry ---


async def test_the_user_step_names_an_unsupported_pump(
    hass: HomeAssistant, proxy: FakeProxyScanner
) -> None:
    proxy.inject("AA:BB:CC:DD:56:78", name="SCALA1", service_data=SERVICE_DATA_OTHER_PUMP)
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_model"
    assert result["description_placeholders"] == {"family": "57", "type": "7"}


async def test_removing_the_entry_offers_the_pump_again(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        await _loaded(hass, config_entry_basic, proxy)
    with patch("custom_components.grundfos_ble.bluetooth.async_rediscover_address") as rediscover:
        await hass.config_entries.async_remove(config_entry_basic.entry_id)
    rediscover.assert_called_once_with(hass, PUMP_ADDRESS)


async def test_the_shaft_power_sensor_becomes_dc_power_with_its_history(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    config_entry_basic.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{PUMP_ADDRESS}_shaft_power",
        config_entry=config_entry_basic,
        suggested_object_id="test_pump_shaft_power",
    )
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        await _loaded(hass, config_entry_basic, proxy)
    entry = registry.async_get(old.entity_id)
    assert entry is not None
    assert entry.unique_id == f"{PUMP_ADDRESS}_dc_power"


async def test_entities_of_denied_objects_are_removed(
    hass: HomeAssistant, proxy: FakeProxyScanner
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=PUMP_NAME,
        unique_id=PUMP_ADDRESS,
        data={**ENTRY_DATA, CONF_CAPABILITIES: [c for c in ALL_CAPABILITIES if c != "energy"]},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "sensor", DOMAIN, f"{PUMP_ADDRESS}_energy", config_entry=entry
    )
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        await _loaded(hass, entry, proxy)
    assert registry.async_get(stale.entity_id) is None


# --- Findings of the 0.2.0b1 hardware test ---


async def test_diagnostics_find_the_proxy_firmware_through_its_bluetooth_entry(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    """ESPHome files the proxy under its Wi-Fi MAC, not the Bluetooth MAC the scanner uses."""
    esphome = MockConfigEntry(domain="esphome")
    esphome.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=esphome.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "00:11:22:33:aa:ff")},
        model="AIR-1",
        sw_version="2026.9.1",
    )
    MockConfigEntry(
        domain="bluetooth",
        unique_id=PROXY_SOURCE,
        data={"source": PROXY_SOURCE, "source_device_id": device.id},
    ).add_to_hass(hass)
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        await _loaded(hass, config_entry_basic, proxy)
    result = await async_get_config_entry_diagnostics(hass, config_entry_basic)
    scanner = result["bluetooth"]["scanners_hearing_the_pump"][0]
    assert (scanner["model"], scanner["sw_version"]) == ("AIR-1", "2026.9.1")


async def test_what_the_last_poll_read_outlives_a_power_cut(
    hass: HomeAssistant, config_entry_full: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        coordinator = await _loaded(hass, config_entry_full, proxy)
    coordinator._async_forget_data()  # the pump stopped advertising
    assert coordinator.data.read == frozenset()
    result = await async_get_config_entry_diagnostics(hass, config_entry_full)
    assert "statistics" in result["polling"]["read_last_poll"]
    assert result["polling"]["last_poll_seconds"] is not None
    assert result["polling"]["last_poll_source"] == "**:**:**:**:AA:01"


async def test_no_poll_starts_on_an_advert_older_than_two_periods(
    hass: HomeAssistant, config_entry_basic: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    """A poll 25 s after the power was cut only added a failure against the proxy."""
    with patch(COORDINATOR_TRANSPORT, side_effect=_full()):
        coordinator = await _loaded(hass, config_entry_basic, proxy)
    info = coordinator._last_service_info
    assert coordinator._needs_poll(info, 120)
    later = monotonic_time_coarse() + POLL_ADVERT_MAX_AGE + 5
    with patch(
        "custom_components.grundfos_ble.coordinator.monotonic_time_coarse", return_value=later
    ):
        assert not coordinator._needs_poll(info, 120)
