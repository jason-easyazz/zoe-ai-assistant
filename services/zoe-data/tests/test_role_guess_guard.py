"""Samantha bar S22 on the ASK path - roles are stated, never guessed (ZOE_ROLE_GUESS_GUARD).

Regression 2026-10-07 (live main 4ceb35ec): S22 passed six consecutive runs, then failed 3 of 4. The
roster turn still asked who's who, but the later ask "Who is Anika Reyes?" was answered "Anika Reyes
is your mother". Since the named-person recall floor (#1899) that ask reaches memory; the packet
holds the person's row ("Anika Reyes: 2 November 1985") - no role anywhere in the store or the
packet - and the 4B brain filled the gap from the name. ``role_guess_guard`` marks such a person
"relationship not stated" inside the recall block and rewrites a guessed role in the reply.

Negative controls: ``ZOE_ROLE_GUESS_GUARD=off`` -> the guessed role survives; a role the packet or
the user states is never touched. Every name is synthetic.
"""
from __future__ import annotations

import json

import pytest

import person_recall_floor as prf
import role_guess_guard as rg
import zoe_flue_client as zc

pytestmark = pytest.mark.ci_safe  # fakes only: no DB, no model, no live service

ASK = "Who is Anika Reyes?"
NAMES = ["Anika Reyes", "Callum Reyes"]
ROLELESS = (
    "## What I know about you\n"
    "- Anika Reyes: 2 November 1985 (Wed 7 Oct, today) [mem:aaaa1111]\n"
    "- Callum Reyes: 14 March 1980 (Wed 7 Oct, today) [mem:bbbb2222]\n"
)
GUESSED = "Anika Reyes is your mother, and she was born on November 2, 1985."


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(rg.ENV, raising=False)
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    monkeypatch.delenv("ZOE_SEAM_CONTINUITY_INJECT", raising=False)


# -- the pure rewrite ----------------------------------------------------------------

@pytest.mark.parametrize("reply,role", [
    (GUESSED, "mother"),
    ("Anika Reyes is your wife.", "wife"),
    ("Anika is Callum's wife and was born in 1985.", "wife"),
    ("Your mother Anika Reyes was born on November 2, 1985.", "mother"),
    ("Anika Reyes, your sister, was born on November 2, 1985.", "sister"),
    ("Anika Reyes (your mum) was born on November 2, 1985.", "mum"),
    ("Callum is Anika's husband.", "husband"),
])
def test_a_role_nothing_states_is_rewritten_and_a_question_added(reply, role):
    out, guessed = rg.neutralise(reply, NAMES, ROLELESS, user_text=ASK)
    assert guessed and guessed[0].endswith("~" + role), guessed
    # the role word is gone from every sentence that names a person
    assert not any(role in sen.lower() for sen in out.split(".") if "anika" in sen.lower() or "callum" in sen.lower()), out
    assert out.count("?") == 1 and "I haven't been told how" in out and out.rstrip().endswith("could you tell me?")


def test_the_birthday_survives_the_rewrite():
    out, _ = rg.neutralise(GUESSED, NAMES, ROLELESS, user_text=ASK)
    assert "November 2, 1985" in out and out.startswith("Anika Reyes is someone you've told me about")


@pytest.mark.parametrize("reply", [
    "Anika Reyes was born on November 2, 1985.",
    "I have Anika Reyes down with a birthday of 2 November 1985 but not how you know her.",
    "Anika Reyes is a friend of yours.",                       # loose labels are not a family guess
    "Your mother called earlier.",                              # a role with nobody named
    "Priya Nair is your mother.",                               # not a person this turn named
])
def test_a_reply_that_assigns_nothing_is_untouched(reply):
    assert rg.neutralise(reply, NAMES, ROLELESS, user_text=ASK) == (reply, [])


