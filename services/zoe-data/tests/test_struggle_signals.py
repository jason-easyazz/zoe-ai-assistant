"""The struggle detector's SIGNALS (``struggle_signals.py``): >= 10 invented positives and >= 10 invented HARD negatives per
signal, the multi-turn scenarios, and in-suite negative controls (break one guard -> a hard negative fires).

Every phrasing here is invented for the test. None is copied from a household transcript or log (public repo).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # pure functions; no DB, no network, no model

import struggle_signals as s

A = "member-a"


def T(text, *, ts=0.0, reply="Done.", dom="", path=s.PATH_FAST, ch="voice", user=A, score=0.85, tsd=None, tsf=False,
      tsa=True, conf=0.95, interrupted=False, conv="voice:hall"):
    """A turn. ``dom`` = the router's domain; by default the two-stage head decided it (``tsd`` = dom)."""
    if tsd is None:
        tsd = dom if (tsa and not tsf) else ""
    return s.Turn(ts=ts, channel=ch, user_id=user, is_guest=(user == "guest"), conv_key=conv, text=text, reply=reply,
                  path=path, predicted_domain=dom, predicted_score=score, two_stage_domain=tsd, two_stage_conf=conf,
                  two_stage_active=tsa, two_stage_failed=tsf, interrupted=interrupted)


def sigs(prev, cur):
    return [e.signal for e in s.detect(prev, cur)]


def pair(a, b, *, gap=10.0, prev_kw=None, **kw):
    return sigs(T(a, ts=0.0, **(prev_kw or {})), T(b, ts=gap, **kw))


# ── RE_ASK ───────────────────────────────────────────────────────────────────

RE_ASK_POS = [
    ("what's on the shopping list", "what's on the shopping list", {}),
    ("What's the time?", "what is the time", {}),
    ("turn on the porch lights", "turn on the porch light", {}),
    ("set a timer for ten minutes", "set a timer for 10 minutes", {}),
    ("hey zoe, when does the library open", "when does the library open", {}),
    ("um add bread to the list", "add bread to the list", {}),
    ("whats teh forecast for saturday", "whats the forecast for saturday", {"ch": "chat", "conv": "chat:s1"}),
    ("please play some quiet piano music", "play some quiet piano music", {}),
    ("remind me to water the ferns at six", "remind me to water the ferns at 6", {}),
    ("how many teaspoons are in a tablespoon", "how many teaspoons are in a tablespoon?", {}),
    ("what's the capital of peru", "what is the capital of Peru", {"ch": "chat", "conv": "chat:s1"}),
    ("show me the recycling day", "Show me the recycling day.", {}),
]
RE_ASK_NEG = [
    ("next song", "next song", {}),                                   # repeatable
    ("volume up", "volume up", {}),
    ("louder", "louder", {}),
    ("yes", "yes", {}),
    ("what's the weather today", "will it rain tomorrow", {}),        # two genuinely different weather questions
    ("set a timer for 10 minutes", "set a timer for 15 minutes", {}),  # changed number
    ("turn the porch light off", "turn the porch light on", {}),       # polarity
    ("add milk to the list", "add eggs to the list", {}),              # a different item
    ("what's the weather in Oslo", "what's the weather in Lima", {}),  # a different place
    ("skip", "skip", {}),
    ("good night", "good night", {}),
    ("what's on the shopping list", "what's on the shopping list", {"gap": 120.0}),   # outside the window
]


@pytest.mark.parametrize("a,b,kw", RE_ASK_POS)
def test_re_ask_positive(a, b, kw):
    gap = kw.pop("gap", 12.0)
    pk = {k: v for k, v in kw.items() if k in ("ch", "conv")}
    assert "RE_ASK" in pair(a, b, gap=gap, prev_kw=pk, **kw), (a, b)


@pytest.mark.parametrize("a,b,kw", RE_ASK_NEG)
def test_re_ask_hard_negative(a, b, kw):
    kw = dict(kw)
    gap = kw.pop("gap", 12.0)
    got = pair(a, b, gap=gap, **kw)
    assert "RE_ASK" not in got and "MISHEAR" not in got and "REPHRASE" not in got, (a, b, got)


