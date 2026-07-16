"""Tests for Kanban handoff → pipeline evidence parsing."""

import pytest
from pipeline_handoff import (
    evidence_from_handoff,
    implementation_required_from_handoff,
    infer_outcome,
)

pytestmark = pytest.mark.ci_safe


def test_evidence_from_implement_handoff_parses_tools_and_tests():
    detail = {
        "latest_summary": "PR_URL=https://github.com/o/r/pull/1\nTOOLS_USED=codebase-memory,opensrc\nTESTS=pytest -q passed",
        "comments": [],
    }
    items = evidence_from_handoff("implement", detail)
    kinds = {item.kind for item in items}
    assert "tool" in kinds
    assert "test" in kinds
    assert "pr" in kinds


def test_implement_evidence_recovers_pr_url_from_prose_without_structured_field():
    # Observed with MiniMax M3 (ZOE-5798): the worker opened a real PR but
    # reported it in prose ("PR opened: <url>") instead of a structured PR_URL=
    # field, so the pr-evidence gate wrongly blocked. The handoff text URL must
    # still produce a 'pr' EvidenceItem.
    detail = {
        "latest_summary": (
            "TOOLS_USED=codebase-memory\nTESTS=pytest -q passed\n"
            "Shipped\n- Branch pushed to origin.\n"
            "- PR opened against main: https://github.com/o/r/pull/514\n"
            "- kanban_complete recorded with PR URL and tests."
        ),
        "comments": [],
    }
    items = evidence_from_handoff("implement", detail)
    pr_items = [i for i in items if i.kind == "pr"]
    assert pr_items, "a PR mentioned only in prose must still yield pr evidence"
    assert pr_items[0].artifact == "https://github.com/o/r/pull/514"


def test_closeout_evidence_recovers_pr_url_from_prose_without_structured_field():
    # The prose-recovery path guards on phase in {implement, verify, closeout}.
    # closeout also runs the inferred_audit computation, so confirm a PR mentioned
    # only in prose still yields a 'pr' EvidenceItem in the closeout phase.
    detail = {
        "latest_summary": (
            "Closeout summary\n- Verified tests pass.\n"
            "- PR merged against main: https://github.com/o/r/pull/520\n"
            "- kanban_complete recorded."
        ),
        "comments": [],
    }
    items = evidence_from_handoff("closeout", detail)
    pr_items = [i for i in items if i.kind == "pr"]
    assert pr_items, "a PR mentioned only in prose must still yield pr evidence at closeout"
    assert pr_items[0].artifact == "https://github.com/o/r/pull/520"


def test_implement_evidence_recovers_live_run_metadata_and_ticket_pr_url():
    detail = {
        "latest_summary": (
            "Fixed timing-attack vulnerability in auth.py token comparison — "
            "replaced vulnerable == with hmac.compare_digest for constant-time security."
        ),
        "task": {
            "body": """Multica issue: ZOE-5354
```zoe-ticket
{"pr_url":"https://github.com/jason-easyazz/zoe-ai-assistant/pull/213"}
```"""
        },
        "runs": [
            {
                "summary": "Fixed timing-attack vulnerability in auth.py token comparison.",
                "metadata": {
                    "changed_files": ["services/zoe-data/auth.py"],
                    "tests_run": 1,
                    "tests_passed": 1,
                },
            }
        ],
    }

    items = evidence_from_handoff("implement", detail, skills=("zoe-engineering",))

    assert any(item.kind == "tool" and item.passed is True for item in items)
    assert any(
        item.kind == "test"
        and item.passed is True
        and item.metadata.get("source") == "kanban_run_metadata"
        for item in items
    )
    assert any(
        item.kind == "pr"
        and item.artifact == "https://github.com/jason-easyazz/zoe-ai-assistant/pull/213"
        for item in items
    )


def test_task_body_does_not_create_tool_evidence_without_logs_or_skills():
    detail = {
        "latest_summary": "Fixed the requested implementation.",
        "task": {
            "body": """Multica issue: ZOE-5354
PR_URL=https://github.com/jason-easyazz/zoe-ai-assistant/pull/213
```zoe-ticket
{"pr_url":"https://github.com/jason-easyazz/zoe-ai-assistant/pull/213"}
```"""
        },
        "runs": [
            {
                "metadata": {
                    "tests_run": 1,
                    "tests_passed": 1,
                },
            }
        ],
    }

    items = evidence_from_handoff("implement", detail, skills=())

    assert not any(item.kind == "tool" for item in items)
    assert any(item.kind == "test" and item.passed is True for item in items)
    assert any(item.kind == "pr" for item in items)


