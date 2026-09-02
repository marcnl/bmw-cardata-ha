"""Descriptor groups and entity-metadata hints for BMW CarData.

``descriptors.py`` is the generated raw catalogue (name/category/unit/type per
BMW descriptor). This module layers on:

* named groups the user picks in the options flow -> the descriptor set we ask
  BMW to put in our telematics container(s);
* small per-descriptor overrides where the raw catalogue lacks a unit or where a
  Home Assistant ``device_class`` / ``state_class`` materially improves the entity.
"""

from __future__ import annotations

from dataclasses import dataclass

from .descriptors import DESCRIPTORS

# --- Groups ---------------------------------------------------------------
# Categories come from the BMW Telematics Data Catalogue (see descriptors.py).
GROUP_CATEGORIES: dict[str, set[str]] = {
    "service": {"Service", "Teleservice", "Legal"},
    "core": {
        "Vehicle",
        "Vehicle Info",
        "Door",
        "Doors",
        "Window",
        "Tailgate",
        "Hood",
        "Sunroof",
        "Convertible",
        "Alarm",
        "Tire",
        "Speed",
        "GPS",
        "Range",
        "Steering Wheel",
    },
    "ev": {
        "Charging EV",
        "Charging Port",
        "Battery EV",
        "Battery HV",
        "Battery",
        "Range EV",
        "OBFCM PHEV",
        "OBFCM",
    },
    "climate": {
        "Preconditioning",
        "Preconditioning Default",
        "Preconditioning Direct",
        "Climate Timer",
        "Seat",
        "Purification",
    },
    "trip": {"Trip"},
}

GROUP_LABELS: dict[str, str] = {
    "service": "Service & inspection",
    "core": "Core status (mileage, doors, tyres, location)",
    "ev": "EV & charging",
    "climate": "Climate & preconditioning",
    "trip": "Trip statistics",
    "all": "Everything in the catalogue",
}

# Descriptors that must always be present regardless of group selection.
ALWAYS_INCLUDE: set[str] = {
    "vehicle.vehicle.travelledDistance",
    "vehicle.sim.status",
}

# Descriptors present in the compiled catalogue CSV that BMW's container API
# rejects (CU-402) -- typos in the source list, or keys that only exist via a
# dedicated endpoint. Excluded up front so we do not waste quota probing them.
# The integration also learns and persists further bad keys at runtime.
KNOWN_BAD: set[str] = {
    "vehicle.sevice.preferredSevicePartner",  # BMW's own catalogue typo
    "vehicle.serviceDemand.defect.id",
    "vehicle.look.image",  # dedicated endpoint only
    "vehicle.vehicleIdentification.basicVehicleData",  # BASIC_DATA / dedicated
    "vehicle.chassis.axle.wheel.tire.diagnosis",  # dedicated endpoint only
    "vehicle.powertrain.electric.battery.charging.history.sessionsList",  # dedicated
    "vehicle.powertrain.electric.battery.charging.settingsList",  # dedicated
}


def resolve_descriptors(groups: list[str], *, exclude: set[str] | None = None) -> list[str]:
    """Return the sorted descriptor set for the selected groups."""
    if "all" in groups:
        selected = set(DESCRIPTORS)
    else:
        wanted_categories: set[str] = set()
        for group in groups:
            wanted_categories |= GROUP_CATEGORIES.get(group, set())
        selected = {
            descriptor
            for descriptor, meta in DESCRIPTORS.items()
            if meta.get("category") in wanted_categories
        }
    selected |= ALWAYS_INCLUDE
    selected -= KNOWN_BAD
    if exclude:
        selected -= exclude
    return sorted(selected)


# --- Per-descriptor overrides ------------------------------------------
@dataclass(frozen=True)
class EntityHint:
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    entity_category: str | None = None  # "diagnostic" or None
    binary: bool | None = None  # force binary_sensor (True) / sensor (False)