def test_re_ask_needs_same_user_in_chat_and_same_conversation():
    a = T("what's on the shopping list", ch="chat", conv="chat:s1")
    assert sigs(a, T("what's on the shopping list", ts=5, ch="chat", conv="chat:s1", user="member-b")) == []
    assert sigs(a, T("what's on the shopping list", ts=5, ch="chat", conv="chat:s2")) == []


# ── REPHRASE ─────────────────────────────────────────────────────────────────

REPHRASE_POS = [
    ("what is my email address", "what is my work email address", "memory"),
    ("what's on the shopping list", "read me the shopping list", "lists"),
    ("what's on my calendar today", "what do I have on today", "calendar"),
    ("what time is it", "what's the time", "time"),
    ("how long is left on the timer", "how much time is left on my timer", "timers"),
    ("is it going to rain today", "will it rain today", "weather"),
    ("what's the capital of peru", "tell me the capital of peru", ""),
    ("who wrote the hobbit", "tell me who wrote the hobbit book", ""),
    ("what's the forecast going to be today", "how warm is it going to get today", "weather"),
    ("when is the bin collection", "when is the next bin collection", "calendar"),
    ("play something relaxing", "play something relaxing and slow", "music"),
]
REPHRASE_NEG = [
    ("what's the weather today", "what about tomorrow", "weather"),         # follow-up opener + new anchor
    ("what's the weather today", "will it rain today", "weather"),          # different question, same anchor
    ("what is my full name", "what is my birthday", "memory"),              # different fact
    ("when is my next appointment", "when is my next appointment this week", "calendar"),  # added time slot
    ("what's on my calendar today", "and tomorrow", "calendar"),
    ("add milk to the shopping list", "add eggs to the shopping list", "lists"),
    ("what's playing", "what song is this", "music"),                       # no shared words, no anchor
    ("how are the tides today", "what's the weather like", "weather"),
    ("set a timer for pasta", "set another one for the rice", "timers"),
    ("what's the temperature outside", "do I need a jacket", "weather"),
    ("turn on the hall light", "turn on the hall light too", "smart_home"),
]


@pytest.mark.parametrize("a,b,dom", REPHRASE_POS)
def test_rephrase_positive(a, b, dom):
    got = pair(a, b, gap=15.0, prev_kw={"dom": dom}, dom=dom)
    assert "REPHRASE" in got or "RE_ASK" in got, (a, b, got)


@pytest.mark.parametrize("a,b,dom", REPHRASE_NEG)
def test_rephrase_hard_negative(a, b, dom):
    got = pair(a, b, gap=15.0, prev_kw={"dom": dom}, dom=dom)
    assert not {"REPHRASE", "RE_ASK", "MISHEAR"} & set(got), (a, b, got)


def test_rephrase_needs_the_same_domain():
    assert "REPHRASE" not in pair("what's the forecast going to be today", "how warm is it going to get today",
                                  prev_kw={"dom": "weather"}, dom="calendar")


# ── CORRECTION ───────────────────────────────────────────────────────────────

CORRECTION_POS = [
    "no, I said the kitchen",
    "I meant tomorrow",
    "no, I asked for the weekend forecast",
    "no no, the other one",
    "wrong room",
    "you misheard me, I said ten",
    "not that one, the blue one",
    "actually I said Friday",
    "no, the bedroom light",
    "I didn't say that",
    "nope, I asked about the weekend",
    "Hmm. I was asking about the bus times.",
]
CORRECTION_NEG = [
    ("no worries, thanks", "Done."),
    ("no thanks", "Done."),
    ("no, that's all", "Done."),
    ("no", "Do you want me to add it to the list?"),                      # an ANSWER to Zoe's question
    ("no, the meeting went well", "How did the meeting go?"),             # an answer, not a repair
    ("no, I'm good", "Done."),
    ("not now", "Done."),
    ("no problem", "Done."),
    ("nope, that's it", "Done."),
    ("I meant to tell you about my day", "Done."),
    ("never mind", "Done."),
    ("wrong number called me again today", "Done."),
    ("no more coffee for me today", "Done."),
    ("no, it's fine", "Done."),                                            # these three only the not-a-correction list
    ("nope, it's all good", "Done."),                                      # keeps quiet (the negative control below)
    ("no, it is okay, leave it", "Done."),
]


@pytest.mark.parametrize("b", CORRECTION_POS)
def test_correction_positive(b):
    assert "CORRECTION" in pair("turn on the light", b, prev_kw={"reply": "Turned the lamp on."}), b


