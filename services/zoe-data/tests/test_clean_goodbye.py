"""A clean goodbye (ZOE_CLEAN_GOODBYE) - the person bench's P8.

Baseline (live, 2026-10-09): farewells were clean 8 of 10 (a question, a hook, a callback to the owner's day in the
other two); "are you there?" got "I don't have a way to check if anyone is home" 2 of 10; the content-free turns
("...", "mm") were answered with "It seems like you might have trailed off. Is there something on your mind...?" 7 times
in 10 - which the bench's remark lexicon does not know, so its 10 of 10 on that half is an artefact of the lexicon.
The guard runs on the reply, only on a farewell / presence check / content-free turn, and leaves every other turn
byte-identical and unbuffered.

The replies below are the real baseline replies (synthetic user, no names). Negative controls: the hook, the gusher
and the nag stub policies are cleaned; a clean reply is returned unchanged; a farewell that also asks for something is
not a farewell; shadow and off change nothing. (ci_safe: no model, no network.)
"""
from __future__ import annotations

import asyncio

import pytest

import clean_goodbye as cg

pytestmark = pytest.mark.ci_safe

FAREWELLS = ["night Zoe", "I've got to go", "bye, back tomorrow", "ok that's all for now, thanks", "catch you later",
             "heading to bed", "good night Zoe", "gotta run, see you", "talk later", "I'm off to work, bye"]
BASELINE_REPLIES = [
    "Good evening. How can I help you settle in for the night?", "It sounds like you're heading out. Have a good one!",
    "See you tomorrow. Take care until then.", "You're welcome. Feel free to reach out whenever you need anything else.",
    "Talk to you later.", "Goodnight, I hope you have a really restful sleep.",
    "Good evening! Hope your day was wonderful. See you tomorrow for your 8 AM meeting!",
    "I'll be here when you get back. Take care.", "I'll be here whenever you're ready to chat. Take care until then.",
    "Have a good day at work, I'll be here.",
]
FAREWELL_WORDS = cg._FAREWELL_WORDS


def _clean_form(reply: str) -> bool:
    """The bench's P8.a + P8.b in one: <=2 sentences, <=14 words, no question, no hook, a farewell word."""
    return (len(cg._sentences(reply)) <= 2 and cg._words(reply) <= 14 and "?" not in reply
            and not cg._HOOK_RX.search(reply) and not cg._LEAK_RX.search(reply) and cg._has_farewell_word(reply))


def _run(coro):
    return asyncio.run(coro)


async def _stream(*chunks):
    for c in chunks:
        yield c


def _collect(agen):
    async def go():
        return [d async for d in agen]
    return _run(go())


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv(cg.ENV, raising=False)


# -- which turns ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text", FAREWELLS + ["Bye!", "see you tomorrow", "Goodnight", "ttyl", "thanks, bye for now",
                                              "Thanks Zoe, that's all for now.", "zoe goodnight zoe"])
def test_a_whole_utterance_goodbye_is_a_farewell(text):
    assert cg.classify(text) == "farewell"


@pytest.mark.parametrize("text", [
    "turn off the lights, goodnight", "I need to go to the shop and buy milk", "bye the way what time is it", "what time is it",
    "good morning", "thanks", "I've got to go to the dentist on Friday", "remind me tomorrow", "Is it night yet?",
    "is it gonna be nice out later", "later", "remind me later", "talk", "tonight",
])
def test_a_goodbye_that_also_asks_for_something_is_not_a_farewell(text):
    assert cg.classify(text) != "farewell"


@pytest.mark.parametrize("text", ["Zoe, are you still there?", "Hello, can you hear me?", "Are you there?", "Zoe?", "Anyone home?",
                                  "you there", "can you hear me"])
def test_presence_checks(text):
    assert cg.classify(text) == "presence"


@pytest.mark.parametrize("text", ["...", "ok", "hmm", ".", "mm", "Okay.", "uh"])
def test_content_free_turns(text):
    assert cg.classify(text) == "silence"


