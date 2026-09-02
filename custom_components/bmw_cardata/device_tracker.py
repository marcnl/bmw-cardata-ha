"""Device tracker platform for BMW CarData (vehicle GPS position)."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CardataConfigEntry, CardataRuntime
from .const import HEADING_DESCRIPTOR, LATITUDE_DESCRIPTOR, LONGITUDE_DESCRIPTOR
from .coordinator import CardataCoordinator
from .entity import CardataDescriptorEntity


async def async_setup_entry(
    hass, entry: CardataConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime: CardataRuntime = entry.runtime_data
    coordinator = runtime.coordinator
    known: set[str] = set()

    @callback
    def _add(vin: str, descriptor: str | None = None) -> None:
        if vin in known:
            return
        known.add(vin)
        async_add_entities([CardataDeviceTracker(coordinator, vin)])

    for vin in list(coordinator.vehicles) + list(coordinator.allowed_vins):
        _add(vin)

    @callback
    def _on_new(vin: str, descriptor: str) -> None:
        if descriptor in (LATITUDE_DESCRIPTOR, LONGITUDE_DESCRIPTOR):
            _add(vin)

    entry.async_on_unload(async_dispatcher_connect(hass, coordinator.signal_new, _on_new))
    entry.async_on_unload(async_dispatcher_connect(hass, coordinator.signal_updated, _on_new))


class CardataDeviceTracker(CardataDescriptorEntity, TrackerEntity):
    def __init__(self, coordinator: CardataCoordinator, vin: str) -> None:
        super().__init__(coordinator, vin, LATITUDE_DESCRIPTOR)
        self._attr_unique_id = f"{vin}_location"
        self._attr_name = "Location"

    def _coord(self, descriptor: str) -> float | None:
        state = self._coordinator.get_state(self._vin, descriptor)
        if state is None or state.value is None:
            return None
        try:
            return float(state.value)
        except (TypeError, ValueError):
            return None

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return self._coord(LATITUDE_DESCRIPTOR)

    @property
    def longitude(self) -> float | None:
        return self._coord(LONGITUDE_DESCRIPTOR)

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {}
        heading = self._coord(HEADING_DESCRIPTOR)
        if heading is not None:
            attrs["heading"] = heading
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _refresh(vin: str, descriptor: str) -> None:
            if vin == self._vin and descriptor in (
                LATITUDE_DESCRIPTOR,
                LONGITUDE_DESCRIPTOR,
                HEADING_DESCRIPTOR,
            ):
                self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._coordinator.signal_updated, _refresh)
        )
