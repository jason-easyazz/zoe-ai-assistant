"""Increment 2b — multi-hop graph-adjacency recall boost eval (proves Part B).

This is the benchmark that earns the ``ZOE_GRAPH_RECALL_BOOST`` flag flip: it
shows that blending a relationship-graph adjacency signal into
``MemoryService._semantic_search`` lifts facts stored under *connected* people
(1–2 hops from the person a query is about) into the top-k, exactly the
multi-hop win vector distance alone misses.

Everything is synthetic and DEMO-user only. The embedding layer is a
``_FakeCollection`` returning canned Chroma distances, so no model loads and
nothing touches Postgres. The graph neighbourhood is injected directly as the
``depth_by_pid`` dict (the same plain dict the async ``search`` caller builds via
``relationship_graph.neighbors``), and a separate test stubs the resolver +
``neighbors`` to prove the async wiring + the default-OFF gate.

Scenario (a seeded people-graph for DEMO user ``demo_graph_user``):

    Alice (sister, the person queries name)  ── pid_alice   depth 0
      └─ Bob (Alice's husband)               ── pid_bob     depth 1
           └─ Carol (Bob's employer)         ── pid_carol   depth 2

Facts about Bob's job live on ``pid_bob`` and facts about the employer live on
``pid_carol`` — NOT on Alice — so a query naming Alice only reaches them through
the graph.

Slim-dep → GitHub ``-m ci_safe`` lane.
"""

import pytest

import memory_service
from memory_service import MemoryService

pytestmark = pytest.mark.ci_safe

USER = "demo_graph_user"  # a DEMO user — never a real person

# People-graph node ids (people.id values the graph BFS would return).
PID_ALICE = "pid_alice"     # sister — the person queries name (depth 0)
PID_BOB = "pid_bob"         # Alice's husband (depth 1)
PID_CAROL = "pid_carol"     # Bob's employer (depth 2)

# The neighbourhood the async caller would build for a query about Alice.
DEPTH_BY_PID = {PID_ALICE: 0, PID_BOB: 1, PID_CAROL: 2}

# All facts share one recent timestamp so the pre-existing age-decay is a common
# multiplicative factor on the semantic term (it never reorders equal-dated docs)
# and OFF ordering is driven purely by distance — stable regardless of run date.
_SAME_DAY = "2026-07-08T00:00:00Z"

# A fixed, explicit boost weight so the eval is deterministic even if the default
# constant is retuned later.
_GRAPH_WEIGHT = "0.30"


class _FakeCollection:
    """Canned Chroma collection keyed by query text.

    ``query(query_texts=[q])`` returns the pre-baked candidate set for ``q``.
    The ON vs OFF comparison changes only ``depth_by_pid`` (passed into
    ``_semantic_search``), never the candidate set, so the fake ignores every
    kwarg except the query text.
    """

    def __init__(self, by_query):
        self._by_query = by_query

    def query(self, **kwargs):
        q = (kwargs.get("query_texts") or [""])[0]
        return self._by_query[q]

    def get(self, **kwargs):  # pragma: no cover - unused
        return {}


def _md(entity_id=None, *, confidence=0.9):
    md = {
        "user_id": USER,
        "visibility": "personal",
        "status": "approved",
        "confidence": confidence,
        "added_at": _SAME_DAY,
        "memory_type": "fact",
    }
    if entity_id is not None:
        md["entity_type"] = "person"
        md["entity_id"] = entity_id
    return md


def _result(rows):
    """rows: list of (id, doc, entity_id, distance) → a Chroma query result."""
    return {
        "ids": [[r[0] for r in rows]],
        "documents": [[r[1] for r in rows]],
        "metadatas": [[_md(r[2]) for r in rows]],
        "distances": [[r[3] for r in rows]],
    }


# ── Fixtures: 3 queries with ground-truth relevant ids ────────────────────────
#
# Distractors are semantically CLOSER (smaller distance) than the graph target,
# so vector-only search ranks the target out of the top-5. None of the
# distractors belong to Alice's graph neighbourhood, so the boost never touches
# them.

