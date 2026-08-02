import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import pytest

from pipeline_evidence import (
    EvidenceItem,
    PipelineState,
    block_fingerprint,
    build_scope_split_packet,
    can_complete_phase,
    content_hash,
    missing_required_evidence,
    record_block_fingerprint,
    scope_split_required,
    transition,
    with_evidence,
)


def test_evidence_metadata_rejects_secret_fields():
    with pytest.raises(ValueError, match="secret fields"):
        EvidenceItem(kind="tool", summary="used graphify", metadata={"access_token": "abc"})


def test_explicit_scope_split_is_allowed_from_scout():
    assert scope_split_required(
        "scout",
        "SCOPE_SPLIT_REQUIRED: parent has several deliverables",
        explicit=True,
    ) is True


def test_repeated_scope_split_is_still_implement_only():
    assert scope_split_required(
        "scout",
        "SCOPE_SPLIT_REQUIRED: repeated scout budget",
        repeated=True,
    ) is False


def test_build_scope_split_packet_preserves_worker_reason():
    packet = build_scope_split_packet(
        "multica:1",
        "implement",
        "SCOPE_SPLIT_REQUIRED: too broad",
        source="handoff",
        existing={
            "reason": "Separate backend schema work from UI wiring",
            "child_issue_template": {"title": "ZOE-1: schema child"},
        },
    )
    assert packet["reason"] == "Separate backend schema work from UI wiring"
    assert packet["block_reason"] == "SCOPE_SPLIT_REQUIRED: too broad"
    assert packet["child_issue_template"]["title"] == "ZOE-1: schema child"


def test_implement_requires_tool_evidence_before_complete():
    state = PipelineState(task_ref="multica:1", phase="implement")

    assert can_complete_phase(state) is False
    assert missing_required_evidence(state) == {"pr", "tool"}
    with pytest.raises(ValueError, match="missing required evidence"):
        transition(state, "complete")

    state = with_evidence(state, EvidenceItem(kind="tool", summary="graphify query ran", passed=True))
    assert missing_required_evidence(state) == {"pr"}
    state = with_evidence(state, EvidenceItem(kind="pr", summary="opened PR for implement", passed=True))
    next_state = transition(state, "complete")

    assert next_state.phase == "verify"
    assert next_state.status == "todo"


def test_verify_requires_test_and_validator_evidence():
    state = PipelineState(task_ref="multica:1", phase="verify", status="running")
    state = with_evidence(state, EvidenceItem(kind="test", summary="pytest passed", passed=True))

    assert missing_required_evidence(state) == {"validator"}

    state = with_evidence(state, EvidenceItem(kind="validator", summary="validate_structure passed", passed=True))
    assert transition(state, "complete").phase == "review"


def test_failed_evidence_does_not_satisfy_gate():
    state = PipelineState(task_ref="multica:1", phase="verify")
    state = with_evidence(
        state,
        EvidenceItem(kind="test", summary="pytest failed", passed=False),
        EvidenceItem(kind="validator", summary="validator passed", passed=True),
    )

    assert missing_required_evidence(state) == {"test"}


def test_indeterminate_evidence_does_not_satisfy_gate():
    state = PipelineState(task_ref="multica:1", phase="verify")
    state = with_evidence(
        state,
        EvidenceItem(kind="test", summary="pytest command recorded"),
        EvidenceItem(kind="validator", summary="validator passed", passed=True),
    )

    assert missing_required_evidence(state) == {"test"}


def test_review_change_request_loops_to_implement():
    state = PipelineState(task_ref="multica:1", phase="review", status="running")
    state = with_evidence(state, EvidenceItem(kind="tool", summary="old implementation evidence", passed=True))
    next_state = transition(state, "request_changes", reason="missing rollback test")

    assert next_state.phase == "implement"
    assert next_state.status == "todo"
    assert next_state.evidence == []
    assert can_complete_phase(next_state) is False
    assert next_state.history[-1].reason == "missing rollback test"


def test_loop_back_outcomes_are_phase_scoped():
    with pytest.raises(ValueError, match="request_changes is only valid from review"):
        transition(PipelineState(task_ref="multica:1", phase="verify"), "request_changes")

    with pytest.raises(ValueError, match="verification_failed is only valid from verify"):
        transition(PipelineState(task_ref="multica:1", phase="closeout"), "verification_failed")

    state = PipelineState(task_ref="multica:1", phase="verify", status="running")
    next_state = transition(state, "verification_failed", reason="pytest failed")

    assert next_state.phase == "implement"
    assert next_state.status == "todo"
    assert next_state.history[-1].reason == "pytest failed"


