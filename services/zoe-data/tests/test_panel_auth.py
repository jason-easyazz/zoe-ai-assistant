"""Tests for panel_auth device token hashing and lookup helpers."""

import hashlib
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))

from routers.panel_auth import _hash_token, _token_cache, lookup_device_token


def test_hash_token_is_sha256():
    raw = "my-secret-token-abc"
    expected = hashlib.sha256(raw.encode()).hexdigest()
    assert _hash_token(raw) == expected


def test_hash_token_deterministic():
    raw = "same-token"
    assert _hash_token(raw) == _hash_token(raw)


def test_lookup_missing_token_returns_none():
    # A token not in the cache should return None.
    result = lookup_device_token("definitely-not-a-real-token-xyz-999")
    assert result is None


def test_lookup_revoked_token_returns_none():
    raw = "revoked-test-token"
    h = _hash_token(raw)
    _token_cache[h] = {"panel_id": "test-panel", "role": "voice-daemon", "revoked": 1, "expires_at": None}
    result = lookup_device_token(raw)
    assert result is None
    del _token_cache[h]


def test_lookup_valid_token_returns_info():
    raw = "valid-test-token-abc123"
    h = _hash_token(raw)
    _token_cache[h] = {"panel_id": "test-panel", "role": "voice-daemon", "revoked": 0, "expires_at": None}
    result = lookup_device_token(raw)
    assert result is not None
    assert result["panel_id"] == "test-panel"
    del _token_cache[h]
