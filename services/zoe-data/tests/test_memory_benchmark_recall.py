"""A LongMemEval/LOCOMO-*style* local recall benchmark over the REAL ranker.

The 2026 memory-audit's #1 gap (docs/architecture/memory-system-audit-2026.md)
is measurement: Zoe had a bespoke eval but no standardized, category-broken
P@k/R@k over ``MemoryService._semantic_search``. This module is the repeatable
benchmark that closes it.

Why canned (no model, no Postgres)?  The benchmark injects Chroma query results
(ids + documents + metadata + L2 distances) straight into a fake collection, so
it exercises the *real* 7-signal blend AND the real per-row visibility/expiry
filters inside ``_semantic_search`` deterministically, with zero embedding or DB
cost. That keeps it ``ci_safe`` and RAM-safe (the box swap-thrashes under model
load) while still measuring the actual ranker. The graph-adjacency (multi-hop)
signal is driven exactly as the async ``search`` caller drives it in prod: via a
``depth_by_pid`` map, i.e. the seeded people-graph adjacency, injected at the
ranking layer instead of round-tripping Postgres.

Query classes (the LongMemEval/LOCOMO taxonomy):
  - single-hop     — the directly-relevant fact tops the list.
  - temporal       — most-recent-wins when a fact is updated/superseded.
  - multi-hop      — the answer lives 1-2 graph hops from the named person.
  - isolation      — cross-user rows never leak into a caller's recall.
  - dedup          — the canonical fact beats a near-duplicate/stale variant.

Each class asserts a floor (a ranker regression trips it) and the scorecard test
prints a per-class P@5 / R@5 / Hit@1 table plus an overall score — the number to
grow toward the full brain-as-judge benchmark (500 Qs) later.
"""
from dataclasses import dataclass, field

import pytest

pytestmark = pytest.mark.ci_safe

from memory_service import MemoryService

USER = "demo_bench_user"        # DEMO user only
OTHER = "demo_bench_intruder"   # a second DEMO user, for isolation cases
_NEW = "2026-07-08T00:00:00Z"
_OLD = "2026-01-01T00:00:00Z"   # ~6 months older → time-decay + recency bite
K = 5                            # report P@K / R@K


# ── fixture model ─────────────────────────────────────────────────────────────
@dataclass
class Row:
    """One candidate Chroma row: (id, doc, distance) + full ranking metadata."""

    id: str
    text: str
    distance: float
    entity_id: str | None = None
    added_at: str = _NEW
    memory_type: str = "fact"
    user_id: str = USER
    visibility: str = "personal"
    status: str = "approved"
    confidence: float = 0.9
    access_count: int = 0
    expires_at: str | None = None

    def metadata(self) -> dict:
        md = {
            "user_id": self.user_id,
            "visibility": self.visibility,
            "status": self.status,
            "confidence": self.confidence,
            "added_at": self.added_at,
            "memory_type": self.memory_type,
            "access_count": self.access_count,
        }
        if self.entity_id is not None:
            md["entity_type"] = "person"
            md["entity_id"] = self.entity_id
        if self.expires_at is not None:
            md["expires_at"] = self.expires_at
        return md


class _FakeCollection:
    """Stand-in for the Chroma collection.

    Honours the ``$or`` user/visibility ``where`` clause the way real Chroma
    does, so the DB-level scoping is modelled too — but ``_semantic_search`` also
    re-checks visibility per row, which is the layer the isolation class targets.
    """

    def __init__(self, rows: list[Row]):
        self._rows = rows

    def query(self, **kwargs):
        rows = self._rows
        where = kwargs.get("where") or {}
        clauses = where.get("$or")
        if clauses:
            def _match(r: Row) -> bool:
                for c in clauses:
                    if c.get("user_id") == r.user_id:
                        return True
                    if c.get("wing") == r.user_id:
                        return True
                    if c.get("visibility") and c["visibility"] == r.visibility:
                        return True
                return False

            rows = [r for r in rows if _match(r)]
        return {
            "ids": [[r.id for r in rows]],
            "documents": [[r.text for r in rows]],
            "metadatas": [[r.metadata() for r in rows]],
            "distances": [[r.distance for r in rows]],
        }

    def get(self, **kwargs):  # pragma: no cover
        return {}


def _service(rows: list[Row]) -> MemoryService:
    svc = MemoryService(data_dir="/tmp/zoe-bench")  # DEMO/isolated; never touched
    svc._collection = lambda: _FakeCollection(rows)
    return svc


def _pr_at_k(rows, relevant, k=K):
    """Standard Precision@k / Recall@k for a single query."""
    top = [r.id for r in rows[:k]]
    hits = len(set(top) & set(relevant))
    precision = hits / k
    recall = hits / len(relevant) if relevant else 0.0
    return precision, recall


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    # Turn the hybrid + graph signals ON for the benchmark run (test-process env
    # only; never prod). OFF would still rank, just without the 2a/2b boosts.
    monkeypatch.setenv("ZOE_HYBRID_RETRIEVAL_ENABLED", "1")
    monkeypatch.setenv("ZOE_GRAPH_RECALL_WEIGHT", "0.30")
    yield


