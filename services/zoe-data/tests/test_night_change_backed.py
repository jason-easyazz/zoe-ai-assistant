"""K9f class fix: the model's ``kind=change`` label alone never changes a thread (model points, code decides).

A 12B window on 2026-10-10 (seed 3000) flagged one thread of a FLAT week as changed: the flat month's day-20 line
"Booked the train to <place> for <child>'s birthday." carried the model's ``change`` label, and the night mind trusted it.
``night_mind.change_backed`` now requires the owner's own words to say something changed (per-language ``change_cues`` data, or
an end-state marker) before a change label moves the thread's status, retires earlier observations, or skips the stale check.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import night_mind as nm  # noqa: E402

pytestmark = pytest.mark.ci_safe

# the K9 / K9f / K12 corpora's own lines (scripts/perf/zmb/life.py) plus contrasts; names are invented
CASES = [
    ("Booked the train to Saltreach for Rowan's birthday.", False),            # K9f flat month day 20: NOT a change
    ("Running with Dana again, we are building up to the fun run.", False),
    ("Dana and I ran 5k today.", False),
    ("The Saltreach trip is off, we are going to Oldmere instead.", True),     # K9 drift + K12 labelled change
    ("My sister Brynja has moved to Pellham.", True),                          # K12 labelled change
    ("The concert got postponed to next month.", True),
    ("We called it off.", True),
    ("I stopped going to the gym.", True),                                     # an end-state marker counts
    ("I didn't stop going to the gym.", False),                                # a denied end is no change
    ("Al final no vamos a Madrid, vamos a Sevilla en su lugar.", True),        # Spanish data, no English in code
    ("Reservé el tren a Madrid para el cumpleaños.", False),
    ("My sister Brynja now lives in Pellham.", True),                          # Greptile round 3: an ordinary changed-value statement with no "moved"
    ("Brynja now works at the clinic.", True),
    ("Mi hermana Brynja ahora vive en Pellham.", True),
    ("I now know the way to Pellham, it is a pleasant drive.", False),         # "now" alone is not a change
    ("", False),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_change_is_backed_only_by_the_owners_own_words(text, expected):
    assert nm.change_backed(text) is expected


def test_a_language_without_change_cues_keeps_the_models_label(monkeypatch):
    """Fail-open by design: a new language file without ``change_cues`` must not silently mute every change."""
    monkeypatch.setattr(nm._lex, "words", lambda lang, key: [] if key == "change_cues" else ["x"])
    assert nm.change_backed("Booked the train to Saltreach.") is True


def test_every_gate_site_asks_change_backed():
    """The three places a change label acts (status, the retire cut, the stale-belief skip) all go through change_backed."""
    src = Path(nm.__file__).read_text(encoding="utf-8")
    assert src.count('x.kind == "change" and moment_change_backed(x)') == 2
    assert 'm.kind == "change" and moment_change_backed(m)' in src
    assert "change_backed(x.quote)" not in src and "change_backed(m.quote)" not in src.replace("if change_backed(m.quote):", "")


def _moment(turn_text, quote, kind="change"):
    from datetime import datetime
    return nm.Moment(mid="m1", turn=nm.Turn(id="t1", text=turn_text, at=datetime(2026, 10, 1, 9, 0)), quote=quote, kind=kind)


def test_a_short_quote_is_checked_against_its_own_sentence_but_not_the_rest_of_the_turn():
    """Greptile round 3 on #1982: the cue may sit in the sentence around a short cited quote; another sentence of the turn must not back the label."""
    assert nm.moment_change_backed(_moment("Brynja now lives in Pellham.", "Brynja")) is True            # the quote alone has no cue, its sentence does
    assert nm.moment_change_backed(_moment("Hello! Brynja has moved to Pellham, which is great.", "Brynja")) is True
    assert nm.moment_change_backed(_moment("Booked the train to Saltreach. The concert is off.", "Booked the train to Saltreach")) is False   # a cue in ANOTHER sentence
    assert nm.moment_change_backed(_moment("Booked the train to Saltreach for Rowan's birthday.", "Booked the train to Saltreach")) is False  # K9f: still no change
    assert nm.moment_change_backed(_moment("anything", "not in the turn")) is False
