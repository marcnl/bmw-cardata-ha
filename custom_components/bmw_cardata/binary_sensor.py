"""Binary sensor platform for BMW CarData."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CardataConfigEntry, CardataRuntime
from .catalogue import OVERRIDES, infer_binary_device_class
from .const import LOCATION_DESCRIPTORS
from .coordinator import CardataCoordinator
from .descriptors import DESCRIPTORS
from .entity import CardataDescriptorEntity, restore_registered_descriptors

# Value tokens that mean "on / open / unlocked / active".
_TRUE_TOKENS = {
    "true", "1", "on", "open", "opened", "unlocked", "connected", "plugged",
    "active", "yes", "charging", "flap_unlocked", "asn_istrue",
}
_FALSE_TOKENS = {
    "false", "0", "off", "closed", "locked", "secured", "disconnected",
    "unplugged", "inactive", "no", "not_charging", "flap_locked", "asn_isfalse",
    "selectivelocked",
}


async def async_setup_entry(
    hass, entry: CardataConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime: CardataRuntime = entry.runtime_data
    coordinator = runtime.coordinator
    known: set[tuple[str, str]] = set()

    @callback
    def _add(vin: str, descriptor: str) -> None:
        if descriptor in LOCATION_DESCRIPTORS or (vin, descriptor) in known:
            return
        if not _is_binary(descriptor):
            return
        known.add((vin, descriptor))
        async_add_entities([CardataBinarySensor(coordinator, vin, descriptor)])

    for vin, descriptor in restore_registered_descriptors(hass, entry):
        _add(vin, descriptor)
    for vin, descriptor in coordinator.iter_descriptors():
        _add(vin, descriptor)

    entry.async_on_unload(async_dispatcher_connect(hass, coordinator.signal_new, _add))
    entry.async_on_unload(async_dispatcher_connect(hass, coordinator.signal_updated, _add))


def _is_binary(descriptor: str) -> bool:
    override = OVERRIDES.get(descriptor)
    if override and override.binary is not None:
        return override.binary
    return DESCRIPTORS.get(descriptor, {}).get("data_type") == "boolean"


class CardataBinarySensor(CardataDescriptorEntity, BinarySensorEntity):
    def __init__(self, coordinator: CardataCoordinator, vin: str, descriptor: str) -> None:
        super().__init__(coordinator, vin, descriptor)
        override = OVERRIDES.get(descriptor)
        dc = override.device_class if override and override.device_class else \
            infer_binary_device_class(descriptor)
        try:
            self._attr_device_class = BinarySensorDeviceClass(dc) if dc else None
        except ValueError:
            self._attr_device_class = None

    @property
    def is_on(self) -> bool | None:
        state = self._coordinator.get_state(self._vin, self._descriptor)
        if state is None or state.value is None:
            return None
        value = state.value
        if isinstance(value, bool):
            return value
        token = str(value).strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
        return None
