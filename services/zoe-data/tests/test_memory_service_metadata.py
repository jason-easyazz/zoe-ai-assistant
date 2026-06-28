import asyncio
import logging
import sys
import types

import pytest

import memory_service
from memory_service import MemoryService, MemoryServiceError, _memory_visible_to_user, is_guest_memory_user


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


def test_memory_visible_to_user_matches_user_id_or_wing_or_shared_visibility():
    assert _memory_visible_to_user({"user_id": "Jason", "visibility": "personal"}, "jason") is True
    assert _memory_visible_to_user({"wing": "jason", "visibility": "personal"}, "jason") is True
    assert _memory_visible_to_user({"user_id": "alex", "wing": "jason", "visibility": "personal"}, "jason") is True
    assert _memory_visible_to_user({"user_id": "alex", "visibility": "personal"}, "jason") is False
    assert _memory_visible_to_user({"user_id": "alex", "visibility": "family"}, "jason") is True


def test_guest_memory_user_detection_blocks_unauthenticated_identities():
    assert is_guest_memory_user(None) is True
    assert is_guest_memory_user("") is True
    assert is_guest_memory_user("guest") is True
    assert is_guest_memory_user("anonymous") is True
    assert is_guest_memory_user("voice-guest") is True
    assert is_guest_memory_user("jason") is False


def test_memory_visible_to_user_blocks_family_rows_for_guest_callers():
    assert _memory_visible_to_user({"user_id": "jason", "visibility": "family"}, "guest") is False
    assert _memory_visible_to_user({"wing": "jason", "visibility": "family"}, "anonymous") is False
    assert _memory_visible_to_user({"user_id": "guest", "visibility": "family"}, "guest") is False
    assert _memory_visible_to_user({"user_id": "guest", "visibility": "personal"}, "guest") is False


@pytest.mark.asyncio
async def test_guest_prompt_and_search_reads_return_no_rows_without_collection_access():
    service = MemoryService(data_dir="/tmp/zoe-test-memory-guest")
    service._collection = lambda: (_ for _ in ()).throw(AssertionError("guest read should not touch storage"))

    assert await service.load_for_prompt("guest", limit=10) == []
    assert await service.load_for_prompt("", limit=10) == []
    assert await service.search("remember me", user_id="guest", limit=10) == []
    assert await service.search("remember me", user_id="", limit=10) == []


def test_metadata_prompt_read_blocks_cross_user_disputed_and_superseded_rows():
    collection = _FakeCollection(
        get_result={
            "ids": ["own", "wing_only", "mixed", "shared", "family_disputed", "other", "disputed", "old"],
            "documents": [
                "Jason active fact",
                "Jason wing-only fact",
                "Jason mixed wing fact",
                "Shared family fact",
                "Disputed shared family fact",
                "Other private fact",
                "Disputed fact",
                "Old superseded fact",
            ],
            "metadatas": [
                {"user_id": "jason", "visibility": "personal", "status": "approved", "added_at": "2026-01-01T00:00:00Z"},
                {"wing": "jason", "visibility": "personal", "status": "approved", "added_at": "2026-01-02T00:00:00Z"},
                {"user_id": "alex", "wing": "jason", "visibility": "personal", "status": "approved", "added_at": "2026-01-03T00:00:00Z"},
                {"user_id": "alex", "visibility": "family", "status": "approved", "added_at": "2026-01-04T00:00:00Z"},
                {"user_id": "alex", "visibility": "family", "status": "disputed", "added_at": "2026-01-05T00:00:00Z"},
                {"user_id": "alex", "visibility": "personal", "status": "approved", "added_at": "2026-01-06T00:00:00Z"},
                {"user_id": "jason", "visibility": "personal", "status": "disputed", "added_at": "2026-01-07T00:00:00Z"},
                {"user_id": "jason", "visibility": "personal", "status": "superseded", "superseded_by_id": "new", "added_at": "2026-01-08T00:00:00Z"},
            ],
        }
    )
    service = MemoryService(data_dir="/tmp/zoe-test-memory-safety")
    service._collection = lambda: collection

    rows = service._metadata_read("jason", limit=10)

    assert {row.id for row in rows} == {"own", "wing_only", "mixed", "shared"}
    assert {"visibility": "family"} in collection.seen_get_where["$or"]