@pytest.mark.parametrize("b,prev_reply", CORRECTION_NEG)
def test_correction_hard_negative(b, prev_reply):
    assert "CORRECTION" not in pair("what's next", b, prev_kw={"reply": prev_reply}), b


def test_correction_needs_a_reply_to_correct():
    assert "CORRECTION" not in pair("turn on the light", "no, I said the kitchen", prev_kw={"reply": ""})


# ── NEGATIVE_FEEDBACK ────────────────────────────────────────────────────────

NEG_FB_POS = [
    "that was wrong", "that's not right", "wrong answer", "bad answer", "you got that wrong",
    "that's not what I meant", "that's wrong, it's Thursday", "you're not listening", "that doesn't make sense",
    "you made a mistake", "that wasn't helpful", "zoe, that was wrong",
]
NEG_FB_NEG = [
    "good answer", "that was helpful", "is that right?", "what's wrong with the dishwasher", "the clock is wrong",
    "I was wrong about the date", "nothing wrong here", "you made my day", "that doesn't matter",
    "it's not right for me to say", "that makes sense", "perfect, thanks",
]


@pytest.mark.parametrize("b", NEG_FB_POS)
def test_negative_feedback_positive(b):
    assert "NEGATIVE_FEEDBACK" in pair("what day is it", b, prev_kw={"reply": "It's Wednesday."}), b


@pytest.mark.parametrize("b", NEG_FB_NEG)
def test_negative_feedback_hard_negative(b):
    assert "NEGATIVE_FEEDBACK" not in pair("what day is it", b, prev_kw={"reply": "It's Wednesday."}), b


def test_negative_feedback_suppresses_a_duplicate_correction():
    got = pair("what day is it", "no, that's wrong, it's Thursday", prev_kw={"reply": "It's Wednesday."})
    assert got.count("NEGATIVE_FEEDBACK") == 1 and "CORRECTION" not in got


# ── CANT_DO ──────────────────────────────────────────────────────────────────

CANT_DO_POS = [
    ("Sorry, I can't access your calendar right now.", "calendar"),
    ("I don't have access to your lists.", "lists"),
    ("I couldn't find a timer called pasta.", "timers"),
    ("I'm unable to control that device.", "smart_home"),
    ("Sorry, I had trouble reaching my brain just now. Could you try again?", ""),   # the degraded fallback = ERROR
    ("Sorry, something went wrong. Please try again.", ""),
    ("I can't set reminders like that yet.", "reminders"),
    ("I don't know how to do that.", "music"),
    ("I'm having trouble connecting to the weather service.", "weather"),
    ("That isn't something I can do yet.", "smart_home"),
    ("I didn't catch that.", "time"),
    ("I don't have that on file.", "memory"),
]
CANT_DO_NEG = [
    ("I can't wait to hear about it!", "calendar"),
    ("Sorry, I can't share that with a guest. Please sign in.", "calendar"),   # policy, the right answer
    ("I don't know about you, but I love Fridays.", "time"),
    ("You have no events today.", "calendar"),
    ("It is 7:15 PM.", "time"),
    ("I can't order food for you.", ""),                                         # not a domain Zoe supports
    ("I can't believe it's already autumn!", "weather"),
    ("I need your PIN to verify it's you.", "calendar"),
    ("I'm not sure, but it might rain later.", "weather"),
    ("Done, I've added it.", "lists"),
    ("There's nothing on your list yet.", "lists"),
    ("", "weather"),
]


@pytest.mark.parametrize("reply,dom", CANT_DO_POS)
def test_cant_do_positive(reply, dom):
    assert "CANT_DO" in sigs(None, T("can you do the thing", reply=reply, dom=dom)), reply


@pytest.mark.parametrize("reply,dom", CANT_DO_NEG)
def test_cant_do_hard_negative(reply, dom):
    assert "CANT_DO" not in sigs(None, T("can you do the thing", reply=reply, dom=dom)), reply


def test_cant_do_mirrors_the_replay_gate_regex():
    """The live detector and the replay gate's CANT_DO verdict must agree on the gate's own phrases."""
    for reply in ("I don't have access to that", "I can't find it", "I couldn't schedule it", "I am unable to",
                  "you'll have to check that yourself"):
        assert s.cant_do(reply) == "cant_do", reply


# ── FALLTHROUGH ──────────────────────────────────────────────────────────────