def test_task_body_tests_do_not_override_run_metadata_recovery():
    detail = {
        "latest_summary": "Fixed the requested implementation.",
        "task": {
            "body": """Multica issue: ZOE-5354
TESTS=old ticket template says pending
PR_URL=https://github.com/jason-easyazz/zoe-ai-assistant/pull/213"""
        },
        "runs": [
            {
                "metadata": {
                    "tests_run": 2,
                    "tests_passed": 2,
                },
            }
        ],
    }

    items = evidence_from_handoff("implement", detail, skills=())

    test_item = next(item for item in items if item.kind == "test")
    assert test_item.passed is True
    assert test_item.metadata.get("source") == "kanban_run_metadata"
    assert "tests_run=2" in test_item.summary


def test_run_metadata_test_evidence_requires_all_tests_to_pass():
    detail = {
        "runs": [
            {
                "metadata": {
                    "tests_run": 5,
                    "tests_passed": 3,
                }
            }
        ],
    }

    items = evidence_from_handoff("implement", detail, skills=("zoe-engineering",))
    test_item = next(item for item in items if item.kind == "test")
    assert test_item.passed is False


def test_run_metadata_test_evidence_uses_latest_qualifying_run():
    detail = {
        "runs": [
            {
                "metadata": {
                    "tests_run": 1,
                    "tests_passed": 1,
                }
            },
            {
                "metadata": {
                    "tests_run": 5,
                    "tests_passed": 3,
                }
            },
        ],
    }

    items = evidence_from_handoff("implement", detail, skills=("zoe-engineering",))
    test_item = next(item for item in items if item.kind == "test")
    assert test_item.passed is False
    assert "tests_run=5" in test_item.summary
    assert "tests_passed=3" in test_item.summary


def test_closeout_ticket_pr_url_prevents_inferred_audit_only_evidence():
    detail = {
        "latest_summary": "SUMMARY=audit closeout completed without prose PR field",
        "task": {
            "body": """Multica issue: ZOE-5354
```zoe-ticket
{"pr_url":"https://github.com/jason-easyazz/zoe-ai-assistant/pull/213"}
```"""
        },
    }

    items = evidence_from_handoff("closeout", detail)

    assert any(item.kind == "pr" for item in items)
    assert not any(
        item.kind == "log"
        and item.metadata.get("audit_only") is True
        for item in items
    )


def test_implementation_required_from_structured_scout_handoff():
    detail = {
        "runs": [
            {
                "metadata": {
                    "IMPLEMENTATION_REQUIRED": "false",
                    "SCOUT_SUMMARY": "Acceptance is already satisfied by PR #173.",
                }
            }
        ]
    }

    assert implementation_required_from_handoff(detail) is False


def test_acceptance_status_is_not_a_routing_signal():
    detail = {
        "runs": [{"metadata": {"ACCEPTANCE_STATUS": "met_by_merged_PRs"}}]
    }

    assert implementation_required_from_handoff(detail) is None


def test_acceptance_status_in_free_text_does_not_skip_implementation():
    detail = {"latest_summary": "ACCEPTANCE_STATUS=met_by_merged_PRs"}

    assert implementation_required_from_handoff(detail) is None


def test_structured_implementation_decision_wins_over_text():
    detail = {
        "latest_summary": "IMPLEMENTATION_REQUIRED=true",
        "runs": [{"metadata": {"IMPLEMENTATION_REQUIRED": "false"}}],
    }

    assert implementation_required_from_handoff(detail) is False


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


def test_verify_evidence_reads_structured_run_metadata():
    detail = {
        "runs": [
            {
                "metadata": {
                    "TESTS": "pytest -q passed",
                    "VALIDATORS": "validate_structure.py passed",
                }
            }
        ]
    }

    items = evidence_from_handoff("verify", detail)

    assert {item.kind for item in items if item.passed is True} >= {"test", "validator"}


