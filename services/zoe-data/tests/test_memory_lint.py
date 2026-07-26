"""Focused unit tests for the pure, report-only memory Lint detectors.

These exercise contradiction / stale / orphan / duplicate detection over
in-memory rows only - no ChromaDB, no MemoryService, no network. They also
assert the hard contract: lint never mutates its inputs.
"""

import datetime

import pytest

from memory_lint import (
    LintReport,
    dreaming_lint_enabled,
    lint_memories,
)

pytestmark = pytest.mark.ci_safe

NOW = datetime.datetime(2026, 6, 18, 12, 0, 0)


def _row(mem_id, text, **meta):
    base = {"status": "approved", "added_at": "2026-06-01T00:00:00", "access_count": 1}
    base.update(meta)
    return {"id": mem_id, "text": text, "metadata": base}


def _lint(rows, **kw):
    kw.setdefault("now", NOW)
    return lint_memories(rows, user_id="jason", **kw)


# Duplicates ----------------------------------------------------------------

def test_exact_duplicate_text_is_flagged():
    rows = [
        _row("a", "User lives in Sydney."),
        _row("b", "user  lives in   sydney."),  # same after normalisation
    ]
    report = _lint(rows)
    assert len(report.duplicates) == 1
    finding = report.duplicates[0]
    assert finding.kind == "duplicate"
    assert set(finding.memory_ids) == {"a", "b"}
    assert finding.detail["similarity"] == 1.0


def test_near_duplicate_text_is_flagged():
    rows = [
        _row("a", "User enjoys long bike rides on the weekend in summer."),
        _row("b", "User enjoys long bike rides on the weekends in summer."),
    ]
    report = _lint(rows, near_duplicate_ratio=0.9)
    kinds = {f.reason for f in report.duplicates}
    assert "near-duplicate text" in kinds


def test_distinct_text_is_not_a_duplicate():
    rows = [
        _row("a", "User likes coffee."),
        _row("b", "User has a dog named Rex."),
    ]
    report = _lint(rows)
    assert report.duplicates == []


# Stale ---------------------------------------------------------------------

def test_superseded_status_is_stale():
    rows = [_row("a", "Old fact", status="superseded")]
    report = _lint(rows)
    assert len(report.stale) == 1
    assert report.stale[0].detail["status"] == "superseded"


def test_expired_row_is_stale():
    rows = [_row("a", "Temporary fact", expires_at="2026-01-01T00:00:00")]
    report = _lint(rows)
    assert any(f.reason == "expired" for f in report.stale)


def test_old_row_is_stale():
    rows = [_row("a", "Ancient fact", added_at="2020-01-01T00:00:00")]
    report = _lint(rows, stale_age_days=365)
    assert any(f.reason == "old" for f in report.stale)


def test_recent_approved_row_is_not_stale():
    rows = [_row("a", "Fresh fact", added_at="2026-06-17T00:00:00")]
    report = _lint(rows)
    assert report.stale == []


# Orphans -------------------------------------------------------------------

def test_orphan_has_no_access_queries_or_links():
    rows = [_row("a", "Never used", access_count=0, unique_query_count=0)]
    report = _lint(rows)
    assert len(report.orphans) == 1
    assert report.orphans[0].kind == "orphan"


def test_accessed_row_is_not_orphan():
    rows = [_row("a", "Used fact", access_count=3, unique_query_count=0)]
    report = _lint(rows)
    assert report.orphans == []


def test_linked_row_is_not_orphan():
    rows = [_row("a", "Linked fact", access_count=0, unique_query_count=0,
                  related_ids="zoe_jason_xyz")]
    report = _lint(rows)
    assert report.orphans == []


# Contradictions ------------------------------------------------------------

def test_single_valued_relation_conflict_is_contradiction():
    rows = [
        _row("a", "User lives in Sydney."),
        _row("b", "User lives in Melbourne."),
    ]
    report = _lint(rows)
    assert len(report.contradictions) >= 1
    finding = report.contradictions[0]
    assert set(finding.memory_ids) == {"a", "b"}
    assert "sydney" in finding.detail["values"] or "melbourne" in finding.detail["values"]


def test_same_value_single_relation_is_not_contradiction():
    rows = [
        _row("a", "User lives in Sydney."),
        _row("b", "User lives in Sydney now."),
    ]
    report = _lint(rows)
    # Same value -> no single-valued contradiction (may dup, but not contradict).
    assert report.contradictions == []


def test_polarity_flip_is_contradiction():
    rows = [
        _row("a", "User drinks coffee every morning before work."),
        _row("b", "User does not drink coffee every morning before work."),
    ]
    report = _lint(rows)
    assert any(f.reason == "polarity flip on overlapping facts"
               for f in report.contradictions)


def test_compatible_preferences_are_not_contradictions():
    rows = [
        _row("a", "User likes coffee."),
        _row("b", "User likes tea."),
    ]
    report = _lint(rows)
    assert report.contradictions == []


# Report shape + contract ---------------------------------------------------

def test_report_to_dict_has_totals_and_sections():
    rows = [_row("a", "User lives in Sydney."), _row("b", "User lives in Perth.")]
    report = _lint(rows)
    d = report.to_dict()
    assert d["user_id"] == "jason"
    assert d["scanned"] == 2
    assert "totals" in d and d["totals"]["all"] == report.total
    for key in ("contradictions", "stale", "orphans", "duplicates"):
        assert key in d


def test_lint_does_not_mutate_inputs():
    rows = [_row("a", "User lives in Sydney."), _row("a2", "User lives in Perth.")]
    snapshot = [dict(r["metadata"]) for r in rows]
    _lint(rows)
    assert [r["metadata"] for r in rows] == snapshot


def test_empty_input_produces_empty_report():
    report = _lint([])
    assert isinstance(report, LintReport)
    assert report.scanned == 0
    assert report.total == 0


def test_dreaming_lint_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_LINT_IN_DREAMING", raising=False)
    assert dreaming_lint_enabled() is False
    monkeypatch.setenv("ZOE_MEMORY_LINT_IN_DREAMING", "1")
    assert dreaming_lint_enabled() is True


def test_accepts_objects_with_attributes():
    class Ref:
        def __init__(self, id, text, metadata):
            self.id = id
            self.text = text
            self.metadata = metadata

    rows = [
        Ref("a", "User lives in Sydney.", {"status": "approved", "access_count": 1}),
        Ref("b", "User lives in Hobart.", {"status": "approved", "access_count": 1}),
    ]
    report = _lint(rows)
    assert report.scanned == 2
    assert len(report.contradictions) >= 1
