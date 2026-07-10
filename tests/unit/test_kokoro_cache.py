"""Unit tests for the Kokoro sidecar's persistent, frequency-ranked phrase cache.

Pure-logic only — no model load, no CUDA, no HTTP.  The module's heavy deps
(torch/kokoro) are lazy-imported inside synth functions, and the top-level
imports (fastapi/pydantic/uvicorn) are slim-dep-green, so importing the module
here is safe on the GitHub runner.  Marked ``ci_safe`` accordingly.

What these lock in (the load-bearing behaviour):
- persistence round-trips (manifest + WAV files survive a "restart"),
- reload seeds the hot set ranked by hit count and bounded by _CACHE_MAX_ENTRIES,
- eviction drops the COLDEST (lowest hits) when over the disk budget,
- hit counting is frequency-aware, not plain recency,
- disabling the flag / a broken dir is fail-open (byte-identical in-memory LRU).
"""
import importlib.util
import pathlib
import sys

import pytest

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe


_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts" / "setup" / "kokoro_sidecar.py"
)


@pytest.fixture(scope="module")
def kok():
    """Import kokoro_sidecar once (no model load — synth deps are lazy)."""
    spec = importlib.util.spec_from_file_location("kokoro_sidecar", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["kokoro_sidecar"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _clean_state(kok):
    """Reset the module-global cache/meta between tests."""
    kok._phrase_cache.clear()
    kok._cache_meta.clear()
    kok._cache_dirty = False
    yield
    kok._phrase_cache.clear()
    kok._cache_meta.clear()
    kok._cache_dirty = False


# ─── filename / manifest round-trip ────────────────────────────────────────────

def test_key_filename_is_deterministic_and_safe(kok):
    fn = kok._key_filename("Turning on the lights.")
    assert fn.endswith(".wav")
    assert fn == kok._key_filename("Turning on the lights.")
    # sha256 hex → filesystem-safe even for keys with slashes/quotes.
    assert kok._key_filename('a/b "c"').replace(".wav", "").isalnum()


def test_manifest_write_read_roundtrip(kok, tmp_path):
    entries = {"hello": {"hits": 3, "last_used": 111.0, "bytes": 42}}
    kok._write_manifest(tmp_path, entries)
    assert kok._read_manifest(tmp_path) == entries


def test_read_manifest_failopen_on_missing_or_garbage(kok, tmp_path):
    assert kok._read_manifest(tmp_path) == {}  # no file
    (tmp_path / kok._MANIFEST_NAME).write_text("{not json", "utf-8")
    assert kok._read_manifest(tmp_path) == {}  # corrupt


def test_read_manifest_skips_malformed_entry_values(kok, tmp_path):
    # A dict `entries` map with one bad (non-dict) value must not poison the rest
    # or AttributeError inside flush/reload — the bad row is dropped, good kept.
    import json as _json
    (tmp_path / kok._MANIFEST_NAME).write_text(
        _json.dumps({"entries": {"ok": {"hits": 2}, "bad": "not-a-dict"}}), "utf-8"
    )
    entries = kok._read_manifest(tmp_path)
    assert set(entries) == {"ok"}
    # And reload over the same corrupt manifest is fail-open (no raise).
    reloaded, meta = kok._reload_from_disk(tmp_path, max_entries=10)
    assert "bad" not in meta


def test_read_manifest_coerces_garbage_scalars(kok, tmp_path):
    # Non-numeric scalar fields must be coerced to safe defaults so downstream
    # int()/float() in flush/reload/budget can't raise ValueError (P1).
    import json as _json
    (tmp_path / kok._MANIFEST_NAME).write_text(
        _json.dumps({"entries": {"x": {"hits": "many", "last_used": None, "bytes": "big"}}}),
        "utf-8",
    )
    entries = kok._read_manifest(tmp_path)
    assert entries["x"] == {"hits": 0, "last_used": 0.0, "bytes": 0}
    # Flush + budget over the coerced entries must not raise.
    keep, evict = kok._select_within_budget(entries, max_disk=10, max_bytes=10_000)
    assert keep == ["x"]


# ─── flush → reload (restart survival) ─────────────────────────────────────────

def test_flush_then_reload_restores_hot_set(kok, tmp_path):
    cache = {"sure!": b"WAVSURE", "done!": b"WAVDONE"}
    meta = {
        "sure!": {"hits": 10, "last_used": 200.0, "bytes": 0},
        "done!": {"hits": 4, "last_used": 100.0, "bytes": 0},
    }
    kok._flush_to_disk(tmp_path, cache, meta, max_disk=100, max_bytes=10_000_000)

    # WAV files + manifest exist on disk.
    assert (tmp_path / kok._MANIFEST_NAME).exists()
    for key in cache:
        assert (tmp_path / kok._key_filename(key)).exists()

    # Simulate a restart: fresh reload.
    reloaded, rmeta = kok._reload_from_disk(tmp_path, max_entries=100)
    assert reloaded == cache
    assert rmeta["sure!"]["hits"] == 10
    assert rmeta["done!"]["hits"] == 4


def test_reload_orders_by_hits_and_respects_max_entries(kok, tmp_path):
    cache = {f"p{i}": f"wav{i}".encode() for i in range(5)}
    # p4 hottest … p0 coldest.
    meta = {f"p{i}": {"hits": i, "last_used": float(i), "bytes": 0} for i in range(5)}
    kok._flush_to_disk(tmp_path, cache, meta, max_disk=100, max_bytes=10_000_000)

    reloaded, rmeta = kok._reload_from_disk(tmp_path, max_entries=2)
    # Only the two hottest get their bytes loaded into memory…
    assert set(reloaded) == {"p4", "p3"}
    # …but ALL keys keep their stats so on-disk-only phrases retain their rank.
    assert set(rmeta) == set(cache)


# ─── budget / eviction (frequency-aware, not recency) ──────────────────────────

def test_select_within_budget_evicts_coldest_by_count(kok):
    entries = {f"k{i}": {"hits": i, "last_used": 0.0, "bytes": 1} for i in range(5)}
    keep, evict = kok._select_within_budget(entries, max_disk=2, max_bytes=10_000)
    assert set(keep) == {"k4", "k3"}          # hottest two
    assert set(evict) == {"k0", "k1", "k2"}   # coldest three


def test_select_within_budget_evicts_by_total_bytes(kok):
    entries = {
        "hot": {"hits": 9, "last_used": 0.0, "bytes": 100},
        "warm": {"hits": 5, "last_used": 0.0, "bytes": 100},
        "cold": {"hits": 1, "last_used": 0.0, "bytes": 100},
    }
    keep, evict = kok._select_within_budget(entries, max_disk=100, max_bytes=250)
    assert set(keep) == {"hot", "warm"}
    assert evict == ["cold"]


def test_select_keeps_at_least_hottest_even_if_over_byte_budget(kok):
    entries = {"big": {"hits": 3, "last_used": 0.0, "bytes": 999}}
    keep, evict = kok._select_within_budget(entries, max_disk=100, max_bytes=1)
    assert keep == ["big"] and evict == []


def test_flush_deletes_evicted_wav_files(kok, tmp_path):
    cache = {"hot": b"H", "cold": b"C"}
    meta = {
        "hot": {"hits": 50, "last_used": 2.0, "bytes": 0},
        "cold": {"hits": 1, "last_used": 1.0, "bytes": 0},
    }
    kok._flush_to_disk(tmp_path, cache, meta, max_disk=1, max_bytes=10_000_000)
    assert (tmp_path / kok._key_filename("hot")).exists()
    assert not (tmp_path / kok._key_filename("cold")).exists()
    assert set(kok._read_manifest(tmp_path)) == {"hot"}


# ─── hit counting (frequency-aware) ────────────────────────────────────────────

def test_store_and_get_increment_hit_count(kok):
    kok._cache_store("hello", b"WAV")            # first request
    assert kok._cache_meta["hello"]["hits"] == 1
    for _ in range(3):
        assert kok._cache_get("hello") == b"WAV"  # 3 more requests
    assert kok._cache_meta["hello"]["hits"] == 4
    assert kok._cache_dirty is True


def test_get_miss_does_not_create_meta(kok):
    assert kok._cache_get("never-seen") is None
    assert "never-seen" not in kok._cache_meta


def test_lru_bound_still_enforced_on_bytes(kok, monkeypatch):
    monkeypatch.setattr(kok, "_CACHE_MAX_ENTRIES", 3)
    for i in range(5):
        kok._cache_store(f"k{i}", f"w{i}".encode())
    # Only the 3 most-recently-inserted survive in-memory (LRU by bytes)…
    assert set(kok._phrase_cache) == {"k2", "k3", "k4"}
    # …but frequency meta persists for all (so evicted-from-RAM keeps its rank).
    assert set(kok._cache_meta) == {f"k{i}" for i in range(5)}


# ─── fail-open ─────────────────────────────────────────────────────────────────

def test_flush_failopen_on_unwritable_dir(kok):
    # A path whose parent is a file cannot be mkdir'd → must not raise.
    bad = pathlib.Path("/proc/nonexistent_kokoro_dir/sub")
    kok._flush_to_disk(bad, {"a": b"x"}, {"a": {"hits": 1}}, 10, 10_000)  # no exception


def test_reload_failopen_on_empty_dir(kok, tmp_path):
    reloaded, meta = kok._reload_from_disk(tmp_path, max_entries=10)
    assert reloaded == {} and meta == {}


def test_env_int_rejects_nonpositive_and_garbage(kok, monkeypatch):
    monkeypatch.setenv("ZOE_TEST_INT", "0")
    assert kok._env_int("ZOE_TEST_INT", 1000) == 1000   # 0 → default
    monkeypatch.setenv("ZOE_TEST_INT", "-5")
    assert kok._env_int("ZOE_TEST_INT", 1000) == 1000   # negative → default
    monkeypatch.setenv("ZOE_TEST_INT", "abc")
    assert kok._env_int("ZOE_TEST_INT", 1000) == 1000   # garbage → default
    monkeypatch.setenv("ZOE_TEST_INT", "42")
    assert kok._env_int("ZOE_TEST_INT", 1000) == 42     # valid positive kept


def test_flush_returns_true_on_success_false_on_disk_error(kok, tmp_path):
    assert kok._flush_to_disk(tmp_path, {"a": b"x"}, {"a": {"hits": 1}}, 10, 10_000) is True
    bad = pathlib.Path("/proc/nonexistent_kokoro_dir/sub")
    assert kok._flush_to_disk(bad, {"a": b"x"}, {"a": {"hits": 1}}, 10, 10_000) is False


def test_reload_skips_entry_with_missing_wav(kok, tmp_path):
    # Manifest references a key whose WAV file is absent.
    kok._write_manifest(tmp_path, {"ghost": {"hits": 5, "last_used": 1.0, "bytes": 10}})
    reloaded, meta = kok._reload_from_disk(tmp_path, max_entries=10)
    assert reloaded == {}          # no bytes loaded
    assert meta["ghost"]["hits"] == 5  # but rank preserved
