"""A failed write must not poison the session, and a false "done" must not slip past the write-claim guard.

Hands-on QA round 2 (2026-11-05, typed chat, a synthetic demo user; every sentence below is invented):

1. SESSION POISONING. A timer request got a reply that claimed the timer with no tool call; the guard replaced it for the user
   ("I didn't manage to add that; want me to try again?"). Every later timer / alarm / reminder request in that session then came
   back with no tool call, "yes, try again" included - a fresh session worked first time. The Flue sidecar keeps its OWN session
   history of what the model generated, so it still held the model's claim (a write "done" with no tool call), which the 4B model
   copied on every later write. ``claim_correction`` (``ZOE_WRITE_CLAIM_CORRECTION``) tells the next brain turn the truth, and
   ``history_safe`` keeps the guard's replacement line out of any history a brain is shown.
2. GUARD GAPS. "I'll remind you to … at …", "I'll add X to your … list" (no "now"), "I've taken that … off your calendar", "got that
   noted", "I've removed the note about you …", "I'll remember …" all passed with no write. They are now claims (lexicon DATA), each
   checked against its own receipt kind (a removal against a removal, a memory against a memory, a forget against a forget).

Negative controls (run by hand when this was written, each went red): with ``lexicons_data/write_claims.json`` and
``write_claim_guard.py`` restored from origin/main the ``*gap*`` / ``*removal*`` / ``*memory*`` / ``*standing*`` tests fail (the
sentences pass unchanged); with the ``_claim_correction_line`` call removed from ``zoe_flue_client`` the outbound and multi-turn
tests fail. The flag-off cases below are the in-suite controls: the same rig with ``ZOE_WRITE_CLAIM_CORRECTION`` unset reproduces
the poisoned session.
"""
from __future__ import annotations

import asyncio
import json
from collections import OrderedDict

import pytest

pytestmark = pytest.mark.ci_safe  # no DB, no sidecar, no network: the transport and the row fetch are stubbed

import claim_correction as cc
import flue_fastpath_context as ffc
import write_claim_guard as g
import write_receipts as wr
import zoe_flue_client as zfc

HONEST = "I didn't manage to add that; want me to try again?"
UID = "test-member-poison"
SID = "test-session-poison"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ZOE_WRITE_CLAIM_GUARD", raising=False)
    monkeypatch.delenv(cc.ENV_FLAG, raising=False)
    wr.reset()
    cc.reset()
    monkeypatch.setattr(ffc, "_BRAIN_SEEN", OrderedDict())
    monkeypatch.setattr(ffc, "_PENDING", OrderedDict())

    async def no_rows(_sid):
        return []

    monkeypatch.setattr(ffc, "_fetch_rows", no_rows)
    yield
    wr.reset()
    cc.reset()


def _on(monkeypatch):
    monkeypatch.setenv(cc.ENV_FLAG, "1")


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── 2. the guard's gaps ────────────────────────────────────────────────────────────────────────────────────────────────

GAP_CLAIMS = [
    ("I'll remind you to call the dentist at half past two tomorrow.", ("reminder",)),
    ("I'll add rice, beans and spices to your to-do list.", ("list",)),
    ("Okay, I've taken that dinner event off your calendar.", ("calendar_removed",)),
    ("I've removed the note about you being a teacher.", ("forget",)),
    ("You've got a work call at three, got that noted.", ()),
    ("Got it, I'll remember that.", ("memory",)),
    ("I'll forget that.", ("forget",)),
    ("Consider it forgotten.", ("forget",)),
    ("I've forgotten that.", ("forget",)),
    ("I've deleted that memory.", ("forget",)),
    ("I've removed milk from your shopping list.", ("list_removed",)),
    ("I've cancelled your morning alarm.", ("timer_removed",)),
    ("I'll take the lunch off your calendar.", ("calendar_removed",)),
    ("Let me set that reminder for you right now.", ("reminder",)),
    # Codex #2312: a promise verb that names its own kind keeps it over the nouns of its CONTENT
    ("I'll remind you to add milk to your shopping list at 3pm.", ("reminder",)),
    ("I'll remember your appointment is on Friday.", ("memory",)),
    ("I've forgotten that memory.", ("forget",)),
    ("I've added milk to your list, got that noted.", ("list",)),         # a generic phrase never replaces the clause's specific claim
]

