from memory_service import MemoryService


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
        extra_metadata={
            "event_id": "mem_evt_candidate",
            "relationships": [{"relationship_type": "FAILED_ON"}],
            "evidence_refs": ["trace:weather:001"],
            "ignored": None,
            "status": "candidate_pending",
        },
        idem_key="idem",
    )

    assert metadata["source_excerpt"].startswith("Weather failed")
    assert metadata["candidate_event_id"] == "mem_evt_candidate"
    assert metadata["candidate_relationships"] == "[{\"relationship_type\":\"FAILED_ON\"}]"
    assert metadata["candidate_evidence_refs"] == "[\"trace:weather:001\"]"
    assert "candidate_ignored" not in metadata
    assert metadata["status"] == "pending"
    assert metadata["candidate_status"] == "candidate_pending"


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