FALL_POS = [
    dict(dom="weather", path=s.PATH_BRAIN, score=0.82),
    dict(dom="timers", path=s.PATH_BRAIN, score=0.9),
    dict(dom="calendar", path=s.PATH_BRAIN, score=0.75),
    dict(dom="lists", path=s.PATH_BRAIN, score=0.71),
    dict(dom="music", path=s.PATH_BRAIN, score=0.0, conf=0.95, text="put some jazz on"),   # no similarity examples: the head alone, sure, and a music cue
    dict(dom="smart_home", path=s.PATH_BRAIN, score=0.0, conf=0.92, text="dim the lounge light"),
    dict(dom="weather", path=s.PATH_BRAIN, score=0.70, tsf=True),        # two-stage timed out, similarity routed
    dict(dom="calendar", path=s.PATH_BRAIN, score=0.85, tsa=False),      # no head at all, similarity strong
    dict(dom="timers", path=s.PATH_FAST, score=0.86, reply="Here is the calendar."),   # another fast domain answered
    dict(dom="weather", path=s.PATH_FAST, score=0.9, reply="It is 7:05 PM."),
    dict(dom="smart_home", path=s.PATH_FAST, score=0.0, conf=0.97, reply="Here is the calendar.", text="dim the lounge light"),
    dict(dom="reminders", path=s.PATH_BRAIN, score=0.78),
]
FALL_NEG = [
    dict(dom="chat", path=s.PATH_BRAIN, score=0.9),
    dict(dom="weather", path=s.PATH_BRAIN, score=0.5),                    # one witness only
    dict(dom="memory", path=s.PATH_BRAIN, score=0.9),                     # deliberately brain-served
    dict(dom="weather", path=s.PATH_FAST, score=0.9, reply="It's 18 degrees and cloudy."),
    dict(dom="weather", path=s.PATH_BRAIN, score=0.55, tsf=True),
    dict(dom="calendar", path=s.PATH_BRAIN, score=0.7, tsa=False),        # similarity alone, not strong
    dict(dom="smart_home", path=s.PATH_BRAIN, score=0.0, conf=0.6),
    dict(dom="lists", path=s.PATH_FAST, score=0.9, reply="Okay."),        # served by something we cannot tell
    dict(dom="timers", path=s.PATH_FAST, score=0.9, reply="Your timer is set for 5 minutes."),
    dict(dom="calendar", path=s.PATH_BRAIN, score=0.65),
    dict(dom="people", path=s.PATH_BRAIN, score=0.95),
    dict(dom="", path=s.PATH_BRAIN, score=None),
]


@pytest.mark.parametrize("kw", FALL_POS)
def test_fallthrough_positive(kw):
    kw = dict(kw)
    reply = kw.pop("reply", "Sure.")
    text = kw.pop("text", "a request")
    assert "FALLTHROUGH" in sigs(None, T(text, reply=reply, **kw)), kw


@pytest.mark.parametrize("kw", FALL_NEG)
def test_fallthrough_hard_negative(kw):
    kw = dict(kw)
    reply = kw.pop("reply", "Sure.")
    assert "FALLTHROUGH" not in sigs(None, T("a request", reply=reply, **kw)), kw


def test_fallthrough_names_a_router_timeout_as_a_latency_fix():
    ev = [e for e in s.detect(None, T("x", dom="weather", path=s.PATH_BRAIN, score=0.7, tsf=True))
          if e.signal == "FALLTHROUGH"][0]
    assert ev.fix_type == s.FIX_ROUTER_LATENCY and "two_stage_failed" in ev.reasons


# ── VERBOSE ──────────────────────────────────────────────────────────────────

LONG = "word " * 120   # 600 chars
VERBOSE_POS = [
    ("what time is it", "time", "voice", 300),
    ("what's the weather today", "weather", "voice", 450),
    ("is it raining", "", "voice", 400),
    ("what's the capital of france", "", "chat", 900),
    ("how old is the eiffel tower", "", "voice", 500),
    ("when is my next appointment", "calendar", "voice", 250),
    ("how many grams in a pound", "", "voice", 320),
    ("who is the prime minister", "", "voice", 600),
    ("what's on my list", "lists", "voice", 260),
    ("is the timer still running", "timers", "voice", 230),
    ("tell me the time", "time", "voice", 420),
]
VERBOSE_NEG = [
    ("tell me a story", "", "voice", 2000),
    ("explain how tides work", "", "voice", 900),
    ("why is the sky blue", "", "voice", 600),
    ("what time is it", "time", "voice", 40),
    ("what's the capital of france", "", "chat", 400),
    ("can you tell me everything that happened in the news today in a lot of detail please", "", "voice", 900),
    ("what do you think about jazz", "", "voice", 500),
    ("tell me about the moon", "", "voice", 700),
    ("give me a recipe for pancakes", "", "voice", 900),
    ("summarise my day", "calendar", "voice", 500),
    ("I had a really long day at work", "", "voice", 400),
]


