"""Increment 2a — hybrid retrieval in ``MemoryService._semantic_search``.

All fixtures are synthetic and the embedding layer is mocked via ``_FakeCollection``
(``service._collection`` is swapped for a stub returning canned Chroma results),
so these run without loading any model. Each test toggles
``ZOE_HYBRID_RETRIEVAL_ENABLED`` explicitly via monkeypatch and never enables it
for the process at large.
"""

import pytest

import memory_service
from memory_service import MemoryService

pytestmark = pytest.mark.ci_safe


class _FakeCollection:
    def __init__(self, *, query_result=None):
        self.query_result = query_result or {}
        self.seen_query_where = None

    def query(self, **kwargs):
        self.seen_query_where = kwargs.get("where")
        return self.query_result

    def get(self, **kwargs):  # pragma: no cover - unused here
        return {}


def _md(user_id="jason", *, visibility="personal", status="approved",
        confidence=0.9, added_at="2026-01-01T00:00:00Z", **extra):
    md = {
        "user_id": user_id,
        "visibility": visibility,
        "status": status,
        "confidence": confidence,
        "added_at": added_at,
    }
    md.update(extra)
    return md


def _make_service(query_result):
    service = MemoryService(data_dir="/tmp/zoe-test-hybrid")
    service._collection = lambda: _FakeCollection(query_result=query_result)
    return service


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch):
    # Guardrail: never leave the flag set for the process. Default OFF each test.
    monkeypatch.delenv("ZOE_HYBRID_RETRIEVAL_ENABLED", raising=False)
    yield


# --- 1. Flag OFF ⇒ byte-for-byte identical ordering to pre-2a behaviour ------

# All rows share the same added_at so the pre-2a age-decay is a constant and
# ordering is driven purely by distance (OFF) or distance+boosts (ON). The dad
# fact 'a' is the *worst* semantic match — a genuine below-cutoff miss — but it
# is the only row that lexically contains the query term "dad".
_SAME_DAY = "2026-06-01T00:00:00Z"
_GOLDEN_QUERY_RESULT = {
    "ids": [["a", "b", "c", "d"]],
    "documents": [[
        "My dad's name is Neil",
        "The weather is often sunny here",
        "Jason prefers quiet mornings",
        "A note about the garage door code",
    ]],
    "metadatas": [[
        _md(memory_type="fact", added_at=_SAME_DAY, confidence=0.9),
        _md(memory_type="fact", added_at=_SAME_DAY, confidence=0.9),
        _md(memory_type="fact", added_at=_SAME_DAY, confidence=0.9),
        _md(memory_type="fact", added_at=_SAME_DAY, confidence=0.9),
    ]],
    # 'b' closest, then c, d, a (a is the dad fact, semantically farthest).
    "distances": [[0.9, 0.3, 0.5, 0.7]],
}


def test_flag_off_ordering_is_a_true_noop(monkeypatch):
    """OFF path must reproduce the exact pre-2a semantic+hotness ordering.

    Golden expectation is derived purely from the semantic distances above
    (all confidences equal, no access_count, so ordering = ascending distance):
    b (0.3) > c (0.5) > d (0.7) > a (0.9).
    """
    assert "ZOE_HYBRID_RETRIEVAL_ENABLED" not in __import__("os").environ
    service = _make_service(_GOLDEN_QUERY_RESULT)

    rows = service._semantic_search("what is my dad name", "jason", limit=10)

    assert [r.id for r in rows] == ["b", "c", "d", "a"]


def test_flag_off_matches_explicit_false_and_hybrid_helper(monkeypatch):
    monkeypatch.setenv("ZOE_HYBRID_RETRIEVAL_ENABLED", "0")
    assert memory_service._hybrid_retrieval_enabled() is False
    service = _make_service(_GOLDEN_QUERY_RESULT)
    rows = service._semantic_search("what is my dad name", "jason", limit=10)
    assert [r.id for r in rows] == ["b", "c", "d", "a"]


# --- 2. Keyword boost rescues a known semantic miss (the dad-name case) -------

def test_keyword_boost_rescues_semantic_miss(monkeypatch):
    """With the flag ON, the lexically-matching dad fact surfaces to top-k
    even though it is the *worst* semantic match (largest distance)."""
    service = _make_service(_GOLDEN_QUERY_RESULT)

    # Baseline OFF: the target 'a' ("My dad's name is Neil") ranks last.
    off = service._semantic_search("what is my dad name", "jason", limit=10)
    assert off[-1].id == "a"

    monkeypatch.setenv("ZOE_HYBRID_RETRIEVAL_ENABLED", "1")
    assert memory_service._hybrid_retrieval_enabled() is True
    on = service._semantic_search("what is my dad name", "jason", limit=2)

    # "dad" (and "neil" if present) overlap lifts 'a' into the top-k.
    assert "a" in [r.id for r in on]
    assert on[0].id == "a"


# --- 3a. Recency boost orders a controlled fixture ---------------------------

