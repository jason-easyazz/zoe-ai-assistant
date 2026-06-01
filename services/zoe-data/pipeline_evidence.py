"""Evidence-gated engineering pipeline state.

This is intentionally independent of the live Kanban adapter for now. Phase 2
defines the contract; the verify-phase integration can adopt it in a smaller PR.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PipelinePhase = Literal["scout", "implement", "verify", "review", "closeout", "retro"]
PipelineStatus = Literal["todo", "running", "blocked", "done"]
EvidenceKind = Literal["tool", "test", "validator", "pr", "greptile", "human", "log"]
EvidenceProfile = Literal["default", "audit", "code", "health"]
TransitionOutcome = Literal[
    "start",
    "complete",
    "block",
    "request_changes",
    "verification_failed",
    "merge_blocked",
]

PHASE_ORDER: tuple[PipelinePhase, ...] = (
    "scout",
    "implement",
    "verify",
    "review",
    "closeout",
    "retro",
)

_REQUIRED_EVIDENCE: dict[PipelinePhase, set[EvidenceKind]] = {
    "scout": {"tool"},
    "implement": {"tool"},
    "verify": {"test", "validator"},
    "review": {"human"},
    "closeout": {"greptile"},
    "retro": {"log"},
}

_EVIDENCE_PROFILES: dict[EvidenceProfile, dict[PipelinePhase, set[EvidenceKind]]] = {
    "default": _REQUIRED_EVIDENCE,
    "code": _REQUIRED_EVIDENCE,
    "audit": {
        "scout": {"tool"},
        "implement": {"tool"},
        "verify": {"validator"},
        "review": {"human"},
        "closeout": {"greptile"},
        "retro": {"log"},
    },
    "health": {
        "scout": {"tool"},
        "implement": {"tool"},
        "verify": {"validator", "tool"},
        "review": {"human"},
        "closeout": {"greptile"},
        "retro": {"log"},
    },
}

_PROFILE_TAG_RE = re.compile(r"evidence_profile:\s*(\w+)", re.I)


class EvidenceItem(BaseModel):
    kind: EvidenceKind
    summary: str = Field(min_length=1, max_length=500)
    command: str | None = Field(default=None, max_length=500)
    artifact: str | None = Field(default=None, max_length=1000)
    content_hash: str | None = Field(default=None, max_length=64)
    passed: bool | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _no_secret_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {"api_key", "token", "password", "secret", "bearer", "credential", "auth"}
        leaked = sorted(k for k in value if any(marker in k.lower() for marker in forbidden))
        if leaked:
            raise ValueError(f"Evidence metadata may not contain secret fields: {', '.join(leaked)}")
        return value


class TransitionRecord(BaseModel):
    from_phase: PipelinePhase
    to_phase: PipelinePhase
    outcome: TransitionOutcome
    at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reason: str | None = None


class PipelineState(BaseModel):
    schema_version: int = 1
    task_ref: str
    phase: PipelinePhase = "implement"
    status: PipelineStatus = "todo"
    evidence_profile: EvidenceProfile = "default"
    attempts: dict[PipelinePhase, int] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    history: list[TransitionRecord] = Field(default_factory=list)
    last_block_fingerprint: str | None = None
    repeated_block_count: int = 0

    @field_validator("task_ref")
    @classmethod
    def _task_ref_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task_ref is required")
        return value.strip()


def evidence_kinds(state: PipelineState) -> set[EvidenceKind]:
    return {item.kind for item in state.evidence if item.passed is True}


def content_hash(text: str) -> str:
    """Stable SHA-256 hex digest for validator/test stdout or handoff bodies."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def block_fingerprint(phase: PipelinePhase, reason: str) -> str:
    """Fingerprint a blocked transition so identical failures can abort loops."""
    normalized = f"{phase}:{reason.strip().lower()}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def record_block_fingerprint(state: PipelineState, fingerprint: str) -> tuple[PipelineState, bool]:
    """Track repeated identical block fingerprints; return (state, should_abort)."""
    if fingerprint and fingerprint == state.last_block_fingerprint:
        count = state.repeated_block_count + 1
    else:
        count = 1
    updated = state.model_copy(
        update={"last_block_fingerprint": fingerprint, "repeated_block_count": count}
    )
    return updated, count >= 2