NOT_CLAIMS = [
    "Consider it done - the lights are off.",              # a smart-home action: no tracked write receipt exists for it
    "Got that done, the music is playing.",
    "I can add it to your list if you like.",
    "Want me to remind you at three?",
    "Shall I take that off your calendar?",
    "Would you like me to remember that?",
    "I'll add it to your list later.",
    "I'll add that to your list once you confirm.",
    "I'll add it as soon as you say.",
    "After I've added the milk, I'll set a reminder.",
    "I've taken note of that.",
    "Sorry, I've forgotten your sister's name.",
    "I remember you mentioned that.",
    "I'll let you know when it's done.",
    "I can't remove that from your calendar.",
    "I couldn't forget that.",
    "I won't forget.",
    "You removed it from your list yesterday.",
    "I've added Alex to your contacts.",
]


@pytest.mark.parametrize("sentence,kinds", GAP_CLAIMS)
def test_gap_sentences_are_claims_of_their_own_kind(sentence, kinds):
    claim = g.find_claim(sentence)
    assert claim is not None, sentence
    assert claim.kinds == kinds, (sentence, claim.kinds)


@pytest.mark.parametrize("sentence", NOT_CLAIMS)
def test_offers_plans_and_admissions_stay_non_claims(sentence):
    assert g.find_claim(sentence) is None, sentence


def test_gap_reply_with_no_write_is_replaced():
    out = g.guard_text("I'll remind you to call the dentist at half past two tomorrow.", UID, wr.begin(),
                       user_text="remind me to call the dentist at half past two tomorrow")
    assert out == HONEST


def test_gap_reply_with_its_write_stands():
    """Control: the same promise passes once the reminder really landed in the turn."""
    started = wr.begin()
    wr.record(UID, "reminder", "r1", "test")
    reply = "I'll remind you to call the dentist at half past two tomorrow."
    assert g.guard_text(reply, UID, started, user_text="remind me to call the dentist") == reply


def test_removal_needs_a_removal_receipt_not_a_create():
    reply = "Okay, I've taken that dinner event off your calendar."
    started = wr.begin()
    wr.record(UID, "calendar", "e1", "test")               # a CREATE of the same target vouches for nothing
    out = g.guard_text(reply, UID, started)
    assert out != reply and "remove" in out
    wr.record(UID, "calendar_removed", "e1", "test")
    assert g.guard_text(reply, UID, started) == reply


def test_list_removal_needs_a_list_removal_receipt():
    reply = "I've removed milk from your shopping list."
    started = wr.begin()
    wr.record(UID, "list", "i1", "test")
    assert g.guard_text(reply, UID, started) != reply
    wr.record(UID, "list_removed", "i1", "test")
    assert g.guard_text(reply, UID, started) == reply


def test_memory_claim_needs_a_memory_receipt():
    reply = "Got it, I'll remember that."
    started = wr.begin()
    wr.record(UID, "list", "i1", "test")
    out = g.guard_text(reply, UID, started)
    assert out == "I didn't manage to save that; want me to try again?"
    wr.record(UID, "memory", "m1", "test")
    assert g.guard_text(reply, UID, started) == reply
    # a note and a memory vouch for each other ("I've made a note of that" after a memory write)
    assert g.guard_text("I've made a note of that.", UID, started) == "I've made a note of that."


def test_forget_claim_needs_a_forget_receipt_and_says_so_honestly():
    reply = "I've removed the note about you being a teacher."
    started = wr.begin()
    wr.record(UID, "memory", "m1", "test")
    assert g.guard_text(reply, UID, started) == "I haven't been able to forget that yet, so it's still saved."
    wr.record(UID, "forget", "f1", "test")
    assert g.guard_text(reply, UID, started) == reply


def test_standing_promise_is_a_description_after_a_describe_question():
    """'what reminders have I got?' -> 'I'll remind you at 2:30' describes an existing reminder; the same words answering a
    write request are a promise. Both directions in one test (each is the other's control)."""
    reply = "I'll remind you to call the dentist at half past two tomorrow."
    assert g.guard_text(reply, UID, wr.begin(), user_text="what reminders have I got?") == reply
    assert g.guard_text(reply, UID, wr.begin(), user_text="ok remind me to call the dentist") == HONEST


def test_standing_promise_streams_like_the_one_shot_guard():
    reply = "Sure thing. I'll add rice, beans and spices to your to-do list. Anything else?"
    want = g.guard_text(reply, UID, wr.begin(), user_text="add rice to my to-do list")
    words = [w + " " for w in reply.split(" ")]
    words[-1] = words[-1].rstrip()

    async def gen():
        for w in words:
            yield w

    async def run():
        return "".join([d async for d in g.guard_stream(gen(), UID, wr.begin(), user_text="add rice to my to-do list")])

    assert _run(run()) == want
    assert HONEST in want


