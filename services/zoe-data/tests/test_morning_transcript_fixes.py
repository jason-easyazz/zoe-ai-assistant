"""Three classes of failure seen in one morning of live voice turns (2026-10-15), each pinned with INVENTED phrasings.

1. "what's my name" is a NAME question: it is answered "Your name is <Name>." from the account's display name (never the raw
   lowercase user id), and a guest is told how to introduce themselves - it is never answered as a sign-in statement.
2. Spoken arithmetic (number words, pi / e, symbols and words, follow-ups on the previous result) is answered by the
   calculator fast path, safely (no eval), instead of costing a brain turn.
3. The struggle detector's FALLTHROUGH does not blame a router domain the words never touched (a calculator follow-up
   filed under ``music``).

Hard negatives and a negative control sit beside every positive list: a guard that cannot be broken is not a guard.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import calc_speech as cs  # noqa: E402
import identity_facts  # noqa: E402
import skybridge_service  # noqa: E402
import struggle_signals as s  # noqa: E402
from skybridge_service import classify_skybridge_intent, resolve_skybridge_request  # noqa: E402

pytestmark = pytest.mark.ci_safe


# ── 1. "what's my name" ──────────────────────────────────────────────────────

class _Db:
    """The one query the identity ask makes: the users row."""

    def __init__(self, row=None):
        self._row = row

    async def fetch(self, *args):
        return [self._row] if self._row and "FROM users" in str(args[0]) else []


def _ident(monkeypatch, ident):
    async def _resolve(user_id, db=None, **_kw):
        return ident

    monkeypatch.setattr(identity_facts, "resolve_identity", _resolve)


NAME_ASKS = ["what's my name", "what is my name", "whats my name", "hey zoe, what's my name?", "What's my name"]


@pytest.mark.parametrize("text", NAME_ASKS)
def test_a_name_question_classifies_as_the_name_kind(text):
    intent = classify_skybridge_intent(text)
    assert intent is not None and (intent.domain, intent.action, intent.query) == ("people", "identity", "name")


@pytest.mark.parametrize("text", ["who am i", "who's signed in", "who am i signed in as", "am i signed in"])
def test_a_signin_question_keeps_the_signin_kind(text):
    intent = classify_skybridge_intent(text)
    assert intent is not None and (intent.domain, intent.action, intent.query) == ("people", "identity", "")


@pytest.mark.parametrize("text", ["what's my dog's name", "what is her name", "what's my sister's name", "what's the name of this song"])
def test_other_names_are_not_the_identity_ask(text):
    intent = classify_skybridge_intent(text)
    assert intent is None or intent.action != "identity"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", NAME_ASKS)
async def test_a_member_hears_their_display_name_not_their_user_id(monkeypatch, text):
    """The account's name is stored lowercase and the id is a key: the reply capitalises the name and never says the id."""
    _ident(monkeypatch, None)       # identity_facts unavailable: the users row decides
    res = await resolve_skybridge_request(text, "jason", db=_Db({"name": "jason", "role": "admin"}))
    assert res["spoken_summary"] == "Your name is Jason."
    assert res["cards"][0]["content"]["person"]["name"] == "Jason"


@pytest.mark.asyncio
async def test_the_preferred_name_wins_when_the_account_has_one(monkeypatch):
    _ident(monkeypatch, identity_facts.Identity(user_id="jason", name="Jay", account_name="Jason", has_preferred_name=True))
    res = await resolve_skybridge_request("what's my name", "jason", db=_Db({"name": "jason", "role": "admin"}))
    assert res["spoken_summary"] == "Your name is Jay."


@pytest.mark.asyncio
async def test_with_no_users_row_the_id_is_still_made_presentable(monkeypatch):
    _ident(monkeypatch, None)
    res = await resolve_skybridge_request("what's my name", "maya", db=_Db(None))
    assert res["spoken_summary"] == "Your name is Maya."


@pytest.mark.asyncio
async def test_who_am_i_still_says_signed_in_and_uses_the_display_name(monkeypatch):
    _ident(monkeypatch, None)
    res = await resolve_skybridge_request("who am i", "jason", db=_Db({"name": "jason", "role": "admin"}))
    assert res["spoken_summary"] == "You're signed in as Jason."


