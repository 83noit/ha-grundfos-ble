"""Sensors: what the pump reports over GENIbus."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    REVOLUTIONS_PER_MINUTE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from grundfos_ble import ControlMode, ErrorCode, OperationMode

from .const import (
    CAP_ALARMS,
    CAP_ENERGY,
    CAP_MAX_FLOW_LIMITER,
    CAP_MIN_FLOW_LIMITER,
    CAP_OPERATION,
    CAP_STATISTICS,
    CAP_TEMPERATURES,
    CAP_WARNINGS,
)
from .coordinator import GrundfosConfigEntry, GrundfosCoordinator
from .entity import GrundfosEntity

PARALLEL_UPDATES = 1

CONTROL_MODE_OPTIONS = [m.name.lower() for m in ControlMode]
OPERATION_MODE_OPTIONS = [m.name.lower() for m in OperationMode]
FAULT_OPTIONS = [
    "none",
    *[c.name.lower() for c in ErrorCode if c is not ErrorCode.OK],
    "unrecognised",
]


def _enum_name(enum: type[ControlMode] | type[OperationMode], value: int | None) -> str | None:
    if value is None:
        return None
    try:
        return str(enum(value).name).lower()
    except ValueError:
        return "unrecognised"


def _fault(codes: tuple[int, ...] | None) -> str | None:
    if codes is None:
        return None
    if not codes:
        return "none"
    try:
        return str(ErrorCode(codes[0]).name).lower()
    except ValueError:
        return "unrecognised"


@dataclass(frozen=True, kw_only=True)
class GrundfosSensorDescription(SensorEntityDescription):
    """A sensor plus how to read it and which probed capability it needs."""

    value_fn: Callable[[GrundfosCoordinator], StateType]
    requires: tuple[str, ...] = ()


def _m(attr: str) -> Callable[[GrundfosCoordinator], StateType]:
    def read(coordinator: GrundfosCoordinator) -> StateType:
        measurements = coordinator.data.measurements
        value: StateType = None if measurements is None else getattr(measurements, attr)
        return value

    return read


SENSORS: tuple[GrundfosSensorDescription, ...] = (
    GrundfosSensorDescription(
        key="flow",
        translation_key="flow",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_m("flow_m3h"),
    ),
    GrundfosSensorDescription(
        key="head",
        translation_key="head",
        native_unit_of_measurement=UnitOfLength.METERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_m("head_m"),
    ),
    GrundfosSensorDescription(
        key="differential_pressure",
        translation_key="differential_pressure",
        device_class=SensorDeviceClass.PRESSURE,
        native_unit_of_measurement=UnitOfPressure.PA,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
        value_fn=_m("differential_pressure_pa"),
    ),
    GrundfosSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_m("power_w"),
    ),
    GrundfosSensorDescription(
        key="speed",
        translation_key="speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=_m("speed_rpm"),
    ),
    GrundfosSensorDescription(
        key="voltage",
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
        value_fn=_m("grid_voltage_v"),
    ),
    GrundfosSensorDescription(
        key="dc_link_voltage",
        translation_key="dc_link_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
        value_fn=_m("dc_link_voltage_v"),
    ),
    GrundfosSensorDescription(
        key="current",
        translation_key="current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=3,
        value_fn=_m("current_a"),
    ),
    GrundfosSensorDescription(
        key="shaft_power",
        translation_key="shaft_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=1,
        value_fn=_m("shaft_power_w"),
    ),
    GrundfosSensorDescription(
        key="electronics_temperature",
        translation_key="electronics_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=_m("electronics_temperature_c"),
    ),
    GrundfosSensorDescription(
        key="media_temperature",
        translation_key="media_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        requires=(CAP_TEMPERATURES,),
        value_fn=lambda c: None if c.data.temperatures is None else c.data.temperatures.media_c,
    ),
    GrundfosSensorDescription(
        key="ambient_temperature",
        translation_key="ambient_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=1,
        requires=(CAP_TEMPERATURES,),
        value_fn=lambda c: None if c.data.temperatures is None else c.data.temperatures.ambient_c,
    ),
    GrundfosSensorDescription(
        key="control_mode",
        translation_key="control_mode",
        device_class=SensorDeviceClass.ENUM,
        options=[*CONTROL_MODE_OPTIONS, "unrecognised"],
        requires=(CAP_OPERATION,),
        value_fn=lambda c: _enum_name(
            ControlMode, None if c.data.operation is None else c.data.operation.control_mode
        ),
    ),
    GrundfosSensorDescription(
        key="operation_mode",
        translation_key="operation_mode",
        device_class=SensorDeviceClass.ENUM,
        options=[*OPERATION_MODE_OPTIONS, "unrecognised"],
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        requires=(CAP_OPERATION,),
        value_fn=lambda c: _enum_name(
            OperationMode, None if c.data.operation is None else c.data.operation.operation_mode
        ),
    ),
    GrundfosSensorDescription(
        key="setpoint",
        translation_key="setpoint",
        suggested_display_precision=2,
        requires=(CAP_OPERATION,),
        value_fn=lambda c: (
            None
            if c.data.operation is None or c.data.operation.setpoint_display is None
            else c.data.operation.setpoint_display[0]
        ),
    ),
    GrundfosSensorDescription(
        key="max_flow_limit",
        translation_key="max_flow_limit",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        requires=(CAP_MAX_FLOW_LIMITER, CAP_MIN_FLOW_LIMITER),
        value_fn=lambda c: None if c.data.max_limiter is None else c.data.max_limiter.limit_m3h,
    ),
    GrundfosSensorDescription(
        key="min_flow_limit",
        translation_key="min_flow_limit",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        requires=(CAP_MAX_FLOW_LIMITER, CAP_MIN_FLOW_LIMITER),
        value_fn=lambda c: None if c.data.min_limiter is None else c.data.min_limiter.limit_m3h,
    ),
    GrundfosSensorDescription(
        key="alarm",
        translation_key="alarm",
        device_class=SensorDeviceClass.ENUM,
        options=FAULT_OPTIONS,
        requires=(CAP_ALARMS, CAP_WARNINGS),
        value_fn=lambda c: _fault(None if c.data.faults is None else c.data.faults.alarms),
    ),
    GrundfosSensorDescription(
        key="warning",
        translation_key="warning",
        device_class=SensorDeviceClass.ENUM,
        options=FAULT_OPTIONS,
        requires=(CAP_ALARMS, CAP_WARNINGS),
        value_fn=lambda c: _fault(None if c.data.faults is None else c.data.faults.warnings),
    ),
    GrundfosSensorDescription(
        key="start_count",
        translation_key="start_count",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        requires=(CAP_STATISTICS,),
        value_fn=lambda c: None if c.data.statistics is None else c.data.statistics.start_count,
    ),
    GrundfosSensorDescription(
        key="operating_time",
        translation_key="operating_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
        requires=(CAP_STATISTICS,),
        value_fn=lambda c: (
            None if c.data.statistics is None else c.data.statistics.operating_time_s
        ),
    ),
    GrundfosSensorDescription(
        key="starts_last_24h",
        translation_key="starts_last_24h",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        requires=(CAP_STATISTICS,),
        value_fn=lambda c: None if c.data.statistics is None else c.data.statistics.starts_last_24h,
    ),
    GrundfosSensorDescription(
        key="last_cycle_time",
        translation_key="last_cycle_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        requires=(CAP_STATISTICS,),
        value_fn=lambda c: None if c.data.statistics is None else c.data.statistics.last_cycle_s,
    ),
    GrundfosSensorDescription(
        key="running_time_24h",
        translation_key="running_time_24h",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        requires=(CAP_STATISTICS,),
        value_fn=lambda c: (
            None if c.data.statistics is None else c.data.statistics.running_time_24h_s
        ),
    ),
    GrundfosSensorDescription(
        key="power_on_time",
        translation_key="power_on_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        requires=(CAP_STATISTICS,),
        value_fn=lambda c: None if c.data.statistics is None else c.data.statistics.power_on_time_s,
    ),
    GrundfosSensorDescription(
        key="power_ups",
        translation_key="power_ups",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        requires=(CAP_STATISTICS,),
        value_fn=lambda c: None if c.data.statistics is None else c.data.statistics.power_ups,
    ),
    GrundfosSensorDescription(
        key="pcb_temperature",
        translation_key="pcb_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=0,
        requires=(CAP_TEMPERATURES,),
        value_fn=lambda c: None if c.data.temperatures is None else c.data.temperatures.pcb_c,
    ),
    GrundfosSensorDescription(
        key="energy",
        translation_key="energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        requires=(CAP_ENERGY,),
        value_fn=lambda c: c.data.energy_kwh,
    ),
    GrundfosSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.rssi,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GrundfosConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the sensors this pump supports."""
    coordinator = entry.runtime_data
    async_add_entities(
        GrundfosSensor(coordinator, description)
        for description in SENSORS
        if all(coordinator.has(capability) for capability in description.requires)
    )


class GrundfosSensor(GrundfosEntity, SensorEntity):
    """A pump value."""

    entity_description: GrundfosSensorDescription

    @property
    def native_value(self) -> StateType:
        """Current value."""
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Give every active code on the alarm and warning sensors."""
        faults = self.coordinator.data.faults
        key = self.entity_description.key
        if faults is None or key not in ("alarm", "warning"):
            return None
        return {"codes": list(faults.alarms if key == "alarm" else faults.warnings)}

    @property
    def native_unit_of_measurement(self) -> str | None:
        """The setpoint's unit follows the control mode (m, rpm or m³/h)."""
        if self.entity_description.key == "setpoint":
            operation = self.coordinator.data.operation
            if operation is None or operation.setpoint_display is None:
                return None
            return operation.setpoint_display[1]
        unit: str | None = super().native_unit_of_measurement
        return unit