def test_a_role_the_packet_states_is_never_touched():
    packet = ROLELESS + "- User's mum is Anika Reyes [mem:cccc3333]\n"
    assert rg.neutralise(GUESSED, NAMES, packet, user_text=ASK) == (GUESSED, [])


def test_a_role_the_user_just_stated_is_never_touched():
    said = "Anika Reyes is my mother, who is she again?"
    assert rg.neutralise(GUESSED, NAMES, ROLELESS, user_text=said) == (GUESSED, [])


def test_a_role_stated_for_another_person_does_not_carry_over():
    packet = ROLELESS + "- User's friend Callum Reyes's wife is Anika Reyes\n"
    # stated for Anika (wife): fine. Her being "mother" is still a guess.
    assert rg.neutralise("Anika Reyes is Callum's wife.", NAMES, packet, user_text=ASK)[1] == []
    assert rg.neutralise(GUESSED, NAMES, packet, user_text=ASK)[1] == ["anika~mother"]


@pytest.mark.parametrize("reply,role", [
    ("Your mother is Anika Reyes.", "mother"),
    ("Your mother is Anika Reyes, born on November 2, 1985.", "mother"),
    ("Your mum's name is Anika.", "mum"),
    ("Your sister was Anika Reyes.", "sister"),
    ("Callum's wife is Anika Reyes.", "wife"),
    ("Your wife is called Anika.", "wife"),
])
def test_a_role_first_copular_claim_is_rewritten_too(reply, role):
    out, guessed = rg.neutralise(reply, NAMES, ROLELESS, user_text=ASK)
    assert guessed and guessed[0].endswith("~" + role), (reply, guessed)
    assert role not in out.lower().replace("someone you've told me about", "")
    assert "someone you've told me about" in out and out.count("?") == 1


def test_a_role_first_copular_claim_the_evidence_states_is_untouched():
    for packet, reply in [
        (ROLELESS + "- User's mum is Anika Reyes [mem:cccc3333]\n", "Your mother is Anika Reyes."),
        (ROLELESS + "- Anika Reyes is Callum Reyes's wife\n", "Callum's wife is Anika Reyes."),
    ]:
        assert rg.neutralise(reply, NAMES, packet, user_text=ASK) == (reply, [])
    assert rg.neutralise("Your mother is Anika Reyes.", NAMES, ROLELESS, "Anika Reyes is my mother") == (
        "Your mother is Anika Reyes.", [])


@pytest.mark.parametrize("packet_row", [
    "- Anika Reyes is Callum Reyes's wife [mem:eeee5555]\n",
    "- Anika Reyes is the wife of Callum Reyes [mem:eeee5555]\n",
    "- Callum Reyes's wife is Anika Reyes [mem:eeee5555]\n",
])
def test_a_role_tied_to_someone_elses_relative_does_not_license_it_for_the_user(packet_row):
    packet = ROLELESS + packet_row
    # stated for Callum: answering it as stated is fine ...
    assert rg.neutralise("Anika Reyes is Callum's wife.", NAMES, packet, user_text=ASK)[1] == []
    # ... but the same role claimed for the USER is a guess
    for reply in ("Anika Reyes is your wife.", "Your wife is Anika Reyes.", "Anika Reyes, your wife, was born in 1985."):
        out, guessed = rg.neutralise(reply, NAMES, packet, user_text=ASK)
        assert guessed == ["anika~wife"], (reply, guessed)
        assert "wife" not in out.lower() and out.endswith("could you tell me?"), out


def test_the_owner_check_keeps_what_the_user_or_an_unowned_row_states():
    assert rg.role_supported_for("Anika Reyes", "wife", "Anika Reyes is my wife", "user")
    assert rg.role_supported_for("Anika Reyes", "wife", "- User's wife is Anika Reyes", "user")
    assert rg.role_supported_for("Anika Reyes", "wife", "- Anika, wife", "user")          # owner unspecified
    assert not rg.role_supported_for("Anika Reyes", "wife", "Anika Reyes is Callum's wife", "user")
    assert not rg.role_supported_for("Anika Reyes", "wife", "Anika Reyes is my wife", ("name", ("callum",)))
    assert rg.role_supported_for("Anika Reyes", "wife", "Anika Reyes is Callum Reyes's wife", ("name", ("callum",)))


