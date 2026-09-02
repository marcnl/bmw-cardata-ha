"""Async client for the BMW CarData REST API."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .const import API_BASE, API_VERSION
from .quota import CardataQuotaError, QuotaTracker

_LOGGER = logging.getLogger(__name__)

TokenProvider = Callable[[], Awaitable[str]]


class CardataApiError(Exception):
    """Generic BMW CarData REST error."""

    def __init__(self, message: str, *, status: int | None = None, error_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.error_id = error_id


class CardataAuthApiError(CardataApiError):
    """Auth-related REST error (CU-100/101/102/103) -- caller should reauth."""


class CardataContainerInvalid(CardataApiError):
    """The referenced container id is no longer valid (CU-105)."""


class CardataApiClient:
    """Thin wrapper over the CarData endpoints with quota accounting.

    ``token_provider`` returns a currently-valid access token (refreshing if
    needed); it is awaited immediately before each request.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_provider: TokenProvider,
        quota: QuotaTracker,
    ) -> None:
        self._session = session
        self._token_provider = token_provider
        self._quota = quota

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        quota_cost: int = 1,
        retries: int = 2,
    ) -> Any:
        if quota_cost and not self._quota.can_spend(quota_cost):
            raise CardataQuotaError("Skipping request: BMW CarData daily quota nearly exhausted")

        token = await self._token_provider()
        headers = {
            "Authorization": f"Bearer {token}",
            "x-version": API_VERSION,
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        url = f"{API_BASE}{path}"
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    text = await response.text()
                    if quota_cost:
                        self._quota.spend(quota_cost)
                    return self._handle_response(response.status, text)
            except (CardataAuthApiError, CardataContainerInvalid, CardataQuotaError):
                raise
            except CardataApiError as err:
                last_err = err
                if err.status and err.status < 500:
                    raise
            except aiohttp.ClientError as err:
                last_err = CardataApiError(f"Network error: {err}")
            if attempt < retries:
                await asyncio.sleep(2 ** attempt)
        assert last_err is not None
        raise last_err

    @staticmethod
    def _handle_response(status: int, text: str) -> Any:
        import json

        payload: Any = None
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = text

        if status in (200, 201):
            return payload
        if status == 204:
            return {}

        error_id = None
        message = text[:300]
        if isinstance(payload, dict):
            error_id = payload.get("errorId") or payload.get("error")
            message = payload.get("errorMessage") or payload.get("error_description") or message

        if error_id in {"CU-100", "CU-101", "CU-102", "CU-103"} or status == 401:
            raise CardataAuthApiError(message, status=status, error_id=error_id)
        if error_id == "CU-105":
            raise CardataContainerInvalid(message, status=status, error_id=error_id)
        if error_id == "CU-429" or status == 429:
            raise CardataQuotaError(f"BMW rate limit hit: {message}")
        raise CardataApiError(message, status=status, error_id=error_id)

    # --- endpoints -----------------------------------------------------
    async def get_mappings(self) -> list[dict[str, Any]]:
        result = await self._request("GET", "/customers/vehicles/mappings")
        return result if isinstance(result, list) else result.get("vehicles", [])

    async def get_basic_data(self, vin: str) -> dict[str, Any]:
        return await self._request("GET", f"/customers/vehicles/{vin}/basicData")

    async def list_containers(self) -> list[dict[str, Any]]:
        result = await self._request("GET", "/customers/containers", quota_cost=1)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("containers", [])
        return []

    async def get_container(self, container_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/customers/containers/{container_id}")

    async def create_container(
        self, *, name: str, purpose: str, descriptors: list[str]
    ) -> str:
        payload = {"name": name, "purpose": purpose, "technicalDescriptors": descriptors}
        result = await self._request(
            "POST", "/customers/containers", json_body=payload, quota_cost=1
        )
        container_id = result.get("containerId") if isinstance(result, dict) else None
        if not container_id:
            raise CardataApiError(f"create_container: no containerId in response ({result})")
        return container_id

    async def delete_container(self, container_id: str) -> None:
        try:
            await self._request(
                "DELETE", f"/customers/containers/{container_id}", quota_cost=1
            )
        except CardataApiError as err:
            if err.status == 404:
                return
            raise

    async def get_telematic_data(self, vin: str, container_id: str) -> dict[str, Any]:
        result = await self._request(
            "GET",
            f"/customers/vehicles/{vin}/telematicData",
            params={"containerId": container_id},
            quota_cost=1,
        )
        if isinstance(result, dict):
            return result.get("telematicData", result)
        return {}

    async def get_charging_history(
        self, vin: str, *, date_from: str, date_to: str
    ) -> list[dict[str, Any]]:
        result = await self._request(
            "GET",
            f"/customers/vehicles/{vin}/chargingHistory",
            params={"from": date_from, "to": date_to},
            quota_cost=1,
        )
        if isinstance(result, dict):
            return result.get("data") or result.get("sessions") or []
        return result if isinstance(result, list) else []

    async def get_tyre_diagnosis(self, vin: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/customers/vehicles/{vin}/smartMaintenanceTyreDiagnosis",
            quota_cost=1,
        )
