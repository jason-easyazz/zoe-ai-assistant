"""Panel voice session - "an identified voice is a password" (ZOE_VOICE_SESSION, flag-dark).

Pinned, each with a negative control (the opposite input, or the fix taken away, turns the assertion over):

* the state machine: a verified claim opens and renews; an unverified voice, a device turn with no verdict, a different
  member, the TTL, a lock phrase and a revoked consent close it; a fresh PIN sign-in of another member is never
  overridden; nothing is open with the flag off;
* the model gate: ResNet34 (claim AND server) opens, Resemblyzer only with the owner's explicit override;
* the step-up tier: payments / security / biometrics / forgetting a person / another member / the policy intents still
  challenge INSIDE a session; ordinary reads and writes do not;
* voice_command end to end: inside a session the Skybridge read is resolved for the member (no PIN challenge) and the
  panel gets the REAL rows on its own channel; a guest gets only the privacy-safe card; a step-up phrase challenges;
* flag off: voice_command never consults the session machinery (it is made to explode); an authorised read still shows
  its rows (the card is not flag-gated).

Synthetic names only; no DB, no network, no models (ci_safe).
"""
from __future__ import annotations

import asyncio
import logging
import sys
import types

import pytest

import routers.voice_tts as v
import voice_cards
import voice_session as vs
from test_voice_identity_persistence import PANEL_ID, _wire_voice_command_fakes

pytestmark = pytest.mark.ci_safe

