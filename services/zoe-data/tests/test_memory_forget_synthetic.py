"""POST /api/memories/users/{id}/forget-synthetic — the internal harness-teardown forget.

Contract pinned here: internal token ONLY (missing 401, wrong/unprovisioned 403,
loopback alone never enough); ids must match ``^(demo|test)[-_]``, must not be
allowlisted (an allowlisted id is a real user) and must not be a guest sentinel —
otherwise 403 with the reason and NO deletion; a permitted id gets exactly the
admin forget's ``delete_user`` call. The pattern is pinned against a loosened
copy (negative control) so widening it goes red.
"""
from __future__ import annotations

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import routers.memories as memories_mod
import user_filters
from routers.memories import router as memories_router

pytestmark = pytest.mark.ci_safe

TOKEN = "tok-forget-synthetic"
HDR = {"X-Internal-Token": TOKEN}


class _FakeSvc:
    def __init__(self):
        self.deleted: list[tuple[str, str]] = []

    async def delete_user(self, user_id, *, actor):
        self.deleted.append((user_id, actor))
        return 4


@pytest.fixture
def svc(monkeypatch):
    fake = _FakeSvc()
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", TOKEN)
    monkeypatch.setattr(memories_mod, "_svc", lambda: fake)
    monkeypatch.delenv("ZOE_SYNTHETIC_USER_ALLOWLIST", raising=False)
    return fake


def _post(uid: str, headers=None, client_host: str = "testclient"):
    app = FastAPI()
    app.include_router(memories_router)
    client = TestClient(app, client=(client_host, 50000))
    return client.post(f"/api/memories/users/{uid}/forget-synthetic", headers=headers or {})


@pytest.mark.parametrize("uid", ["demo_bar_0a1b2c3d", "demo-tomb", "test_isolation_a_1", "test-sec-b-4f9c0c"])
def test_demo_and_test_ids_are_deleted(svc, uid):
    r = _post(uid, HDR)
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": uid, "removed": 4, "mode": "synthetic"}
    assert svc.deleted == [(uid, "internal:forget-synthetic")]


@pytest.mark.parametrize("uid", ["jason", "family-admin", "Christine", "testa", "demolition",
                                 "probe-x", "ci_run", "e2e-1", "bench-1", "DEMO_x", "Test_x"])
def test_real_and_other_ids_are_refused(svc, uid):
    r = _post(uid, HDR)
    assert r.status_code == 403 and "refused" in r.json()["detail"]
    assert svc.deleted == []


@pytest.mark.parametrize("uid", ["guest", "anonymous", "voice-guest", "voice-daemon"])
def test_guest_sentinels_are_refused(svc, uid):
    r = _post(uid, HDR)
    assert r.status_code == 403 and "guest" in r.json()["detail"]
    assert svc.deleted == []


def test_allowlisted_demo_id_is_treated_as_real(svc, monkeypatch):
    monkeypatch.setenv("ZOE_SYNTHETIC_USER_ALLOWLIST", "demo_lab_keep")
    r = _post("demo_lab_keep", HDR)
    assert r.status_code == 403 and "allowlisted" in r.json()["detail"]
    assert svc.deleted == []
    assert _post("demo_lab_other", HDR).status_code == 200  # only the listed id is protected


def test_missing_token_is_401_even_from_loopback(svc):
    for host in ("testclient", "127.0.0.1"):
        r = _post("demo_bar_0a1b2c3d", {}, client_host=host)
        assert r.status_code == 401
    assert svc.deleted == []


def test_wrong_token_is_403(svc):
    r = _post("demo_bar_0a1b2c3d", {"X-Internal-Token": "nope"}, client_host="127.0.0.1")
    assert r.status_code == 403 and svc.deleted == []


def test_unprovisioned_token_is_403(svc, monkeypatch):
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "")
    r = _post("demo_bar_0a1b2c3d", HDR)
    assert r.status_code == 403 and svc.deleted == []


def test_admin_session_does_not_substitute_for_the_token(svc):
    r = _post("demo_bar_0a1b2c3d", {"X-Session-ID": "anything"})
    assert r.status_code == 401 and svc.deleted == []


def test_refusal_and_success_are_audited(svc, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger=memories_mod.logger.name):
        _post("jason", HDR)
        _post("demo_bar_0a1b2c3d", HDR)
    lines = [r.getMessage() for r in caplog.records if "MEMORY_FORGET_SYNTHETIC" in r.getMessage()]
    assert any("refused" in m and "jason" in m for m in lines)
    assert any("user=demo_bar_0a1b2c3d removed=4" in m for m in lines)


# ── the rule itself, and its negative control ─────────────────────────────

IDS_REFUSED_BY_THE_NARROW_RULE = ["probe-x", "ci_run", "e2e-1", "bench-1", "DEMO_x"]


def test_pattern_is_pinned():
    assert user_filters.FORGET_SYNTHETIC_RE.pattern == r"^(demo|test)[-_]"
    assert not (user_filters.FORGET_SYNTHETIC_RE.flags & re.IGNORECASE)


def test_negative_control_loosened_pattern_would_admit_more(svc, monkeypatch):
    # Prove the refusal tests above are sensitive to the pattern: swap in the
    # broader batch-filter regex and those ids become erasable.
    monkeypatch.setattr(user_filters, "FORGET_SYNTHETIC_RE", user_filters.SYNTHETIC_USER_RE)
    admitted = [u for u in IDS_REFUSED_BY_THE_NARROW_RULE
                if user_filters.synthetic_forget_refusal(u) is None]
    assert admitted == IDS_REFUSED_BY_THE_NARROW_RULE


def test_whitespace_padded_id_is_refused():
    assert user_filters.synthetic_forget_refusal(" demo_x") is not None
    assert user_filters.synthetic_forget_refusal("") is not None
    assert user_filters.synthetic_forget_refusal(None) is not None
