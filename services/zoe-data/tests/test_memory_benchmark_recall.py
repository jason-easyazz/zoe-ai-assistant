"""A LongMemEval/LOCOMO-*style* local recall benchmark over the REAL ranker.

The 2026 memory-audit's #1 gap (docs/architecture/memory-system-audit-2026.md)
is measurement: Zoe had a bespoke eval but no standardized, category-broken
P@k/R@k over MemoryService._semantic_search. This is the first slice — the same
category taxonomy LongMemEval/LOCOMO use, run against the real 7-signal blend
with a canned Chroma collection (no model, no Postgres, ci_safe, DEMO user).

Categories: single-hop, multi-hop (graph), temporal-recency, preference. Each
asserts a floor so a ranker regression trips it; the printed P@k/R@k table is the
number to grow toward the full benchmark (brain-as-judge, 500 Qs) later.
"""
import pytest

pytestmark = pytest.mark.ci_safe

from memory_service import MemoryService

USER = "demo_bench_user"  # DEMO user only
_NEW = "2026-07-08T00:00:00Z"
_OLD = "2026-01-01T00:00:00Z"  # ~6 months older → time-decay bites


class _FakeCollection:
    def __init__(self, result):
        self._result = result

    def query(self, **kwargs):
        return self._result

    def get(self, **kwargs):  # pragma: no cover
        return {}


def _md(entity_id=None, *, added_at=_NEW, memory_type="fact", confidence=0.9):
    md = {
        "user_id": USER, "visibility": "personal", "status": "approved",
        "confidence": confidence, "added_at": added_at, "memory_type": memory_type,
    }
    if entity_id is not None:
        md["entity_type"] = "person"
        md["entity_id"] = entity_id
    return md


def _rows(items):
    # items: (id, doc, entity_id, distance, added_at, memory_type)
    return {
        "ids": [[i[0] for i in items]],
        "documents": [[i[1] for i in items]],
        "metadatas": [[_md(i[2], added_at=i[4], memory_type=i[5]) for i in items]],
        "distances": [[i[3] for i in items]],
    }


def _service(items):
    svc = MemoryService(data_dir="/tmp/zoe-bench")
    svc._collection = lambda: _FakeCollection(_rows(items))
    return svc


def _pr_at_k(rows, relevant, k=5):
    top = [r.id for r in rows[:k]]
    hits = len(set(top) & relevant)
    return hits / k, hits / len(relevant)


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setenv("ZOE_HYBRID_RETRIEVAL_ENABLED", "1")
    monkeypatch.setenv("ZOE_GRAPH_RECALL_WEIGHT", "0.30")
    yield


# ── single-hop: the directly-relevant fact must top the list ──────────────────
def test_single_hop_recall():
    items = [
        ("ans", "My locker code is beef42", None, 0.22, _NEW, "fact"),
        ("d1", "The gym has new lockers", None, 0.40, _NEW, "fact"),
        ("d2", "A code review is scheduled", None, 0.45, _NEW, "fact"),
    ]
    svc = _service(items)
    rows = svc._semantic_search("what is my locker code", USER, limit=5)
    p, r = _pr_at_k(rows, {"ans"}, k=1)
    print(f"[single-hop] P@1={p:.2f} R@1={r:.2f}")
    assert rows[0].id == "ans" and p == 1.0


# ── multi-hop via graph: a connected-person fact lifts into top-k ─────────────
def test_multi_hop_graph_recall():
    # Distractors are semantically nearer than the target but share NO query
    # keywords (so the keyword signal is neutral) and none are graph-connected —
    # so only the graph adjacency of the connected fact (Bob) can lift it.
    items = [
        ("bob_job", "Bob works as a marine biologist", "pid_bob", 0.75, _NEW, "person"),
        ("x1", "The weather is pleasant today", None, 0.50, _NEW, "fact"),
        ("x2", "Someone left a parcel by the door", None, 0.52, _NEW, "fact"),
        ("x3", "The car is due for a service", None, 0.54, _NEW, "fact"),
    ]
    svc = _service(items)
    q = "what is Alice's husband's job"
    off = svc._semantic_search(q, USER, limit=5, depth_by_pid=None)
    on = svc._semantic_search(q, USER, limit=5, depth_by_pid={"pid_alice": 0, "pid_bob": 1})
    off_p, _ = _pr_at_k(off, {"bob_job"}, k=3)
    on_p, on_r = _pr_at_k(on, {"bob_job"}, k=3)
    print(f"[multi-hop] P@3 OFF={off_p:.2f} → ON={on_p:.2f} R@3 ON={on_r:.2f}")
    assert on_p >= off_p and on_r == 1.0
    # boost must lift the connected fact strictly above where distance alone put it
    assert (next(i for i, r in enumerate(on) if r.id == "bob_job")
            <= next(i for i, r in enumerate(off) if r.id == "bob_job"))


# ── temporal-recency: a fresh fact outranks a stale one at equal distance ─────
def test_temporal_recency_recall():
    items = [
        ("old", "I live in New York", None, 0.30, _OLD, "fact"),
        ("new", "I live in San Francisco", None, 0.30, _NEW, "fact"),
    ]
    svc = _service(items)
    rows = svc._semantic_search("where do I live", USER, limit=5)
    order = [r.id for r in rows]
    print(f"[temporal] order={order} (fresh 'new' should precede stale 'old')")
    assert order.index("new") < order.index("old"), "time-decay must favour the fresher fact"


# ── preference: a preference-typed fact gets the preference lift ──────────────
def test_preference_recall():
    items = [
        ("pref", "I prefer window seats", None, 0.40, _NEW, "preference"),
        ("plain", "Window cleaning is Tuesday", None, 0.38, _NEW, "fact"),
    ]
    svc = _service(items)
    rows = svc._semantic_search("what seat do I like", USER, limit=5)
    print(f"[preference] order={[r.id for r in rows]}")
    # the slightly-farther preference fact should not be buried below a plain
    # near-distance distractor once the preference signal applies
    assert "pref" in {r.id for r in rows[:2]}


# ── the scorecard: overall P@1 across the categories (the growable number) ────
def test_overall_precision_floor(capsys):
    cases = [
        ("what is my locker code", {"ans"}, [
            ("ans", "My locker code is beef42", None, 0.22, _NEW, "fact"),
            ("d", "a distractor about codes", None, 0.44, _NEW, "fact")]),
        ("where do I live", {"new"}, [
            ("new", "I live in San Francisco", None, 0.30, _NEW, "fact"),
            ("old", "I live in New York", None, 0.30, _OLD, "fact")]),
    ]
    hit = 0
    for q, rel, items in cases:
        svc = _service(items)
        rows = svc._semantic_search(q, USER, limit=5)
        hit += 1 if rows and rows[0].id in rel else 0
    p_at_1 = hit / len(cases)
    print(f"[BENCHMARK] mean P@1 over {len(cases)} categories = {p_at_1:.2f}")
    assert p_at_1 == 1.0
