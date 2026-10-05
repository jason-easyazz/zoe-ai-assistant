"""The shared reconcile seam never SKIPS a fact that names something new (brain-extraction research L3).

After the digests' own dedup, every writer asks ``memory_quality`` for ADD / UPDATE / SKIP. A candidate
that adds a short new name ("... two kids Mika", +4 characters) sat inside the richness margin and was
called a restatement (SKIP): the new name was dropped. A candidate that names something new is now
never a skip: it supersedes the row it strictly extends (history kept), or is added beside a different
statement. The digests also pass ``extend_supersedes=True`` so an extension that the similarity rules
call "add" still supersedes the sparse row it contains.
"""
from __future__ import annotations

import asyncio

import pytest

from memory_quality import classify_against_existing, reconcile_for_ingest

pytestmark = pytest.mark.ci_safe

STORED = "User's friend Dana has two kids."


class _Row:
    def __init__(self, mem_id, text):
        self.id, self.text, self.metadata = mem_id, text, {}


class _Svc:
    def __init__(self, rows):
        self.rows = rows

    async def search(self, *a, **k):
        return self.rows


def test_a_short_new_name_is_an_update_not_a_skip():
    got = classify_against_existing("User's friend Dana has two kids Mika.", [("x", STORED)])
    assert got == ("update", "x")


def test_control_a_pure_restatement_of_a_richer_row_is_still_a_skip():
    rich = [("r", "My dad's name is Neil, spelled N-E-I-L.")]
    assert classify_against_existing("User's father's name is Neil.", rich) == ("skip", "r")


def test_control_an_identical_fact_is_still_a_skip():
    assert classify_against_existing(STORED, [("x", STORED)]) == ("skip", "x")


def test_a_new_name_that_does_not_extend_the_row_is_added_not_skipped():
    # near-identical wording but the stored row holds a word the candidate lacks: a different statement
    got = classify_against_existing("User's friend Dana has two kids Mika.",
                                    [("x", "User's friend Dana has two kids, aged six.")])
    assert got[0] != "skip"


def test_extend_supersedes_flag_turns_an_extension_into_an_update():
    svc = _Svc([_Row("stored-1", "User's friend Dana has a dog.")])
    fact = "User's friend Dana has a dog called Max."
    assert asyncio.run(reconcile_for_ingest(svc, fact, "demo-user")) == ("add", None)   # default unchanged
    assert asyncio.run(reconcile_for_ingest(
        svc, fact, "demo-user", extend_supersedes=True)) == ("update", "stored-1")
