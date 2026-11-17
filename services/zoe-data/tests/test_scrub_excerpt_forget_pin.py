"""The scrubber widening must not weaken a forget that still matches the owner's words (the split of PR #2574).

On this branch a forget still finds a row by the EXCERPT's words (``turn_forget.row_is_from_turn``: the excerpt equals the turn as
said; ``ask_to_remember.siblings_of`` / ``provenance_answers`` twins: two excerpts compared) and an exact-words entry by its words
(``exact_words.erase_text``). So the stored excerpt and the exact-words index keep the pre-widening scrub
(``memory_service.scrub_pii_matchable``) until forgets match by saved-turn id (#2574), while the fact text and every other derived
copy take the widened ``scrub_pii``. Every value here is invented.

Built-in negative control (``wide``): pointing those two copies at the widened scrubber makes the forget miss the row - the very
regression this split must not ship. Delete this file when #2574 lands (its excerpts are redacted and its forgets go by id).
"""
from __future__ import annotations

import pytest

import exact_words as xw
import memory_service
from memory_service import scrub_pii, scrub_pii_matchable
from test_ask_to_remember import UID, _execute, rows, run, say, svc  # noqa: F401 - the fixture

pytestmark = pytest.mark.ci_safe

R = "[REDACTED]"
FIRST = "Remember that the plumber's number is 0400 000 001"
SECOND = "Remember that the plumber's number is 0400 000 002"


def _widen(monkeypatch):
    """The control: the excerpt / index copies take the widened scrubber (what #2574 does, safe only with its id forgets)."""
    monkeypatch.setattr(memory_service, "scrub_pii_matchable", lambda t: scrub_pii(t))


# ── the pre-widening scrub, byte for byte (rows from origin/main's scrub_pii, read on 857e55f2c) ─────────────────────────────

@pytest.mark.parametrize("text, want", [
    ("my password is hunter2", ("my password is [REDACTED]", None)),
    ("the api key: abc123 for staging", ("the api key: [REDACTED] for staging", None)),
    ("call me on 0400 555 101", ("call me on 0400 555 101", None)),             # contact: kept here
    ("we live at 12 Example Street", ("we live at 12 Example Street", None)),
    ("the door code is 5791", ("the door code is 5791", None)),                 # a code: kept here
    ("my pin 4821", ("my pin 4821", None)),
    ("card 4111 1111 1111 1111", ("card 4111 1111 1111 1111", "luhn_cc")),        # the rejects are the shared ones
    ("", ("", None)),
])
def test_the_matchable_scrub_is_the_old_scrub(text, want):
    assert scrub_pii_matchable(text) == want


def test_the_widened_scrub_is_what_the_fact_and_other_copies_take():
    assert scrub_pii("call me on 0400 555 101")[0] == "call me on " + R
    assert scrub_pii("the door code is 5791", contact=False)[0] == "the door code is " + R
    assert scrub_pii("call me on 0400 555 101", contact=False)[0] == "call me on 0400 555 101"


# ── "forget that": two turns that differ ONLY by a phone number ────────────────────────────────────────────────────────

@pytest.mark.parametrize("wide", [False, True])
def test_forget_that_takes_the_turn_said_last_and_leaves_its_look_alike(svc, monkeypatch, wide):  # noqa: F811
    if wide:
        _widen(monkeypatch)
    assert say(svc, FIRST) and say(svc, SECOND)
    assert sorted(r.text for r in rows(svc)) == ["the plumber's number is 0400 000 001", "the plumber's number is 0400 000 002"]
    reply = _execute(svc, "memory_forget_last")
    left = [r.text for s in ("approved", "pending") for r in rows(svc, status=s)]
    if wide:
        # the control: the redacted excerpt no longer equals the turn as said, and the ask's own id is not an extractor id,
        # so the forget finds nothing to take - the second number stays stored
        assert "the plumber's number is 0400 000 002" in left and not reply.startswith("Done — I forgot")
    else:
        assert reply.startswith("Done — I forgot")
        assert left == ["the plumber's number is 0400 000 001"]                      # the look-alike from the other turn stays


def test_the_stored_excerpt_keeps_its_words_and_the_fact_keeps_the_number(svc):  # noqa: F811
    say(svc, FIRST)
    (row,) = rows(svc)
    assert row.text == "the plumber's number is 0400 000 001"
    assert row.metadata["source_excerpt"] == FIRST                                    # matchable by the forget, as on main


# ── the exact-words index: erase_text finds a turn by the words as said ─────────────────────────────────────────────────

ONE = "I told the courier to ring me on 0400 000 001 when he arrives"
TWO = "I told the courier to ring me on 0400 000 002 when he arrives"


@pytest.mark.parametrize("wide", [False, True])
def test_the_exact_words_forget_erases_the_turn_said_and_only_it(monkeypatch, wide):
    if wide:
        _widen(monkeypatch)
    monkeypatch.delenv(xw.ENV, raising=False)
    be = xw.MemoryBackend()
    xw.set_backend(be)

    async def not_shielded(_u, _t):
        return False

    monkeypatch.setattr(xw, "_shielded", not_shielded)
    try:
        assert run(xw.index_turn(UID, ONE, said_at=1000.0))
        second = run(xw.index_turn(UID, TWO, said_at=1001.0))
        erased = run(xw.erase_text(UID, TWO))
        kept = [t for _id, t in run(be.rows_matching(UID, "courier"))]
        if wide:
            # the control, two harms: the two turns redact alike and collapse into ONE entry (the second is a "re-delivery"),
            # and the forget of the words as said matches nothing, so the entry stays
            assert second is False and erased == 0 and len(kept) == 1 and R in kept[0]
        else:
            assert second is True
            assert erased == 1 and kept == [ONE]
    finally:
        xw.set_backend(None)