def issue_evidence_profile(issue: dict | None) -> EvidenceProfile:
    """Resolve per-issue evidence requirements from Multica metadata or description tags."""
    issue = issue or {}
    meta = issue.get("metadata") or {}
    explicit = str(meta.get("evidence_profile") or issue.get("evidence_profile") or "").strip().lower()
    if explicit in _EVIDENCE_PROFILES:
        return explicit  # type: ignore[return-value]

    haystack = " ".join(
        [
            str(issue.get("title") or ""),
            str(issue.get("description") or ""),
            json.dumps(meta),
        ]
    ).lower()
    tag_match = _PROFILE_TAG_RE.search(haystack)
    if tag_match:
        tagged = tag_match.group(1).lower()
        if tagged in _EVIDENCE_PROFILES:
            return tagged  # type: ignore[return-value]

    if (
        "audit-only" in haystack
        or str(meta.get("audit_only") or issue.get("audit_only") or "").strip().lower() in {"1", "true", "yes"}
        or str(meta.get("AUDIT_ONLY") or issue.get("AUDIT_ONLY") or "").strip().lower() in {"1", "true", "yes"}
    ):
        return "audit"
    if "health check" in haystack or str(meta.get("health") or "").strip().lower() in {"1", "true", "yes"}:
        return "health"
    return "default"


def required_evidence_for(state: PipelineState, phase: PipelinePhase | None = None) -> set[EvidenceKind]:
    selected = phase or state.phase
    profile_map = _EVIDENCE_PROFILES.get(state.evidence_profile, _REQUIRED_EVIDENCE)
    return profile_map.get(selected, set())


def missing_required_evidence(state: PipelineState, phase: PipelinePhase | None = None) -> set[EvidenceKind]:
    selected = phase or state.phase
    return required_evidence_for(state, selected) - evidence_kinds(state)


def implement_validator_hash(state: PipelineState) -> str | None:
    """Latest handoff-recorded validator hash from implement (ignores sync-time harness runs)."""
    for item in reversed(state.evidence):
        if item.kind != "validator" or item.passed is not True or not item.content_hash:
            continue
        if item.metadata.get("phase") == "implement" and item.metadata.get("source") == "handoff":
            return item.content_hash
    return None


def verify_validator_hash_matches(state: PipelineState) -> bool:
    """Verify handoff validator hash must match implement when both are worker-sourced."""
    impl_hash = implement_validator_hash(state)
    if not impl_hash:
        return True
    verify_hashes = [
        item.content_hash
        for item in state.evidence
        if item.kind == "validator"
        and item.passed is True
        and item.content_hash
        and item.metadata.get("phase") == "verify"
        and item.metadata.get("source") == "handoff"
    ]
    if not verify_hashes:
        return True
    return impl_hash in verify_hashes


def can_complete_phase(state: PipelineState) -> bool:
    if missing_required_evidence(state):
        return False
    if state.phase == "verify" and not verify_validator_hash_matches(state):
        return False
    return True


def with_evidence(state: PipelineState, *items: EvidenceItem) -> PipelineState:
    return state.model_copy(update={"evidence": [*state.evidence, *items]})


def transition(state: PipelineState, outcome: TransitionOutcome, *, reason: str | None = None) -> PipelineState:
    if outcome == "start":
        next_phase = state.phase
        next_status: PipelineStatus = "running"
    elif outcome == "block":
        next_phase = state.phase
        next_status = "blocked"
    elif outcome == "request_changes":
        if state.phase != "review":
            raise ValueError("request_changes is only valid from review")
        next_phase = "implement"
        next_status = "todo"
    elif outcome == "verification_failed":
        if state.phase != "verify":
            raise ValueError("verification_failed is only valid from verify")
        next_phase = "implement"
        next_status = "todo"
    elif outcome == "merge_blocked":
        if state.phase != "closeout":
            raise ValueError("merge_blocked is only valid from closeout")
        next_phase = "closeout"
        next_status = "blocked"
    elif outcome == "complete":
        if not can_complete_phase(state):
            missing = ", ".join(sorted(missing_required_evidence(state)))
            raise ValueError(f"{state.phase} is missing required evidence: {missing}")
        current_idx = PHASE_ORDER.index(state.phase)
        if current_idx == len(PHASE_ORDER) - 1:
            next_phase = state.phase
            next_status = "done"
        else:
            next_phase = PHASE_ORDER[current_idx + 1]
            next_status = "todo"
    else:
        raise ValueError(f"Unsupported outcome: {outcome}")

    attempts = dict(state.attempts)
    if next_status == "running":
        attempts[state.phase] = attempts.get(state.phase, 0) + 1
    history = [
        *state.history,
        TransitionRecord(from_phase=state.phase, to_phase=next_phase, outcome=outcome, reason=reason),
    ]
    evidence = [] if outcome in {"request_changes", "verification_failed"} else state.evidence
    return state.model_copy(
        update={"phase": next_phase, "status": next_status, "attempts": attempts, "evidence": evidence, "history": history}
    )

