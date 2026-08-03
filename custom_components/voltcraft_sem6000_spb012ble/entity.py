from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import VoltcraftDataUpdateCoordinator


class VoltcraftCoordinatorEntity(CoordinatorEntity[VoltcraftDataUpdateCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: VoltcraftDataUpdateCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_device_info = coordinator.device_info
