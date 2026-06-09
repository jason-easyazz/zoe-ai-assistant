import pytest

from memory_service import MemoryService, MemoryServiceError


class _FakeCollection:
    def __init__(self, *, get_result=None, query_result=None):
        self.get_result = get_result or {}
        self.query_result = query_result or {}
        self.seen_get_where = None
        self.seen_query_where = None

    def get(self, **kwargs):
        self.seen_get_where = kwargs.get("where")
        return self.get_result

    def query(self, **kwargs):
        self.seen_query_where = kwargs.get("where")
        return self.query_result


def test_build_metadata_preserves_candidate_source_excerpt_and_structured_metadata():
    metadata = MemoryService._build_metadata(
        user_id="jason",
        source="hindsight_retain_candidate",
        session_id=None,
        user_turn_id="mem_evt_candidate",
        memory_type="failure",
        confidence=0.8,
        status="pending",
        tags=["relational"],
        entity_type="zoe_memory_event",
        entity_id="weather_card",
        expires_at=None,
        source_excerpt="Weather failed\nEvidence: trace:weather:001",
        scope="project",
        extra_metadata={
            "event_id": "mem_evt_candidate",
            "scope": "project",
            "relationships": [{"relationship_type": "FAILED_ON"}],
            "evidence_refs": ["trace:weather:001"],
            "ignored": None,
            "status": "candidate_pending",
        },
        idem_key="idem",
    )

    assert metadata["source_excerpt"].startswith("Weather failed")
    assert metadata["scope"] == "project"
    assert metadata["visibility"] == "personal"
    assert metadata["event_id"] == "mem_evt_candidate"
    assert metadata["relationships"] == "[{\"relationship_type\":\"FAILED_ON\"}]"
    assert metadata["evidence_refs"] == "[\"trace:weather:001\"]"
    assert metadata["candidate_event_id"] == "mem_evt_candidate"
    assert metadata["candidate_scope"] == "project"
    assert metadata["candidate_relationships"] == "[{\"relationship_type\":\"FAILED_ON\"}]"
    assert metadata["candidate_evidence_refs"] == "[\"trace:weather:001\"]"
    assert "candidate_ignored" not in metadata
    assert metadata["status"] == "pending"
    assert metadata["candidate_status"] == "candidate_pending"


def test_build_metadata_maps_shared_scope_to_family_visibility():
    metadata = MemoryService._build_metadata(
        user_id="jason",
        source="hindsight_retain_candidate",
        session_id=None,
        user_turn_id="mem_evt_shared",
        memory_type="fact",
        confidence=0.8,
        status="pending",
        tags=[],
        entity_type=None,
        entity_id=None,
        expires_at=None,
        scope="shared",
    )

    assert metadata["scope"] == "shared"
    assert metadata["visibility"] == "family"


def test_build_metadata_promotes_metadata_scope_when_explicit_scope_missing():
    metadata = MemoryService._build_metadata(
        user_id="jason",
        source="hindsight_retain_candidate",
        session_id=None,
        user_turn_id="mem_evt_metadata_scope",
        memory_type="fact",
        confidence=0.8,
        status="pending",
        tags=[],
        entity_type=None,
        entity_id=None,
        expires_at=None,
        scope=None,
        extra_metadata={"scope": "project", "event_id": "mem_evt_metadata_scope"},
    )

    assert metadata["scope"] == "project"
    assert metadata["visibility"] == "personal"
    assert metadata["candidate_scope"] == "project"
    assert metadata["event_id"] == "mem_evt_metadata_scope"


def test_build_metadata_rejects_unknown_scope():
    with pytest.raises(MemoryServiceError, match="unsupported memory scope"):
        MemoryService._build_metadata(
            user_id="jason",
            source="hindsight_retain_candidate",
            session_id=None,
            user_turn_id="mem_evt_bad_scope",
            memory_type="fact",
            confidence=0.8,
            status="pending",
            tags=[],
            entity_type=None,
            entity_id=None,
            expires_at=None,
            scope="global",
        )


def test_build_metadata_rejects_blank_scope_when_explicit():
    with pytest.raises(MemoryServiceError, match="scope cannot be blank"):
        MemoryService._build_metadata(
            user_id="jason",
            source="hindsight_retain_candidate",
            session_id=None,
            user_turn_id="mem_evt_blank_scope",
            memory_type="fact",
            confidence=0.8,
            status="pending",
            tags=[],
            entity_type=None,
            entity_id=None,
            expires_at=None,
            scope="",
        )


