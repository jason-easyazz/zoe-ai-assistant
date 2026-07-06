"""Audit rows must not cost an embedding — memory-pressure candidate #5.

``_append_audit_sync`` writes to the ``mempalace_audit`` collection, whose rows
are only ever metadata-filtered (``_delete_audit_for_user_sync``). Passing an
explicit constant embedding makes chromadb 0.6.3 skip the per-mutation ONNX
MiniLM inference (opensrc-verified: ``CollectionCommon.
_validate_and_prepare_upsert_request`` embeds only when ``embeddings is
None``). These tests lock the contract in with a fake collection — no chromadb
import, slim-dep.
"""
import pytest

pytestmark = pytest.mark.ci_safe

from memory_service import _AUDIT_NULL_EMBEDDING, MemoryService


class _FakeAuditCollection:
    def __init__(self):
        self.upserts = []

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)


@pytest.fixture()
def svc_with_fake_audit(tmp_path, monkeypatch):
    svc = MemoryService(data_dir=str(tmp_path))
    fake = _FakeAuditCollection()
    monkeypatch.setattr(svc, "_audit_collection", lambda: fake)
    return svc, fake


def test_audit_upsert_provides_explicit_embedding(svc_with_fake_audit):
    svc, fake = svc_with_fake_audit
    svc._append_audit_sync(
        "mem-1", "u1", "test-actor", "edit", {"a": 1}, {"a": 2}, "unit"
    )
    assert len(fake.upserts) == 1
    kw = fake.upserts[0]
    # Explicit embeddings → chroma never calls the embedding function.
    assert kw["embeddings"] == [_AUDIT_NULL_EMBEDDING]
    # The audit payload itself is unchanged.
    assert kw["documents"] == ["edit mem-1 by test-actor for u1"]
    md = kw["metadatas"][0]
    assert md["mempalace_id"] == "mem-1" and md["action"] == "edit"
    assert md["user_id"] == "u1" and md["reason"] == "unit"


def test_null_embedding_shape_is_stable():
    """384-dim (matches the existing MiniLM rows) and non-zero (valid under
    cosine/ip spaces, not just l2)."""
    assert len(_AUDIT_NULL_EMBEDDING) == 384
    assert any(v != 0.0 for v in _AUDIT_NULL_EMBEDDING)
