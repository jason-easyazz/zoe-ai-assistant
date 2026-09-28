"""POST /api/memories/users/{id}/forget-synthetic — the internal harness-teardown forget.

Contract pinned here: internal token ONLY (missing 401, wrong/unprovisioned 403,
loopback alone never enough); ids must be harness-shaped (``FORGET_SYNTHETIC_RE``:
``demo_<tag>_<hex>`` / ``test_<tag>_<hex>`` — never a bare prefix, since Zoe Auth
derives account ids from usernames), must not be allowlisted (an allowlisted id is
a real user), must not be a guest sentinel, and must NOT be a registered Zoe Auth
account (``auth_users``; a failed lookup refuses too) — otherwise 403/409 with the
reason and NO deletion; a permitted id gets exactly the admin forget's
``delete_user`` call. The pattern is pinned against loosened copies (negative
controls) so widening it goes red.
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

    async def not_registered(user_id):
        fake.looked_up.append(user_id)
        return False
    fake.looked_up = []
    monkeypatch.setattr(memories_mod, "_registered_account", not_registered)
    return fake


def _post(uid: str, headers=None, client_host: str = "testclient"):
    app = FastAPI()
    app.include_router(memories_router)
    client = TestClient(app, client=(client_host, 50000))
    return client.post(f"/api/memories/users/{uid}/forget-synthetic", headers=headers or {})


# Exactly what the harnesses mint: samantha_bar `demo_bar_<8 hex>`, the chroma
# rehearsal `demo_b08_<8 hex>`; a test_ family with a hex nonce is the same shape.
HARNESS_IDS = ["demo_bar_0a1b2c3d", "demo_b08_1a2b3c4d", "test_sec_4f9c0c"]
# Username-shaped ids Zoe Auth could hand a REAL account (bare prefix, no hex
# nonce, wrong separator, short/uppercase nonce) — the old `^(demo|test)[-_]`
# rule admitted every one of these.
USERNAME_SHAPED_IDS = ["demo_user", "test_jason", "demo-tomb", "test-sec-b-4f9c0c",
                       "test_isolation_a_1", "demo_bar_abc", "DEMO_bar_0a1b2c3d",
                       "demo_bar_0A1B2C3D", "demo_b08_sentinel", "demo_", "test_x_"]


@pytest.mark.parametrize("uid", HARNESS_IDS)
def test_harness_shaped_unregistered_ids_are_deleted(svc, uid):
    r = _post(uid, HDR)
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": uid, "removed": 4, "mode": "synthetic"}
    assert svc.deleted == [(uid, "internal:forget-synthetic")]
    assert svc.looked_up == [uid]  # the registration check ran before the delete


@pytest.mark.parametrize("uid", USERNAME_SHAPED_IDS)
def test_username_shaped_ids_are_refused_before_any_lookup(svc, uid):
    r = _post(uid, HDR)
    assert r.status_code == 403 and "harness-minted" in r.json()["detail"]
    assert svc.deleted == [] and svc.looked_up == []


def test_registered_account_with_a_harness_shape_is_refused(svc, monkeypatch, caplog):
    import logging

    async def registered(user_id):
        return True
    monkeypatch.setattr(memories_mod, "_registered_account", registered)
    with caplog.at_level(logging.WARNING, logger=memories_mod.logger.name):
        r = _post("demo_bar_0a1b2c3d", HDR)
    assert r.status_code == 403 and "registered account" in r.json()["detail"]
    assert svc.deleted == []
    msgs = [rec.getMessage() for rec in caplog.records if "MEMORY_FORGET_SYNTHETIC" in rec.getMessage()]
    assert msgs == ["MEMORY_FORGET_SYNTHETIC user=demo_bar_0a1b2c3d outcome=refused_registered"]


def test_registration_lookup_failure_refuses_closed(svc, monkeypatch, caplog):
    import logging

    async def broken(user_id):
        raise RuntimeError("relation auth_users does not exist")
    monkeypatch.setattr(memories_mod, "_registered_account", broken)
    with caplog.at_level(logging.WARNING, logger=memories_mod.logger.name):
        r = _post("demo_bar_0a1b2c3d", HDR)
    assert r.status_code == 409 and "could not verify" in r.json()["detail"]
    assert svc.deleted == []
    msgs = [rec.getMessage() for rec in caplog.records if "MEMORY_FORGET_SYNTHETIC" in rec.getMessage()]
    assert len(msgs) == 1 and "outcome=refused_unverified" in msgs[0] and "auth_users" in msgs[0]


def test_registered_account_check_reads_auth_users_not_users():
    """The real lookup targets zoe-auth's account store; zoe-data's ``users``
    mirror is filled by /api/chat for every id and would refuse every teardown."""
    import inspect
    src = inspect.getsource(memories_mod._registered_account)
    assert "FROM auth_users WHERE user_id" in src and "FROM users" not in src


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
    monkeypatch.setenv("ZOE_SYNTHETIC_USER_ALLOWLIST", "demo_lab_c0ffee")
    r = _post("demo_lab_c0ffee", HDR)
    assert r.status_code == 403 and "allowlisted" in r.json()["detail"]
    assert svc.deleted == []
    assert _post("demo_lab_0ther1", HDR).status_code == 403  # allowlist aside, not hex-shaped
    assert _post("demo_lab_0a1b2c", HDR).status_code == 200  # only the listed id is protected


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


def test_failed_delete_is_audited_and_still_400(svc, monkeypatch, caplog):
    """A raise inside delete_user can be a PARTIAL delete (memory rows gone,
    audit rows not) — the outcome must reach the audit log, not just the 400."""
    import logging
    from memory_service import MemoryServiceError

    async def boom(user_id, *, actor):
        raise MemoryServiceError("delete_user failed: audit table locked")
    monkeypatch.setattr(svc, "delete_user", boom)
    with caplog.at_level(logging.WARNING, logger=memories_mod.logger.name):
        r = _post("demo_bar_0a1b2c3d", HDR)
    assert r.status_code == 400 and "audit table locked" in r.json()["detail"]
    lines = [rec for rec in caplog.records if "MEMORY_FORGET_SYNTHETIC" in rec.getMessage()]
    assert len(lines) == 1 and lines[0].levelno >= logging.WARNING
    msg = lines[0].getMessage()
    assert "user=demo_bar_0a1b2c3d" in msg and "outcome=error" in msg and "audit table locked" in msg


# ── the rule itself, and its negative control ─────────────────────────────

IDS_REFUSED_BY_THE_NARROW_RULE = ["probe-x", "ci_run", "e2e-1", "bench-1", "DEMO_x"]


def test_pattern_is_pinned():
    assert user_filters.FORGET_SYNTHETIC_RE.pattern == r"^(demo|test)_[a-z0-9]{1,16}_[0-9a-f]{6,32}$"
    assert not (user_filters.FORGET_SYNTHETIC_RE.flags & re.IGNORECASE)


def test_negative_control_old_bare_prefix_would_admit_username_shapes():
    # The pre-fix rule: any demo-/test_ prefix. Every username-shaped id above
    # passes it — which is exactly the hole (Zoe Auth ids ARE usernames).
    old = re.compile(r"^(demo|test)[-_]")
    admitted = [u for u in USERNAME_SHAPED_IDS if old.match(u)]
    assert admitted == [u for u in USERNAME_SHAPED_IDS if u[:4] in ("demo", "test")]
    assert {"demo_user", "test_jason", "demo-tomb"} <= set(admitted)
    assert not any(user_filters.FORGET_SYNTHETIC_RE.match(u) for u in USERNAME_SHAPED_IDS)


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