@pytest.mark.asyncio
async def test_a_guest_asking_their_name_is_told_how_to_introduce_themselves():
    for guest in ("guest", "voice-guest"):
        res = await resolve_skybridge_request("what's my name", guest, db=_Db(None))
        assert res["spoken_summary"] == skybridge_service.GUEST_NAME_REPLY
        assert "signed in as" not in res["spoken_summary"]
        assert "PIN" in res["spoken_summary"] and "it's" not in res["spoken_summary"]   # no introduction path exists: never offered
    # control: the guest's SIGN-IN question keeps its own answer
    assert (await resolve_skybridge_request("who am i", "guest", db=_Db(None)))["spoken_summary"] == "You're signed in as a guest."


@pytest.mark.asyncio
async def test_control_the_old_resolver_said_signed_in_as_the_lowercase_id(monkeypatch):
    """Negative control: with the account lookup and the capitalisation removed, the reply IS the old bug - so the tests above
    are measuring the fix, not a coincidence."""
    _ident(monkeypatch, None)
    monkeypatch.setattr(skybridge_service, "_humanize_user_id", lambda uid: uid)

    class _Raw(_Db):
        async def fetch(self, *args):
            return [{"name": "jason", "role": "admin"}]

    async def _raw_name(user_id, db):
        rows = await db.fetch("SELECT name, role FROM users WHERE id = $1 LIMIT 1", user_id)
        return str(rows[0]["name"]), ""

    monkeypatch.setattr(skybridge_service, "_self_display_name", _raw_name)
    res = await resolve_skybridge_request("what's my name", "jason", db=_Raw())
    assert res["spoken_summary"] == "Your name is jason."          # the failure the real fix removes


# ── 2. spoken arithmetic ─────────────────────────────────────────────────────

SUMS = [
    ("what's two plus two", "Two plus two is four."),
    ("What is 2 + 2?", "Two plus two is four."),
    ("what about 100 times 100", "100 times 100 is 10,000."),
    ("what's a hundred times a hundred", "100 times 100 is 10,000."),
    ("what about 3 times pi", "3 times pi is about 9.42."),
    ("what's 15 * 4", "15 times 4 is 60."),
    ("7 x 8", "7 times 8 is 56."),
    ("what is 9 × 9", "9 times 9 is 81."),
    ("what is 84 ÷ 7", "84 divided by 7 is 12."),
    ("what is twelve over four", "Twelve divided by four is three."),
    ("hey zoe, what's twenty one plus thirty five", "21 plus 35 is 56."),
    ("what's three point five times two", "3.5 times 2 is 7."),
    ("how much is five million divided by four", "5,000,000 divided by 4 is 1,250,000."),
    ("what's 1,000 plus 2,500", "1,000 plus 2,500 is 3,500."),
    ("what's 25 percent of 80", "25 percent of 80 is 20."),
    ("what is 15% of 200", "15 percent of 200 is 30."),
    ("what's the square root of sixteen", "The square root of sixteen is four."),
    ("what's the square root of two", "The square root of 2 is about 1.41."),
    ("what is nine squared", "9 squared is 81."),
    ("what's 10 divided by 3", "10 divided by 3 is about 3.33."),
    ("what's seven minus ten", "7 minus 10 is minus 3."),
    ("what is 2 plus 3 times 4", "Two plus three times four is fourteen."),
    ("calculate 6 times 7", "6 times 7 is 42."),
    ("what's the answer to two plus two", "Two plus two is four."),
    ("what's e times two", "The number e times 2 is about 5.44."),
]


@pytest.mark.parametrize(("text", "reply"), SUMS)
def test_a_spoken_sum_is_answered_the_way_a_person_says_it(text, reply):
    got = cs.parse(text)
    assert got is not None and got.reply == reply, (text, got and got.reply)


@pytest.mark.parametrize("text", [
    "what's two plus two in French", "times are hard", "pi day", "what is pi", "what's five", "tell me about square roots",
    "what about three times a week", "set a timer for two minutes plus thirty seconds", "add two plus two to my list",
    "I have two plus points", "what's the time over there", "play song two times", "two over there", "what's the weather today",
    "turn on light 2", "twenty twenty four", "what is the square root of", "minus", "plus", "what's 5 plus", "x plus y",
    "who is 12 times", "what about", "what's 2 ** 10", "9 ** 9 ** 9", "2 to the power of 10",
])
def test_hard_negatives_are_not_arithmetic(text):
    assert cs.parse(text) is None
    assert cs.parse(text, 100.0) is None


