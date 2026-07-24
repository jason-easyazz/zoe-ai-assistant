"""Panel-daemon local speaker matching (`scripts/setup/zoe_voice_daemon.py`).

The daemon runs on the Pi and imports hardware/audio deps (pyaudio) at module
level, so this test loads it via importlib with those modules stubbed — no
mic, no network, no models. Guards the W5 local-matching pieces:

1. ``_match_speaker_local`` — best-cosine match over the synced cache,
   returning (user_id, raw score); bad rows are skipped, empty cache → None.
   The ACCEPTANCE decision is the server's, so the raw score is never
   thresholded here.
2. ``_sync_speaker_profiles`` — TTL-gated + single-flight (a second caller
   during an in-flight sync returns without fetching), persists the cache
   0600 with the parent dir created, and keeps the old cache on failure.
3. ``_load_profile_cache_from_disk`` — restores profiles but leaves
   ``fetched_at`` at 0 so the first live sync still refreshes.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

_DAEMON_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup" / "zoe_voice_daemon.py"


@pytest.fixture(scope="module")
def daemon():
    """Import the daemon once with hardware deps stubbed."""
    stubs = {}
    for name in ("pyaudio",):
        if name not in sys.modules:
            stubs[name] = MagicMock()
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("zoe_voice_daemon_under_test", _DAEMON_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev


@pytest.fixture(autouse=True)
def clean_cache(daemon):
    with daemon._profile_cache_lock:
        daemon._profile_cache.update({"fetched_at": 0.0, "profiles": [], "syncing": False})
    yield


def _profile(user_id, vec):
    emb = np.asarray(vec, dtype=np.float32).tobytes()
    return {"user_id": user_id, "display_name": user_id,
            "embedding_base64": base64.b64encode(emb).decode()}


# ── 1. local matching ──────────────────────────────────────────────────────

def test_match_returns_best_user_and_raw_score(daemon):
    with daemon._profile_cache_lock:
        daemon._profile_cache["profiles"] = [
            _profile("jason", [1.0, 0.0, 0.0]),
            _profile("kiddo", [0.0, 1.0, 0.0]),
        ]
    user, score = daemon._match_speaker_local(np.asarray([0.9, 0.1, 0.0], dtype=np.float32))
    assert user == "jason"
    assert 0.9 < score <= 1.0


def test_match_returns_low_score_rather_than_thresholding(daemon):
    # A poor match still returns (user, score): the server decides acceptance.
    with daemon._profile_cache_lock:
        daemon._profile_cache["profiles"] = [_profile("jason", [1.0, 0.0, 0.0])]
    user, score = daemon._match_speaker_local(np.asarray([0.1, 1.0, 0.0], dtype=np.float32))
    assert user == "jason"
    assert score < 0.2


def test_match_empty_cache_and_bad_rows(daemon):
    assert daemon._match_speaker_local(np.ones(3, dtype=np.float32)) is None
    with daemon._profile_cache_lock:
        daemon._profile_cache["profiles"] = [
            {"user_id": "junk", "embedding_base64": "!!not-base64!!"},
            _profile("wrongdim", [1.0, 0.0]),        # dim mismatch vs 3-dim query
            {"user_id": "", "embedding_base64": _profile("x", [1.0, 0.0, 0.0])["embedding_base64"]},
            _profile("jason", [1.0, 0.0, 0.0]),
        ]
    # One bad row (junk base64 / wrong dim / empty user) must cost only that
    # row, never the whole turn's speaker ID.
    user, _ = daemon._match_speaker_local(np.asarray([1.0, 0.0, 0.0], dtype=np.float32))
    assert user == "jason"


# ── 2. sync ────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, profiles):
        self._profiles = profiles

    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True, "threshold": 0.82, "profiles": self._profiles}


def test_sync_fetches_persists_0600_and_creates_parent(daemon, monkeypatch, tmp_path):
    cache_path = tmp_path / "nested" / "speaker_profiles.json"
    monkeypatch.setattr(daemon, "_PROFILE_CACHE_PATH", str(cache_path))
    profiles = [_profile("jason", [1.0, 0.0, 0.0])]
    monkeypatch.setattr(daemon.requests, "get", lambda *a, **k: _Resp(profiles))

    daemon._sync_speaker_profiles(force=True)

    with daemon._profile_cache_lock:
        assert daemon._profile_cache["profiles"] == profiles
        assert daemon._profile_cache["fetched_at"] > 0
        assert daemon._profile_cache["syncing"] is False
    assert json.loads(cache_path.read_text())["profiles"] == profiles
    assert oct(os.stat(cache_path).st_mode & 0o777) == "0o600"


def test_sync_is_ttl_gated_and_single_flight(daemon, monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "_PROFILE_CACHE_PATH", str(tmp_path / "cache.json"))
    calls = []
    monkeypatch.setattr(daemon.requests, "get", lambda *a, **k: (calls.append(1), _Resp([]))[1])

    daemon._sync_speaker_profiles(force=True)
    daemon._sync_speaker_profiles()          # fresh → no fetch
    assert len(calls) == 1

    with daemon._profile_cache_lock:
        daemon._profile_cache["syncing"] = True
    daemon._sync_speaker_profiles(force=True)  # in-flight elsewhere → skip
    assert len(calls) == 1


def test_sync_failure_keeps_cached_profiles(daemon, monkeypatch):
    with daemon._profile_cache_lock:
        daemon._profile_cache["profiles"] = [_profile("jason", [1.0])]

    def _boom(*a, **k):
        raise daemon.requests.exceptions.ConnectionError("server down")

    monkeypatch.setattr(daemon.requests, "get", _boom)
    daemon._sync_speaker_profiles(force=True)
    with daemon._profile_cache_lock:
        assert len(daemon._profile_cache["profiles"]) == 1
        assert daemon._profile_cache["syncing"] is False


# ── 3. disk restore ────────────────────────────────────────────────────────

def test_disk_restore_keeps_fetched_at_zero(daemon, monkeypatch, tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps({"profiles": [_profile("jason", [1.0])]}))
    monkeypatch.setattr(daemon, "_PROFILE_CACHE_PATH", str(cache_path))

    daemon._load_profile_cache_from_disk()

    with daemon._profile_cache_lock:
        assert len(daemon._profile_cache["profiles"]) == 1
        assert daemon._profile_cache["fetched_at"] == 0.0  # first sync still refreshes


# ── 4. startup warmup ──────────────────────────────────────────────────────

class _FakeEncoder:
    def __init__(self):
        self.embedded = []

    def embed_utterance(self, wav):
        self.embedded.append(wav)
        return np.zeros(256, dtype=np.float32)


def _stub_resemblyzer(monkeypatch):
    """Slim CI has no resemblyzer; the warmup imports preprocess_wav lazily."""
    mod = types.ModuleType("resemblyzer")
    mod.preprocess_wav = lambda p: np.zeros(16000, dtype=np.float32)
    monkeypatch.setitem(sys.modules, "resemblyzer", mod)


def test_warmup_runs_full_pipeline_and_cleans_temp(daemon, monkeypatch, tmp_path, caplog):
    import logging
    enc = _FakeEncoder()
    monkeypatch.setattr(daemon, "_get_voice_encoder", lambda: enc)
    monkeypatch.setattr(daemon.tempfile, "tempdir", str(tmp_path))
    _stub_resemblyzer(monkeypatch)
    with caplog.at_level(logging.INFO, logger=daemon.log.name):
        daemon._speaker_id_warmup()
    # the wav was written, preprocessed, and embedded
    assert len(enc.embedded) == 1
    assert "warmed" in caplog.text
    assert list(tmp_path.iterdir()) == []  # temp wav removed


def test_warmup_never_raises_and_cleans_temp_on_failure(daemon, monkeypatch, tmp_path):
    class _BoomEncoder:
        def embed_utterance(self, wav):
            raise RuntimeError("embed exploded")

    monkeypatch.setattr(daemon, "_get_voice_encoder", lambda: _BoomEncoder())
    monkeypatch.setattr(daemon.tempfile, "tempdir", str(tmp_path))
    _stub_resemblyzer(monkeypatch)
    daemon._speaker_id_warmup()  # must not raise
    assert list(tmp_path.iterdir()) == []  # temp wav removed on the failure path


def test_warmup_noop_without_encoder(daemon, monkeypatch):
    monkeypatch.setattr(daemon, "_get_voice_encoder", lambda: None)
    daemon._speaker_id_warmup()  # no encoder → clean no-op
