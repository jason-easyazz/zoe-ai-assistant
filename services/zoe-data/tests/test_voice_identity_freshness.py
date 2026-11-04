"""A voice turn's member identity never outlives its source (privacy, 2026-10-13).

Two live defects, reproduced here against the REAL identity block of
``routers.voice_tts.voice_command`` and the REAL ``_resolve_recent_panel_session_user``
freshness check (only the database row and the downstream lanes are faked):

1. ``_VOICE_SESSIONS[panel]["bound_user_id"]`` was stamped by one PIN sign-in or one
   accepted voice claim and never cleared (it was even carried across session
   rollovers). Every later spoken turn on that panel, from ANYONE, ran as that member
   — their calendar, lists, memory — until zoe-data restarted.
2. The attribution fallback ``_resolve_panel_default_user`` returns the panel's LAST
   signed-in member (``ui_panel_sessions ORDER BY last_seen_at``) with no freshness
   check, so the public ``calendar_show`` / ``list_show`` shortcut read that member's
   data for whoever was talking.

The rule now: a member identity comes only from THIS turn's identification (an
accepted voice claim / the PIN replay), a FRESH PIN panel session (the panel
idle-logout window, the existing expiry authority), or the caller's own signed-in
session. Anything else is guest. Every test carries a negative control showing the
same harness DOES resolve the member when a fresh source exists, so "guest" is a
verdict, not a broken rig.

Section 7 (2026-10-14): a fresh member session is EXTENDED only by a turn that
identifies itself as that member (accepted claim, PIN replay, own session) - an
unidentified voice uses it but no longer keeps it alive. The ambient and LiveKit
lanes are pinned in test_ambient_memory_scoping.py and test_livekit_media_authz.py.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.ci_safe

import routers.voice_tts as voice_tts
from routers.voice_tts import voice_command

PANEL = "panel-fresh"
ALICE = "alice"
BOB = "bob"
IDLE_WINDOW_S = 900
DEVICE = {"source": "device", "user_id": "voice-daemon", "panel_id": PANEL}


def _ts(age_s: float) -> str:
    """A ``ui_panel_sessions.last_seen_at`` value written ``age_s`` seconds ago (Postgres TEXT shape)."""
    return (datetime.now(timezone.utc) - timedelta(seconds=age_s)).strftime("%Y-%m-%d %H:%M:%S.%f+00")


class _Cur:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _PanelStore:
    """The two tables voice identity reads: ``ui_panel_sessions`` (one row per panel) and
    ``panel_user_bindings`` (the operator-declared owner). Records heartbeat upserts."""

    def __init__(self):
        self.session: dict | None = None      # {"user_id": str, "last_seen_at": str}
        self.owner: str | None = None
        self.heartbeats: list[tuple] = []

    async def execute(self, sql, params=()):
        u = " ".join(sql.split()).upper()
        if "FROM UI_PANEL_SESSIONS" in u:
            return _Cur(dict(self.session) if self.session else None)
        if "FROM PANEL_USER_BINDINGS" in u:
            return _Cur({"user_id": self.owner} if self.owner else None)
        if u.startswith("INSERT INTO UI_PANEL_SESSIONS"):
            self.heartbeats.append(tuple(params))
            self.session = {"user_id": params[1], "last_seen_at": _ts(0)}
            return _Cur()
        raise AssertionError(f"unexpected SQL in identity fake: {sql!r}")

    async def commit(self):
        return None


class _Audio:
    body = b"RIFF-test"
    media_type = "audio/wav"


def _wire(monkeypatch, store: _PanelStore, *, skybridge_handles: bool = True):
    """Fake everything downstream of the identity block; keep the identity block and the
    freshness resolver real. Returns a record of what each lane ran as."""
    seen: dict = {"skybridge_user": [], "saved_users": [], "exec_users": [], "spawned": []}

    class _Ctx:
        async def __aenter__(self):
            return store

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setitem(sys.modules, "db_pool", types.SimpleNamespace(get_db_ctx=lambda: _Ctx()))

    async def _idle_window():
        return IDLE_WINDOW_S

    monkeypatch.setattr(voice_tts, "_panel_idle_logout_s", _idle_window)

    async def save_chat(session_id, role, content, user_id=None, **_kw):
        seen["saved_users"].append(user_id)
        return True

    async def ensure_session(*_a, **_k):
        return None

    monkeypatch.setitem(sys.modules, "routers.chat", types.SimpleNamespace(
        _save_chat_message=save_chat, _ensure_user_and_chat_session=ensure_session))

    async def resolve_skybridge_request(text, user_id, *, context=None, db=None, **_kw):
        seen["skybridge_user"].append(user_id)
        if not skybridge_handles:
            return {"handled": False}
        return {"handled": True, "spoken_summary": "Here it is.",
                "intent": {"domain": "calendar", "action": "current"},
                "skybridge_context": {"domain": "calendar"}, "cards": []}

    monkeypatch.setitem(sys.modules, "skybridge_service",
                        types.SimpleNamespace(resolve_skybridge_request=resolve_skybridge_request))

    class _Bc:
        async def broadcast(self, *_a, **_k):
            return None

    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=_Bc()))
    monkeypatch.setitem(sys.modules, "semantic_router", types.SimpleNamespace(is_enabled=lambda: False))

    async def _cue(**_kw):
        return {"available": False, "text": ""}

    monkeypatch.setitem(sys.modules, "pi_hybrid_production", types.SimpleNamespace(
        PiHybridProductionConfig=types.SimpleNamespace(from_env=lambda: types.SimpleNamespace(enabled=False)),
        pi_hybrid_production_eligible=lambda _t, config=None: (False, "disabled"),
        processing_cue_packet=_cue, try_pi_hybrid_production=None))

    async def _no_tier(*_a, **_k):
        return None

    monkeypatch.setitem(sys.modules, "fast_tiers", types.SimpleNamespace(resolve=_no_tier))

    # The public-intent shortcut: a household read the intent router answers directly.
    intent = types.SimpleNamespace(name="list_show", slots={})

    async def detect_and_extract(*_a, **_k):
        return None

    async def execute_intent(it, user_id):
        seen["exec_users"].append((it.name, user_id))
        return f"{it.name} for {user_id}"

    monkeypatch.setitem(sys.modules, "intent_router", types.SimpleNamespace(
        detect_intent=lambda *_a, **_k: intent,
        detect_and_extract_intent=detect_and_extract,
        execute_intent=execute_intent,
        Intent=lambda name, slots=None: types.SimpleNamespace(name=name, slots=slots or {})))

    async def can_use(_db, _user, _intent):
        return True

    monkeypatch.setitem(sys.modules, "guest_policy", types.SimpleNamespace(
        PUBLIC_HOUSEHOLD_INTENTS=frozenset({"list_show", "calendar_show"}),
        can_use_voice_intent=can_use,
        record_policy_decision=lambda *_a, **_k: None))

    async def synth(*_a, **_k):
        return _Audio()

    def spawn(coro):
        seen["spawned"].append(asyncio.ensure_future(coro))

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(voice_tts, "synthesize", synth)
    monkeypatch.setattr(voice_tts, "_spawn_bg", spawn)
    monkeypatch.setattr(voice_tts, "_broadcast_skybridge_ui", _noop)
    monkeypatch.setattr(voice_tts, "_broadcast_calendar_ui", _noop)
    monkeypatch.setattr(voice_tts, "_run_voice_memory_passes", _noop)
    monkeypatch.setattr(voice_tts, "_VOICE_SESSIONS", {})
    monkeypatch.setattr(voice_tts, "_PENDING_CONFIRMATIONS", {})
    seen["intent"] = intent
    return seen


def _pin_replay(member):
    """What ``panel_auth.submit_pin`` passes on a correct PIN: the member in the payload AND the internal caller (the only
    caller whose ``identified_user_id`` counts - a device's or a session's payload field is ignored, 2026-10-18)."""
    return {"identified_user_id": member, "_caller": {"source": "pin_resume", "panel_id": PANEL, "user_id": member}}


async def _turn(seen, text="what's on my calendar", **payload):
    # No session_id: the Pi daemon never sends one, so the panel's voice session (and its binding slot) is the server's.
    caller = payload.pop("_caller", None) or dict(DEVICE)
    if "voice_user_id" in payload:
        # a panel claim names the speaker model (ResNet34 is the only one the server acts on; Resemblyzer was retired)
        payload.setdefault("voice_model_id", "wespeaker_resnet34+i8qdq-u8s8+c1")
    body = {"text": text, "panel_id": PANEL, **payload}
    resp = await voice_command(body, caller=caller, stream=False, db=object())
    if seen["spawned"]:
        await asyncio.gather(*seen["spawned"], return_exceptions=True)
        seen["spawned"].clear()
    return resp


def _last(seen, key):
    return seen[key][-1] if seen[key] else None


# --------------------------------------------------------------------------- #
# 1. An expired PIN session means guest
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_after_pin_session_expires_voice_turn_is_guest(monkeypatch):
    store = _PanelStore()
    seen = _wire(monkeypatch, store)

    # Negative control: Alice's PIN session is fresh -> her turn is hers.
    store.session = {"user_id": ALICE, "last_seen_at": _ts(30)}
    await _turn(seen)
    assert _last(seen, "skybridge_user") == ALICE
    assert voice_tts._VOICE_SESSIONS[PANEL].get("bound_user_id") == ALICE

    # The session lapses (older than the idle-logout window). The next voice turn,
    # with no claim, must not run as Alice.
    store.session = {"user_id": ALICE, "last_seen_at": _ts(IDLE_WINDOW_S + 600)}
    seen["saved_users"].clear()
    beats = len(store.heartbeats)
    await _turn(seen)
    assert _last(seen, "skybridge_user") == "guest", "an expired PIN session still identified the speaker"
    assert ALICE not in seen["saved_users"], "a guest's words were saved into the expired member's history"
    assert voice_tts._VOICE_SESSIONS[PANEL].get("bound_user_id") is None, "the binding outlived its session"
    # ... and the lapsed session is not revived by the turn's heartbeat.
    assert len(store.heartbeats) == beats


# --------------------------------------------------------------------------- #
# 2. A binding from member A never applies once A's session is gone
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("panel_row", [
    None,                                                        # no session row at all
    {"user_id": "guest", "last_seen_at": _ts(5)},                # the kiosk reclaimed the panel as guest
    {"user_id": ALICE, "last_seen_at": _ts(6 * 3600)},           # Alice's row, long stale ("last signed in")
], ids=["no_row", "guest_row", "stale_member_row"])
async def test_binding_from_member_never_applies_after_session_gone(monkeypatch, panel_row):
    store = _PanelStore()
    seen = _wire(monkeypatch, store)
    store.session = panel_row
    store.owner = ALICE   # the operator-declared panel owner is not a fresh source either
    # A binding left over from an earlier PIN / claim (what the old code kept forever).
    voice_tts._VOICE_SESSIONS[PANEL] = {"session_id": "vs-1", "last_at": voice_tts.time.monotonic(),
                                        "bound_user_id": ALICE}

    await _turn(seen)

    assert _last(seen, "skybridge_user") == "guest"
    assert ALICE not in seen["saved_users"]
    assert voice_tts._VOICE_SESSIONS[PANEL].get("bound_user_id") is None


@pytest.mark.asyncio
async def test_binding_follows_the_fresh_session_not_the_old_member(monkeypatch):
    """Negative control for the test above: a FRESH session is honoured — and it is the
    session's member (Bob), never the stale binding's (Alice)."""
    store = _PanelStore()
    seen = _wire(monkeypatch, store)
    store.session = {"user_id": BOB, "last_seen_at": _ts(10)}
    voice_tts._VOICE_SESSIONS[PANEL] = {"session_id": "vs-1", "last_at": voice_tts.time.monotonic(),
                                        "bound_user_id": ALICE}

    await _turn(seen)

    assert _last(seen, "skybridge_user") == BOB
    assert voice_tts._VOICE_SESSIONS[PANEL].get("bound_user_id") == BOB
    # An unidentified voice uses the fresh session but never extends it (section 7).
    assert store.heartbeats == []


# --------------------------------------------------------------------------- #
# 3. A voice claim answers for its own turn only
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_voice_claim_applies_only_to_its_own_turn(monkeypatch):
    store = _PanelStore()
    seen = _wire(monkeypatch, store)

    async def consented(_uid, *_model):   # #2024 adds the claim's model id
        return True

    monkeypatch.setattr(voice_tts, "_voice_claim_consented", consented)

    # Negative control: the claimed turn IS Alice's.
    await _turn(seen, voice_user_id=ALICE, voice_score=0.99)
    assert _last(seen, "skybridge_user") == ALICE
    assert voice_tts._VOICE_SESSIONS[PANEL].get("bound_user_id") is None, "a claim wrote a panel binding"

    # The next turn carries no claim (a different voice, or none matched): guest.
    await _turn(seen)
    assert _last(seen, "skybridge_user") == "guest", "a voice claim carried over to the next speaker"

    # A below-threshold claim is no identity either.
    await _turn(seen, voice_user_id=ALICE, voice_score=0.10)
    assert _last(seen, "skybridge_user") == "guest"


# --------------------------------------------------------------------------- #
# 4. The calendar / list public shortcut uses the same fresh identity
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("intent_name", ["list_show", "calendar_show"])
async def test_public_read_shortcut_runs_as_guest_without_fresh_identity(monkeypatch, intent_name):
    store = _PanelStore()
    seen = _wire(monkeypatch, store, skybridge_handles=False)
    seen["intent"].name = intent_name
    # Alice signed in here hours ago: she is the panel's LAST signed-in user, not a fresh one.
    store.session = {"user_id": ALICE, "last_seen_at": _ts(3 * 3600)}

    resp = await _turn(seen)

    assert resp.get("intent") == intent_name, f"rig did not reach the public shortcut: {resp!r}"
    assert seen["exec_users"][-1] == (intent_name, "guest"), (
        "the public read ran as the panel's last signed-in member: %r" % (seen["exec_users"],))


@pytest.mark.asyncio
@pytest.mark.parametrize("intent_name", ["list_show", "calendar_show"])
async def test_public_read_shortcut_runs_as_member_with_fresh_identity(monkeypatch, intent_name):
    """Negative control: with a fresh PIN session the same shortcut reads the member's data."""
    store = _PanelStore()
    seen = _wire(monkeypatch, store, skybridge_handles=False)
    seen["intent"].name = intent_name
    store.session = {"user_id": ALICE, "last_seen_at": _ts(20)}

    resp = await _turn(seen)

    assert resp.get("intent") == intent_name
    assert seen["exec_users"][-1] == (intent_name, ALICE)


