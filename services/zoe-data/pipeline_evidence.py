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
BlockClassification = Literal["scope_split_required"]
# `cross_review` MUST NEVER appear in any phase's required-evidence set below
# (or in any EvidenceProfile). It is discharged ONLY via the explicit, flag-gated
# `human` subtraction in missing_required_evidence; requiring it directly would
# let a bare kind-marker satisfy a phase and bypass the provenance check.
EvidenceKind = Literal[
    "tool", "test", "validator", "pr", "greptile", "human", "log", "cross_review"
]
EvidenceProfile = Literal["default", "audit", "code", "health"]
TransitionOutcome = Literal[
    "start",
    "complete",
    "skip_implementation",
    "block",
    "request_changes",
    "verification_failed",
    "merge_blocked",
    "retry_evidence",
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
    "implement": {"tool", "pr"},
    "verify": {"test", "validator"},
    "review": {"human"},
    "closeout": {"greptile"},
    "retro": {"log"},
}

_EVIDENCE_PROFILES: dict[EvidenceProfile, dict[PipelinePhase, set[EvidenceKind]]] = {
    # Default is the normal code-producing path, so implement completion needs PR evidence.
    "default": _REQUIRED_EVIDENCE,
    "code": _REQUIRED_EVIDENCE,
    "audit": {
        "scout": {"tool"},
        "implement": {"tool"},
        "verify": {"validator"},
        "review": {"human"},
        "closeout": {"log"},
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

# A cross_review sign-off is only trustworthy when bound to the reviewed commit
# — a full or abbreviated git SHA (lowercased before matching), mirroring how
# validator/handoff evidence is tied to a content_hash rather than a bare claim.
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
# Only an explicit approve verdict counts; a blocking/request-changes verdict
# (or any unrecognised value) never substitutes for a human review sign-off.
_CROSS_REVIEW_APPROVE_VERDICTS = frozenset({"approve", "approved"})

# The set of MODEL PLATFORMS the cross-vendor gate recognises. An identity that
# does not resolve into this set is UNKNOWN, and the sign-off check fails closed
# on it (see valid_cross_review_signoff) — an unrecognised reviewer/implementer
# is never treated as a valid distinct vendor that passes the cross-vendor rule.
_KNOWN_PLATFORMS: frozenset[str] = frozenset({"anthropic", "openai", "openrouter"})

# Vendor identities are normalised to their MODEL PLATFORM before the
# cross-vendor comparison so aliases cannot smuggle a same-vendor reviewer past
# the gate (AGENTS.md cross-vendor routing: "the reviewer's platform must differ
# from the implementer's"). The whole Anthropic-hosted family collapses to
# `anthropic` — claude/claude_code, the `claude-sdk` harness, every
# Opus/Sonnet/Haiku tier and Fable (`claude-fable-5`); the OpenAI / ChatGPT /
# codex family (incl. every `gpt-*` id like `gpt-5.4`) to `openai`; pi/GLM
# (`glm-*`) to `openrouter`. Prefix families below catch the versioned model ids
# the harness actually configures without enumerating every point release.
#
# NOT mapped, on purpose: `cursor` (Bugbot — disabled), `hermes`, and
# `opencode`/`openclaw` (retired). Their underlying platform is not deterministic
# from the token, so they resolve to UNKNOWN and fail closed rather than be
# trusted as a distinct vendor. Add a mapping here only once a harness's platform
# is fixed and known.
_VENDOR_ALIASES: dict[str, str] = {
    "claude": "anthropic",
    "claude_code": "anthropic",
    "claude-code": "anthropic",
    "claudecode": "anthropic",
    "claude-sdk": "anthropic",
    "claude-fable-5": "anthropic",
    "claude-test": "anthropic",
    "anthropic": "anthropic",
    "opus": "anthropic",
    "sonnet": "anthropic",
    "haiku": "anthropic",
    "fable": "anthropic",
    "codex": "openai",
    "openai": "openai",
    "chatgpt": "openai",
    "gpt": "openai",
    "pi": "openrouter",
    "openrouter": "openrouter",
    "glm": "openrouter",
}

# Prefix -> platform for versioned model ids (matched only after an exact alias
# miss). `claude-*` -> anthropic covers claude-3-opus / claude-fable-5 / any
# future tier; `gpt*` -> openai covers gpt-5.4 / gpt-5.5-medium; `glm*` ->
# openrouter covers glm-4.6 / glm5.2.
_VENDOR_PREFIXES: tuple[tuple[str, str], ...] = (
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("glm", "openrouter"),
)


def normalize_vendor(name: str | None) -> str:
    """Collapse a reviewer/implementer identity to its canonical model platform.

    Returns "" for an empty/whitespace identity. An exact alias (claude_code,
    claude-sdk, codex, fable, …) maps to its platform, then a versioned-id prefix
    (claude-fable-5, gpt-5.4, glm-4.6) maps to its family; anything else falls
    back to its own lowercased token so distinct unknown vendors stay distinct
    while identical ones stay equal. Callers that must fail closed on an
    unrecognised vendor check the result against ``_KNOWN_PLATFORMS`` — a
    fall-through token is deliberately NOT a member.
    """
    token = (name or "").strip().lower()
    if not token:
        return ""
    if token in _VENDOR_ALIASES:
        return _VENDOR_ALIASES[token]
    for prefix, platform in _VENDOR_PREFIXES:
        if token.startswith(prefix):
            return platform
    return token

_PROFILE_TAG_RE = re.compile(r"evidence_profile:\s*(\w+)", re.I)
_SCOPE_SPLIT_REASON_RE = re.compile(
    r"\b(?:PROTOCOL_VIOLATION|TURN_BUDGET|CONTEXT_LIMIT|TOKEN_LIMIT|TOO_BROAD|"
    r"SCOPE_SPLIT_REQUIRED|NEEDS_SPLIT)\b",
    re.I,
)


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
    journal_revision: int = 0
    task_ref: str
    phase: PipelinePhase = "implement"
    status: PipelineStatus = "todo"
    evidence_profile: EvidenceProfile = "default"
    # Flag (default OFF => unchanged behaviour): when True, an approving
    # cross_review sign-off with verifiable provenance may satisfy the review
    # phase in place of `human`. Live pipelines keep human-in-the-loop review
    # until a downstream producer both emits the evidence and sets this True.
    allow_cross_review_signoff: bool = False
    # TRUSTED anchors for the cross_review provenance check — set by the harness
    # dispatcher / PR-creation seam, NEVER derived from the evidence item itself.
    # `pr_head_sha`: the authoritative PR head the sign-off must be bound to (an
    # approval's commit_sha must match it EXACTLY). `implementer_platform`: the
    # MODEL PLATFORM that produced the diff, so the reviewer can be required to
    # differ from it. Both unset => no autonomous cross_review can clear review
    # (fail-closed: there is nothing trustworthy to compare against).
    pr_head_sha: str | None = None
    implementer_platform: str | None = None
    attempts: dict[PipelinePhase, int] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    history: list[TransitionRecord] = Field(default_factory=list)
    last_block_fingerprint: str | None = None
    repeated_block_count: int = 0
    block_classification: BlockClassification | None = None
    split_packet: dict[str, Any] | None = None

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


def scope_split_required(
    phase: PipelinePhase,
    reason: str,
    *,
    repeated: bool = False,
    explicit: bool = False,
) -> bool:
    """Classify broad failures that should become split/escalation packets."""
    if explicit:
        return phase in {"scout", "implement"}
    if phase != "implement":
        return False
    return repeated and bool(_SCOPE_SPLIT_REASON_RE.search(reason or ""))


def build_scope_split_packet(
    task_ref: str,
    phase: PipelinePhase,
    reason: str,
    *,
    source: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Machine-readable handoff for creating smaller child Multica issues."""
    packet = dict(existing or {})
    worker_reason = packet.get("reason")
    block_reason = (reason or "scope split required")[:1000]
    packet.update(
        {
            "schema_version": 1,
            "kind": "scope_split_required",
            "parent_task_ref": task_ref,
            "blocked_phase": phase,
            "reason": worker_reason or block_reason,
            "block_reason": block_reason,
            "source": source,
            "recommended_action": (
                "Create one or more child Multica issues with narrow acceptance criteria, "
                "then link them to this blocked parent."
            ),
        }
    )
    packet.setdefault(
        "child_issue_template",
        {
            "title": "<parent identifier>: <small deliverable>",
            "description": (
                "Scope: <one narrow behavior or file area>\n"
                "Acceptance criteria:\n"
                "- <observable outcome>\n"
                "Evidence required:\n"
                "- focused tests or validators"
            ),
            "labels": ["hermes", "needs-split"],
        },
    )
    return packet


def issue_evidence_profile(issue: dict | None) -> EvidenceProfile:
    """Resolve per-issue evidence requirements from Multica metadata or description tags."""
    issue = issue or {}
    meta = issue.get("metadata") or {}
    try:
        from multica_ticket_contract import parse_ticket_block

        meta = {**parse_ticket_block(issue.get("description") or ""), **meta}
    except Exception:
        pass
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


def issue_allows_cross_review_signoff(issue: dict | None) -> bool:
    """Resolve the cross_review-substitution flag from STRUCTURED Multica metadata.

    Mirrors ``issue_evidence_profile`` but reads ONLY structured sources — the
    explicit ``metadata`` key and the ``parse_ticket_block`` ticket-block tag —
    with a truthy value in {"1", "true", "yes"}. There is deliberately NO free-text
    title/description substring fallback: this flag gates an autonomous
    cross_review -> squash-merge -> auto-deploy path, so a ticket that merely
    *mentions* the flag name (or one that explicitly opts out) must never flip it
    on. Absence or an explicit "false"/"no"/"0" resolves to False.
    """
    issue = issue or {}
    meta = issue.get("metadata") or {}
    try:
        from multica_ticket_contract import parse_ticket_block

        meta = {**parse_ticket_block(issue.get("description") or ""), **meta}
    except Exception:
        pass
    raw = meta.get("allow_cross_review_signoff")
    if raw is None:
        raw = issue.get("allow_cross_review_signoff")
    return str(raw or "").strip().lower() in {"1", "true", "yes"}


def issue_implementer_platform(issue: dict | None) -> str | None:
    """Resolve the implementer's MODEL PLATFORM from STRUCTURED Multica metadata.

    Reads ONLY the structured ``implementer_platform`` key (explicit ``metadata``
    or the ``parse_ticket_block`` ticket-block tag) — never free-text — and
    normalises aliases (claude_code -> anthropic, codex -> openai, …) so the
    recorded platform is directly comparable with a reviewer's. Returns None when
    absent/blank, which keeps the cross_review provenance check fail-closed until
    the trusted dispatcher records who implemented the diff.
    """
    issue = issue or {}
    meta = issue.get("metadata") or {}
    try:
        from multica_ticket_contract import parse_ticket_block

        meta = {**parse_ticket_block(issue.get("description") or ""), **meta}
    except Exception:
        pass
    raw = meta.get("implementer_platform")
    if raw is None:
        raw = issue.get("implementer_platform")
    normalized = normalize_vendor(str(raw or ""))
    return normalized or None


def required_evidence_for(state: PipelineState, phase: PipelinePhase | None = None) -> set[EvidenceKind]:
    selected = phase or state.phase
    profile_map = _EVIDENCE_PROFILES.get(state.evidence_profile, _REQUIRED_EVIDENCE)
    return profile_map.get(selected, set())


def valid_cross_review_signoff(state: PipelineState) -> bool:
    """True if a trustworthy APPROVING cross_review sign-off is present.

    A cross_review kind marker is not enough on its own — that would let a bare
    self-claim ("I reviewed it, looks good") clear review. To count as a stand-in
    for a human sign-off the entry must carry verifiable provenance, mirroring how
    validator/handoff evidence is bound to a content_hash rather than trusted on
    its say-so. The evidence must carry:

    - ``passed is True`` (an unresolved/failed review never counts),
    - a non-empty ``reviewer`` (or ``vendor``) naming the reviewing agent/vendor,
    - an explicit ``verdict`` of approve — a ``blocking``/``request_changes``
      verdict (or any unrecognised value) is rejected,
    - a ``commit_sha`` (or ``sha``) that looks like a real git SHA.

    And it is checked against the TRUSTED anchors on ``state`` (set by the harness,
    not by the evidence), so a producer cannot self-certify the two facts that
    matter:

    - the approval's ``commit_sha`` must EXACTLY match ``state.pr_head_sha`` — a
      stale (post-review) or fabricated SHA that merely *looks* like a git hash no
      longer clears review,
    - the reviewer's normalised platform must DIFFER from
      ``state.implementer_platform`` — a same-vendor or self-asserted reviewer
      never satisfies the cross-vendor rule,
    - BOTH the reviewer's and the implementer's normalised platform must resolve
      to a KNOWN platform (``_KNOWN_PLATFORMS``). An unrecognised token is not a
      valid distinct vendor: it fails closed rather than clearing the gate by
      merely differing from a known implementer.

    Both anchors are required: if ``state`` has no recorded PR head or no recorded
    (known) implementer platform, there is nothing trustworthy to compare against
    and the sign-off is rejected (fail-closed).
    """
    impl_platform = normalize_vendor(state.implementer_platform)
    if impl_platform not in _KNOWN_PLATFORMS:
        return False
    expected_head = str(state.pr_head_sha or "").strip().lower()
    if not _SHA_RE.match(expected_head):
        return False
    for item in state.evidence:
        if item.kind != "cross_review" or item.passed is not True:
            continue
        meta = item.metadata or {}
        reviewer = str(meta.get("reviewer") or meta.get("vendor") or "").strip()
        verdict = str(meta.get("verdict") or "").strip().lower()
        commit_sha = str(meta.get("commit_sha") or meta.get("sha") or "").strip().lower()
        if not reviewer:
            continue
        if verdict not in _CROSS_REVIEW_APPROVE_VERDICTS:
            continue
        if not _SHA_RE.match(commit_sha):
            continue
        if commit_sha != expected_head:
            continue
        reviewer_platform = normalize_vendor(reviewer)
        # Fail closed on an unrecognised reviewer: an unknown token must NOT be
        # treated as a valid distinct vendor just because it differs from a known
        # implementer platform.
        if reviewer_platform not in _KNOWN_PLATFORMS:
            continue
        if reviewer_platform == impl_platform:
            continue
        return True
    return False


def missing_required_evidence(state: PipelineState, phase: PipelinePhase | None = None) -> set[EvidenceKind]:
    selected = phase or state.phase
    missing = required_evidence_for(state, selected) - evidence_kinds(state)
    # Review acceptance = human OR a valid approving cross_review, gated behind
    # allow_cross_review_signoff (default OFF preserves human-required behaviour).
    if (
        selected == "review"
        and "human" in missing
        and state.allow_cross_review_signoff
        and valid_cross_review_signoff(state)
    ):
        missing = missing - {"human"}
    return missing


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
    elif outcome == "retry_evidence":
        # Bounded re-arm of the verify phase to todo WITHOUT clearing evidence.
        # Used when verify completed but the evidence gate is missing a required
        # kind it should have produced (the focused pytest -> `test` evidence).
        # Preserving the evidence means the re-dispatched worker only needs to
        # supply the missing kind rather than redo everything. The caller is
        # responsible for bounding this by attempt count so it cannot loop.
        # Restricted to verify (the only intended call-site) so a stray caller
        # cannot silently re-arm an earlier phase, mirroring request_changes /
        # verification_failed phase guards.
        if state.phase != "verify":
            raise ValueError("retry_evidence is only valid from verify")
        next_phase = state.phase
        next_status = "todo"
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
    elif outcome == "skip_implementation":
        if state.phase not in {"scout", "implement"}:
            raise ValueError("skip_implementation is only valid from scout or implement")
        if not can_complete_phase(state):
            missing = ", ".join(sorted(missing_required_evidence(state)))
            raise ValueError(f"{state.phase} is missing required evidence: {missing}")
        next_phase = "verify"
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
    evidence_profile = "audit" if outcome == "skip_implementation" else state.evidence_profile
    return state.model_copy(
        update={
            "phase": next_phase,
            "status": next_status,
            "attempts": attempts,
            "evidence": evidence,
            "evidence_profile": evidence_profile,
            "history": history,
        }
    )
