"""Unit tests for the HA-independent parts of the BMW CarData integration.

Run with:  python -m pytest tests/test_core.py
(no Home Assistant install required)
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "custom_components" / "bmw_cardata"


def _load(name: str):
    """Import a single module file without importing the whole package."""
    spec = importlib.util.spec_from_file_location(f"_bmw_{name}", PKG / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- auth / PKCE ------------------------------------------------------
def test_pkce_pair_is_valid_s256():
    auth = _load("auth")
    verifier, challenge = auth.generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert challenge == expected


def test_token_bundle_roundtrip():
    auth = _load("auth")
    bundle = auth.TokenBundle.from_response(
        {
            "access_token": "a",
            "id_token": "i",
            "refresh_token": "r",
            "gcid": "g",
            "expires_in": 3600,
            "scope": "s",
        }
    )
    assert not bundle.expired
    restored = auth.TokenBundle.from_dict(bundle.as_dict())
    assert restored.access_token == "a"
    assert restored.gcid == "g"


# --- quota -----------------------------------------------------------
def test_quota_stops_before_limit():
    quota = _load("quota")
    tracker = quota.QuotaTracker()
    # limit 50, safety margin 4 -> 46 usable
    for _ in range(46):
        tracker.spend()
    assert tracker.remaining == 0
    with pytest.raises(quota.CardataQuotaError):
        tracker.spend()


def test_quota_prunes_old_events():
    quota = _load("quota")
    old = time.time() - quota.QUOTA_WINDOW_SECONDS - 10
    tracker = quota.QuotaTracker([old, old, old])
    assert tracker.used == 0


# --- catalogue -----------------------------------------------------
def test_resolve_service_group_has_inspection_keys():
    sys.modules.setdefault("_bmw_descriptors", _load("descriptors"))
    catalogue = _load("catalogue")
    # patch its DESCRIPTORS ref to the standalone-loaded one
    catalogue.DESCRIPTORS = sys.modules["_bmw_descriptors"].DESCRIPTORS
    resolved = catalogue.resolve_descriptors(["service"])
    assert "vehicle.status.serviceTime.inspectionDateLegal" in resolved
    assert "vehicle.status.serviceDistance.next" in resolved
    assert "vehicle.status.conditionBasedServices" in resolved
    # always-include
    assert "vehicle.vehicle.travelledDistance" in resolved


def test_resolve_all_returns_full_catalogue():
    catalogue = _load("catalogue")
    catalogue.DESCRIPTORS = _load("descriptors").DESCRIPTORS
    resolved = catalogue.resolve_descriptors(["all"])
    assert len(resolved) >= len(catalogue.DESCRIPTORS)


def test_infer_binary_device_class():
    catalogue = _load("catalogue")
    assert catalogue.infer_binary_device_class("vehicle.body.trunk.isLocked") == "lock"
    assert catalogue.infer_binary_device_class("vehicle.cabin.door.row1.left.isOpen") == "door"
    assert catalogue.infer_binary_device_class("vehicle.body.window.isOpen") == "window"


# --- container signature ------------------------------------------
def test_container_signature_is_order_independent():
    container = _load("container")
    a = container.descriptor_signature(["b", "a", "c"])
    b = container.descriptor_signature(["c", "b", "a"])
    assert a == b
    assert a != container.descriptor_signature(["a", "b"])