# ── the benchmark corpus ──────────────────────────────────────────────────────
@dataclass
class Case:
    cls: str
    query: str
    rows: list[Row]
    relevant: set[str]
    depth_by_pid: dict[str, int] | None = None
    k: int = K
    note: str = ""


def _distractors(prefix: str, texts: list[str], dist: float) -> list[Row]:
    return [Row(f"{prefix}{i}", t, dist) for i, t in enumerate(texts)]


CASES: list[Case] = [
    # ── single-hop: the one directly-relevant fact must top the list ──────────
    Case(
        "single-hop", "what is my locker code",
        [Row("s1_ans", "My locker code is beef42", 0.22)]
        + _distractors("s1_d", [
            "The gym has new lockers", "A code review is scheduled",
            "The bike lock combination changed", "Room 4 needs a keycard",
            "The safe code was reset last year",
        ], 0.45),
        {"s1_ans"},
    ),
    Case(
        "single-hop", "what is my wifi password",
        [Row("s2_ans", "The wifi password is hunter2sky", 0.20)]
        + _distractors("s2_d", [
            "The router is in the hallway", "A guest network exists",
            "Broadband is billed monthly", "The password manager is locked",
            "The modem restarts nightly",
        ], 0.44),
        {"s2_ans"},
    ),

    # ── temporal: most-recent-wins when a fact is updated/superseded ──────────
    Case(
        "temporal", "where do I live",
        [Row("t1_new", "I live in San Francisco", 0.30, added_at=_NEW),
         Row("t1_old", "I live in New York", 0.30, added_at=_OLD)],
        {"t1_new"}, note="fresh address supersedes the stale one at equal distance",
    ),
    Case(
        "temporal", "what is my phone number",
        [Row("t2_new", "My phone number is 555-0powerNEW", 0.28, added_at=_NEW),
         Row("t2_old", "My phone number is 555-0oldNUMB", 0.28, added_at=_OLD)],
        {"t2_new"}, note="updated number wins over the superseded one",
    ),

    # ── multi-hop: the answer is 1 graph hop from the named person ────────────
    # Distractors are semantically NEARER than the target and share no query
    # keywords, and none are graph-connected — so only the graph adjacency of the
    # connected fact (Bob) can lift it into top-k. depth_by_pid mirrors the
    # seeded people-graph the async search() caller passes in prod.
    Case(
        "multi-hop", "what is Alice's husband's job",
        [Row("m1_ans", "Bob works as a marine biologist", 0.75,
             entity_id="pid_bob", memory_type="person")]
        + _distractors("m1_d", [
            "The weather is pleasant today", "Someone left a parcel by the door",
            "The car is due for a service", "Tuesday is bin collection",
            "The kettle needs descaling", "A film premieres this weekend",
        ], 0.50),
        {"m1_ans"}, depth_by_pid={"pid_alice": 0, "pid_bob": 1},
    ),
    Case(
        "multi-hop", "where does my sister work",
        [Row("m2_ans", "Priya leads the robotics lab", 0.72,
             entity_id="pid_priya", memory_type="person")]
        + _distractors("m2_d", [
            "The garden needs watering", "A parcel arrived yesterday",
            "The heating is on a timer", "Recycling goes out Thursday",
            "The stapler is out of staples", "A concert is next month",
        ], 0.50),
        {"m2_ans"}, depth_by_pid={"pid_me": 0, "pid_priya": 1},
    ),

    # ── isolation: another user's rows must NEVER surface for the caller ──────
    Case(
        "isolation", "what is the launch code",
        [Row("i1_ans", "My launch code is ZOE-ALPHA", 0.25),
         Row("i1_leak", "My launch code is ENEMY-OMEGA", 0.15, user_id=OTHER),
         Row("i1_leak2", "The launch code rotates weekly", 0.18, user_id=OTHER)],
        {"i1_ans"}, note="nearer intruder rows must be filtered before ranking",
    ),
    Case(
        "isolation", "what is my bank pin",
        [Row("i2_ans", "My bank pin is 8842", 0.30),
         Row("i2_leak", "My bank pin is 0000", 0.10, user_id=OTHER)],
        {"i2_ans"}, note="a far-nearer intruder row must not win",
    ),

    # ── dedup: the canonical fact beats a near-duplicate / stale variant ──────
    Case(
        "dedup", "what is my locker code",
        [Row("d1_ans", "My locker code is beef42", 0.24, added_at=_NEW),
         Row("d1_dup", "My old locker code was cafe11", 0.30, added_at=_OLD)],
        {"d1_ans"}, note="canonical current value beats the stale near-duplicate",
    ),
    Case(
        "dedup", "what is my email address",
        [Row("d2_ans", "My email is jay@current.example", 0.26, added_at=_NEW),
         Row("d2_dup", "My email was jay@former.example", 0.28, added_at=_OLD)],
        {"d2_ans"}, note="current email beats the former near-duplicate",
    ),
]