R34 = vs.RESNET34_MODEL_ID
DEVICE = {"source": "device", "panel_id": PANEL_ID, "user_id": "voice-daemon"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    vs.reset()
    for key in ("ZOE_VOICE_SESSION", "ZOE_VOICE_SESSION_TTL_S", "ZOE_SPEAKER_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.82")
    yield
    vs.reset()


def _on(monkeypatch, *, model="resnet34", ttl=None):
    monkeypatch.setenv("ZOE_VOICE_SESSION", "1")
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", model)
    if ttl is not None:
        monkeypatch.setenv("ZOE_VOICE_SESSION_TTL_S", str(ttl))


def _turn(*, verdict, claimed=None, pin_user=None, model_ok=True, text="what's on my calendar", source="device"):
    return vs.observe_turn(panel_id=PANEL_ID, caller_source=source, verdict=verdict, claimed=claimed,
                           pin_user=pin_user, model_ok=model_ok, model_reason="resnet34", text=text)


def _states(decision):
    return [(e["state"], e.get("reason")) for e in decision.events]


# -- 1. the state machine -------------------------------------------------------------------------------------------

def test_flag_off_nothing_opens(monkeypatch):
    assert _turn(verdict=True, claimed="casey") is None
    assert vs.active_member(PANEL_ID) is None
    _on(monkeypatch)                                           # control: the same turn with the flag on opens
    assert _turn(verdict=True, claimed="casey").scope_user == "casey"
    assert vs.active_member(PANEL_ID) == "casey"


def test_verified_claim_opens_then_renews(monkeypatch):
    _on(monkeypatch)
    d1 = _turn(verdict=True, claimed="casey")
    assert (d1.scope_user, d1.by_session, _states(d1)) == ("casey", True, [("open", None)])
    d2 = _turn(verdict=True, claimed="casey")
    assert _states(d2) == [("renewed", None)]
    assert vs.active_member(PANEL_ID) == "casey"


def test_only_a_panel_device_drives_the_session(monkeypatch):
    _on(monkeypatch)
    assert _turn(verdict=True, claimed="casey", source="session") is None     # a browser keeps the legacy chain
    assert vs.active_member(PANEL_ID) is None


@pytest.mark.parametrize("verdict, reason", [(False, "unverified_voice"), (None, "no_verdict")])
def test_an_unverified_or_unknown_voice_closes(monkeypatch, verdict, reason):
    _on(monkeypatch)
    _turn(verdict=True, claimed="casey")
    d = _turn(verdict=verdict)
    assert _states(d) == [("closed", reason)]
    assert d.scope_user is None and not d.by_session
    assert vs.active_member(PANEL_ID) is None


def test_an_unverified_voice_keeps_only_a_fresh_pin_sign_in(monkeypatch):
    _on(monkeypatch)
    assert _turn(verdict=False, pin_user="dana").scope_user == "dana"   # the panel's own PIN sign-in, unchanged
    assert _turn(verdict=False).scope_user is None                      # control: no sign-in -> guest


def test_a_different_member_closes_and_gets_their_own(monkeypatch):
    _on(monkeypatch)
    _turn(verdict=True, claimed="casey")
    d = _turn(verdict=True, claimed="dana")
    assert _states(d) == [("closed", "different_voice"), ("open", None)]
    assert d.scope_user == "dana" and vs.active_member(PANEL_ID) == "dana"


def test_ttl_expiry(monkeypatch):
    _on(monkeypatch, ttl=120)
    now = [1000.0]
    monkeypatch.setattr(vs, "_clock", lambda: now[0])
    _turn(verdict=True, claimed="casey")
    now[0] += 119.0
    assert vs.active_member(PANEL_ID) == "casey"        # control: inside the TTL it is still open
    _turn(verdict=True, claimed="casey")                # ...and a verified turn renews it from NOW
    now[0] += 119.0
    assert vs.active_member(PANEL_ID) == "casey"
    now[0] += 1.0
    assert vs.active_member(PANEL_ID) is None


def test_ttl_expiry_is_reported_on_the_next_turn(monkeypatch):
    _on(monkeypatch, ttl=60)
    now = [0.0]
    monkeypatch.setattr(vs, "_clock", lambda: now[0])
    _turn(verdict=True, claimed="casey")
    now[0] += 61.0
    assert ("closed", "timeout") in _states(_turn(verdict=False))


def test_a_fresh_pin_sign_in_of_another_member_is_never_overridden(monkeypatch, caplog):
    _on(monkeypatch)
    caplog.set_level(logging.INFO, logger="zoe.voice_session")
    d = _turn(verdict=True, claimed="casey", pin_user="dana")
    assert d.scope_user is None and not d.by_session      # neither the voice's session nor the signed-in member's data
    assert vs.active_member(PANEL_ID) is None
    assert "reason=pin_session_other_member" in caplog.text
    d2 = _turn(verdict=True, claimed="casey", pin_user="casey")   # control: the SAME member's sign-in is fine
    assert d2.scope_user == "casey" and not d2.by_session          # (a PIN already covers the step-up tier)


def test_lock_and_close_phrases(monkeypatch):
    _on(monkeypatch)
    d = _turn(verdict=False, text="lock")      # no session open: never claim a lock that did not happen
    assert not d.lock and not d.events
    _turn(verdict=True, claimed="casey")
    d = _turn(verdict=True, claimed="casey", text="Zoe, lock the screen")
    assert d.lock and _states(d) == [("closed", "lock")] and d.scope_user is None
    _turn(verdict=True, claimed="casey")
    d = _turn(verdict=True, claimed="casey", text="thanks, that's all for now")
    assert not d.lock and _states(d) == [("closed", "close")] and vs.active_member(PANEL_ID) is None
    _turn(verdict=True, claimed="casey")
    d = _turn(verdict=True, claimed="casey", text="lock the front door")         # control: a request, not a lock
    assert not d.lock and vs.active_member(PANEL_ID) == "casey"


def test_lexicon_examples_for_lock_and_close():
    ex = vs.text_for("examples", default={})
    for s in ex["lock"]:
        assert vs.phrase_kind(s) == "lock", s
    for s in ex["close"]:
        assert vs.phrase_kind(s) == "close", s
    for s in ex["not_lock"] + ex["not_close"]:
        assert vs.phrase_kind(s) is None, s


def test_consent_revoked_closes_and_tells_the_panel(monkeypatch):
    _on(monkeypatch)
    _turn(verdict=True, claimed="casey")
    sent = []

    async def fake_send(panel, etype, data):
        sent.append((panel, etype, data))
        return 1

    monkeypatch.setattr(vs, "send_to_panel", fake_send)
    asyncio.run(vs.revoke_member("dana"))                  # control: another member's revocation closes nothing
    assert vs.active_member(PANEL_ID) == "casey" and not sent
    asyncio.run(vs.revoke_member("casey", "consent_revoked"))
    assert vs.active_member(PANEL_ID) is None
    assert sent == [(PANEL_ID, "voice_session",
                     {"panel_id": PANEL_ID, "state": "closed", "member_id": "casey", "reason": "consent_revoked"})]


def test_deleting_a_voice_profile_closes_the_session(monkeypatch):
    """Both delete paths (settings route, "forget my voice") reach voice_enrollment.push_forget_to_panels."""
    import voice_enrollment

    _on(monkeypatch)
    _turn(verdict=True, claimed="casey")

    async def no_send(*_a):
        return 0

    monkeypatch.setattr(vs, "send_to_panel", no_send)
    def no_db():
        raise RuntimeError("no db")

    monkeypatch.setitem(sys.modules, "db_compat", types.SimpleNamespace(get_compat_db=no_db))
    asyncio.run(voice_enrollment.push_forget_to_panels("casey"))
    assert vs.active_member(PANEL_ID) is None


def test_the_audit_line_never_carries_a_score(monkeypatch, caplog):
    _on(monkeypatch)
    caplog.set_level(logging.INFO, logger="zoe.voice_session")
    _turn(verdict=True, claimed="casey")
    _turn(verdict=False)
    lines = [r.getMessage() for r in caplog.records if r.name == "zoe.voice_session"]
    assert any("event=open member=casey panel=%s" % PANEL_ID in ln for ln in lines)
    assert any("event=close member=casey" in ln and "reason=unverified_voice" in ln for ln in lines)
    assert not any("score" in ln for ln in lines)


# -- 2. the model gate ----------------------------------------------------------------------------------------------

def test_model_gate(monkeypatch):
    r34_claim = {"speaker": {"model_id": R34, "member": "casey", "score": 0.9}}
    legacy_claim = {"voice_user_id": "casey", "voice_score": 0.9}
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")
    assert vs.model_gate(r34_claim) == (True, "resnet34")
    assert vs.model_gate(legacy_claim) == (False, "claim_not_acting_model")     # a Resemblyzer score means nothing here
    monkeypatch.setenv("ZOE_VOICE_SESSION_ALLOW_RESEMBLYZER", "1")              # the retired override flag does nothing any more
    assert vs.model_gate(legacy_claim) == (False, "claim_not_acting_model")
    explicit = {"speaker": {"model_id": "resemblyzer", "member": "casey", "score": 0.99}}
    assert vs.model_gate(explicit) == (False, "claim_not_acting_model")


def test_model_gate_unmet_attributes_but_does_not_unlock(monkeypatch):
    _on(monkeypatch)
    d = vs.observe_turn(panel_id=PANEL_ID, caller_source="device", verdict=True, claimed="casey", pin_user=None,
                        model_ok=False, model_reason="model_below_minimum", text="what's on my calendar")
    assert d.scope_user is None and vs.active_member(PANEL_ID) is None


# -- 3. the step-up tier --------------------------------------------------------------------------------------------

def test_lexicon_step_up_examples():
    ex = vs.text_for("examples", default={})
    for s in ex["step_up"]:
        assert vs.step_up_reason(s), s
    for s in ex["not_step_up"]:
        assert vs.step_up_reason(s) is None, s


def test_step_up_tiers_from_the_policy_data():
    assert vs.step_up_reason("anything", intent_name="transaction_create") == "intent:transaction_create"
    assert vs.step_up_reason("anything", intent_name="calendar_show") is None
    assert vs.step_up_reason("use the warm voice", skybridge=("voice", "set")) == "skybridge:voice:set"
    assert vs.step_up_reason("show the calendar", skybridge=("calendar", "show")) is None
    names = ("robin",)
    assert vs.step_up_reason("what's on robin's calendar", other_member_names=names) == "other_member"
    assert vs.step_up_reason("add eggs to the list for robin", other_member_names=names) == "other_member"
    assert vs.step_up_reason("what's on my calendar", other_member_names=names) is None


def test_private_read_kinds():
    assert vs.private_read_kind(intent_name="reminder_list") == "reminders"
    assert vs.private_read_kind(skybridge=("calendar", "show")) == "calendar"
    assert vs.private_read_kind(skybridge=("lists", "add_item")) is None       # a write is never a read card
    assert vs.private_read_kind(intent_name="weather") is None


# -- 4. cards -------------------------------------------------------------------------------------------------------

CAL_RESULT = {
    "handled": True,
    "spoken_summary": "You have 2 events today: Dentist at 9 AM and Dinner at 6:30 PM.",   # the resolver names them now
    "intent": {"domain": "calendar", "action": "show", "range": "today"},
    "cards": [{"card_type": "generic", "content": {
        "source": "calendar_show", "qualifier": "today", "start_date": "2026-10-13", "end_date": "2026-10-13",
        "events": [{"title": "Dentist", "start_time": "09:00"}, {"title": "Dinner", "start_time": "18:30"}]}}],
}


def test_card_from_a_skybridge_read_carries_the_real_rows():
    card = voice_cards.from_skybridge(PANEL_ID, "casey", CAL_RESULT)
    assert card["private"] is True and card["member_id"] == "casey" and card["kind"] == "calendar"
    assert [i["text"] for i in card["items"]] == ["Dentist", "Dinner"]
    assert [i["meta"] for i in card["items"]] == ["9 AM", "6:30 PM"]
    assert voice_cards.spoken_calendar(CAL_RESULT) == "You have 2 events today: Dentist at 9 AM and Dinner at 6:30 PM."


def test_the_locked_card_has_no_member_and_no_rows(monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_MODEL", "resnet34")
    card = voice_cards.locked(PANEL_ID)
    assert card["private"] is False and card["items"] == [] and "member_id" not in card
    assert "Say it again" in card["body"]
    assert "Say it again" not in voice_cards.locked(PANEL_ID, step_up=True)["body"]      # control: a step-up card names the PIN instead


def test_cards_go_to_the_panel_channel_only(monkeypatch):
    sent = []

    class B:
        async def broadcast(self, channel, etype, data, user_id=None):
            sent.append(channel)
            return 1

        async def broadcast_to_panel(self, *_a):          # the 'all'-fallback helper must NOT be used
            raise AssertionError("broadcast_to_panel falls back to 'all'")

    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=B()))
    assert asyncio.run(voice_cards.push(voice_cards.from_skybridge(PANEL_ID, "casey", CAL_RESULT))) == 1
    assert sent == [f"panel_{PANEL_ID}"]


