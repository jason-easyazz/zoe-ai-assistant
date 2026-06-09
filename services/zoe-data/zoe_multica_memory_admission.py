"""Bridge Multica review records into Zoe memory admission decisions.

This module is intentionally inert. It trusts only explicit Zoe ticket
metadata from Multica/review records, then builds the existing memory admission
request shape. It does not write to MemoryService, Hindsight, Graphiti, or
Multica.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from multica_ticket_contract import parse_ticket_block
from zoe_evolution_proposal import EvolutionProposal
from zoe_memory_admission import MemoryAdmissionDecision, MemoryAdmissionRequest, evaluate_memory_admission
from zoe_memory_contract import MemoryEvent, memory_event_from_mapping
from zoe_memory_router import MemoryBackend
from zoe_observation_trace import ObservationOutcome, ObservationTrace, ObservationTraceType


MULTICA_MEMORY_ADMISSION_SOURCE = "multica_memory_admission"
DEFAULT_MULTICA_MEMORY_TARGET_BACKENDS = (MemoryBackend.HINDSIGHT.value,)


def build_multica_memory_admission_request(
    issue_or_record: Mapping[str, Any],
    event_or_payload: MemoryEvent | Mapping[str, Any],
    *,
    admission_id: str | None = None,
    requested_by: str = MULTICA_MEMORY_ADMISSION_SOURCE,
    target_backends: Sequence[str] | None = None,
    proposal: EvolutionProposal | None = None,
) -> MemoryAdmissionRequest:
    """Build an admission request from a Multica issue/review record.

    Explicit `memory_admission_approved: true` metadata supplies approval
    evidence plus a successful admission trace. Blocked metadata supplies a
    blocked admission trace. Missing approval metadata leaves the candidate in
    pending review through the normal memory admission contract.
    """

    event = event_or_payload if isinstance(event_or_payload, MemoryEvent) else memory_event_from_mapping(event_or_payload)
    event.validate()

    metadata = _ticket_metadata(issue_or_record)
    review_id = _review_identifier(issue_or_record, metadata)
    backends = _target_backends(metadata, target_backends)
    approval_refs = _approval_refs(metadata, review_id)
    traces = _observation_traces(metadata, review_id, event)

    request = MemoryAdmissionRequest(
        admission_id=admission_id or f"admit_multica_{event.event_id}",
        candidate=event,
        requested_by=requested_by,
        target_backends=tuple(backends),
        observation_traces=tuple(traces),
        approval_refs=tuple(approval_refs),
        proposal=proposal,
        metadata={
            "source": MULTICA_MEMORY_ADMISSION_SOURCE,
            "candidate_event_id": event.event_id,
            "multica_review_id": review_id,
            "ticket_metadata": metadata,
        },
    )
    request.validate()
    return request


def evaluate_multica_memory_admission(
    issue_or_record: Mapping[str, Any],
    event_or_payload: MemoryEvent | Mapping[str, Any],
    *,
    admission_id: str | None = None,
    requested_by: str = MULTICA_MEMORY_ADMISSION_SOURCE,
    target_backends: Sequence[str] | None = None,
    proposal: EvolutionProposal | None = None,
) -> MemoryAdmissionDecision:
    """Evaluate Multica-backed admission without promoting durable memory."""

    request = build_multica_memory_admission_request(
        issue_or_record,
        event_or_payload,
        admission_id=admission_id,
        requested_by=requested_by,
        target_backends=target_backends,
        proposal=proposal,
    )
    return evaluate_memory_admission(request)


def _ticket_metadata(issue_or_record: Mapping[str, Any]) -> dict[str, Any]:
    raw_metadata = issue_or_record.get("metadata")
    metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    parsed = parse_ticket_block(str(issue_or_record.get("description") or ""))
    if parsed:
        metadata.update({key: value for key, value in parsed.items() if value is not None})
    return metadata


def _review_identifier(issue_or_record: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    for key in ("memory_admission_review_id", "issue_id", "id", "ticket_id", "key"):
        value = metadata.get(key) or issue_or_record.get(key)
        if value:
            return str(value)
    return "unknown"


def _target_backends(metadata: Mapping[str, Any], explicit: Sequence[str] | None) -> tuple[str, ...]:
    if explicit is not None:
        return tuple(str(item) for item in explicit)

    raw = metadata.get("memory_admission_target_backends") or metadata.get("target_backends")
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray, str)):
        return tuple(str(item) for item in raw if item is not None and str(item))
    return DEFAULT_MULTICA_MEMORY_TARGET_BACKENDS


def _approval_refs(metadata: Mapping[str, Any], review_id: str) -> tuple[str, ...]:
    if metadata.get("memory_admission_approved") is not True:
        return ()

    refs = [f"approval:multica:{review_id}"]
    for ref in _metadata_sequence(metadata.get("memory_admission_approval_refs")):
        if ref not in refs:
            refs.append(ref)
    return tuple(refs)


def _observation_traces(metadata: Mapping[str, Any], review_id: str, event: MemoryEvent) -> tuple[ObservationTrace, ...]:
    blocked_reason = str(metadata.get("blocked_reason") or "").strip()
    if blocked_reason:
        return (
            ObservationTrace(
                trace_id=f"trace_multica_memory_admission_blocked_{_safe_id(review_id)}",
                trace_type=ObservationTraceType.ADMISSION.value,
                surface="multica",
                scope=event.scope,
                outcome=ObservationOutcome.BLOCKED.value,
                summary=f"Multica memory admission blocked: {blocked_reason}",
                evidence_refs=(f"multica:{review_id}",),
                user_id=event.user_id,
                subject_id=event.event_id,
                related_ids=(review_id,),
                metadata={"blocked_reason": blocked_reason},
            ),
        )

    if metadata.get("memory_admission_approved") is True:
        evidence_refs = [f"multica:{review_id}"]
        for ref in _metadata_sequence(metadata.get("memory_admission_evidence_refs")):
            if ref not in evidence_refs:
                evidence_refs.append(ref)
        return (
            ObservationTrace(
                trace_id=f"trace_multica_memory_admission_approved_{_safe_id(review_id)}",
                trace_type=ObservationTraceType.ADMISSION.value,
                surface="multica",
                scope=event.scope,
                outcome=ObservationOutcome.SUCCESS.value,
                summary="Multica memory admission approved with explicit review metadata.",
                evidence_refs=tuple(evidence_refs),
                user_id=event.user_id,
                subject_id=event.event_id,
                related_ids=(review_id,),
                metadata={"memory_admission_approved": True},
            ),
        )

    return ()


def _metadata_sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return tuple(str(item) for item in value if item is not None and str(item))
    return (str(value),)


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_") or "unknown"


__all__ = [
    "DEFAULT_MULTICA_MEMORY_TARGET_BACKENDS",
    "MULTICA_MEMORY_ADMISSION_SOURCE",
    "build_multica_memory_admission_request",
    "evaluate_multica_memory_admission",
]
