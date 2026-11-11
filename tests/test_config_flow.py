"""Config flow: discovery, pairing (success and every failure), reauth, reconfigure, options."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.grundfos_ble.const import (
    CONF_CAPABILITIES,
    CONF_MODEL,
    CONF_PAIRED_SOURCE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OPT_SCAN_INTERVAL,
)
from grundfos_ble import GrundfosConnectionError, PairingRequired, RequestTimeout

from .conftest import (
    PROXY_SOURCE,
    PUMP_ADDRESS,
    PUMP_NAME,
    SERVICE_DATA_BUTTON,
    SERVICE_DATA_IDLE,
    SERVICE_DATA_OTHER_PUMP,
    FakeProxyScanner,
    service_info,
)


async def _start_bluetooth_flow(hass: HomeAssistant) -> dict[str, Any]:
    """Use the flow the Bluetooth integration started from the advert, or start one."""
    await hass.async_block_till_done()
    started = [f for f in hass.config_entries.flow.async_progress() if f["handler"] == DOMAIN]
    if started:
        result = await hass.config_entries.flow.async_configure(started[0]["flow_id"])
    else:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=service_info()
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    return result


async def _run_pairing(hass: HomeAssistant, flow_id: str) -> dict[str, Any]:
    """Submit the confirm form, let the progress task finish, and continue."""
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "pair"
    assert result["progress_action"] == "pair"
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(flow_id)


async def test_bluetooth_discovery_pairs_and_creates_entry(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    proxy.inject()
    result = await _start_bluetooth_flow(hass)
    assert result["errors"] == {}
    assert (
        PROXY_SOURCE in result["description_placeholders"]["connectable"]
        or "esphome-proxy" in (result["description_placeholders"]["connectable"])
    )

    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == PUMP_NAME
    assert result["data"][CONF_ADDRESS] == PUMP_ADDRESS
    assert result["data"][CONF_MODEL] == "ALPHA2 GO XX-75"
    assert result["data"][CONF_CAPABILITIES] == ["alarms", "operation", "warnings"]
    assert result["data"][CONF_PAIRED_SOURCE] == PROXY_SOURCE
    assert result["result"].unique_id == PUMP_ADDRESS
    mock_pairing["transport"].connect.assert_awaited_once()
    mock_pairing["transport"].disconnect.assert_awaited_once()


async def test_pairing_clears_local_bluez_bonds_first(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    proxy.inject()
    result = await _start_bluetooth_flow(hass)
    with patch(
        "custom_components.grundfos_ble.config_flow.remove_bluez_bond", AsyncMock()
    ) as remove:
        result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (call,) = remove.await_args_list
    assert call.args[0].address == PUMP_ADDRESS


async def test_duplicate_is_refused(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=service_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_unsupported_and_malformed_adverts_abort(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=service_info(SERVICE_DATA_OTHER_PUMP),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_model"
    assert result["description_placeholders"] == {"family": "57", "type": "7"}

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=service_info(b"\x01\x02")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_no_connectable_proxy_blocks_confirm_until_one_appears(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    with patch("custom_components.grundfos_ble.config_flow._connectable_scanners", return_value=[]):
        result = await _start_bluetooth_flow(hass)
        assert result["errors"] == {"base": "no_connectable_proxy"}
        assert result["description_placeholders"]["connectable"] == "none"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "bluetooth_confirm"
        assert result["errors"] == {"base": "no_connectable_proxy"}
    proxy.inject()
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (PairingRequired("refused", 82), "window_missed"),
        (GrundfosConnectionError("no slot"), "cannot_connect"),
        (TimeoutError(), "cannot_connect"),
    ],
)
async def test_pairing_failures_then_recovery(
    hass: HomeAssistant,
    proxy: FakeProxyScanner,
    mock_pairing: dict[str, Any],
    failure: Exception,
    error: str,
) -> None:
    proxy.inject()
    mock_pairing["transport"].connect.side_effect = failure
    result = await _start_bluetooth_flow(hass)
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pair_failed"
    assert result["errors"] == {"base": error}
    assert result["description_placeholders"]["source"] == PROXY_SOURCE

    # Retry succeeds.
    mock_pairing["transport"].connect.side_effect = None
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_button_timeout_then_recovery(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    proxy.inject()
    mock_pairing["wait_for_button"].side_effect = TimeoutError
    result = await _start_bluetooth_flow(hass)
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "button_timeout"}
    mock_pairing["wait_for_button"].side_effect = None
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_proxy_vanishes_before_connect(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    proxy.inject()
    result = await _start_bluetooth_flow(hass)

    async def _forget_device(*_args: Any, **_kwargs: Any) -> Any:
        # The button advert arrives, but by then no connectable path exists.
        from homeassistant.components import bluetooth

        from custom_components.grundfos_ble import config_flow

        config_flow.bluetooth = bluetooth  # keep module intact
        proxy.clear_all_devices() if hasattr(proxy, "clear_all_devices") else None
        return service_info()

    mock_pairing["wait_for_button"].side_effect = _forget_device
    # Make the connectable lookup fail regardless of scanner internals.
    from unittest.mock import patch

    with patch(
        "custom_components.grundfos_ble.config_flow.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_connectable_proxy"}


async def test_pump_identity_without_profile(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    proxy.inject()
    mock_pairing["identity"].profile = None
    mock_pairing["identity"].family, mock_pairing["identity"].type = 57, 1
    result = await _start_bluetooth_flow(hass)
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unsupported_model"}
    assert result["description_placeholders"]["family"] == "57"


async def test_user_flow_lists_discovered_pumps(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    proxy.inject()
    proxy.inject("AA:BB:CC:DD:EE:FF", name="SCALA1", service_data=SERVICE_DATA_OTHER_PUMP)
    await hass.async_block_till_done()
    # Discovery already opened a flow for the pump; the user flow must still offer it.
    for flow in hass.config_entries.flow.async_progress():
        if flow["handler"] == DOMAIN:
            hass.config_entries.flow.async_abort(flow["flow_id"])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    options = result["data_schema"].schema[CONF_ADDRESS].container
    assert list(options) == [PUMP_ADDRESS]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: PUMP_ADDRESS}
    )
    assert result["step_id"] == "bluetooth_confirm"
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_without_pumps_aborts(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_user_flow_skips_configured_pumps(
    hass: HomeAssistant, config_entry: MockConfigEntry, proxy: FakeProxyScanner
) -> None:
    config_entry.add_to_hass(hass)
    proxy.inject()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_reauth_flow(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    proxy: FakeProxyScanner,
    mock_pairing: dict[str, Any],
) -> None:
    config_entry.add_to_hass(hass)
    # Out of range first.
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "no_longer_in_range"}

    # Submitting while out of range changes nothing; once in range the same submit proceeds.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "no_longer_in_range"}
    proxy.inject()
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_CAPABILITIES] == ["alarms", "operation", "warnings"]


async def test_reconfigure_flow(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    proxy: FakeProxyScanner,
    mock_pairing: dict[str, Any],
) -> None:
    config_entry.add_to_hass(hass)
    proxy.inject()
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {}
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"


async def test_reauth_without_connectable_proxy(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    from homeassistant.components import bluetooth

    config_entry.add_to_hass(hass)
    # Heard by the local (non-connectable in this test) adapter only.
    bluetooth.async_get_advertisement_callback(hass)(
        service_info(connectable=False, source="local")
    )
    result = await config_entry.start_reauth_flow(hass)
    assert result["errors"] == {"base": "no_connectable_proxy"}


async def test_options_flow(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_SCAN_INTERVAL: 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {OPT_SCAN_INTERVAL: 120}
    assert DEFAULT_SCAN_INTERVAL == 60


async def test_pair_task_is_single_and_disconnects_on_failure(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    proxy.inject()
    mock_pairing["pump"].read_identity = AsyncMock(side_effect=GrundfosConnectionError("lost"))
    result = await _start_bluetooth_flow(hass)
    result = await _run_pairing(hass, result["flow_id"])
    assert result["errors"] == {"base": "cannot_connect"}
    mock_pairing["transport"].disconnect.assert_awaited()


async def test_real_button_wait(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    """Run the wait-for-button step for real: the flow continues on a fresh button advert."""
    import asyncio

    from homeassistant.components.bluetooth import async_process_advertisements

    from custom_components.grundfos_ble import config_flow

    proxy.inject()
    result = await _start_bluetooth_flow(hass)
    with patch.object(config_flow, "async_process_advertisements", async_process_advertisements):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await asyncio.sleep(0)
        proxy.inject(service_data=SERVICE_DATA_BUTTON, rssi=-61)
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


def test_fresh_button_press_filter() -> None:
    import time

    from custom_components.grundfos_ble.config_flow import _fresh_button_press

    assert _fresh_button_press(service_info(SERVICE_DATA_BUTTON))
    assert not _fresh_button_press(service_info(SERVICE_DATA_IDLE))
    assert not _fresh_button_press(service_info(SERVICE_DATA_BUTTON, time=time.monotonic() - 30))
    assert not _fresh_button_press(service_info(b"\x00"))


def test_likely_path_without_scanners(hass: HomeAssistant) -> None:
    from custom_components.grundfos_ble.config_flow import _likely_path

    assert _likely_path(hass, "00:11:22:33:44:55") is None


async def test_unexpected_error_reports_and_recovers(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    proxy.inject()
    mock_pairing["transport"].connect.side_effect = RuntimeError("boom")
    result = await _start_bluetooth_flow(hass)
    result = await _run_pairing(hass, result["flow_id"])
    assert result["errors"] == {"base": "unknown"}
    mock_pairing["transport"].connect.side_effect = None
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("reply_seen", "error"), [(False, "window_missed"), (True, "cannot_connect")]
)
async def test_link_lost_during_pairing_read(
    hass: HomeAssistant,
    proxy: FakeProxyScanner,
    mock_pairing: dict[str, Any],
    reply_seen: bool,
    error: str,
) -> None:
    proxy.inject()
    mock_pairing["transport"].reply_seen = reply_seen
    mock_pairing["pump"].read_identity = AsyncMock(side_effect=RequestTimeout("silent"))
    result = await _start_bluetooth_flow(hass)
    result = await _run_pairing(hass, result["flow_id"])
    assert result["errors"] == {"base": error}


async def test_unsupported_identity_recovers_on_retry(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    proxy.inject()
    profile = mock_pairing["identity"].profile
    mock_pairing["identity"].profile = None
    result = await _start_bluetooth_flow(hass)
    result = await _run_pairing(hass, result["flow_id"])
    assert result["errors"] == {"base": "unsupported_model"}
    mock_pairing["identity"].profile = profile
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reauth_on_a_loaded_entry_pauses_polling_while_pairing(
    hass: HomeAssistant,
    config_entry_basic: MockConfigEntry,
    proxy: FakeProxyScanner,
    mock_pairing: dict[str, Any],
    replay_transport_factory: Any,
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
        seen: list[bool] = []

        async def wait(*_args: Any, **_kwargs: Any) -> Any:
            seen.append(coordinator._paused)
            return service_info()

        mock_pairing["wait_for_button"].side_effect = wait
        result = await config_entry_basic.start_reauth_flow(hass)
        result = await _run_pairing(hass, result["flow_id"])
    assert result["reason"] == "reauth_successful"
    assert seen == [True]
    assert coordinator._paused is False
