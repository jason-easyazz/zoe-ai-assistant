"""The household authority table (household_authority.py + authority_table.json) - Samantha item 17 / lane F E7.

Invariants pinned here: no child, guest or unrecognised voice gets a high-risk or irreversible action; nobody is ``allow`` on an
irreversible action; ``self_modify`` is never; ``never`` cannot be overridden by a confirmation; every dispatchable intent has a
class; the existing household rules hold (fridge rule, voice is a password, guests never get memory, guests never drive the house).

Negative controls: every ``test_loosened_*`` case feeds the validator a table that breaks one floor and must be refused. By hand
when written (each went red): deleting the Floor-1 block in ``validate`` fails ``test_loosened_child_purchase``; making ``decide``
return ``allow`` for a confirmed ``never`` fails ``test_never_cannot_be_overridden``; deleting the shadow call in
``routers/system.py`` fails ``test_dispatch_schedules_shadow_when_on``.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging

import pytest

pytestmark = pytest.mark.ci_safe  # pure logic + an in-process router; no DB, no network

import household_authority as ha
import write_receipts

RAW = json.loads(ha.TABLE_PATH.read_text(encoding="utf-8"))
T = ha.validate(RAW)


@pytest.fixture(autouse=True)
def _fresh_cache():
    ha.reset_cache()
    yield
    ha.reset_cache()


def _cells():
    for cls, spec in T.classes.items():
        for role in ha.ROLES:
            yield cls, spec["risk"], role, spec["rows"][role]


# ── the shipped table ────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_shipped_table_loads():
    assert ha.table() is not None
    assert set(T.classes) >= {"read_public", "read_private", "household_list", "personal_write", "home_control", "messaging",
                              "purchase", "memory_write_stated", "memory_write_inferred", "memory_forget", "settings",
                              "irreversible_other", "self_modify", "unknown"}


def test_no_child_guest_or_unrecognised_voice_gets_a_risky_action():
    for cls, risk, role, verdict in _cells():
        if risk in ("high", "irreversible") and role in ha.RESTRICTED_ROLES:
            assert verdict == "never", (cls, role)


def test_nobody_is_allowed_an_irreversible_action_without_asking():
    for cls, risk, role, verdict in _cells():
        if risk == "irreversible":
            assert verdict != "allow", (cls, role)


def test_self_modify_is_never_for_everyone():
    assert set(T.classes["self_modify"]["rows"].values()) == {"never"}


def test_never_cannot_be_overridden():
    nevers = [(cls, role) for cls, _, role, v in _cells() if v == "never"]
    assert nevers
    for cls, role in nevers:
        d = ha.decide(cls, role, confirmed=True, tbl=T)
        assert d.outcome == "refuse" and d.verdict == "never", (cls, role)


def test_ask_first_needs_the_yes():
    asks = [(cls, role) for cls, _, role, v in _cells() if v == "ask_first"]
    assert asks
    for cls, role in asks:
        assert ha.decide(cls, role, tbl=T).outcome == "ask"
        assert ha.decide(cls, role, confirmed=True, tbl=T).outcome == "allow"


def test_existing_household_rules_hold():
    rows = {c: s["rows"] for c, s in T.classes.items()}
    # Fridge rule: anyone at the panel edits household list ITEMS; deleting/renaming the list is not for them.
    assert rows["household_list"]["guest"] == rows["household_list"]["unrecognised_voice"] == "allow"
    assert rows["irreversible_other"]["guest"] == "never"
    # Voice is a password: no account, no private read.
    assert rows["read_private"]["guest"] == rows["read_private"]["unrecognised_voice"] == "never"
    # Guests never get memory (affect included).
    for c in ha.MEMORY_CLASSES:
        assert rows[c]["guest"] == rows[c]["unrecognised_voice"] == "never"
    # A guest id may not drive the house (intent-dispatch home-control rule, 2026-10-18).
    assert rows["home_control"]["guest"] == rows["home_control"]["unrecognised_voice"] == "never"
    # Connecting an account is a confirmed handoff (QR on the panel) for grown-ups only.
    assert rows["settings"]["owner"] == "ask_first" and rows["settings"]["child"] == "never"
    # A child's profile needs a guardian; inference about a child, never.
    assert rows["memory_write_stated"]["child"] == "ask_first"
    assert rows["memory_write_inferred"]["child"] == "never"


def test_every_dispatchable_intent_has_a_class_and_no_stale_rows():
    from routers.system import _ALARM_DISPATCH_INTENTS, _DISPATCHABLE_INTENTS

    dispatchable = set(_DISPATCHABLE_INTENTS) | set(_ALARM_DISPATCH_INTENTS)
    assert dispatchable == set(T.intents), (dispatchable ^ set(T.intents))


def test_receipt_kinds_are_real_receipt_kinds():
    for intent, spec in T.intents.items():
        if spec.receipt:
            assert spec.receipt in write_receipts.KINDS, intent


def test_classify():
    assert ha.classify_intent("list_add", {}, tbl=T) == "household_list"
    assert ha.classify_intent("calendar_create", {"title": "x"}, tbl=T) == "personal_write"
    assert ha.classify_intent("calendar_create", {"attendees": ["a"]}, tbl=T) == "calendar_shared"
    assert ha.classify_intent("rm_rf", {}, tbl=T) == "unknown"
    assert ha.decide("no_such_class", "owner", tbl=T).action_class == "unknown"
    assert ha.decide("read_public", "martian", tbl=T).role == "unrecognised_voice"


@pytest.mark.parametrize("uid,auth_role,adult,want", [
    ("voice-guest", "", False, "unrecognised_voice"),
    ("", "", False, "unrecognised_voice"),
    ("anonymous", "", True, "unrecognised_voice"),
    ("guest", "", True, "guest"),
    ("guest-1a2b", "", True, "guest"),
    ("member-a", "guest", True, "guest"),
    ("member-a", "user", False, "child"),          # not positively an adult = child (fail closed)
    ("member-a", "admin", False, "child"),         # an admin role does not make a kid an owner
    ("member-a", "admin", True, "owner"),
    ("member-a", "family-admin", True, "owner"),
    ("member-a", "user", True, "adult"),
])
def test_resolve_role(uid, auth_role, adult, want):
    assert ha.resolve_role(uid, auth_role=auth_role, adult=adult) == want


def test_lookup_role_fails_closed_to_child(monkeypatch):
    async def boom(_uid):
        raise RuntimeError("db down")

    monkeypatch.setattr(ha, "_account_role", boom)
    assert asyncio.run(ha.lookup_role("member-a")) == "child"
    assert asyncio.run(ha.lookup_role("voice-guest")) == "unrecognised_voice"   # no DB needed


# ── negative controls: a table that loosens a floor does not load ─────────────────────────────────────────────────────────────

def _loosen(mutate):
    raw = copy.deepcopy(RAW)
    mutate(raw)
    return raw


@pytest.mark.parametrize("name,mutate", [
    ("child_purchase", lambda r: r["classes"]["purchase"]["rows"].__setitem__("child", "ask_first")),
    ("guest_messaging", lambda r: r["classes"]["messaging"]["rows"].__setitem__("guest", "allow")),
    ("owner_allow_irreversible", lambda r: r["classes"]["memory_forget"]["rows"].__setitem__("owner", "allow")),
    ("self_modify_owner", lambda r: r["classes"]["self_modify"]["rows"].__setitem__("owner", "ask_first")),
    ("guest_private_read", lambda r: r["classes"]["read_private"]["rows"].__setitem__("guest", "allow")),
    ("guest_memory", lambda r: r["classes"]["memory_write_stated"]["rows"].__setitem__("unrecognised_voice", "allow")),
    ("risk_not_a_risk", lambda r: r["classes"]["purchase"].__setitem__("risk", "medium")),
    ("risk_downgrade_hides_floor", lambda r: (r["classes"]["purchase"].__setitem__("risk", "low"),
                                              r["classes"]["purchase"]["rows"].__setitem__("child", "allow"))),
    ("forget_remapped_to_read", lambda r: r["intents"].__setitem__("memory_retire", {"class": "read_public"})),
    ("memory_store_remapped", lambda r: r["intents"].__setitem__("memory_store", {"class": "panel_utility"})),
    ("unknown_allows", lambda r: r["classes"]["unknown"]["rows"].__setitem__("owner", "allow")),
    ("unknown_removed", lambda r: r["classes"].pop("unknown")),
    ("self_modify_removed", lambda r: r["classes"].pop("self_modify")),
    ("missing_role_row", lambda r: r["classes"]["read_public"]["rows"].pop("child")),
    ("bad_verdict", lambda r: r["classes"]["read_public"]["rows"].__setitem__("owner", "sure")),
    ("intent_to_missing_class", lambda r: r["intents"].__setitem__("list_add", {"class": "nope"})),
    ("extra_role", lambda r: r.__setitem__("roles", r["roles"] + ["superuser"])),
])
def test_loosened(name, mutate):
    with pytest.raises(ha.AuthorityTableError):
        ha.validate(_loosen(mutate))


def test_invalid_table_file_refuses_everything(monkeypatch, tmp_path):
    bad = tmp_path / "authority_table.json"
    bad.write_text(json.dumps(_loosen(lambda r: r["classes"]["purchase"]["rows"].__setitem__("child", "allow"))))
    monkeypatch.setattr(ha, "TABLE_PATH", bad)
    ha.reset_cache()
    assert ha.table() is None
    d = ha.decide("read_public", "owner")
    assert (d.outcome, d.reason) == ("refuse", "table_invalid")
    assert ha.classify_intent("list_add") == "unknown"


# ── log-only wiring at the brain's tool dispatch ──────────────────────────────────────────────────────────────────────────────

def test_shadow_check_is_dark_by_default(monkeypatch, caplog):
    monkeypatch.delenv("ZOE_PROBLEM_LOOP", raising=False)
    caplog.set_level(logging.INFO, logger="household_authority")
    assert asyncio.run(ha.shadow_check("list_add", {}, "member-a")) is None
    assert "AUTHORITY_SHADOW" not in caplog.text


def test_shadow_check_logs_would_without_ids_or_slots(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_PROBLEM_LOOP", "shadow")

    async def role(_uid):
        return "guest"

    monkeypatch.setattr(ha, "lookup_role", role)
    caplog.set_level(logging.INFO, logger="household_authority")
    d = asyncio.run(ha.shadow_check("smart_home", {"room": "secret-room-x"}, "member-zz9"))
    assert d is not None and d.outcome == "refuse"
    line = next(r.getMessage() for r in caplog.records if "AUTHORITY_SHADOW" in r.getMessage())
    assert "would=refuse" in line and "class=home_control" in line and "role=guest" in line
    assert "member-zz9" not in line and "secret-room-x" not in line


def _dispatch(monkeypatch, mode):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import auth
    import intent_router
    from routers.system import router as system_router

    if mode is None:
        monkeypatch.delenv("ZOE_PROBLEM_LOOP", raising=False)
    else:
        monkeypatch.setenv("ZOE_PROBLEM_LOOP", mode)
    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "tok")
    seen = []

    async def fake_shadow(intent, slots, user_id):
        seen.append(intent)
        return ha.Decision("household_list", "guest", "never", "refuse", "never")   # a refusal must change nothing

    async def fake_execute_intent(intent, user_id="x"):
        return "done"

    monkeypatch.setattr(ha, "shadow_check", fake_shadow)
    monkeypatch.setattr(intent_router, "execute_intent", fake_execute_intent)
    app = FastAPI()
    app.include_router(system_router)
    resp = TestClient(app).post("/api/system/intent-dispatch", json={"user_id": "member-a", "intent": "list_show", "slots": {}},
                                headers={"X-Internal-Token": "tok"})
    return resp, seen


def test_dispatch_does_not_schedule_shadow_when_off(monkeypatch):
    resp, seen = _dispatch(monkeypatch, None)
    assert resp.status_code == 200 and resp.json() == {"intent": "list_show", "ok": True, "result": "done"}
    assert seen == []


def test_dispatch_schedules_shadow_when_on(monkeypatch):
    resp, seen = _dispatch(monkeypatch, "shadow")
    # Same response as with the flag off, even though the (fake) table would refuse: log-only.
    assert resp.status_code == 200 and resp.json() == {"intent": "list_show", "ok": True, "result": "done"}
    assert seen == ["list_show"]