@pytest.mark.asyncio
async def test_public_read_shortcut_runs_as_claimed_member_for_that_turn(monkeypatch):
    store = _PanelStore()
    seen = _wire(monkeypatch, store, skybridge_handles=False)

    async def consented(_uid, *_model):   # #2024 adds the claim's model id
        return True

    monkeypatch.setattr(voice_tts, "_voice_claim_consented", consented)
    await _turn(seen, voice_user_id=BOB, voice_score=0.95)
    assert seen["exec_users"][-1] == ("list_show", BOB)
    await _turn(seen)
    assert seen["exec_users"][-1] == ("list_show", "guest")


# --------------------------------------------------------------------------- #
# 5. The PIN sign-in (touch) flow is unchanged
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_pin_sign_in_then_voice_is_the_member_within_its_session(monkeypatch):
    """What ``panel_auth.submit_pin`` does on a correct PIN — stamp the binding and write a
    fresh ``ui_panel_sessions`` row — still identifies the member on the following turns
    while that session is fresh. Those turns carry no identification of their own, so they
    do not extend it (section 7); the PIN replay, which does, is what refreshes it."""
    store = _PanelStore()
    seen = _wire(monkeypatch, store)
    voice_tts._VOICE_SESSIONS[PANEL] = {"session_id": "vs-1", "last_at": voice_tts.time.monotonic(),
                                        "bound_user_id": ALICE}
    store.session = {"user_id": ALICE, "last_seen_at": _ts(0)}

    for _ in range(3):
        await _turn(seen)
        assert _last(seen, "skybridge_user") == ALICE
    assert store.heartbeats == []
    # The replayed turn after a correct PIN names its member itself (panel_auth.submit_pin).
    await _turn(seen, **_pin_replay(ALICE))
    assert _last(seen, "skybridge_user") == ALICE
    assert [h[:2] for h in store.heartbeats] == [(PANEL, ALICE)]