@pytest.mark.asyncio
async def test_ingest_passes_first_class_event_metadata_to_writer():
    service = MemoryService(data_dir="/tmp/zoe-test-memory-service")
    seen = {}

    def fake_write(mem_id, text, metadata):
        seen["mem_id"] = mem_id
        seen["text"] = text
        seen["metadata"] = metadata

    async def fake_audit(**kwargs):
        seen["audit_after"] = kwargs["after"]

    service._write_row = fake_write
    service._append_audit = fake_audit

    ref = await service.ingest(
        "The weather card failed because duplicate voice queue emits raced.",
        user_id="jason",
        source="hindsight_retain_candidate",
        user_turn_id="mem_evt_candidate",
        memory_type="failure",
        confidence=0.8,
        status="pending",
        tags=["hindsight-retain-candidate"],
        scope="project",
        metadata={
            "event_id": "mem_evt_candidate",
            "scope": "project",
            "relationships": [{"relationship_type": "FAILED_ON"}],
            "evidence_refs": ["trace:weather:001"],
        },
    )

    assert ref is not None
    assert seen["metadata"]["scope"] == "project"
    assert seen["metadata"]["visibility"] == "personal"
    assert seen["metadata"]["event_id"] == "mem_evt_candidate"
    assert seen["metadata"]["relationships"] == "[{\"relationship_type\":\"FAILED_ON\"}]"
    assert seen["metadata"]["evidence_refs"] == "[\"trace:weather:001\"]"
    assert seen["audit_after"]["event_id"] == "mem_evt_candidate"


def test_build_metadata_defaults_keep_review_edit_call_compatible():
    metadata = MemoryService._build_metadata(
        user_id="jason",
        source="review_ui",
        session_id=None,
        user_turn_id=None,
        memory_type="fact",
        confidence=0.7,
        status="approved",
        tags=[],
        entity_type=None,
        entity_id=None,
        expires_at=None,
    )

    assert metadata["source"] == "review_ui"
    assert "source_excerpt" not in metadata


def test_metadata_prompt_read_blocks_cross_user_disputed_and_superseded_rows():
    collection = _FakeCollection(
        get_result={
            "ids": ["own", "shared", "other", "disputed", "old"],
            "documents": [
                "Jason active fact",
                "Shared family fact",
                "Other private fact",
                "Disputed fact",
                "Old superseded fact",
            ],
            "metadatas": [
                {"user_id": "jason", "visibility": "personal", "status": "approved", "added_at": "2026-01-01T00:00:00Z"},
                {"user_id": "alex", "visibility": "family", "status": "approved", "added_at": "2026-01-02T00:00:00Z"},
                {"user_id": "alex", "visibility": "personal", "status": "approved", "added_at": "2026-01-03T00:00:00Z"},
                {"user_id": "jason", "visibility": "personal", "status": "disputed", "added_at": "2026-01-04T00:00:00Z"},
                {"user_id": "jason", "visibility": "personal", "status": "superseded", "superseded_by_id": "new", "added_at": "2026-01-05T00:00:00Z"},
            ],
        }
    )
    service = MemoryService(data_dir="/tmp/zoe-test-memory-safety")
    service._collection = lambda: collection

    rows = service._metadata_read("jason", limit=10)

    assert [row.id for row in rows] == ["shared", "own"]
    assert {"visibility": "family"} in collection.seen_get_where["$or"]


def test_semantic_search_blocks_cross_user_disputed_and_superseded_rows():
    collection = _FakeCollection(
        query_result={
            "ids": [["own", "shared", "other", "disputed", "old"]],
            "documents": [[
                "Jason active fact",
                "Shared family fact",
                "Other private fact",
                "Disputed fact",
                "Old superseded fact",
            ]],
            "metadatas": [[
                {"user_id": "jason", "visibility": "personal", "status": "approved", "confidence": 0.9, "added_at": "2026-01-01T00:00:00Z"},
                {"user_id": "alex", "visibility": "family", "status": "approved", "confidence": 0.9, "added_at": "2026-01-02T00:00:00Z"},
                {"user_id": "alex", "visibility": "personal", "status": "approved", "confidence": 0.9, "added_at": "2026-01-03T00:00:00Z"},
                {"user_id": "jason", "visibility": "personal", "status": "disputed", "confidence": 0.9, "added_at": "2026-01-04T00:00:00Z"},
                {"user_id": "jason", "visibility": "personal", "status": "superseded", "superseded_by_id": "new", "confidence": 0.9, "added_at": "2026-01-05T00:00:00Z"},
            ]],
            "distances": [[0.1, 0.2, 0.3, 0.4, 0.5]],
        }
    )
    service = MemoryService(data_dir="/tmp/zoe-test-memory-safety")
    service._collection = lambda: collection

    rows = service._semantic_search("fact", "jason", limit=10)

    assert [row.id for row in rows] == ["own", "shared"]
    assert {"visibility": "family"} in collection.seen_query_where["$or"]
