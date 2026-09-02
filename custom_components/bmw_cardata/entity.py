"""Base entity for BMW CarData descriptor entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import CardataCoordinator
from .descriptors import DESCRIPTORS


class CardataDescriptorEntity(RestoreEntity):
    """Common wiring for one (vin, descriptor) pair."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator: CardataCoordinator, vin: str, descriptor: str) -> None:
        self._coordinator = coordinator
        self._vin = vin
        self._descriptor = descriptor
        self._attr_unique_id = f"{vin}_{descriptor}"
        meta = DESCRIPTORS.get(descriptor, {})
        self._attr_name = meta.get("name") or _fallback_name(descriptor)

    @property
    def vin(self) -> str:
        return self._vin

    @property
    def descriptor(self) -> str:
        return self._descriptor

    @property
    def device_info(self) -> DeviceInfo:
        meta = self._coordinator.metadata.get(self._vin, {})
        info = DeviceInfo(
            identifiers={(DOMAIN, self._vin)},
            manufacturer=meta.get("manufacturer", "BMW"),
            name=meta.get("name") or f"BMW {self._vin[-7:]}",
            serial_number=self._vin,
        )
        if meta.get("model"):
            info["model"] = meta["model"]
        return info

    @property
    def extra_state_attributes(self) -> dict:
        state = self._coordinator.get_state(self._vin, self._descriptor)
        if not state:
            return {}
        attrs: dict = {"source": state.source}
        if state.timestamp:
            attrs["last_reported"] = state.timestamp.isoformat()
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Seed the coordinator from the last known state so entities are not
        # "unknown" between a restart and the first poll/stream message.
        if self._coordinator.get_state(self._vin, self._descriptor) is None:
            last = await self.async_get_last_state()
            if last and last.state not in (None, "unknown", "unavailable"):
                reported = last.attributes.get("last_reported")
                ts = dt_util.parse_datetime(reported) if reported else None
                self._coordinator.restore_descriptor(
                    self._vin,
                    self._descriptor,
                    last.state,
                    last.attributes.get("unit_of_measurement"),
                    ts,
                )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._coordinator.signal_updated, self._handle_signal
            )
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self._coordinator.signal_metadata, self._handle_metadata
            )
        )

    def _handle_signal(self, vin: str, descriptor: str) -> None:
        if vin == self._vin and descriptor == self._descriptor:
            self.async_write_ha_state()

    def _handle_metadata(self, vin: str) -> None:
        if vin == self._vin:
            self.async_write_ha_state()


def restore_registered_descriptors(hass, entry) -> list[tuple[str, str]]:
    """Return (vin, descriptor) pairs for entities this entry registered before.

    Lets a platform recreate its entities on restart even though the coordinator
    starts empty. Unique ids are ``"<vin>_<descriptor>"``; account-level
    diagnostic entities use ``"<entry_id>_..."`` and are skipped.
    """
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    pairs: list[tuple[str, str]] = []
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = reg_entry.unique_id or ""
        if unique_id.startswith(entry.entry_id) or "_vehicle." not in unique_id:
            continue
        vin, descriptor = unique_id.split("_", 1)
        pairs.append((vin, descriptor))
    return pairs


def _fallback_name(descriptor: str) -> str:
    parts = [
        p
        for p in descriptor.replace("_", " ").replace(".", " ").split()
        if p.lower() != "vehicle"
    ]
    return " ".join(p.capitalize() for p in parts) or descriptor