EVERY_CUT_REPLIES = [
    "Sure. I'll remind you to call the dentist at half past two tomorrow. Anything else?",
    "Okay, I've taken that dinner event off your calendar. Talk soon.",
    "You've got a work call at three, got that noted. Bye!",
    "I can add it to your list if you like. Consider it forgotten.",
]


@pytest.mark.parametrize("reply", EVERY_CUT_REPLIES)
def test_new_claim_shapes_stream_like_the_one_shot_guard_at_every_cut(reply):
    """Cross-review: the streamed output equals guard_text for the new constructs at EVERY character boundary."""
    want = g.guard_text(reply, UID, wr.begin(), user_text="please do it")
    assert want != reply                                    # each reply really holds an unsupported claim

    async def run(parts):
        async def gen():
            for p in parts:
                yield p

        return "".join([d async for d in g.guard_stream(gen(), UID, wr.begin(), user_text="please do it")])

    for cut in range(1, len(reply)):
        assert _run(run([reply[:cut], reply[cut:]])) == want, cut


# ── 1. session poisoning: the correction note ──────────────────────────────────────────────────────────────────────────

def test_a_replaced_claim_notes_a_correction_for_the_session(monkeypatch):
    _on(monkeypatch)
    out = g.guard_text("Setting a timer for five minutes now.", UID, wr.begin(), session_id=SID)
    assert out == HONEST
    line = cc.take(SID)
    assert line.startswith("(Note: my previous reply said a timer or alarm was done") and "tool call" in line
    assert cc.take(SID) == ""                              # once


def test_a_spanish_replaced_claim_is_corrected_and_neutralised_too(monkeypatch):
    """Cross-review: the guard also enforces Spanish; a Spanish replacement must get a note and never stay in history."""
    _on(monkeypatch)
    out = g.guard_text("He añadido leche a tu lista de compras.", UID, wr.begin(), session_id=SID)
    es_honest = "No logré añadir eso; ¿quieres que lo intente otra vez?"
    assert out == es_honest
    assert cc.take(SID).startswith("(Nota: mi respuesta anterior dijo que se hizo un cambio en una lista")
    assert es_honest not in cc.history_safe("Vale. " + es_honest)
    # a language row with no note of its own falls back to one that has it, never to nothing
    assert cc.render("xx", ("timer",)).startswith("(Note: my previous reply said a timer or alarm")


def test_no_correction_when_the_flag_is_off():
    """Negative control: flag off (the default), nothing is noted - the brain's history keeps the uncorrected claim."""
    g.guard_text("Setting a timer for five minutes now.", UID, wr.begin(), session_id=SID)
    assert cc.pending(SID) is None and cc.take(SID) == ""


def test_no_correction_for_a_supported_claim(monkeypatch):
    _on(monkeypatch)
    started = wr.begin()
    wr.record(UID, "timer", "t1", "test")
    g.guard_text("Setting a timer for five minutes now.", UID, started, session_id=SID)
    assert cc.pending(SID) is None


def test_shadow_mode_notes_nothing(monkeypatch):
    _on(monkeypatch)
    monkeypatch.setenv("ZOE_WRITE_CLAIM_GUARD", "shadow")
    g.guard_text("Setting a timer for five minutes now.", UID, wr.begin(), session_id=SID)
    assert cc.pending(SID) is None


def test_stream_guard_notes_the_correction_too(monkeypatch):
    _on(monkeypatch)

    async def gen():
        for d in ("Setting a timer ", "for five minutes now."):
            yield d

    async def run():
        return "".join([d async for d in g.guard_stream(gen(), UID, wr.begin(), session_id=SID)])

    assert _run(run()) == HONEST
    assert cc.pending(SID) == ("en", ("timer",))


def test_history_safe_swaps_the_replacement_line_for_a_neutral_marker(monkeypatch):
    text = "Sure. " + HONEST + " Anything else?"
    assert cc.history_safe(text) == text                    # flag off: unchanged (control)
    _on(monkeypatch)
    out = cc.history_safe(text)
    assert HONEST not in out and "(nothing was saved in that reply)" in out
    assert cc.history_safe("I haven't been able to forget that yet, so it's still saved.") == "(nothing was saved in that reply)"


def test_fastpath_block_never_shows_the_brain_the_replacement_line(monkeypatch):
    pairs = [("user", "set a timer for five minutes"), ("assistant", HONEST)]
    assert HONEST in ffc.render(pairs)                      # control: flag off, the old bytes
    _on(monkeypatch)
    block = ffc.render(pairs)
    assert HONEST not in block and "Zoe: (nothing was saved in that reply)" in block


