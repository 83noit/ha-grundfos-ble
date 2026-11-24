"""A pump this integration can't read yet: add it for data collection, then convert it later."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant.common import MockConfigEntry
from pytest_homeassistant.components.repairs import (
    process_repair_fix_flow,
    start_repair_fix_flow,
)
from pytest_homeassistant.typing import ClientSessionGenerator

from custom_components.grundfos_ble.const import (
    CONF_CAPABILITIES,
    CONF_CAPTURE,
    CONF_COLLECT_ONLY,
    CONF_FAMILY,
    CONF_MODEL,
    CONF_PAIRED_SOURCE,
    CONF_SERIAL_NUMBER,
    CONF_TYPE,
    DOMAIN,
    ISSUE_COLLECT_ONLY,
    ISSUE_NOW_SUPPORTED,
)
from custom_components.grundfos_ble.diagnostics import async_get_config_entry_diagnostics
from grundfos_ble import Capture, PairingRequired, ProbeResult, Recorder

from .conftest import (
    PROXY_SOURCE,
    PUMP_ADDRESS,
    PUMP_NAME,
    SERVICE_DATA_OTHER_PUMP,
    FakeProxyScanner,
    service_info,
)
from .test_config_flow import _run_pairing

REQUEST = bytes.fromhex("2707e7f80203949596eb47")
REPLY = bytes.fromhex("2407f8e7020039070000")
CAPTURE_LINES = ["# Recorded in a test.", "", "# identity 2/148-150", "> 27 07", "< 24 07"]


def _collect_entry(family: int, type_: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title=PUMP_NAME,
        unique_id=PUMP_ADDRESS,
        data={
            CONF_ADDRESS: PUMP_ADDRESS,
            CONF_COLLECT_ONLY: True,
            CONF_FAMILY: family,
            CONF_TYPE: type_,
            CONF_MODEL: "SCALA1",
            CONF_CAPTURE: CAPTURE_LINES,
            CONF_PAIRED_SOURCE: PROXY_SOURCE,
        },
    )


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{ISSUE_COLLECT_ONLY}_{entry.entry_id}")


async def test_an_unsupported_pump_can_be_added_for_data_collection(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    identity = mock_pairing["identity"]
    identity.family, identity.type, identity.profile, identity.model = 57, 7, None, "SCALA1"

    async def fake_capture(_pump: Any, recorder: Recorder, **_kwargs: Any) -> Capture:
        assert not recorder.exchanges  # the private identity read was not kept
        recorder.exchanges.append(("identity 2/148-150", REQUEST, REPLY))
        return Capture(identity, {}, mock_pairing["probe"], tuple(recorder.exchanges))

    proxy.inject(name="SCALA1", service_data=SERVICE_DATA_OTHER_PUMP)
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: PUMP_ADDRESS}
    )
    assert result["step_id"] == "collect_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "bluetooth_confirm"
    with patch(
        "custom_components.grundfos_ble.config_flow.record_capture", side_effect=fake_capture
    ):
        result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    data = result["data"]
    assert data[CONF_COLLECT_ONLY] is True
    assert (data[CONF_FAMILY], data[CONF_TYPE]) == (57, 7)
    assert CONF_SERIAL_NUMBER not in data
    assert "> 27 07 e7 f8 02 03 94 95 96 eb 47" in data[CONF_CAPTURE]
    assert data[CONF_CAPTURE][0].startswith("# Recorded from SCALA1 (family 57, type 7)")

    # Set up without sensors, with a notice that says what to do.
    await hass.async_block_till_done()
    entry = result["result"]
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id) == []
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == ISSUE_COLLECT_ONLY
    assert not issue.is_fixable
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert _issue(hass, entry) is None


async def test_the_discovery_of_an_unsupported_pump_stays_quiet(
    hass: HomeAssistant, proxy: FakeProxyScanner
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=service_info(SERVICE_DATA_OTHER_PUMP),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_model"


async def test_pairing_a_listed_supported_pump_still_rejects_an_unknown_identity(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    """Only a pump chosen for data collection may pair without a profile."""
    mock_pairing["identity"].profile = None
    proxy.inject()
    await hass.async_block_till_done()
    for flow in hass.config_entries.flow.async_progress():
        if flow["handler"] == DOMAIN:
            hass.config_entries.flow.async_abort(flow["flow_id"])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ADDRESS: PUMP_ADDRESS}
    )
    result = await _run_pairing(hass, result["flow_id"])
    assert result["errors"] == {"base": "unsupported_model"}


async def test_a_supported_pump_keeps_the_probe_frames(
    hass: HomeAssistant, proxy: FakeProxyScanner, mock_pairing: dict[str, Any]
) -> None:
    proxy.inject()
    await hass.async_block_till_done()
    (flow,) = [f for f in hass.config_entries.flow.async_progress() if f["handler"] == DOMAIN]
    result = await hass.config_entries.flow.async_configure(flow["flow_id"])
    result = await _run_pairing(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_COLLECT_ONLY not in result["data"]
    assert result["data"][CONF_CAPTURE][0].startswith("# Recorded from ALPHA2 GO XX-75")


async def test_diagnostics_of_a_collect_only_entry(
    hass: HomeAssistant, proxy: FakeProxyScanner
) -> None:
    entry = _collect_entry(57, 7)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    proxy.inject(service_data=SERVICE_DATA_OTHER_PUMP)
    await hass.async_block_till_done()
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["capture"] == CAPTURE_LINES
    assert CONF_CAPTURE not in result["entry"]["data"]
    assert result["entry"]["data"][CONF_ADDRESS] == "**:**:**:**:12:34"
    assert result["bluetooth"]["advertisement"] == SERVICE_DATA_OTHER_PUMP.hex(" ")
    assert PUMP_ADDRESS not in str(result)


@pytest.fixture
def mock_fix() -> Any:
    identity = MagicMock()
    identity.family, identity.type, identity.version = 52, 6, 0
    identity.profile = MagicMock()
    identity.profile.name = "ALPHA2 GO"
    identity.model = "ALPHA2 GO XX-75"
    identity.serial_number = "12345678"
    identity.software_version = "SW"
    identity.ble_software_version = "BLE-SW"
    identity.unit_name = None
    probe = ProbeResult(
        supported=frozenset({"operation"}), frames={}, errors={}, denied=frozenset()
    )
    transport = MagicMock()
    transport.connect = AsyncMock()
    transport.disconnect = AsyncMock()
    transport.last_source = PROXY_SOURCE
    pump = MagicMock()
    pump.read_identity = AsyncMock(return_value=identity)
    pump.probe_capabilities = AsyncMock(return_value=probe)
    with (
        patch("custom_components.grundfos_ble.repairs.BleTransport", return_value=transport),
        patch("custom_components.grundfos_ble.repairs.GrundfosPump", return_value=pump),
    ):
        yield {"identity": identity, "pump": pump, "transport": transport}


async def test_a_pump_supported_later_is_converted_by_the_fix(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    proxy: FakeProxyScanner,
    mock_fix: dict[str, Any],
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    entry = _collect_entry(52, 6)  # as if an update had added this pump's profile
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    proxy.inject()
    await hass.async_block_till_done()
    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == ISSUE_NOW_SUPPORTED
    assert issue.is_fixable

    client = await hass_client()
    flow = await start_repair_fix_flow(client, DOMAIN, issue.issue_id)
    assert flow["step_id"] == "confirm"
    result = await process_repair_fix_flow(client, flow["flow_id"], json={})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    assert entry.data[CONF_COLLECT_ONLY] is False
    assert entry.data[CONF_MODEL] == "ALPHA2 GO XX-75"
    assert entry.data[CONF_CAPABILITIES] == ["operation"]
    assert entry.state is config_entries.ConfigEntryState.LOADED
    assert er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert _issue(hass, entry) is None
    mock_fix["transport"].connect.assert_awaited_once()
    mock_fix["transport"].disconnect.assert_awaited_once()


async def test_the_fix_sends_a_refused_bond_to_reconfigure(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    proxy: FakeProxyScanner,
    mock_fix: dict[str, Any],
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    mock_fix["transport"].connect.side_effect = PairingRequired("refused")
    entry = _collect_entry(52, 6)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    proxy.inject()
    await hass.async_block_till_done()
    issue = _issue(hass, entry)
    assert issue is not None

    client = await hass_client()
    flow = await start_repair_fix_flow(client, DOMAIN, issue.issue_id)
    result = await process_repair_fix_flow(client, flow["flow_id"], json={})
    assert result["type"] == "form"
    assert result["errors"] == {"base": "needs_pairing"}
    assert entry.data[CONF_COLLECT_ONLY] is True