def test_guarded_people_include_those_the_packet_relates_only_to_someone_else():
    packet = ROLELESS + "- Anika Reyes is Callum Reyes's wife\n- User's brother is Callum Reyes\n"
    assert rg.guarded_people(NAMES, packet) == ["Anika Reyes"]          # Callum is the user's brother
    assert rg.unstated_people(NAMES, packet) == []                        # the rule line stays quiet


@pytest.mark.parametrize("reply,role", [
    ("Anika Reyes might be your mother.", "mother"),
    ("Anika seems to be your mother.", "mother"),
    ("Anika is probably your mother.", "mother"),
    ("Anika could well be your sister, I think.", "sister"),
    ("Your mother might be Anika Reyes.", "mother"),
    ("Anika sounds like your wife.", "wife"),
])
def test_a_hedged_or_modal_guess_is_still_a_guess(reply, role):
    out, guessed = rg.neutralise(reply, NAMES, ROLELESS, user_text=ASK)
    assert guessed and guessed[0].endswith("~" + role), (reply, guessed)
    assert role not in out.lower() and out.endswith("could you tell me?"), out


def test_a_hedge_on_a_stated_role_is_left_alone():
    packet = ROLELESS + "- User's mum is Anika Reyes\n"
    for reply in ("Anika might be your mother.", "Anika is probably your mum."):
        assert rg.neutralise(reply, NAMES, packet, user_text=ASK) == (reply, [])


def test_a_role_stated_for_a_namesake_does_not_license_the_other_person():
    packet = ROLELESS + "- User's mother is Anika Patel\n"
    assert rg.role_supported_for("Anika Patel", "mother", packet, "user")
    assert not rg.role_supported_for("Anika Reyes", "mother", packet, "user")
    out, guessed = rg.neutralise("Anika Reyes is your mother.", NAMES, packet, user_text=ASK)
    assert guessed == ["anika~mother"] and "mother" not in out.lower()
    assert rg.guarded_people(NAMES, packet) == ["Anika Reyes", "Callum Reyes"]
    # ... while a bare first name in the evidence still counts for her
    assert rg.role_supported_for("Anika Reyes", "mother", ROLELESS + "- User's mother is Anika\n", "user")


@pytest.mark.parametrize("reply", [
    "Anika Reyes is John Smith's wife.",
    "John Smith's wife is Anika Reyes.",
])
def test_the_owner_must_match_completely_not_by_a_shared_surname(reply):
    packet = ROLELESS + "- Anika Reyes is Mary Smith's wife\n"
    assert rg.neutralise("Anika Reyes is Mary Smith's wife.", NAMES, packet, user_text=ASK)[1] == []
    assert rg.neutralise(reply, NAMES, packet, user_text=ASK)[1] == ["anika~wife"]
    # a bare given name still names the same owner as the full name
    pk2 = ROLELESS + "- Anika Reyes is Mary Smith's wife\n"
    assert rg.neutralise("Anika Reyes is Mary's wife.", NAMES, pk2, user_text=ASK)[1] == []


def test_nested_possessives_compare_equal_however_the_user_is_worded():
    packet = ROLELESS + "- User's friend's wife is Anika Reyes [mem:ffff6666]\n"
    for reply in ("Your friend's wife is Anika Reyes.", "Anika Reyes is your friend's wife.",
                  "Anika Reyes is the wife of your friend."):
        assert rg.neutralise(reply, NAMES, packet, user_text=ASK) == (reply, []), reply
    # ... and the user's OWN wife is a different claim than a friend's wife
    assert rg.neutralise("Anika Reyes is your wife.", NAMES, packet, user_text=ASK)[1] == ["anika~wife"]
    assert rg.neutralise("Anika Reyes is your brother's wife.", NAMES, packet, user_text=ASK)[1] == ["anika~wife"]


