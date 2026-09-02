"""BMW CarData OAuth 2.0 Device Code Flow (with PKCE) and token refresh."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import string
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import DEVICE_CODE_URL, SCOPES, TOKEN_URL

_LOGGER = logging.getLogger(__name__)

_VERIFIER_ALPHABET = string.ascii_letters + string.digits + "-._~"


class CardataAuthError(Exception):
    """Raised when authentication with BMW CarData fails."""


class CardataAuthExpired(CardataAuthError):
    """Raised when the refresh token is no longer valid and reauth is required."""


@dataclass
class TokenBundle:
    """A set of BMW CarData tokens plus the derived expiry."""

    access_token: str
    id_token: str
    refresh_token: str
    gcid: str
    expires_at: float
    scope: str = ""

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "id_token": self.id_token,
            "refresh_token": self.refresh_token,
            "gcid": self.gcid,
            "token_expires_at": self.expires_at,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TokenBundle":
        return cls(
            access_token=data["access_token"],
            id_token=data.get("id_token", ""),
            refresh_token=data["refresh_token"],
            gcid=data.get("gcid", ""),
            expires_at=float(data.get("token_expires_at", 0.0)),
            scope=data.get("scope", ""),
        )

    @classmethod
    def from_response(cls, payload: dict[str, Any]) -> "TokenBundle":
        expires_in = int(payload.get("expires_in", 3600))
        return cls(
            access_token=payload["access_token"],
            id_token=payload.get("id_token", ""),
            refresh_token=payload["refresh_token"],
            gcid=payload.get("gcid", ""),
            expires_at=time.time() + expires_in,
            scope=payload.get("scope", ""),
        )


def generate_pkce_pair() -> tuple[str, str]:
    """Return an (code_verifier, code_challenge) pair using the S256 method."""
    verifier = "".join(secrets.choice(_VERIFIER_ALPHABET) for _ in range(86))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


async def async_request_device_code(
    session: aiohttp.ClientSession, *, client_id: str, code_challenge: str
) -> dict[str, Any]:
    """Start the device code flow. Returns the raw BMW response."""
    data = {
        "client_id": client_id,
        "response_type": "device_code",
        "scope": SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    try:
        async with session.post(
            DEVICE_CODE_URL,
            data=data,
            headers={"Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            body = await response.json(content_type=None)
            if response.status != 200:
                raise CardataAuthError(
                    f"Device code request failed ({response.status}): "
                    f"{body.get('error_description') or body.get('error') or body}"
                )
            return body
    except aiohttp.ClientError as err:
        raise CardataAuthError(f"Network error requesting device code: {err}") from err


async def async_poll_for_tokens(
    session: aiohttp.ClientSession,
    *,
    client_id: str,
    device_code: str,
    code_verifier: str,
    interval: int,
    expires_in: int,
) -> TokenBundle:
    """Poll the token endpoint until the user approves the device (or it times out)."""
    deadline = time.time() + expires_in
    poll_interval = max(interval, 2)
    data = {
        "client_id": client_id,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "code_verifier": code_verifier,
    }
    while time.time() < deadline:
        await asyncio.sleep(poll_interval)
        try:
            async with session.post(
                TOKEN_URL,
                data=data,
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                body = await response.json(content_type=None)
        except aiohttp.ClientError as err:
            raise CardataAuthError(f"Network error polling for tokens: {err}") from err

        if response.status == 200 and "access_token" in body:
            return TokenBundle.from_response(body)

        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            poll_interval += 5
            continue
        raise CardataAuthError(
            f"Token polling failed: {body.get('error_description') or error or body}"
        )

    raise CardataAuthError("Timed out waiting for device authorization")


async def async_refresh_tokens(
    session: aiohttp.ClientSession, *, client_id: str, refresh_token: str
) -> TokenBundle:
    """Exchange a refresh token for a fresh token bundle."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    try:
        async with session.post(
            TOKEN_URL,
            data=data,
            headers={"Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            body = await response.json(content_type=None)
    except aiohttp.ClientError as err:
        raise CardataAuthError(f"Network error refreshing tokens: {err}") from err

    if response.status == 200 and "access_token" in body:
        return TokenBundle.from_response(body)

    error = body.get("error") or body.get("error_description") or body
    if response.status in (400, 401):
        raise CardataAuthExpired(f"Refresh token rejected: {error}")
    raise CardataAuthError(f"Token refresh failed ({response.status}): {error}")
