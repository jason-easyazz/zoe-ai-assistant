"""A TYPED turn from a signed-in member records a POSITIVE speaker verdict with source ``typed_signed_in``; guests, panel/kiosk
principals, the internal-token override and every non-typed lane get NO verdict. Placeholder names only. ``ci_safe``."""
from __future__ import annotations

import asyncio
import json

import pytest

import child_safety_floor as csf
import memory_claims_validate as vm
import speaker_verdict as sv
from routers import chat as chat_router

pytestmark = pytest.mark.ci_safe

MEMBER = {"user_id": "member-ada", "role": "user", "username": "ada", "permissions": []}
ADMIN = {"user_id": "member-bo", "role": "admin", "username": "bo", "permissions": []}
CHILD = {"user_id": "member-kit", "role": "child", "username": "kit", "permissions": []}
POS = sv.Verdict(True, "typed_signed_in")


# -- who counts as signed in ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("user", [MEMBER, ADMIN, CHILD])
def test_signed_in_member_typing_gets_the_positive_typed_verdict(user):
    assert sv.typed_verdict(user) == POS


@pytest.mark.parametrize("user", [
    None, {},
    {"user_id": "guest", "role": "guest"},                                              # anonymous / kiosk guest session
    {"user_id": "guest-3f2a", "role": "user"},                                          # a guest-shaped id
    {"user_id": "voice-guest", "role": "guest"},
    {"user_id": "guest", "role": "guest", "panel_id": "panel-1", "username": "panel:panel-1"},   # a device-token (panel) principal
    {**MEMBER, "panel_id": "panel-1"},                                                  # anything stamped as a panel
    {**MEMBER, "auth_degraded": True},                                                  # fail-open auth outage
    {"user_id": "family-admin", "role": "admin", "unauthenticated_override": True},     # header-less legacy promotion
    {**MEMBER, "internal_override": True},                                              # X-Internal-Token + X-Zoe-User-Id
])
def test_no_verdict_for_everyone_else(user):
    assert sv.typed_verdict(user) is None          # None, never a False: nothing was checked


@pytest.mark.parametrize("kw", [{"voice_mode": True}, {"channel": "voice"}, {"channel": "telegram"}, {"channel": "livekit"}])
def test_only_the_typed_chat_lane_qualifies(kw):
    assert sv.typed_verdict(MEMBER, **kw) is None


def test_internal_token_override_is_marked_and_gets_no_verdict(monkeypatch):
    import auth

    monkeypatch.setattr(auth, "_ZOE_INTERNAL_TOKEN", "t0ken-for-test")

    class _Req:
        headers = {"X-Zoe-User-Id": "member-ada", "X-Internal-Token": "t0ken-for-test"}

    got = asyncio.run(auth.resolve_acting_user(_Req()))
    assert got["user_id"] == "member-ada" and got["internal_override"] is True
    assert sv.typed_verdict(got) is None
    assert sv.typed_verdict({k: v for k, v in got.items() if k != "internal_override"}) == POS   # NEGATIVE CONTROL: the marker decides


# -- persistence ---------------------------------------------------------------------------------------------------------

class _Db:
    def __init__(self):
        self.executed = []

    async def execute(self, sql, params=()):
        self.executed.append((sql, params))

    async def execute_fetchall(self, sql, params=()):
        return [{"title": "Trip"}] if "SELECT title FROM chat_sessions" in sql else []

    async def commit(self):
        pass


def _patch_pool(monkeypatch, db):
    import sys
    import types

    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *a):
            return False

    mod = sys.modules.get("db_pool") or types.ModuleType("db_pool")
    monkeypatch.setattr(mod, "get_db_ctx", lambda: _Ctx(), raising=False)
    monkeypatch.setitem(sys.modules, "db_pool", mod)


def _saved_meta(monkeypatch, role="user", user_id="member-ada", **kw):
    db = _Db()
    _patch_pool(monkeypatch, db)
    assert asyncio.run(chat_router._save_chat_message("s1", role, "the gate code is blue", user_id=user_id, **kw))
    return json.loads(next(p for s, p in db.executed if "INSERT INTO chat_messages" in s)[4])


def test_user_row_carries_the_typed_verdict_with_its_source(monkeypatch):
    meta = _saved_meta(monkeypatch, **chat_router._typed_verdict_kw(MEMBER, "chat"))
    assert meta["speaker_verified"] is True and meta["speaker_source"] == "typed_signed_in" and meta["user_id"] == "member-ada"
    assert sv.for_validator(meta) is True and sv.from_metadata(meta) == POS


@pytest.mark.parametrize("user,channel", [({"user_id": "guest", "role": "guest"}, "chat"),
                                          ({"user_id": "guest", "role": "guest", "panel_id": "p1"}, "chat"),
                                          ({**MEMBER, "internal_override": True}, "chat"), (MEMBER, "telegram")])
def test_no_verdict_keys_for_a_guest_panel_override_or_other_lane(monkeypatch, user, channel):
    kw = chat_router._typed_verdict_kw(user, channel)
    assert kw == {}
    meta = _saved_meta(monkeypatch, user_id=user["user_id"], **kw)
    assert "speaker_verified" not in meta and "speaker_source" not in meta
    assert sv.for_validator(meta) is None


