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


def test_evidence_from_skills_when_handoff_omits_tools():
    detail = {"latest_summary": "SUMMARY=scout complete", "comments": []}
    items = evidence_from_handoff("scout", detail, skills=("zoe-graphify", "zoe-engineering"))
    assert any(item.kind == "tool" and item.metadata.get("source") == "skills" for item in items)


def test_evidence_from_implement_skills_graphify():
    detail = {"latest_summary": "SUMMARY=done", "comments": []}
    items = evidence_from_handoff(
        "implement",
        detail,
        skills=("zoe-engineering", "zoe-graphify", "source-code-context"),
    )
    assert any(item.kind == "tool" for item in items)


def test_closeout_greptile_from_pinned_skill():
    detail = {"latest_summary": "PR_URL=https://github.com/o/r/pull/2\nSUMMARY=merged", "comments": []}
    items = evidence_from_handoff("closeout", detail, skills=("github-greptile-loop",))
    greptile = [item for item in items if item.kind == "greptile"]
    assert greptile
    assert greptile[0].passed is None


def test_block_reason_from_handoff_ignores_dynamic_log_tail():
    from pipeline_handoff import block_reason_from_handoff

    detail = {
        "latest_summary": "",
        "comments": [{"body": "Error: WORKTREE_NOT_READY\nbranch has no upstream"}],
    }
    reason = block_reason_from_handoff(detail)
    assert reason == "WORKTREE_NOT_READY"


def test_block_reason_ignores_dynamic_log_tail_without_stable_token():
    from pipeline_handoff import block_reason_from_handoff

    detail = {
        "latest_summary": "",
        "comments": [{"body": "retry 3 at 2026-06-01T12:00:00Z\nstill waiting..."}],
    }
    assert block_reason_from_handoff(detail) == ""
