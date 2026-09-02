"""Reconcile BMW CarData telematics containers with the desired descriptor set.

BMW rejects a whole ``create_container`` call with ``CU-402`` if *any* descriptor
in it is invalid/deprecated, and does not say which one. We isolate the bad keys
with a quota-capped bisection, then persist them so subsequent runs skip straight
to a clean create. BMW also limits an account to 10 containers and the per
container descriptor cap is undocumented, so we keep everything in one container
when we can and only split if BMW refuses the size.
"""

from __future__ import annotations

import hashlib
import logging

from .api import CardataApiClient, CardataApiError
from .quota import CardataQuotaError

_LOGGER = logging.getLogger(__name__)

CONTAINER_NAME_PREFIX = "HA bmw_cardata"
CONTAINER_PURPOSE = "Home Assistant BMW CarData integration"

# Hard ceiling on create calls during one reconcile so a pathological descriptor
# set cannot drain the 50/day REST budget. When hit, the still-unresolved keys
# are marked bad wholesale; persistence means the next run converges further.
MAX_PROBE_CREATES = 8
MIN_QUOTA_FOR_PROBING = 12


def descriptor_signature(descriptors: list[str]) -> str:
    joined = "|".join(sorted(set(descriptors)))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def _is_invalid_key_error(err: CardataApiError) -> bool:
    return getattr(err, "error_id", None) == "CU-402"


async def async_reconcile_containers(
    api: CardataApiClient,
    *,
    desired: list[str],
    known_ids: list[str],
    known_signature: str | None,
) -> tuple[list[str], str, set[str]]:
    """Ensure a container covering ``desired`` exists.

    Returns ``(container_ids, signature, newly_discovered_bad_descriptors)``.
    """
    desired = sorted(set(desired))
    signature = descriptor_signature(desired)

    existing = await api.list_containers()
    ours = {
        c["containerId"]: c
        for c in existing
        if isinstance(c, dict)
        and isinstance(c.get("containerId"), str)
        and str(c.get("name", "")).startswith(CONTAINER_NAME_PREFIX)
        and c.get("state", "ACTIVE") == "ACTIVE"
    }

    if known_signature == signature and known_ids and all(cid in ours for cid in known_ids):
        _LOGGER.debug("Reusing existing CarData container(s): %s", known_ids)
        return list(known_ids), signature, set()

    for cid in ours:
        try:
            await api.delete_container(cid)
            _LOGGER.debug("Deleted stale CarData container %s", cid)
        except CardataApiError as err:
            _LOGGER.warning("Could not delete container %s: %s", cid, err)

    bad: set[str] = set()
    probe_budget = [MAX_PROBE_CREATES]

    async def _try_create(keys: list[str], *, keep: bool) -> str | None:
        """Create a container for ``keys``. Return its id, or None on CU-402."""
        try:
            cid = await api.create_container(
                name=f"{CONTAINER_NAME_PREFIX} {signature} {_short(keys)}",
                purpose=CONTAINER_PURPOSE,
                descriptors=keys,
            )
        except CardataApiError as err:
            if _is_invalid_key_error(err):
                return None
            raise
        if not keep:
            try:
                await api.delete_container(cid)
            except CardataApiError:
                pass
        return cid

    async def _isolate_bad(keys: list[str]) -> None:
        """Bisect ``keys`` to grow ``bad`` with the invalid ones."""
        if not keys:
            return
        if probe_budget[0] <= 0 or len(keys) == 1:
            bad.update(keys)
            _LOGGER.warning(
                "CarData: marking %d descriptor(s) invalid without further probing "
                "(probe budget spent): %s",
                len(keys),
                ", ".join(keys),
            )
            return
        mid = len(keys) // 2
        for half in (keys[:mid], keys[mid:]):
            if not half:
                continue
            probe_budget[0] -= 1
            cid = await _try_create(half, keep=False)
            if cid is None:
                await _isolate_bad(half)

    # 1. Try the whole set in one container.
    try:
        cid = await _try_create(desired, keep=True)
    except CardataQuotaError:
        raise
    if cid is not None:
        _LOGGER.info("Created CarData container %s (%d descriptors)", cid, len(desired))
        return [cid], signature, set()

    # 2. CU-402: isolate the bad keys (quota permitting) and retry clean.
    if not api.quota.can_spend(MIN_QUOTA_FOR_PROBING):
        raise CardataApiError(
            "Container has invalid descriptor(s) but REST quota is too low to "
            "isolate them today; will retry after the quota resets"
        )
    _LOGGER.warning("Container create hit CU-402; isolating invalid descriptor(s)")
    await _isolate_bad(desired)

    clean = [d for d in desired if d not in bad]
    cid = await _try_create(clean, keep=True)
    if cid is None:
        # Still failing -> give up cleanly rather than loop.
        raise CardataApiError(
            f"Container still rejected after excluding {len(bad)} descriptor(s)"
        )
    _LOGGER.info(
        "Created CarData container %s (%d descriptors, %d excluded as invalid)",
        cid,
        len(clean),
        len(bad),
    )
    return [cid], descriptor_signature(clean), bad


def _short(keys: list[str]) -> str:
    return hashlib.sha1("|".join(keys).encode()).hexdigest()[:6]