# -- 5. voice_command end to end ------------------------------------------------------------------------------------

def _wire(monkeypatch, *, panel_user=None, consented=True):
    chat_calls, spawned = _wire_voice_command_fakes(monkeypatch, panel_user=panel_user)
    rec = {"resolver_users": [], "frames": [], "legacy_ui": 0, "challenges": []}

    async def resolve(text, user_id, *, context=None, db=None, **_kw):
        rec["resolver_users"].append(user_id)
        if user_id in {"guest", "voice-guest", ""}:
            return {"handled": True, "auth_required": True, "intent": {"domain": "calendar", "action": "show"},
                    "spoken_summary": "Please authenticate on the touch panel to continue.", "cards": []}
        return dict(CAL_RESULT)

    monkeypatch.setitem(sys.modules, "skybridge_service", types.SimpleNamespace(
        resolve_skybridge_request=resolve, classify_skybridge_intent=lambda *_a, **_k: None))

    class B:
        async def broadcast(self, channel, etype, data, user_id=None):
            if channel.startswith("panel_"):
                rec["frames"].append((channel, etype, dict(data)))
                return 1
            return 0

    monkeypatch.setitem(sys.modules, "push", types.SimpleNamespace(broadcaster=B()))

    async def legacy_ui(*_a, **_k):
        rec["legacy_ui"] += 1

    async def consent(_uid, *_model):      # PR #2024 adds the claim's model id as a second argument
        return consented

    async def challenge(**kw):
        rec["challenges"].append(kw)
        if vs.enabled():   # the real helper's first act with the flag on: the privacy-safe card
            await voice_cards.push(voice_cards.locked(kw["panel_id"], step_up=kw.get("step_up", False)))
        return {"ok": True, "panel_id": kw["panel_id"], "reply": "pin", "status": "awaiting_pin"}

    async def no_names(_m):
        return ()

    monkeypatch.setattr(v, "_broadcast_skybridge_ui", legacy_ui)
    monkeypatch.setattr(v, "_voice_claim_consented", consent)
    monkeypatch.setattr(v, "_request_voice_identity_challenge", challenge)
    monkeypatch.setattr(vs, "other_member_names", no_names)

    async def run(payload, text="what's on my calendar"):
        # No explicit session_id: the panel's own voice session row is minted, so the legacy binding is observable.
        body = {"text": text, "panel_id": PANEL_ID, **payload}
        resp = await v.voice_command(body, caller=DEVICE, stream=False, db=object())
        if spawned:
            await asyncio.gather(*spawned)
            spawned.clear()
        return resp

    return run, rec


