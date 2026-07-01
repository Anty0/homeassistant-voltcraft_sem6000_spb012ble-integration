from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VoltcraftDataUpdateCoordinator
from .protocol import SwitchModes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VoltcraftDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MainSwitchEntity(coordinator)])


class MainSwitchEntity(CoordinatorEntity[VoltcraftDataUpdateCoordinator], SwitchEntity):
    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.mac
        self._attr_name = "Power switch"
        self._attr_device_info = coordinator.device_info
        self._optimistic_is_on: bool | None = None
        self._optimistic_count = coordinator.measurement_count

    @property
    def is_on(self) -> bool | None:
        if self._optimistic_is_on is not None:
            return self._optimistic_is_on
        return self.coordinator.data.is_on if self.coordinator.data else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set_state(False)

    async def _async_set_state(self, on: bool) -> None:
        await self.coordinator.async_send_switch_command(SwitchModes.ON if on else SwitchModes.OFF)
        self._optimistic_is_on = on
        self._optimistic_count = self.coordinator.measurement_count
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        # Clear the optimistic value only once a measurement that could have observed
        # the toggle arrives: one confirming the requested state, or the second since
        # the command (the first may be a stale poll already in flight at toggle time).
        if self._optimistic_is_on is not None and self.coordinator.data is not None:
            advanced = self.coordinator.measurement_count - self._optimistic_count
            if self.coordinator.data.is_on == self._optimistic_is_on or advanced >= 2:
                self._optimistic_is_on = None
        super()._handle_coordinator_update()
