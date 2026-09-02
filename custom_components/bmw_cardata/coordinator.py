"""In-memory vehicle state store for BMW CarData.

This is a push model (MQTT stream + occasional REST polls), so it is a plain
object rather than a ``DataUpdateCoordinator``. Platforms subscribe to dispatcher
signals for "new descriptor seen" and "descriptor value changed".
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    INVALID_VALUES,
    SIGNAL_DESCRIPTOR_UPDATED,
    SIGNAL_DIAGNOSTICS,
    SIGNAL_NEW_DESCRIPTOR,
    SIGNAL_VEHICLE_METADATA,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class DescriptorState:
    value: Any
    unit: str | None
    timestamp: datetime | None
    source: str  # "stream" | "poll" | "restore"
    updated_at: float = field(default_factory=time.time)


class CardataCoordinator:
    """Owns per-VIN descriptor state and vehicle metadata."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.vehicles: dict[str, dict[str, DescriptorState]] = {}
        self.metadata: dict[str, dict[str, Any]] = {}
        self.allowed_vins: set[str] = set()

        # diagnostics
        self.last_stream_message_at: datetime | None = None
        self.last_poll_at: datetime | None = None
        self.stream_status: str = "disconnected"
        self.stream_status_reason: str | None = None
        self._last_poll_per_vin: dict[str, float] = {}
        self._last_stream_per_vin: dict[str, float] = {}

        self.signal_new = SIGNAL_NEW_DESCRIPTOR.format(entry_id=entry_id)
        self.signal_updated = SIGNAL_DESCRIPTOR_UPDATED.format(entry_id=entry_id)
        self.signal_diagnostics = SIGNAL_DIAGNOSTICS.format(entry_id=entry_id)
        self.signal_metadata = SIGNAL_VEHICLE_METADATA.format(entry_id=entry_id)

    # --- reads --------------------------------------------------------
    def get_state(self, vin: str, descriptor: str) -> DescriptorState | None:
        return self.vehicles.get(vin, {}).get(descriptor)

    def iter_descriptors(self) -> list[tuple[str, str]]:
        return [
            (vin, descriptor)
            for vin, descriptors in self.vehicles.items()
            for descriptor in descriptors
        ]

    def seconds_since_poll(self, vin: str) -> float | None:
        ts = self._last_poll_per_vin.get(vin)
        return None if ts is None else time.time() - ts

    def seconds_since_stream(self, vin: str) -> float | None:
        ts = self._last_stream_per_vin.get(vin)
        return None if ts is None else time.time() - ts

    def newest_data_age(self, vin: str) -> float | None:
        ages = [
            age
            for age in (self.seconds_since_poll(vin), self.seconds_since_stream(vin))
            if age is not None
        ]
        return min(ages) if ages else None

    # --- writes ------------------------------------------------------
    @callback
    def set_allowed_vins(self, vins: set[str]) -> None:
        self.allowed_vins = vins

    @callback
    def update_metadata(self, vin: str, metadata: dict[str, Any]) -> None:
        self.metadata.setdefault(vin, {}).update(metadata)
        async_dispatcher_send(self.hass, self.signal_metadata, vin)

    @callback
    def set_stream_status(self, status: str, reason: str | None = None) -> None:
        self.stream_status = status
        self.stream_status_reason = reason
        async_dispatcher_send(self.hass, self.signal_diagnostics)

    @callback
    def async_ingest(
        self, payload: dict[str, Any], *, source: str
    ) -> None:
        """Merge a ``{"vin": ..., "data": {descriptor: entry}}`` payload."""
        vin = payload.get("vin")
        data = payload.get("data")
        if not vin or not isinstance(data, dict):
            return
        if self.allowed_vins and vin not in self.allowed_vins:
            return

        now = time.time()
        if source == "stream":
            self.last_stream_message_at = dt_util.utcnow()
            self._last_stream_per_vin[vin] = now
        elif source == "poll":
            self.last_poll_at = dt_util.utcnow()
            self._last_poll_per_vin[vin] = now

        store = self.vehicles.setdefault(vin, {})
        new_descriptors: list[str] = []
        changed_descriptors: list[str] = []

        for descriptor, entry in data.items():
            value, unit, ts = _normalise_entry(entry)
            existing = store.get(descriptor)
            if existing is not None and _is_stale(existing.timestamp, ts):
                continue
            state = DescriptorState(value=value, unit=unit, timestamp=ts, source=source)
            store[descriptor] = state
            if existing is None:
                new_descriptors.append(descriptor)
            elif existing.value != value:
                changed_descriptors.append(descriptor)

        for descriptor in new_descriptors:
            async_dispatcher_send(self.hass, self.signal_new, vin, descriptor)
        for descriptor in new_descriptors + changed_descriptors:
            async_dispatcher_send(self.hass, self.signal_updated, vin, descriptor)
        async_dispatcher_send(self.hass, self.signal_diagnostics)

    @callback
    def restore_descriptor(
        self, vin: str, descriptor: str, value: Any, unit: str | None, timestamp: datetime | None
    ) -> None:
        store = self.vehicles.setdefault(vin, {})
        store.setdefault(
            descriptor,
            DescriptorState(value=value, unit=unit, timestamp=timestamp, source="restore"),
        )


def _normalise_entry(entry: Any) -> tuple[Any, str | None, datetime | None]:
    if not isinstance(entry, dict):
        return entry, None, None
    raw_value = entry.get("value")
    unit = entry.get("unit") or None
    ts_raw = entry.get("timestamp")
    timestamp = dt_util.parse_datetime(ts_raw) if isinstance(ts_raw, str) else None

    value: Any = raw_value
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if stripped.upper() in INVALID_VALUES:
            value = None
        else:
            value = stripped
    return value, unit, timestamp


def _is_stale(existing_ts: datetime | None, incoming_ts: datetime | None) -> bool:
    """True when the incoming reading is older than what we already have."""
    if incoming_ts is None:
        return False  # no timestamp -> treat as "now", always apply
    if existing_ts is None:
        return False
    return incoming_ts < existing_ts
