"""Fixtures: a fake ESPHome-style proxy scanner, pump adverts and recorded pump frames."""

from __future__ import annotations

from collections.abc import Callable, Generator
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from habluetooth import BaseHaRemoteScanner, HaBluetoothConnector
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.grundfos_ble.const import (
    CONF_BLE_SOFTWARE_VERSION,
    CONF_CAPABILITIES,
    CONF_MODEL,
    CONF_PAIRED_SOURCE,
    CONF_SERIAL_NUMBER,
    CONF_SOFTWARE_VERSION,
    CONF_UNIT_NAME,
    DOMAIN,
)
from grundfos_ble.testing import FIXTURES as LIB_FIXTURES
from grundfos_ble.testing import ReplayTransport, load_fixture

PUMP_ADDRESS = "AA:BB:CC:DD:12:34"
PUMP_NAME = "Test pump"
PROXY_SOURCE = "00:11:22:33:AA:01"
PROXY_NAME = "esphome-proxy-aa01"
GRUNDFOS_UUID = "0000fe5d-0000-1000-8000-00805f9b34fb"
SERVICE_DATA_IDLE = bytes.fromhex("03010034060630001234")
SERVICE_DATA_BUTTON = bytes.fromhex("03010034060630101234")
SERVICE_DATA_OTHER_PUMP = bytes.fromhex("03010039070630001234")  # SCALA1 family/type


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components from this repository."""


@pytest.fixture(autouse=True)
def bluetooth_stack(enable_bluetooth: None) -> None:
    """Start the Bluetooth integration with a mocked local adapter."""


class FakeProxyScanner(BaseHaRemoteScanner):
    """A connectable remote scanner standing in for an ESPHome Bluetooth proxy."""

    def inject(
        self,
        address: str = PUMP_ADDRESS,
        *,
        name: str = PUMP_NAME,
        service_data: bytes = SERVICE_DATA_IDLE,
        rssi: int = -67,
        at: float | None = None,
    ) -> None:
        """Deliver one advertisement as if the proxy had heard it (``at``: monotonic time)."""
        self._async_on_advertisement(
            address,
            rssi,
            name,
            [GRUNDFOS_UUID],
            {GRUNDFOS_UUID: service_data},
            {},
            None,
            {"source": self.source, "address_type": 0},
            time.monotonic() if at is None else at,
        )


@pytest.fixture
def proxy(hass: HomeAssistant) -> Generator[FakeProxyScanner]:
    """Register a connectable fake proxy with three connection slots."""
    connector = HaBluetoothConnector(
        client=MagicMock(), source=PROXY_SOURCE, can_connect=lambda: True
    )
    scanner = FakeProxyScanner(PROXY_SOURCE, PROXY_NAME, connector, True)
    unregister = bluetooth.async_register_scanner(hass, scanner, connection_slots=3)
    yield scanner
    unregister()


def service_info(
    service_data: bytes = SERVICE_DATA_IDLE, **overrides: Any
) -> BluetoothServiceInfoBleak:
    """A BluetoothServiceInfoBleak as the config flow receives it from discovery."""
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

    address = overrides.pop("address", PUMP_ADDRESS)
    name = overrides.pop("name", PUMP_NAME)
    device = BLEDevice(address, name, {"source": PROXY_SOURCE})
    advertisement = AdvertisementData(
        local_name=name,
        manufacturer_data={},
        service_data={GRUNDFOS_UUID: service_data},
        service_uuids=[GRUNDFOS_UUID],
        tx_power=None,
        rssi=-67,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-67,
        manufacturer_data={},
        service_data={GRUNDFOS_UUID: service_data},
        service_uuids=[GRUNDFOS_UUID],
        source=overrides.pop("source", PROXY_SOURCE),
        device=device,
        advertisement=advertisement,
        connectable=overrides.pop("connectable", True),
        time=overrides.pop("time", time.monotonic()),
        tx_power=None,
    )


ENTRY_DATA: dict[str, Any] = {
    CONF_ADDRESS: PUMP_ADDRESS,
    CONF_MODEL: "ALPHA2 GO XX-75",
    CONF_SERIAL_NUMBER: "12345678",
    CONF_SOFTWARE_VERSION: "92975974V01.05.00.00888",
    CONF_BLE_SOFTWARE_VERSION: "93127901V06.00.03.00001",
    CONF_UNIT_NAME: PUMP_NAME,
    CONF_CAPABILITIES: ["alarms", "warnings", "operation", "autonight"],
    CONF_PAIRED_SOURCE: PROXY_SOURCE,
}


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A paired pump entry with the optional objects an ALPHA2 GO is expected to have."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=PUMP_NAME,
        unique_id=PUMP_ADDRESS,
        data=dict(ENTRY_DATA),
    )


ALL_CAPABILITIES = [
    "alarms",
    "warnings",
    "operation",
    "autonight",
    "max_flow_limiter",
    "min_flow_limiter",
    "statistics",
    "temperatures",
    "energy",
    "panel_setting",
]


@pytest.fixture
def config_entry_full() -> MockConfigEntry:
    """A paired pump entry that answered every optional object."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=PUMP_NAME,
        unique_id=PUMP_ADDRESS,
        data={**ENTRY_DATA, CONF_CAPABILITIES: ALL_CAPABILITIES},
    )


@pytest.fixture
def config_entry_basic() -> MockConfigEntry:
    """A paired pump entry whose optional objects all probed as absent."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=PUMP_NAME,
        unique_id=PUMP_ADDRESS,
        data={**ENTRY_DATA, CONF_CAPABILITIES: []},
    )


@pytest.fixture
def replay_transport_factory() -> Callable[..., ReplayTransport]:
    """Build a ReplayTransport that answers from the library's recorded frames."""
    exchanges = load_fixture(LIB_FIXTURES / "measured-2026-11-09.hex")

    def factory(*_args: Any, **_kwargs: Any) -> ReplayTransport:
        return ReplayTransport(list(exchanges))

    return factory


@pytest.fixture
def mock_pairing() -> Generator[dict[str, Any]]:
    """Patch the pairing step's library calls so the flow runs without a pump.

    Yields a dict with the patched objects so tests can alter behaviour per case.
    """
    identity = MagicMock()
    identity.family, identity.type, identity.version = 52, 6, 0
    identity.profile = MagicMock(name="ALPHA2 GO")
    identity.profile.name = "ALPHA2 GO"
    identity.model = "ALPHA2 GO XX-75"
    identity.serial_number = "12345678"
    identity.software_version = "92975974V01.05.00.00888"
    identity.ble_software_version = "93127901V06.00.03.00001"
    identity.unit_name = PUMP_NAME
    probe = MagicMock()
    probe.supported = frozenset({"alarms", "warnings", "operation"})
    probe.errors = {"statistics": "ObjectError: object reply status 4"}

    transport = MagicMock()
    transport.connect = AsyncMock()
    transport.disconnect = AsyncMock()
    pump = MagicMock()
    pump.read_identity = AsyncMock(return_value=identity)
    pump.probe_capabilities = AsyncMock(return_value=probe)

    with (
        patch("custom_components.grundfos_ble.config_flow.BleTransport", return_value=transport),
        patch("custom_components.grundfos_ble.config_flow.GrundfosPump", return_value=pump),
        patch(
            "custom_components.grundfos_ble.config_flow.async_process_advertisements",
            new=AsyncMock(return_value=service_info(SERVICE_DATA_BUTTON)),
        ) as wait_for_button,
    ):
        yield {
            "transport": transport,
            "pump": pump,
            "identity": identity,
            "probe": probe,
            "wait_for_button": wait_for_button,
        }
