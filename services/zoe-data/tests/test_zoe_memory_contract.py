import pytest

from zoe_memory_contract import (
    MemoryContractError,
    MemoryEvent,
    MemoryEventType,
    MemoryRelationship,
    MemoryScope,
    MemorySource,
    RelationshipType,
    memory_event_from_mapping,
)

pytestmark = pytest.mark.ci_safe


def test_relational_memory_requires_evidence():
    event = MemoryEvent(
        user_id="jason",
        scope=MemoryScope.PERSONAL.value,
        source=MemorySource.TRACE.value,
        event_type=MemoryEventType.FAILURE.value,
        content="weather card failed during mobile render",
        relationships=(
            MemoryRelationship(
                relationship_type=RelationshipType.FAILED_ON.value,
                source="weather_card",
                target="mobile_dashboard_render",
            ),
        ),
        evidence_refs=(),
    )

    with pytest.raises(MemoryContractError, match="evidence_refs"):
        event.validate()


def test_valid_event_serializes_contract_fields():
    event = MemoryEvent(
        user_id="jason",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TEST.value,
        event_type=MemoryEventType.FIX.value,
        content="voice queue guard fixed duplicate weather responses",
        relationships=(
            MemoryRelationship(
                relationship_type=RelationshipType.FIXED_BY.value,
                source="duplicate_weather_response",
                target="voice_queue_guard",
            ),
        ),
        evidence_refs=("pytest:services/zoe-data/tests/test_voice_transcribe.py",),
        confidence=0.82,
    )

    payload = event.to_dict()

    assert payload["user_id"] == "jason"
    assert payload["status"] == "active"
    assert payload["relationships"][0]["relationship_type"] == "FIXED_BY"
    assert payload["evidence_refs"] == ["pytest:services/zoe-data/tests/test_voice_transcribe.py"]


def test_mapping_rejects_missing_user_id():
    with pytest.raises(MemoryContractError, match="user_id"):
        memory_event_from_mapping(
            {
                "scope": "personal",
                "source": "chat",
                "event_type": "preference",
                "content": "prefers concise answers",
                "evidence_refs": [],
            }
        )


def test_mapping_rejects_missing_scope():
    with pytest.raises(MemoryContractError, match="unsupported scope"):
        memory_event_from_mapping(
            {
                "user_id": "jason",
                "source": "chat",
                "event_type": "preference",
                "content": "prefers concise answers",
                "evidence_refs": [],
            }
        )


def test_mapping_supports_supersession_with_evidence():
    event = memory_event_from_mapping(
        {
            "user_id": "jason",
            "scope": "personal",
            "source": "chat",
            "event_type": "fact",
            "content": "Jason now prefers Hindsight-first bake-offs for Zoe memory.",
            "evidence_refs": ["chat:2026-06-08:zoe-evolution-plan"],
            "supersedes": ["mem_evt_old_graphiti_first"],
        }
    )

    assert event.supersedes == ("mem_evt_old_graphiti_first",)


def test_experience_memory_requires_evidence():
    event = MemoryEvent(
        user_id="jason",
        scope=MemoryScope.PROJECT.value,
        source=MemorySource.TRACE.value,
        event_type=MemoryEventType.EXPERIENCE.value,
        content="Zoe tried a Hindsight recall bake-off and learned from the outcome.",
        evidence_refs=(),
    )

    with pytest.raises(MemoryContractError, match="evidence_refs"):
        event.validate()
