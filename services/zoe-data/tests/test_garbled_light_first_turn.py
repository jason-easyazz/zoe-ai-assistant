"""A quiet, misheard "turn on / off the light" as the FIRST turn of a sitting (2026-11-06/07, the owner's panel).

Every sitting opened with the same request and every first attempt failed: the words reached Zoe as sound-alikes nothing
claimed, the router guessed lists / memory / music / chat, a domain acknowledgement ("Let me check your lists.", "On it.")
played, and the brain answered "I'm Zoe.", read a feeling into it, or promised "I'll turn the lights on for you." with no tool
call. Pinned here, ``ci_safe`` (pure modules, committed lexicon data, the hub calls faked):

1. ``device_confirm``: a short light-shaped transcript nothing claims gets ONE question; the negative controls never do.
2. ``voice_spacer``: a domain line only for a domain the words name; a router guess gets a neutral line.
3. ``write_claim_guard`` + ``write_receipts``: a device switch claim needs a ``device`` receipt; the Skybridge resolver records
   one only for a switch it really made; a device receipt never backs a write claim.

Every phrasing is invented (public repo): neutral paraphrases of the live class, never the owner's words.

Negative controls (run by hand when written, each went red): with ``lexicons_data/en.json`` "device_confirm" removed the shape
tests fail (no question is offered); with ``SpacerTurn._domain_pool`` returning the domain pool unconditionally the spacer tests
fail; with the "device" verbs removed from ``lexicons_data/write_claims.json`` the claim tests fail (the promise passes); with the
``record_device_receipt`` call removed from ``skybridge_service`` the receipt test fails. The flag-off cases below are the in-suite
controls.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.ci_safe  # pure modules + committed lexicon data; the hub is faked; no DB, no sidecar, no network

import device_confirm as dc
import voice_spacer as vs
import write_claim_guard as g
import write_receipts as wr

UID = "test-member-garbled"
DEVICE_HONEST = "I didn't manage to switch that, so nothing's changed. Want me to try again?"
LISTS_GUESS = {"domain": "lists", "routed": "lists", "score": 0.4, "gated": False, "head_conf": 0.98,
               "two_stage": {"tool": "show_list", "domain": "lists"}}
HOME_SURE = {"domain": "smart_home", "routed": "smart_home", "score": 0.0, "gated": False, "head_conf": 0.95,
             "two_stage": {"tool": "home", "domain": "smart_home"}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("ZOE_DEVICE_CONFIRM_GUARD", "ZOE_SPACER_REQUIRE_CLAIM", "ZOE_WRITE_CLAIM_GUARD", "ZOE_VOICE_FILLER_PHRASES"):
        monkeypatch.delenv(k, raising=False)
    dc.reset()
    vs.reset()
    wr.reset()
    yield
    dc.reset()
    vs.reset()
    wr.reset()


def _unclaimed(_text, _ctx):
    return False


# ── 1. confirm-then-act ─────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, polarity", [
    ("shone the lots", "on"),                 # the verb + particle fused into one word, the noun heard as "lots"
    ("do the one", "on"),                     # verb and noun both misheard, no particle: offer the light ON
    ("Do the one.", "on"),
    ("sound the lamp", "on"),
    ("two of the one", "off"),                # "of" is the off particle
    ("turn on the line", "on"),
    ("I've shone a lot", "on"),
    ("hey zoe, shone the lots", "on"),        # wake / politeness words do not count
])
def test_a_garbled_short_light_command_has_the_shape(text, polarity):
    assert dc.shape(text) == polarity


@pytest.mark.parametrize("text", [
    "I've had a lot on",                      # a light-like word and a particle, but nothing that commands: a feelings turn
    "what's on the TV",                       # a question, and no light-like noun
    "a little bit of the lot",                # no command verb: left to the brain, which now asks what was meant
    "I've shone a lot of patience",           # the noun is not the last word
    "do you like this one",                   # "you" / "this": talk, not a command
    "show me the one",
    "shone the lots?",                        # a question is never offered a command
    "turn the light on and off",              # both particles: no single reading
    "shone",                                  # one word
    "please could you do the one with the cheese and the ham",   # too long
])
def test_the_negative_controls_have_no_shape(text):
    assert dc.shape(text) is None


def test_an_unclaimed_garble_routed_elsewhere_gets_one_short_question():
    ask = dc.decide("shone the lots", router_decision=LISTS_GUESS, panel_key="p1", claimed=_unclaimed)
    assert ask is not None and ask.question == "Light on?" and ask.command == "turn on the light"
    off = dc.decide("two of the one", router_decision=None, panel_key="p2", claimed=_unclaimed)
    assert off is not None and off.question == "Turn the light off?" and off.command == "turn off the light"


def test_the_yes_command_is_one_a_real_matcher_claims():
    """The words a yes runs must be claimed by Skybridge itself (its resolver + receipt), never by the brain."""
    import stt_snap

    for polarity in ("on", "off"):
        ask = dc.decide("shone the lots" if polarity == "on" else "two of the one", panel_key=f"p-{polarity}",
                        claimed=_unclaimed)
        claim = stt_snap.claim_of(ask.command, None)
        assert claim is not None and claim.tag == f"sky:smart_home/turn_{polarity}", claim


def test_a_claimed_turn_is_never_asked_about():
    assert dc.decide("turn on the light", panel_key="p3") is None          # the real matcher claims it: it just runs


def test_a_confident_smart_home_verdict_is_the_home_tools_turn():
    assert dc.decide("do the one", router_decision=HOME_SURE, panel_key="p4", claimed=_unclaimed) is None
    gated = dict(HOME_SURE, gated=True)
    assert dc.decide("do the one", router_decision=gated, panel_key="p5", claimed=_unclaimed) is not None


def test_never_in_the_middle_of_a_conversation_about_something_else():
    t0 = 1000.0
    dc.note_turn("p6", device=False, now=t0)                              # the person was just talking about their day
    assert dc.decide("do the one", panel_key="p6", claimed=_unclaimed, now=t0 + 20) is None
    # a sitting that opens with it (or a pause long enough) is asked
    dc.note_turn("p7", device=False, now=t0)
    assert dc.decide("do the one", panel_key="p7", claimed=_unclaimed, now=t0 + dc.MID_CONVERSATION_S + 1) is not None
    # a retry right after a device turn is not "another conversation"
    dc.note_turn("p8", device=True, now=t0)
    assert dc.decide("do the one", panel_key="p8", claimed=_unclaimed, now=t0 + 5) is not None


def test_only_the_panel_lane_and_the_kill_switch(monkeypatch):
    assert dc.at_the_panel({"source": "panel"}) and dc.at_the_panel(None)
    assert not dc.at_the_panel({"source": "session"})                     # a member's own session (desktop / phone)
    assert dc.enabled()                                                   # a behaviour fix: on by default
    monkeypatch.setenv("ZOE_DEVICE_CONFIRM_GUARD", "0")
    assert not dc.enabled()


def test_the_voice_lane_asks_before_skybridge_and_reuses_the_snap_confirmation():
    """Source pin: the guard sits after the STT snap and before the Skybridge cards, behind the panel + flag checks, its pending
    entry is the STT-snap shape (so a yes rewrites the words into the command and every gate below judges it), and a caller who
    may not drive the house is never asked."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "routers" / "voice_tts.py").read_text(encoding="utf-8")
    i_snap = src.index("voice/command stt snap failed")
    i_guard = src.index("_dc.decide(text, router_decision=_router_decision")
    i_sky = src.index("from skybridge_service import resolve_skybridge_request")
    assert i_snap < i_guard < i_sky
    block = src[i_guard - 400:src.index("voice/command device confirm failed")]
    for needle in ("_dc.at_the_panel(caller)", "_dc.enabled()", '"stt_snap_text": _dc_ask.command', '_dc_refused("device_confirm")',
                   "_PENDING_CONFIRMATIONS.pop(panel_id, None)", '"speaker": _confirm_speaker'):
        assert needle in block, needle