def test_a_first_name_two_people_share_is_not_a_handle():
    out, guessed = rg.neutralise("Dana is your sister.", ["Dana Reyes", "Dana Whitfield"], "- Dana Reyes: 1 May 1990", "")
    assert guessed == []


def test_the_rule_line_marks_exactly_the_people_no_row_relates():
    line = rg.rule_line(NAMES, ROLELESS)
    assert line.startswith("Relationship not stated for: Anika Reyes, Callum Reyes.")
    assert "never guessed" in line
    stated = ROLELESS + "- User's friend's wife is Anika Reyes\n"
    assert rg.rule_line(NAMES, stated).startswith("Relationship not stated for: Callum Reyes.")
    assert rg.rule_line(["Anika Reyes"], stated) == ""
    assert rg.rule_line([], ROLELESS) == ""


@pytest.mark.parametrize("raw,want", [(None, "on"), ("1", "on"), ("on", "on"), ("shadow", "shadow"),
                                       ("off", "off"), ("0", "off"), ("false", "off")])
def test_mode(monkeypatch, raw, want):
    if raw is not None:
        monkeypatch.setenv(rg.ENV, raw)
    assert rg.mode() == want


# -- the stream filter -------------------------------------------------------------------

async def _drain(gen):
    return [c async for c in gen]


def _turn(chunks):
    async def gen():
        for c in chunks:
            yield c
    return gen()


async def test_an_unguarded_turn_streams_byte_identical():
    chunks = ["__THINKING__:{}", "Anika Reyes is ", "your mother, ", "obviously."]
    assert await _drain(rg.filter_stream(_turn(chunks), {})) == chunks


async def test_a_guarded_turn_holds_the_text_and_passes_sentinels_at_once():
    sink = {"names": NAMES, "packet": ROLELESS}
    out = await _drain(rg.filter_stream(_turn(["__TOOL__:{}", "Anika Reyes is ", "your mother, ", "born 1985."]), sink, ASK))
    assert out[0] == "__TOOL__:{}" and len(out) == 2
    assert "mother" not in out[1] and "someone you've told me about" in out[1] and out[1].endswith("tell me?")


async def test_a_guarded_turn_with_no_guess_comes_back_whole():
    sink = {"names": NAMES, "packet": ROLELESS}
    out = await _drain(rg.filter_stream(_turn(["Anika Reyes was born ", "on 2 November 1985."]), sink, ASK))
    assert "".join(out) == "Anika Reyes was born on 2 November 1985."


async def test_negative_control_guard_off_the_guessed_role_survives(monkeypatch):
    monkeypatch.setenv(rg.ENV, "off")
    sink = {"names": NAMES, "packet": ROLELESS}
    out = await _drain(rg.filter_stream(_turn([GUESSED]), sink, ASK))
    assert out == [GUESSED]          # <- with the guard on this is the rewritten text (test above)


async def test_shadow_logs_and_changes_nothing(monkeypatch, caplog):
    import logging

    caplog.set_level(logging.INFO, logger=rg.__name__)
    monkeypatch.setenv(rg.ENV, "shadow")
    sink = {"names": NAMES, "packet": ROLELESS}
    out = await _drain(rg.filter_stream(_turn([GUESSED]), sink, ASK))
    assert out == [GUESSED]
    assert any("ROLE_GUESS_GUARD mode=shadow" in r.message for r in caplog.records)


async def test_closing_the_wrapper_closes_the_inner_turn():
    closed = []

    async def inner():
        try:
            yield "a"
            yield "b"
        finally:
            closed.append(True)

    g = rg.filter_stream(inner(), {})
    assert await g.__anext__() == "a"
    await g.aclose()
    assert closed == [True]