# Q1 — 1-hop: answer lives on Bob (Alice's husband, depth 1).
Q1 = "What is Alice's husband's job?"
Q1_TARGET = "bob_job"
Q1_ROWS = [
    (Q1_TARGET, "Bob works as a marine biologist", PID_BOB, 0.75),
    ("d1", "The job market is competitive downtown", None, 0.50),
    ("d2", "Her husband enjoys weekend hikes", None, 0.52),
    ("d3", "A note about a job application deadline", None, 0.54),
    ("d4", "The office job fair is next week", None, 0.56),
    ("d5", "Someone asked about a part-time job", None, 0.58),
    ("d6", "A husband-and-wife bakery opened nearby", None, 0.60),
]

# Q2 — 2-hop: answer lives on Carol (Bob's employer, depth 2).
Q2 = "Who does Alice's husband work for?"
Q2_TARGET = "carol_employer"
Q2_ROWS = [
    (Q2_TARGET, "Carol owns the coastal research lab", PID_CAROL, 0.75),
    ("e1", "He works remotely most Fridays", None, 0.50),
    ("e2", "The company works with several vendors", None, 0.52),
    ("e3", "A note about who works the late shift", None, 0.54),
    ("e4", "Alice works out every morning", None, 0.56),
    ("e5", "They work well as a team", None, 0.58),
    ("e6", "The network works after the reboot", None, 0.60),
]

# Q3 — no-regression: a direct hit about Alice herself (depth 0). Turning the
# boost ON must NOT demote it below where vector-only search placed it.
Q3 = "What does Alice do?"
Q3_TARGET = "alice_job"
Q3_ROWS = [
    (Q3_TARGET, "Alice is a pediatric nurse", PID_ALICE, 0.20),
    ("bob_law", "Bob is a lawyer", PID_BOB, 0.30),   # a depth-1 neighbour fact
    ("f1", "What Alice does on weekends is garden", None, 0.35),
    ("f2", "A note about what to do this evening", None, 0.40),
    ("f3", "The to-do list has three items", None, 0.45),
]

_BY_QUERY = {
    Q1: _result(Q1_ROWS),
    Q2: _result(Q2_ROWS),
    Q3: _result(Q3_ROWS),
}

# Ground-truth relevant ids per query (for P@k / R@k).
_RELEVANT = {
    Q1: {Q1_TARGET},
    Q2: {Q2_TARGET},
    Q3: {Q3_TARGET},
}

# The multi-hop set the boost is designed to fix (excludes the direct hit).
_MULTIHOP = [Q1, Q2]


def _make_service():
    service = MemoryService(data_dir="/tmp/zoe-test-graph-recall")
    service._collection = lambda: _FakeCollection(_BY_QUERY)
    return service


def _rank(rows, target_id):
    for i, r in enumerate(rows):
        if r.id == target_id:
            return i  # 0-based
    return None


def _precision_recall_at_k(service, queries, *, depth_by_pid, k=5):
    """Mean P@k / R@k over ``queries`` for a given depth_by_pid (None ⇒ OFF)."""
    precisions, recalls = [], []
    for q in queries:
        rows = service._semantic_search(q, USER, limit=k, depth_by_pid=depth_by_pid)
        top = {r.id for r in rows[:k]}
        rel = _RELEVANT[q]
        hits = len(top & rel)
        precisions.append(hits / k)
        recalls.append(hits / len(rel))
    return (sum(precisions) / len(precisions), sum(recalls) / len(recalls))


@pytest.fixture(autouse=True)
def _clear_flags(monkeypatch):
    # Guardrail: this eval drives the ranking directly via `depth_by_pid`, so no
    # env flag should be needed; keep the process clean either way.
    monkeypatch.delenv("ZOE_HYBRID_RETRIEVAL_ENABLED", raising=False)
    monkeypatch.delenv("ZOE_GRAPH_RECALL_BOOST", raising=False)
    monkeypatch.delenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", raising=False)
    monkeypatch.setenv("ZOE_GRAPH_RECALL_WEIGHT", _GRAPH_WEIGHT)
    yield


# ── 1. The headline eval: P@5 / R@5 improve on the multi-hop set ──────────────

