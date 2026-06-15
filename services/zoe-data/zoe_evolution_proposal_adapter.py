"""Adapters that bridge legacy Zoe proposal writers to the proposal contract.

The live `evolution_proposals` table predates `zoe_evolution_proposal.py` and is
still read by existing UI, Multica, and review routes. These helpers keep that
legacy shape intact while attaching a validated contract snapshot to
`target_patterns` for future gates.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from zoe_candidate_scoring import CandidateEvaluation, CandidateScore
from zoe_evolution_proposal import (
    EvolutionSignal,
    EvolutionSignalType,
    ProposalRisk,
    ProposalStatus,
    TrustAutonomyClass,
    build_evolution_proposal,
)

CONTRACT_ENVELOPE_VERSION = 1

_SIGNAL_BY_LEGACY_TYPE = {
    "intent_pattern": EvolutionSignalType.REPEATED_FAILURE.value,
    "agent_health": EvolutionSignalType.OUTCOME_EVAL_FAILURE.value,
    "user_frustration": EvolutionSignalType.USER_REQUEST.value,
    "user_issue_report": EvolutionSignalType.USER_REQUEST.value,
    "code_improvement": EvolutionSignalType.TOOL_GAP.value,
}

_AFFECTED_CAPABILITIES_BY_TYPE = {
    "intent_pattern": ("intent_router", "chat_router", "observation_trace"),
    "agent_health": ("agent_runtime", "multica_governance", "observation_trace"),
    "user_frustration": ("chat_experience", "memory_router", "observation_trace"),
    "user_issue_report": ("chat_experience", "intent_router", "multica_governance"),
    "code_improvement": ("zoe_codebase", "multica_governance", "verification"),
}


def legacy_signal_type_for_proposal_type(proposal_type: str | None) -> str:
    """Return the contract signal type for a legacy proposal type."""

    return _SIGNAL_BY_LEGACY_TYPE[normalize_legacy_evolution_proposal_type(proposal_type)]


def build_existing_zoe_proposal_candidate(
    *,
    proposal_type: str | None,
    title: str,
    evidence_refs: Sequence[str],
    legacy_writer: str,
    runtime_notes: str | None = None,
    target_patterns: Sequence[str] = (),
) -> CandidateEvaluation:
    """Build the standard review-only existing-Zoe candidate for proposal writers."""

    normalized_type = normalize_legacy_evolution_proposal_type(proposal_type)
    legacy_target_patterns = tuple(str(item) for item in target_patterns if item is not None)
    metadata: dict[str, Any] = {
        "legacy_proposal_type": normalized_type,
        "legacy_writer": legacy_writer,
    }
    if legacy_target_patterns:
        metadata["legacy_target_patterns"] = list(legacy_target_patterns)
    return CandidateEvaluation(
        candidate_id=f"existing_zoe_{normalized_type}",
        name="Existing Zoe proposal path",
        source="existing_zoe",
        task=title,
        score=CandidateScore(
            fit=4,
            activity=4,
            license=5,
            offline=5,
            security=4,
            footprint=5,
            tests=3,
            maintainability=4,
            overlap=5,
        ),
        evidence_refs=tuple(evidence_refs),
        license_risk="compatible",
        offline_viability="required",
        runtime_notes=runtime_notes
        or f"Legacy proposal writer {legacy_writer} creates review-only proposals; no execution is granted by this contract.",
        overlaps_existing=("evolution_proposals", "multica_governance"),
        recommendation="needs_review",
        metadata=metadata,
    )


def build_legacy_evolution_proposal_contract(
    *,
    proposal_id: str,
    title: str,
    description: str,
    evidence: str = "",
    proposal_type: str = "intent_pattern",
    legacy_writer: str,
    user_id: str | None = None,
    multica_issue_id: str | None = None,
    target_patterns: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a validated contract snapshot for a legacy proposal writer.

    The returned dict is JSON-serializable and intentionally does not execute,
    approve, install, or write memory. It is safe to store beside the existing
    legacy proposal row as evidence for later Multica/review gates.
    """

    normalized_type = normalize_legacy_evolution_proposal_type(proposal_type)
    source_ref = f"{legacy_writer}:{proposal_id}"
    evidence_refs = _evidence_refs(source_ref, evidence)
    legacy_target_patterns = tuple(str(item) for item in target_patterns if item is not None)
    signal = EvolutionSignal(
        signal_id=f"signal_{proposal_id}",
        signal_type=legacy_signal_type_for_proposal_type(normalized_type),
        summary=description,
        source=legacy_writer,
        evidence_refs=evidence_refs,
        user_id=user_id,
        scope="system" if not user_id else "personal",
        metadata={
            "legacy_proposal_type": normalized_type,
            "evidence_excerpt": evidence[:500],
            "legacy_target_patterns": list(legacy_target_patterns),
        },
    )
    candidate = build_existing_zoe_proposal_candidate(
        proposal_type=normalized_type,
        title=title,
        evidence_refs=evidence_refs,
        legacy_writer=legacy_writer,
        target_patterns=legacy_target_patterns,
    )
    proposal = build_evolution_proposal(
        proposal_id=proposal_id,
        title=title,
        problem_statement=description,
        signals=(signal,),
        candidate=candidate,
        affected_capabilities=_AFFECTED_CAPABILITIES_BY_TYPE[normalized_type],
        autonomy_class=TrustAutonomyClass.SUGGEST.value,
        risk=ProposalRisk.MEDIUM.value,
        expected_benefit="Create a reviewable Zoe improvement proposal with evidence before any implementation work.",
        verification_plan=(
            "human_or_multica_review_required_before_approval",
            "implementation_pr_must_attach_tests_and_evidence_before_completion",
        ),
        rollback_plan="Reject or defer the proposal; no runtime change has been made by proposal creation.",
        status=ProposalStatus.PENDING_APPROVAL.value,
        multica_issue_id=multica_issue_id,
        metadata={
            "legacy_table": "evolution_proposals",
            "legacy_status": "pending",
            "legacy_proposal_type": normalized_type,
            "legacy_target_patterns": list(legacy_target_patterns),
            "created_by": legacy_writer,
        },
    )
    return {
        "version": CONTRACT_ENVELOPE_VERSION,
        "schema": "zoe_evolution_proposal",
        "legacy_writer": legacy_writer,
        "proposal": proposal.to_dict(),
    }


