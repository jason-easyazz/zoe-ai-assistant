"""An unconfigured forget ledger is LOUD (gap audit 2026-11-13): with ``ZOE_FORGET_LEDGER_SALT`` unset or too short a name forget
is guarded only by the 300 s tombstone, so the reply must not imply "gone for good", ``/health`` publishes the state as an enum,
and startup logs ``FORGET_LEDGER state=<enum>``. Configured: every reply is byte-identical to before. Synthetic names only.

Ledger-dependent replies (name forget, no-match name forget, "keep the contact" undo, confirmed alias spelling) carry the note.
The topic forget (forget_topic) and "forget that" (turn_forget) never read or write the ledger, so they say nothing about it.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import intent_router
import memory_forgotten
import memory_service
from test_honest_confirm_entity_forget import _seeded_svc

pytestmark = pytest.mark.ci_safe

SALT = "unit-test-forget-ledger-secret-0123456789"       # synthetic, >= 16 chars
NOTE = memory_forgotten.UNCONFIGURED_NOTE


@pytest.fixture(autouse=True)
def _ledger():
    memory_forgotten.set_backend(memory_forgotten.MemoryBackend())
    yield
    memory_forgotten.set_backend(None)


def _salt(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(memory_forgotten.SALT_ENV, raising=False)
    else:
        monkeypatch.setenv(memory_forgotten.SALT_ENV, value)


def _forget(name, svc, user="jason"):
    return asyncio.run(intent_router.execute_intent(intent_router.Intent("memory_forget_entity", {"name": name}), user))


UNCONFIGURED = [None, "too-short-salt"]                   # unset; set but under MIN_SALT_CHARS (14 < 16)


def test_configured_replies_are_byte_identical(monkeypatch):
    _salt(monkeypatch, SALT)
    svc = _seeded_svc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    assert _forget("Caitlin", svc) == "Okay — I've forgotten 2 things about Caitlin."
    assert sorted(svc.archived) == ["m1", "m2"]
    assert _forget("Zelda", svc) == "I don't have anything saved about Zelda."
    assert memory_forgotten.ledger_state() == "configured" and memory_forgotten.permanence_note() == ""


@pytest.mark.parametrize("salt", UNCONFIGURED)
def test_unconfigured_name_forget_says_it_plainly_and_still_deletes(monkeypatch, salt):
    _salt(monkeypatch, salt)
    svc = _seeded_svc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    out = _forget("Caitlin", svc)
    assert out == "Okay — I've forgotten 2 things about Caitlin." + NOTE
    assert sorted(svc.archived) == ["m1", "m2"], "the deletion itself must still happen"
    assert _forget("Zelda", svc) == "I don't have anything saved about Zelda." + NOTE
    assert "Caitlin" not in NOTE and memory_forgotten.ledger_state() == "unconfigured"


@pytest.mark.parametrize("salt", [SALT, None])
def test_keep_the_contact_never_promises_permanence_without_the_ledger(monkeypatch, salt):
    import memory_forget_cascade

    _salt(monkeypatch, salt)

    async def _restored(_uid):
        return ["person-1"]

    monkeypatch.setattr(memory_forget_cascade, "restore_contacts", _restored)
    out = asyncio.run(intent_router.execute_intent(
        intent_router.Intent("memory_forget_entity", {"name": "x", "undo": True}), "demo_member_0001"))
    if salt:
        assert out == ("Okay — I've kept the contact. What I forgot about them stays forgotten "
                       "unless you tell me again.")
    else:
        assert out == "Okay — I've kept the contact." + NOTE and "stays forgotten unless" not in out


@pytest.mark.parametrize("salt", [SALT, None, "too-short-salt"])
def test_confirmed_alias_spelling_carries_the_note_only_when_unconfigured(monkeypatch, salt):
    import memory_forget_alias
    import pending_suggestions

    _salt(monkeypatch, salt)
    monkeypatch.setattr(memory_forget_alias, "clear_asked", lambda _u: None)

    async def _ok(_sid, _uid):
        return {"ok": True}

    async def _nothing(_uid):
        return ""

    monkeypatch.setattr(pending_suggestions, "execute_suggestion", _ok)
    monkeypatch.setattr(memory_forget_alias, "next_question", _nothing)
    out = asyncio.run(memory_forget_alias.answer("demo_member_0001", "sug-1", True))
    base = "Done - I've forgotten that spelling too."
    assert out == (base if salt == SALT else base + NOTE)


def _assert_unconfigured_reply(svc):
    """The SAME assertion the positive test makes."""
    assert _forget("Caitlin", svc) == "Okay — I've forgotten 2 things about Caitlin." + NOTE


def test_negative_control_the_assertion_can_see_a_missing_note(monkeypatch):
    """Feature absent (no note) -> the shared assertion must FAIL, proving it can see the fault."""
    _salt(monkeypatch, None)
    monkeypatch.setattr(intent_router, "_forget_permanence_note", lambda: "")
    svc = _seeded_svc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    with pytest.raises(AssertionError):
        _assert_unconfigured_reply(svc)


@pytest.mark.parametrize("salt,state", [(SALT, "configured"), (None, "unconfigured"), ("too-short-salt", "unconfigured")])
def test_startup_marker_carries_only_the_enum(monkeypatch, caplog, salt, state):
    _salt(monkeypatch, salt)
    with caplog.at_level(logging.DEBUG, logger="memory_forgotten"):
        memory_forgotten.log_startup_state()
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs == [f"FORGET_LEDGER state={state}"]
    assert caplog.records[0].levelno == (logging.INFO if state == "configured" else logging.WARNING)
    assert not any(SALT in m or "too-short" in m for m in msgs)


@pytest.mark.parametrize("salt,state", [(SALT, "configured"), (None, "unconfigured"), ("too-short-salt", "unconfigured")])
def test_health_publishes_the_enum(monkeypatch, salt, state):
    import main

    _salt(monkeypatch, salt)
    monkeypatch.setitem(main._pool_health_cache, "checked_at", 0.0)
    body = asyncio.run(main.root_health())
    assert body["forget_ledger"] == state
    assert SALT not in str(body) and "too-short" not in str(body)


def test_salt_length_does_not_leak(monkeypatch, caplog):
    import main

    monkeypatch.setitem(main._pool_health_cache, "checked_at", 0.0)
    seen = []
    for salt in ("a" * 20 + "-synthetic-one", "b" * 61 + "-synthetic-two-longer"):
        _salt(monkeypatch, salt)
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="memory_forgotten"):
            memory_forgotten.log_startup_state()
        marker = [r.getMessage() for r in caplog.records]
        body = asyncio.run(main.root_health())
        seen.append(({k: v for k, v in body.items() if k.startswith("forget_ledger")}, marker))
        assert not any(str(len(salt)) in str(v) for v in seen[-1][0].values())
    assert seen[0] == seen[1] == ({"forget_ledger": "configured"}, ["FORGET_LEDGER state=configured"])


def test_unestablishable_state_fails_closed(monkeypatch, caplog):
    import main

    _salt(monkeypatch, SALT)

    def _boom():
        raise RuntimeError("ledger broken")

    monkeypatch.setattr(memory_forgotten, "ledger_state", _boom)
    monkeypatch.setitem(main._pool_health_cache, "checked_at", 0.0)
    body = asyncio.run(main.root_health())
    assert body["forget_ledger"] == "unconfigured" and body["forget_ledger_reason"] == "module_error"
    # the status then drives zoe_health red under its normal two-failing-runs rule
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "zoe_health_t", pathlib.Path(__file__).resolve().parents[3] / "scripts/maintenance/zoe_health.py")
    zh = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(__import__("sys").modules, "zoe_health_t", zh)
    spec.loader.exec_module(zh)
    chk = [zh.forget_ledger_check(body["forget_ledger"])]
    assert not chk[0].ok and not chk[0].unknown
    _, st = zh.transitions(chk, {})
    lines, _ = zh.transitions(chk, st)
    assert lines and lines[0].startswith("DOWN: forget ledger")


def test_startup_marker_fails_closed(monkeypatch, caplog):
    """main's lifespan guard: when the ledger module's startup call raises, the marker says unconfigured."""
    import inspect
    import main

    src = inspect.getsource(main.lifespan)
    assert 'FORGET_LEDGER state=unconfigured reason=module_error' in src and "state=unknown" not in src
