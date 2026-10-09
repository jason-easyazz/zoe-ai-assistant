"""ZOE_FIRST_SOUND_NARRATION_EARLY: the narration stripper releases a reply's first words as soon as they
cannot be an announcement, instead of holding them until the first sentence closes (measured 2026-10-09: the
first delta reached the stream loop ~0.9 s after the sidecar sent it, on every brain turn). Only allowed if the
OUTPUT is unchanged, so the load-bearing test is the equivalence property: for every reply and every chunking,
early release == the buffering form == strip_leading_narration."""
from __future__ import annotations

import itertools
import random

import pytest

import narration_filter as nf

pytestmark = pytest.mark.ci_safe

REPLIES = [
    # announcements: still stripped / held exactly as before
    "I'll check what I've got on file about you. You are Jason, and you live in Perth.",
    "Let me look: you have 3 events today.", "Sure, let me check. You have nothing on tomorrow.",
    "Okay. I'll just take a look. Your list has milk and eggs.", "Checking your calendar now. You are free at noon.",
    "Hold on, let me pull up your reminders. You have two.", "Let me check my notes, then tell you.",
    "I'll check with you tomorrow about the dentist.", "Give me a moment to look. It's twelve degrees and clear.",
    "I'm going to look at your lists. You have a tacos list.", "I will quickly check the weather. It is sunny.",
    # answers that merely START like an announcement
    "I think the best idea is to rest, but also drink some water.", "I will remember that for next time.",
    "I'm happy to help with that, and here is an idea.", "I am sure you will enjoy it. Octopuses are clever.",
    "Let me know if you want more detail. Octopuses have three hearts.", "Just kidding, the sky is blue.",
    "One thing to know is that bees make honey from nectar.", "Well, honestly, it depends on the day.",
    "Looking good today! You have a free afternoon.", "Hold the line, bees are fascinating.",
    # plain answers and odd shapes
    "Octopuses have three hearts and blue blood. That is quite unusual.", "22 degrees and mostly clear.",
    "Dr. Patel's number is on your list. Anything else?", "...", "It's 8:05 in the morning.", "", " ", "A",
    "Okay, sure. Absolutely, I will check. You are free.",
]


def _run(chunks, monkeypatch, early: bool) -> str:
    monkeypatch.setenv("ZOE_FIRST_SOUND_NARRATION_EARLY", "1" if early else "0")
    st = nf.NarrationStripper()
    return "".join([st.feed(c) for c in chunks] + [st.finish()])


def _chunkings(text: str):
    yield [text]
    yield list(text) or [""]
    for seed in range(5):
        rnd, out, i = random.Random(seed), [], 0
        while i < len(text):
            n = rnd.choice([1, 2, 3, 4, 5, 8])
            out.append(text[i:i + n])
            i += n
        yield out or [""]


@pytest.mark.parametrize("reply", REPLIES)
def test_early_release_output_is_byte_identical(reply, monkeypatch):
    for chunks in _chunkings(reply):
        slow = _run(chunks, monkeypatch, early=False)
        assert slow == nf.strip_leading_narration(reply), (reply, chunks)
        assert _run(chunks, monkeypatch, early=True) == slow, (reply, chunks)


def test_generated_replies_are_byte_identical(monkeypatch):
    fillers = ["", "Sure, ", "Okay. ", "Well, ", "Hmm... ", "Of course, ", "So "]
    leads = ["I'll ", "I will ", "Let me ", "Let's ", "I'm going to ", "I am going to ", "Allow me to ", "I think ",
             "I can ", "I'm ", "Let me know ", "Just ", "One ", "Checking ", "Looking at ", "Give me ", "Hold on, ",
             "Bear with me, ", "It is ", "The "]
    verbs = ["check", "look", "have a look", "pull up", "search for", "see what", "find out", "go through",
             "be happy to", "remember", "say", "fetch", "quickly check", "just look"]
    tails = [" your notes. You have two.", " the weather: it is sunny.", " it. Done.", " now", ", then tell you later."]
    rnd = random.Random(1234)
    combos = list(itertools.product(fillers, leads, verbs, tails))
    rnd.shuffle(combos)
    for filler, lead, verb, tail in combos[:600]:
        reply = f"{filler}{lead}{verb}{tail}"
        chunks = next(itertools.islice(_chunkings(reply), rnd.randint(0, 6), None))
        assert _run(chunks, monkeypatch, True) == _run(chunks, monkeypatch, False) == nf.strip_leading_narration(reply), \
            (reply, chunks)


def test_an_announcement_is_still_held_and_dropped(monkeypatch):
    monkeypatch.setenv("ZOE_FIRST_SOUND_NARRATION_EARLY", "1")
    st = nf.NarrationStripper()
    outs = [st.feed(c) for c in ["Let me ", "check what I've ", "got on file. ", "You are Jason."]] + [st.finish()]
    assert "".join(outs) == "You are Jason." and outs[:3] == ["", "", ""]


def test_default_off_follows_the_clause_flag_and_early_release_really_is_early(monkeypatch):
    monkeypatch.delenv("ZOE_FIRST_SOUND_NARRATION_EARLY", raising=False)
    monkeypatch.delenv("ZOE_FIRST_SOUND_CLAUSE", raising=False)
    assert nf.early_release_enabled() is False
    st = nf.NarrationStripper()
    assert st.feed("Octopuses have three hearts and blue") == "" and st.feed(" blood. ") != ""   # off: held to the end
    monkeypatch.setenv("ZOE_FIRST_SOUND_CLAUSE", "1")
    assert nf.early_release_enabled() is True
    assert nf.NarrationStripper().feed("Octopuses have three hearts and blue").startswith("Octopuses have")   # on: now
    monkeypatch.setenv("ZOE_FIRST_SOUND_NARRATION_EARLY", "0")   # an explicit setting wins
    assert nf.early_release_enabled() is False


@pytest.mark.parametrize("prefix,expected", [
    ("Octopuses have", False), ("I think", False), ("I will remember", False), ("Let me know", False),
    ("22 degrees", False), ("I'm happy", False), ("One thing", False), ("Hold the", False), ("Hmm, interesting", False),
    ("I", True), ("I'll check", True), ("Let me check", True), ("Sure, let me", True), ("I am going", True),
    ("Checking the", True), ("One moment", True), ("Hold on", True), ("", True), ("...", True), ("Okay.", True),
])
def test_may_become_narration(prefix, expected):
    assert nf.may_become_narration(prefix) is expected
