"""Tests for the Hermes Kanban executor adapter (CLI mocked)."""
import json
from pathlib import Path

import pytest

import executors.kanban_adapter as ka
import kanban_phase_budget as kb


@pytest.fixture(autouse=True)
def _mock_ensure_worktree(monkeypatch):
    """Dispatch tests must not run real git worktree subprocesses."""
    monkeypatch.setattr(
        "worktree_bootstrap.prepare_kanban_worktree",
        lambda task_id, **kwargs: Path(f"/tmp/worktrees/{task_id}"),
    )


@pytest.fixture(autouse=True)
def _isolated_pipeline_store(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(tmp_path / "pipeline_runs.jsonl"))


@pytest.fixture(autouse=True)
def _mock_repo_validators(monkeypatch):
    from pipeline_validators import ValidatorRunResult

    monkeypatch.setattr(
        "pipeline_validators.run_repo_validators",
        lambda **kwargs: ValidatorRunResult(
            exit_code=0,
            summary="validate_structure: exit 0",
            content_hash="deadbeef",
            passed=True,
        ),
    )


class _FakeAdapter(ka.KanbanAdapter):
    """KanbanAdapter with the CLI replaced by a scripted recorder."""

    def __init__(self, list_rows=None, show_map=None):
        self.calls = []
        self._list_rows = list_rows or []
        self._show_map = show_map or {}
        self._create_seq = 0

    async def _run(self, args, *, expect_json=False):
        self.calls.append(args)
        verb = args[0]
        if verb == "create":
            self._create_seq += 1
            # idempotency-key arg lets us derive a stable id per phase
            key = args[args.index("--idempotency-key") + 1]
            return {"id": f"t_{key.split(':')[-1]}", "deduplicated": False}
        if verb == "list":
            return self._list_rows
        if verb == "show":
            return self._show_map.get(args[1], {"comments": [], "latest_summary": ""})
        if verb == "block":
            return ""
        return ""


@pytest.mark.asyncio
async def test_dispatch_creates_one_ready_phase():
    a = _FakeAdapter()
    issue = {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": "do it"}
    result = await a.dispatch(issue)
    assert result["ok"] is True
    assert result["external_ref"] == "multica:uuid-1"
    assert result["phase"] == "scout"
    assert result["ready_phase_only"] is True
    assert set(result["chain"]) == {"scout"}
    creates = [c for c in a.calls if c[0] == "create"]
    assert len(creates) == 1
    assert "--parent" not in creates[0]
    keys = [c[c.index("--idempotency-key") + 1] for c in creates]
    assert keys == ["multica:uuid-1:scout"]
    assert result["mode"] == "interactive"


@pytest.mark.asyncio
async def test_dispatch_refuses_to_create_without_pipeline_journal(monkeypatch):
    async def fail_bootstrap(*args, **kwargs):
        raise RuntimeError("store offline")

    monkeypatch.setattr("pipeline_store.bootstrap_state", fail_bootstrap)
    a = _FakeAdapter()
    result = await a.dispatch({"id": "uuid-no-journal", "identifier": "ZOE-NJ", "title": "No journal"})

    assert result["ok"] is False
    assert result["reason"] == "pipeline bootstrap failed"
    assert [c for c in a.calls if c[0] == "create"] == []




@pytest.mark.asyncio
async def test_dispatch_does_not_parent_recovered_phase_to_blocked_prior_row():
    from pipeline_evidence import EvidenceItem, PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-recovered-parent",
        phase="verify",
        status="todo",
        evidence_profile="audit",
        attempts={"implement": 1},
        evidence=[
            EvidenceItem(
                kind="tool",
                summary="audit/no-PR implement auto-recovered",
                passed=True,
                metadata={"source": "audit_protocol_recovery", "phase": "implement"},
            )
        ],
    )
    save_state(state, event="transition")
    rows = [_row("implement", "blocked", chain_version="v4", issue_id="uuid-recovered-parent")]
    a = _FakeAdapter(list_rows=rows)
    result = await a.dispatch(
        {
            "id": "uuid-recovered-parent",
            "identifier": "ZOE-REC",
            "title": "audit-only recovered parent",
            "description": "evidence_profile: audit",
        }
    )

    creates = [c for c in a.calls if c[0] == "create"]
    assert result["ok"] is True
    assert result["phase"] == "verify"
    assert len(creates) == 1
    assert "--parent" not in creates[0]




@pytest.mark.asyncio
async def test_dispatch_does_not_parent_recovered_phase_to_terminal_like_prior_row():
    from pipeline_evidence import EvidenceItem, PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-recovered-error-parent",
        phase="verify",
        status="todo",
        evidence_profile="audit",
        attempts={"implement": 1},
        evidence=[
            EvidenceItem(
                kind="tool",
                summary="audit/no-PR implement auto-recovered",
                passed=True,
                metadata={"source": "audit_protocol_recovery", "phase": "implement"},
            )
        ],
    )
    save_state(state, event="transition")
    rows = [_row("implement", "error", chain_version="v4", issue_id="uuid-recovered-error-parent")]
    a = _FakeAdapter(list_rows=rows)
    result = await a.dispatch(
        {
            "id": "uuid-recovered-error-parent",
            "identifier": "ZOE-REC",
            "title": "audit-only recovered parent",
            "description": "evidence_profile: audit",
        }
    )

    creates = [c for c in a.calls if c[0] == "create"]
    assert result["ok"] is True
    assert result["phase"] == "verify"
    assert len(creates) == 1
    assert "--parent" not in creates[0]