def _run(case: Case):
    svc = _service(case.rows)
    return svc._semantic_search(case.query, USER, limit=case.k,
                                depth_by_pid=case.depth_by_pid)


# ── focused per-class assertions (a regression trips exactly one) ─────────────
def test_single_hop_recall():
    for c in [x for x in CASES if x.cls == "single-hop"]:
        rows = _run(c)
        p, r = _pr_at_k(rows, c.relevant, k=1)
        assert rows and rows[0].id in c.relevant, f"{c.query!r}: {[x.id for x in rows]}"
        assert r == 1.0


def test_temporal_most_recent_wins():
    for c in [x for x in CASES if x.cls == "temporal"]:
        order = [r.id for r in _run(c)]
        newer = next(iter(c.relevant))
        older = next(r.id for r in c.rows if r.id != newer)
        assert order.index(newer) < order.index(older), f"{c.query!r}: {order}"


def test_multi_hop_graph_recall():
    for c in [x for x in CASES if x.cls == "multi-hop"]:
        on = _run(c)
        off = _service(c.rows)._semantic_search(c.query, USER, limit=c.k, depth_by_pid=None)
        _, on_r = _pr_at_k(on, c.relevant)
        _, off_r = _pr_at_k(off, c.relevant)
        # graph adjacency must lift the connected fact into top-k where distance
        # alone (OFF) buried it below the nearer, unconnected distractors.
        assert on_r == 1.0 and on_r > off_r, f"{c.query!r}: ON={on_r} OFF={off_r}"


def test_isolation_no_cross_user_leak():
    for c in [x for x in CASES if x.cls == "isolation"]:
        rows = _run(c)
        ids = [r.id for r in rows]
        leaked = [r.id for r in rows
                  if str(r.metadata.get("user_id")) not in (USER,)
                  and str(r.metadata.get("visibility")) != "family"]
        assert not leaked, f"{c.query!r}: cross-user leak {leaked}"
        assert set(ids) & c.relevant == c.relevant, f"{c.query!r}: caller row missing {ids}"


def test_dedup_canonical_beats_near_duplicate():
    for c in [x for x in CASES if x.cls == "dedup"]:
        order = [r.id for r in _run(c)]
        canonical = next(iter(c.relevant))
        dup = next(r.id for r in c.rows if r.id != canonical)
        assert order.index(canonical) < order.index(dup), f"{c.query!r}: {order}"


# ── the scorecard: per-class P@5 / R@5 / Hit@1 + overall (the growable number) ─
def test_benchmark_scorecard(capsys):
    classes = ["single-hop", "temporal", "multi-hop", "isolation", "dedup"]
    per_class: dict[str, list[tuple[float, float, int]]] = {c: [] for c in classes}

    for c in CASES:
        rows = _run(c)
        p, r = _pr_at_k(rows, c.relevant, k=K)
        hit1 = 1 if rows and rows[0].id in c.relevant else 0
        per_class[c.cls].append((p, r, hit1))

    def _mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    print(f"\n{'='*58}")
    print(f"  MEMORY RECALL BENCHMARK  (LongMemEval/LOCOMO-style, k={K})")
    print(f"{'='*58}")
    print(f"  {'class':<12} {'n':>2}  {'P@'+str(K):>6} {'R@'+str(K):>6} {'Hit@1':>6}")
    print(f"  {'-'*44}")
    all_p, all_r, all_h = [], [], []
    for cls in classes:
        vals = per_class[cls]
        ps = [v[0] for v in vals]; rs = [v[1] for v in vals]; hs = [v[2] for v in vals]
        all_p += ps; all_r += rs; all_h += hs
        print(f"  {cls:<12} {len(vals):>2}  {_mean(ps):>6.2f} {_mean(rs):>6.2f} {_mean(hs):>6.2f}")
    print(f"  {'-'*44}")
    print(f"  {'OVERALL':<12} {len(all_p):>2}  "
          f"{_mean(all_p):>6.2f} {_mean(all_r):>6.2f} {_mean(all_h):>6.2f}")
    print(f"{'='*58}\n")

    # Floors: every class must recall all its gold in top-k, and never miss the
    # top-1 answer. Grow the corpus, not the floors, toward the full benchmark.
    for cls in classes:
        rs = [v[1] for v in per_class[cls]]
        hs = [v[2] for v in per_class[cls]]
        assert _mean(rs) == 1.0, f"{cls}: R@{K}={_mean(rs):.2f} < 1.0"
        assert _mean(hs) == 1.0, f"{cls}: Hit@1={_mean(hs):.2f} < 1.0"
    assert _mean(all_r) == 1.0 and _mean(all_h) == 1.0
