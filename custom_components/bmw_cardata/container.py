"""Reconcile BMW CarData telematics containers with the desired descriptor set.

BMW limits an account to 10 containers. The per-container descriptor cap is not
documented; we chunk defensively and shrink the chunk size if a create fails.
"""

from __future__ import annotations

import hashlib
import logging

from .api import CardataApiClient, CardataApiError

_LOGGER = logging.getLogger(__name__)

CONTAINER_NAME_PREFIX = "HA bmw_cardata"
CONTAINER_PURPOSE = "Home Assistant BMW CarData integration"
MAX_CONTAINERS = 8
# Try to fit the whole descriptor set into one container first; the create loop
# halves this and retries if BMW rejects an over-large container. Fewer
# containers => fewer telematicData calls per poll => less quota burn.
DEFAULT_CHUNK_SIZE = 500


def descriptor_signature(descriptors: list[str]) -> str:
    joined = "|".join(sorted(set(descriptors)))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def async_reconcile_containers(
    api: CardataApiClient,
    *,
    desired: list[str],
    known_ids: list[str],
    known_signature: str | None,
) -> tuple[list[str], str]:
    """Ensure containers exist covering ``desired``. Returns (container_ids, signature).

    Reuses existing containers when the signature is unchanged and BMW still
    lists them; otherwise deletes the ones we own and recreates.
    """
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

    if (
        known_signature == signature
        and known_ids
        and all(cid in ours for cid in known_ids)
    ):
        _LOGGER.debug("Reusing %d existing CarData container(s)", len(known_ids))
        return list(known_ids), signature

    # Rebuild: drop every container we own, then create fresh ones.
    for cid in ours:
        try:
            await api.delete_container(cid)
            _LOGGER.debug("Deleted stale CarData container %s", cid)
        except CardataApiError as err:
            _LOGGER.warning("Could not delete container %s: %s", cid, err)

    chunk_size = DEFAULT_CHUNK_SIZE
    while chunk_size >= 10:
        chunks = _chunks(sorted(set(desired)), chunk_size)
        if len(chunks) > MAX_CONTAINERS:
            chunk_size = max(10, -(-len(desired) // MAX_CONTAINERS))
            chunks = _chunks(sorted(set(desired)), chunk_size)
        created: list[str] = []
        try:
            for index, chunk in enumerate(chunks):
                name = f"{CONTAINER_NAME_PREFIX} {signature} #{index + 1}"
                created.append(
                    await api.create_container(
                        name=name, purpose=CONTAINER_PURPOSE, descriptors=chunk
                    )
                )
            _LOGGER.info(
                "Created %d CarData container(s) for %d descriptors",
                len(created),
                len(desired),
            )
            return created, signature
        except CardataApiError as err:
            _LOGGER.warning(
                "Container create failed at chunk size %d (%s); retrying smaller",
                chunk_size,
                err,
            )
            for cid in created:
                try:
                    await api.delete_container(cid)
                except CardataApiError:
                    pass
            chunk_size //= 2

    raise CardataApiError("Unable to create CarData containers for the descriptor set")