@pytest.mark.asyncio
async def test_dispatch_after_scout_evidence_creates_next_phase():
    from pipeline_store import bootstrap_state, save_state
    from pipeline_evidence import EvidenceItem, transition, with_evidence

    state = await bootstrap_state("multica:uuid-next", start_phase="scout")
    state = with_evidence(state, EvidenceItem(kind="tool", summary="graphify map", passed=True))
    state = transition(state, "complete")
    save_state(state, event="transition", extra={"row_phase": "scout"})

    rows = [_row("scout", "done", chain_version="v4", issue_id="uuid-next")]
    a = _FakeAdapter(list_rows=rows)
    result = await a.dispatch({"id": "uuid-next", "identifier": "ZOE-NEXT", "title": "Fix thing"})

    creates = [c for c in a.calls if c[0] == "create"]
    assert result["phase"] == "implement"
    assert set(result["chain"]) == {"implement"}
    assert len(creates) == 1
    assert creates[0][creates[0].index("--idempotency-key") + 1] == "multica:uuid-next:implement"
    assert "--parent" in creates[0]
    assert creates[0][creates[0].index("--parent") + 1] == "t_scout"


@pytest.mark.asyncio
async def test_dispatch_overnight_mode_extends_runtime(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_OVERNIGHT_MAX_RUNTIME", "8h")
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-night",
            "identifier": "ZOE-NIGHT",
            "title": "Slow cheap work",
            "engineering_mode": "overnight",
        }
    )

    creates = [c for c in a.calls if c[0] == "create"]
    runtimes = [c[c.index("--max-runtime") + 1] for c in creates]
    body = creates[0][creates[0].index("--body") + 1]
    assert result["mode"] == "overnight"
    assert runtimes == ["8h"]
    assert "latency is secondary" in body


@pytest.mark.asyncio
async def test_dispatch_interactive_mode_uses_default_runtime(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_MAX_RUNTIME", "30m")
    monkeypatch.delenv("ZOE_ENGINEERING_MODE", raising=False)
    a = _FakeAdapter()
    await a.dispatch({"id": "uuid-fast", "identifier": "ZOE-FAST", "title": "Immediate work"})

    creates = [c for c in a.calls if c[0] == "create"]
    assert creates[0][creates[0].index("--max-runtime") + 1] == "30m"
    body = creates[0][creates[0].index("--body") + 1]
    assert "Engineering mode: interactive" in body


@pytest.mark.asyncio
async def test_dispatch_blocks_on_first_worker_failure():
    a = _FakeAdapter()
    await a.dispatch({"id": "uuid-cost", "identifier": "ZOE-COST", "title": "Bound paid retries"})

    creates = [c for c in a.calls if c[0] == "create"]
    assert creates[0][creates[0].index("--max-retries") + 1] == "1"


@pytest.mark.asyncio
async def test_dispatch_overnight_mode_from_metadata(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_OVERNIGHT_MAX_RUNTIME", "7h")
    a = _FakeAdapter()
    result = await a.dispatch(
        {"id": "uuid-meta", "identifier": "ZOE-META", "title": "Background work", "metadata": {"engineering_mode": "overnight"}}
    )
    creates = [c for c in a.calls if c[0] == "create"]
    assert result["mode"] == "overnight"
    assert creates[0][creates[0].index("--max-runtime") + 1] == "7h"


@pytest.mark.asyncio
async def test_dispatch_overnight_mode_from_env(monkeypatch):
    monkeypatch.setenv("ZOE_ENGINEERING_MODE", "self_evolution")
    monkeypatch.setenv("ZOE_KANBAN_OVERNIGHT_MAX_RUNTIME", "9h")
    a = _FakeAdapter()
    result = await a.dispatch({"id": "uuid-env", "identifier": "ZOE-ENV", "title": "Env work"})
    creates = [c for c in a.calls if c[0] == "create"]
    assert result["mode"] == "overnight"
    assert creates[0][creates[0].index("--max-runtime") + 1] == "9h"


@pytest.mark.asyncio
async def test_dispatch_skip_scout_when_env_set(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_SKIP_SCOUT", "1")
    a = _FakeAdapter()
    result = await a.dispatch({"id": "uuid-skip", "identifier": "ZOE-SKIP", "title": "t"})
    assert "scout" not in result["chain"]
    assert set(result["chain"]) == {"implement"}


@pytest.mark.asyncio
async def test_dispatch_skips_scout_for_scope_split_child():
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-child",
            "identifier": "ZOE-5438",
            "title": "card_service foundation",
            "metadata": {
                "zoe_kind": "child",
                "source": "scope_split",
                "acceptance_criteria": ["card_service.py foundation"],
            },
        }
    )
    assert "scout" not in result["chain"]
    assert set(result["chain"]) == {"implement"}


@pytest.mark.asyncio
async def test_dispatch_skips_scout_for_scope_split_child_ticket_block():
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-child-block",
            "identifier": "ZOE-5439",
            "title": "calendar child",
            "description": '```zoe-ticket\n{"zoe_kind":"child","source":"scope_split","acceptance_criteria":["calendar builder"]}\n```',
        }
    )
    assert "scout" not in result["chain"]
    assert set(result["chain"]) == {"implement"}


@pytest.mark.asyncio
async def test_dispatch_keeps_scout_for_under_specified_scope_split_child():
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-child-empty",
            "identifier": "ZOE-EMPTY",
            "title": "under-specified child",
            "metadata": {
                "zoe_kind": "child",
                "source": "scope_split",
                "acceptance_criteria": [],
            },
        }
    )
    assert set(result["chain"]) == {"scout"}


