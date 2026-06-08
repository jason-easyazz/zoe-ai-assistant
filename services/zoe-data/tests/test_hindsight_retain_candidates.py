import pytest

from hindsight_retain_candidates import (
    HINDSIGHT_RETAIN_SOURCE,
    build_hindsight_retain_candidate,
    create_hindsight_retain_candidate,
)
from samantha_memory_contract import (
    MemoryEvent,
    MemoryEventType,
    MemoryRelationship,
    MemoryScope,
    MemorySource,
    RelationshipType,
)


def _event():
    return MemoryEvent(
        event_id="mem_evt_candidate",
        user_id="jason",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TRACE.value,
        event_type=MemoryEventType.FAILURE.value,
        content="The weather card failed because duplicate voice queue emits raced.",
        entities=("weather_card", "voice_queue"),
        relationships=(
            MemoryRelationship(
                relationship_type=RelationshipType.FAILED_ON.value,
                source="weather_card",
                target="mobile_dashboard_render",
            ),
        ),
        evidence_refs=("trace:weather:001",),
        confidence=0.81,
    )


def test_build_hindsight_retain_candidate_is_pending_and_evidence_tagged():
    candidate = build_hindsight_retain_candidate(_event())

    assert candidate["source"] == HINDSIGHT_RETAIN_SOURCE
    assert candidate["status"] == "pending"
    assert candidate["user_turn_id"] == "mem_evt_candidate"
    assert "hindsight-retain-candidate" in candidate["tags"]
    assert "evidence:trace:weather:001" in candidate["tags"]
    assert "relational" in candidate["tags"]
    assert candidate["metadata"]["relationships"][0]["relationship_type"] == "FAILED_ON"


@pytest.mark.asyncio
async def test_create_hindsight_retain_candidate_uses_memory_service_pending_ingest():
    seen = {}

    class FakeMemoryService:
        async def ingest(self, text, **kwargs):
            seen["text"] = text
            seen.update(kwargs)
            return {"id": "zoe_pending_mem"}

    result = await create_hindsight_retain_candidate(_event(), memory_service=FakeMemoryService())

    assert result == {"id": "zoe_pending_mem"}
    assert seen["source"] == HINDSIGHT_RETAIN_SOURCE
    assert seen["status"] == "pending"
    assert seen["memory_type"] == "failure"
    assert seen["user_turn_id"] == "mem_evt_candidate"
    assert "hindsight-retain-candidate" in seen["tags"]
