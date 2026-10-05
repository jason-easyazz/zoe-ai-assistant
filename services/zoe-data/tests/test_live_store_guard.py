"""live_store_guard: a test / harness / script can never write the household palace.

Class fixed (docs/knowledge/memory-loss-audit-2026-10-05.md, bucket a): ~477 suite runs wrote
7,078 ``ingest`` + 860 ``archive`` audit rows for the owner's real id into the LIVE palace because
one test stubbed the drawers collection but not ``MemoryService._audit_collection``, and the only
guard was a directory pin (conftest) that ``--noconftest`` / a script / a harness bypasses.

Each guard assertion has a negative control: the same call from the SERVICE context (no pytest, no
``ZOE_HARNESS``) is allowed, so the test goes red if the guard is deleted (the raise vanishes) and
also if it is widened (the service stops working).
"""

from __future__ import annotations

import os
import types

import pytest

import live_store_guard as g

pytestmark = pytest.mark.ci_safe


@pytest.fixture
def live(tmp_path, monkeypatch):
    """A fake HOME whose ``~/.mempalace`` is the 'live palace' (nothing real is touched)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("ZOE_LIVE_PALACE_DIR", raising=False)
    monkeypatch.delenv("ZOE_HARNESS", raising=False)
    palace = tmp_path / ".mempalace"
    palace.mkdir()
    return str(palace)


def _as_service(monkeypatch):
    monkeypatch.setattr(g, "non_service_context", lambda: "")


def test_pytest_session_cannot_open_the_live_palace(live):
    with pytest.raises(g.LiveStoreViolation, match="live palace"):
        g.assert_palace_open_allowed(live)


def test_control_service_may_open_the_live_palace(live, monkeypatch):
    _as_service(monkeypatch)
    g.assert_palace_open_allowed(live)   # does not raise


def test_a_scratch_dir_is_never_blocked(live, tmp_path):
    g.assert_palace_open_allowed(str(tmp_path / "scratch"))
    g.assert_write_allowed(str(tmp_path / "scratch"), "jason", "ingest")


def test_symlink_and_dotdot_spellings_cannot_dodge_the_check(live, tmp_path):
    link = tmp_path / "innocent"
    os.symlink(live, link)
    with pytest.raises(g.LiveStoreViolation):
        g.assert_palace_open_allowed(str(link))
    with pytest.raises(g.LiveStoreViolation):
        g.assert_palace_open_allowed(os.path.join(str(tmp_path), "x", "..", ".mempalace"))


def test_relocated_install_is_declared_with_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    moved = tmp_path / "data" / "palace"
    moved.mkdir(parents=True)
    g.assert_palace_open_allowed(str(moved))                      # unknown dir: allowed
    monkeypatch.setenv("ZOE_LIVE_PALACE_DIR", str(moved))
    with pytest.raises(g.LiveStoreViolation):
        g.assert_palace_open_allowed(str(moved))                  # declared live: blocked


def test_pytest_write_to_live_is_refused_even_for_a_synthetic_id(live):
    # pytest has no business in the live palace at all — a demo id does not excuse it
    with pytest.raises(g.LiveStoreViolation):
        g.assert_write_allowed(live, "jason", "ingest")
    with pytest.raises(g.LiveStoreViolation):
        g.assert_write_allowed(live, "demo_bar_ab0e0001", "ingest")


def test_harness_context_real_id_refused_synthetic_id_allowed(live, monkeypatch):
    monkeypatch.setattr(g, "non_service_context", lambda: "harness")
    with pytest.raises(g.LiveStoreViolation, match="harness"):
        g.assert_write_allowed(live, "jason", "ingest")
    with pytest.raises(g.LiveStoreViolation):
        g.assert_write_allowed(live, "guest", "ingest")           # a sentinel is synthetic-shaped but never a person
    g.assert_write_allowed(live, "demo_bar_ab0e0001", "ingest")   # declared harness + its own throwaway id


def test_control_service_may_write_the_live_palace(live, monkeypatch):
    _as_service(monkeypatch)
    g.assert_write_allowed(live, "jason", "ingest")


def test_context_detection(monkeypatch):
    assert g.non_service_context() == "pytest"        # we ARE a pytest session
    monkeypatch.setattr(g, "sys", types.SimpleNamespace(modules={}))   # as if pytest were not loaded
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert g.non_service_context() == ""
    monkeypatch.setenv("ZOE_HARNESS", "1")
    assert g.non_service_context() == "harness"