@pytest.mark.asyncio
async def test_dispatch_quality_escalation_mode(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_ESCALATION_MAX_RUNTIME", "75m")
    a = _FakeAdapter()
    result = await a.dispatch(
        {"id": "uuid-q", "identifier": "ZOE-Q", "title": "Hard task", "engineering_mode": "quality-escalation"}
    )
    creates = [c for c in a.calls if c[0] == "create"]
    assert result["mode"] == "quality-escalation"
    assert creates[0][creates[0].index("--max-runtime") + 1] == "75m"
    body = creates[0][creates[0].index("--body") + 1]
    assert "quality-escalation" in body
    verify_body = ka.KanbanAdapter()._build_body(
        "verify",
        {
            "id": "uuid-q",
            "identifier": "ZOE-Q",
            "title": "Hard task",
            "engineering_mode": "quality-escalation",
        },
        "ZOE-Q",
    )
    assert "zoe-model-escalation: true" in verify_body
    assert "anthropic/claude-sonnet-4.6" in verify_body
    assert "Do NOT use openrouter/auto" in verify_body


@pytest.mark.asyncio
async def test_dispatch_model_escalation_from_metadata():
    a = _FakeAdapter()
    await a.dispatch(
        {
            "id": "uuid-meta-esc",
            "identifier": "ZOE-ESC",
            "title": "Escalate on metadata",
            "metadata": {"model_escalation": True},
        }
    )
    review_body = ka.KanbanAdapter()._build_body(
        "review",
        {
            "id": "uuid-meta-esc",
            "identifier": "ZOE-ESC",
            "title": "Escalate on metadata",
            "metadata": {"model_escalation": True},
        },
        "ZOE-ESC",
    )
    assert "zoe-model-escalation: true" in review_body
    assert "anthropic/claude-sonnet-4.6" in review_body


@pytest.mark.asyncio
async def test_model_escalation_from_ticket_block():
    issue = {
        "id": "uuid-block-esc",
        "identifier": "ZOE-ESC",
        "title": "Escalate from block",
        "description": "```zoe-ticket\n{\"model_escalation\":true,\"confirm_paid_auto\":true}\n```",
    }
    review_body = ka.KanbanAdapter()._build_body("review", issue, "ZOE-ESC")
    assert "zoe-model-escalation: true" in review_body
    assert "anthropic/claude-sonnet-4.6" in review_body
    assert "openrouter/auto is allowed" in review_body


@pytest.mark.asyncio
async def test_dispatch_overnight_implement_mentions_free_fallback(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_SKIP_SCOUT", "1")
    a = _FakeAdapter()
    await a.dispatch(
        {"id": "uuid-night2", "identifier": "ZOE-N2", "title": "Night work", "engineering_mode": "overnight"}
    )
    creates = [c for c in a.calls if c[0] == "create"]
    impl_body = creates[0][creates[0].index("--body") + 1]
    assert "openrouter/free" in impl_body


@pytest.mark.asyncio
async def test_implement_body_mentions_scope_split_packet():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Hard thing", "description": ""},
        "ZOE-9",
    )
    assert "NEEDS_SPLIT=1" in body
    assert "SPLIT_PACKET=" in body
    assert "scope-split packet" in body


@pytest.mark.asyncio
async def test_retro_body_prefers_cheap_summary_models():
    body = ka.KanbanAdapter()._build_body(
        "retro",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )
    assert "gemini-2.5-flash" in body or "openrouter/free" in body


@pytest.mark.asyncio
async def test_dispatch_writes_zoe_ref_marker_into_body():
    # poll() correlates on the `zoe-ref:` body marker because the live
    # `hermes kanban list --json` output does not expose the idempotency key.
    a = _FakeAdapter()
    await a.dispatch({"id": "uuid-7", "identifier": "ZOE-7", "title": "t"})
    creates = [c for c in a.calls if c[0] == "create"]
    assert len(creates) == 1
    body = creates[0][creates[0].index("--body") + 1]
    assert "zoe-ref: multica:uuid-7:scout" in body
    assert "zoe-chain: v4" in body


@pytest.mark.asyncio
async def test_closeout_body_instructs_merge_when_ready():
    body = ka.KanbanAdapter()._build_body(
        "closeout",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )
    assert "run_greploop_guard.sh --pr N --once" in body
    assert "--merge-when-ready" in body
    assert "MERGE_SHA=" in body
    assert "--packet-only" in body
    assert "never --admin" in body
    assert "COST/ITERATION FAST PATH" in body
    assert "make the first repository command" in body
    assert "Do not repeat the rebase/guard cycle" in body
    assert "duplicate `gh pr view`" in body


@pytest.mark.asyncio
async def test_scout_body_is_read_only():
    body = ka.KanbanAdapter()._build_body(
        "scout",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )
    assert "read-only" in body
    assert "Do NOT edit code" in body
    assert "TOOLS_USED" in body


@pytest.mark.asyncio
async def test_retro_body_captures_learnings():
    body = ka.KanbanAdapter()._build_body(
        "retro",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )
    assert "RETRO=" in body or "LEARNINGS=" in body
    assert "Do NOT merge" in body


@pytest.mark.asyncio
async def test_poll_done_when_retro_completes():
    rows = [
        _row("scout", "done", v3=True),
        _row("implement", "done", v3=True),
        _row("verify", "done", v3=True),
        _row("review", "done", v3=True),
        _row("closeout", "done", v3=True),
        _row("retro", "done", v3=True),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["status"] == "done"
    assert out["phases"]["retro"] == "done"


@pytest.mark.asyncio
async def test_poll_v2_chain_done_at_closeout_without_retro():
    rows = [
        _row("implement", "done"),
        _row("verify", "done"),
        _row("review", "done"),
        _row("closeout", "done"),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["status"] == "done"


@pytest.mark.asyncio
async def test_poll_skip_scout_v3_chain_done_when_retro_completes():
    rows = [
        _row("implement", "done", v3=True),
        _row("verify", "done", v3=True),
        _row("review", "done", v3=True),
        _row("closeout", "done", v3=True),
        _row("retro", "done", v3=True),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["status"] == "done"


@pytest.mark.asyncio
async def test_poll_running_when_closeout_done_but_retro_pending():
    rows = [
        _row("scout", "done", v3=True),
        _row("implement", "done", v3=True),
        _row("verify", "done", v3=True),
        _row("review", "done", v3=True),
        _row("closeout", "done", v3=True),
        _row("retro", "todo", v3=True),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["status"] == "running"


@pytest.mark.asyncio
async def test_poll_skip_scout_v3_partial_missing_retro_is_redispatchable():
    rows = [
        _row("implement", "done", v3=True),
        _row("verify", "done", v3=True),
        _row("review", "done", v3=True),
        _row("closeout", "done", v3=True),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["status"] == "partial"


@pytest.mark.asyncio
async def test_verify_body_requires_evidence_gate():
    body = ka.KanbanAdapter()._build_body(
        "verify",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )
    assert "objective test/evidence gate" in body
    assert "TESTS" in body
    assert "VALIDATORS" in body
    assert "validate_structure.py" in body
    assert "validate_critical_files.py" in body


@pytest.mark.asyncio
async def test_review_body_requires_verify_evidence():
    body = ka.KanbanAdapter()._build_body(
        "review",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )
    assert "verify-phase evidence" in body
    assert "Block" in body or "block" in body
    assert "pipeline_evidence_commands.py mark-reviewed multica:uuid-1" in body
    assert "--critical-count <N>" in body
    assert "REVIEW=<approved or blocked>" in body


@pytest.mark.asyncio
async def test_dispatch_pins_expected_skills():
    a = _FakeAdapter()
    await a.dispatch({"id": "u", "identifier": "ZOE-1", "title": "t"})
    creates = [c for c in a.calls if c[0] == "create"]
    scout_skills = [creates[0][i + 1] for i, v in enumerate(creates[0]) if v == "--skill"]
    assert "zoe-graphify" in scout_skills


@pytest.mark.asyncio
async def test_dispatch_pins_implement_skill_when_scout_skipped(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_SKIP_SCOUT", "1")
    a = _FakeAdapter()
    await a.dispatch({"id": "u", "identifier": "ZOE-1", "title": "t"})
    creates = [c for c in a.calls if c[0] == "create"]
    impl_skills = [creates[0][i + 1] for i, v in enumerate(creates[0]) if v == "--skill"]
    assert impl_skills == ["zoe-engineering"]


@pytest.mark.asyncio
async def test_dispatch_requires_issue_id():
    a = _FakeAdapter()
    result = await a.dispatch({"identifier": "ZOE-2"})
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_poll_not_found():
    a = _FakeAdapter(list_rows=[{"id": "t_x", "idempotency_key": "other:1:implement"}])
    out = await a.poll("multica:uuid-1")
    assert out["found"] is False
    assert out["status"] == "not_found"


@pytest.mark.asyncio
async def test_poll_blocked_detected():
    rows = [
        {"id": "t_i", "idempotency_key": "multica:u:implement", "status": "blocked", "block_reason": "dirty tree"},
        {"id": "t_r", "idempotency_key": "multica:u:review", "status": "todo"},
        {"id": "t_c", "idempotency_key": "multica:u:closeout", "status": "todo"},
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:u")
    assert out["found"] is True
    assert out["status"] == "blocked"
    assert "dirty tree" in out["blocker"]


@pytest.mark.asyncio
async def test_poll_partial_chain_is_redispatchable():
    # closeout phase never got created (e.g. CLI error mid-chain): must report
    # "partial" (not "running") so the sync path re-dispatches instead of
    # skipping the wedged chain forever.
    rows = [
        {"id": "t_i", "idempotency_key": "multica:u:implement", "status": "done"},
        {"id": "t_r", "idempotency_key": "multica:u:review", "status": "running"},
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:u")
    assert out["found"] is True
    assert out["status"] == "partial"


def _row(phase, status, **extra):
    """A Kanban list row shaped like the real CLI: body marker, NO idempotency_key."""
    issue_id = extra.pop("issue_id", "uuid-9")
    body = f"Multica issue: ZOE-9 (id {issue_id})\nzoe-ref: multica:{issue_id}:{phase}\nTitle: x"
    chain_version = extra.pop("chain_version", None)
    if chain_version:
        body = (
            f"Multica issue: ZOE-9 (id {issue_id})\n"
            f"zoe-ref: multica:{issue_id}:{phase}\n"
            f"zoe-chain: {chain_version}\nTitle: x"
        )
    if extra.pop("v3", False):
        body = f"Multica issue: ZOE-9 (id {issue_id})\nzoe-ref: multica:{issue_id}:{phase}\nzoe-chain: v3\nTitle: x"
    return {
        "id": f"t_{phase}",
        "body": body,
        "status": status,
        **extra,
    }


@pytest.mark.asyncio
async def test_poll_correlates_via_body_marker_when_no_idempotency_key():
    # Regression: live `hermes kanban list --json` rows have no idempotency_key,
    # so correlation must fall back to the `zoe-ref:` body marker — otherwise the
    # in_progress->done auto-advance never fires.
    rows = [
        _row("implement", "done"),
        _row("review", "done"),
        _row("closeout", "done"),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["found"] is True
    assert out["status"] == "done"
    assert out["phases"] == {"implement": "done", "review": "done", "closeout": "done"}


@pytest.mark.asyncio
async def test_poll_current_chain_includes_verify_phase():
    rows = [
        _row("implement", "done"),
        _row("verify", "done"),
        _row("review", "running"),
        _row("closeout", "todo"),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["found"] is True
    assert out["status"] == "running"
    assert out["phases"]["verify"] == "done"


@pytest.mark.asyncio
async def test_poll_body_marker_blocked_detected():
    rows = [
        _row("implement", "blocked", block_reason="dirty tree"),
        _row("review", "todo"),
        _row("closeout", "todo"),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["status"] == "blocked"
    assert "dirty tree" in out["blocker"]


@pytest.mark.asyncio
async def test_poll_partial_chain_via_body_marker_is_redispatchable():
    # Production path: real `hermes kanban list --json` rows expose the `zoe-ref:`
    # body marker, NOT the idempotency key. A chain missing its closeout phase must
    # still report "partial" so the sync path re-dispatches and backfills it. The
    # idempotency_key-based partial test exercises the forward-compat branch only;
    # this guards the marker regex (e.g. dropping re.MULTILINE) from silently
    # reporting "not_found" and wedging the chain forever.
    rows = [
        _row("implement", "done"),
        _row("review", "running"),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["found"] is True
    assert out["status"] == "partial"


@pytest.mark.asyncio
async def test_poll_v4_done_phase_with_ready_next_phase_is_partial():
    rows = [_row("scout", "done", chain_version="v4")]
    show = {"t_scout": {"latest_summary": "TOOLS_USED=graphify\nSCOUT_SUMMARY=small", "comments": []}}
    a = _FakeAdapter(list_rows=rows, show_map=show)
    out = await a.poll("multica:uuid-9")
    assert out["found"] is True
    assert out["status"] == "partial"
    assert out["pipeline"]["phase"] == "implement"
    assert out["pipeline"]["status"] == "todo"


@pytest.mark.asyncio
async def test_poll_v4_pipeline_sync_failure_blocks_redispatch(monkeypatch):
    async def fail_sync(*args, **kwargs):
        raise RuntimeError("pipeline store unavailable")

    monkeypatch.setattr("pipeline_store.sync_pipeline_from_chain", fail_sync)
    rows = [_row("scout", "done", chain_version="v4")]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")

    assert out["status"] == "blocked"
    assert out["blocker"] == "pipeline_store_unavailable"
    assert out["pipeline"]["tracked"] is False
    assert out["pipeline"]["terminal_block"] is True
    assert "pipeline store unavailable" in out["pipeline"]["error"]






@pytest.mark.asyncio
async def test_poll_non_v4_pipeline_terminal_block_stays_blocked():
    from pipeline_evidence import PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-9",
        phase="implement",
        status="blocked",
        repeated_block_count=2,
        last_block_fingerprint="abc123",
    )
    save_state(state, event="fingerprint_abort")
    rows = [_row("implement", "running")]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")

    assert out["status"] == "blocked"
    assert "pipeline terminal block" in out["blocker"]




@pytest.mark.asyncio
async def test_poll_v4_running_pipeline_clears_stale_recovered_blocker():
    from pipeline_evidence import EvidenceItem, PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-9",
        phase="verify",
        status="running",
        evidence_profile="audit",
        attempts={"implement": 1, "verify": 1},
        evidence=[
            EvidenceItem(
                kind="tool",
                summary="audit/no-PR implement auto-recovered",
                passed=True,
                metadata={"source": "audit_protocol_recovery", "phase": "implement"},
            ),
            EvidenceItem(
                kind="validator",
                summary="verify pending",
                passed=True,
                metadata={"phase": "verify"},
            ),
        ],
    )
    save_state(state, event="effect_requested")
    rows = [
        _row("implement", "blocked", chain_version="v4", block_reason="implement blocked"),
        _row("verify", "running", chain_version="v4"),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")

    assert out["status"] == "running"
    assert out["blocker"] is None
    assert out["pipeline"]["phase"] == "verify"
    assert out["pipeline"]["status"] == "running"


@pytest.mark.asyncio
async def test_poll_v4_partial_clears_stale_prior_phase_blocker():
    from pipeline_evidence import EvidenceItem, PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-9",
        phase="verify",
        status="todo",
        evidence_profile="audit",
        attempts={"scout": 1, "implement": 1},
        evidence=[
            EvidenceItem(
                kind="tool",
                summary="audit/no-PR scout auto-recovered",
                passed=True,
                metadata={"source": "audit_protocol_recovery", "phase": "scout"},
            ),
            EvidenceItem(
                kind="tool",
                summary="audit/no-PR implement auto-recovered",
                passed=True,
                metadata={"source": "audit_protocol_recovery", "phase": "implement"},
            ),
        ],
    )
    save_state(state, event="transition")
    rows = [
        _row("scout", "blocked", chain_version="v4"),
        _row("implement", "blocked", chain_version="v4"),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")

    assert out["status"] == "partial"
    assert out["blocker"] is None
    assert out["pipeline"]["phase"] == "verify"
    assert out["pipeline"]["status"] == "todo"


@pytest.mark.asyncio
async def test_poll_v4_todo_existing_phase_clears_stale_prior_blocker():
    from pipeline_evidence import EvidenceItem, PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-9",
        phase="verify",
        status="todo",
        evidence_profile="audit",
        attempts={"scout": 1, "implement": 1, "verify": 1},
        evidence=[
            EvidenceItem(
                kind="tool",
                summary="audit/no-PR scout auto-recovered",
                passed=True,
                metadata={"source": "audit_protocol_recovery", "phase": "scout"},
            ),
            EvidenceItem(
                kind="tool",
                summary="audit/no-PR implement auto-recovered",
                passed=True,
                metadata={"source": "audit_protocol_recovery", "phase": "implement"},
            ),
        ],
    )
    save_state(state, event="transition")
    rows = [
        _row("scout", "blocked", chain_version="v4"),
        _row("implement", "blocked", chain_version="v4"),
        _row("verify", "ready", chain_version="v4"),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")

    assert out["status"] == "running"
    assert out["blocker"] is None
    assert out["pipeline"]["phase"] == "verify"
    assert out["pipeline"]["status"] == "todo"


@pytest.mark.asyncio
async def test_poll_v4_audit_protocol_recovery_reports_partial_for_next_phase():
    from pipeline_store import bootstrap_state

    await bootstrap_state(
        "multica:uuid-9",
        issue={"description": "evidence_profile: audit"},
    )
    rows = [_row("implement", "blocked", chain_version="v4", block_reason="implement blocked")]
    show = {
        "t_implement": {
            "latest_summary": "",
            "comments": [],
            "events": [{"kind": "protocol_violation", "payload": {"exit_code": 0}}],
            "runs": [
                {
                    "status": "crashed",
                    "outcome": "crashed",
                    "error": "worker exited cleanly without calling kanban_complete",
                }
            ],
        }
    }
    a = _FakeAdapter(list_rows=rows, show_map=show)
    out = await a.poll("multica:uuid-9")

    assert out["status"] == "partial"
    assert out["blocker"] is None
    assert out["pipeline"]["phase"] == "verify"
    assert out["pipeline"]["status"] == "todo"


@pytest.mark.asyncio
async def test_poll_v4_does_not_promote_kanban_blocked_to_partial():
    rows = [_row("implement", "blocked", chain_version="v4", block_reason="dirty tree")]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["status"] == "blocked"
    assert "dirty tree" in (out["blocker"] or "")


@pytest.mark.asyncio
async def test_poll_current_partial_chain_with_verify_is_redispatchable():
    rows = [
        _row("implement", "done"),
        _row("verify", "running"),
    ]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["found"] is True
    assert out["status"] == "partial"


@pytest.mark.asyncio
async def test_poll_ignores_rows_without_marker():
    # Foreign tasks (other dispatchers) carry neither marker nor matching key.
    rows = [{"id": "t_x", "body": "some unrelated task body", "status": "running"}]
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9")
    assert out["found"] is False
    assert out["status"] == "not_found"


@pytest.mark.asyncio
async def test_poll_done_and_pr_extracted():
    rows = [
        {"id": "t_i", "idempotency_key": "multica:u:implement", "status": "done"},
        {"id": "t_r", "idempotency_key": "multica:u:review", "status": "done"},
        {"id": "t_c", "idempotency_key": "multica:u:closeout", "status": "done"},
    ]
    show = {
        "t_c": {
            "latest_summary": "",
            "comments": [{"body": "Merged via https://github.com/o/r/pull/42 — done"}],
        }
    }
    a = _FakeAdapter(list_rows=rows, show_map=show)
    out = await a.poll("multica:u")
    assert out["status"] == "done"
    assert out["pr_url"] == "https://github.com/o/r/pull/42"


def test_protocol_violation_count():
    detail = {
        "events": [
            {"kind": "claimed"},
            {"kind": "protocol_violation", "payload": {"exit_code": 0}},
            {"kind": "protocol_violation", "payload": {"exit_code": 0}},
        ]
    }
    assert ka._protocol_violation_count(detail) == 2


def test_phase_budget_reason_enforces_tool_and_runtime_limits(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    log_path.write_text("\n".join(["  ┊ tool call"] * 9), encoding="utf-8")
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)
    monkeypatch.setenv("ZOE_KANBAN_SCOUT_TOOL_BUDGET", "8")

    reason = kb.phase_budget_reason(
        "t_scout",
        "scout",
        {"task": {"started_at": 100}},
        now=110,
    )

    assert reason is None

    log_path.write_text("\n".join(["  ┊ tool call"] * 11), encoding="utf-8")
    reason = kb.phase_budget_reason(
        "t_scout",
        "scout",
        {"task": {"started_at": 100}},
        now=110,
    )
    assert reason is not None
    assert "SCOUT_BUDGET" in reason
    assert "steps=11" in reason
    assert "guidance_limit=8" in reason
    assert "hard_limit=10" in reason

    log_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("ZOE_KANBAN_SCOUT_RUNTIME_BUDGET_SECONDS", "5")
    reason = kb.phase_budget_reason(
        "t_scout",
        "scout",
        {"runs": [{"status": "running", "started_at": 100, "worker_pid": 123}]},
        now=106,
    )
    assert reason is not None
    assert "runtime budget exceeded" in reason


def test_phase_budget_accepts_iso_timestamp_and_ascii_step_logs(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    log_path.write_text("| shell command  0.2s\n", encoding="utf-8")
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)
    monkeypatch.setenv("ZOE_KANBAN_SCOUT_TOOL_BUDGET", "5")
    monkeypatch.setenv("ZOE_KANBAN_SCOUT_RUNTIME_BUDGET_SECONDS", "5")

    assert kb.tool_step_count("t_scout") == 1
    reason = kb.phase_budget_reason(
        "t_scout",
        "scout",
        {"task": {"started_at": "2026-06-05T12:00:00Z"}},
        now=kb._timestamp("2026-06-05T12:00:06Z"),
    )
    assert reason is not None
    assert "runtime budget exceeded" in reason


def test_tool_step_count_only_measures_latest_task_run(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    log_path.write_text(
        "\n".join(
            [
                "Query: work kanban task t_scout",
                *(["  ┊ old tool call"] * 9),
                "Query: work kanban task t_scout",
                "  ┊ current tool call",
                "  ┊ current tool call",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)

    assert kb.tool_step_count("t_scout") == 2


def test_fresh_resumed_run_with_no_steps_does_not_warn(tmp_path, monkeypatch, caplog):
    log_path = tmp_path / "task.log"
    log_path.write_text(
        "Query: work kanban task t_scout\nInitializing agent...\n────────────────\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)
    kb._ZERO_STEP_WARNED.discard("t_scout")

    assert kb.tool_step_count("t_scout") == 0
    assert "could not identify tool steps" not in caplog.text


def test_stale_log_is_ignored_until_resumed_worker_writes(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    log_path.write_text(
        "Query: work kanban task t_scout\n" + "\n".join(["  ┊ old tool call"] * 9),
        encoding="utf-8",
    )
    log_path.touch()
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)

    assert kb.tool_step_count("t_scout", since=log_path.stat().st_mtime + 1) == 0


def test_latest_attempt_timestamp_wins_before_run_reaches_running():
    detail = {
        "task": {"started_at": 100},
        "runs": [
            {"status": "blocked", "started_at": 300},
            {"status": "claimed", "started_at": 200},
        ],
    }

    assert kb._started_timestamp(detail) == 200


def test_attempt_without_timestamp_falls_back_to_task_start():
    detail = {
        "task": {"started_at": 100},
        "runs": [{"status": "running", "worker_pid": 4242}],
    }

    assert kb._started_timestamp(detail) == 100


def test_running_worker_pids_require_expected_hermes_command(monkeypatch):
    monkeypatch.setattr(kb, "_is_expected_worker", lambda pid: pid == 4242)
    detail = {
        "runs": [
            {"status": "running", "worker_pid": 2},
            {"status": "running", "worker_pid": 4242},
            {"status": "done", "worker_pid": 5252},
        ]
    }

    assert kb.running_worker_pids(detail) == [4242]


@pytest.mark.asyncio
async def test_poll_auto_blocks_and_terminates_worker_after_phase_budget(monkeypatch):
    rows = [_row("scout", "running")]
    show = {
        "t_scout": {
            "task": {"started_at": 100},
            "runs": [{"status": "running", "started_at": 100, "worker_pid": 4242}],
        }
    }
    stopped = []
    monkeypatch.setattr(
        ka,
        "phase_budget_reason",
        lambda *_args, **_kwargs: "BLOCKER=SCOUT_BUDGET: test limit",
    )
    monkeypatch.setattr(kb, "_is_expected_worker", lambda pid: pid == 4242)
    monkeypatch.setattr(ka, "terminate_running_workers", lambda detail: stopped.extend(kb.running_worker_pids(detail)))
    a = _FakeAdapter(list_rows=rows, show_map=show)

    out = await a.poll("multica:uuid-9")

    assert out["status"] == "blocked"
    assert "SCOUT_BUDGET" in (out["blocker"] or "")
    assert stopped == [4242]
    assert any(call[0] == "block" for call in a.calls)


@pytest.mark.asyncio
async def test_poll_does_not_apply_previous_attempt_budget_to_ready_task(monkeypatch):
    rows = [_row("scout", "ready")]
    show = {
        "t_scout": {
            "task": {"started_at": 100},
            "runs": [{"status": "blocked", "started_at": 100}],
        }
    }
    monkeypatch.setattr(
        ka,
        "phase_budget_reason",
        lambda *_args, **_kwargs: pytest.fail("ready task must not be budgeted"),
    )
    a = _FakeAdapter(list_rows=rows, show_map=show)

    out = await a.poll("multica:uuid-9")

    assert out["status"] == "partial"
    assert not any(call[0] == "block" for call in a.calls)


@pytest.mark.asyncio
async def test_poll_does_not_apply_previous_protocol_violations_to_ready_task(monkeypatch):
    monkeypatch.setattr(ka, "_PROTOCOL_VIOLATION_LIMIT", 2)
    rows = [_row("scout", "ready")]
    show = {
        "t_scout": {
            "events": [
                {"kind": "protocol_violation", "payload": {"exit_code": 0}},
                {"kind": "protocol_violation", "payload": {"exit_code": 0}},
            ]
        }
    }
    a = _FakeAdapter(list_rows=rows, show_map=show)

    out = await a.poll("multica:uuid-9")

    assert out["status"] == "partial"
    assert not any(call[0] == "block" for call in a.calls)


@pytest.mark.asyncio
async def test_poll_auto_blocks_after_protocol_violations(monkeypatch):
    monkeypatch.setattr(ka, "_PROTOCOL_VIOLATION_LIMIT", 2)
    rows = [_row("implement", "running")]
    show = {
        "t_implement": {
            "events": [
                {"kind": "protocol_violation", "payload": {"exit_code": 0}},
                {"kind": "protocol_violation", "payload": {"exit_code": 0}},
            ]
        }
    }
    a = _FakeAdapter(list_rows=rows, show_map=show)
    out = await a.poll("multica:uuid-9")
    assert out["status"] == "blocked"
    assert "PROTOCOL_VIOLATION" in (out["blocker"] or "")
    assert any(c[0] == "block" for c in a.calls)


def test_closeout_body_requires_terminal_protocol():
    body = ka.KanbanAdapter()._build_body(
        "closeout",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )
    assert "TERMINAL PROTOCOL" in body
    assert "kanban_complete" in body
    assert "kanban_block" in body
    assert "AUDIT_ONLY=<1 for no-PR audit closeout, otherwise 0>" in body


def test_closeout_body_uses_supported_greploop_launcher():
    body = ka.KanbanAdapter()._build_body(
        "closeout",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )

    assert "scripts/maintenance/run_greploop_guard.sh --pr N --merge-when-ready" in body
    assert "python3 scripts/maintenance/greploop_guard.py" not in body


def test_audit_no_pr_phases_have_bounded_completion_path():
    adapter = ka.KanbanAdapter()
    issue = {"id": "uuid-1", "identifier": "ZOE-9", "title": "Audit smoke", "description": ""}
    verify = adapter._build_body("verify", issue, "ZOE-9")
    review = adapter._build_body("review", issue, "ZOE-9")
    closeout = adapter._build_body("closeout", issue, "ZOE-9")

    assert "AUDIT/NO-PR FAST PATH" in verify
    assert "no PR_URL" in verify
    assert "do not load broad skills" in verify
    assert "TESTS=not applicable/audit evidence" in verify
    assert "VALIDATORS=not applicable/audit-only" in verify
    assert "VERIFY_BUDGET" in verify
    assert verify.index("AUDIT/NO-PR FAST PATH") < verify.index("Start with `kanban_show`")
    assert "audit-only/no-code" in review
    assert "REVIEW_BUDGET" in review
    assert "For smoke/audit tickets" in closeout
    assert "do not wait for Greptile" in closeout
    assert "- If a code task has no PR" in closeout


def test_review_body_has_post_merge_fast_path_and_terminal_marker_guidance():
    body = ka.KanbanAdapter()._build_body(
        "review",
        {
            "id": "uuid-1",
            "identifier": "ZOE-9",
            "title": "Review merged change",
            "description": "",
        },
        "ZOE-9",
    )

    assert "POST-MERGE FAST PATH" in body
    assert "GREPTILE/greptile_status=5/5" in body
    assert "do not explore broad worktrees" in body
    assert "do not call `--help` after it" in body
    assert "immediately call `kanban_complete` in the same turn" in body
    assert body.index("POST-MERGE FAST PATH") < body.index("AUDIT/NO-PR FAST PATH")


def test_closeout_body_defers_multica_done_until_retro():
    body = ka.KanbanAdapter()._build_body(
        "closeout",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )
    assert "Zoe will update Multica after the retro phase completes" in body
    assert "update the Multica issue to done" not in body
    assert "note Multica done" not in body
    assert "MULTICA=<Zoe updates after retro; report blocker if any>" in body


def test_implement_body_puts_bounded_fast_paths_before_graphify():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Audit driver", "description": "evidence_profile: audit"},
        "ZOE-9",
    )

    assert "AUDIT/SMOKE FAST PATH" in body
    assert "TOOLS_USED=audit-read" in body
    assert "TESTS=not applicable/audit-only" in body
    assert "Graphify map" in body
    assert "explicitly says audit-only" in body
    assert "uses trace/map with an audit/no-code qualifier" in body
    assert "SMALL EXPLICIT CODE FAST PATH" in body
    assert "Start editing within 6 tool/model steps" in body
    assert "BLOCKER=IMPLEMENT_BUDGET" in body
    assert "Do NOT create additional Hermes/Kanban tasks" in body
    assert "scaffold subtasks" in body
    assert "broad or ambiguous code-changing tickets only" in body
    assert body.index("AUDIT/SMOKE FAST PATH") < body.index("Graphify map")
    assert body.index("SMALL EXPLICIT CODE FAST PATH") < body.index("Graphify map")


def test_scout_body_has_child_followup_fast_path():
    body = ka.KanbanAdapter()._build_body(
        "scout",
        {
            "id": "uuid-1",
            "identifier": "ZOE-9",
            "title": "child fixture follow-up",
            "description": "zoe_kind: child\nacceptance criteria: focused fixture tests",
        },
        "ZOE-9",
    )

    assert "CHILD/FOLLOW-UP FAST PATH" in body
    assert "do not inspect branch history or broad worktrees" in body
    assert "at most the named artifact files" in body
    assert body.index("CHILD/FOLLOW-UP FAST PATH") < body.index("Keep this phase bounded")


def test_scout_body_has_broad_parent_split_fast_path():
    # This is a static prompt-contract assertion, not conditional broad-ticket detection.
    body = ka.KanbanAdapter()._build_body(
        "scout",
        {
            "id": "uuid-1",
            "identifier": "ZOE-5288",
            "title": "card-upgrade: backend service, registry, and domain builders",
            "description": "Wire chat.py, intent_router.py, zoe_agent.py, ui_orchestrator.py, and mcp_server.py",
        },
        "ZOE-5288",
    )

    assert "BROAD PARENT SPLIT FAST PATH" in body
    assert "BLOCKER=SCOPE_SPLIT_REQUIRED" in body
    assert "NEEDS_SPLIT=1" in body
    assert "SPLIT_PACKET={" in body
    assert "child_issue_template" in body
    assert body.index("BROAD PARENT SPLIT FAST PATH") < body.index("Keep this phase bounded")


def test_retro_body_has_post_closeout_fast_path():
    body = ka.KanbanAdapter()._build_body(
        "retro",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Retro", "description": ""},
        "ZOE-9",
    )

    assert "POST-CLOSEOUT FAST PATH" in body
    assert "PR_URL, MERGE_SHA" in body
    assert "GREPTILE/greptile_status=5/5 or already_merged" in body
    assert "GREPTILE=5/5/already_merged" not in body
    assert "do not inspect worktrees, branch history, or GitHub" in body
    assert "Use `kanban_show`, summarize one learning, and `kanban_complete`" in body
    assert body.index("POST-CLOSEOUT FAST PATH") < body.index("AUDIT/NO-PR FAST PATH")


def test_audit_no_pr_phases_do_not_preload_broad_skills():
    from executors.kanban_adapter import _phase_plan_entry

    audit_issue = {"title": "audit-only lifecycle", "description": "operator audit"}
    no_code_issue = {"title": "no code/config changes only", "description": "operator check"}
    smoke_only_issue = {"title": "smoke test only", "description": "operator check"}
    code_smoke_issue = {"title": "add smoke test coverage", "description": "code change required"}
    code_clause_issue = {
        "title": "refactor auth flow",
        "description": "code change required; no code change needed in legacy adapter",
    }

    for phase in ("scout", "implement", "verify", "review", "closeout", "retro"):
        assert _phase_plan_entry(phase, audit_issue)[2] == ()
        assert _phase_plan_entry(phase, no_code_issue)[2] == ()
        assert _phase_plan_entry(phase, smoke_only_issue)[2] == ()

    assert _phase_plan_entry("implement", code_smoke_issue)[2] == ("zoe-engineering",)
    assert _phase_plan_entry("review", code_smoke_issue)[2] == ("zoe-engineering",)
    assert _phase_plan_entry("retro", code_smoke_issue)[2] == ("zoe-status-refresh",)
    assert _phase_plan_entry("implement", code_clause_issue)[2] == ("zoe-engineering",)
    assert _phase_plan_entry("review", code_clause_issue)[2] == ("zoe-engineering",)
    assert _phase_plan_entry("retro", code_clause_issue)[2] == ("zoe-status-refresh",)

    assert _phase_plan_entry("verify", code_smoke_issue)[2] == ("zoe-engineering",)
    assert _phase_plan_entry("verify", code_clause_issue)[2] == ("zoe-engineering",)
    assert _phase_plan_entry("closeout", code_smoke_issue)[2] == ("github-greptile-loop",)
    assert _phase_plan_entry("closeout", code_clause_issue)[2] == ("github-greptile-loop",)
