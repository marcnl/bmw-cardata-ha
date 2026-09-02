"""Diagnostics support for BMW CarData."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import CardataConfigEntry

REDACT = {
    "access_token",
    "id_token",
    "refresh_token",
    "client_id",
    "gcid",
    "vin",
    "vehicles",
    "containers",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CardataConfigEntry
) -> dict[str, Any]:
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    return {
        "entry_data": async_redact_data(dict(entry.data), REDACT),
        "options": dict(entry.options),
        "quota": {
            "used": runtime.quota.used,
            "remaining": runtime.quota.remaining,
        },
        "stream_status": coordinator.stream_status,
        "container_count": len(runtime.container_ids),
        "vehicles": {
            f"vehicle_{i}": {
                "descriptor_count": len(descriptors),
                "descriptors": {
                    d: {"value": s.value, "unit": s.unit, "source": s.source}
                    for d, s in descriptors.items()
                },
            }
            for i, descriptors in enumerate(coordinator.vehicles.values())
        },
    }
