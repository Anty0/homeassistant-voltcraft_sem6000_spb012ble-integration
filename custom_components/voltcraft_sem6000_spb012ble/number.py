from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VoltcraftDataUpdateCoordinator

_MIN_POWER_LIMIT_W = 1
_MAX_POWER_LIMIT_W = 3680


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VoltcraftDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VoltcraftOverpowerLimitNumber(coordinator)])


class VoltcraftOverpowerLimitNumber(
    CoordinatorEntity[VoltcraftDataUpdateCoordinator], NumberEntity
):
    """Configure the device-side overpower threshold."""

    _attr_device_class = NumberDeviceClass.POWER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = _MIN_POWER_LIMIT_W
    _attr_native_max_value = _MAX_POWER_LIMIT_W
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_overpower_limit"
        self._attr_name = "Overpower limit"
        self._attr_device_info = coordinator.device_info

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.power_limit_w is not None

    @property
    def native_value(self) -> int | None:
        return self.coordinator.power_limit_w

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_power_limit(round(value))