def test_generic_not_applicable_tests_do_not_count_as_passed():
    detail = {
        "runs": [
            {
                "metadata": {
                    "TESTS": "not applicable",
                    "VALIDATORS": "validate_structure.py passed",
                }
            }
        ]
    }

    items = evidence_from_handoff("verify", detail)
    test_item = next(item for item in items if item.kind == "test")

    assert test_item.passed is False


def test_audit_no_code_verify_tests_count_as_passed():
    detail = {
        "runs": [
            {
                "metadata": {
                    "TESTS": "not applicable — audit-only no-code verification",
                    "VALIDATORS": "validate_structure.py passed",
                }
            }
        ]
    }

    items = evidence_from_handoff("verify", detail)
    test_item = next(item for item in items if item.kind == "test")

    assert test_item.passed is True


def test_unhyphenated_no_code_verify_tests_do_not_count_as_passed():
    detail = {
        "runs": [
            {
                "metadata": {
                    "TESTS": "not applicable — no code changes were made",
                }
            }
        ]
    }

    items = evidence_from_handoff("verify", detail)
    test_item = next(item for item in items if item.kind == "test")

    assert test_item.passed is False


def test_audit_no_code_test_exemption_is_verify_only():
    detail = {
        "runs": [
            {
                "metadata": {
                    "TESTS": "not applicable — audit-only no-code verification",
                }
            }
        ]
    }

    items = evidence_from_handoff("implement", detail)
    test_item = next(item for item in items if item.kind == "test")

    assert test_item.passed is False


def test_not_applicable_validators_do_not_count_as_passed():
    detail = {
        "runs": [
            {
                "metadata": {
                    "VALIDATORS": "not applicable — validators were not run",
                }
            }
        ]
    }

    items = evidence_from_handoff("verify", detail)
    validator = next(item for item in items if item.kind == "validator")

    assert validator.passed is False


def test_blocked_validator_does_not_count_as_passed():
    detail = {"latest_summary": "VALIDATORS=gate blocked pending repository access"}

    items = evidence_from_handoff("verify", detail)
    validator = next(item for item in items if item.kind == "validator")

    assert validator.passed is False


def test_block_substrings_do_not_reject_successful_test_evidence():
    detail = {
        "latest_summary": "TESTS=unblocked blockchain tests; pytest passed",
    }

    items = evidence_from_handoff("verify", detail)
    test_item = next(item for item in items if item.kind == "test")

    assert test_item.passed is True


def test_evidence_from_review_metadata_records_human_approval():
    detail = {
        "latest_summary": "APPROVE ZOE-5401. PR is merge-ready.",
        "comments": [],
        "metadata": {
            "merge_readiness": "merge_ready",
            "approver": "zoe-reviewer",
        },
    }

    items = evidence_from_handoff("review", detail)

    human = next(item for item in items if item.kind == "human")
    assert human.passed is True
    assert human.metadata["source"] == "kanban_metadata"
    assert human.metadata["approver"] == "zoe-reviewer"


def test_evidence_from_review_run_metadata_records_human_approval():
    detail = {
        "latest_summary": "APPROVE ZOE-5401. PR is merge-ready.",
        "comments": [],
        "runs": [
            {
                "metadata": {
                    "merge_readiness": "merge_ready",
                    "approver": "zoe-reviewer",
                }
            }
        ],
    }

    items = evidence_from_handoff("review", detail)

    human = next(item for item in items if item.kind == "human")
    assert human.passed is True
    assert human.metadata["source"] == "kanban_run_metadata"
    assert human.metadata["approver"] == "zoe-reviewer"


def test_evidence_from_review_prefers_top_level_metadata_over_run_metadata():
    detail = {
        "latest_summary": "APPROVE ZOE-5401. PR is merge-ready.",
        "comments": [],
        "metadata": {
            "merge_readiness": "merge_ready",
            "approver": "top-level-reviewer",
        },
        "runs": [
            {
                "metadata": {
                    "merge_readiness": "merge_ready",
                    "approver": "run-reviewer",
                }
            }
        ],
    }

    items = evidence_from_handoff("review", detail)

    human = next(item for item in items if item.kind == "human")
    assert human.metadata["source"] == "kanban_metadata"
    assert human.metadata["approver"] == "top-level-reviewer"