@pytest.mark.parametrize("text,dom,ch,n", VERBOSE_POS)
def test_verbose_positive(text, dom, ch, n):
    assert "VERBOSE" in sigs(None, T(text, reply=(LONG * 4)[:n], dom=dom, ch=ch, conv=f"{ch}:x")), text


@pytest.mark.parametrize("text,dom,ch,n", VERBOSE_NEG)
def test_verbose_hard_negative(text, dom, ch, n):
    assert "VERBOSE" not in sigs(None, T(text, reply=(LONG * 4)[:n], dom=dom, ch=ch, conv=f"{ch}:x")), text


# ── MISHEAR ──────────────────────────────────────────────────────────────────

WAKE_ONLY = ["hey zoe", "Zoey.", "ok zoe", "A zoey.", "Hey, Zoe.", "hi zoe!"]
MISHEAR_PAIRS = [
    ("set a time for five minutes", "set a timer for five minutes"),
    ("what's the whether today", "what's the weather today"),
    ("turn on the kitchen lice", "turn on the kitchen lights"),
    ("add bred to the list", "add bread to the list"),
    ("play some jars music", "play some jazz music"),
    ("how cold is it going to bet tonight", "how cold is it going to get tonight"),
    ("Had milk to my list please", "add milk to my list please"),
    ("Hazy, what time is it", "what time is it"),
]
MISHEAR_NEG = [
    ("hey zoe what time is it", None),                                   # a wake word WITH a request
    ("add milk to the list", "add eggs to the list"),
    ("turn on the light", "turn off the light"),
    ("what's the weather in Oslo", "what's the weather in Lima"),
    ("set a timer for five minutes", "set a timer for nine minutes"),
    ("play some jazz", "play some rock"),
    ("call the office", "call the clinic"),
    ("zoe is great", None),
    ("set a time for five minutes", "set a timer for five minutes", 40.0),   # too late for a corrected repeat
    ("open the blinds", "close the blinds"),
    ("what's the whether today", "what's the weather today", 10.0, "chat"),  # typed: a typo fix, not a mishearing
]


@pytest.mark.parametrize("text", WAKE_ONLY)
def test_mishear_wake_only(text):
    assert sigs(None, T(text, path=s.PATH_BRAIN)) == ["MISHEAR"], text


@pytest.mark.parametrize("a,b", MISHEAR_PAIRS)
def test_mishear_corrected_repeat(a, b):
    assert "MISHEAR" in pair(a, b, gap=12.0), (a, b)


@pytest.mark.parametrize("case", MISHEAR_NEG)
def test_mishear_hard_negative(case):
    a, b, *rest = case
    gap = rest[0] if rest else 12.0
    ch = rest[1] if len(rest) > 1 else "voice"
    got = sigs(None, T(a, ch=ch)) if b is None else pair(a, b, gap=gap, prev_kw={"ch": ch}, ch=ch)
    assert "MISHEAR" not in got, (a, b, got)


# ── ABANDON ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("reply", ["The first part of a long answer", "x" * 300, "One. Two. Three.", "Sure, so the",
                                   "Well, it depends", "Here is", "a", "Long " * 80, "Okay so first", "Right."])
def test_abandon_positive(reply):
    assert "ABANDON" in sigs(None, T("tell me something", reply=reply, interrupted=True)), reply


@pytest.mark.parametrize("reply,interrupted", [("Full answer.", False), ("", True), ("   ", True)] +
                         [(f"answer {i}", False) for i in range(8)])
def test_abandon_hard_negative(reply, interrupted):
    assert "ABANDON" not in sigs(None, T("tell me something", reply=reply, interrupted=interrupted))


# ── scenarios (multi-turn, invented) ─────────────────────────────────────────