class _Stop(Exception):
    pass


def _outbound(monkeypatch, message):
    sent = {}

    def capture(outbound):
        sent["msg"] = outbound
        raise _Stop

    monkeypatch.setattr(zfc, "_request_payload", capture)

    async def run():
        async for _ in zfc._run_flue_brain_streaming_turn(message, SID, UID):
            pass

    with pytest.raises(_Stop):
        _run(run())
    return sent["msg"]


def test_next_brain_turn_opens_with_the_correction_and_only_once(monkeypatch):
    _on(monkeypatch)
    g.guard_text("Setting a timer for five minutes now.", UID, wr.begin(), session_id=SID)
    first = _outbound(monkeypatch, "set a timer for ten minutes")
    lines = first.split("\n")
    note_at = next(i for i, ln in enumerate(lines) if ln.startswith("(Note: my previous reply said"))
    assert lines[-1] == "set a timer for ten minutes" and note_at == len(lines) - 2   # right before the user's words
    assert not any(ln.startswith("[MEMORY CONTEXT") for ln in lines[:note_at])        # not a block the sidecar elides
    assert "(Note:" not in _outbound(monkeypatch, "thanks")


def test_without_the_flag_the_next_turn_is_unchanged(monkeypatch):
    """Negative control for the outbound wiring."""
    g.guard_text("Setting a timer for five minutes now.", UID, wr.begin(), session_id=SID)
    assert "(Note:" not in _outbound(monkeypatch, "set a timer for ten minutes")


# ── 1. the multi-turn session: failed write, then a later timer request still calls the tool ─────────────────────────────

class _Sidecar:
    """A stand-in for the Flue sidecar with its OWN per-session history, and a model that does what the live 4B did on
    2026-11-05: on a write request it copies its last write turn - if that turn "did" the write with no tool call and nothing
    in its history since says otherwise, it claims again with no tool call. A correction line in its history breaks the copy.
    Only that behaviour is modelled; everything between zoe-data and the sidecar is the real code."""

    def __init__(self):
        self.history: dict = {}
        self.fail_next = False
        self.tools: list = []

    def respond(self, sid: str, body: str) -> str:
        hist = self.history.setdefault(sid, [])
        words = body.split("\n")[-1]
        if "timer" not in words:
            hist.append((body, "Okay.", None))
            return "Okay."
        last_write = max((i for i, h in enumerate(hist) if h[2] is not None), default=None)
        corrected = any("(Note: my previous reply said" in h[0] for h in hist[(last_write or 0) + 1:]) or \
            "(Note: my previous reply said" in body
        poisoned = last_write is not None and hist[last_write][2] is False and not corrected
        if self.fail_next or poisoned:
            self.fail_next = False
            reply, tool = "Setting a timer for five minutes now.", False
        else:
            wr.record(UID, "timer", f"t{len(hist)}", "fake_set_timer")
            reply, tool = "Your five minute timer is set.", True
        self.tools.append(tool)
        hist.append((body, reply, tool))
        return reply


def _wire_sidecar(monkeypatch, sidecar: _Sidecar):
    import httpx

    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://127.0.0.1:3578")
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setenv("ZOE_FLUE_STREAM_ENABLED", "0")
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)

    class _Resp:
        def __init__(self, text):
            self._text = text
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"result": {"text": self._text}}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content=None, headers=None):
            sid = url.split("/agents/zoe/")[1].split("?")[0]
            return _Resp(sidecar.respond(sid, json.loads(content)["message"]))

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


def _session(monkeypatch):
    import brain_dispatch as bd

    sidecar = _Sidecar()
    _wire_sidecar(monkeypatch, sidecar)

    async def turn(text):
        return await bd.brain_oneshot(text, SID, UID)

    sidecar.fail_next = True
    first = _run(turn("hey can you set a timer for five minutes"))
    second = _run(turn("set a timer for ten minutes"))
    return sidecar, first, second


def test_failed_write_then_a_later_timer_request_still_calls_the_tool(monkeypatch):
    _on(monkeypatch)
    sidecar, first, second = _session(monkeypatch)
    assert first == HONEST                                   # the user heard the truth about turn 1
    assert sidecar.tools == [False, True]                    # turn 2 called the tool
    assert second == "Your five minute timer is set."


def test_without_the_correction_the_session_stays_poisoned(monkeypatch):
    """Negative control: the same session with the flag off is the QA bug - turn 2 calls no tool and is replaced again."""
    sidecar, first, second = _session(monkeypatch)
    assert first == HONEST
    assert sidecar.tools == [False, False]
    assert second == HONEST