def test_evidence_from_live_review_run_metadata_accepts_merge_ready_verdict():
    detail = {
        "latest_summary": "APPROVE ZOE-5408 audit-only E2E.",
        "comments": [],
        "task": {"assignee": "zoe-reviewer"},
        "runs": [
            {
                "metadata": {
                    "verdict": "approve",
                    "merge_ready": True,
                    "audit_only": True,
                }
            }
        ],
    }

    items = evidence_from_handoff("review", detail)

    human = next(item for item in items if item.kind == "human")
    assert human.passed is True
    assert human.metadata["source"] == "kanban_run_metadata"
    assert human.metadata["approver"] == "zoe-reviewer"
    assert human.metadata["merge_readiness"] == "merge_ready"
    assert human.metadata["verdict"] == "approve"


def test_evidence_from_live_review_run_metadata_accepts_explicit_approved_ready():
    detail = {
        "latest_summary": "APPROVE ZOE-5347. Merge-ready.",
        "comments": [],
        "task": {"assignee": "zoe-reviewer"},
        "runs": [
            {
                "metadata": {
                    "approved": True,
                    "merge_readiness": "ready",
                }
            }
        ],
    }

    items = evidence_from_handoff("review", detail)

    human = next(item for item in items if item.kind == "human")
    assert human.passed is True
    assert human.metadata["source"] == "kanban_run_metadata"
    assert human.metadata["approver"] == "zoe-reviewer"
    assert human.metadata["merge_readiness"] == "merge_ready"


def test_evidence_from_review_metadata_accepts_approved_readiness():
    detail = {
        "latest_summary": "Approved after review.",
        "comments": [],
        "metadata": {
            "merge_readiness": "approved",
            "approved_by": "zoe-reviewer",
        },
    }

    items = evidence_from_handoff("review", detail)

    human = next(item for item in items if item.kind == "human")
    assert human.passed is True
    assert human.metadata["approver"] == "zoe-reviewer"
    assert human.metadata["merge_readiness"] == "approved"


def test_evidence_from_review_ignores_summary_without_approver():
    detail = {
        "latest_summary": "APPROVE ZOE-5401. PR is merge-ready.",
        "comments": [],
        "metadata": {},
    }

    items = evidence_from_handoff("review", detail)

    assert not any(item.kind == "human" for item in items)


def test_evidence_from_review_ignores_legacy_readiness_with_only_task_assignee():
    detail = {
        "latest_summary": "APPROVE ZOE-5401. PR is merge-ready.",
        "comments": [],
        "task": {"assignee": "zoe-reviewer"},
        "metadata": {
            "merge_readiness": "merge_ready",
        },
    }

    items = evidence_from_handoff("review", detail)

    assert not any(item.kind == "human" for item in items)


def test_evidence_from_review_ignores_ready_without_explicit_approval():
    detail = {
        "latest_summary": "Reviewer says this looks ready.",
        "comments": [],
        "task": {"assignee": "zoe-reviewer"},
        "metadata": {
            "merge_readiness": "ready",
        },
    }

    items = evidence_from_handoff("review", detail)

    assert not any(item.kind == "human" for item in items)


def test_evidence_from_review_run_ignores_ready_without_explicit_approval():
    detail = {
        "latest_summary": "Reviewer says this looks ready.",
        "comments": [],
        "task": {"assignee": "zoe-reviewer"},
        "runs": [
            {
                "metadata": {
                    "merge_readiness": "ready",
                }
            }
        ],
    }

    items = evidence_from_handoff("review", detail)

    assert not any(item.kind == "human" for item in items)


def test_evidence_from_review_ignores_generic_pass_readiness():
    detail = {
        "latest_summary": "Reviewer pass note",
        "comments": [],
        "metadata": {
            "merge_readiness": "pass",
            "approver": "zoe-reviewer",
        },
    }

    items = evidence_from_handoff("review", detail)

    assert not any(item.kind == "human" for item in items)


def test_evidence_from_review_ignores_reviewer_assignment():
    detail = {
        "latest_summary": "Review assigned and PR looks ready.",
        "comments": [],
        "metadata": {
            "merge_readiness": "merge_ready",
            "reviewer": "zoe-reviewer",
        },
    }

    items = evidence_from_handoff("review", detail)

    assert not any(item.kind == "human" for item in items)


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