def test_retry_evidence_rearms_phase_to_todo_keeping_evidence():
    # verify completed with validator+pr evidence but no `test` evidence.
    state = PipelineState(
        task_ref="multica:1",
        phase="verify",
        status="running",
        attempts={"verify": 1},
        evidence=[
            EvidenceItem(kind="validator", summary="validate_structure passed", passed=True),
            EvidenceItem(kind="pr", summary="https://github.com/o/r/pull/1", artifact="https://github.com/o/r/pull/1", passed=True),
        ],
    )
    next_state = transition(state, "retry_evidence", reason="needs pytest")

    # Re-armed to the SAME phase as todo, evidence preserved, attempts untouched
    # (the increment happens on the next start).
    assert next_state.phase == "verify"
    assert next_state.status == "todo"
    assert [e.kind for e in next_state.evidence] == ["validator", "pr"]
    assert next_state.attempts == {"verify": 1}
    assert next_state.history[-1].outcome == "retry_evidence"
    assert next_state.history[-1].reason == "needs pytest"
    # Still cannot complete until the missing `test` evidence arrives.
    assert can_complete_phase(next_state) is False


def test_start_records_attempts_per_phase():
    state = PipelineState(task_ref="multica:1", phase="implement")

    state = transition(state, "start")
    state = transition(state, "start")

    assert state.status == "running"
    assert state.attempts["implement"] == 2


def test_block_preserves_phase_and_evidence_until_restarted():
    state = PipelineState(task_ref="multica:1", phase="verify", status="running")
    state = with_evidence(state, EvidenceItem(kind="test", summary="pytest passed", passed=True))

    blocked = transition(state, "block", reason="validator unavailable")
    restarted = transition(blocked, "start")

    assert blocked.phase == "verify"
    assert blocked.status == "blocked"
    assert blocked.evidence == state.evidence
    assert restarted.phase == "verify"
    assert restarted.status == "running"
    assert restarted.attempts["verify"] == 1


def test_merge_blocked_is_closeout_only_and_can_restart():
    state = PipelineState(task_ref="multica:1", phase="closeout", status="running")
    state = with_evidence(state, EvidenceItem(kind="greptile", summary="review passed", passed=True))

    blocked = transition(state, "merge_blocked", reason="branch policy")
    restarted = transition(blocked, "start")

    assert blocked.phase == "closeout"
    assert blocked.status == "blocked"
    assert blocked.evidence == state.evidence
    assert restarted.phase == "closeout"
    assert restarted.status == "running"
    assert restarted.attempts["closeout"] == 1

    with pytest.raises(ValueError, match="only valid from closeout"):
        transition(PipelineState(task_ref="multica:1", phase="retro"), "merge_blocked")


def test_scout_requires_tool_evidence_before_complete():
    state = PipelineState(task_ref="multica:1", phase="scout")
    assert can_complete_phase(state) is False
    state = with_evidence(state, EvidenceItem(kind="tool", summary="graphify path query", passed=True))
    next_state = transition(state, "complete")
    assert next_state.phase == "implement"


def test_retro_complete_marks_pipeline_done():
    state = PipelineState(task_ref="multica:1", phase="retro")
    state = with_evidence(state, EvidenceItem(kind="log", summary="retro captured", passed=True))

    done = transition(state, "complete")

    assert done.phase == "retro"
    assert done.status == "done"


def test_content_hash_is_stable():
    assert content_hash("validate_structure passed") == content_hash("validate_structure passed")
    assert content_hash("a") != content_hash("b")


def test_block_fingerprint_aborts_after_two_identical():
    reason = "WORKTREE_NOT_READY: missing worktree"
    fp = block_fingerprint("implement", reason)
    state = PipelineState(task_ref="multica:1", phase="implement", status="running")

    state, abort = record_block_fingerprint(state, fp)
    assert abort is False
    assert state.repeated_block_count == 1

    state, abort = record_block_fingerprint(state, fp)
    assert abort is True
    assert state.repeated_block_count == 2


