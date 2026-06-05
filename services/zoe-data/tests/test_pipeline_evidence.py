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
    transition,
    with_evidence,
)


def test_evidence_metadata_rejects_secret_fields():
    with pytest.raises(ValueError, match="secret fields"):
        EvidenceItem(kind="tool", summary="used graphify", metadata={"access_token": "abc"})


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
    assert missing_required_evidence(state) == {"tool"}
    with pytest.raises(ValueError, match="missing required evidence"):
        transition(state, "complete")

    state = with_evidence(state, EvidenceItem(kind="tool", summary="graphify query ran", passed=True))
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

    with pytest.raises(ValueError, match="implement is missing required evidence: tool"):
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
