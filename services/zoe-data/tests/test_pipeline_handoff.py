"""Tests for Kanban handoff → pipeline evidence parsing."""

from pipeline_handoff import evidence_from_handoff, infer_outcome


def test_evidence_from_implement_handoff_parses_tools_and_tests():
    detail = {
        "latest_summary": "PR_URL=https://github.com/o/r/pull/1\nTOOLS_USED=graphify,opensrc\nTESTS=pytest -q passed",
        "comments": [],
    }
    items = evidence_from_handoff("implement", detail)
    kinds = {item.kind for item in items}
    assert "tool" in kinds
    assert "test" in kinds
    assert "pr" in kinds


def test_evidence_from_verify_handoff_parses_validators():
    detail = {
        "latest_summary": "VALIDATORS=validate_structure.py pass\nTESTS=pytest services/zoe-data/tests -q pass",
        "comments": [],
    }
    items = evidence_from_handoff("verify", detail)
    assert {item.kind for item in items} >= {"validator", "test"}
    validator = next(item for item in items if item.kind == "validator")
    assert validator.content_hash
    assert len(validator.content_hash) == 64


def test_block_reason_from_handoff_prefers_blocker_field():
    from pipeline_handoff import block_reason_from_handoff

    detail = {"latest_summary": "BLOCKER=WORKTREE_NOT_READY", "comments": []}
    assert block_reason_from_handoff(detail) == "WORKTREE_NOT_READY"


def test_infer_outcome_blocked_verify_loops():
    assert (
        infer_outcome(
            "verify",
            "blocked",
            {"latest_summary": "BLOCKER=pytest failed", "comments": []},
        )
        == "verification_failed"
    )


def test_infer_outcome_done_without_blocker_completes():
    assert infer_outcome("review", "done", {"latest_summary": "SUMMARY=approved", "comments": []}) == "complete"