def test_issue_evidence_profile_audit_from_metadata():
    from pipeline_evidence import issue_evidence_profile, missing_required_evidence

    issue = {"metadata": {"evidence_profile": "audit"}}
    assert issue_evidence_profile(issue) == "audit"
    state = PipelineState(task_ref="multica:1", phase="verify", evidence_profile="audit")
    state = with_evidence(
        state,
        EvidenceItem(kind="validator", summary="validate_structure pass", passed=True),
    )
    assert missing_required_evidence(state) == set()


def test_audit_profile_verify_does_not_require_test():
    from pipeline_evidence import issue_evidence_profile

    issue = {"description": "audit-only map of chat router"}
    assert issue_evidence_profile(issue) == "audit"
    state = PipelineState(task_ref="multica:1", phase="verify", evidence_profile="audit")
    state = with_evidence(
        state,
        EvidenceItem(kind="validator", summary="validators pass", passed=True),
    )
    assert missing_required_evidence(state) == set()


def test_verify_validator_hash_must_match_implement():
    from pipeline_evidence import verify_validator_hash_matches

    state = PipelineState(task_ref="multica:1", phase="verify")
    state = with_evidence(
        state,
        EvidenceItem(
            kind="validator",
            summary="impl",
            content_hash="aaa",
            passed=True,
            metadata={"phase": "implement", "source": "handoff"},
        ),
        EvidenceItem(
            kind="validator",
            summary="verify",
            content_hash="bbb",
            passed=True,
            metadata={"phase": "verify", "source": "handoff"},
        ),
    )
    assert verify_validator_hash_matches(state) is False


def test_verify_validator_hash_ignores_harness_sync_mismatch():
    from pipeline_evidence import verify_validator_hash_matches

    state = PipelineState(task_ref="multica:1", phase="verify")
    state = with_evidence(
        state,
        EvidenceItem(
            kind="validator",
            summary="impl harness",
            content_hash="aaa",
            passed=True,
            metadata={"phase": "implement", "source": "harness"},
        ),
        EvidenceItem(
            kind="validator",
            summary="verify harness",
            content_hash="bbb",
            passed=True,
            metadata={"phase": "verify", "source": "harness"},
        ),
    )
    assert verify_validator_hash_matches(state) is True


def test_audit_profile_closeout_requires_log_not_greptile():
    state = PipelineState(task_ref="multica:1", phase="closeout", evidence_profile="audit")
    assert missing_required_evidence(state) == {"log"}
    state = with_evidence(state, EvidenceItem(kind="log", summary="audit-only closeout", passed=True))
    assert missing_required_evidence(state) == set()


def test_skip_implementation_rejects_implement_without_evidence():
    state = PipelineState(
        task_ref="multica:no-code",
        phase="implement",
        status="blocked",
    )

    with pytest.raises(ValueError, match="implement is missing required evidence: pr, tool"):
        transition(state, "skip_implementation")


def test_skip_implementation_rejects_scout_without_evidence():
    state = PipelineState(
        task_ref="multica:no-scout-evidence",
        phase="scout",
        status="running",
    )

    with pytest.raises(ValueError, match="scout is missing required evidence: tool"):
        transition(state, "skip_implementation")


def test_skip_implementation_rejects_invalid_phase():
    state = PipelineState(
        task_ref="multica:already-verifying",
        phase="verify",
        status="running",
    )

    with pytest.raises(
        ValueError,
        match="skip_implementation is only valid from scout or implement",
    ):
        transition(state, "skip_implementation")


def test_skip_implementation_uses_audit_evidence_profile():
    state = PipelineState(task_ref="multica:no-code", phase="scout", status="running")
    state = with_evidence(
        state,
        EvidenceItem(kind="tool", summary="merged work inspected", passed=True),
    )

    skipped = transition(state, "skip_implementation")

    assert skipped.phase == "verify"
    assert skipped.evidence_profile == "audit"


# --- cross_review sign-off: an autonomous different-vendor review may clear the
# review phase in place of `human`, but only with verifiable provenance and only
# when the allow_cross_review_signoff flag is on (default OFF => unchanged). ---

_GOOD_SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
_OTHER_SHA = "0f1e2d3c4b5a69788796a5b4c3d2e1f001234567"


def _approving_cross_review(**overrides):
    metadata = {"reviewer": "codex", "verdict": "approve", "commit_sha": _GOOD_SHA}
    metadata.update(overrides.pop("metadata", {}))
    return EvidenceItem(
        kind="cross_review",
        summary=overrides.pop("summary", "codex cross-review approved the diff"),
        passed=overrides.pop("passed", True),
        metadata=metadata,
    )