def _claim(model=R34, member="casey", score=0.93):
    return {"voice_user_id": member, "voice_score": score,
            "speaker": {"verified": None, "member": member, "score": score, "model_id": model}}


def _private_cards(rec):
    return [d for (_c, t, d) in rec["frames"] if t == "voice_card" and d.get("private")]


@pytest.mark.asyncio
async def test_inside_a_session_the_read_is_the_members_and_shows_its_rows(monkeypatch):
    _on(monkeypatch)
    run, rec = _wire(monkeypatch)
    resp = await run(_claim())
    assert rec["resolver_users"] == ["casey"] and not rec["challenges"]          # no PIN
    assert resp["reply"] == "You have 2 events today: Dentist at 9 AM and Dinner at 6:30 PM."
    cards = _private_cards(rec)
    assert len(cards) == 1 and [i["text"] for i in cards[0]["items"]] == ["Dentist", "Dinner"]
    assert cards[0]["member_id"] == "casey"
    assert ("voice_session" in {t for (_c, t, _d) in rec["frames"]})
    assert rec["legacy_ui"] == 0                       # delivered -> no page-reloading navigate over the card
    assert vs.active_member(PANEL_ID) == "casey"
    assert "bound_user_id" not in (v._VOICE_SESSIONS.get(PANEL_ID) or {})   # no indefinite voice binding