def test_infer_outcome_verify_budget_surfaces_block():
    assert (
        infer_outcome(
            "verify",
            "blocked",
            {"latest_summary": "BLOCKER=VERIFY_BUDGET: code-enforced tool budget exceeded", "comments": []},
        )
        == "block"
    )


def test_infer_outcome_freeform_verify_budget_surfaces_block():
    assert (
        infer_outcome(
            "verify",
            "blocked",
            {"latest_summary": "VERIFY_BUDGET: code-enforced tool budget exceeded", "comments": []},
        )
        == "block"
    )


def test_infer_outcome_pr_review_required_surfaces_block():
    assert (
        infer_outcome(
            "verify",
            "blocked",
            {
                "latest_summary": (
                    "BLOCKER=PR_REVIEW_REQUIRED: Greptile has unresolved comments\n"
                    "PR_URL=https://github.com/o/r/pull/213"
                ),
                "comments": [],
            },
        )
        == "block"
    )


def test_infer_outcome_review_budget_surfaces_block():
    assert (
        infer_outcome(
            "review",
            "blocked",
            {"latest_summary": "BLOCKER=REVIEW_BUDGET: reviewer exceeded budget", "comments": []},
        )
        == "block"
    )


def test_infer_outcome_closeout_budget_surfaces_block():
    assert (
        infer_outcome(
            "closeout",
            "blocked",
            {"latest_summary": "BLOCKER=CLOSEOUT_BUDGET: closeout exceeded budget", "comments": []},
        )
        == "block"
    )


def test_infer_outcome_done_with_verify_budget_surfaces_block():
    assert (
        infer_outcome(
            "verify",
            "done",
            {"latest_summary": "BLOCKER=VERIFY_BUDGET: exceeded\nTESTS=not completed", "comments": []},
        )
        == "block"
    )


def test_infer_outcome_done_ignores_freeform_stable_blocker_text():
    assert (
        infer_outcome(
            "verify",
            "done",
            {
                "latest_summary": (
                    "SUMMARY=verification completed\n"
                    "Log excerpt: GATE_BLOCKED: old text from a previous attempt"
                ),
                "comments": [],
            },
        )
        == "complete"
    )


def test_later_empty_blocker_clears_older_blocked_run():
    detail = {
        "latest_summary": "PR_URL=https://github.com/o/r/pull/213\nBLOCKER=\nTESTS=validate_structure.py passed",
        "runs": [
            {
                "status": "blocked",
                "summary": "BLOCKER=review-required: PR opened but worker blocked",
            },
            {
                "status": "completed",
                "summary": "PR_URL=https://github.com/o/r/pull/213\nBLOCKER=\nTESTS=validate_structure.py passed",
            },
        ],
    }

    assert infer_outcome("implement", "done", detail) == "complete"


def test_infer_outcome_done_without_blocker_completes():
    assert infer_outcome("review", "done", {"latest_summary": "SUMMARY=approved", "comments": []}) == "complete"


def test_evidence_from_skills_when_handoff_omits_tools():
    detail = {"latest_summary": "SUMMARY=scout complete", "comments": []}
    items = evidence_from_handoff("scout", detail, skills=("codebase-memory", "zoe-engineering"))
    assert any(item.kind == "tool" and item.metadata.get("source") == "skills" for item in items)


