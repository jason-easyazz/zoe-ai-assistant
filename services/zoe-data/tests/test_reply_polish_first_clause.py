"""ZOE_FIRST_SOUND_POLISH_EARLY: reply_polish releases a reply's first CLAUSE before its sentence closes, so the voice loop's
clause cut (ZOE_FIRST_SOUND_CLAUSE) can fire at all. Allowed only if the OUTPUT TEXT is unchanged: a clause already spoken
cannot be unsaid. The gate is the equivalence property, EXHAUSTIVE over split points: for every reply in the corpus, every
polish policy, and every chunking into one, two or three deltas (every cut position) plus one character at a time, the
joined stream output with the early release ON byte-equals ``polish(text)`` (and with it OFF, as before).

Also pinned: the must-hold cases (plumbing in the first clause, a stock opener, an opener that only starts like a stock line)
release nothing early; plumbing only in the remainder and a first-sentence question release the clause and still equal the
batch form; the chain narration -> mirror guard -> polish (early) -> the voice loop's first unit cuts the clause and speaks the
same words as with the flags off. Synthetic sentences only.
"""
from __future__ import annotations

import asyncio
import itertools

import pytest

import reply_polish as rp
import voice_first_sound as fs

pytestmark = pytest.mark.ci_safe

CORPUS = [
    "Honestly, I think the best thing you can do is rest, drink some water, and take a short walk later.",
    "The forecast for tomorrow looks mild, with a light breeze and no rain expected at all.",
    "Okay, I'd activate the calendar ability, and then check what is on tomorrow morning for you.",
    "Sure thing, you have three items on that list, and I can read them out if you like.",
    "That sounds like a lot to deal with right now, so let me use the calendar tool to find a free hour.",
    "I'm here to listen, whatever is on your mind right now.",
    "I am all ears, and that sounds really hard to carry by yourself this week.",
    "Did you mean the kitchen light, or the one in the hallway near the door?",
    "Your shopping list has eggs, milk, and bread on it. Is there anything else you need?",
    "It was a long day at work for you, and you handled it well. How are you feeling?",
    "On March 3, 2026, the clinic opens a little later than usual for the holiday.",
    "Your appointment is on Monday, March 3rd at ten in the morning.",
    "At 8:05, the first bus leaves from the corner stop near the park.",
    "The total came to about 3.5, which is a bit more than last week overall.",
    "Dr. Smith, the one from the clinic, called earlier about your results today.",
    "Here is what is on the list: eggs, milk, bread, and a bag of oranges.",
    "Sure.",
    "I would  probably wait a while, then use the reminders tool to set one for later.",
    "I'd and then maybe, if you want, I can use the timer tool for ten minutes.",
    "Well, okay then, I'll call show_calendar, and then read you the first event.",
    "First, a quick note for you; the meeting moved to the afternoon — later than planned.",
    "You have nothing on tomorrow, so it might be a good day to rest. Anything else I can help with?",
    "Feel free to share whatever is on your mind, and I will listen carefully.",
    "\"I am quite sure of it,\" the note says, \"that it will work out in the end.\"",
    " Leading space here, and a clause that is long enough to cut early.",
    "Activate the home ability. Okay, the kitchen light is now on, and the hallway one too.",
    "That's a really lovely idea, and I think your neighbour will appreciate it.\nAnything else?",
    "No problem at all, the timer is set for twenty minutes, starting right now. Does that make sense?",
    "Let's see, the weather on Saturday looks warm,\tand Sunday looks cooler with showers.",
    "Right, so the plan for the weekend is simple, just rest and use show_list later.",
]
POLICIES = [   # (previous reply ended on a question, strict cap, stock line rule)
    (False, False, False), (True, False, False), (True, True, True), (False, False, True),
]


def _stream(chunks, prev, strict, stock, early):
    p = rp.PolishStream(rp.closer_lexicon_for("hi"), prev, strict_cap=strict, stock=stock, early=early)
    outs = [p.feed(c) for c in chunks] + [p.finish()]
    return "".join(outs), p, outs


def _chunkings(text):
    """Every chunking into 1, 2 or 3 deltas (every cut position), plus one character at a time."""
    n = len(text)
    yield [text]
    for i in range(1, n):
        yield [text[:i], text[i:]]
    for i, j in itertools.combinations(range(1, n), 2):
        yield [text[:i], text[i:j], text[j:]]
    yield list(text)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("ZOE_REPLY_POLISH", "ZOE_FIRST_SOUND_CLAUSE", "ZOE_FIRST_SOUND_POLISH_EARLY",
              "ZOE_FIRST_SOUND_CLAUSE_MIN_CHARS", "ZOE_FIRST_SOUND_CLAUSE_MIN_WORDS"):
        monkeypatch.delenv(k, raising=False)
    rp.reset_state()
    yield
    rp.reset_state()


@pytest.mark.parametrize("reply", CORPUS)
def test_early_release_is_byte_identical_for_every_chunking(reply):
    for prev, strict, stock in POLICIES:
        want = rp.polish(reply, "hi", prev, strict_cap=strict, stock=stock)
        for chunks in _chunkings(reply):
            for early in (True, False):
                got, _p, _o = _stream(chunks, prev, strict, stock, early)
                assert got == want, (reply, (prev, strict, stock), chunks, early)


def test_the_corpus_exercises_the_early_release():
    """Not vacuous: most of the corpus releases a clause before its sentence closes (char by char)."""
    released = [r for r in CORPUS if _stream(list(r), True, True, True, True)[1].early_released]
    assert len(released) >= 12, released