def test_multihop_precision_recall_improves_with_boost(capsys):
    service = _make_service()

    p_off, r_off = _precision_recall_at_k(service, _MULTIHOP, depth_by_pid=None)
    p_on, r_on = _precision_recall_at_k(service, _MULTIHOP, depth_by_pid=DEPTH_BY_PID)

    # Print the ON-vs-OFF numbers (visible with `pytest -s`).
    print(
        f"\n[graph-recall multi-hop eval] "
        f"P@5 OFF={p_off:.3f} ON={p_on:.3f} | R@5 OFF={r_off:.3f} ON={r_on:.3f}"
    )

    # OFF: the multi-hop targets are genuine vector-only misses (out of top-5).
    assert p_off == 0.0
    assert r_off == 0.0
    # ON: the graph boost pulls both connected-entity facts into the top-5.
    assert p_on > p_off
    assert r_on > r_off
    assert r_on == 1.0  # every multi-hop answer recovered


def test_each_multihop_target_enters_top5_only_with_boost():
    service = _make_service()
    for q, target in ((Q1, Q1_TARGET), (Q2, Q2_TARGET)):
        off = service._semantic_search(q, USER, limit=5, depth_by_pid=None)
        on = service._semantic_search(q, USER, limit=5, depth_by_pid=DEPTH_BY_PID)
        assert target not in {r.id for r in off}, f"{target} unexpectedly in OFF top-5"
        assert target in {r.id for r in on}, f"{target} missing from ON top-5"


# ── 2. No-regression: a direct hit is never demoted by the boost ──────────────

def test_direct_hit_not_demoted_by_boost():
    service = _make_service()
    off = service._semantic_search(Q3, USER, limit=10, depth_by_pid=None)
    on = service._semantic_search(Q3, USER, limit=10, depth_by_pid=DEPTH_BY_PID)

    off_rank = _rank(off, Q3_TARGET)
    on_rank = _rank(on, Q3_TARGET)

    # The directly-relevant fact is top-1 without the boost and must stay top-1
    # with it — the depth-0 boost is the largest, so a neighbour fact can never
    # leapfrog the person's own fact.
    assert off_rank == 0
    assert on_rank is not None
    assert on_rank <= off_rank, "boost demoted a directly-relevant fact"


# ── 3. OFF is byte-identical: empty/None/non-matching depth_by_pid = no-op ─────

def test_off_ordering_is_byte_identical_noop():
    service = _make_service()
    for q in (Q1, Q2, Q3):
        baseline = [r.id for r in service._semantic_search(q, USER, limit=10)]
        none_order = [
            r.id for r in service._semantic_search(q, USER, limit=10, depth_by_pid=None)
        ]
        empty_order = [
            r.id for r in service._semantic_search(q, USER, limit=10, depth_by_pid={})
        ]
        # A populated dict that matches no candidate must also be a pure no-op.
        nonmatch_order = [
            r.id
            for r in service._semantic_search(
                q, USER, limit=10, depth_by_pid={"pid_nobody": 0}
            )
        ]
        assert none_order == baseline
        assert empty_order == baseline
        assert nonmatch_order == baseline


# ── 3b. A malformed weight disables only the graph term, never the search ─────

def test_invalid_weight_does_not_drop_results(monkeypatch):
    """``ZOE_GRAPH_RECALL_WEIGHT=abc`` must not raise out of the blend (which
    search() would catch and turn into an empty result list). It falls back to
    the default weight and still returns the full ranked candidate set."""
    monkeypatch.setenv("ZOE_GRAPH_RECALL_WEIGHT", "not-a-number")
    service = _make_service()
    rows = service._semantic_search(Q1, USER, limit=10, depth_by_pid=DEPTH_BY_PID)
    # All candidates survive (no exception ⇒ no empty-list fallback) and the
    # boost still applied (default weight), so the target is present.
    assert {r.id for r in rows} == {r[0] for r in Q1_ROWS}
    assert Q1_TARGET in {r.id for r in rows[:5]}


# ── 4. Async wiring + the default-OFF gate (search → _graph_depth_by_pid) ──────

