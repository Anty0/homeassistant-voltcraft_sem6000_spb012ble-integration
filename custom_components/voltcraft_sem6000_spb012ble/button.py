from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    async_add_entities(
        [
            VoltcraftActionButton(coordinator, "sync_time", "Synchronize time", "mdi:clock-check-outline", coordinator.async_sync_time),
            VoltcraftActionButton(coordinator, "refresh_all", "Refresh all settings", "mdi:refresh", coordinator.async_refresh_all),
            VoltcraftActionButton(coordinator, "stop_timer", "Stop timer", "mdi:timer-off-outline", coordinator.async_stop_timer),
        ]
    )


class VoltcraftActionButton(VoltcraftCoordinatorEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: VoltcraftDataUpdateCoordinator,
        key: str,
        name: str,
        icon: str,
        action: Callable[[], Awaitable[None]],
        *,
        enabled_default: bool = True,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mac}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_entity_registry_enabled_default = enabled_default
        self._action = action

    async def async_press(self) -> None:
        await self._action()
