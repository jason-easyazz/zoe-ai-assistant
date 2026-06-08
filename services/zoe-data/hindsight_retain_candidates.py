"""Hindsight retain-candidate admission helpers.

Zoe should not let Hindsight reflection or extraction write trusted memory
silently. These helpers create pending MemoryService rows that can be reviewed
or admitted by Multica/evidence gates before any sidecar retain call occurs.
"""

from __future__ import annotations

from typing import Any, Mapping

from samantha_memory_contract import MemoryEvent, memory_event_from_mapping


HINDSIGHT_RETAIN_SOURCE = "hindsight_retain_candidate"


def build_hindsight_retain_candidate(event_or_payload: MemoryEvent | Mapping[str, Any]) -> dict[str, Any]:
    """Build a pending MemoryService ingest payload from a Samantha event."""

    event = event_or_payload if isinstance(event_or_payload, MemoryEvent) else memory_event_from_mapping(event_or_payload)
    event.validate()

    tags = [
        "zoe-memory",
        "hindsight-retain-candidate",
        f"scope:{event.scope}",
        f"event:{event.event_type}",
        f"status:{event.status}",
    ]
    tags.extend(f"entity:{entity}" for entity in event.entities[:6])
    tags.extend(f"evidence:{ref}" for ref in event.evidence_refs[:6])
    if event.relationships:
        tags.append("relational")
    if event.supersedes:
        tags.append("supersession")

    source_excerpt = event.content[:220]
    if event.evidence_refs:
        source_excerpt = f"{source_excerpt}\nEvidence: {', '.join(event.evidence_refs[:4])}"

    return {
        "text": event.content,
        "user_id": event.user_id,
        "source": HINDSIGHT_RETAIN_SOURCE,
        "session_id": None,
        "user_turn_id": event.event_id,
        "memory_type": event.event_type,
        "confidence": event.confidence,
        "status": "pending",
        "tags": tags,
        "entity_type": "samantha_event" if event.entities else None,
        "entity_id": event.entities[0] if event.entities else event.event_id,
        "source_excerpt": source_excerpt,
        "metadata": {
            "event_id": event.event_id,
            "scope": event.scope,
            "event_type": event.event_type,
            "evidence_refs": list(event.evidence_refs),
            "relationships": [relationship.to_dict() for relationship in event.relationships],
            "supersedes": list(event.supersedes),
            "retention_policy": event.retention_policy,
        },
    }


async def create_hindsight_retain_candidate(
    event_or_payload: MemoryEvent | Mapping[str, Any],
    *,
    memory_service: Any | None = None,
) -> Any:
    """Create a pending MemoryService row for later Hindsight admission."""

    payload = build_hindsight_retain_candidate(event_or_payload)
    svc = memory_service
    if svc is None:
        from memory_service import get_memory_service

        svc = get_memory_service()

    return await svc.ingest(
        payload["text"],
        user_id=payload["user_id"],
        source=payload["source"],
        session_id=payload["session_id"],
        user_turn_id=payload["user_turn_id"],
        memory_type=payload["memory_type"],
        confidence=payload["confidence"],
        status=payload["status"],
        tags=payload["tags"],
        entity_type=payload["entity_type"],
        entity_id=payload["entity_id"],
    )


__all__ = [
    "HINDSIGHT_RETAIN_SOURCE",
    "build_hindsight_retain_candidate",
    "create_hindsight_retain_candidate",
]
