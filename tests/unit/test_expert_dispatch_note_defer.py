"""Unit tests for expert_dispatch.store_fact — note/jot capability-defer.

"make a note …" / "jot this down" / "note that …" belong to the notes
capability (note_create), NOT memory-store. store_fact must DEFER these to the
brain (return None) instead of emitting a bogus "Got it — I'll remember …" and
storing them as facts. Genuine memory teaches ("remember that …", "don't forget
…", "keep in mind …") are untouched.

The note-defer is an early return BEFORE any DB/MemPalace import, so calling
store_fact on a note cue does no I/O — slim-dep green (ci_safe).
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys

import pytest

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "services", "zoe-data", "expert_dispatch.py",
)


def _load_expert_dispatch():
    spec = importlib.util.spec_from_file_location("expert_dispatch", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    # Register before exec so @dataclass can resolve cls.__module__ in sys.modules.
    sys.modules["expert_dispatch"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def ed():
    return _load_expert_dispatch()


# Note/jot phrasings → defer (store_fact returns None → brain → note_create).
NOTE_CUES = [
    "make a note: wifi password is hunter2",
    "make a note that the door is broken",
    "take a note about the meeting tomorrow",
    "jot this down: buy milk on the way home",
    "jot down the new gate code",
    "note that the recycling goes out on tuesdays",
]

# Genuine memory teaches → NOT note cues (still stored by store_fact).
MEMORY_TEACHES = [
    "remember that my dad's name is Neil",
    "don't forget my anniversary is June 1st",
    "keep in mind I hate cilantro",
    "my mum is called Janice",
]


class TestStoreFactNoteDefer:
    def test_note_cue_regex_matches_notes_only(self, ed):
        for msg in NOTE_CUES:
            assert ed._NOTE_CUE_RE.search(msg.lower()), (
                f"note cue not detected: {msg!r}"
            )
        for msg in MEMORY_TEACHES:
            assert not ed._NOTE_CUE_RE.search(msg.lower()), (
                f"memory teach wrongly flagged as note cue: {msg!r}"
            )

    def test_store_fact_defers_note_cues_to_brain(self, ed):
        # The note-defer is an early return before any DB/MemPalace import, so
        # this reaches None without I/O.
        for msg in NOTE_CUES:
            result = asyncio.run(
                ed.store_fact("memory", msg, user_id="test-user", session_id="")
            )
            assert result is None, (
                f"store_fact should defer note cue to brain, got: {result!r} for {msg!r}"
            )
