"""Memory reconciliation + the 2026 field's named failure modes (durable).

Covers the mem0-style ADD/UPDATE/SKIP write reconciliation
(memory_quality.classify_against_existing) and the failure modes the 2026 audit
(docs/architecture/memory-system-audit-2026.md) calls out — staleness,
same-attribute contradiction, and identity/scope isolation — asserted against the
REAL _semantic_search filters and the REAL classifier. ci_safe / DEMO users, no
model, no Postgres (canned Chroma collection).
"""
import pytest

pytestmark = pytest.mark.ci_safe

from memory_service import MemoryService
import memory_quality

USER = "demo_fm_user"
OTHER = "demo_other_user"
_NOW = "2026-07-08T00:00:00Z"
_PAST = "2020-01-01T00:00:00Z"


# ── mem0 ADD / UPDATE / SKIP reconciliation ───────────────────────────────────
def test_reconciliation_add_when_novel():
    op, mem_id = memory_quality.classify_against_existing("I love sailing", [])
    assert op == "add" and mem_id is None


def test_reconciliation_skip_near_duplicate():
    existing = [("m1", "My dad's name is Neil")]
    op, mem_id = memory_quality.classify_against_existing("My dad's name is Neil", existing)
    assert op == "skip" and mem_id == "m1", (op, mem_id)


def test_reconciliation_update_on_same_attribute_contradiction():
    # same attribute (dad's name), DIFFERENT value → correction → supersede stale
    existing = [("m1", "My dad's name is Neil")]
    op, mem_id = memory_quality.classify_against_existing("My dad's name is Neal", existing)
    assert op == "update" and mem_id == "m1", (op, mem_id)


def test_reconciliation_keeps_distinct_facts():
    existing = [("m1", "My dad's name is Neil")]
    op, _ = memory_quality.classify_against_existing("My mum's name is Janice", existing)
    assert op == "add", op  # different attribute → keep both


# ── failure modes against the real _semantic_search filters ───────────────────
class _FakeCollection:
    def __init__(self, rows):
        self._rows = rows

    def query(self, **kwargs):
        return {
            "ids": [[r["id"] for r in self._rows]],
            "documents": [[r["doc"] for r in self._rows]],
            "metadatas": [[r["md"] for r in self._rows]],
            "distances": [[r["dist"] for r in self._rows]],
        }

    def get(self, **kwargs):  # pragma: no cover
        return {}


def _svc(rows):
    svc = MemoryService(data_dir="/tmp/zoe-fm")
    svc._collection = lambda: _FakeCollection(rows)
    return svc


def _md(**over):
    md = {"user_id": USER, "visibility": "personal", "status": "approved",
          "confidence": 0.9, "added_at": _NOW, "memory_type": "fact"}
    md.update(over)
    return md


def test_identity_scope_isolation_filters_other_user():
    rows = [
        {"id": "mine", "doc": "my vault code is 1234", "dist": 0.30, "md": _md()},
        {"id": "theirs", "doc": "vault code SECRET", "dist": 0.10, "md": _md(user_id=OTHER)},
    ]
    out = {r.id for r in _svc(rows)._semantic_search("vault code", USER, limit=5)}
    assert "theirs" not in out and "mine" in out, out  # another user's fact must never surface


def test_expired_fact_is_not_recalled():
    rows = [
        {"id": "live", "doc": "meeting at 3pm today", "dist": 0.30, "md": _md()},
        {"id": "expired", "doc": "meeting at 9am (old)", "dist": 0.10,
         "md": _md(expires_at=_PAST)},
    ]
    out = {r.id for r in _svc(rows)._semantic_search("meeting time", USER, limit=5)}
    assert "expired" not in out and "live" in out, out


def test_superseded_status_fact_is_not_recalled():
    # the correction machinery marks stale rows status=superseded; they must not resurface
    rows = [
        {"id": "current", "doc": "I live in San Francisco", "dist": 0.30, "md": _md()},
        {"id": "stale", "doc": "I live in New York", "dist": 0.10, "md": _md(status="superseded")},
    ]
    out = {r.id for r in _svc(rows)._semantic_search("where do I live", USER, limit=5)}
    assert "stale" not in out and "current" in out, out


def test_staleness_fresh_outranks_old_at_equal_distance():
    rows = [
        {"id": "stale", "doc": "I work at Acme", "dist": 0.30, "md": _md(added_at=_PAST)},
        {"id": "fresh", "doc": "I work at Globex", "dist": 0.30, "md": _md(added_at=_NOW)},
    ]
    order = [r.id for r in _svc(rows)._semantic_search("where do I work", USER, limit=5)]
    assert order.index("fresh") < order.index("stale"), order
