from __future__ import annotations

from datetime import time

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import VoltcraftDataUpdateCoordinator
from .entity import VoltcraftCoordinatorEntity
from .protocol import SwitchModes

_RANDOM_WEEKDAYS: tuple[tuple[str, str, int, int], ...] = (
    ("Monday", "mon", 1, 3),
    ("Tuesday", "tue", 2, 4),
    ("Wednesday", "wed", 3, 5),
    ("Thursday", "thu", 4, 6),
    ("Friday", "fri", 5, 7),
    ("Saturday", "sat", 6, 8),
    ("Sunday", "sun", 0, 9),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VoltcraftDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            VoltcraftPowerSwitch(coordinator),
            VoltcraftNightModeSwitch(coordinator),
            VoltcraftPowerProtectionSwitch(coordinator),
            VoltcraftReducedTariffSwitch(coordinator),
            VoltcraftRandomModeSwitch(coordinator),
            *(
                VoltcraftRandomWeekdaySwitch(coordinator, name, key, bit, order)
                for name, key, bit, order in _RANDOM_WEEKDAYS
            ),
        ]
    )


class VoltcraftSwitch(VoltcraftCoordinatorEntity, SwitchEntity):
    pass


class VoltcraftPowerSwitch(VoltcraftSwitch):
    _attr_name = "Power switch"
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.mac

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.is_on if self.coordinator.data else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_send_switch_command(SwitchModes.ON)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_send_switch_command(SwitchModes.OFF)


class VoltcraftNightModeSwitch(VoltcraftSwitch):
    _attr_name = "Night mode"
    _attr_icon = "mdi:led-off"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_night_mode"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.night_mode if self.coordinator.data else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_night_mode(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_night_mode(False)


class VoltcraftPowerProtectionSwitch(VoltcraftSwitch):
    _attr_name = "Over-power protection"
    # mdi:shield-flash is not present in the MDI set bundled with Home Assistant.
    _attr_icon = "mdi:shield-alert-outline"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_power_protection"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.power_protection_enabled if data else None

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_power_protection(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_power_protection(False)


class VoltcraftReducedTariffSwitch(VoltcraftSwitch):
    _attr_name = "Tariff 3 - Reduced period"
    _attr_icon = "mdi:cash-clock"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_reduced_tariff_enabled"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.reduced_tariff_enabled if data else None

    async def _set(self, enabled: bool) -> None:
        data = self.coordinator.data
        if data is None or data.reduced_tariff_start is None or data.reduced_tariff_end is None:
            await self.coordinator.async_refresh_settings()
            data = self.coordinator.data
        if data is None or data.reduced_tariff_start is None or data.reduced_tariff_end is None:
            raise HomeAssistantError("Reduced tariff period is not available")
        await self.coordinator.async_set_reduced_period(
            enabled, data.reduced_tariff_start, data.reduced_tariff_end
        )

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)


class VoltcraftRandomModeSwitch(VoltcraftSwitch):
    _attr_name = "Random mode"
    _attr_icon = "mdi:shuffle-variant"

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_random_mode"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.random_enabled if data else None

    async def _set(self, enabled: bool) -> None:
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
            if not enabled:
                await self.coordinator.async_set_random(False, 0, time(0, 0), time(0, 0))
                return
            raise HomeAssistantError("Configure random-mode days and times first")
        await self.coordinator.async_set_random(
            enabled, data.random_weekday_mask, data.random_start, data.random_end
        )

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)


class VoltcraftRandomWeekdaySwitch(VoltcraftSwitch):
    """One intuitive weekday toggle for the SEM6000 random mode."""

    _attr_icon = "mdi:calendar"

    def __init__(
        self,
        coordinator: VoltcraftDataUpdateCoordinator,
        day_name: str,
        day_key: str,
        bit: int,
        order: int,
    ) -> None:
        super().__init__(coordinator)
        self._day_bit = 1 << bit
        # The Home Assistant device page sorts entities by display name.
        # Stable numeric prefixes keep the random-mode controls in workflow order.
        self._attr_name = f"Random mode {order} - {day_name}"
        self._attr_unique_id = f"{coordinator.mac}_random_weekday_{day_key}"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None or data.random_weekday_mask is None:
            return None
        return bool(data.random_weekday_mask & self._day_bit)

    async def _set(self, enabled: bool) -> None:
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

        mask = data.random_weekday_mask
        mask = mask | self._day_bit if enabled else mask & ~self._day_bit
        await self.coordinator.async_set_random(
            bool(data.random_enabled), mask, data.random_start, data.random_end
        )

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)