def test_evidence_from_implement_skills_codebase_memory():
    detail = {"latest_summary": "SUMMARY=done", "comments": []}
    items = evidence_from_handoff(
        "implement",
        detail,
        skills=("zoe-engineering", "codebase-memory", "source-code-context"),
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


def test_block_reason_from_handoff_extracts_iteration_budget_from_run_error():
    from pipeline_handoff import block_reason_from_handoff

    detail = {
        "latest_summary": "",
        "comments": [],
        "runs": [
            {
                "outcome": "gave_up",
                "error": "Iteration budget exhausted (22/22) — task could not complete within the allowed iterations",
                "metadata": {"trigger_outcome": "timed_out"},
            }
        ],
    }

    assert block_reason_from_handoff(detail) == "ITERATION_BUDGET"


def test_block_reason_ignores_dynamic_log_tail_without_stable_token():
    from pipeline_handoff import block_reason_from_handoff

    detail = {
        "latest_summary": "",
        "comments": [{"body": "retry 3 at 2026-06-01T12:00:00Z\nstill waiting..."}],
    }
    assert block_reason_from_handoff(detail) == ""


def test_audit_only_from_handoff_detects_kv_field():
    from pipeline_handoff import audit_only_from_handoff

    detail = {"latest_summary": "AUDIT_ONLY=1\nSUMMARY=findings only", "comments": []}
    assert audit_only_from_handoff(detail) is True
    assert audit_only_from_handoff({"latest_summary": "SUMMARY=code change", "comments": []}) is False


def test_split_request_from_handoff_parses_packet():
    from pipeline_handoff import split_request_from_handoff

    detail = {
        "latest_summary": (
            'NEEDS_SPLIT=1\nSPLIT_PACKET={"child_issue_template":{"title":"ZOE-1: narrow child"}}'
        ),
        "comments": [],
    }
    requested, packet = split_request_from_handoff(detail)
    assert requested is True
    assert packet["child_issue_template"]["title"] == "ZOE-1: narrow child"


def test_split_request_from_handoff_parses_multiline_packet():
    from pipeline_handoff import split_request_from_handoff

    detail = {
        "latest_summary": (
            "NEEDS_SPLIT=1\n"
            "SPLIT_PACKET={\n"
            '  "child_issue_template": {\n'
            '    "title": "ZOE-1: multiline child"\n'
            "  },\n"
            '  "reason": "too broad"\n'
            "}\n"
            "SUMMARY=blocked cleanly"
        ),
        "comments": [],
    }
    requested, packet = split_request_from_handoff(detail)
    assert requested is True
    assert packet["child_issue_template"]["title"] == "ZOE-1: multiline child"
    assert packet["reason"] == "too broad"


def test_retro_handoff_can_request_followup_ticket():
    detail = {
        "latest_summary": (
            "RETRO=Worktree guard prevented drift\n"
            "FOLLOW_UP_TITLE=Add regression for retro follow-up tickets\n"
            "FOLLOW_UP_DESCRIPTION=Create a focused test so retro follow-up metadata keeps creating backlog tickets."
        ),
        "comments": [],
    }

    items = evidence_from_handoff("retro", detail)

    log = next(item for item in items if item.kind == "log")
    assert log.metadata["follow_up"]["title"] == "Add regression for retro follow-up tickets"
    assert log.metadata["follow_up"]["source"] == "retro"


def test_retro_run_metadata_records_log_and_followup_evidence():
    detail = {
        "latest_summary": "Retro complete for ZOE-5347.",
        "comments": [],
        "runs": [
            {
                "metadata": {
                    "RETRO": "Closeout reached the merge guard too late.",
                    "LEARNINGS": "Front-load deterministic guard commands.",
                    "TOOLS_USED": "kanban_show,terminal",
                    "FOLLOW_UP_TITLE": "Reduce closeout orchestration overhead",
                    "FOLLOW_UP_DESCRIPTION": "Run the merge guard before broad repository inspection.",
                    "changed_files": ["ignored.py"],
                }
            }
        ],
    }

    items = evidence_from_handoff("retro", detail)

    log = next(item for item in items if item.kind == "log")
    tool = next(item for item in items if item.kind == "tool")
    assert log.summary == "Closeout reached the merge guard too late."
    assert log.metadata["follow_up"]["title"] == "Reduce closeout orchestration overhead"
    assert tool.summary == "kanban_show,terminal"


def test_retro_top_level_metadata_records_log_evidence():
    detail = {
        "latest_summary": "Retro complete.",
        "comments": [],
        "metadata": {
            "RETRO": "Top-level structured retro evidence.",
            "TOOLS_USED": "kanban_show",
        },
    }

    items = evidence_from_handoff("retro", detail)

    log = next(item for item in items if item.kind == "log")
    tool = next(item for item in items if item.kind == "tool")
    assert log.summary == "Top-level structured retro evidence."
    assert tool.summary == "kanban_show"


def test_retro_generic_run_metadata_does_not_create_log_evidence():
    detail = {
        "latest_summary": "Retro complete.",
        "comments": [],
        "runs": [{"metadata": {"changed_files": ["ignored.py"], "tests_run": "2"}}],
    }

    items = evidence_from_handoff("retro", detail)

    assert not any(item.kind == "log" for item in items)


def test_review_structured_summary_does_not_bypass_approver_gate():
    detail = {
        "latest_summary": "Review task completed.",
        "comments": [],
        "task": {"assignee": "zoe-reviewer"},
        "metadata": {"SUMMARY": "approved"},
        "runs": [{"metadata": {"REVIEW": "approved"}}],
    }

    items = evidence_from_handoff("review", detail)

    assert not any(item.kind == "human" for item in items)


def test_retro_handoff_without_followup_title_only_logs():
    detail = {"latest_summary": "RETRO=No harness change needed", "comments": []}

    items = evidence_from_handoff("retro", detail)

    log = next(item for item in items if item.kind == "log")
    assert "follow_up" not in log.metadata


def test_retro_handoff_ignores_undocumented_followup_description_aliases():
    detail = {
        "latest_summary": (
            "RETRO=Captured learnings\n"
            "FOLLOW_UP_TITLE=Document prompt contract\n"
            "FOLLOW_UP=This should not become the ticket description"
        ),
        "comments": [],
    }

    items = evidence_from_handoff("retro", detail)

    log = next(item for item in items if item.kind == "log")
    assert log.metadata["follow_up"]["description"] == "Document prompt contract"


def test_closeout_audit_only_handoff_records_log_evidence():
    detail = {"latest_summary": """AUDIT_ONLY=1
SUMMARY=audit-only closeout completed
PR_URL=""", "comments": []}

    items = evidence_from_handoff("closeout", detail, skills=())

    log = next(item for item in items if item.kind == "log")
    assert log.passed is True
    assert log.summary == "audit-only closeout completed"
    assert log.metadata["phase"] == "closeout"
    assert log.metadata["audit_only"] is True
    assert not any(item.kind == "greptile" for item in items)


def test_closeout_audit_only_handoff_accepts_indented_log_lines():
    detail = {"log_tail": """
     PR_URL=
     MERGE_SHA=
     GREPTILE=n/a (no-code plan)
     AUDIT_ONLY=1
     SUMMARY=4-phase card producer adoption plan approved; no code changes
""", "comments": []}

    items = evidence_from_handoff("closeout", detail, skills=("github-greptile-loop",))

    log = next(item for item in items if item.kind == "log")
    assert log.passed is True
    assert log.summary == "4-phase card producer adoption plan approved; no code changes"
    assert log.metadata["audit_only"] is True

    greptile_items = [item for item in items if item.kind == "greptile"]
    assert not any(item.passed is True for item in greptile_items), (
        "GREPTILE=n/a should not produce a passed=True greptile evidence item"
    )


def test_closeout_infers_audit_log_only_from_audit_summary_without_pr():
    detail = {"latest_summary": """SUMMARY=audit-only closeout completed
PR_URL=""", "comments": []}

    items = evidence_from_handoff("closeout", detail, skills=())

    log = next(item for item in items if item.kind == "log")
    assert log.metadata["audit_only"] is True


def test_closeout_does_not_infer_audit_log_from_generic_summary_without_pr():
    detail = {"latest_summary": """SUMMARY=closeout finished
PR_URL=""", "comments": []}

    items = evidence_from_handoff("closeout", detail, skills=())

    assert not any(item.kind == "log" for item in items)



def test_closeout_placeholder_pr_url_does_not_shadow_ticket_pr_url():
    detail = {
        "latest_summary": "PR_URL=<url>\nSUMMARY=closeout template text",
        "comments": [],
        "task": {
            "body": "```zoe-ticket\n{\"pr_url\": \"https://github.com/o/r/pull/99\"}\n```"
        },
    }

    items = evidence_from_handoff("closeout", detail, skills=())

    pr = next(item for item in items if item.kind == "pr")
    assert pr.artifact == "https://github.com/o/r/pull/99"


def test_pr_url_from_ticket_block_treats_malformed_metadata_as_absent(monkeypatch):
    import sys
    import types
    from pipeline_handoff import _pr_url_from_ticket_block

    def _raise_key_error(_body):
        raise KeyError("bad ticket block")

    monkeypatch.setitem(
        sys.modules,
        "multica_ticket_contract",
        types.SimpleNamespace(parse_ticket_block=_raise_key_error),
    )

    assert _pr_url_from_ticket_block({"task": {"body": "```zoe-ticket\n{}\n```"}}) == ""