# Keyed by exact descriptor. Only what the raw catalogue gets wrong or omits.
OVERRIDES: dict[str, EntityHint] = {
    "vehicle.vehicle.travelledDistance": EntityHint(
        unit="km", device_class="distance", state_class="total_increasing"
    ),
    "vehicle.status.serviceDistance.next": EntityHint(unit="km", device_class="distance"),
    "vehicle.status.serviceDistance.yellow": EntityHint(
        unit="km", device_class="distance", entity_category="diagnostic"
    ),
    "vehicle.status.serviceTime.inspectionDateLegal": EntityHint(device_class="timestamp"),
    "vehicle.status.serviceTime.yellow": EntityHint(entity_category="diagnostic"),
    "vehicle.status.serviceTime.hUandAuServiceYellow": EntityHint(
        entity_category="diagnostic"
    ),
    "vehicle.status.conditionBasedServicesAverageDistancePerDay": EntityHint(
        unit="km", entity_category="diagnostic"
    ),
    "vehicle.status.conditionBasedServicesCount": EntityHint(entity_category="diagnostic"),
    "vehicle.channel.teleservice.lastAutomaticServiceCallTime": EntityHint(
        device_class="timestamp", entity_category="diagnostic"
    ),
    "vehicle.channel.teleservice.lastManualCallTime": EntityHint(
        device_class="timestamp", entity_category="diagnostic"
    ),
    "vehicle.channel.teleservice.lastBreakdownCallTime": EntityHint(
        device_class="timestamp", entity_category="diagnostic"
    ),
    "vehicle.channel.teleservice.lastTeleserviceReportTime": EntityHint(
        device_class="timestamp", entity_category="diagnostic"
    ),
    "vehicle.drivetrain.batteryManagement.header": EntityHint(
        unit="%", device_class="battery", state_class="measurement"
    ),
    "vehicle.drivetrain.electricEngine.charging.level": EntityHint(
        unit="%", device_class="battery", state_class="measurement"
    ),
    "vehicle.powertrain.electric.battery.stateOfCharge.target": EntityHint(unit="%"),
    "vehicle.drivetrain.electricEngine.remainingElectricRange": EntityHint(
        unit="km", device_class="distance"
    ),
    "vehicle.drivetrain.electricEngine.charging.power": EntityHint(
        unit="kW", device_class="power", state_class="measurement"
    ),
    "vehicle.powertrain.electric.battery.charging.power": EntityHint(
        unit="kW", device_class="power", state_class="measurement"
    ),
    "vehicle.drivetrain.electricEngine.charging.timeToFullyCharged": EntityHint(
        unit="min", device_class="duration"
    ),
    "vehicle.sim.status": EntityHint(entity_category="diagnostic"),
}

# Unit strings BMW uses that differ from Home Assistant's canonical form.
UNIT_NORMALISATION: dict[str, str] = {
    "percent": "%",
    "celsius": "°C",
    "Celsius": "°C",
    "degrees": "°",
    "kPa": "kPa",
    "km/h": "km/h",
}

# --- device_class inference from descriptor path -----------------------
_DOOR_HINTS = ("door.", ".door", "cabin.door")
_WINDOW_HINTS = ("window",)
_LOCK_HINTS = ("islocked", "lockstate", "lockedstatus", "centrallock")


def infer_binary_device_class(descriptor: str) -> str | None:
    lower = descriptor.lower()
    if any(h in lower for h in _LOCK_HINTS):
        return "lock"
    if any(h in lower for h in _WINDOW_HINTS):
        return "window"
    if any(h in lower for h in _DOOR_HINTS) or lower.endswith(".isopen"):
        return "door"
    if "alarm" in lower or "intrusion" in lower:
        return "safety"
    if "isplugged" in lower or "chargingport" in lower:
        return "plug"
    if lower.endswith(".isopen"):
        return "opening"
    return None


def normalise_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    return UNIT_NORMALISATION.get(unit, unit)