@pytest.mark.parametrize("text", [
    "__import__('os').system('id')", "what is __import__('os')", "2 plus __import__('os').getcwd()", "open('/etc/passwd')",
    "exec('1+1')", "2 plus (3 times 4)", "lambda: 1", "[1] * 99999999", "9" * 400 + " plus 1",
])
def test_nothing_that_is_not_a_plain_sum_is_evaluated(text):
    assert cs.parse(text) is None


def test_the_calculator_never_uses_eval_exec_or_ast():
    import re

    code = "\n".join(line.split("#", 1)[0] for line in inspect.getsource(cs).splitlines())
    code = re.sub(r'""".*?"""', "", code, flags=re.S)          # the docstring says what it does NOT do
    assert not re.search(r"(?<![\w.])(?:eval|exec|compile)\s*\(", code)
    assert not re.search(r"__import__|^\s*(?:import|from)\s+ast\b|literal_eval|\bsubprocess\b", code, flags=re.M)


def test_division_by_zero_and_huge_results_are_answered_honestly():
    assert cs.parse("what's five divided by zero").reply == "I can't divide by zero."
    assert cs.parse("what's the square root of negative four").reply == "I can't take the square root of a negative number."
    big = cs.parse("what is 999999999 times 999999999 times 999999999")
    assert big is not None and big.reply == "That's too big for me to say out loud." and big.value is None


FOLLOW_UPS = [
    # (previous sum, follow-up, reply)
    ("what about 100 times 100", "that one divided by 3.5", "That's about 2,857.14."),
    ("what about 100 times 100", "what's that times three", "That's 30,000."),
    ("what's two plus two", "and times three", "That's 12."),
    ("what's two plus two", "times 3", "That's 12."),
    ("what's two plus two", "plus ten", "That's 14."),
    ("what's two plus two", "minus one", "That's 3."),
    ("what's two plus two", "the answer plus ten", "That's 14."),
    ("what's two plus two", "what about that squared", "That's 16."),
    ("what's ten times ten", "that over four", "That's 25."),
    ("what is 20 divided by 8", "what about 3 times pi", "3 times pi is about 9.42."),
]


@pytest.mark.parametrize(("first", "follow", "reply"), FOLLOW_UPS)
def test_a_follow_up_points_at_the_previous_result(first, follow, reply):
    cs.forget()
    assert cs.answer(first, "u|s") is not None
    assert cs.answer(follow, "u|s") == reply


def test_a_follow_up_chains_on_its_own_result():
    cs.forget()
    assert cs.answer("what about 100 times 100", "u|s") == "100 times 100 is 10,000."
    assert cs.answer("that one divided by 3.5", "u|s") == "That's about 2,857.14."
    assert cs.answer("that times 3.5", "u|s") == "That's 10,000."


def test_a_follow_up_needs_a_recent_result_in_the_same_conversation():
    """Negative control: the same words with no previous result (or an old one, or another conversation's) are NOT claimed."""
    cs.forget()
    for text in ("that one divided by 3.5", "that times three", "times 3", "plus ten", "the answer minus 1"):
        assert cs.answer(text, "u|s") is None, text
    cs.remember("u|s", 100.0, now=1000.0)
    assert cs.answer("that one divided by 4", "u|s", now=1000.0 + cs.WINDOW_S - 1) == "That's 25."
    cs.remember("u|s", 100.0, now=1000.0)
    assert cs.answer("that one divided by 4", "u|s", now=1000.0 + cs.WINDOW_S + 1) is None
    cs.remember("u|a", 100.0)
    assert cs.answer("that one divided by 4", "u|b") is None


def test_an_error_does_not_replace_the_remembered_result():
    cs.forget()
    cs.answer("what's 8 times 8", "u|s")
    assert cs.answer("that divided by zero", "u|s") == "I can't divide by zero."
    assert cs.answer("that minus 4", "u|s") == "That's 60."


def test_the_last_result_store_is_bounded():
    cs.forget()
    for i in range(cs._MAX_KEYS + 40):
        cs.remember(f"u|{i}", float(i))
    assert len(cs._last) <= cs._MAX_KEYS and cs.recall("u|0") is None and cs.recall(f"u|{cs._MAX_KEYS + 39}") is not None


