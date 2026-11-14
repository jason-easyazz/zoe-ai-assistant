"""Golden C27: when two stored people share the name a correction uses, the correction rewrites NOTHING (it used to rewrite
both). "Distinct person" comes from data: the rows' person linkage, else the people graph. Invented names only. ``ci_safe``."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import value_correction as vc

pytestmark = pytest.mark.ci_safe

UID = "demo_samename_00000001"
SAID = "tova is 7 not 6"


def run(coro):
    return asyncio.run(coro)


class _Row:
    def __init__(self, i, text, person=None):
        self.id, self.text = i, text
        self.metadata = {"user_id": UID, **({"entity_type": "person", "entity_id": person} if person else {})}


class _Svc:
    def __init__(self, *rows):
        self.rows = list(rows)
        self.edits = []

    async def list_by_status(self, **_k):
        return list(self.rows)

    async def review(self, rid, decision=None, edits=None, actor=None, **_k):
        self.edits.append((rid, edits))
        return object()


def _graph(monkeypatch, answer):
    """The people graph answers the typed name with ``answer`` (None = unreadable)."""
    import person_extractor as pe

    async def ensure(_db):
        return (object(), False) if answer is not None else (None, False)

    async def resolve(_name, _uid, _db):
        return answer

    monkeypatch.setattr(pe, "_ensure_db", ensure)
    monkeypatch.setattr(pe, "_resolve_person", resolve)


@pytest.fixture(autouse=True)
def _wiring(monkeypatch):
    import correction_apply
    import user_prefs

    async def opted_in(_u):
        return False

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    _graph(monkeypatch, None)
    vc.reset_state()
    yield
    vc.reset_state()


def test_two_linked_people_with_one_name_rewrite_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    svc = _Svc(_Row("r0", "Tova is 6", "person-a"), _Row("r1", "Tova is 6 years old", "person-b"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 0
    assert svc.edits == []
    line = next(r.getMessage() for r in caplog.records if "VALUE_CORRECTION_AMBIGUOUS_SUBJECT" in r.getMessage())
    assert line == f"VALUE_CORRECTION_AMBIGUOUS_SUBJECT user={UID} rows=2 people=2"
    assert "tova" not in caplog.text.lower()                                    # never the name or the fact


def test_one_person_with_two_rows_still_rewrites_both():
    svc = _Svc(_Row("r0", "Tova is 6", "person-a"), _Row("r1", "Tova is 6 years old", "person-a"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 2
    assert svc.edits == [("r0", "Tova is 7"), ("r1", "Tova is 7 years old")]


def test_no_linkage_and_the_graph_says_ambiguous_rewrites_nothing(monkeypatch):
    _graph(monkeypatch, SimpleNamespace(ambiguous=True, matches=("p1", "p2"), person_id=None))
    svc = _Svc(_Row("r0", "Tova is 6"), _Row("r1", "Tova is 6 years old"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 0 and svc.edits == []


@pytest.mark.parametrize("answer", [None, SimpleNamespace(ambiguous=False, matches=("p1",), person_id="p1")])
def test_no_linkage_and_the_graph_cannot_tell_keeps_todays_behaviour(answer, monkeypatch):
    """Two unlinked rows the graph does not show to be two people may be the same person: today's behaviour, both rewritten."""
    _graph(monkeypatch, answer)
    svc = _Svc(_Row("r0", "Tova is 6"), _Row("r1", "Tova is 6 years old"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 2


def test_one_linked_row_and_one_unlinked_with_an_ambiguous_graph_rewrites_nothing(monkeypatch):
    _graph(monkeypatch, SimpleNamespace(ambiguous=True, matches=("person-a", "person-b"), person_id=None))
    svc = _Svc(_Row("r0", "Tova is 6", "person-a"), _Row("r1", "Tova is 6 years old"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 0


def test_a_single_row_is_rewritten_as_today(monkeypatch):
    _graph(monkeypatch, SimpleNamespace(ambiguous=True, matches=("p1", "p2"), person_id=None))
    svc = _Svc(_Row("r0", "Tova is 6"))
    assert run(vc.apply(SAID, UID, None, svc=svc)) == 1