def _review_state(**overrides):
    """A review-phase state with the trusted anchors set (flag on, PR head bound,
    implementer = claude_code so the default `codex` reviewer is cross-vendor)."""
    params = dict(
        task_ref="multica:1",
        phase="review",
        allow_cross_review_signoff=True,
        pr_head_sha=_GOOD_SHA,
        implementer_platform="claude_code",
    )
    params.update(overrides)
    return PipelineState(**params)


def test_cross_review_signoff_satisfies_review_when_flag_on():
    state = _review_state(status="running")
    state = with_evidence(state, _approving_cross_review())

    assert missing_required_evidence(state) == set()
    assert can_complete_phase(state) is True

    next_state = transition(state, "complete")
    assert next_state.phase == "closeout"
    assert next_state.status == "todo"


def test_cross_review_without_sha_or_verdict_does_not_satisfy():
    from pipeline_evidence import valid_cross_review_signoff

    # Missing commit SHA.
    no_sha = _review_state()
    no_sha = with_evidence(no_sha, _approving_cross_review(metadata={"commit_sha": ""}))
    assert missing_required_evidence(no_sha) == {"human"}
    assert valid_cross_review_signoff(no_sha) is False

    # Missing verdict.
    no_verdict = _review_state()
    no_verdict = with_evidence(no_verdict, _approving_cross_review(metadata={"verdict": ""}))
    assert missing_required_evidence(no_verdict) == {"human"}

    # Empty reviewer/vendor identity (unattributable) => does not satisfy.
    no_reviewer = _review_state()
    no_reviewer = with_evidence(no_reviewer, _approving_cross_review(metadata={"reviewer": "", "vendor": ""}))
    assert missing_required_evidence(no_reviewer) == {"human"}
    assert valid_cross_review_signoff(no_reviewer) is False

    # Bare self-claim with no provenance metadata at all.
    bare = _review_state()
    bare = with_evidence(
        bare,
        EvidenceItem(kind="cross_review", summary="I reviewed it, looks good", passed=True),
    )
    assert missing_required_evidence(bare) == {"human"}
    with pytest.raises(ValueError, match="missing required evidence"):
        transition(bare, "complete")


def test_cross_review_sha_must_match_pr_head():
    """A well-formed but stale/fabricated SHA no longer clears review — the
    approval's commit_sha must EXACTLY match the state's authoritative PR head."""
    from pipeline_evidence import valid_cross_review_signoff

    # Evidence SHA is a valid git hash but not the recorded PR head => rejected.
    mismatched = _review_state(pr_head_sha=_GOOD_SHA)
    mismatched = with_evidence(mismatched, _approving_cross_review(metadata={"commit_sha": _OTHER_SHA}))
    assert valid_cross_review_signoff(mismatched) is False
    assert missing_required_evidence(mismatched) == {"human"}

    # No authoritative PR head recorded at all => nothing to bind to => rejected.
    no_head = _review_state(pr_head_sha=None)
    no_head = with_evidence(no_head, _approving_cross_review())
    assert valid_cross_review_signoff(no_head) is False
    assert missing_required_evidence(no_head) == {"human"}

    # Exact match (case-insensitive) => accepted.
    exact = _review_state(pr_head_sha=_GOOD_SHA.upper())
    exact = with_evidence(exact, _approving_cross_review(metadata={"commit_sha": _GOOD_SHA}))
    assert valid_cross_review_signoff(exact) is True


def test_cross_review_reviewer_must_differ_from_implementer():
    """The reviewer's normalised platform must differ from the implementer's;
    aliases of the same vendor cannot slip a same-vendor reviewer through."""
    from pipeline_evidence import valid_cross_review_signoff

    # No implementer platform recorded => cannot prove cross-vendor => rejected.
    no_impl = _review_state(implementer_platform=None)
    no_impl = with_evidence(no_impl, _approving_cross_review())
    assert valid_cross_review_signoff(no_impl) is False

    # Same literal vendor => rejected.
    same = _review_state(implementer_platform="codex")
    same = with_evidence(same, _approving_cross_review(metadata={"reviewer": "codex"}))
    assert valid_cross_review_signoff(same) is False

    # Aliases of the same platform (claude_code vs sonnet -> both anthropic) => rejected.
    alias = _review_state(implementer_platform="claude_code")
    alias = with_evidence(alias, _approving_cross_review(metadata={"reviewer": "sonnet"}))
    assert valid_cross_review_signoff(alias) is False

    # openai family both sides (codex implementer, openai reviewer) => rejected.
    openai_both = _review_state(implementer_platform="codex")
    openai_both = with_evidence(openai_both, _approving_cross_review(metadata={"reviewer": "openai"}))
    assert valid_cross_review_signoff(openai_both) is False

    # Genuinely different platforms (anthropic implementer, openai reviewer) => accepted.
    cross = _review_state(implementer_platform="claude_code")
    cross = with_evidence(cross, _approving_cross_review(metadata={"reviewer": "codex"}))
    assert valid_cross_review_signoff(cross) is True