def test_detect_intent_claims_a_spoken_sum_and_executes_it():
    import intent_router as ir

    intent = ir.detect_intent("what's two plus two", log_miss=False)
    assert intent is not None and intent.name == "calculate"
    assert asyncio.run(ir.execute_intent(intent, "guest")) == "Two plus two is four."
    for text in ("times are hard", "pi day", "what's two plus two in French"):
        got = ir.detect_intent(text, log_miss=False)
        assert got is None or got.name != "calculate", text


@pytest.mark.parametrize("channel", ["voice", "chat"])
def test_the_fast_tier_answers_sums_and_follow_ups_on_both_lanes(channel):
    import fast_tiers

    cs.forget()

    async def go(text):
        kw = {"phase": "conversation"} if channel == "voice" else {}
        return await fast_tiers.resolve(text, "member-a", "sess-1", channel=channel, **kw)

    first = asyncio.run(go("what about 100 times 100"))
    assert first is not None and first.reply == "100 times 100 is 10,000." and first.tier == "calc"
    second = asyncio.run(go("that one divided by 3.5"))
    assert second is not None and second.reply == "That's about 2,857.14."
    # a conversation that never did a sum is not claimed
    cs.forget()
    assert asyncio.run(go("that one divided by 3.5")) is None


# ── 3. FALLTHROUGH must not blame a domain the words never touched ──────────

def _turn(text, *, dom, score=0.0, conf=0.95, path=s.PATH_BRAIN, reply="Sure."):
    return s.Turn(ts=0.0, channel="voice", user_id="member-a", is_guest=False, conv_key="voice:hall", text=text, reply=reply,
                  path=path, predicted_domain=dom, predicted_score=score, two_stage_domain=dom, two_stage_conf=conf,
                  two_stage_active=True)


def _signals(turn):
    return [e.signal for e in s.detect(None, turn)]


@pytest.mark.parametrize("text", [
    "that one divided by 3.5", "what's that times three", "what about 3 times pi", "what's two plus two", "and plus ten",
])
@pytest.mark.parametrize("dom", ["music", "smart_home", "weather", "timers", "lists", "calendar", "time", "reminders"])
def test_a_sum_is_never_a_fallthrough_in_any_domain(text, dom):
    assert "FALLTHROUGH" not in _signals(_turn(text, dom=dom, score=0.9))
    assert "FALLTHROUGH" not in _signals(_turn(text, dom=dom, score=0.0, conf=0.97))


@pytest.mark.parametrize(("text", "dom"), [
    ("that one is lovely", "music"), ("how about the other thing", "music"), ("it was fine thanks", "smart_home"),
    ("tell me something interesting", "music"), ("what is a good name for a cat", "smart_home"),
])
def test_a_head_alone_needs_a_cue_for_the_domain_it_names(text, dom):
    """The head's word is the only witness for a domain with no similarity examples; the turn must at least carry a word that
    domain's requests carry."""
    assert "FALLTHROUGH" not in _signals(_turn(text, dom=dom))


@pytest.mark.parametrize(("text", "dom"), [
    ("put some jazz on", "music"), ("play that song again", "music"), ("make it a bit louder", "music"),
    ("dim the lounge light", "smart_home"), ("turn the heater off", "smart_home"), ("lock the front door", "smart_home"),
])
def test_control_a_cued_turn_still_falls_through(text, dom):
    assert "FALLTHROUGH" in _signals(_turn(text, dom=dom))


def test_control_without_the_guards_the_calculator_follow_up_is_blamed_on_music(monkeypatch):
    """Negative control: neutralise both guards and the original wrong attribution comes back - the tests above measure them."""
    turn = _turn("that one divided by 3.5", dom="music")
    assert "FALLTHROUGH" not in _signals(turn)
    monkeypatch.setattr(s, "domain_plausible", lambda dom, text: True)
    monkeypatch.setattr(s, "_is_arithmetic", lambda text: False)
    events = [e for e in s.detect(None, turn) if e.signal == "FALLTHROUGH"]
    assert events and events[0].numbers["domain"] == "music"


def test_a_second_witness_still_carries_a_domain_without_a_cue():
    """Similarity agreeing with the head is a real second witness: no cue is demanded (existing behaviour, unchanged)."""
    assert "FALLTHROUGH" in _signals(_turn("a request", dom="weather", score=0.9))
