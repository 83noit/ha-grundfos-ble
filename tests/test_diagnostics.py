"""Diagnostics redact the address and serial and include frames and Bluetooth paths."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.grundfos_ble.diagnostics import (
    async_get_config_entry_diagnostics,
    redact_address,
)

from .conftest import PROXY_SOURCE, FakeProxyScanner


def test_redact_address() -> None:
    assert redact_address("AA:BB:CC:DD:12:34") == "**:**:**:**:12:34"
    assert redact_address("garbage") == "**REDACTED**"


async def test_diagnostics(
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
    result = await async_get_config_entry_diagnostics(hass, config_entry_basic)
    assert result["entry"]["data"]["address"] == "**:**:**:**:12:34"
    assert result["entry"]["data"]["serial_number"] == "**REDACTED**"
    assert result["bluetooth"]["last_source"] == "**:**:**:**:AA:01"
    assert PROXY_SOURCE not in str(result)
    assert "AA:BB:CC:DD:12:34" not in str(result)
    assert result["bluetooth"]["scanners_hearing_the_pump"][0]["connectable"] is True
    assert result["bluetooth"]["advertisement"]["family"] == 52
    assert result["polling"]["poll_count"] == 1
    assert result["polling"]["capabilities"] == []
    assert result["data"]["measurements"]["power_w"] > 0
    assert result["last_frames"][0]["direction"] == "tx"
    assert "27 07 e7 f8 0a 03 5d 01 22" in result["last_frames"][0]["frame"]