# --------------------------------------------------------------------------- #
# 6. ZOE_VOICE_SESSION (#2025): an OPEN voice session is a valid fresh source -
#    and nothing outlives it
# --------------------------------------------------------------------------- #
R34 = "wespeaker_resnet34+i8qdq-u8s8+c1"


def _voice_claim(member, score=0.95):
    return {"voice_user_id": member, "voice_score": score,
            "speaker": {"verified": None, "member": member, "score": score, "model_id": R34}}


def _voice_session_on(monkeypatch):
    import voice_session as vs

    monkeypatch.setenv("ZOE_VOICE_SESSION", "1")
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")
    monkeypatch.setattr(vs, "_SESSIONS", {})

    async def no_names(_m):
        return ()

    async def no_publish(_events):
        return None

    monkeypatch.setattr(vs, "other_member_names", no_names)
    monkeypatch.setattr(vs, "publish", no_publish)
    # The step-up check classifies the turn; an unclassifiable turn is no step-up.
    sys.modules["skybridge_service"].classify_skybridge_intent = lambda *_a, **_k: None
    return vs


@pytest.mark.asyncio
async def test_open_voice_session_is_the_member_and_its_close_is_guest(monkeypatch):
    store = _PanelStore()
    seen = _wire(monkeypatch, store)

    async def consented(_uid, *_model):
        return True

    monkeypatch.setattr(voice_tts, "_voice_claim_consented", consented)
    vs = _voice_session_on(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(vs, "_clock", lambda: clock[0])

    # Alice's verified voice opens the panel's voice session: her turn is hers (scope AND attribution).
    await _turn(seen, **_voice_claim(ALICE))
    assert vs.active_member(PANEL) == ALICE
    assert _last(seen, "skybridge_user") == ALICE
    assert _last(seen, "saved_users") == ALICE
    # The session is the source - never an indefinite panel binding.
    assert voice_tts._VOICE_SESSIONS[PANEL].get("bound_user_id") is None

    # Inside the open session her next verified turn is still hers.
    clock[0] += 30
    await _turn(seen, **_voice_claim(ALICE))
    assert _last(seen, "skybridge_user") == ALICE and vs.active_member(PANEL) == ALICE

    # Another (unverified) voice closes the session: that turn is guest and saves nothing as Alice.
    seen["saved_users"].clear()
    await _turn(seen, speaker={"verified": False, "member": None, "score": None})
    assert vs.active_member(PANEL) is None
    assert _last(seen, "skybridge_user") == "guest"
    assert ALICE not in seen["saved_users"]

    # With the session closed, a stale binding, a stale "last signed in" row and the operator-declared owner
    # are all still NOT sources: a claim-less turn is guest and the stale binding is dropped.
    store.session = {"user_id": ALICE, "last_seen_at": _ts(6 * 3600)}
    store.owner = ALICE
    voice_tts._VOICE_SESSIONS[PANEL]["bound_user_id"] = ALICE
    await _turn(seen)
    assert _last(seen, "skybridge_user") == "guest", "a stale binding applied after the voice session closed"
    assert ALICE not in seen["saved_users"]
    assert voice_tts._VOICE_SESSIONS[PANEL].get("bound_user_id") is None


@pytest.mark.asyncio
async def test_voice_session_ttl_expiry_leaves_the_next_turn_guest(monkeypatch):
    store = _PanelStore()
    seen = _wire(monkeypatch, store)

    async def consented(_uid, *_model):
        return True

    monkeypatch.setattr(voice_tts, "_voice_claim_consented", consented)
    vs = _voice_session_on(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(vs, "_clock", lambda: clock[0])

    await _turn(seen, **_voice_claim(BOB))
    assert vs.active_member(PANEL) == BOB and _last(seen, "skybridge_user") == BOB

    clock[0] += vs.ttl_s() + 1                      # the session's TTL passes with nobody speaking
    seen["saved_users"].clear()
    await _turn(seen)
    assert vs.active_member(PANEL) is None
    assert _last(seen, "skybridge_user") == "guest", "an expired voice session still identified the speaker"
    assert BOB not in seen["saved_users"]


# --------------------------------------------------------------------------- #
# 7. Only a turn that identifies ITSELF as the member extends the member's PIN session
#    (privacy, 2026-10-14). Before, ANY voice turn on the panel refreshed a fresh member
#    session (#1349), so it never lapsed while anybody at all kept talking.
# --------------------------------------------------------------------------- #
async def _consent_all(monkeypatch):
    async def consented(_uid, *_model):
        return True

    monkeypatch.setattr(voice_tts, "_voice_claim_consented", consented)


@pytest.mark.asyncio
async def test_unidentified_voice_does_not_extend_a_member_session(monkeypatch):
    store = _PanelStore()
    seen = _wire(monkeypatch, store)
    signed_in = _ts(IDLE_WINDOW_S - 60)          # Alice's PIN session, one minute from lapsing
    store.session = {"user_id": ALICE, "last_seen_at": signed_in}

    for _ in range(3):                            # somebody keeps talking; no claim, no PIN
        await _turn(seen)

    assert store.heartbeats == [], "an unidentified voice refreshed the member's PIN session"
    assert store.session["last_seen_at"] == signed_in
    # ... so the window still runs out on schedule: once it has, the same voice is guest.
    store.session = {"user_id": ALICE, "last_seen_at": _ts(IDLE_WINDOW_S + 1)}
    await _turn(seen)
    assert _last(seen, "skybridge_user") == "guest"


@pytest.mark.asyncio
async def test_another_members_claim_does_not_extend_or_take_over_the_session(monkeypatch):
    store = _PanelStore()
    seen = _wire(monkeypatch, store)
    await _consent_all(monkeypatch)
    store.session = {"user_id": ALICE, "last_seen_at": _ts(30)}

    await _turn(seen, voice_user_id=BOB, voice_score=0.99)

    assert _last(seen, "skybridge_user") == BOB          # Bob's turn is Bob's ...
    assert store.heartbeats == []                         # ... but it neither refreshes Alice's session
    assert store.session["user_id"] == ALICE              # ... nor turns it into a Bob session


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["accepted_claim", "pin_replay"])
async def test_the_members_own_identification_extends_their_session(monkeypatch, how):
    """NEGATIVE CONTROL: the same rig DOES refresh the session when the turn identifies
    itself as that member - an accepted voice claim, or the replay after a correct PIN."""
    store = _PanelStore()
    seen = _wire(monkeypatch, store)
    await _consent_all(monkeypatch)
    store.session = {"user_id": ALICE, "last_seen_at": _ts(IDLE_WINDOW_S - 60)}

    if how == "accepted_claim":
        await _turn(seen, voice_user_id=ALICE, voice_score=0.99)
    else:
        await _turn(seen, **_pin_replay(ALICE))

    assert [h[:2] for h in store.heartbeats] == [(PANEL, ALICE)]


@pytest.mark.asyncio
async def test_a_below_threshold_claim_does_not_extend_the_session(monkeypatch):
    store = _PanelStore()
    seen = _wire(monkeypatch, store)
    await _consent_all(monkeypatch)
    store.session = {"user_id": ALICE, "last_seen_at": _ts(30)}

    await _turn(seen, voice_user_id=ALICE, voice_score=0.10)

    assert store.heartbeats == []


def test_the_last_signed_in_fallback_is_retired():
    """`_resolve_panel_default_user` (the panel's LAST signed-in member, else its declared
    owner - no freshness check) was the stale-identity source on every lane that used it.
    Retired by removing; every lane resolves a panel member through
    `_resolve_fresh_panel_member` instead."""
    assert not hasattr(voice_tts, "_resolve_panel_default_user")
    assert callable(voice_tts._resolve_fresh_panel_member)