def test_normalize_vendor_collapses_aliases():
    from pipeline_evidence import normalize_vendor

    assert normalize_vendor("claude_code") == normalize_vendor("Sonnet") == "anthropic"
    assert normalize_vendor("Fable") == "anthropic"
    assert normalize_vendor("codex") == normalize_vendor("OpenAI") == "openai"
    assert normalize_vendor("pi") == normalize_vendor("glm") == "openrouter"
    assert normalize_vendor("") == ""
    assert normalize_vendor(None) == ""
    # Unknown vendors map to their own token (distinct stays distinct).
    assert normalize_vendor("acme") == "acme"
    assert normalize_vendor("acme") != normalize_vendor("other")


def test_normalize_vendor_maps_real_repo_identifiers():
    """This repo's REAL configured reviewer/harness/model ids must collapse to
    their platform — a missed alias is a same-vendor bypass (the Fable/claude-sdk
    reviewer that previously fell through to its own distinct token)."""
    from pipeline_evidence import normalize_vendor

    # Anthropic family: harness id, Fable id, and every tier.
    for anthropic_id in ("claude-fable-5", "claude-sdk", "claude-test", "fable", "opus", "Sonnet", "HAIKU"):
        assert normalize_vendor(anthropic_id) == "anthropic", anthropic_id
    # Versioned claude-* ids collapse via the prefix family too.
    assert normalize_vendor("claude-3-opus") == "anthropic"

    # OpenAI family: codex, the gpt-* versioned ids, and brand aliases.
    for openai_id in ("codex", "gpt", "gpt-5.4", "gpt-5.5-medium", "OpenAI", "chatgpt"):
        assert normalize_vendor(openai_id) == "openai", openai_id

    # pi / OpenRouter family, including versioned glm-* ids.
    for or_id in ("pi", "openrouter", "glm", "glm-4.6", "GLM5.2"):
        assert normalize_vendor(or_id) == "openrouter", or_id


def test_cross_review_real_anthropic_reviewer_is_same_vendor_rejected():
    """The reported bug: an Anthropic reviewer reporting this repo's REAL id
    (claude-fable-5 / claude-sdk) must normalise to `anthropic` and be rejected
    as same-vendor against an `anthropic` implementer — not slip through as a
    distinct unrecognised token."""
    from pipeline_evidence import valid_cross_review_signoff

    for reviewer_id in ("claude-fable-5", "claude-sdk", "fable", "opus"):
        state = _review_state(implementer_platform="anthropic")
        state = with_evidence(state, _approving_cross_review(metadata={"reviewer": reviewer_id}))
        assert valid_cross_review_signoff(state) is False, reviewer_id
        assert missing_required_evidence(state) == {"human"}, reviewer_id


def test_cross_review_unknown_reviewer_fails_closed():
    """An unrecognised/garbage reviewer identity is NOT a valid distinct vendor —
    it must fail closed rather than clear the gate by merely differing from a
    known implementer platform."""
    from pipeline_evidence import valid_cross_review_signoff

    for junk in ("acme", "totally-made-up", "reviewer-1", "x"):
        state = _review_state(implementer_platform="anthropic")
        state = with_evidence(state, _approving_cross_review(metadata={"reviewer": junk}))
        assert valid_cross_review_signoff(state) is False, junk
        assert missing_required_evidence(state) == {"human"}, junk


def test_cross_review_unknown_implementer_fails_closed():
    """An implementer platform that cannot be resolved to a KNOWN platform leaves
    nothing trustworthy to compare against => sign-off rejected."""
    from pipeline_evidence import valid_cross_review_signoff

    state = _review_state(implementer_platform="mystery-harness")
    state = with_evidence(state, _approving_cross_review(metadata={"reviewer": "codex"}))
    assert valid_cross_review_signoff(state) is False
    assert missing_required_evidence(state) == {"human"}


