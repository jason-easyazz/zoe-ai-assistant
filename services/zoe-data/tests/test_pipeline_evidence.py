import pytest

from pipeline_evidence import (
    EvidenceItem,
    PipelineState,
    can_complete_phase,
    missing_required_evidence,
    transition,
    with_evidence,
)


def test_evidence_metadata_rejects_secret_fields():
    with pytest.raises(ValueError, match="secret fields"):
        EvidenceItem(kind="tool", summary="used graphify", metadata={"access_token": "abc"})


def test_implement_requires_tool_evidence_before_complete():
    state = PipelineState(task_ref="multica:1")

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


def test_start_records_attempts_per_phase():
    state = PipelineState(task_ref="multica:1")

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


def test_retro_complete_marks_pipeline_done():
    state = PipelineState(task_ref="multica:1", phase="retro")
    state = with_evidence(state, EvidenceItem(kind="log", summary="retro captured", passed=True))

    done = transition(state, "complete")

    assert done.phase == "retro"
    assert done.status == "done"