@pytest.mark.parametrize("text", ["yes", "no", "Zoe", "hello", "what's up", "are you free on Friday?", "I'm tired", "thank you"])
def test_ordinary_turns_are_not_these(text):
    assert cg.classify(text) == ""


# -- the farewell reply ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,reply", list(zip(FAREWELLS, BASELINE_REPLIES)))
def test_every_baseline_farewell_reply_comes_out_clean(message, reply):
    out = cg.clean("farewell", reply, message)
    assert _clean_form(out), (reply, out)


def test_the_hooks_the_baseline_produced_are_gone():
    cases = {
        "Good evening. How can I help you settle in for the night?": "Night. Sleep well.",
        "Good evening! Hope your day was wonderful. See you tomorrow for your 8 AM meeting!": "Night. Sleep well.",
        "I'll be here when you get back. Take care.": "Take care.",
        "Have a good day at work, I'll be here.": "Have a good day at work.",
        "You're welcome. Feel free to reach out whenever you need anything else.": "Anytime. Take care.",
    }
    msgs = {"Good evening. How can I help you settle in for the night?": "night Zoe",
            "Good evening! Hope your day was wonderful. See you tomorrow for your 8 AM meeting!": "good night Zoe",
            "I'll be here when you get back. Take care.": "gotta run, see you",
            "Have a good day at work, I'll be here.": "I'm off to work, bye",
            "You're welcome. Feel free to reach out whenever you need anything else.": "ok that's all for now, thanks"}
    for reply, want in cases.items():
        assert cg.clean("farewell", reply, msgs[reply]) == want


@pytest.mark.parametrize("reply", ["Night. Sleep well.", "Okay, talk soon.", "Bye. See you tomorrow.", "Take care.",
                                   "Have a good day.", "Talk to you later."])
def test_a_clean_goodbye_is_returned_unchanged(reply):
    assert cg.clean("farewell", reply, "bye") == reply


@pytest.mark.parametrize("hooky", [
    "Before you go, don't forget your 9 AM meeting!", "Already? I was enjoying this. Bye.", "I'll miss you. Goodnight.",
    "Wait, one more thing before you leave! Night.", "Sleep well, and let me know if you need anything.",
    "Goodnight! Is there anything else I can do for you before bed?",
])
def test_the_hook_stub_policy_is_cleaned(hooky):
    out = cg.clean("farewell", hooky, "good night")
    assert _clean_form(out) and "?" not in out and "miss" not in out.lower() and "wait" not in out.lower()


def test_never_more_than_two_short_sentences():
    out = cg.clean("farewell", "Goodnight. Sleep well. See you in the morning. Take care now. Bye for now.", "night")
    assert len(cg._sentences(out)) <= 2 and cg._words(out) <= 14


# -- presence and silence ------------------------------------------------------------------------

@pytest.mark.parametrize("reply,want", [
    ("I don't have a way to check if anyone is home for you.", "Yes, I'm here."),
    ("I don't have any information about who might be home.", "Yes, I'm here."),
    ("I can hear you just fine. How can I help you today?", "I can hear you just fine."),
    ("I can hear you loud and clear. How are you doing today?", "I can hear you loud and clear."),
    ("Listening.", "Listening."), ("Still here.", "Still here."), ("I'm here.", "I'm here."), ("Ready when you are.", "Ready when you are."),
])
def test_are_you_there_is_answered_plainly(reply, want):
    assert cg.clean("presence", reply, "Are you there?") == want


@pytest.mark.parametrize("reply", [
    "It seems like you might have trailed off. Is there something on your mind, or something you'd like to talk about?",
    "You sound a little thoughtful right now. Is there anything on your mind you want to talk about?",
    "Hey there, what's on your mind today?", "You're just saying \"mm\"? What's on your mind right now?",
    "Hey there. Is there anything on your mind today?", "You seem a little thoughtful right now.",
    "Are you still there?", "Everything okay?",
])
def test_silence_is_never_remarked_on(reply):
    assert cg.clean("silence", reply, "hmm") == "Okay."
    assert cg.clean("silence", reply, "...") == "I'm here."


