"""Hindsight retain-candidate admission helpers.

Zoe should not let Hindsight reflection or extraction write trusted memory
silently. These helpers create pending MemoryService rows that can be reviewed
or admitted by Multica/evidence gates before any sidecar retain call occurs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from hindsight_memory import HindsightConfig, event_to_hindsight_item
from zoe_evolution_proposal import EvolutionProposal
from zoe_memory_admission import MemoryAdmissionDecision, MemoryAdmissionRequest, evaluate_memory_admission
from zoe_memory_contract import MemoryEvent, memory_event_from_mapping
from zoe_memory_router import MemoryBackend
from zoe_observation_trace import ObservationTrace


HINDSIGHT_RETAIN_SOURCE = "hindsight_retain_candidate"


class HindsightRetainAdmissionError(ValueError):
    """Raised when a Hindsight retain plan lacks an approved admission decision."""


@dataclass(frozen=True)
class HindsightAdmittedRetainPlan:
    admission_id: str
    event_id: str
    bank_id: str
    payload: Mapping[str, Any]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.payload)
        if isinstance(payload.get("items"), tuple):
            payload["items"] = list(payload["items"])
        return {
            "admission_id": self.admission_id,
            "event_id": self.event_id,
            "bank_id": self.bank_id,
            "payload": payload,
            "evidence_refs": list(self.evidence_refs),
        }


def build_hindsight_retain_candidate(event_or_payload: MemoryEvent | Mapping[str, Any]) -> dict[str, Any]:
    """Build a pending MemoryService ingest payload from a Zoe memory event."""

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

    context = {
        "event_id": event.event_id,
        "scope": event.scope,
        "event_type": event.event_type,
        "evidence_refs": list(event.evidence_refs),
        "relationships": [relationship.to_dict() for relationship in event.relationships],
        "supersedes": list(event.supersedes),
        "retention_policy": event.retention_policy,
    }

    return {
        "text": _candidate_text(event, context),
        "user_id": event.user_id,
        "source": HINDSIGHT_RETAIN_SOURCE,
        "session_id": None,
        "user_turn_id": event.event_id,
        "memory_type": event.event_type,
        "confidence": event.confidence,
        "status": "pending",
        "tags": tags,
        "entity_type": "zoe_memory_event" if event.entities else None,
        "entity_id": event.entities[0] if event.entities else event.event_id,
        "source_excerpt": source_excerpt,
        "metadata": context,
    }


def _candidate_text(event: MemoryEvent, context: Mapping[str, Any]) -> str:
    structured = json.dumps(context, sort_keys=True, separators=(",", ":"))
    return f"{event.content}\n\n[zoe_hindsight_retain_candidate] {structured}"


def build_hindsight_retain_admission_request(
    event_or_payload: MemoryEvent | Mapping[str, Any],
    *,
    admission_id: str | None = None,
    requested_by: str = HINDSIGHT_RETAIN_SOURCE,
    target_backends: tuple[str, ...] = (MemoryBackend.HINDSIGHT.value,),
    observation_traces: tuple[ObservationTrace, ...] = (),
    approval_refs: tuple[str, ...] = (),
    proposal: EvolutionProposal | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> MemoryAdmissionRequest:
    """Build the admission request that must approve durable retain promotion."""

    event = event_or_payload if isinstance(event_or_payload, MemoryEvent) else memory_event_from_mapping(event_or_payload)
    event.validate()
    request = MemoryAdmissionRequest(
        admission_id=admission_id or f"admit_hindsight_retain_{event.event_id}",
        candidate=event,
        requested_by=requested_by,
        target_backends=target_backends,
        observation_traces=observation_traces,
        approval_refs=approval_refs,
        proposal=proposal,
        metadata={
            "source": HINDSIGHT_RETAIN_SOURCE,
            "candidate_event_id": event.event_id,
            "extra": dict(metadata or {}),
        },
    )
    request.validate()
    return request


def evaluate_hindsight_retain_candidate_admission(
    event_or_payload: MemoryEvent | Mapping[str, Any],
    *,
    admission_id: str | None = None,
    requested_by: str = HINDSIGHT_RETAIN_SOURCE,
    target_backends: tuple[str, ...] = (MemoryBackend.HINDSIGHT.value,),
    observation_traces: tuple[ObservationTrace, ...] = (),
    approval_refs: tuple[str, ...] = (),
    proposal: EvolutionProposal | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> MemoryAdmissionDecision:
    """Evaluate admission without writing to Hindsight or any durable backend."""

    request = build_hindsight_retain_admission_request(
        event_or_payload,
        admission_id=admission_id,
        requested_by=requested_by,
        target_backends=target_backends,
        observation_traces=observation_traces,
        approval_refs=approval_refs,
        proposal=proposal,
        metadata=metadata,
    )
    return evaluate_memory_admission(request)


def build_admitted_hindsight_retain_plan(
    request: MemoryAdmissionRequest,
    decision: MemoryAdmissionDecision,
    *,
    config: HindsightConfig | None = None,
) -> HindsightAdmittedRetainPlan:
    """Build the exact Hindsight retain payload only after admission approves it.

    This does not call Hindsight. It gives future runtime writers a small,
    testable object that cannot be produced unless the existing memory
    admission decision explicitly permits a durable Hindsight write.
    """

    request.validate()
    _validate_hindsight_retain_decision(request, decision)
    hindsight_config = config or HindsightConfig.from_env()
    return HindsightAdmittedRetainPlan(
        admission_id=request.admission_id,
        event_id=request.candidate.event_id,
        bank_id=hindsight_config.bank_id(request.candidate.user_id, request.candidate.scope),
        payload=MappingProxyType({
            "async": hindsight_config.async_retain,
            "items": (event_to_hindsight_item(request.candidate),),
        }),
        evidence_refs=decision.evidence_refs,
    )


def _validate_hindsight_retain_decision(
    request: MemoryAdmissionRequest,
    decision: MemoryAdmissionDecision,
) -> None:
    if decision.admission_id != request.admission_id:
        raise HindsightRetainAdmissionError(
            f"{request.admission_id}: decision admission_id {decision.admission_id!r} does not match request"
        )
    if MemoryBackend.HINDSIGHT.value not in request.target_backends:
        raise HindsightRetainAdmissionError(f"{request.admission_id}: request does not target Hindsight")
    if not decision.allowed_to_write_durable:
        raise HindsightRetainAdmissionError(f"{request.admission_id}: admission decision does not allow durable write")
    if MemoryBackend.HINDSIGHT.value not in decision.allowed_backends:
        raise HindsightRetainAdmissionError(f"{request.admission_id}: admission decision does not allow Hindsight")


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
        source_excerpt=payload["source_excerpt"],
        metadata=payload["metadata"],
    )


__all__ = [
    "HindsightAdmittedRetainPlan",
    "HINDSIGHT_RETAIN_SOURCE",
    "HindsightRetainAdmissionError",
    "build_admitted_hindsight_retain_plan",
    "build_hindsight_retain_admission_request",
    "build_hindsight_retain_candidate",
    "create_hindsight_retain_candidate",
    "evaluate_hindsight_retain_candidate_admission",
]
