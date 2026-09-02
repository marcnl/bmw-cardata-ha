"""The BMW CarData integration."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import CardataApiClient, CardataApiError, CardataAuthApiError
from .auth import (
    CardataAuthError,
    CardataAuthExpired,
    TokenBundle,
    async_refresh_tokens,
)
from .catalogue import resolve_descriptors
from .const import (
    CONF_CLIENT_ID,
    CONF_CONTAINER_SIGNATURE,
    CONF_CONTAINERS,
    CONF_QUOTA_LOG,
    CONF_VEHICLES,
    DEFAULT_DESCRIPTOR_GROUPS,
    DOMAIN,
    FRESH_DATA_THRESHOLD,
    MIN_POLL_INTERVAL,
    OPT_DESCRIPTOR_GROUPS,
    OPT_ENABLE_STREAM,
    OPT_POLL_INTERVAL,
    PLATFORMS,
    QUOTA_WINDOW_SECONDS,
    TOKEN_MIN_REFRESH_INTERVAL,
    TOKEN_REFRESH_MARGIN,
)
from .container import async_reconcile_containers
from .coordinator import CardataCoordinator
from .quota import QuotaTracker
from .stream import CardataStream

_LOGGER = logging.getLogger(__name__)

type CardataConfigEntry = ConfigEntry["CardataRuntime"]


@dataclass
class CardataRuntime:
    coordinator: CardataCoordinator
    quota: QuotaTracker
    tokens: TokenBundle
    api: CardataApiClient | None = None
    stream: CardataStream | None = None
    container_ids: list[str] = field(default_factory=list)
    _token_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_refresh_attempt: float = 0.0
    _daily_fetch_done: dict[str, float] = field(default_factory=dict)


async def async_setup_entry(hass: HomeAssistant, entry: CardataConfigEntry) -> bool:
    """Set up BMW CarData from a config entry."""
    session = async_get_clientsession(hass)
    tokens = TokenBundle.from_dict(entry.data)
    quota = QuotaTracker(entry.data.get(CONF_QUOTA_LOG, []))
    coordinator = CardataCoordinator(hass, entry.entry_id)

    runtime = CardataRuntime(
        coordinator=coordinator,
        quota=quota,
        tokens=tokens,
    )

    async def token_provider() -> str:
        return await _async_valid_access_token(hass, entry, runtime, session)

    runtime.api = CardataApiClient(session, token_provider, quota)
    entry.runtime_data = runtime

    # Ensure tokens are valid before the first REST call.
    try:
        await token_provider()
    except CardataAuthExpired as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except CardataAuthError as err:
        raise ConfigEntryNotReady(f"Token refresh failed: {err}") from err

    # Vehicle mappings -> allowed VINs.
    try:
        mappings = await runtime.api.get_mappings()
    except CardataAuthApiError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except Exception as err:  # noqa: BLE001 - fall back to cached VINs on any failure
        _LOGGER.warning("Could not fetch vehicle mappings on setup: %s", err)
        mappings = [{"vin": v} for v in entry.data.get(CONF_VEHICLES, [])]

    vins = [m["vin"] for m in mappings if isinstance(m, dict) and m.get("vin")]
    coordinator.set_allowed_vins(set(vins))
    if vins:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_VEHICLES: vins}
        )

    # Reconcile the telematics container(s).
    groups = entry.options.get(OPT_DESCRIPTOR_GROUPS, DEFAULT_DESCRIPTOR_GROUPS)
    desired = resolve_descriptors(groups)
    try:
        container_ids, signature = await async_reconcile_containers(
            runtime.api,
            desired=desired,
            known_ids=entry.data.get(CONF_CONTAINERS, []),
            known_signature=entry.data.get(CONF_CONTAINER_SIGNATURE),
        )
        runtime.container_ids = container_ids
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_CONTAINERS: container_ids,
                CONF_CONTAINER_SIGNATURE: signature,
            },
        )
    except Exception as err:  # noqa: BLE001 - keep the entry usable (stream still works)
        _LOGGER.error("Container reconciliation failed, REST polling disabled: %s", err)
        runtime.container_ids = entry.data.get(CONF_CONTAINERS, [])

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Fetch basic data (model names) in the background.
    entry.async_create_background_task(
        hass, _async_fetch_basic_data(hass, entry, runtime, vins), "bmw_cardata_basic"
    )

    # Streaming.
    if entry.options.get(OPT_ENABLE_STREAM, True) and runtime.tokens.gcid:
        stream = CardataStream(
            hass,
            gcid=runtime.tokens.gcid,
            id_token=runtime.tokens.id_token,
            on_message=lambda data: coordinator.async_ingest(data, source="stream"),
            on_status=coordinator.set_stream_status,
        )
        runtime.stream = stream
        entry.async_create_background_task(
            hass, stream.async_start(), "bmw_cardata_stream"
        )

    # Background loops.
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            lambda now: hass.async_create_task(_async_token_tick(hass, entry, runtime, session)),
            timedelta(minutes=5),
        )
    )
    poll_interval = _effective_poll_interval(entry, runtime, len(vins) or 1)
    _LOGGER.debug(
        "BMW CarData poll interval: %ds (%d container(s), %d vehicle(s))",
        poll_interval,
        len(runtime.container_ids),
        len(vins) or 1,
    )
    entry.async_on_unload(
        async_track_time_interval(
            hass,
            lambda now: hass.async_create_task(_async_poll_tick(hass, entry, runtime)),
            timedelta(seconds=poll_interval),
        )
    )
    # Kick off an initial poll shortly after startup.
    entry.async_create_background_task(
        hass, _async_poll_tick(hass, entry, runtime, initial=True), "bmw_cardata_poll_initial"
    )

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CardataConfigEntry) -> bool:
    runtime: CardataRuntime = entry.runtime_data
    if runtime.stream:
        await runtime.stream.async_stop()
    _persist_quota(hass, entry, runtime)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(hass: HomeAssistant, entry: CardataConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _effective_poll_interval(
    entry: CardataConfigEntry, runtime: CardataRuntime, vin_count: int
) -> int:
    """Poll interval that keeps ``containers x vehicles`` calls/cycle under quota.

    Targets ~36 scheduled telematicData calls/day (leaving headroom under BMW's
    50/24h). A user-set option acts as a lower bound only if it is safe.
    """
    calls_per_cycle = max(1, len(runtime.container_ids) * vin_count)
    target_daily_calls = 36
    auto = int(QUOTA_WINDOW_SECONDS * calls_per_cycle / target_daily_calls)
    safe_floor = max(MIN_POLL_INTERVAL, MIN_POLL_INTERVAL * calls_per_cycle)
    user = entry.options.get(OPT_POLL_INTERVAL)
    if isinstance(user, (int, float)) and user >= safe_floor:
        return int(user)
    return max(auto, safe_floor)


# --- token handling --------------------------------------------------
async def _async_valid_access_token(
    hass: HomeAssistant, entry: CardataConfigEntry, runtime: CardataRuntime, session
) -> str:
    async with runtime._token_lock:
        tokens = runtime.tokens
        if time.time() < tokens.expires_at - TOKEN_REFRESH_MARGIN:
            return tokens.access_token
        if time.time() - runtime._last_refresh_attempt < TOKEN_MIN_REFRESH_INTERVAL:
            return tokens.access_token
        runtime._last_refresh_attempt = time.time()
        try:
            new_tokens = await async_refresh_tokens(
                session,
                client_id=entry.data[CONF_CLIENT_ID],
                refresh_token=tokens.refresh_token,
            )
        except CardataAuthExpired as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        runtime.tokens = new_tokens
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, **new_tokens.as_dict()}
        )
        if runtime.stream:
            hass.async_create_task(runtime.stream.async_update_token(new_tokens.id_token))
        _LOGGER.debug("Refreshed BMW CarData tokens")
        return new_tokens.access_token


async def _async_token_tick(hass, entry, runtime: CardataRuntime, session) -> None:
    try:
        await _async_valid_access_token(hass, entry, runtime, session)
    except ConfigEntryAuthFailed:
        entry.async_start_reauth(hass)
    except CardataAuthError as err:
        _LOGGER.warning("Scheduled token refresh failed: %s", err)


# --- polling --------------------------------------------------------
async def _async_poll_tick(
    hass: HomeAssistant, entry: CardataConfigEntry, runtime: CardataRuntime, *, initial: bool = False
) -> None:
    if not runtime.container_ids:
        return

    vins = sorted(runtime.coordinator.allowed_vins)
    any_data = False
    for vin in vins:
        age = runtime.coordinator.newest_data_age(vin)
        if not initial and age is not None and age < FRESH_DATA_THRESHOLD:
            continue
        merged: dict = {}
        for container_id in runtime.container_ids:
            try:
                data = await runtime.api.get_telematic_data(vin, container_id)
            except CardataAuthApiError:
                entry.async_start_reauth(hass)
                return
            except CardataApiError as err:
                _LOGGER.debug("telematicData %s/%s failed: %s", vin, container_id, err)
                continue
            except Exception as err:  # noqa: BLE001 - quota, network
                _LOGGER.debug("telematicData %s skipped: %s", vin, err)
                return
            if isinstance(data, dict):
                merged.update(data)
        if merged:
            runtime.coordinator.async_ingest({"vin": vin, "data": merged}, source="poll")
            any_data = True
    if any_data:
        _persist_quota(hass, entry, runtime)


async def _async_fetch_basic_data(hass, entry, runtime: CardataRuntime, vins: list[str]) -> None:
    for vin in vins:
        try:
            data = await runtime.api.get_basic_data(vin)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("basicData for %s failed: %s", vin, err)
            continue
        if not isinstance(data, dict):
            continue
        model = data.get("modelName") or data.get("model")
        runtime.coordinator.update_metadata(
            vin,
            {
                "name": model or f"BMW {vin[-7:]}",
                "manufacturer": data.get("brand", "BMW"),
                "model": model,
                "series": data.get("series"),
                "raw": data,
            },
        )
    _persist_quota(hass, entry, runtime)


def _persist_quota(hass: HomeAssistant, entry: CardataConfigEntry, runtime: CardataRuntime) -> None:
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_QUOTA_LOG: runtime.quota.dump()}
    )


# --- services ------------------------------------------------------
async def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "poll_now"):
        return

    async def _poll_now(call) -> None:
        entry = _first_entry(hass, call)
        if entry and getattr(entry, "runtime_data", None):
            await _async_poll_tick(hass, entry, entry.runtime_data, initial=True)

    async def _refresh_tokens(call) -> None:
        entry = _first_entry(hass, call)
        if not entry or not getattr(entry, "runtime_data", None):
            return
        runtime: CardataRuntime = entry.runtime_data
        runtime.tokens.expires_at = 0
        await _async_valid_access_token(
            hass, entry, runtime, async_get_clientsession(hass)
        )

    async def _recreate_container(call) -> None:
        entry = _first_entry(hass, call)
        if not entry or not getattr(entry, "runtime_data", None):
            return
        runtime: CardataRuntime = entry.runtime_data
        groups = entry.options.get(OPT_DESCRIPTOR_GROUPS, DEFAULT_DESCRIPTOR_GROUPS)
        ids, signature = await async_reconcile_containers(
            runtime.api,
            desired=resolve_descriptors(groups),
            known_ids=[],
            known_signature=None,
        )
        runtime.container_ids = ids
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_CONTAINERS: ids, CONF_CONTAINER_SIGNATURE: signature},
        )

    hass.services.async_register(DOMAIN, "poll_now", _poll_now)
    hass.services.async_register(DOMAIN, "refresh_tokens", _refresh_tokens)
    hass.services.async_register(DOMAIN, "recreate_container", _recreate_container)


def _first_entry(hass: HomeAssistant, call) -> CardataConfigEntry | None:
    entry_id = call.data.get("config_entry_id")
    if entry_id:
        return hass.config_entries.async_get_entry(entry_id)
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None