def test_cross_review_known_cross_vendor_pair_still_accepted():
    """Regression guard: a genuine cross-vendor pair (codex reviewer vs anthropic
    implementer) still clears review after the fail-closed tightening."""
    from pipeline_evidence import valid_cross_review_signoff

    state = _review_state(implementer_platform="anthropic")
    state = with_evidence(state, _approving_cross_review(metadata={"reviewer": "codex"}))
    assert valid_cross_review_signoff(state) is True
    assert missing_required_evidence(state) == set()


def test_cross_review_flag_off_still_requires_human():
    # Flag default OFF: a fully valid approving cross_review is ignored.
    state = PipelineState(
        task_ref="multica:1",
        phase="review",
        pr_head_sha=_GOOD_SHA,
        implementer_platform="claude_code",
    )
    assert state.allow_cross_review_signoff is False
    state = with_evidence(state, _approving_cross_review())

    assert missing_required_evidence(state) == {"human"}
    assert can_complete_phase(state) is False
    with pytest.raises(ValueError, match="missing required evidence"):
        transition(state, "complete")

    # And a human sign-off still clears it as before.
    state = with_evidence(state, EvidenceItem(kind="human", summary="human approved", passed=True))
    assert transition(state, "complete").phase == "closeout"


def test_blocking_verdict_cross_review_does_not_satisfy():
    from pipeline_evidence import valid_cross_review_signoff

    for verdict in ("blocking", "request_changes", "reject"):
        state = _review_state()
        state = with_evidence(state, _approving_cross_review(metadata={"verdict": verdict}))
        assert missing_required_evidence(state) == {"human"}, verdict
        assert valid_cross_review_signoff(state) is False, verdict


def test_cross_review_signoff_resolver_reads_structured_metadata():
    from pipeline_evidence import issue_allows_cross_review_signoff

    # Explicit structured metadata key.
    assert issue_allows_cross_review_signoff({"metadata": {"allow_cross_review_signoff": "true"}}) is True
    assert issue_allows_cross_review_signoff({"metadata": {"allow_cross_review_signoff": True}}) is True
    # Structured ticket-block tag in the description (parse_ticket_block).
    ticket = "prep\n```zoe-ticket\n{\"allow_cross_review_signoff\": true}\n```\ntail"
    assert issue_allows_cross_review_signoff({"description": ticket}) is True
    # Absence resolves to False.
    assert issue_allows_cross_review_signoff({"title": "ordinary task"}) is False
    assert issue_allows_cross_review_signoff(None) is False


def test_cross_review_signoff_resolver_rejects_optout_and_freetext():
    from pipeline_evidence import issue_allows_cross_review_signoff

    # (i) Explicit opt-out must resolve False, not be flipped on by substring matching.
    assert issue_allows_cross_review_signoff({"metadata": {"allow_cross_review_signoff": "false"}}) is False
    assert issue_allows_cross_review_signoff({"metadata": {"allow_cross_review_signoff": False}}) is False
    assert issue_allows_cross_review_signoff({"metadata": {"allow_cross_review_signoff": "no"}}) is False
    # (ii) A ticket that merely MENTIONS the flag name in free text (no structured
    # truthy tag) must NOT enable it — the safety-critical flag is spoof-resistant.
    assert issue_allows_cross_review_signoff(
        {"title": "discuss allow_cross_review_signoff", "description": "should we set cross-review-signoff?"}
    ) is False
    # A structured opt-out tag in the description likewise stays False.
    ticket_off = "```zoe-ticket\n{\"allow_cross_review_signoff\": false}\n```"
    assert issue_allows_cross_review_signoff({"description": ticket_off}) is False


def test_issue_implementer_platform_resolver_reads_structured_metadata():
    from pipeline_evidence import issue_implementer_platform

    # Structured metadata, normalised to platform.
    assert issue_implementer_platform({"metadata": {"implementer_platform": "claude_code"}}) == "anthropic"
    assert issue_implementer_platform({"metadata": {"implementer_platform": "codex"}}) == "openai"
    # Structured ticket-block tag.
    ticket = "prep\n```zoe-ticket\n{\"implementer_platform\": \"codex\"}\n```\ntail"
    assert issue_implementer_platform({"description": ticket}) == "openai"
    # Absence resolves to None (fail-closed anchor).
    assert issue_implementer_platform({"title": "ordinary task"}) is None
    assert issue_implementer_platform(None) is None