def _run(turns):
    out, prev = [], None
    for t in turns:
        out.append([(e.signal, e.about is prev) for e in s.detect(prev, t)])
        prev = t
    return out


def test_scenario_forecast_answered_with_current_conditions():
    """A forecast question answered with the CURRENT reading, a rephrase asking for the day's high that the router timed
    out on and the brain answered, then a misheard repeat corrected by the user."""
    turns = [
        T("hey zoe, what's it going to be like outside today", ts=0, dom="weather", reply="It's 17 degrees and sunny."),
        T("how warm is it going to get today", ts=14, dom="weather", path=s.PATH_BRAIN, score=0.7, tsf=True,
          reply="It should reach about 24."),
        T("how warn is it going to get today", ts=28, dom="weather", path=s.PATH_BRAIN, score=0.72, tsf=True,
          reply="About 24 this afternoon."),
    ]
    got = _run(turns)
    assert got[0] == []
    assert ("FALLTHROUGH", False) in got[1] and ("REPHRASE", True) in got[1]
    assert ("FALLTHROUGH", False) in got[2] and ("MISHEAR", True) in got[2]
    ev = [e for e in s.detect(turns[0], turns[1]) if e.signal == "REPHRASE"][0]
    assert ev.pattern == "weather|what:future:today" and ev.fix_type == s.FIX_CAPABILITY_GAP


def test_scenario_a_good_exchange_is_quiet():
    turns = [
        T("what time is it", ts=0, dom="time", reply="It's 8:05."),
        T("thanks", ts=6, reply="You're welcome."),
        T("what's the weather like", ts=20, dom="weather", reply="It's 15 degrees and clear."),
        T("set a timer for ten minutes", ts=40, dom="timers", reply="Timer set for 10 minutes."),
        T("and another for twenty minutes", ts=55, dom="timers", reply="Timer set for 20 minutes."),
        T("tell me a joke", ts=70, path=s.PATH_BRAIN, dom="chat", reply="Why did the cat sit on the computer? " * 3),
    ]
    assert _run(turns) == [[]] * len(turns)


def test_scenario_list_add_misheard_then_corrected():
    turns = [
        T("add bred to the shopping list", ts=0, dom="lists", reply="Added bred to the shopping list."),
        T("no, I said bread", ts=8, dom="lists", reply="Fixed it."),
    ]
    assert _run(turns)[1] == [("CORRECTION", True)]


def test_scenario_brain_says_cant_then_user_rephrases():
    turns = [
        T("whats on for tomorrow", ts=0, dom="calendar", path=s.PATH_BRAIN, score=0.8,
          reply="Sorry, I can't access your calendar right now."),
        T("what is on my calendar for tomorrow", ts=12, dom="calendar", reply="You have 2 events tomorrow."),
    ]
    got = _run(turns)
    assert ("CANT_DO", False) in got[0] and ("FALLTHROUGH", False) in got[0]
    assert ("REPHRASE", True) in got[1] or ("RE_ASK", True) in got[1]


def test_scenario_identity_flip_on_one_panel_still_pairs_voice_turns():
    a = T("set a time for five minutes", ts=0, user=A)
    b = T("set a timer for five minutes", ts=9, user="guest")
    assert [e.signal for e in s.detect(a, b)] == ["MISHEAR"]


def test_events_carry_the_failing_turns_domain_and_path():
    a = T("how long is left on the timer", ts=0, dom="timers", path=s.PATH_BRAIN, score=0.9, reply="Hmm.")
    b = T("how much time is left on my timer", ts=10, dom="timers")
    ev = [e for e in s.detect(a, b) if e.signal == "REPHRASE"][0]
    assert ev.about is a and ev.turn is b and ev.fix_type == s.FIX_ROUTER_TRAINING


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_shape_is_text_free():
    for text in ("what's the weather going to be like today", "set a timer for 10 minutes", "Remind Alex about the keys"):
        shp = s.shape(text)
        lead, orient, anchor = shp.split(":")
        assert orient in ("future", "now", "past", "none")
        assert anchor in s._ANCHORS or anchor == "none"
        assert lead in s._WH or lead in ("ask", "stmt", "empty") or lead[4:] in s._CMD_VERBS
    assert "alex" not in s.shape("Remind Alex about the keys").lower()


