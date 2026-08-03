from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VoltcraftDataUpdateCoordinator
from .entity import VoltcraftCoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VoltcraftDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            VoltcraftPowerLimitNumber(coordinator),
            VoltcraftNormalTariffNumber(coordinator),
            VoltcraftReducedTariffNumber(coordinator),
        ]
    )


class VoltcraftNumber(VoltcraftCoordinatorEntity, NumberEntity):
    _attr_mode = NumberMode.BOX


class VoltcraftPowerLimitNumber(VoltcraftNumber):
    _attr_name = "Over-power limit"
    _attr_icon = "mdi:flash-alert"
    _attr_native_min_value = 1
    _attr_native_max_value = 4000
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "W"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_power_limit"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return float(data.power_limit_watts) if data and data.power_limit_watts is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_power_limit(round(value))


class VoltcraftTariffNumber(VoltcraftNumber):
    _attr_native_min_value = 0
    _attr_native_max_value = 2.55
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "€/kWh"

    async def _set_prices(self, normal: float | None, reduced: float | None) -> None:
        data = self.coordinator.data
        if data is None or data.normal_tariff is None or data.reduced_tariff is None:
            await self.coordinator.async_refresh_settings()
            data = self.coordinator.data
        if data is None or data.normal_tariff is None or data.reduced_tariff is None:
            raise HomeAssistantError("Tariff settings are not available")
        await self.coordinator.async_set_prices(
            data.normal_tariff if normal is None else normal,
            data.reduced_tariff if reduced is None else reduced,
        )


class VoltcraftNormalTariffNumber(VoltcraftTariffNumber):
    _attr_name = "Tariff 1 - Normal price"
    _attr_icon = "mdi:currency-eur"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_normal_tariff"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return data.normal_tariff if data else None

    async def async_set_native_value(self, value: float) -> None:
        await self._set_prices(value, None)


class VoltcraftReducedTariffNumber(VoltcraftTariffNumber):
    _attr_name = "Tariff 2 - Reduced price"
    _attr_icon = "mdi:currency-eur-off"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_reduced_tariff"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        return data.reduced_tariff if data else None

    async def async_set_native_value(self, value: float) -> None:
        await self._set_prices(None, value)