def build_mcp_evolution_proposal_contract(
    *,
    proposal_id: str,
    title: str,
    description: str,
    evidence: str = "",
    proposal_type: str = "intent_pattern",
    user_id: str | None = None,
    multica_issue_id: str | None = None,
    target_patterns: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a validated contract snapshot for the MCP proposal writer."""

    return build_legacy_evolution_proposal_contract(
        proposal_id=proposal_id,
        title=title,
        description=description,
        evidence=evidence,
        proposal_type=proposal_type,
        legacy_writer="mcp:create_evolution_proposal",
        user_id=user_id,
        multica_issue_id=multica_issue_id,
        target_patterns=target_patterns,
    )


def dump_legacy_evolution_proposal_contract(**kwargs: Any) -> str:
    """Return a stable JSON string for the legacy `target_patterns` column."""

    return json.dumps(build_legacy_evolution_proposal_contract(**kwargs), sort_keys=True, separators=(",", ":"))


def dump_mcp_evolution_proposal_contract(
    *,
    proposal_id: str,
    title: str,
    description: str,
    evidence: str = "",
    proposal_type: str = "intent_pattern",
    user_id: str | None = None,
    multica_issue_id: str | None = None,
    target_patterns: Sequence[str] = (),
) -> str:
    """Return a stable JSON string for the legacy `target_patterns` column."""

    return json.dumps(
        build_mcp_evolution_proposal_contract(
            proposal_id=proposal_id,
            title=title,
            description=description,
            evidence=evidence,
            proposal_type=proposal_type,
            user_id=user_id,
            multica_issue_id=multica_issue_id,
            target_patterns=target_patterns,
        ),
        sort_keys=True,
        separators=(",", ":"),
    )


def load_proposal_contract_snapshot(raw: str | bytes | Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Best-effort loader for rows that may or may not carry a contract snapshot."""

    if raw is None:
        return None
    if isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        try:
            payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    if not isinstance(payload, Mapping) or payload.get("schema") != "zoe_evolution_proposal":
        return None
    proposal = payload.get("proposal")
    if not isinstance(proposal, Mapping):
        return None
    return payload


def normalize_mcp_evolution_proposal_type(proposal_type: str | None) -> str:
    """Normalize legacy MCP proposal types before DB and contract storage."""

    return normalize_legacy_evolution_proposal_type(proposal_type)


def normalize_legacy_evolution_proposal_type(proposal_type: str | None) -> str:
    """Normalize legacy proposal types before DB and contract storage."""

    normalized = (proposal_type or "intent_pattern").strip().lower()
    if normalized not in _SIGNAL_BY_LEGACY_TYPE:
        return "intent_pattern"
    return normalized


def _evidence_refs(source_ref: str, evidence: str) -> tuple[str, ...]:
    refs = [source_ref]
    if evidence.strip():
        refs.append(f"evidence:{source_ref}")
    return tuple(refs)


__all__ = [
    "CONTRACT_ENVELOPE_VERSION",
    "build_existing_zoe_proposal_candidate",
    "build_legacy_evolution_proposal_contract",
    "build_mcp_evolution_proposal_contract",
    "dump_legacy_evolution_proposal_contract",
    "dump_mcp_evolution_proposal_contract",
    "legacy_signal_type_for_proposal_type",
    "load_proposal_contract_snapshot",
    "normalize_legacy_evolution_proposal_type",
    "normalize_mcp_evolution_proposal_type",
]
