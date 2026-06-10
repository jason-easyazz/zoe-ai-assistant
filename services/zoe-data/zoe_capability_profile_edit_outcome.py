"""Build memory/trust evidence from verified capability-profile PR edits.

This module is intentionally inert. It closes the evidence loop after the
profile PR-edit gate by converting a verified, PR-backed profile edit plan into
an admission-gated memory candidate and capability trust evidence records. It
does not write MemoryService, Hindsight, Graphiti, Multica, profile files, or
GitHub branches.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from zoe_capability_profile_pr_edit_gate import CapabilityProfilePREditPlan
from zoe_capability_utils import merge_string_refs
from zoe_memory_admission import MemoryAdmissionDecision, MemoryAdmissionRequest, evaluate_memory_admission
from zoe_memory_contract import MemoryEvent, MemoryEventType, MemoryRelationship, MemoryScope, MemorySource, RelationshipType
from zoe_memory_router import MemoryBackend
from zoe_observation_trace import ObservationTrace


CAPABILITY_PROFILE_EDIT_OUTCOME_SOURCE = "capability_profile_edit_outcome"
DEFAULT_PROFILE_EDIT_OUTCOME_BACKENDS = (MemoryBackend.HINDSIGHT.value, MemoryBackend.GRAPHITI.value)


@dataclass(frozen=True)
class CapabilityProfileEditOutcomeTrustRecord:
    capability_id: str
    from_trust_level: str
    to_trust_level: str
    ticket_id: str
    target_path: str
    evidence_refs: tuple[str, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        self.validate()

    def validate(self) -> None:
        if not self.capability_id:
            raise ValueError("capability_id is required")
        if not self.ticket_id:
            raise ValueError(f"{self.capability_id}: ticket_id is required")
        if not self.target_path:
            raise ValueError(f"{self.capability_id}: target_path is required")
        if not self.from_trust_level:
            raise ValueError(f"{self.capability_id}: from_trust_level is required")
        if not self.to_trust_level:
            raise ValueError(f"{self.capability_id}: to_trust_level is required")
        if self.from_trust_level == self.to_trust_level:
            raise ValueError(f"{self.capability_id}: trust outcome must change trust level")
        if not self.evidence_refs:
            raise ValueError(f"{self.capability_id}: evidence_refs are required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "from_trust_level": self.from_trust_level,
            "to_trust_level": self.to_trust_level,
            "ticket_id": self.ticket_id,
            "target_path": self.target_path,
            "evidence_refs": list(self.evidence_refs),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CapabilityProfileEditOutcomePlan:
    memory_candidate: MemoryEvent | None
    admission_request: MemoryAdmissionRequest | None
    admission_decision: MemoryAdmissionDecision | None
    trust_records: tuple[CapabilityProfileEditOutcomeTrustRecord, ...]
    blockers: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.blockers and (self.memory_candidate or self.admission_request or self.admission_decision or self.trust_records):
            raise ValueError("blocked profile edit outcome plans cannot carry memory or trust records")

    @property
    def allowed_to_admit_memory(self) -> bool:
        return self.admission_decision is not None and self.admission_decision.allowed_to_write_durable and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_to_admit_memory": self.allowed_to_admit_memory,
            "blockers": list(self.blockers),
            "memory_candidate": self.memory_candidate.to_dict() if self.memory_candidate else None,
            "admission_request": _admission_request_dict(self.admission_request),
            "admission_decision": self.admission_decision.to_dict() if self.admission_decision else None,
            "trust_records": [record.to_dict() for record in self.trust_records],
            "metadata": dict(self.metadata),
        }


def build_capability_profile_edit_outcome_plan(
    pr_edit_plan: CapabilityProfilePREditPlan,
    *,
    verification_traces: Sequence[ObservationTrace],
    user_id: str,
    scope: str = MemoryScope.PROJECT.value,
    target_backends: Sequence[str] = DEFAULT_PROFILE_EDIT_OUTCOME_BACKENDS,
    approval_refs: Sequence[str] = (),
    admission_id: str | None = None,
    event_id: str | None = None,
    promotion_manifest: str | Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> CapabilityProfileEditOutcomePlan:
    """Create an admission-gated memory/trust evidence plan for a verified PR edit."""

    blockers = _outcome_blockers(pr_edit_plan, verification_traces, user_id=user_id, scope=scope, target_backends=target_backends)
    manifest = _manifest(promotion_manifest, pr_edit_plan)
    if manifest.get("parse_error"):
        blockers.append("invalid_promotion_manifest")
    trust_records = () if blockers else _trust_records(pr_edit_plan, manifest, verification_traces)
    if blockers:
        return CapabilityProfileEditOutcomePlan(
            memory_candidate=None,
            admission_request=None,
            admission_decision=None,
            trust_records=(),
            blockers=tuple(dict.fromkeys(blockers)),
            metadata=_metadata(metadata),
        )

    candidate = _memory_candidate(
        pr_edit_plan,
        verification_traces,
        trust_records,
        user_id=user_id,
        scope=scope,
        event_id=event_id,
        metadata=metadata,
    )
    request = MemoryAdmissionRequest(
        admission_id=admission_id or f"admit_profile_edit_outcome_{pr_edit_plan.ticket_id}",
        candidate=candidate,
        requested_by=CAPABILITY_PROFILE_EDIT_OUTCOME_SOURCE,
        target_backends=tuple(str(item) for item in target_backends if str(item)),
        observation_traces=tuple(verification_traces),
        approval_refs=tuple(str(ref) for ref in approval_refs if str(ref)),
        proposal=None,
        metadata={
            "source": CAPABILITY_PROFILE_EDIT_OUTCOME_SOURCE,
            "ticket_id": pr_edit_plan.ticket_id,
            "target_path": pr_edit_plan.target_path,
            "promoted_capability_ids": list(pr_edit_plan.promoted_capability_ids),
            "extra": dict(metadata or {}),
        },
    )
    request.validate()
    return CapabilityProfileEditOutcomePlan(
        memory_candidate=candidate,
        admission_request=request,
        admission_decision=evaluate_memory_admission(request),
        trust_records=trust_records,
        metadata=_metadata(metadata),
    )


def _outcome_blockers(
    pr_edit_plan: CapabilityProfilePREditPlan,
    traces: Sequence[ObservationTrace],
    *,
    user_id: str,
    scope: str,
    target_backends: Sequence[str],
) -> list[str]:
    blockers: list[str] = []
    if not pr_edit_plan.allowed_to_prepare_pr_edit:
        blockers.append("pr_edit_plan_not_allowed")
        blockers.extend(pr_edit_plan.blockers)
    if not user_id:
        blockers.append("missing_user_id")
    if scope not in {item.value for item in MemoryScope}:
        blockers.append("unsupported_scope")
    if not target_backends:
        blockers.append("missing_target_backends")
    if not traces:
        blockers.append("missing_verification_traces")
    for trace in traces:
        try:
            trace.validate()
        except ValueError as exc:
            blockers.append(f"invalid_trace:{trace.trace_id}:{exc}")
            continue
        if trace.user_id != user_id:
            blockers.append(f"trace_user_mismatch:{trace.trace_id}")
        if trace.subject_id and trace.subject_id != pr_edit_plan.ticket_id:
            blockers.append(f"trace_subject_mismatch:{trace.trace_id}")
    return blockers


def _manifest(promotion_manifest: str | Mapping[str, Any] | None, pr_edit_plan: CapabilityProfilePREditPlan) -> Mapping[str, Any]:
    if promotion_manifest is None:
        extra = pr_edit_plan.metadata.get("extra") if isinstance(pr_edit_plan.metadata.get("extra"), Mapping) else {}
        promotion_manifest = extra.get("promotion_manifest") if isinstance(extra, Mapping) else None
    if promotion_manifest is None:
        return {}
    if isinstance(promotion_manifest, Mapping):
        return promotion_manifest
    try:
        value = json.loads(promotion_manifest)
    except json.JSONDecodeError as exc:
        return {"parse_error": str(exc)}
    return value if isinstance(value, Mapping) else {"parse_error": "promotion manifest must decode to an object"}


def _trust_records(
    pr_edit_plan: CapabilityProfilePREditPlan,
    manifest: Mapping[str, Any],
    traces: Sequence[ObservationTrace],
) -> tuple[CapabilityProfileEditOutcomeTrustRecord, ...]:
    records_by_capability = {
        str(record.get("capability_id")): record
        for record in manifest.get("records", [])
        if isinstance(record, Mapping) and record.get("capability_id")
    }
    evidence_refs = _evidence_refs(pr_edit_plan, traces)
    records: list[CapabilityProfileEditOutcomeTrustRecord] = []
    for capability_id in pr_edit_plan.promoted_capability_ids:
        manifest_record = records_by_capability.get(capability_id, {})
        records.append(
            CapabilityProfileEditOutcomeTrustRecord(
                capability_id=capability_id,
                from_trust_level=str(manifest_record.get("from_trust_level") or "unknown"),
                to_trust_level=str(manifest_record.get("to_trust_level") or "applied"),
                ticket_id=pr_edit_plan.ticket_id,
                target_path=pr_edit_plan.target_path,
                evidence_refs=evidence_refs,
                metadata={
                    "source": CAPABILITY_PROFILE_EDIT_OUTCOME_SOURCE,
                    "promotion_manifest_record": dict(manifest_record),
                },
            )
        )
    return tuple(records)


def _memory_candidate(
    pr_edit_plan: CapabilityProfilePREditPlan,
    traces: Sequence[ObservationTrace],
    trust_records: Sequence[CapabilityProfileEditOutcomeTrustRecord],
    *,
    user_id: str,
    scope: str,
    event_id: str | None,
    metadata: Mapping[str, Any] | None,
) -> MemoryEvent:
    evidence_refs = _evidence_refs(pr_edit_plan, traces)
    relationships = [
        MemoryRelationship(
            relationship_type=RelationshipType.EVIDENCED_BY.value,
            source=pr_edit_plan.ticket_id,
            target=ref,
        )
        for ref in evidence_refs
    ]
    relationships.extend(
        MemoryRelationship(
            relationship_type=RelationshipType.TRUSTED_FOR.value,
            source=record.capability_id,
            target="zoe_capability_profile",
            metadata={"from_trust_level": record.from_trust_level, "to_trust_level": record.to_trust_level},
        )
        for record in trust_records
    )
    return MemoryEvent(
        event_id=event_id or f"mem_evt_profile_edit_outcome_{pr_edit_plan.ticket_id}",
        user_id=user_id,
        scope=scope,
        source=MemorySource.TRACE.value,
        event_type=MemoryEventType.FACT.value,
        content=(
            f"Zoe capability profile edit {pr_edit_plan.ticket_id} was verified for "
            f"{', '.join(pr_edit_plan.promoted_capability_ids)} in {pr_edit_plan.target_path}."
        ),
        entities=tuple(dict.fromkeys((pr_edit_plan.ticket_id, pr_edit_plan.target_path, *pr_edit_plan.promoted_capability_ids))),
        relationships=tuple(relationships),
        evidence_refs=evidence_refs,
        confidence=_confidence(traces),
        metadata={
            "source": CAPABILITY_PROFILE_EDIT_OUTCOME_SOURCE,
            "ticket_id": pr_edit_plan.ticket_id,
            "target_path": pr_edit_plan.target_path,
            "promoted_capability_ids": list(pr_edit_plan.promoted_capability_ids),
            "trust_records": [record.to_dict() for record in trust_records],
            "extra": dict(metadata or {}),
        },
    ).validated()


def _evidence_refs(pr_edit_plan: CapabilityProfilePREditPlan, traces: Sequence[ObservationTrace]) -> tuple[str, ...]:
    refs: list[str] = []
    refs.extend(pr_edit_plan.pr_refs)
    refs.extend(pr_edit_plan.rollback_refs)
    refs.extend(pr_edit_plan.verification_refs)
    refs.extend(pr_edit_plan.greptile_refs)
    for trace in traces:
        refs.extend(trace.evidence_refs)
    return merge_string_refs(refs)


def _confidence(traces: Sequence[ObservationTrace]) -> float:
    confidences = [trace.confidence for trace in traces if trace.confidence is not None]
    return min(confidences) if confidences else 0.8


def _metadata(metadata: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return {"source": CAPABILITY_PROFILE_EDIT_OUTCOME_SOURCE, "extra": dict(metadata or {})}


def _admission_request_dict(request: MemoryAdmissionRequest | None) -> dict[str, Any] | None:
    if request is None:
        return None
    return {
        "admission_id": request.admission_id,
        "requested_by": request.requested_by,
        "target_backends": list(request.target_backends),
        "approval_refs": list(request.approval_refs),
        "trace_ids": [trace.trace_id for trace in request.observation_traces],
        "metadata": dict(request.metadata or {}),
    }


__all__ = [
    "CAPABILITY_PROFILE_EDIT_OUTCOME_SOURCE",
    "DEFAULT_PROFILE_EDIT_OUTCOME_BACKENDS",
    "CapabilityProfileEditOutcomePlan",
    "CapabilityProfileEditOutcomeTrustRecord",
    "build_capability_profile_edit_outcome_plan",
]