# ── 2. the acknowledgement waits for a claim ──────────────────────────────────────────────────────────────────────────────

def _ack(text, domain, panel="pa"):
    turn = vs.SpacerTurn(panel_key=panel, text=text)
    turn.note_router({"routed": domain, "domain": domain})
    return turn.tool_ack_line()


def test_a_guessed_domain_gets_a_neutral_line_not_its_own(monkeypatch):
    monkeypatch.setattr(vs, "is_disclosure", lambda *_a, **_k: False)
    neutral = set(vs.generic_pool())
    for domain in ("lists", "music", "smart_home", "calendar"):
        for _ in range(4):
            line = _ack("shone the lots", domain, panel=f"pa-{domain}")
            assert line in neutral, (domain, line)


def test_words_that_name_the_domain_keep_its_line(monkeypatch):
    monkeypatch.setattr(vs, "is_disclosure", lambda *_a, **_k: False)
    lists = set(vs.tool_ack_pool("lists"))
    assert _ack("what's on my shopping list", "lists", panel="pb") in lists
    action = set(vs.tool_ack_pool("smart_home"))
    assert _ack("dim the lamp in the hall", "smart_home", panel="pc") in action


def test_a_neutral_family_needs_no_claim_and_the_kill_switch_restores_the_old_line(monkeypatch):
    monkeypatch.setattr(vs, "is_disclosure", lambda *_a, **_k: False)
    turn = vs.SpacerTurn(panel_key="pd", text="I've shone a lot")
    turn.note_router({"routed": "memory", "domain": "memory"})
    assert turn.corroborated                                              # "Let me think." promises nothing
    monkeypatch.setenv("ZOE_SPACER_REQUIRE_CLAIM", "0")
    assert _ack("shone the lots", "lists", panel="pe") in set(vs.tool_ack_pool("lists"))


