"""A filtered HNSW query that comes back short must not become "nothing stored".

Measured 2026-10-04 on the live palace (258 rows, 1,591 ever added — 84 % tombstones
from demo-user churn): ``col.query`` for "When did I tell you about the dentist?" returned
0 ids WITH the owner filter and 18 without, while a one-word query returned 18 either
way. The recall packet then carried no semantic hits and the brain answered that it had
nothing about the dentist. ``_semantic_search`` now over-fetches without the filter when
the filtered query is short and applies the same visibility rules in Python."""
from __future__ import annotations

import pytest

import memory_service
from memory_service import MemoryService

pytestmark = pytest.mark.ci_safe


class _Col:
    """Rows for two owners; the filtered query is deliberately SHORT (the measured
    hnswlib behaviour), the unfiltered query returns everything nearest-first."""

    def __init__(self, rows):
        self.rows = rows  # list of (id, doc, meta, dist)
        self.calls = []

    def count(self):
        return len(self.rows)

    def query(self, *, query_texts, n_results, include, where=None):
        self.calls.append({"where": where, "n": n_results})
        rows = self.rows if where is None else []
        rows = rows[:n_results]
        return {"ids": [[r[0] for r in rows]], "documents": [[r[1] for r in rows]],
                "metadatas": [[r[2] for r in rows]], "distances": [[r[3] for r in rows]]}


def _svc(col):
    svc = MemoryService(data_dir="/nonexistent/zoe-test-search-fallback")
    svc._collection = lambda: col
    return svc


def _row(i, owner, text, dist, **meta):
    return (f"id{i}", text, {"user_id": owner, "status": "approved", "confidence": 0.82, **meta}, dist)


def test_short_filtered_query_falls_back_and_keeps_only_the_owners_rows():
    col = _Col([
        _row(1, "family-admin", "Admin fact nearest to the query", 0.10),
        _row(2, "demo_bar_x", "User has a dentist appointment on Friday for a cracked molar.", 0.20),
        _row(3, "other", "Someone else's dentist", 0.25),
        _row(4, "demo_bar_x", "User is really nervous about their dentist appointment.", 0.30),
        _row(5, "other", "Family-visible row", 0.35, visibility="family"),
    ])
    hits = _svc(col)._semantic_search("When did I tell you about the dentist?", "demo_bar_x", 6, {})
    ids = [h.id for h in hits]
    assert "id2" in ids and "id4" in ids          # the owner's rows came back
    assert "id1" not in ids and "id3" not in ids  # other owners' private rows never leak
    assert "id5" in ids                           # family-visible rows stay visible, as the filter allows
    assert [c["where"] for c in col.calls] == [{"$or": [{"user_id": "demo_bar_x"}, {"wing": "demo_bar_x"},
                                                        {"visibility": "family"}]}, None]
    assert col.calls[1]["n"] == len(col.rows)      # capped at the collection size


def test_sufficient_filtered_query_never_issues_the_fallback():
    rows = [_row(i, "u1", f"fact {i}", 0.1 * i) for i in range(1, 8)]

    class _Full(_Col):
        def query(self, *, query_texts, n_results, include, where=None):
            self.calls.append({"where": where, "n": n_results})
            r = self.rows[:n_results]
            return {"ids": [[x[0] for x in r]], "documents": [[x[1] for x in r]],
                    "metadatas": [[x[2] for x in r]], "distances": [[x[3] for x in r]]}

    col = _Full(rows)
    hits = _svc(col)._semantic_search("fact", "u1", 3, {})
    assert len(hits) == 3 and len(col.calls) == 1 and col.calls[0]["where"] is not None


def test_fallback_dedups_and_still_drops_superseded_rows():
    col = _Col([
        _row(1, "u1", "live", 0.1),
        ("id1", "live", {"user_id": "u1", "status": "approved"}, 0.1),   # the same id again
        _row(2, "u1", "gone", 0.2, status="superseded"),
    ])
    hits = _svc(col)._semantic_search("live", "u1", 5, {})
    assert [h.id for h in hits] == ["id1"]