@pytest.mark.asyncio
async def test_the_skybridge_challenge_is_skipped_only_inside_a_session(monkeypatch):
    """Negative control of the test above: a claim in the retired Resemblyzer space (gate unmet) is challenged."""
    _on(monkeypatch)
    run, rec = _wire(monkeypatch)
    resp = await run(_claim(model="resemblyzer"))
    assert rec["resolver_users"] == ["guest"] and len(rec["challenges"]) == 1
    assert resp["status"] == "awaiting_pin"
    assert not _private_cards(rec) and vs.active_member(PANEL_ID) is None


@pytest.mark.asyncio
async def test_a_guest_never_sees_a_private_card(monkeypatch):
    _on(monkeypatch)
    run, rec = _wire(monkeypatch)
    await run(_claim())                                       # the member opens a session...
    rec["frames"].clear()
    rec["resolver_users"].clear()
    resp = await run({"speaker": {"verified": False, "member": None, "score": None}})   # ...then a guest speaks
    assert rec["resolver_users"] == ["guest"] and resp["status"] == "awaiting_pin"
    assert not _private_cards(rec)
    locked = [d for (_c, t, d) in rec["frames"] if t == "voice_card"]
    assert locked and all(d["private"] is False and d["items"] == [] and "member_id" not in d for d in locked)
    closed = [d for (_c, t, d) in rec["frames"] if t == "voice_session" and d["state"] == "closed"]
    assert closed and closed[0]["reason"] == "unverified_voice"