def test_the_timer_filler_follows_the_same_rule(monkeypatch):
    monkeypatch.setattr(vs, "is_disclosure", lambda *_a, **_k: False)
    turn = vs.SpacerTurn(panel_key="pf", text="do the one")
    turn.note_router({"routed": "music", "domain": "music"})
    assert turn.timer_line() in set(vs.generic_pool())


# ── 3. a device switch claim needs a device receipt ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sentence", [
    "I'll turn the lights on for you.",
    "Turning it off now.",
    "I've switched the lamp off.",
    "Done, the light's on.",
    "Sure, I'll switch the fan on.",
])
def test_a_switch_claim_is_a_device_claim(sentence):
    claim = g.find_claim(sentence)
    assert claim is not None and claim.kinds == ("device",), (sentence, claim)


@pytest.mark.parametrize("sentence", [
    "The light's on.",                         # a state read-back, not a claim
    "I turned it over in my mind.",            # no particle, no device object
    "It turned out fine.",
    "Want me to turn the light on?",           # an offer
    "I can't turn the light off from here.",   # a refusal
    "If you like, I'll turn the light on.",    # conditional
])
def test_descriptions_offers_and_refusals_are_not_device_claims(sentence):
    claim = g.find_claim(sentence)
    assert claim is None or "device" not in claim.kinds, (sentence, claim)


def test_a_false_switch_promise_is_replaced_with_the_honest_line():
    out = g.guard_text("I'll turn the lights on for you.", UID, wr.begin(), user_text="do the one")
    assert out == DEVICE_HONEST


def test_a_real_switch_lets_the_claim_stand():
    started = wr.begin()
    wr.record(UID, "device", "light.demo_lamp", "test")
    reply = "I've switched the lamp off."
    assert g.guard_text(reply, UID, started, user_text="turn off the lamp") == reply


def test_a_device_receipt_never_backs_a_write_claim():
    started = wr.begin()
    wr.record(UID, "device", "light.demo_lamp", "test")
    assert g.guard_text("I've added it.", UID, started, user_text="add milk") != "I've added it."
    assert g.guard_text("I've added milk to your shopping list.", UID, started, user_text="add milk") \
        != "I've added milk to your shopping list."


def test_the_switch_promise_streams_like_the_one_shot_guard_at_every_cut():
    reply = "Sure. I'll turn the lights on for you. Anything else?"
    want = g.guard_text(reply, UID, wr.begin(), user_text="do the one")
    assert want != reply and DEVICE_HONEST in want

    async def run(parts):
        async def gen():
            for p in parts:
                yield p

        return "".join([d async for d in g.guard_stream(gen(), UID, wr.begin(), user_text="do the one")])

    for cut in range(1, len(reply)):
        assert asyncio.run(run([reply[:cut], reply[cut:]])) == want, cut


def _wire_one_lamp(monkeypatch, *, ok=True):
    import skybridge_service as sky
    import smart_home_service as shs

    devices = [shs._device_from_light({"entity_id": "light.demo_lamp", "state": "off",
                                       "attributes": {"friendly_name": "Demo Lamp"}})]

    async def list_devices():
        return [dict(d) for d in devices]

    async def list_scenes():
        return []

    async def set_device(entity_id, on, *, brightness_pct=None):
        return ok

    async def room_eids(_ctx, _db):
        return set()

    monkeypatch.setattr(shs, "list_devices", list_devices)
    monkeypatch.setattr(shs, "list_scenes", list_scenes)
    monkeypatch.setattr(shs, "set_device", set_device)
    monkeypatch.setattr(sky, "_room_eids_for_context", room_eids)
    return sky


def test_skybridge_records_a_device_receipt_for_a_switch_it_really_made(monkeypatch):
    sky = _wire_one_lamp(monkeypatch)
    started = wr.begin()
    out = asyncio.run(sky.resolve_skybridge_request("turn on the light", UID))
    assert out["handled"] and out["controlled_entities"] == ["light.demo_lamp"]
    assert wr.has(UID, started, "device")
    assert not wr.has(UID, started)                                       # ...and it is not a WRITE receipt


def test_a_switch_the_hub_refused_leaves_no_receipt(monkeypatch):
    sky = _wire_one_lamp(monkeypatch, ok=False)
    started = wr.begin()
    out = asyncio.run(sky.resolve_skybridge_request("turn on the light", UID))
    assert "couldn't reach" in out["spoken_summary"].lower()
    assert not wr.has(UID, started, "device")


def test_the_household_authority_table_proves_a_light_with_the_device_receipt():
    import household_authority as ha

    assert ha.receipt_kind("smart_home") == "device"
    assert "device" in wr.KINDS