def test_voice_lane_rows_are_unchanged(monkeypatch):
    assert "speaker_verified" not in _saved_meta(monkeypatch, speaker_verified=True)      # bare True (voice accepted): still unrecorded
    assert "speaker_verified" not in _saved_meta(monkeypatch)                              # no verdict
    rej = _saved_meta(monkeypatch, speaker_verified=False)                                 # rejection: recorded as before
    assert rej["speaker_verified"] is False and "speaker_source" not in rej
    assert sv.for_validator(rej) is False


def test_assistant_rows_never_carry_the_verdict(monkeypatch):
    meta = _saved_meta(monkeypatch, role="assistant", speaker_verified=True, speaker_source="typed_signed_in")
    assert "speaker_verified" not in meta


def test_a_bare_true_in_metadata_is_not_read_as_a_verdict():
    assert sv.for_validator({"speaker_verified": True}) is None
    assert sv.for_validator(None) is None and sv.for_validator("not json") is None


# -- the claim validator's speaker rule ----------------------------------------------------------------------------------

def _ctx(verified):
    return vm.Context("member-ada", [], [], vm.Graph([]), speaker_verified=verified)


def test_rule_16_passes_for_the_typed_verdict_and_still_abstains_without_one():
    vm.rule_16_speaker(_ctx(sv.for_validator({"speaker_verified": True, "speaker_source": "typed_signed_in"})))
    for verdict in (sv.for_validator({}), sv.for_validator({"speaker_verified": False})):
        with pytest.raises(vm._Fail):
            vm.rule_16_speaker(_ctx(verdict))
    # NEGATIVE CONTROL: before this change a typed row carried nothing, so the typed turn abstained.
    with pytest.raises(vm._Fail):
        vm.rule_16_speaker(_ctx(sv.for_validator(None)))


# -- the child safety floor ----------------------------------------------------------------------------------------------

HAZARD = "how do i turn on the gas stove"


def _floor(monkeypatch, minor, role, uid):
    import enrollment_state

    async def facts(_uid):
        return minor, role

    monkeypatch.setattr(enrollment_state, "_account_facts", facts)
    monkeypatch.delenv(csf.ENV, raising=False)
    csf._SESSIONS.clear()
    p = csf.check(HAZARD, "sess-typed", uid)
    assert p is not None
    return asyncio.run(csf.resolve(p, uid))


def test_signed_in_child_typing_a_hazard_ask_is_still_refused(monkeypatch):
    assert sv.typed_verdict(CHILD) == POS                          # the verdict says WHO: the child account IS signed in ...
    assert _floor(monkeypatch, False, "child", CHILD["user_id"])   # ... and the floor still refuses (role = child)
    assert _floor(monkeypatch, True, "user", CHILD["user_id"])     # a minor-mode account with an adult-shaped role too


def test_signed_in_adult_is_answered_by_the_floor_exactly_as_before(monkeypatch):
    """The owner's stove check: the floor reads the account's role, never the speaker verdict, so this PR changes nothing here."""
    assert sv.typed_verdict(MEMBER) == POS
    assert _floor(monkeypatch, False, "user", MEMBER["user_id"]) is None        # an adult: passed to the brain (as on main)
    assert _floor(monkeypatch, False, "weird-role", MEMBER["user_id"])          # an unknown role is still refused (not an adult)


def test_the_floor_has_no_verdict_input():
    import inspect

    assert "speaker" not in inspect.signature(csf.resolve).parameters and "speaker" not in inspect.signature(csf.check).parameters


# -- the voice-mode header on the STREAMING path (review blocker) ---------------------------------------------------------------

class _Stop(Exception):
    pass


def _first_user_save(monkeypatch, voice_mode):
    """Drive locked_chat_stream just far enough to see the kwargs the user turn is persisted with."""
    seen = {}

    async def ensure(*_a, **_k):
        return None

    async def save(session_id, role, content, user_id=None, **kw):
        seen.update(kw)
        raise _Stop

    monkeypatch.setattr(chat_router, "_ensure_user_and_chat_session", ensure)
    monkeypatch.setattr(chat_router, "_save_chat_message", save)

    async def drive():
        kw = {} if voice_mode is None else {"voice_mode": voice_mode}
        async for _ in chat_router.locked_chat_stream("hello", "s-stream", dict(MEMBER), **kw):
            pass

    with pytest.raises(_Stop):
        asyncio.run(drive())
    return seen


def test_streaming_with_the_voice_mode_header_gets_no_typed_verdict(monkeypatch):
    assert _first_user_save(monkeypatch, True) == {}


@pytest.mark.parametrize("voice_mode", [False, None])
def test_streaming_without_it_gets_the_verdict(monkeypatch, voice_mode):
    assert _first_user_save(monkeypatch, voice_mode) == {"speaker_verified": True, "speaker_source": "typed_signed_in"}


def test_the_endpoint_threads_the_header_into_both_paths():
    """The endpoint reads X-Voice-Mode once and hands it to the streaming generator AND the non-stream save."""
    import inspect

    src = inspect.getsource(chat_router.chat)
    import re

    assert re.search(r"locked_chat_stream\([^)]*voice_mode=is_voice_mode", src, re.S)        # streaming: locked_chat_stream(...)
    assert "_typed_verdict_kw(user, req_channel, voice_mode=is_voice_mode)" in src         # non-stream persist
    assert chat_router._typed_verdict_kw(MEMBER, "chat", voice_mode=True) == {}            # non-stream, header: none
    assert chat_router._typed_verdict_kw(MEMBER, "chat", voice_mode=False)["speaker_source"] == "typed_signed_in"