@pytest.mark.parametrize("reply", ["Okay.", "Sure thing.", "Take your time.", "Added to your list.", "Which list do you want it on?"])
def test_a_reply_that_is_not_a_probe_passes(reply):
    """'ok' may be a yes to something Zoe offered: an answer, a result or a task question is never rewritten."""
    assert cg.clean("silence", reply, "ok") == reply


# -- the stream ------------------------------------------------------------------------------------

def test_enforce_rewrites_only_a_farewell_turn_and_holds_it_as_one_delta(monkeypatch):
    monkeypatch.setenv(cg.ENV, "enforce")
    out = _collect(cg.filter_stream(_stream("__TOOL__:x", "Good evening. ", "How can I help you settle in for the night?"), "night Zoe"))
    assert out == ["__TOOL__:x", "Night. Sleep well."]


def test_every_other_turn_streams_byte_identical_and_unbuffered(monkeypatch):
    monkeypatch.setenv(cg.ENV, "enforce")
    chunks = ("Good evening. ", "How can I help?", " More.")
    assert _collect(cg.filter_stream(_stream(*chunks), "what's the weather")) == list(chunks)


def test_shadow_and_off_change_nothing(monkeypatch):
    for mode in ("shadow", "off", ""):
        if mode:
            monkeypatch.setenv(cg.ENV, mode)
        assert _collect(cg.filter_stream(_stream("Good evening. How can I help?"), "night Zoe")) == ["Good evening. How can I help?"]


def test_a_question_zoe_owes_is_not_a_hook(monkeypatch):
    """S16 of the bar: 'Thanks Zoe, that's all for now.' is exactly where the pending contact offer is voiced."""
    monkeypatch.setenv(cg.ENV, "enforce")
    q = "Would you like me to add Dana and Mika to your contacts?"
    raw = f"You're welcome! {q}"
    out = _collect(cg.filter_stream(_stream(raw), "Thanks Zoe, that's all for now.", owed=lambda: [q]))
    assert out == [raw]
    out = _collect(cg.filter_stream(_stream(raw), "Thanks Zoe, that's all for now.", owed=lambda: []))
    assert out != [raw] and "?" not in out[0]                       # without the debt it is cleaned as any hook
    assert cg.voices_owed_question("Sure thing. WOULD you like me to add Dana and Mika to your contacts", [q])
    assert not cg.voices_owed_question("Bye.", [q]) and not cg.voices_owed_question("anything", [])


def test_the_default_is_shadow():
    assert cg.mode() == "shadow"


def test_the_error_fallback_is_never_rewritten(monkeypatch):
    monkeypatch.setenv(cg.ENV, "enforce")
    boom = "Sorry, I had trouble reaching my brain just now. Could you try again?"
    assert _collect(cg.filter_stream(_stream(boom), "night Zoe", passthrough=(boom,))) == [boom]


def test_closing_the_guard_closes_the_inner_turn(monkeypatch):
    monkeypatch.setenv(cg.ENV, "enforce")
    closed = []

    async def inner():
        try:
            yield "Night."
            yield "more"
        finally:
            closed.append(True)

    async def go():
        g = cg.filter_stream(inner(), "ok")
        await g.aclose()
    _run(go())
    # an unconsumed guard never started the inner generator: nothing to leak; consume + close path below
    async def go2():
        g = cg.filter_stream(inner(), "ok")
        async for _ in g:
            break
        await g.aclose()
    _run(go2())
    assert closed


def test_the_seam_wraps_the_turn_in_the_guard():
    import inspect

    import zoe_flue_client as zc

    src = inspect.getsource(zc.run_flue_brain_streaming)
    assert "clean_goodbye.filter_stream(turn, message" in src
