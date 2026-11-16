"""The child-safety floor does what docs/knowledge/authority-table.md says it does (owner decision 2026-11-16, audit row G5).

The doc's "Who the child-safety floor treats as a child" table is the rule in words: an unrecognised or unverified voice is a
guest and is answered; a child, or a member who could be one, is refused; an error in the check refuses. This test reads that
table and runs each row through the real ``child_safety_floor.check`` + ``resolve`` (the real ``_who``; only the account lookup is
faked, with invented ids), so the doc and the code cannot drift apart silently. Negative control: a doc row flipped is caught.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

import child_safety_floor as csf

pytestmark = pytest.mark.ci_safe

DOC = Path(__file__).resolve().parents[3] / "docs/knowledge/authority-table.md"
HAZARD_ASK = "walk me through using the drill"
ACCOUNTS = {"member-adult": (False, "family_member"), "member-child": (False, "child"), "member-unknown": (False, "")}


def doc_rows(text: str) -> dict[str, tuple[str, str]]:
    section = text.split("## Who the child-safety floor treats as a child", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| ([a-z ]+?) \| `([\w-]+)` \| (answered|refused) \|$", section, re.M)
    return {speaker: (uid, verdict) for speaker, uid, verdict in rows}


@pytest.fixture
def floor(monkeypatch):
    import enrollment_state

    monkeypatch.delenv(csf.ENV, raising=False)            # the default mode (enforce)
    csf._SESSIONS.clear()

    async def account_facts(uid, db=None):
        return ACCOUNTS[uid]

    monkeypatch.setattr(enrollment_state, "_account_facts", account_facts)

    def run(speaker: str, uid: str) -> str:
        if speaker == "floor check errors":
            async def boom(_uid):
                raise RuntimeError("synthetic")
            monkeypatch.setattr(csf, "_who", boom)
        pending = csf.check(HAZARD_ASK, "session-1", uid)
        assert pending is not None, "the sample must be a hazard ask"
        return "refused" if asyncio.run(csf.resolve(pending, uid)) else "answered"

    yield run
    csf._SESSIONS.clear()


def test_the_doc_states_the_owner_decision_rows():
    rows = doc_rows(DOC.read_text(encoding="utf-8"))
    assert {"adult member", "child member", "guest", "unverified voice"} <= set(rows)
    assert rows["guest"][1] == rows["unverified voice"][1] == "answered"     # the 2026-11-16 decision
    assert rows["child member"][1] == "refused" and rows["floor check errors"][1] == "refused"


@pytest.mark.parametrize("speaker", ["adult member", "child member", "guest", "unverified voice", "member who could be a child",
                                     "floor check errors"])
def test_the_floor_does_what_the_doc_says(speaker, floor):
    uid, verdict = doc_rows(DOC.read_text(encoding="utf-8"))[speaker]
    assert floor(speaker, uid) == verdict, f"{speaker}: the doc says {verdict}"


def test_negative_control_a_flipped_doc_row_is_caught(floor):
    flipped = DOC.read_text(encoding="utf-8").replace("| `voice-guest` | answered |", "| `voice-guest` | refused |")
    uid, verdict = doc_rows(flipped)["unverified voice"]
    assert floor("unverified voice", uid) != verdict