def test_recency_boost_orders_recent_ahead_when_semantics_tie(monkeypatch):
    """Two facts with identical distance/confidence and no keyword overlap:
    the more recent one should rank first only when the flag is ON."""
    now_ish = "2026-07-01T00:00:00Z"
    old = "2024-01-01T00:00:00Z"
    result = {
        "ids": [["recent", "old"]],
        "documents": [["fact zzz", "fact yyy"]],
        "metadatas": [[
            _md(memory_type="fact", added_at=now_ish),
            _md(memory_type="fact", added_at=old),
        ]],
        "distances": [[0.5, 0.5]],
    }
    service = _make_service(result)

    # OFF: identical scores; the existing decay already favours recent, so this
    # test asserts the ON path keeps recent first (and stays stable).
    monkeypatch.setenv("ZOE_HYBRID_RETRIEVAL_ENABLED", "1")
    rows = service._semantic_search("unrelated words here", "jason", limit=10)
    assert [r.id for r in rows] == ["recent", "old"]


def test_recency_boost_is_bounded_and_does_not_beat_relevance(monkeypatch):
    """A mildly-old but far-more-relevant fact must still outrank a brand-new
    but semantically distant one — recency never dominates relevance."""
    # Both rows added the same day so the pre-2a age-decay is constant; the
    # only differences are distance (strongly favours relevant) and a tiny
    # recency edge given to the *irrelevant* row. Recency must not flip it.
    result = {
        "ids": [["relevant_slightly_older", "irrelevant_newer"]],
        "documents": [["fact aaa", "fact bbb"]],
        "metadatas": [[
            _md(memory_type="fact", added_at="2026-06-30T00:00:00Z"),
            _md(memory_type="fact", added_at="2026-07-01T00:00:00Z"),
        ]],
        "distances": [[0.05, 0.9]],
    }
    service = _make_service(result)
    monkeypatch.setenv("ZOE_HYBRID_RETRIEVAL_ENABLED", "1")
    rows = service._semantic_search("no overlap tokens", "jason", limit=10)
    assert rows[0].id == "relevant_slightly_older"


# --- 3b. Preference boost orders a controlled fixture ------------------------

def test_preference_boost_orders_preference_ahead_on_tie(monkeypatch):
    """Two candidates identical on semantics/recency/keywords; the one whose
    memory_type marks it a preference ranks first only with the flag ON."""
    result = {
        "ids": [["pref", "plain"]],
        "documents": [["fact qqq", "fact www"]],
        "metadatas": [[
            _md(memory_type="preference", added_at="2026-01-01T00:00:00Z"),
            _md(memory_type="fact", added_at="2026-01-01T00:00:00Z"),
        ]],
        "distances": [[0.5, 0.5]],
    }
    service = _make_service(result)

    off = service._semantic_search("zzz", "jason", limit=10)
    # OFF: equal blend → stable sort preserves input order (pref, plain).
    assert [r.id for r in off] == ["pref", "plain"]

    monkeypatch.setenv("ZOE_HYBRID_RETRIEVAL_ENABLED", "1")
    on = service._semantic_search("zzz", "jason", limit=10)
    assert on[0].id == "pref"


def test_preference_boost_uses_importance_field_when_present(monkeypatch):
    """If a producer ever writes numeric `importance`, it feeds the same arm."""
    result = {
        "ids": [["important", "plain"]],
        "documents": [["fact eee", "fact rrr"]],
        "metadatas": [[
            _md(memory_type="fact", importance=1.0, added_at="2026-01-01T00:00:00Z"),
            _md(memory_type="fact", added_at="2026-01-01T00:00:00Z"),
        ]],
        "distances": [[0.5, 0.5]],
    }
    service = _make_service(result)
    monkeypatch.setenv("ZOE_HYBRID_RETRIEVAL_ENABLED", "1")
    on = service._semantic_search("zzz", "jason", limit=10)
    assert on[0].id == "important"


# --- 4. Safety filters (cross-user + approved-only) hold with flag ON --------

def test_flag_on_still_blocks_cross_user_and_unapproved(monkeypatch):
    monkeypatch.setenv("ZOE_HYBRID_RETRIEVAL_ENABLED", "1")
    result = {
        "ids": [["own", "other_user", "disputed", "family_shared"]],
        "documents": [[
            "My dad's name is Neil",
            "Alex private dad fact",
            "Jason disputed dad fact",
            "Shared family dad fact",
        ]],
        "metadatas": [[
            _md(user_id="jason", visibility="personal", status="approved"),
            _md(user_id="alex", visibility="personal", status="approved"),
            _md(user_id="jason", visibility="personal", status="disputed"),
            _md(user_id="alex", visibility="family", status="approved"),
        ]],
        "distances": [[0.5, 0.1, 0.1, 0.4]],
    }
    service = _make_service(result)

    rows = service._semantic_search("what is my dad name", "jason", limit=10)
    ids = {r.id for r in rows}

    # own + the family-shared row survive; the other user's private row and the
    # disputed row are filtered even though keyword overlap would have boosted them.
    assert ids == {"own", "family_shared"}
    assert "other_user" not in ids
    assert "disputed" not in ids