# -- the seam: the S22 ask end to end through run_flue_brain_streaming ----------------------

class _Resp:
    def __init__(self, text):
        self._t = text

    def raise_for_status(self):
        return None

    def json(self):
        return {"result": {"text": self._t}}


class _Client:
    captured: dict = {}
    reply = "ok"

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, content=None, headers=None):
        type(self).captured["content"] = content
        return _Resp(type(self).reply)


async def _ask(monkeypatch, message, brain_reply, *, packet=ROLELESS, floor=True, uid="demo-owner"):
    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setattr(_Client, "captured", {})
    monkeypatch.setattr(_Client, "reply", brain_reply)
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    async def fake_floor(msg, user_id):
        return [prf.NamedPerson("Anika Reyes", "", "memory")] if floor else []

    async def fake_fetch(user_id, msg, focus=None):
        return packet

    async def no_continuity(*a, **k):
        return ""

    monkeypatch.setattr(zc, "_named_person_floor", fake_floor)
    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake_fetch)
    monkeypatch.setattr(zc, "_continuity_context_block", no_continuity)
    out = [c async for c in zc.run_flue_brain_streaming(message, "s22", uid)]
    return "".join(out), json.loads(_Client.captured["content"])["message"]


async def test_s22_the_ask_block_carries_the_marker_and_the_guessed_role_never_reaches_the_user(monkeypatch):
    reply, sent = await _ask(monkeypatch, ASK, GUESSED)
    assert "Relationship not stated for: Anika Reyes." in sent          # the role: unknown marker, in the block
    block = sent[: sent.index(zc._RECALL_BLOCK_CLOSE)]
    assert "Relationship not stated for" in block and "Anika Reyes: 2 November 1985" in block
    assert "mother" not in reply.lower() and "someone you've told me about" in reply
    assert "November 2, 1985" in reply and reply.endswith("tell me?")


async def test_s22_negative_control_guard_off_the_guess_survives_and_no_marker_is_sent(monkeypatch):
    monkeypatch.setenv(rg.ENV, "off")
    reply, sent = await _ask(monkeypatch, ASK, GUESSED)
    assert reply == GUESSED                       # RED if the guard were not what removed it
    assert "Relationship not stated" not in sent  # byte-identical to before the marker existed


async def test_a_role_the_store_states_is_answered_as_stated(monkeypatch):
    packet = ROLELESS + "- User's friend's wife is Anika Reyes [mem:dddd4444]\n"
    reply, sent = await _ask(monkeypatch, ASK, "Anika Reyes is your friend's wife.", packet=packet)
    assert reply == "Anika Reyes is your friend's wife."
    assert "Relationship not stated for: Anika Reyes" not in sent


async def test_a_turn_where_the_floor_did_not_fire_is_untouched(monkeypatch):
    msg = "Tell me a joke"
    reply, sent = await _ask(monkeypatch, msg, GUESSED, floor=False)
    assert reply == GUESSED and "Relationship not stated" not in sent


async def test_s22_a_wife_of_someone_else_is_not_answered_as_the_users_wife_end_to_end(monkeypatch):
    packet = ROLELESS + "- Anika Reyes is Callum Reyes's wife [mem:eeee5555]\n"
    reply, sent = await _ask(monkeypatch, ASK, "Anika Reyes is your wife.", packet=packet)
    assert "wife" not in reply.lower() and "someone you've told me about" in reply and reply.endswith("tell me?")
    # the packet states a role for her, so no "not stated" marker contradicts it
    assert "Relationship not stated for: Anika Reyes" not in sent


async def test_s22_a_role_first_copular_guess_is_caught_end_to_end(monkeypatch):
    reply, _ = await _ask(monkeypatch, ASK, "Your mother is Anika Reyes, born on November 2, 1985.")
    assert "mother" not in reply.lower() and "November 2, 1985" in reply and reply.endswith("tell me?")
