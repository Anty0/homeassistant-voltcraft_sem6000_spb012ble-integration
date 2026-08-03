from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VoltcraftData, VoltcraftDataUpdateCoordinator
from .entity import VoltcraftCoordinatorEntity
from .protocol import TimerAction, weekdays_from_mask


@dataclass(frozen=True, kw_only=True)
class VoltcraftSensorEntityDescription(SensorEntityDescription):
    value_fn: Callable[[VoltcraftData], Any] | None = None


SENSORS: tuple[VoltcraftSensorEntityDescription, ...] = (
    VoltcraftSensorEntityDescription(
        key="power",
        name="Power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data: data.power,
    ),
    VoltcraftSensorEntityDescription(
        key="voltage",
        name="Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.voltage,
    ),
    VoltcraftSensorEntityDescription(
        key="current",
        name="Current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: data.current,
    ),
    VoltcraftSensorEntityDescription(
        key="frequency",
        name="Frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.frequency,
    ),
    VoltcraftSensorEntityDescription(
        key="power_factor",
        name="Power factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: data.power_factor,
    ),
    VoltcraftSensorEntityDescription(
        key="energy",
        name="Total energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda data: data.consumed_energy,
    ),
    VoltcraftSensorEntityDescription(
        key="serial",
        name="Serial number",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.serial,
    ),
    VoltcraftSensorEntityDescription(
        key="vendor",
        name="Vendor",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.vendor,
    ),
    VoltcraftSensorEntityDescription(
        key="firmware_version",
        name="Firmware version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.firmware_version,
    ),
    VoltcraftSensorEntityDescription(
        key="hardware_version",
        name="Hardware version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.hardware_version,
    ),
    VoltcraftSensorEntityDescription(
        key="connection_mode",
        name="Connection mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.session_transport,
    ),
    VoltcraftSensorEntityDescription(
        key="att_mtu",
        name="ATT MTU",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.att_mtu,
    ),
    VoltcraftSensorEntityDescription(
        key="app_initialization",
        name="App-compatible initialization",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: "complete" if data.app_initialization_complete else "incomplete",
    ),
    VoltcraftSensorEntityDescription(
        key="app_cccd_handshake",
        name="App CCCD handshake",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: (
            "applied"
            if data.app_cccd_handshake_applied is True
            else "not applied"
            if data.app_cccd_handshake_applied is False
            else None
        ),
    ),
    VoltcraftSensorEntityDescription(
        key="app_finalize",
        name="App finalization probe",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: (
            "successful"
            if data.app_finalize_succeeded is True
            else "failed"
            if data.app_finalize_succeeded is False
            else "pending"
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VoltcraftDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [VoltcraftSensor(coordinator, description) for description in SENSORS]
        + [
            VoltcraftHistorySensor(coordinator, "history_24h", "Energy history 24 hours", "history_24h_wh"),
            VoltcraftHistorySensor(coordinator, "history_30d", "Energy history 30 days", "history_30d_wh"),
            VoltcraftHistorySensor(coordinator, "history_12m", "Energy history 12 months", "history_12m_wh"),
            VoltcraftTimerSensor(coordinator),
            VoltcraftSchedulesSensor(coordinator),
        ]
    )


class VoltcraftSensor(VoltcraftCoordinatorEntity, SensorEntity):
    entity_description: VoltcraftSensorEntityDescription

    def __init__(
        self,
        coordinator: VoltcraftDataUpdateCoordinator,
        description: VoltcraftSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.mac}_{description.key}"

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        if data is None or self.entity_description.value_fn is None:
            return None
        return self.entity_description.value_fn(data)


class VoltcraftHistorySensor(VoltcraftCoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:chart-bar"

    def __init__(
        self,
        coordinator: VoltcraftDataUpdateCoordinator,
        key: str,
        name: str,
        data_attribute: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_{key}"
        self._attr_name = name
        self._data_attribute = data_attribute

    def _values(self) -> tuple[int | None, ...]:
        data = self.coordinator.data
        return getattr(data, self._data_attribute) if data is not None else ()

    @property
    def native_value(self) -> float | None:
        values = [value for value in self._values() if value is not None]
        return sum(values) / 1000.0 if values else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"values_wh": list(self._values())}


class VoltcraftTimerSensor(VoltcraftCoordinatorEntity, SensorEntity):
    _attr_name = "Timer"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_timer"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        if data is None:
            return None
        return {
            TimerAction.INACTIVE: "inactive",
            TimerAction.TURN_ON: "turn_on",
            TimerAction.TURN_OFF: "turn_off",
        }.get(data.timer_action, "unknown")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            "target": data.timer_target.isoformat() if data.timer_target else None,
            "original_runtime_seconds": data.timer_original_runtime_seconds,
        }


class VoltcraftSchedulesSensor(VoltcraftCoordinatorEntity, SensorEntity):
    _attr_name = "Schedules"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_schedules"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        return len(data.schedules) if data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None:
            return {}
        entries: list[dict[str, Any]] = []
        for item in data.schedules:
            entries.append(
                {
                    "slot": self.coordinator.schedule_user_slot(item.slot_id),
                    "wire_slot": item.slot_id,
                    "active": item.active,
                    "action": "turn_on" if item.turn_on else "turn_off",
                    "weekdays": list(weekdays_from_mask(item.weekday_mask)),
                    "time": item.at_time.isoformat(timespec="minutes"),
                    "datetime": item.when.isoformat(timespec="minutes") if item.when else None,
                }
            )
        return {"entries": entries}