@pytest.mark.asyncio
async def test_a_step_up_action_still_challenges_inside_a_session(monkeypatch):
    _on(monkeypatch)
    run, rec = _wire(monkeypatch)
    await run(_claim())
    assert vs.active_member(PANEL_ID) == "casey"
    resp = await run(_claim(), text="unlock the front door")
    assert resp["status"] == "awaiting_pin" and rec["challenges"][-1]["step_up"] is True
    resp = await run(_claim(), text="what's on my calendar")      # control: an ordinary read inside the session
    assert resp.get("status") != "awaiting_pin" and len(rec["challenges"]) == 1


@pytest.mark.asyncio
async def test_a_lock_phrase_answers_and_closes(monkeypatch):
    _on(monkeypatch)
    run, rec = _wire(monkeypatch)
    await run(_claim())
    resp = await run(_claim(), text="lock the screen")
    assert resp["intent"] == "voice_session_lock" and resp["reply"].startswith("Locked")
    assert vs.active_member(PANEL_ID) is None


@pytest.mark.asyncio
async def test_flag_off_is_todays_path_and_never_touches_the_session(monkeypatch):
    run, rec = _wire(monkeypatch)

    def boom(*_a, **_k):
        raise AssertionError("voice_session consulted with the flag off")

    for name in ("observe_turn", "model_gate", "step_up_reason", "publish"):
        monkeypatch.setattr(vs, name, boom)
    resp = await run(_claim())
    # An accepted claim unlocks its own turn; since #2026 (voice identity freshness) a claim never writes the
    # indefinite panel binding, flag on or off. The session machinery is never consulted (no open / close / step-up), but an
    # AUTHORISED read still shows its rows - "I want to see the results as well as hear them" is not a flag.
    assert rec["resolver_users"] == ["casey"] and not rec["challenges"]
    assert resp["reply"].startswith("You have 2 events today: Dentist") and resp["intent"] == "skybridge:calendar"
    assert not [d for (_c, t, d) in rec["frames"] if t == "voice_session"]       # no session frame at all
    cards = _private_cards(rec)
    assert len(cards) == 1 and cards[0]["member_id"] == "casey" and rec["legacy_ui"] == 0
    assert "bound_user_id" not in (v._VOICE_SESSIONS.get(PANEL_ID) or {})


@pytest.mark.asyncio
async def test_a_fresh_pin_sign_in_hears_and_sees_the_read_with_no_flag(monkeypatch):
    """The live shape (2026-10-14): a member signed in by PIN, ZOE_VOICE_SESSION unset. The read shows its rows on the
    panel's own channel (the card is not flag-gated); nobody signed in gets the challenge and no rows."""
    run, rec = _wire(monkeypatch, panel_user="casey")
    resp = await run({})
    assert rec["resolver_users"] == ["casey"] and not rec["challenges"]
    assert resp["reply"].startswith("You have 2 events today: Dentist")
    cards = _private_cards(rec)
    assert len(cards) == 1 and cards[0]["member_id"] == "casey" and rec["legacy_ui"] == 0
    assert not [d for (_c, t, d) in rec["frames"] if t == "voice_session"]


@pytest.mark.asyncio
async def test_no_sign_in_and_no_flag_is_the_challenge_and_no_private_card(monkeypatch):
    """Negative control of the test above: nobody signed in."""
    run, rec = _wire(monkeypatch, panel_user=None)
    resp = await run({})
    assert rec["resolver_users"] == ["guest"] and len(rec["challenges"]) == 1 and resp["status"] == "awaiting_pin"
    assert not _private_cards(rec)
