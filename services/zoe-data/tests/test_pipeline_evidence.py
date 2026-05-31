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
        EvidenceItem(kind="tool", summary="used graphify", metadata={"token": "abc"})


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


def test_review_change_request_loops_to_implement():
    state = PipelineState(task_ref="multica:1", phase="review", status="running")
    next_state = transition(state, "request_changes", reason="missing rollback test")

    assert next_state.phase == "implement"
    assert next_state.status == "todo"
    assert next_state.history[-1].reason == "missing rollback test"


def test_start_records_attempts_per_phase():
    state = PipelineState(task_ref="multica:1")

    state = transition(state, "start")
    state = transition(state, "start")

    assert state.status == "running"
    assert state.attempts["implement"] == 2


def test_retro_complete_marks_pipeline_done():
    state = PipelineState(task_ref="multica:1", phase="retro")
    state = with_evidence(state, EvidenceItem(kind="log", summary="retro captured", passed=True))

    done = transition(state, "complete")

    assert done.phase == "retro"
    assert done.status == "done"