@pytest.mark.asyncio
async def test_graph_depth_by_pid_is_empty_when_flags_off(monkeypatch):
    """Both flags OFF (the production default) ⇒ empty neighbourhood, zero DB
    work: the boost never runs and search stays byte-identical to today."""
    import relationship_graph

    monkeypatch.delenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", raising=False)
    monkeypatch.delenv("ZOE_GRAPH_RECALL_BOOST", raising=False)

    # If the gate leaked, this would blow up — asserting no DB path is taken.
    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("neighbors() called while flags OFF")

    monkeypatch.setattr(relationship_graph, "neighbors", _boom)

    service = _make_service()
    depth = await service._graph_depth_by_pid(Q1, USER)
    assert depth == {}


@pytest.mark.asyncio
async def test_graph_depth_by_pid_builds_neighbourhood_when_flags_on(monkeypatch):
    """Both flags ON + stubbed resolver/neighbors ⇒ the async caller builds the
    exact ``{start:0, **neighbours}`` dict the blend consumes."""
    import contextlib

    import db_pool
    import memory_extractor
    import relationship_graph

    monkeypatch.setenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", "1")
    monkeypatch.setenv("ZOE_GRAPH_RECALL_BOOST", "1")

    @contextlib.asynccontextmanager
    async def _fake_db_ctx():
        yield object()  # a dummy db; the resolver + neighbors are stubbed

    async def _fake_resolve(name, user_id, db):
        return PID_ALICE if name == "Alice" and user_id == USER else None

    async def _fake_neighbors(db, user_id, start_pid, *, max_depth, limit):
        assert start_pid == PID_ALICE
        assert max_depth == 2 and limit == 32
        return [
            {"person_id": PID_BOB, "depth": 1, "name": "Bob"},
            {"person_id": PID_CAROL, "depth": 2, "name": "Carol"},
        ]

    monkeypatch.setattr(db_pool, "get_db_ctx", _fake_db_ctx)
    # boost now resolves via the ambiguity-safe resolver (fix 2026-07-09)
    monkeypatch.setattr(memory_extractor, "_resolve_unique_person_uuid", _fake_resolve)
    monkeypatch.setattr(relationship_graph, "neighbors", _fake_neighbors)

    service = _make_service()
    depth = await service._graph_depth_by_pid(Q1, USER)
    assert depth == DEPTH_BY_PID


@pytest.mark.asyncio
async def test_graph_depth_by_pid_best_effort_swallows_failures(monkeypatch):
    """A failure in the graph path must degrade to ``{}`` (no boost), never
    raise — a turn is never crashed or slowed by the boost."""
    import db_pool
    import relationship_graph

    monkeypatch.setenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", "1")
    monkeypatch.setenv("ZOE_GRAPH_RECALL_BOOST", "1")

    def _explode():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(db_pool, "get_db_ctx", _explode)

    service = _make_service()
    depth = await service._graph_depth_by_pid(Q1, USER)
    assert depth == {}


# ── 5. The query-name extractor (feeds the existing resolver, not new NLU) ─────

def test_candidate_person_names_extraction():
    # Precise capitalized pass: only the proper noun survives (relationship /
    # question words are lowercase and never captured).
    assert memory_service._candidate_person_names("What is Alice's husband's job?") == ["Alice"]
    assert memory_service._candidate_person_names("Who does Alice's husband work for?") == ["Alice"]
    # Sentence-opener / interrogative capitals are rejected; real names kept.
    assert memory_service._candidate_person_names("Tell me about Bob and Carol") == ["Bob", "Carol"]
    # No capitalized name ⇒ empty (the fallback below also yields no *resolvable*
    # name, but here every token is a stopword or common word).
    assert memory_service._candidate_person_names("what is the weather") == ["weather"]


def test_candidate_person_names_lowercase_voice_fallback():
    """A fully lowercase voice/STT transcript still surfaces the name first, so
    the graph boost isn't silently dead on the voice path (Greptile P1)."""
    names = memory_service._candidate_person_names("what is alice's husband's job?")
    # Capitalized pass finds nothing → case-insensitive fallback kicks in.
    assert "alice" in names
    assert names[0] == "alice"  # appears before the non-name tokens
    # The resolver returns None for non-name tokens, so their presence is inert;
    # what matters is the real name is present and tried first.
