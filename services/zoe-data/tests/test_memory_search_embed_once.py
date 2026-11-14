"""Memory search embeds the query text ONCE (speed audit 2026-11-14 A4).

``_semantic_search`` runs an unfiltered over-fetch and, when the owner's visible rows are
still fewer than ``limit``, an owner-filtered supplement. Both used ``query_texts=[query]``,
so chroma ran the MiniLM on the same text twice. The search now embeds once with the
collection's own embedding function and hands the vector to both queries.

Proof of equivalence, on a REAL chroma collection (in-memory, no model loaded): a
deterministic embedding function that counts its calls stands in for the MiniLM. The same
search runs twice, once on the old path (``_query_embeddings_once`` forced to None, which
is exactly the old ``query_texts`` code) and once on the new path; the returned memories,
their order, texts, metadata and scores must be identical, and the embedder must be called
once instead of twice.
"""
from __future__ import annotations

import hashlib
import uuid

import pytest

import memory_service
from memory_service import MemoryService

pytestmark = pytest.mark.ci_safe

chromadb = pytest.importorskip("chromadb")
np = pytest.importorskip("numpy")
from chromadb.api.types import EmbeddingFunction  # noqa: E402

DIM = 16


class _CountingEF(EmbeddingFunction):
    """Deterministic unit vectors from a text hash; counts every call (and every text)."""

    def __init__(self):
        self.calls = 0
        self.texts = 0

    def __call__(self, input):
        self.calls += 1
        self.texts += len(input)
        out = []
        for t in input:
            raw = np.frombuffer(hashlib.sha256(str(t).encode()).digest()[:DIM], dtype=np.uint8).astype(np.float32)
            raw = raw - 127.5
            out.append((raw / np.linalg.norm(raw)).astype(np.float32))
        return out

    @staticmethod
    def name() -> str:
        return "zoe-test-counting"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return _CountingEF()


def _collection(rows):
    ef = _CountingEF()
    client = chromadb.EphemeralClient()
    col = client.create_collection(f"t{uuid.uuid4().hex}", embedding_function=ef)
    col.add(ids=[r[0] for r in rows], documents=[r[1] for r in rows], metadatas=[r[2] for r in rows])
    ef.calls = ef.texts = 0      # count only what the search does
    return col, ef


def _svc(col):
    svc = MemoryService(data_dir="/nonexistent/zoe-test-embed-once")
    svc._collection = lambda: col
    return svc


def _rows(n_other, n_owner, owner="member_a"):
    rows = []
    for i in range(n_other):
        rows.append((f"o{i}", f"other household note number {i}", {"user_id": f"other_{i % 3}", "status": "approved",
                                                                    "confidence": 0.7}))
    for i in range(n_owner):
        rows.append((f"m{i}", f"the member's own memory {i} about the garden", {"user_id": owner, "status": "approved",
                                                                                  "confidence": 0.8}))
    rows.append(("f0", "a family-visible note about dinner", {"user_id": "other_0", "visibility": "family",
                                                              "status": "approved", "confidence": 0.75}))
    return rows


def _flat(hits):
    return [(h.id, h.text, sorted(h.metadata.items()), h.score) for h in hits]


def _run_both(monkeypatch, rows, query, user, limit):
    col, ef = _collection(rows)
    svc = _svc(col)
    calls = []
    real_query = col.query

    def spy(**kw):
        calls.append(sorted(k for k in kw if k.startswith("query_")))
        return real_query(**kw)

    monkeypatch.setattr(col, "query", spy)

    with monkeypatch.context() as m:
        m.setattr(memory_service, "_query_embeddings_once", lambda c, q: None)   # the old two-text-query path
        old = svc._semantic_search(query, user, limit)
    old_calls, old_kinds = ef.calls, list(calls)
    ef.calls = 0
    calls.clear()
    new = svc._semantic_search(query, user, limit)
    return old, new, old_calls, ef.calls, old_kinds, list(calls)


@pytest.mark.parametrize(
    "n_other,n_owner,limit",
    [
        (5, 2, 5),       # few owner rows: the supplement fires and adds nothing new
        (260, 3, 5),     # owner rows pushed out of the 200-row over-fetch: the supplement ADDS them
        (40, 30, 5),     # plenty of owner rows: no supplement, one query
    ],
)
@pytest.mark.parametrize("query", ["what do I know about the garden", "dinner", "?"])
def test_results_identical_and_the_query_is_embedded_once(monkeypatch, n_other, n_owner, limit, query):
    old, new, old_embeds, new_embeds, old_kinds, new_kinds = _run_both(
        monkeypatch, _rows(n_other, n_owner), query, "member_a", limit)
    assert _flat(new) == _flat(old)
    assert new, "the scenario must return something, or the comparison proves nothing"
    assert new_embeds == 1
    assert all(k == ["query_embeddings"] for k in new_kinds)
    assert all(k == ["query_texts"] for k in old_kinds)
    assert len(new_kinds) == len(old_kinds)
    assert old_embeds == len(old_kinds)     # the old path embedded once PER query


def test_a_supplemented_search_used_to_embed_twice(monkeypatch):
    """The measurement behind A4: 2 embeds before, 1 after, on a search that supplements."""
    old, new, old_embeds, new_embeds, old_kinds, _ = _run_both(
        monkeypatch, _rows(260, 3), "garden", "member_a", 5)
    assert len(old_kinds) == 2 and old_embeds == 2 and new_embeds == 1
    assert {h.id for h in new} >= {"m0", "m1", "m2"}   # the supplement found the owner's rows


def test_the_vector_is_the_one_chroma_would_compute():
    col, ef = _collection(_rows(3, 1))
    got = memory_service._query_embeddings_once(col, "the garden")
    want = col._embed(input=["the garden"], is_query=True)      # chroma's own path for query_texts
    assert len(got) == len(want) == 1
    assert got[0].dtype == want[0].dtype and got[0].tobytes() == want[0].tobytes()


def test_a_collection_without_a_chroma_embedding_function_keeps_query_texts():
    class _Fake:
        _embedding_function = object()

    assert memory_service._query_embeddings_once(_Fake(), "q") is None
    assert memory_service._query_embeddings_once(object(), "q") is None


def test_the_palace_embedding_function_takes_the_embed_once_path():
    """The live palace EF (``_ZoeMiniLM``) subclasses chroma's ``ONNXMiniLM_L6_V2``: that class must be
    an ``EmbeddingFunction`` and NOT the ``DefaultEmbeddingFunction``, or the helper would return None in
    production and this change would do nothing. Class check only: no model is loaded."""
    from chromadb.api.types import DefaultEmbeddingFunction
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    assert issubclass(ONNXMiniLM_L6_V2, EmbeddingFunction)
    assert not issubclass(ONNXMiniLM_L6_V2, DefaultEmbeddingFunction)


def test_chromas_default_embedding_function_keeps_query_texts():
    from chromadb.api.types import DefaultEmbeddingFunction

    class _Fake:
        _embedding_function = DefaultEmbeddingFunction()

    assert memory_service._query_embeddings_once(_Fake(), "q") is None
