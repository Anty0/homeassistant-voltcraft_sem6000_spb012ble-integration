from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    # Weekdays are exposed as seven normal switches since beta 4.  This avoids
    # requiring a comma-separated technical text value such as sun,mon,tue.
    async_add_entities([VoltcraftDeviceNameText(coordinator)])


class VoltcraftDeviceNameText(VoltcraftCoordinatorEntity, TextEntity):
    _attr_name = "Device name"
    _attr_mode = TextMode.TEXT
    _attr_native_min = 1
    _attr_native_max = 20

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_device_name"

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return data.device_name if data else None

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_set_device_name(value.strip())