def test_reply_domain():
    assert s.reply_domain("Here is the calendar.") == "calendar"
    assert s.reply_domain("It is 6:47 AM.") == "time"
    assert s.reply_domain("It's 19 degrees and clear.") == "weather"
    assert s.reply_domain("Turned the hall lamp off.") == "smart_home"
    assert s.reply_domain("Sure thing.") == ""


# ── negative controls: break one guard, a hard negative must fire ────────────
# Each control proves the guard - not luck - keeps a hard negative quiet. If a control ever passes without the patch, the
# guard is dead weight; if a hard-negative test above ever passes WITH the patch, the guard is not what protects it.

def test_control_window_check_is_what_stops_a_late_re_ask(monkeypatch):
    late = ("what's on the shopping list", "what's on the shopping list")
    assert "RE_ASK" not in pair(*late, gap=120.0)
    monkeypatch.setattr(s, "DEFAULT_WINDOW_S", 1e9)
    real = s.detect
    monkeypatch.setattr(s, "detect", lambda p, c, window_s=1e9: real(p, c, window_s=1e9))
    assert "RE_ASK" in pair(*late, gap=120.0)


def test_control_slot_guard_is_what_separates_two_weather_questions(monkeypatch):
    a, b = "what's the weather in Oslo", "what's the weather in Lima"
    assert not {"RE_ASK", "MISHEAR", "REPHRASE"} & set(pair(a, b, prev_kw={"dom": "weather"}, dom="weather"))
    monkeypatch.setattr(s, "_new_slots", lambda *_a: set())
    monkeypatch.setattr(s, "sound_alike", lambda *_a: True)
    assert {"RE_ASK", "MISHEAR", "REPHRASE"} & set(pair(a, b, prev_kw={"dom": "weather"}, dom="weather"))


def test_control_not_a_correction_list_is_what_spares_no_worries(monkeypatch):
    assert "CORRECTION" not in pair("what's next", "no, it's fine", prev_kw={"reply": "Done."})
    import re as _re
    monkeypatch.setattr(s, "_NOT_A_CORRECTION", _re.compile(r"(?!)"))
    assert "CORRECTION" in pair("what's next", "no, it's fine", prev_kw={"reply": "Done."})


def test_control_question_guard_is_what_spares_an_answered_no(monkeypatch):
    kw = {"prev_kw": {"reply": "Should I add it to the list?"}}
    assert "CORRECTION" not in pair("buy milk", "no, the list is fine as it is", **kw)
    monkeypatch.setattr(s, "_NO_PLUS_CONTENT", __import__("re").compile(r"^no\b"))
    real = s.correction_cue
    monkeypatch.setattr(s, "correction_cue", lambda _r, t: real("", t))
    assert "CORRECTION" in pair("buy milk", "no, the list is fine as it is", **kw)


def test_control_two_witness_rule_is_what_stops_a_weak_fallthrough(monkeypatch):
    weak = T("a statement", dom="lists", path=s.PATH_BRAIN, score=0.6)
    assert "FALLTHROUGH" not in sigs(None, weak)
    monkeypatch.setattr(s, "FALLTHROUGH_TWO_STAGE_SIM", 0.0)
    assert "FALLTHROUGH" in sigs(None, weak)


def test_control_open_ended_guard_is_what_spares_a_story(monkeypatch):
    story = T("tell me a story", reply="x" * 2000)
    assert "VERBOSE" not in sigs(None, story)
    import re as _re
    monkeypatch.setattr(s, "_OPEN_ENDED", _re.compile(r"(?!)"))
    assert "VERBOSE" in sigs(None, story)


def test_control_policy_guard_is_what_spares_a_guest_refusal(monkeypatch):
    t = T("show my calendar", dom="calendar", reply="Sorry, I can't share that with a guest. Please sign in.")
    assert "CANT_DO" not in sigs(None, t)
    import re as _re
    monkeypatch.setattr(s, "_CANT_DO_POLICY", _re.compile(r"(?!)"))
    monkeypatch.setattr(s, "_CANT_DO_RE", _re.compile(r"can't share", _re.IGNORECASE))
    assert "CANT_DO" in sigs(None, t)


def test_control_repeatable_list_is_what_spares_next_song(monkeypatch):
    assert "RE_ASK" not in pair("next track", "next track")
    monkeypatch.setattr(s, "_repeatable", lambda _n: False)
    assert "RE_ASK" in pair("next track", "next track")