def test_semantic_search_blocks_cross_user_disputed_and_superseded_rows():
    collection = _FakeCollection(
        query_result={
            "ids": [["own", "wing_only", "mixed", "shared", "family_superseded", "other", "disputed", "old"]],
            "documents": [[
                "Jason active fact",
                "Jason wing-only fact",
                "Jason mixed wing fact",
                "Shared family fact",
                "Superseded shared family fact",
                "Other private fact",
                "Disputed fact",
                "Old superseded fact",
            ]],
            "metadatas": [[
                {"user_id": "jason", "visibility": "personal", "status": "approved", "confidence": 0.9, "added_at": "2026-01-01T00:00:00Z"},
                {"wing": "jason", "visibility": "personal", "status": "approved", "confidence": 0.9, "added_at": "2026-01-02T00:00:00Z"},
                {"user_id": "alex", "wing": "jason", "visibility": "personal", "status": "approved", "confidence": 0.9, "added_at": "2026-01-03T00:00:00Z"},
                {"user_id": "alex", "visibility": "family", "status": "approved", "confidence": 0.9, "added_at": "2026-01-04T00:00:00Z"},
                {"user_id": "alex", "visibility": "family", "status": "superseded", "superseded_by_id": "new", "confidence": 0.9, "added_at": "2026-01-05T00:00:00Z"},
                {"user_id": "alex", "visibility": "personal", "status": "approved", "confidence": 0.9, "added_at": "2026-01-06T00:00:00Z"},
                {"user_id": "jason", "visibility": "personal", "status": "disputed", "confidence": 0.9, "added_at": "2026-01-07T00:00:00Z"},
                {"user_id": "jason", "visibility": "personal", "status": "superseded", "superseded_by_id": "new", "confidence": 0.9, "added_at": "2026-01-08T00:00:00Z"},
            ]],
            "distances": [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]],
        }
    )
    service = MemoryService(data_dir="/tmp/zoe-test-memory-safety")
    service._collection = lambda: collection

    rows = service._semantic_search("fact", "jason", limit=10)

    assert {row.id for row in rows} == {"own", "wing_only", "mixed", "shared"}
    assert {"visibility": "family"} in collection.seen_query_where["$or"]


@pytest.mark.asyncio
async def test_background_task_tracking_holds_task_until_done_and_retrieves_exception(caplog):
    service = MemoryService(data_dir="/tmp/zoe-test-memory-tasks")

    async def failing_task():
        await asyncio.sleep(0)
        raise RuntimeError("tick failed")

    caplog.set_level(logging.WARNING, logger="memory_service")
    task = service._track_background_task(failing_task(), name="memory_tick_test")

    assert task in service._background_tasks

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    for _ in range(10):
        if task not in service._background_tasks:
            break
        await asyncio.sleep(0)

    assert task.done()
    assert task not in service._background_tasks
    assert "background task memory_tick_test failed" in caplog.text


def test_audit_collection_reuses_cached_persistent_client_per_data_dir(monkeypatch):
    created_paths = []

    class FakeClient:
        def __init__(self, path):
            self.path = path
            self.collections = []

        def get_or_create_collection(self, name):
            self.collections.append(name)
            return {"path": self.path, "name": name}

    def persistent_client(*, path):
        created_paths.append(path)
        return FakeClient(path)

    memory_service._AUDIT_CLIENTS.clear()
    monkeypatch.setitem(
        sys.modules,
        "chromadb",
        types.SimpleNamespace(PersistentClient=persistent_client),
    )

    first = MemoryService(data_dir="/tmp/zoe-test-audit-cache")
    second = MemoryService(data_dir="/tmp/zoe-test-audit-cache")
    other = MemoryService(data_dir="/tmp/zoe-test-audit-cache-other")

    assert first._audit_collection()["path"] == "/tmp/zoe-test-audit-cache"
    assert first._audit_collection()["path"] == "/tmp/zoe-test-audit-cache"
    assert second._audit_collection()["path"] == "/tmp/zoe-test-audit-cache"
    assert other._audit_collection()["path"] == "/tmp/zoe-test-audit-cache-other"
    assert created_paths == [
        "/tmp/zoe-test-audit-cache",
        "/tmp/zoe-test-audit-cache-other",
    ]
