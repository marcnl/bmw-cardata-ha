"""Sensor platform for BMW CarData."""

from __future__ import annotations

import json
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import CardataConfigEntry, CardataRuntime
from .catalogue import OVERRIDES, normalise_unit
from .const import LOCATION_DESCRIPTORS
from .coordinator import CardataCoordinator
from .descriptors import DESCRIPTORS
from .entity import CardataDescriptorEntity, restore_registered_descriptors

_NUMERIC_TYPES = {"int32", "uint16", "uint32", "int16", "uint8", "float", "double", "int"}


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
        if _is_binary(descriptor, coordinator):
            return
        known.add((vin, descriptor))
        async_add_entities([CardataSensor(coordinator, vin, descriptor)])

    # Recreate entities known from a previous run so they restore state before
    # the first poll/stream message arrives.
    for vin, descriptor in restore_registered_descriptors(hass, entry):
        _add(vin, descriptor)
    for vin, descriptor in coordinator.iter_descriptors():
        _add(vin, descriptor)

    # Listen to both signals: `signal_new` is the normal path, `signal_updated`
    # is a safety net for descriptors that first arrived before this platform
    # finished subscribing. `_add` is idempotent via the `known` set.
    entry.async_on_unload(async_dispatcher_connect(hass, coordinator.signal_new, _add))
    entry.async_on_unload(async_dispatcher_connect(hass, coordinator.signal_updated, _add))

    # Diagnostic sensors (one set per config entry).
    async_add_entities(
        [
            CardataStreamStatusSensor(coordinator, entry.entry_id),
            CardataLastStreamSensor(coordinator, entry.entry_id),
            CardataLastPollSensor(coordinator, entry.entry_id),
            CardataQuotaSensor(coordinator, entry.entry_id, runtime),
        ]
    )


def _is_binary(descriptor: str, coordinator: CardataCoordinator) -> bool:
    override = OVERRIDES.get(descriptor)
    if override and override.binary is not None:
        return override.binary
    if DESCRIPTORS.get(descriptor, {}).get("data_type") == "boolean":
        return True
    return False


class CardataSensor(CardataDescriptorEntity, SensorEntity):
    """A dynamically-created sensor for one BMW descriptor."""

    def __init__(self, coordinator: CardataCoordinator, vin: str, descriptor: str) -> None:
        super().__init__(coordinator, vin, descriptor)
        meta = DESCRIPTORS.get(descriptor, {})
        override = OVERRIDES.get(descriptor)

        unit = (override.unit if override and override.unit else meta.get("unit"))
        self._attr_native_unit_of_measurement = normalise_unit(unit)

        dc = override.device_class if override else None
        sc = override.state_class if override else None
        if dc is None and self._attr_native_unit_of_measurement in ("km", "mi"):
            dc = "distance"
        if sc is None and (meta.get("data_type") in _NUMERIC_TYPES) and dc != "distance":
            sc = "measurement"
        self._attr_device_class = SensorDeviceClass(dc) if dc else None
        self._attr_state_class = SensorStateClass(sc) if sc else None
        if override and override.entity_category == "diagnostic":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        self._data_type = meta.get("data_type")

    @property
    def native_value(self):
        state = self._coordinator.get_state(self._vin, self._descriptor)
        if state is None or state.value is None:
            return None
        value = state.value

        if self._attr_device_class == SensorDeviceClass.TIMESTAMP:
            return _coerce_datetime(value)

        if isinstance(value, (list, dict)):
            return _summarise_complex(value)

        if isinstance(value, str) and value.startswith(("[", "{")):
            try:
                return _summarise_complex(json.loads(value))
            except json.JSONDecodeError:
                return value[:255]

        if self._data_type in _NUMERIC_TYPES or self._attr_state_class:
            try:
                num = float(value)
                return int(num) if num.is_integer() else round(num, 3)
            except (TypeError, ValueError):
                return value
        return value

    @property
    def extra_state_attributes(self) -> dict:
        attrs = dict(super().extra_state_attributes)
        state = self._coordinator.get_state(self._vin, self._descriptor)
        if state and isinstance(state.value, str) and state.value.startswith(("[", "{")):
            try:
                attrs["details"] = json.loads(state.value)
            except json.JSONDecodeError:
                pass
        elif state and isinstance(state.value, (list, dict)):
            attrs["details"] = state.value
        return attrs


_DATETIME_FORMATS = (
    "%m/%d/%Y %H:%M %Z",
    "%m/%d/%Y %H:%M:%S %Z",
    "%Y-%m-%d %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%Y-%m-%d",
)


def _coerce_datetime(value):
    if isinstance(value, datetime):
        return dt_util.as_utc(value)
    text = str(value).strip()
    parsed = dt_util.parse_datetime(text)
    if parsed is None:
        for fmt in _DATETIME_FORMATS:
            try:
                parsed = datetime.strptime(text.replace("UTC", "GMT"), fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return dt_util.as_utc(parsed)


def _summarise_complex(value) -> int | str:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return json.dumps(value)[:255]
    return str(value)[:255]


class _CardataEntryDiagnostic(SensorEntity):
    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: CardataCoordinator, entry_id: str, key: str, name: str) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_name = name

    @property
    def device_info(self):
        from homeassistant.helpers.device_registry import DeviceInfo

        return DeviceInfo(
            identifiers={("bmw_cardata", f"account_{self._entry_id}")},
            manufacturer="BMW",
            name="BMW CarData account",
            entry_type="service",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._coordinator.signal_diagnostics, self.async_write_ha_state
            )
        )


class CardataStreamStatusSensor(_CardataEntryDiagnostic):
    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator, entry_id, "stream_status", "Stream status")

    @property
    def native_value(self):
        return self._coordinator.stream_status

    @property
    def extra_state_attributes(self):
        return {"reason": self._coordinator.stream_status_reason}


class CardataLastStreamSensor(_CardataEntryDiagnostic):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator, entry_id, "last_stream_message", "Last stream message")

    @property
    def native_value(self):
        return self._coordinator.last_stream_message_at


class CardataLastPollSensor(_CardataEntryDiagnostic):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry_id):
        super().__init__(coordinator, entry_id, "last_poll", "Last API poll")

    @property
    def native_value(self):
        return self._coordinator.last_poll_at


class CardataQuotaSensor(_CardataEntryDiagnostic):
    _attr_native_unit_of_measurement = "requests"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry_id, runtime: CardataRuntime):
        super().__init__(coordinator, entry_id, "api_quota_used", "API quota used (24h)")
        self._runtime = runtime

    @property
    def native_value(self):
        return self._runtime.quota.used

    @property
    def extra_state_attributes(self):
        quota = self._runtime.quota
        reset = quota.next_reset
        return {
            "remaining": quota.remaining,
            "resets_at": dt_util.utc_from_timestamp(reset).isoformat() if reset else None,
        }
