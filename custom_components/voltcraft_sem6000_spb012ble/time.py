from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
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
            VoltcraftReducedStartTime(coordinator),
            VoltcraftReducedEndTime(coordinator),
            VoltcraftRandomStartTime(coordinator),
            VoltcraftRandomEndTime(coordinator),
        ]
    )


class VoltcraftTime(VoltcraftCoordinatorEntity, TimeEntity):
    pass


class VoltcraftReducedTime(VoltcraftTime):
    async def _set(self, value: time, is_start: bool) -> None:
        data = self.coordinator.data
        if data is None or data.reduced_tariff_start is None or data.reduced_tariff_end is None:
            await self.coordinator.async_refresh_settings()
            data = self.coordinator.data
        if data is None or data.reduced_tariff_start is None or data.reduced_tariff_end is None:
            raise HomeAssistantError("Reduced tariff period is not available")
        await self.coordinator.async_set_reduced_period(
            bool(data.reduced_tariff_enabled),
            value if is_start else data.reduced_tariff_start,
            data.reduced_tariff_end if is_start else value,
        )


class VoltcraftReducedStartTime(VoltcraftReducedTime):
    _attr_name = "Tariff 4 - Reduced start"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_reduced_start"

    @property
    def native_value(self) -> time | None:
        data = self.coordinator.data
        return data.reduced_tariff_start if data else None

    async def async_set_value(self, value: time) -> None:
        await self._set(value, True)


class VoltcraftReducedEndTime(VoltcraftReducedTime):
    _attr_name = "Tariff 5 - Reduced end"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_reduced_end"

    @property
    def native_value(self) -> time | None:
        data = self.coordinator.data
        return data.reduced_tariff_end if data else None

    async def async_set_value(self, value: time) -> None:
        await self._set(value, False)


class VoltcraftRandomTime(VoltcraftTime):
    async def _set(self, value: time, is_start: bool) -> None:
        data = self.coordinator.data
        if (
            data is None
            or data.random_weekday_mask is None
            or data.random_start is None
            or data.random_end is None
        ):
            await self.coordinator.async_refresh_random()
            data = self.coordinator.data
        if (
            data is None
            or data.random_weekday_mask is None
            or data.random_start is None
            or data.random_end is None
        ):
            raise HomeAssistantError("Random-mode settings are not available")
        await self.coordinator.async_set_random(
            bool(data.random_enabled),
            data.random_weekday_mask,
            value if is_start else data.random_start,
            data.random_end if is_start else value,
        )


class VoltcraftRandomStartTime(VoltcraftRandomTime):
    _attr_name = "Random mode 1 - Start"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_random_start"

    @property
    def native_value(self) -> time | None:
        data = self.coordinator.data
        return data.random_start if data else None

    async def async_set_value(self, value: time) -> None:
        await self._set(value, True)


class VoltcraftRandomEndTime(VoltcraftRandomTime):
    _attr_name = "Random mode 2 - End"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_random_end"

    @property
    def native_value(self) -> time | None:
        data = self.coordinator.data
        return data.random_end if data else None

    async def async_set_value(self, value: time) -> None:
        await self._set(value, False)
