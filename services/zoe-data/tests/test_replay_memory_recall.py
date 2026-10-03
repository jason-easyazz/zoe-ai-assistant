"""The replay harness reports whether its brain turns ran WITH memory recall.

Since B0.8 the palace is chromadb 1.x; a replay launched on 3.10 (chromadb 0.6.3)
had every palace open refused by memory_service._check_palace_format, every
recall reader logged-and-swallowed it, and the gate scored brain turns without
recall for a week. The harness now emits ``memory_recall: ok|mismatch|error|
disabled`` (preflight with the SERVICE's own guard, plus a log watch for the
mismatch the readers swallow); the probe fails closed on anything but ``ok``.

Slim-dep safe: the preflight's chromadb/memory_service imports are lazy and the
tests substitute a fake chromadb version against a real on-disk SQLite fixture.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe


def _harness():
    cwd = os.getcwd()
    try:
        import replay_samples
        return replay_samples
    finally:
        os.chdir(cwd)


def _palace(tmp_path, sysdb_version: int):
    db = sqlite3.connect(tmp_path / "chroma.sqlite3")
    db.execute("CREATE TABLE migrations (dir TEXT, version INTEGER)")
    db.execute("INSERT INTO migrations VALUES ('sysdb', ?)", (sysdb_version,))
    db.commit()
    db.close()
    return tmp_path


@pytest.fixture
def palace_env(monkeypatch, tmp_path):
    import memory_service

    def setup(sysdb_version: int, client_version: str):
        d = _palace(tmp_path, sysdb_version)
        monkeypatch.setattr(memory_service, "get_memory_service",
                            lambda: SimpleNamespace(_data_dir=str(d)))
        monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(__version__=client_version))
    return setup


def test_preflight_ok_when_client_matches_the_palace(palace_env):
    palace_env(10, "1.5.9")
    state, detail = _harness()._memory_recall_preflight()
    assert state == "ok" and "1.5.9" in detail


def test_preflight_mismatch_on_the_b08_signature(palace_env):
    """NEGATIVE CONTROL: the exact 2026-10-03 condition — a 1.x palace, a 0.6.3 client."""
    palace_env(10, "0.6.3")
    state, detail = _harness()._memory_recall_preflight()
    assert state == "mismatch" and "installed client is 0.6.3" in detail


def test_preflight_unidentifiable_palace_is_error_not_ok(palace_env, tmp_path):
    palace_env(10, "1.5.9")
    (tmp_path / "chroma.sqlite3").unlink()
    sqlite3.connect(tmp_path / "chroma.sqlite3").execute("CREATE TABLE x (y)").connection.close()
    state, _ = _harness()._memory_recall_preflight()
    assert state == "error"


def test_watch_counts_swallowed_recall_failures_and_still_prints(capsys):
    rs = _harness()
    watch = rs._MemoryRecallWatch()
    log = logging.getLogger("memory_service.test_watch")
    log.addHandler(watch)
    try:
        log.warning("memory_service: load_for_prompt failed user=u: palace /p is chromadb "
                    "1.x format but the installed client is 0.6.3; refusing")
        log.warning("unrelated warning")
        log.info("SEAM_RECALL user=u")   # below WARNING: not counted
    finally:
        log.removeHandler(watch)
    assert (watch.mismatch, watch.load_failures) == (1, 1)
    assert "unrelated warning" in capsys.readouterr().err


@pytest.mark.parametrize("brain,preflight,logs,load_failures,expected", [
    (False, "mismatch", 3, 2, "disabled"),   # no brain turns -> recall not exercised
    (True, "ok", 0, 0, "ok"),
    (True, "ok", 1, 0, "mismatch"),          # a runtime mismatch beats a clean preflight
    (True, "mismatch", 0, 0, "mismatch"),
    (True, "error", 0, 0, "error"),
    # Codex P1 (#1811): a clean preflight + swallowed runtime read failures is NOT
    # evidence — brain turns were scored without recall. Mismatch stays the more
    # specific verdict when both are logged.
    (True, "ok", 0, 1, "load-failure"),
    (True, "ok", 2, 1, "mismatch"),
])
def test_recall_state(brain, preflight, logs, load_failures, expected):
    assert _harness()._memory_recall_state(brain, preflight, logs, load_failures) == expected


def test_recall_state_load_failures_default_to_zero():
    # Older callers pass three positionals; the default must not turn them into a failure.
    assert _harness()._memory_recall_state(True, "ok", 0) == "ok"
