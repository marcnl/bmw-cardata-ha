"""Constants for the BMW CarData integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "bmw_cardata"

# --- OAuth 2.0 Device Code Flow (PKCE) -------------------------------------
OAUTH_BASE: Final = "https://customer.bmwgroup.com"
DEVICE_CODE_URL: Final = f"{OAUTH_BASE}/gcdm/oauth/device/code"
TOKEN_URL: Final = f"{OAUTH_BASE}/gcdm/oauth/token"
SCOPES: Final = "authenticate_user openid cardata:api:read cardata:streaming:read"

# --- REST API ------------------------------------------------------------
API_BASE: Final = "https://api-cardata.bmwgroup.com"
API_VERSION: Final = "v1"

# --- MQTT streaming ----------------------------------------------------
STREAM_HOST: Final = "customer.streaming-cardata.bmwgroup.com"
STREAM_PORT: Final = 9000
STREAM_KEEPALIVE: Final = 30

# --- Quota -------------------------------------------------------------
# BMW enforces 50 REST requests per rolling 24h window. Keep a safety margin.
QUOTA_LIMIT: Final = 50
QUOTA_SAFETY_MARGIN: Final = 4
QUOTA_WINDOW_SECONDS: Final = 24 * 60 * 60

# --- Timing ----------------------------------------------------------
# Access/ID tokens live 3600s; refresh a little early.
TOKEN_REFRESH_MARGIN: Final = 5 * 60
TOKEN_MIN_REFRESH_INTERVAL: Final = 60
# Refresh token lives 14 days and is rotated on every refresh.
DEFAULT_POLL_INTERVAL: Final = 40 * 60
MIN_POLL_INTERVAL: Final = 20 * 60
FRESH_DATA_THRESHOLD: Final = 5 * 60
DAILY_FETCH_INTERVAL: Final = 24 * 60 * 60

# --- Config entry / option keys --------------------------------------
CONF_CLIENT_ID: Final = "client_id"
CONF_ACCESS_TOKEN: Final = "access_token"
CONF_ID_TOKEN: Final = "id_token"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_GCID: Final = "gcid"
CONF_TOKEN_EXPIRES_AT: Final = "token_expires_at"
CONF_CONTAINERS: Final = "containers"
CONF_CONTAINER_SIGNATURE: Final = "container_signature"
CONF_VEHICLES: Final = "vehicles"
CONF_QUOTA_LOG: Final = "quota_log"

OPT_DESCRIPTOR_GROUPS: Final = "descriptor_groups"
OPT_POLL_INTERVAL: Final = "poll_interval"
OPT_ENABLE_STREAM: Final = "enable_stream"
OPT_ENABLE_CHARGING_HISTORY: Final = "enable_charging_history"
OPT_ENABLE_TYRE_DIAGNOSIS: Final = "enable_tyre_diagnosis"

DEFAULT_DESCRIPTOR_GROUPS: Final = ["service", "core"]

# --- Dispatcher signals (formatted with entry_id) ---------------------
SIGNAL_NEW_DESCRIPTOR: Final = "bmw_cardata_new_descriptor_{entry_id}"
SIGNAL_DESCRIPTOR_UPDATED: Final = "bmw_cardata_descriptor_updated_{entry_id}"
SIGNAL_DIAGNOSTICS: Final = "bmw_cardata_diagnostics_{entry_id}"
SIGNAL_VEHICLE_METADATA: Final = "bmw_cardata_vehicle_metadata_{entry_id}"

PLATFORMS: Final = ["binary_sensor", "device_tracker", "sensor"]

# GPS descriptors are consumed by device_tracker, not surfaced as sensors.
LATITUDE_DESCRIPTOR: Final = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
LONGITUDE_DESCRIPTOR: Final = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
HEADING_DESCRIPTOR: Final = "vehicle.cabin.infotainment.navigation.currentLocation.heading"
LOCATION_DESCRIPTORS: Final = {
    "vehicle.cabin.infotainment.navigation.currentLocation.latitude",
    "vehicle.cabin.infotainment.navigation.currentLocation.longitude",
    "vehicle.cabin.infotainment.navigation.currentLocation.heading",
    "vehicle.cabin.infotainment.navigation.currentLocation.altitude",
}

# Values BMW uses to mean "no reading".
INVALID_VALUES: Final = {"INVALID", "-NA-", "NA", "UNKNOWN", "", "NOT_AVAILABLE"}