def _first_early_chunk(reply, **kw):
    """Char by char with early on: the text released BEFORE the first sentence end arrived ('' when none)."""
    p = rp.PolishStream(rp.closer_lexicon_for("hi"), kw.get("prev", False), strict_cap=kw.get("strict", False),
                        stock=kw.get("stock", False), early=True)
    first_end = rp._next_end(reply)   # the sentence is complete only once the whitespace after its end arrives
    out = ""
    for ch in reply[: first_end if first_end >= 0 else len(reply)]:
        out += p.feed(ch)
    return out


@pytest.mark.parametrize("reply", [
    "Okay, I'd activate the calendar ability, and then check what is on tomorrow morning for you.",   # plumbing, first clause
    "Well, okay then, I'll call show_calendar, and then read you the first event.",                   # raw tool id, first clause
    "I'm here to listen, whatever is on your mind right now.",                                        # a stock opener
    "I am all ears, and that sounds really hard to carry by yourself this week.",                     # starts like a stock line
    "I would  probably wait a while, then use the reminders tool to set one for later.",              # the scrub would collapse
    "I'd and then maybe, if you want, I can use the timer tool for ten minutes.",                     # the scrub would rejoin
    "Let's see, the weather on Saturday looks warm,\tand Sunday looks cooler with showers.",          # a tab, not one space
])
def test_must_hold_cases_release_nothing_early(reply):
    assert _first_early_chunk(reply, prev=True, strict=True, stock=True) == ""
    assert _stream(list(reply), True, True, True, True)[1].early_released == 0


@pytest.mark.parametrize("reply,clause", [
    ("That sounds like a lot to deal with right now, so let me use the calendar tool to find a free hour.",
     "That sounds like a lot to deal with right now, "),                                              # plumbing only later
    ("Did you mean the kitchen light, or the one in the hallway near the door?",
     "Did you mean the kitchen light, "),                                                             # a first-sentence question
    ("Honestly, I think the best thing you can do is rest, drink some water, and take a short walk later.",
     "Honestly, I think the best thing you can do is rest, "),
])
def test_safe_clauses_go_early_and_the_whole_still_equals_polish(reply, clause):
    assert _first_early_chunk(reply, prev=True, strict=True, stock=True) == clause
    got, p, outs = _stream(list(reply), True, True, True, True)
    assert got == rp.polish(reply, "hi", True, strict_cap=True, stock=True) and p.early_released == 1


def test_off_by_default_and_follows_the_clause_flag(monkeypatch):
    assert fs.polish_early_enabled() is False
    monkeypatch.setenv("ZOE_FIRST_SOUND_CLAUSE", "1")
    assert fs.polish_early_enabled() is True
    monkeypatch.setenv("ZOE_FIRST_SOUND_POLISH_EARLY", "0")   # its own kill switch
    assert fs.polish_early_enabled() is False
    monkeypatch.delenv("ZOE_FIRST_SOUND_CLAUSE")
    monkeypatch.setenv("ZOE_FIRST_SOUND_POLISH_EARLY", "1")
    assert fs.polish_early_enabled() is True


# -- the chain: narration -> mirror guard -> polish (early) -> the voice loop's first unit ---------------------------

class _Source:
    """The brain: yields the chunks and counts how many characters it has sent so far."""

    def __init__(self, chunks):
        self.chunks, self.sent = chunks, 0

    async def stream(self):
        for c in self.chunks:
            self.sent += len(c)
            yield c


def _chain(src, message):
    import mirror_guard
    import narration_filter

    turn = narration_filter.filter_stream(src.stream())
    turn = mirror_guard.filter_stream(turn, message)
    return rp.filter_stream(turn, message, "chain-user", "chain-session")


async def _voice_loop(src, message):
    """voice_tts._generate_voice_stream's text rules over the filtered stream: the first unit (its kind, and how many brain
    characters had arrived when it was cut), then whole sentences, then the end-of-stream flush."""
    import routers.voice_tts as vt

    units, buf, first, cut_at, text = [], "", None, None, ""
    async for d in _chain(src, message):
        text += d
        buf += d
        if first is None:
            unit, buf, kind = vt._extract_first_unit_ex(buf)
            if unit:
                first, cut_at = kind, src.sent
                units.append(unit)
        if first is not None:
            ready, buf = vt._extract_complete_sentences(buf)
            units += ready
    if buf.strip():
        units.append(buf.strip())
    return text, units, first, cut_at


def test_chain_cuts_the_clause_before_its_sentence_ends_and_speaks_the_same_words(monkeypatch):
    reply = "Honestly, I think the best thing you can do is rest, drink some water, and take a short walk later. Sleep well."
    chunks = [reply[i:i + 4] for i in range(0, len(reply), 4)]
    sentence_end = reply.index("later.") + len("later.")
    monkeypatch.setenv("ZOE_STRIP_NARRATION", "1")
    monkeypatch.setenv("ZOE_MIRROR_GUARD", "on")

    off_text, off_units, off_kind, off_cut = asyncio.run(_voice_loop(_Source(chunks), "any tips for a slow afternoon"))
    rp.reset_state()
    monkeypatch.setenv("ZOE_FIRST_SOUND_CLAUSE", "1")
    on_text, on_units, on_kind, on_cut = asyncio.run(_voice_loop(_Source(chunks), "any tips for a slow afternoon"))

    assert on_text == off_text == reply                                   # the filters' TEXT is unchanged
    # off: polish hands over the WHOLE first sentence, so nothing is cut before it ends (then the 60-character rule cuts late)
    assert off_kind == "clause60" and off_cut > sentence_end
    # on: the clause reaches the loop while the brain is still inside the first sentence
    assert on_kind == "clause" and on_units[0] == "Honestly, I think the best thing you can do is rest,"
    assert on_cut < reply.index("water")
    assert " ".join(on_units).split() == " ".join(off_units).split()      # same words spoken, in the same order
