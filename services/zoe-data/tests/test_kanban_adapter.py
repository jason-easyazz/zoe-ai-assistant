"""Tests for the Hermes Kanban executor adapter (CLI mocked)."""
import asyncio
import json
from pathlib import Path

import pytest

import executors.kanban_adapter as ka
import kanban_phase_budget as kb

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def _mock_ensure_worktree(monkeypatch):
    """Dispatch tests must not run real git worktree subprocesses."""
    monkeypatch.setattr(
        "worktree_bootstrap.prepare_kanban_worktree",
        lambda task_id, **kwargs: Path(f"/tmp/worktrees/{task_id}"),
    )
    monkeypatch.setattr(
        "worktree_bootstrap.prepare_existing_pr_revision_worktree",
        lambda task_id, pr_url, **kwargs: Path(f"/tmp/worktrees/{task_id}"),
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
        if verb == "complete":
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
async def test_dispatch_prepares_existing_pr_revision_worktree(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_SKIP_SCOUT", "1")
    calls = []

    def fake_prepare(task_id, pr_url, **kwargs):
        calls.append((task_id, pr_url, kwargs))
        return Path(f"/tmp/worktrees/{task_id}")

    monkeypatch.setattr("worktree_bootstrap.prepare_existing_pr_revision_worktree", fake_prepare)
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-pr-revision",
            "identifier": "ZOE-5354",
            "title": "Metrics auth revision",
            "description": """```zoe-ticket
{"pr_url":"https://github.com/jason-easyazz/zoe-ai-assistant/pull/213"}
```""",
        }
    )

    assert result["ok"] is True
    assert result["phase"] == "implement"
    assert calls == [
        ("t_implement", "https://github.com/jason-easyazz/zoe-ai-assistant/pull/213", {})
    ]


@pytest.mark.asyncio
async def test_dispatch_blocks_existing_pr_revision_when_precheckout_fails(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_SKIP_SCOUT", "1")

    def fail_prepare(*args, **kwargs):
        raise RuntimeError("fetch failed")

    monkeypatch.setattr("worktree_bootstrap.prepare_existing_pr_revision_worktree", fail_prepare)
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-pr-revision-fail",
            "identifier": "ZOE-5354",
            "title": "Metrics auth revision",
            "description": """```zoe-ticket
{"pr_url":"https://github.com/jason-easyazz/zoe-ai-assistant/pull/213"}
```""",
        }
    )

    assert result["ok"] is False
    assert result["reason"] == "existing PR worktree preparation failed"
    block_calls = [c for c in a.calls if c[0] == "block"]
    assert len(block_calls) == 1
    assert "BLOCKER=PR_REVISION_CHECKOUT_FAILED" in block_calls[0][2]


@pytest.mark.asyncio
async def test_dispatch_blocks_regular_worktree_failures_with_generic_reason(monkeypatch):
    monkeypatch.setenv("ZOE_KANBAN_SKIP_SCOUT", "1")

    def fail_prepare(*args, **kwargs):
        raise RuntimeError("worktree add failed")

    monkeypatch.setattr("worktree_bootstrap.prepare_kanban_worktree", fail_prepare)
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-regular-worktree-fail",
            "identifier": "ZOE-WT",
            "title": "Regular implement task",
        }
    )

    assert result["ok"] is False
    assert result["reason"] == "kanban worktree preparation failed"
    block_calls = [c for c in a.calls if c[0] == "block"]
    assert len(block_calls) == 1
    assert "BLOCKER=WORKTREE_PREPARATION_FAILED" in block_calls[0][2]
    assert "PR_REVISION_CHECKOUT_FAILED" not in block_calls[0][2]


@pytest.mark.asyncio
async def test_dispatch_retro_uses_main_repo_workspace():
    from pipeline_evidence import PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-retro-workspace",
        phase="retro",
        status="todo",
        attempts={"implement": 1, "verify": 1, "review": 1, "closeout": 1},
    )
    save_state(state, event="operator_resumed")
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-retro-workspace",
            "identifier": "ZOE-RETRO",
            "title": "Retro fallback",
        }
    )

    assert result["ok"] is True
    assert result["phase"] == "retro"
    create = [c for c in a.calls if c[0] == "create"][0]
    assert create[create.index("--workspace") + 1] == f"dir:{ka.zoe_repo_root()}"




@pytest.mark.asyncio
async def test_dispatch_verify_includes_already_covered_handoff():
    from pipeline_evidence import EvidenceItem, PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-already-covered-verify",
        phase="verify",
        status="todo",
        evidence_profile="audit",
        attempts={"implement": 1},
        evidence=[
            EvidenceItem(
                kind="tool",
                summary="focused harness test passed before edit; implementation already covered",
                passed=True,
                metadata={"source": "already_covered", "phase": "implement"},
            )
        ],
    )
    save_state(state, event="already_covered_implementation_skipped")
    a = _FakeAdapter()

    result = await a.dispatch(
        {
            "id": "uuid-already-covered-verify",
            "identifier": "ZOE-COVERED",
            "title": "Intent gap: already covered",
            "description": "Original ticket evidence should appear after handoff.",
        }
    )

    assert result["ok"] is True
    assert result["phase"] == "verify"
    create = [c for c in a.calls if c[0] == "create"][0]
    body = create[create.index("--body") + 1]
    assert "IMPLEMENT_ALREADY_COVERED=1" in body
    assert "Do not rerun zoe_apply_intent_gap_contract.py" in body
    assert body.index("IMPLEMENT_ALREADY_COVERED=1") < body.index("Original ticket evidence")


@pytest.mark.asyncio
async def test_dispatch_review_includes_audit_pipeline_handoff():
    from pipeline_evidence import EvidenceItem, PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-audit-review",
        phase="review",
        status="todo",
        evidence_profile="audit",
        attempts={"implement": 1, "verify": 1},
        evidence=[
            EvidenceItem(
                kind="validator",
                summary="focused validation passed",
                passed=True,
                metadata={"source": "audit_protocol_recovery", "phase": "verify"},
            )
        ],
    )
    save_state(state, event="transition")
    a = _FakeAdapter()

    result = await a.dispatch(
        {
            "id": "uuid-audit-review",
            "identifier": "ZOE-AUDIT",
            "title": "Audit no PR",
            "description": "Original audit ticket.",
        }
    )

    assert result["ok"] is True
    assert result["phase"] == "review"
    create = [c for c in a.calls if c[0] == "create"][0]
    body = create[create.index("--body") + 1]
    assert "Zoe pipeline handoff (authoritative):" in body
    assert "AUDIT_ONLY=1" in body
    assert "PR_URL=" in body
    assert "Do not hunt for PRs" in body
    assert "kanban_complete(summary=\"REVIEW=approved" in body
    assert body.index("AUDIT_ONLY=1") < body.index("Original audit ticket")


@pytest.mark.asyncio
async def test_dispatch_verify_injects_pr_url_from_implement_evidence():
    """Normal (real-PR) verify handoff must carry the implement phase's PR_URL
    from the authoritative pipeline evidence, so the verify worker does not race
    the async Multica pr_url write, block 'awaiting PR evidence', and bounce the
    chain back to implement."""
    from pipeline_evidence import EvidenceItem, PipelineState
    from pipeline_store import save_state

    pr = "https://github.com/jason-easyazz/zoe-ai-assistant/pull/600"
    state = PipelineState(
        task_ref="multica:uuid-normal-verify",
        phase="verify",
        status="todo",
        attempts={"implement": 1},
        evidence=[
            EvidenceItem(
                kind="pr",
                summary=pr,
                artifact=pr,
                passed=True,
                metadata={"source": "handoff", "phase": "implement"},
            )
        ],
    )
    save_state(state, event="transition")
    a = _FakeAdapter()

    result = await a.dispatch(
        {
            "id": "uuid-normal-verify",
            "identifier": "ZOE-NORMAL",
            "title": "Add focused tests",
            "description": "Original ticket body.",
        }
    )

    assert result["ok"] is True
    assert result["phase"] == "verify"
    create = [c for c in a.calls if c[0] == "create"][0]
    body = create[create.index("--body") + 1]
    assert "Zoe pipeline handoff (authoritative):" in body
    assert f"PR_URL={pr}" in body
    assert "AUDIT_ONLY" not in body  # normal path, not the audit handoff
    assert body.index(f"PR_URL={pr}") < body.index("Original ticket body")


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
async def test_dispatch_resumed_todo_archives_blocked_current_phase_before_retry():
    from pipeline_evidence import PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-retry-current",
        phase="implement",
        status="todo",
        attempts={"implement": 1},
    )
    save_state(state, event="operator_resumed")
    rows = [_row("implement", "blocked", chain_version="v4", issue_id="uuid-retry-current")]
    a = _FakeAdapter(list_rows=rows)
    result = await a.dispatch(
        {
            "id": "uuid-retry-current",
            "identifier": "ZOE-RETRY",
            "title": "Harness: retry current phase",
            "metadata": {"zoe_kind": "harness_fix", "acceptance_criteria": ["retry"]},
        }
    )

    archives = [c for c in a.calls if c[0] == "archive"]
    creates = [c for c in a.calls if c[0] == "create"]
    assert archives == [["archive", "t_implement"]]
    assert len(creates) == 1
    assert creates[0][creates[0].index("--idempotency-key") + 1] == "multica:uuid-retry-current:implement"
    assert result["ok"] is True
    assert result["phase"] == "implement"
    assert result["created"] == ["implement"]
    assert result["chain"] == {"implement": "t_implement"}



@pytest.mark.asyncio
async def test_dispatch_resumed_todo_reports_archive_failure_without_create():
    from pipeline_evidence import PipelineState
    from pipeline_store import save_state

    class ArchiveFailAdapter(_FakeAdapter):
        async def _run(self, args, *, expect_json=False):
            if args[0] == "archive":
                self.calls.append(args)
                raise ka.KanbanCLIError("archive failed")
            return await super()._run(args, expect_json=expect_json)

    state = PipelineState(
        task_ref="multica:uuid-archive-fail",
        phase="implement",
        status="todo",
        attempts={"implement": 1},
    )
    save_state(state, event="operator_resumed")
    rows = [_row("implement", "blocked", chain_version="v4", issue_id="uuid-archive-fail")]
    a = ArchiveFailAdapter(list_rows=rows)
    result = await a.dispatch(
        {
            "id": "uuid-archive-fail",
            "identifier": "ZOE-ARCHIVE",
            "title": "Harness: retry current phase",
            "metadata": {"zoe_kind": "harness_fix", "acceptance_criteria": ["retry"]},
        }
    )

    assert [c for c in a.calls if c[0] == "archive"] == [["archive", "t_implement"]]
    assert [c for c in a.calls if c[0] == "create"] == []
    assert result["ok"] is False
    assert result["reason"] == "stale phase archive failed"
    assert result["phase"] == "implement"



@pytest.mark.asyncio
async def test_dispatch_after_scout_evidence_creates_next_phase():
    from pipeline_store import bootstrap_state, save_state
    from pipeline_evidence import EvidenceItem, transition, with_evidence

    state = await bootstrap_state("multica:uuid-next", start_phase="scout")
    state = with_evidence(state, EvidenceItem(kind="tool", summary="codebase-memory map", passed=True))
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
async def test_dispatch_after_implement_evidence_creates_verify():
    """Regression: when implement is done the chain must auto-advance — dispatch
    creates the verify phase. This is the autonomous implement→verify→…→closeout
    path; if it regresses, chains strand at implement and never self-close-out.
    """
    from pipeline_store import bootstrap_state, save_state
    from pipeline_evidence import EvidenceItem, transition, with_evidence

    state = await bootstrap_state("multica:uuid-impl-verify", start_phase="implement")
    state = with_evidence(state, EvidenceItem(kind="tool", summary="codebase-memory", passed=True))
    state = with_evidence(
        state,
        EvidenceItem(
            kind="pr",
            summary="https://github.com/o/r/pull/1",
            artifact="https://github.com/o/r/pull/1",
            passed=True,
        ),
    )
    state = with_evidence(state, EvidenceItem(kind="test", summary="pytest passed", passed=True))
    state = transition(state, "complete")
    assert state.phase == "verify"
    save_state(state, event="transition", extra={"row_phase": "implement"})

    rows = [_row("implement", "done", chain_version="v4", issue_id="uuid-impl-verify")]
    a = _FakeAdapter(list_rows=rows)
    result = await a.dispatch({"id": "uuid-impl-verify", "identifier": "ZOE-IV", "title": "Fix thing"})

    creates = [c for c in a.calls if c[0] == "create"]
    assert result["phase"] == "verify"
    assert set(result["chain"]) == {"verify"}
    assert len(creates) == 1
    assert (
        creates[0][creates[0].index("--idempotency-key") + 1]
        == "multica:uuid-impl-verify:verify"
    )
    # verify must be parented to the completed implement row for chain ordering.
    assert "--parent" in creates[0]
    assert creates[0][creates[0].index("--parent") + 1] == "t_implement"


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
async def test_dispatch_skips_scout_for_harness_fix_with_acceptance_criteria():
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-harness-fix",
            "identifier": "ZOE-5446",
            "title": "Harness follow-up for implement budget blocks",
            "description": (
                "```zoe-ticket\n"
                '{"zoe_kind":"harness_fix","acceptance_criteria":["idempotent follow-up ticket"]}'
                "\n```"
            ),
        }
    )
    assert "scout" not in result["chain"]
    assert set(result["chain"]) == {"implement"}


@pytest.mark.asyncio
async def test_dispatch_skips_scout_for_harness_fix_metadata_with_acceptance_criteria():
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-harness-fix-meta",
            "identifier": "ZOE-5447",
            "title": "Harness follow-up for scout budget blocks",
            "metadata": {
                "zoe_kind": "harness_fix",
                "acceptance_criteria": ["budget blockers create one follow-up ticket"],
            },
        }
    )
    assert "scout" not in result["chain"]
    assert set(result["chain"]) == {"implement"}


@pytest.mark.asyncio
async def test_dispatch_keeps_scout_for_harness_fix_without_acceptance_criteria():
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-harness-fix-no-criteria",
            "identifier": "ZOE-5448",
            "title": "Harness follow-up without a concrete contract",
            "metadata": {"zoe_kind": "harness_fix"},
        }
    )
    assert set(result["chain"]) == {"scout"}


@pytest.mark.asyncio
async def test_dispatch_uses_bounded_goal_mode_for_actionable_code_audit_implement():
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-code-audit-goal",
            "identifier": "ZOE-5354",
            "title": "GET /metrics endpoint is unauthenticated",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_p0_security",
                "acceptance_criteria": ["Require admin or internal token on /metrics"],
            },
        }
    )

    creates = [c for c in a.calls if c[0] == "create"]
    assert result["phase"] == "implement"
    assert len(creates) == 1
    assert "--goal" in creates[0]
    assert creates[0][creates[0].index("--goal-max-turns") + 1] == "2"
    assert creates[0][creates[0].index("--max-retries") + 1] == "2"


@pytest.mark.asyncio
async def test_dispatch_omits_goal_mode_for_non_code_audit_implement():
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-harness-goal",
            "identifier": "ZOE-5449",
            "title": "Harness: follow up ITERATION_BUDGET",
            "metadata": {
                "zoe_kind": "harness_fix",
                "source": "engineering_blocker_followup",
                "acceptance_criteria": ["Focused tests cover blocker path"],
            },
        }
    )

    creates = [c for c in a.calls if c[0] == "create"]
    assert result["phase"] == "implement"
    assert len(creates) == 1
    assert "--goal" not in creates[0]
    assert "--goal-max-turns" not in creates[0]
    assert creates[0][creates[0].index("--max-retries") + 1] == "1"


@pytest.mark.asyncio
async def test_dispatch_skips_scout_for_code_audit_ticket_with_acceptance_criteria():
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-code-audit",
            "identifier": "ZOE-5354",
            "title": "GET /metrics endpoint is unauthenticated",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_p0_security",
                "acceptance_criteria": ["Require admin or internal token on /metrics"],
            },
        }
    )
    assert "scout" not in result["chain"]
    assert set(result["chain"]) == {"implement"}


@pytest.mark.asyncio
async def test_dispatch_skips_scout_for_code_audit_ticket_block():
    from multica_ticket_contract import parse_ticket_block

    description = (
        "```zoe-ticket\n"
        '{"zoe_kind":"bug","source":"code_audit_p0_security",'
        '"acceptance_criteria":["Add security headers"]}'
        "\n```"
    )
    assert parse_ticket_block(description)["source"] == "code_audit_p0_security"
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-code-audit-block",
            "identifier": "ZOE-5355",
            "title": "nginx.conf missing HTTP security headers",
            "description": description,
        }
    )
    assert "scout" not in result["chain"]
    assert set(result["chain"]) == {"implement"}


@pytest.mark.asyncio
async def test_dispatch_keeps_scout_for_code_audit_without_acceptance_criteria():
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-code-audit-no-criteria",
            "identifier": "ZOE-5356",
            "title": "CORS misconfiguration",
            "metadata": {"zoe_kind": "bug", "source": "code_audit_p0_security"},
        }
    )
    assert set(result["chain"]) == {"scout"}


@pytest.mark.asyncio
async def test_dispatch_keeps_scout_for_non_bug_code_audit_ticket():
    a = _FakeAdapter()
    result = await a.dispatch(
        {
            "id": "uuid-code-audit-feature",
            "identifier": "ZOE-5366",
            "title": "Automated issue prioritization workflow",
            "metadata": {
                "zoe_kind": "feature",
                "source": "code_audit_meta",
                "acceptance_criteria": ["Design the prioritization workflow"],
            },
        }
    )
    assert set(result["chain"]) == {"scout"}


@pytest.mark.asyncio
async def test_dispatch_archives_stale_terminal_revision_phase_row():
    from pipeline_evidence import PipelineState
    from pipeline_store import load_latest_state, save_state

    save_state(
        PipelineState(
            task_ref="multica:uuid-revision-terminal",
            phase="implement",
            status="todo",
            evidence_profile="code",
        ),
        event="verification_failed",
    )
    rows = [
        _row("implement", "done", chain_version="v4", issue_id="uuid-revision-terminal"),
        _row("verify", "blocked", chain_version="v4", issue_id="uuid-revision-terminal"),
    ]
    a = _FakeAdapter(list_rows=rows)

    result = await a.dispatch(
        {
            "id": "uuid-revision-terminal",
            "identifier": "ZOE-5354",
            "title": "Fix metrics auth",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_p0_security",
                "acceptance_criteria": ["Require internal token"],
            },
        }
    )

    assert result["ok"] is True
    assert result["phase"] == "implement"
    assert result["created"] == ["implement"]
    assert ["archive", "t_implement"] in a.calls
    latest = load_latest_state("multica:uuid-revision-terminal")
    assert latest.phase == "implement"
    assert latest.status == "running"


@pytest.mark.asyncio
async def test_dispatch_adjusts_stale_scout_journal_for_scope_split_child():
    from pipeline_evidence import PipelineState
    from pipeline_store import load_latest_state, save_state

    save_state(
        PipelineState(
            task_ref="multica:uuid-child-stale-scout",
            phase="scout",
            status="todo",
            evidence_profile="code",
        ),
        event="bootstrap",
    )
    rows = [
        _row(
            "scout",
            "archived",
            chain_version="v4",
            issue_id="uuid-child-stale-scout",
        )
    ]
    a = _FakeAdapter(list_rows=rows)

    result = await a.dispatch(
        {
            "id": "uuid-child-stale-scout",
            "identifier": "ZOE-5439",
            "title": "calendar child",
            "description": """```zoe-ticket
{"zoe_kind":"child","source":"scope_split","acceptance_criteria":["calendar builder"]}
```""",
        }
    )

    assert result["ok"] is True
    assert result["phase"] == "implement"
    assert set(result["chain"]) == {"implement"}
    latest = load_latest_state("multica:uuid-child-stale-scout")
    assert latest.phase == "implement"
    assert latest.status == "running"


@pytest.mark.asyncio
async def test_dispatch_adjusts_running_scout_journal_when_scout_row_is_archived():
    from pipeline_evidence import PipelineState
    from pipeline_store import load_latest_state, save_state

    save_state(
        PipelineState(
            task_ref="multica:uuid-child-running-archived-scout",
            phase="scout",
            status="running",
            evidence_profile="code",
            attempts={"scout": 1},
        ),
        event="effect_requested",
    )
    rows = [
        _row(
            "scout",
            "archived",
            chain_version="v4",
            issue_id="uuid-child-running-archived-scout",
        )
    ]
    a = _FakeAdapter(list_rows=rows)

    result = await a.dispatch(
        {
            "id": "uuid-child-running-archived-scout",
            "identifier": "ZOE-5439",
            "title": "calendar child",
            "description": """```zoe-ticket
{"zoe_kind":"child","source":"scope_split","acceptance_criteria":["calendar builder"]}
```""",
        }
    )

    assert result["ok"] is True
    assert result["phase"] == "implement"
    assert set(result["chain"]) == {"implement"}
    latest = load_latest_state("multica:uuid-child-running-archived-scout")
    assert latest.phase == "implement"
    assert latest.status == "running"


@pytest.mark.asyncio
async def test_dispatch_adjusts_running_scout_journal_when_scout_row_is_done():
    from pipeline_evidence import PipelineState
    from pipeline_store import load_latest_state, save_state

    save_state(
        PipelineState(
            task_ref="multica:uuid-child-running-done-scout",
            phase="scout",
            status="running",
            evidence_profile="code",
            attempts={"scout": 1},
        ),
        event="effect_requested",
    )
    rows = [
        _row(
            "scout",
            "done",
            chain_version="v4",
            issue_id="uuid-child-running-done-scout",
        )
    ]
    a = _FakeAdapter(list_rows=rows)

    result = await a.dispatch(
        {
            "id": "uuid-child-running-done-scout",
            "identifier": "ZOE-5439",
            "title": "calendar child",
            "description": """```zoe-ticket
{"zoe_kind":"child","source":"scope_split","acceptance_criteria":["calendar builder"]}
```""",
        }
    )

    assert result["ok"] is True
    assert result["phase"] == "implement"
    assert set(result["chain"]) == {"implement"}
    latest = load_latest_state("multica:uuid-child-running-done-scout")
    assert latest.phase == "implement"
    assert latest.status == "running"


@pytest.mark.asyncio
async def test_dispatch_does_not_adjust_running_scout_journal_with_active_row():
    from pipeline_evidence import PipelineState
    from pipeline_store import load_latest_state, save_state

    save_state(
        PipelineState(
            task_ref="multica:uuid-child-running-active-scout",
            phase="scout",
            status="running",
            evidence_profile="code",
            attempts={"scout": 1},
        ),
        event="effect_requested",
    )
    rows = [
        _row(
            "scout",
            "running",
            chain_version="v4",
            issue_id="uuid-child-running-active-scout",
        )
    ]
    a = _FakeAdapter(list_rows=rows)

    result = await a.dispatch(
        {
            "id": "uuid-child-running-active-scout",
            "identifier": "ZOE-5439",
            "title": "calendar child",
            "description": """```zoe-ticket
{"zoe_kind":"child","source":"scope_split","acceptance_criteria":["calendar builder"]}
```""",
        }
    )

    assert result["ok"] is False
    assert result["reason"] == "phase scout is not in this issue plan"
    assert [call for call in a.calls if call[0] == "create"] == []
    latest = load_latest_state("multica:uuid-child-running-active-scout")
    assert latest.phase == "scout"
    assert latest.status == "running"


@pytest.mark.asyncio
async def test_dispatch_adjusts_running_scout_journal_when_scout_row_is_missing():
    from pipeline_evidence import PipelineState
    from pipeline_store import load_latest_state, save_state

    save_state(
        PipelineState(
            task_ref="multica:uuid-child-running-missing-scout",
            phase="scout",
            status="running",
            evidence_profile="code",
            attempts={"scout": 1},
        ),
        event="effect_requested",
    )
    a = _FakeAdapter(list_rows=[])

    result = await a.dispatch(
        {
            "id": "uuid-child-running-missing-scout",
            "identifier": "ZOE-5439",
            "title": "calendar child",
            "description": """```zoe-ticket
{"zoe_kind":"child","source":"scope_split","acceptance_criteria":["calendar builder"]}
```""",
        }
    )

    assert result["ok"] is True
    assert result["phase"] == "implement"
    assert set(result["chain"]) == {"implement"}
    latest = load_latest_state("multica:uuid-child-running-missing-scout")
    assert latest.phase == "implement"
    assert latest.status == "running"


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
async def test_implement_body_requires_shipping_a_pr_not_blocking_for_review():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )
    # The worker must ship a PR on completion, not present a finished diff and
    # block "for review" (observed failure mode with conservative models).
    assert "COMPLETION MEANS YOU SHIP A PR" in body
    assert "block for review" in body
    assert "PROTOCOL VIOLATION" in body


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
async def test_verify_body_uses_pr_url_fast_path_before_generic_work():
    body = ka.KanbanAdapter()._build_body(
        "verify",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Fix thing", "description": ""},
        "ZOE-9",
    )
    assert "PR_URL FAST PATH" in body
    assert "do not hunt branches or commits" in body
    assert "gh pr view <url>" in body
    assert "PR_REVIEW_REQUIRED" in body
    assert body.index("PR_URL FAST PATH") < body.index("Start with `kanban_show`")


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
    assert "codebase-memory" in scout_skills


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
async def test_poll_fails_closed_on_malformed_kanban_list():
    class _BadListAdapter(_FakeAdapter):
        async def _run(self, args, *, expect_json=False):
            self.calls.append(args)
            if args[0] == "list":
                return None
            return await super()._run(args, expect_json=expect_json)

    a = _BadListAdapter()
    with pytest.raises(ka.KanbanCLIError, match="malformed JSON"):
        await a.poll("multica:nope")


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
async def test_poll_recovers_pr_after_push_then_api_interruption(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_implement.log").write_text(
        "Query: work kanban task t_implement\n"
        "  ┊ 💻 $         git add services/zoe-ui/nginx.conf && git commit -m security && git push -u origin HEAD  2.1s\n"
        "⚡ Interrupted during API call.\n",
        encoding="utf-8",
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    rows = [
        _row(
            "implement",
            "blocked",
            chain_version="v4",
            workspace_path=str(worktree),
            block_reason="worker interrupted after push",
        )
    ]

    class _RecoverAdapter(_FakeAdapter):
        def __init__(self):
            super().__init__(
                list_rows=rows,
                show_map={
                    "t_implement": {
                        "task": {"workspace_path": str(worktree)},
                        "latest_summary": "",
                        "comments": [],
                    }
                },
            )
            self.worktree_commands = []

        async def _run_worktree_command(self, args, *, cwd, timeout=45.0):
            self.worktree_commands.append(args)
            if args[:3] == ["git", "branch", "--show-current"]:
                return "wt/t_implement"
            if args[:3] == ["gh", "pr", "view"]:
                raise ka.KanbanCLIError("no pull request found")
            if args[:3] == ["gh", "pr", "create"]:
                return "https://github.com/jason-easyazz/zoe-ai-assistant/pull/999"
            raise AssertionError(args)

    a = _RecoverAdapter()
    out = await a.poll(
        "multica:uuid-9",
        issue={
            "id": "uuid-9",
            "identifier": "ZOE-999",
            "title": "Recover pushed branch",
            "metadata": {"evidence_profile": "code"},
        },
    )

    assert out["phases"]["implement"] == "done"
    assert out["pr_url"] == "https://github.com/jason-easyazz/zoe-ai-assistant/pull/999"
    assert out["status"] != "blocked"
    complete = next(call for call in a.calls if call[0] == "complete")
    assert "PR_URL=https://github.com/jason-easyazz/zoe-ai-assistant/pull/999" in "\n".join(complete)
    create_cmd = next(cmd for cmd in a.worktree_commands if cmd[:3] == ["gh", "pr", "create"])
    # No --base is passed: gh defaults to the repo's default branch. Deriving the
    # base from HEAD@{upstream} is wrong after `git push -u` (upstream == the task
    # branch itself), which would make head == base and fail.
    assert "--base" not in create_cmd


@pytest.mark.asyncio
async def test_poll_completes_recovered_pr_url_already_in_comments(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_implement.log").write_text(
        "Query: work kanban task t_implement\n"
        "  ┊ 💻 $         git push -u origin HEAD  2.1s\n"
        "⚡ Interrupted during API call.\n",
        encoding="utf-8",
    )
    rows = [
        _row(
            "implement",
            "blocked",
            chain_version="v4",
            block_reason="worker interrupted after push",
        )
    ]

    class _RecoverAdapter(_FakeAdapter):
        def __init__(self):
            super().__init__(
                list_rows=rows,
                show_map={
                    "t_implement": {
                        "latest_summary": "",
                        "comments": [
                            {
                                "body": "manual recovery PR: https://github.com/jason-easyazz/zoe-ai-assistant/pull/998"
                            }
                        ],
                    }
                },
            )

        async def _run_worktree_command(self, args, *, cwd, timeout=45.0):
            raise AssertionError(f"early PR URL recovery should not inspect worktree: {args}")

    a = _RecoverAdapter()
    out = await a.poll("multica:uuid-9")

    assert out["phases"]["implement"] == "done"
    assert out["pr_url"] == "https://github.com/jason-easyazz/zoe-ai-assistant/pull/998"
    assert out["status"] != "blocked"
    complete = next(call for call in a.calls if call[0] == "complete")
    assert "PR_URL=https://github.com/jason-easyazz/zoe-ai-assistant/pull/998" in "\n".join(complete)


@pytest.mark.asyncio
async def test_poll_does_not_crash_when_existing_url_recovery_complete_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_implement.log").write_text(
        "Query: work kanban task t_implement\n"
        "  ┊ 💻 $         git push -u origin HEAD  2.1s\n"
        "⚡ Interrupted during API call.\n",
        encoding="utf-8",
    )
    rows = [
        _row(
            "implement",
            "blocked",
            chain_version="v4",
            block_reason="worker interrupted after push",
        )
    ]

    class _FailCompleteAdapter(_FakeAdapter):
        def __init__(self):
            super().__init__(
                list_rows=rows,
                show_map={
                    "t_implement": {
                        "latest_summary": "",
                        "comments": [
                            {
                                "body": "manual recovery PR: https://github.com/jason-easyazz/zoe-ai-assistant/pull/998"
                            }
                        ],
                    }
                },
            )

        async def _run(self, args, *, expect_json=False):
            if args[0] == "complete":
                raise ka.KanbanCLIError("transient kanban complete failure")
            return await super()._run(args, expect_json=expect_json)

        async def _run_worktree_command(self, args, *, cwd, timeout=45.0):
            raise AssertionError(f"existing PR URL recovery should not inspect worktree: {args}")

    a = _FailCompleteAdapter()
    out = await a.poll("multica:uuid-9")

    assert out["found"] is True
    assert out["status"] == "blocked"
    assert out["phases"]["implement"] == "blocked"


def test_pushed_branch_recovery_scans_latest_log_session_only(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         git push -u origin HEAD  2.1s\n"
        "⚡ Interrupted during API call.\n"
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         python3 -m pytest services/zoe-data/tests/test_x.py  0.4s [exit 1]\n"
        "  ┊ kanban_block BLOCKER=TEST_FAILURE\n",
        encoding="utf-8",
    )

    assert ka.KanbanAdapter()._pushed_branch_without_pr_handoff("t_impl") is False


def test_pushed_branch_recovery_ignores_pr_create_in_reasoning_text(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "I will run `gh pr create` after this push.\n"
        "  ┊ 💻 $         git push -u origin HEAD  2.1s\n"
        "⚡ Interrupted during API call.\n",
        encoding="utf-8",
    )

    assert ka.KanbanAdapter()._pushed_branch_without_pr_handoff("t_impl") is True


def test_pushed_branch_recovery_allows_failed_pr_create_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         git push -u origin HEAD  2.1s\n"
        "  ┊ 💻 $         gh pr create --base main --title x --body y  1.1s [exit 1]\n"
        "⚡ Interrupted during API call.\n",
        encoding="utf-8",
    )

    assert ka.KanbanAdapter()._pushed_branch_without_pr_handoff("t_impl") is True


def test_pushed_branch_recovery_allows_successful_pr_create_without_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         git push -u origin HEAD  2.1s\n"
        "  ┊ 💻 $         gh pr create --base main --title x --body y  1.1s\n"
        "https://github.com/jason-easyazz/zoe-ai-assistant/pull/998\n"
        "⚡ Interrupted during API call.\n",
        encoding="utf-8",
    )

    assert ka.KanbanAdapter()._pushed_branch_without_pr_handoff("t_impl") is True


def test_pushed_branch_recovery_scans_past_failed_push_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         git push -u origin HEAD  2.1s [exit 1]\n"
        "  ┊ 💻 $         git push -u origin HEAD  2.4s\n"
        "⚡ Interrupted during API call.\n",
        encoding="utf-8",
    )

    assert ka.KanbanAdapter()._pushed_branch_without_pr_handoff("t_impl") is True


def test_pushed_branch_recovery_allows_compound_push_then_failed_pr_create(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         git push -u origin HEAD && gh pr create --base main --title x --body y  3.1s [exit 1]\n"
        "⚡ Interrupted during API call.\n",
        encoding="utf-8",
    )

    assert ka.KanbanAdapter()._pushed_branch_without_pr_handoff("t_impl") is True


@pytest.mark.asyncio
async def test_poll_creates_pr_when_pr_view_returns_no_url(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_implement.log").write_text(
        "Query: work kanban task t_implement\n"
        "  ┊ 💻 $         git push -u origin HEAD  2.1s\n"
        "⚡ Interrupted during API call.\n",
        encoding="utf-8",
    )
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    rows = [
        _row(
            "implement",
            "blocked",
            chain_version="v4",
            workspace_path=str(worktree),
            block_reason="worker interrupted after push",
        )
    ]

    class _RecoverAdapter(_FakeAdapter):
        def __init__(self):
            super().__init__(
                list_rows=rows,
                show_map={
                    "t_implement": {
                        "task": {"workspace_path": str(worktree)},
                        "latest_summary": "",
                        "comments": [],
                    }
                },
            )
            self.worktree_commands = []

        async def _run_worktree_command(self, args, *, cwd, timeout=45.0):
            self.worktree_commands.append(args)
            if args[:3] == ["git", "branch", "--show-current"]:
                return "wt/t_implement"
            if args[:3] == ["gh", "pr", "view"]:
                return "null"
            if args[:3] == ["gh", "pr", "create"]:
                return "https://github.com/jason-easyazz/zoe-ai-assistant/pull/997"
            raise AssertionError(args)

    a = _RecoverAdapter()
    out = await a.poll("multica:uuid-9")

    assert out["phases"]["implement"] == "done"
    assert out["pr_url"] == "https://github.com/jason-easyazz/zoe-ai-assistant/pull/997"
    assert any(cmd[:3] == ["gh", "pr", "create"] for cmd in a.worktree_commands)


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
async def test_poll_v4_keeps_audit_closeout_done_for_sync():
    from pipeline_evidence import PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-audit-closeout",
        phase="closeout",
        status="todo",
        evidence_profile="audit",
        attempts={"closeout": 1},
    )
    save_state(state, event="operator_resumed")
    rows = [
        _row(
            "closeout",
            "done",
            chain_version="v4",
            issue_id="uuid-audit-closeout",
            created_at=200.0,
        ),
    ]
    show = {
        "t_closeout": {
            "latest_summary": None,
            "comments": [],
            "runs": [{"summary": "Completed the audit closeout using recorded pipeline evidence."}],
        }
    }
    issue = {
        "id": "uuid-audit-closeout",
        "identifier": "ZOE-AUDIT",
        "title": "Audit closeout",
        "description": "",
    }

    out = await _FakeAdapter(list_rows=rows, show_map=show).poll(
        "multica:uuid-audit-closeout",
        issue=issue,
    )

    assert out["pipeline"]["phase"] == "retro"
    assert out["pipeline"]["status"] == "todo"
    assert "stale_executor_phase" not in out["pipeline"]


@pytest.mark.asyncio
async def test_poll_v4_done_phase_with_ready_next_phase_is_partial():
    rows = [_row("scout", "done", chain_version="v4")]
    show = {"t_scout": {"latest_summary": "TOOLS_USED=codebase-memory\nSCOUT_SUMMARY=small", "comments": []}}
    a = _FakeAdapter(list_rows=rows, show_map=show)
    out = await a.poll("multica:uuid-9")
    assert out["found"] is True
    assert out["status"] == "partial"
    assert out["pipeline"]["phase"] == "implement"
    assert out["pipeline"]["status"] == "todo"


@pytest.mark.asyncio
async def test_poll_v4_blocks_code_implement_done_without_pr():
    rows = [_row("implement", "done", chain_version="v4", issue_id="uuid-no-pr")]
    show = {
        "t_implement": {
            "latest_summary": "TOOLS_USED=codebase-memory\nTESTS=validate_structure.py passed\nSUMMARY=investigated only",
            "comments": [],
        }
    }
    a = _FakeAdapter(list_rows=rows, show_map=show)
    out = await a.poll(
        "multica:uuid-no-pr",
        issue={
            "id": "uuid-no-pr",
            "identifier": "ZOE-NO-PR",
            "title": "Harness fix must produce a PR",
            "metadata": {"evidence_profile": "code"},
        },
    )

    assert out["found"] is True
    assert out["status"] == "blocked"
    assert out["pipeline"]["phase"] == "implement"
    assert out["pipeline"]["status"] == "blocked"
    assert out["pipeline"]["missing_evidence"] == ["pr"]
    assert out["blocker"] == "GATE_BLOCKED: missing required evidence pr"


@pytest.mark.asyncio
async def test_poll_ignores_stale_terminal_row_for_revision_phase():
    from pipeline_evidence import PipelineState
    from pipeline_store import save_state

    save_state(
        PipelineState(
            task_ref="multica:uuid-revision-poll",
            phase="implement",
            status="todo",
            evidence_profile="code",
        ),
        event="verification_failed",
    )
    rows = [
        _row("implement", "done", chain_version="v4", issue_id="uuid-revision-poll"),
        _row("verify", "blocked", chain_version="v4", issue_id="uuid-revision-poll"),
    ]
    a = _FakeAdapter(list_rows=rows)

    out = await a.poll(
        "multica:uuid-revision-poll",
        issue={
            "id": "uuid-revision-poll",
            "identifier": "ZOE-5354",
            "title": "Fix metrics auth",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_p0_security",
                "acceptance_criteria": ["Require internal token"],
            },
        },
    )

    assert out["status"] == "partial"
    assert out["pipeline"]["phase"] == "implement"
    assert out["pipeline"]["status"] == "todo"
    assert out["pipeline"]["stale_executor_phase"] == "implement"
    assert out["pipeline"]["stale_executor_status"] == "done"
    assert out["blocker"] is None


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
async def test_poll_v4_resumed_todo_ignores_stale_blocked_current_phase():
    from pipeline_evidence import PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-resume-current",
        phase="implement",
        status="todo",
        attempts={"implement": 1},
    )
    save_state(state, event="operator_resumed")
    rows = [_row("implement", "blocked", chain_version="v4", issue_id="uuid-resume-current")]
    issue = {
        "id": "uuid-resume-current",
        "identifier": "ZOE-RESUME",
        "title": "Harness: retry current phase",
        "metadata": {"zoe_kind": "harness_fix", "acceptance_criteria": ["retry"]},
    }
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-resume-current", issue=issue)

    assert out["status"] == "partial"
    assert out["blocker"] is None
    assert out["phases"] == {}
    assert out["pipeline"]["phase"] == "implement"
    assert out["pipeline"]["status"] == "todo"
    assert out["pipeline"]["stale_executor_phase"] == "implement"
    assert out["pipeline"]["stale_executor_status"] == "blocked"



@pytest.mark.asyncio
async def test_poll_v4_keeps_already_covered_implement_for_sync():
    from pipeline_evidence import PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-already-covered",
        phase="implement",
        status="todo",
        attempts={"implement": 1},
    )
    save_state(state, event="operator_resumed")
    rows = [
        _row(
            "implement",
            "blocked",
            chain_version="v4",
            issue_id="uuid-already-covered",
            block_reason=None,
            created_at=200.0,
        ),
        _row(
            "verify",
            "blocked",
            chain_version="v4",
            issue_id="uuid-already-covered",
            created_at=100.0,
        ),
    ]
    show = {
        "t_implement": {
            "latest_summary": "BLOCKER=ALREADY_COVERED: focused tests passed; no PR required",
            "comments": [],
        },
        "t_verify": {
            "latest_summary": "BLOCKER=WORKTREE_PATH_VIOLATION: stale verify",
            "comments": [],
        },
    }
    issue = {
        "id": "uuid-already-covered",
        "identifier": "ZOE-COVERED",
        "title": "Intent gap: already covered",
        "description": "",
    }

    out = await _FakeAdapter(list_rows=rows, show_map=show).poll(
        "multica:uuid-already-covered",
        issue=issue,
    )

    assert out["status"] == "running"
    assert out["pipeline"]["phase"] == "verify"
    assert out["pipeline"]["status"] == "todo"
    assert "stale_executor_phase" not in out["pipeline"]


@pytest.mark.asyncio
async def test_poll_v4_resumed_todo_ignores_stale_blocked_current_phase_with_prior_done():
    from pipeline_evidence import PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-resume-after-scout",
        phase="implement",
        status="todo",
        attempts={"scout": 1, "implement": 1},
    )
    save_state(state, event="operator_resumed")
    rows = [
        _row("scout", "done", chain_version="v4", issue_id="uuid-resume-after-scout"),
        _row("implement", "blocked", chain_version="v4", issue_id="uuid-resume-after-scout"),
    ]
    issue = {
        "id": "uuid-resume-after-scout",
        "identifier": "ZOE-RESUME",
        "title": "Code ticket after scout",
    }
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-resume-after-scout", issue=issue)

    assert out["status"] == "partial"
    assert out["blocker"] is None
    assert out["phases"] == {"scout": "done"}
    assert out["pipeline"]["phase"] == "implement"
    assert out["pipeline"]["status"] == "todo"
    assert out["pipeline"]["stale_executor_phase"] == "implement"
    assert out["pipeline"]["stale_executor_status"] == "blocked"



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
async def test_poll_v4_resumed_skip_scout_ignores_stale_scout_blocker():
    from pipeline_evidence import PipelineState
    from pipeline_store import save_state

    state = PipelineState(
        task_ref="multica:uuid-9",
        phase="scout",
        status="todo",
        evidence_profile="code",
        attempts={"scout": 1},
    )
    save_state(state, event="operator_resumed")
    rows = [_row("scout", "blocked", chain_version="v4", block_reason="SCOUT_BUDGET")]
    issue = {
        "id": "uuid-9",
        "identifier": "ZOE-5446",
        "title": "Harness follow-up",
        "metadata": {
            "zoe_kind": "harness_fix",
            "acceptance_criteria": ["budget blockers create one follow-up ticket"],
        },
    }
    a = _FakeAdapter(list_rows=rows)
    out = await a.poll("multica:uuid-9", issue=issue)

    assert out["status"] == "partial"
    assert out["blocker"] is None
    assert out["pipeline"]["phase"] == "scout"
    assert out["pipeline"]["status"] == "todo"
    assert out["pipeline"]["stale_executor_phase"] == "scout"


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
async def test_poll_v4_prefers_journal_block_reason_over_kanban_row():
    rows = [
        _row(
            "implement",
            "blocked",
            chain_version="v4",
            block_reason="pipeline blocked at implement",
        )
    ]
    show = {
        "t_implement": {
            "latest_summary": (
                "BLOCKER=IMPLEMENT_BUDGET: code-enforced tool budget exceeded "
                "(steps=17, guidance_limit=14, hard_limit=16)"
            ),
            "comments": [],
        }
    }
    a = _FakeAdapter(list_rows=rows, show_map=show)
    out = await a.poll("multica:uuid-9")

    assert out["status"] == "blocked"
    assert out["blocker"] == (
        "IMPLEMENT_BUDGET: code-enforced tool budget exceeded "
        "(steps=17, guidance_limit=14, hard_limit=16)"
    )
    assert out["pipeline"]["block_reason"] == out["blocker"]


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


def test_phase_budget_reason_recovers_hermes_iteration_budget_log(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 📖 read      services/zoe-data/main.py  0.1s\n"
        "⚠ Iteration budget reached (22/22) — response may be incomplete\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason_from_log("t_impl", "implement")

    assert reason == (
        "BLOCKER=ITERATION_BUDGET: Hermes iteration budget reached during implement "
        "(steps=22, limit=22)"
    )


def test_implement_edit_safety_allows_bounded_patched_file_read_before_check(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/intent_router.py  5.9s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n"
        "  ┊ 💻 $         python3 -m py_compile services/zoe-data/intent_router.py  0.2s\n",
        encoding="utf-8",
    )

    assert kb.implement_edit_safety_reason_from_log("t_impl", "implement") is None


def test_implement_edit_safety_blocks_excess_patched_file_reads_before_check(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/intent_router.py  5.9s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_edit_safety_reason_from_log("t_impl", "implement")

    assert reason == (
        "BLOCKER=IMPLEMENT_EDIT_SAFETY: Python patch was followed by more "
        "exploration before py_compile/focused tests"
    )


def test_implement_edit_safety_blocks_unrelated_file_read_before_check(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/intent_router.py  5.9s\n"
        "  ┊ 📖 read      /work/services/zoe-data/main.py  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_edit_safety_reason_from_log("t_impl", "implement")

    assert reason == (
        "BLOCKER=IMPLEMENT_EDIT_SAFETY: Python patch was followed by more "
        "exploration before py_compile/focused tests"
    )


def test_implement_edit_safety_repatch_resets_patched_file_read_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/intent_router.py  5.9s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/intent_router.py  0.5s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n",
        encoding="utf-8",
    )

    assert kb.implement_edit_safety_reason_from_log("t_impl", "implement") is None


def test_implement_edit_safety_allows_editing_another_file_after_python_patch(tmp_path, monkeypatch):
    # Multi-file tasks legitimately patch a .py then a config file before running
    # checks; editing another allowlisted file is productive work, not the
    # patch-then-keep-exploring failure this guard targets.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/tests/test_pipeline_evidence.py  5.9s\n"
        "  ┊ 🔧 patch     /work/.github/workflows/validate.yml  1.2s\n"
        "  ┊ 💻 $         python3 -m pytest services/zoe-data/tests/test_pipeline_evidence.py  0.2s\n",
        encoding="utf-8",
    )

    assert kb.implement_edit_safety_reason_from_log("t_impl", "implement") is None


def test_implement_edit_safety_still_blocks_read_exploration_after_multifile_edits(tmp_path, monkeypatch):
    # The relaxation only covers edits — a read/grep of an unrelated file after a
    # Python patch (with no syntax check yet) is still the failure mode we block.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/tests/test_pipeline_evidence.py  5.9s\n"
        "  ┊ 🔧 patch     /work/.github/workflows/validate.yml  1.2s\n"
        "  ┊ 📖 read      /work/services/zoe-data/main.py  0.1s\n",
        encoding="utf-8",
    )

    assert kb.implement_edit_safety_reason_from_log("t_impl", "implement") == (
        "BLOCKER=IMPLEMENT_EDIT_SAFETY: Python patch was followed by more "
        "exploration before py_compile/focused tests"
    )


def test_implement_edit_safety_still_blocks_grep_exploration_after_multifile_edits(tmp_path, monkeypatch):
    # The relaxation covers edits only; a grep (like read/find) after a Python
    # patch with no syntax check yet is still the exploration we block.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/tests/test_pipeline_evidence.py  5.9s\n"
        "  ┊ 🔧 patch     /work/.github/workflows/validate.yml  1.2s\n"
        "  ┊ 🔎 grep      required_evidence  0.1s\n",
        encoding="utf-8",
    )

    assert kb.implement_edit_safety_reason_from_log("t_impl", "implement") == (
        "BLOCKER=IMPLEMENT_EDIT_SAFETY: Python patch was followed by more "
        "exploration before py_compile/focused tests"
    )


def test_implement_edit_safety_allows_live_patch_review_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/multica_ticket_contract.py  1.6s\n"
        "  ┊ review diff\n"
        "a//work/services/zoe-data/multica_ticket_contract.py → "
        "b//work/services/zoe-data/multica_ticket_contract.py\n"
        "@@ -31,6 +31,7 @@\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/multica_ticket_contract.py  0.5s\n"
        "  ┊ review diff\n"
        "a//work/services/zoe-data/multica_ticket_contract.py → "
        "b//work/services/zoe-data/multica_ticket_contract.py\n"
        "@@ -119,6 +119,7 @@\n"
        "  ┊ 📖 read      /work/services/zoe-data/multica_ticket_contract.py  0.1s\n",
        encoding="utf-8",
    )

    assert kb.implement_edit_safety_reason_from_log("t_impl", "implement") is None


def test_implement_edit_safety_allows_immediate_python_check(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/intent_router.py  5.9s\n"
        "  ┊ 💻 $         python3 -m py_compile services/zoe-data/intent_router.py  0.2s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n",
        encoding="utf-8",
    )

    assert kb.implement_edit_safety_reason_from_log("t_impl", "implement") is None


def test_implement_edit_safety_ignores_patch_review_diff_before_check(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/intent_router.py  5.9s\n"
        "  ┊ review diff\n"
        "a//work/services/zoe-data/intent_router.py → b//work/services/zoe-data/intent_router.py\n"
        "@@ -410,7 +410,8 @@\n"
        "-    r\"can you explain|set up (?:a )?new automation|what is happening in)\",\n"
        "+    r\"can you explain|set up (?:a )?new automation|what is happening in|\"\n"
        "+    r\"tell me (?:a|another) joke)\",\n"
        "  ┊ 💻 $         python3 -m py_compile services/zoe-data/intent_router.py  0.2s\n",
        encoding="utf-8",
    )

    assert kb.implement_edit_safety_reason_from_log("t_impl", "implement") is None


def test_implement_edit_safety_blocks_explore_after_patch_review_diff(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/intent_router.py  5.9s\n"
        "  ┊ review diff\n"
        "a//work/services/zoe-data/intent_router.py → b//work/services/zoe-data/intent_router.py\n"
        "@@ -410,7 +410,8 @@\n"
        "  ┊ 🔎 grep      def execute_intent  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_edit_safety_reason_from_log("t_impl", "implement")

    assert reason is not None
    assert "IMPLEMENT_EDIT_SAFETY" in reason


def test_implement_edit_safety_ignores_non_step_patch_text(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "Planning note: patch services/zoe-data/intent_router.py after locating the helper.\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n",
        encoding="utf-8",
    )

    assert kb.implement_edit_safety_reason_from_log("t_impl", "implement") is None


def test_implement_edit_safety_ignores_patch_word_in_step_arguments(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔎 grep      patch utils.py  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n",
        encoding="utf-8",
    )

    assert kb.implement_edit_safety_reason_from_log("t_impl", "implement") is None


def test_implement_edit_safety_does_not_clear_on_check_word_in_step_arguments(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/intent_router.py  5.9s\n"
        "  ┊ 🔎 grep      validate_structure /work/services/zoe-data/main.py  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_edit_safety_reason_from_log("t_impl", "implement")

    assert reason is not None
    assert "IMPLEMENT_EDIT_SAFETY" in reason


def test_implement_edit_safety_covers_revision_phase(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_revision.log").write_text(
        "Query: work kanban task t_revision\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/intent_router.py  5.9s\n"
        "  ┊ 🔎 grep      def execute_intent  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_edit_safety_reason_from_log("t_revision", "implement_revision")

    assert reason is not None
    assert "IMPLEMENT_EDIT_SAFETY" in reason


def test_implement_pre_edit_drift_blocks_repeated_reads_without_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_REPEAT_READ_BUDGET", "6")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    repeated_reads = "\n".join(
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s"
        for _ in range(7)
    )
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ ⚡ kanban_sh   0.0s\n"
        "  ┊ 💻 $         cd /work && git status  0.1s\n"
        f"{repeated_reads}\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log("t_impl", "implement")

    assert reason is not None
    assert "IMPLEMENT_HANDOFF_DRIFT" in reason
    assert "repeated pre-edit reads" in reason


def test_implement_pre_edit_drift_normalizes_read_range_suffixes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_REPEAT_READ_BUDGET", "3")
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "20")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    ranged_reads = "\n".join(
        f"  ┊ 📖 read      /work/services/zoe-data/intent_router.py:{index}-{index + 20}  0.1s"
        for index in [1, 40, 80, 120]
    )
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        f"{ranged_reads}\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log("t_impl", "implement")

    assert reason is not None
    assert "repeated pre-edit reads" in reason
    assert "file=/work/services/zoe-data/intent_router.py" in reason


def test_implement_pre_edit_drift_blocks_exploration_without_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "12")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    explore = "\n".join(
        f"  ┊ 🔎 grep      symbol_{index}  0.1s"
        for index in range(13)
    )
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ ⚡ kanban_sh   0.0s\n"
        f"{explore}\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log("t_impl", "implement")

    assert reason is not None
    assert "pre-edit exploration exceeded budget" in reason


def test_implement_pre_edit_drift_blocks_excess_harness_followup_grep_after_focused_test(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "12")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 📖 read      /work/services/zoe-data/main.py  0.1s\n"
        "  ┊ 💻 $         cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -v "
        "services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup -x  3.3s\n"
        "  ┊ 📖 read      /work/services/zoe-data/executors/kanban_adapter.py  0.1s\n"
        "  ┊ 🔎 grep      ITERATION_BUDGET  0.1s\n"
        "  ┊ 🔎 grep      _ensure_blocker_followup_ticket  0.1s\n"
        "  ┊ 🔎 grep      another_symbol  0.1s\n"
        "  ┊ 🔎 grep      final_symbol  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is not None
    assert "BLOCKER=ALREADY_COVERED" in reason


def test_implement_pre_edit_drift_allows_harness_followup_named_file_read_after_focused_test(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup  3.3s\n"
        "  ┊ 📖 read      /work/services/zoe-data/main.py  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is None


def test_implement_pre_edit_drift_does_not_charge_allowed_named_file_read_to_budget(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "12")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    pre_focus_explore = "\n".join(
        f"  ┊ 🔎 grep      symbol_{index}  0.1s"
        for index in range(12)
    )
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        f"{pre_focus_explore}\n"
        "  ┊ 💻 $         cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup  3.3s\n"
        "  ┊ 📖 read      /work/services/zoe-data/executors/kanban_adapter.py  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is None


def test_implement_pre_edit_drift_allows_three_focused_greps_after_focused_test(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $ cd /home/zoe/.worktrees/t_impl && PYTHONPATH=services/zoe-data "
        "python3 -m pytest -q services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup  3.3s\n"
        "  ┊ 📖 read /home/zoe/.worktrees/t_impl/services/zoe-data/tests/test_main_multica_poll.py  0.1s\n"
        "  ┊ 🔎 grep test_record_blocked_multica_chain_creates_iteration_budget_followup  0.1s\n"
        "  ┊ 📖 read /home/zoe/.worktrees/t_impl/services/zoe-data/tests/test_main_multica_poll.py  0.1s\n"
        "  ┊ 🔎 grep _record_blocked_multica_chain  0.0s\n"
        "  ┊ 📖 read /home/zoe/.worktrees/t_impl/services/zoe-data/main.py  0.1s\n"
        "  ┊ 🔎 grep _ensure_blocker_followup_ticket  0.0s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is None


def test_implement_pre_edit_drift_honors_post_focus_grep_budget_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_POST_FOCUS_GREP_BUDGET", "1")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup  3.3s\n"
        "  ┊ 🔎 grep      _record_blocked_multica_chain  0.1s\n"
        "  ┊ 🔎 grep      _ensure_blocker_followup_ticket  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is not None
    assert "BLOCKER=ALREADY_COVERED" in reason


def test_implement_pre_edit_drift_allows_bounded_named_file_navigation_after_focused_test(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup  3.3s\n"
        "  ┊ 🔎 grep      _record_blocked_multica_chain  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/main.py  0.1s\n"
        "  ┊ 🔎 grep      _ensure_blocker_followup_ticket  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/main.py  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/tests/test_main_multica_poll.py  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is None




def test_implement_pre_edit_drift_allows_repeated_focused_test_reads_after_focused_test(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ ⚡ kanban_sh   0.0s\n"
        "  ┊ 💻 $ cd /home/zoe/.worktrees/t_impl && pwd && git branch --show-current  0.1s\n"
        "  ┊ 💻 $ cd /home/zoe/.worktrees/t_impl && PYTHONPATH=services/zoe-data "
        "python3 -m pytest -q services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup  3.3s\n"
        "  ┊ 📖 read /home/zoe/.worktrees/t_impl/services/zoe-data/tests/test_main_multica_poll.py  0.1s\n"
        "  ┊ 🔎 grep test_record_blocked_multica_chain_creates_iteration_budget_followup  0.1s\n"
        "  ┊ 📖 read /home/zoe/.worktrees/t_impl/services/zoe-data/tests/test_main_multica_poll.py  0.1s\n"
        "  ┊ 📖 read /home/zoe/.worktrees/t_impl/services/zoe-data/tests/test_main_multica_poll.py  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is None



def test_implement_pre_edit_drift_honors_focused_test_read_budget_override(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_POST_FOCUS_FOCUSED_TEST_READ_BUDGET", "1")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $ cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_kanban_adapter.py::"
        "test_implement_body_includes_harness_blocker_followup_focused_tests  0.9s\n"
        "  ┊ 📖 read /work/services/zoe-data/tests/test_kanban_adapter.py  0.1s\n"
        "  ┊ 📖 read /work/services/zoe-data/tests/test_kanban_adapter.py  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is not None
    assert "BLOCKER=ALREADY_COVERED" in reason


def test_implement_pre_edit_drift_blocks_excess_focused_test_reads_after_focused_test(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $ cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_kanban_adapter.py::"
        "test_implement_body_includes_harness_blocker_followup_focused_tests  0.9s\n"
        + "\n".join(
            "  ┊ 📖 read /work/services/zoe-data/tests/test_kanban_adapter.py  0.1s"
            for _ in range(5)
        )
        + "\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is not None
    assert "BLOCKER=ALREADY_COVERED" in reason



def test_implement_pre_edit_drift_honors_post_focus_read_budget_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_POST_FOCUS_READ_BUDGET", "1")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup  3.3s\n"
        "  ┊ 📖 read      /work/services/zoe-data/main.py  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/main.py  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is not None
    assert "BLOCKER=ALREADY_COVERED" in reason


def test_implement_pre_edit_drift_blocks_excess_harness_followup_navigation_after_focused_test(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup  3.3s\n"
        "  ┊ 📖 read      /work/services/zoe-data/main.py  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/main.py  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/main.py  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is not None
    assert "BLOCKER=ALREADY_COVERED" in reason


def test_implement_pre_edit_drift_blocks_excess_harness_followup_focused_test_greps_in_other_files(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_kanban_adapter.py::"
        "test_implement_body_includes_harness_blocker_followup_focused_tests  0.9s\n"
        "  ┊ 🔎 grep      _ensure_blocker_followup_ticket  0.1s\n"
        "  ┊ 🔎 grep      IMPLEMENT_HANDOFF_DRIFT  0.1s\n"
        "  ┊ 🔎 grep      ALREADY_COVERED  0.1s\n"
        "  ┊ 🔎 grep      FINAL_SYMBOL  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is not None
    assert "BLOCKER=ALREADY_COVERED" in reason


def test_implement_pre_edit_drift_maps_post_focus_budget_exhaustion_to_already_covered(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $ cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup  3.3s\n"
        "  ┊ 📖 read /work/services/zoe-data/tests/test_main_multica_poll.py  0.1s\n"
        "  ┊ 🔎 grep test_record_blocked_multica_chain_creates_iteration_budget_followup  0.1s\n"
        "  ┊ 📖 read /work/services/zoe-data/tests/test_main_multica_poll.py  0.1s\n"
        "  ┊ 🔎 grep _record_blocked_multica_chain  0.1s\n"
        "  ┊ 📖 read /work/services/zoe-data/main.py  0.1s\n"
        "  ┊ 🔎 grep _ensure_blocker_followup_ticket  0.1s\n"
        "  ┊ 📖 read /work/services/zoe-data/main.py  0.1s\n"
        "  ┊ 🔎 grep _blocker_followup_marker  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is not None
    assert "BLOCKER=ALREADY_COVERED" in reason


def test_implement_pre_edit_drift_ignores_plain_followup_prompt_without_metadata(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "12")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_kanban_adapter.py::"
        "test_implement_body_includes_harness_blocker_followup_focused_tests  0.9s\n"
        "  ┊ 🔎 grep      _ensure_blocker_followup_ticket  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body="For engineering_blocker_followup tickets, inspect only the named files.",
    )

    assert reason is None


def test_implement_pre_edit_drift_stands_down_for_harness_followup_patch_without_focused_test(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "12")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    pre_patch_explore = "\n".join(
        f"  ┊ 🔎 grep      symbol_{index}  0.1s"
        for index in range(13)
    )
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        f"{pre_patch_explore}\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/executors/kanban_adapter.py  5.9s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is None


def test_implement_pre_edit_drift_stands_down_for_patch_before_focused_test(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "12")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    pre_patch_explore = "\n".join(
        f"  ┊ 🔎 grep      symbol_{index}  0.1s"
        for index in range(13)
    )
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        f"{pre_patch_explore}\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/executors/kanban_adapter.py  5.9s\n"
        "  ┊ 💻 $         cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup  3.3s\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log(
        "t_impl",
        "implement",
        task_body='{"source":"engineering_blocker_followup"}',
    )

    assert reason is None


def test_implement_pre_edit_drift_prioritizes_explore_budget_when_both_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_REPEAT_READ_BUDGET", "3")
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "3")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    repeated_reads = "\n".join(
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s"
        for _ in range(4)
    )
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        f"{repeated_reads}\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log("t_impl", "implement")

    assert reason is not None
    assert "pre-edit exploration exceeded budget" in reason


def test_implement_pre_edit_drift_stands_down_after_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n"
        "  ┊ 🔧 patch     /work/services/zoe-data/intent_router.py  5.9s\n"
        "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n",
        encoding="utf-8",
    )

    assert kb.implement_pre_edit_drift_reason_from_log("t_impl", "implement") is None


def test_implement_pre_edit_drift_stands_down_after_terminal_call(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "12")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    explore = "\n".join(
        f"  ┊ 🔎 grep      symbol_{index}  0.1s"
        for index in range(13)
    )
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        f"{explore}\n"
        "  ┊ ✅ kanban_block  0.1s\n",
        encoding="utf-8",
    )

    assert kb.implement_pre_edit_drift_reason_from_log("t_impl", "implement") is None


def test_implement_pre_edit_drift_covers_revision_phase(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "12")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    explore = "\n".join(
        f"  ┊ 🔎 grep      stale_comment_{index}  0.1s"
        for index in range(13)
    )
    (log_dir / "t_revision.log").write_text(
        "Query: work kanban task t_revision\n"
        f"{explore}\n",
        encoding="utf-8",
    )

    reason = kb.implement_pre_edit_drift_reason_from_log("t_revision", "implement_revision")

    assert reason is not None
    assert "IMPLEMENT_HANDOFF_DRIFT" in reason


def test_phase_budget_reason_blocks_pre_edit_handoff_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "12")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    explore = "\n".join(
        f"  ┊ 🔎 grep      symbol_{index}  0.1s"
        for index in range(13)
    )
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        f"{explore}\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {"task": {"started_at": 100}, "runs": [{"started_at": 100}]},
        now=120,
    )

    assert reason is not None
    assert "IMPLEMENT_HANDOFF_DRIFT" in reason




def test_phase_budget_blocks_intent_gap_pre_edit_churn_from_live_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_INTENT_GAP_REPEAT_READ_BUDGET", "2")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_intent.log").write_text(
        """Query: work kanban task t_intent
  | kanban_sh   0.0s
  | $         pwd && git branch --show-current  0.2s
  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s
  ┊ 🔎 grep      _AGENT_CHAT_RE  /work/services/zoe-data/intent_router.py  0.1s
  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s
  ┊ 🔎 grep      def detect_intent /work/services/zoe-data/intent_router.py  0.1s
  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s
""",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_intent",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "INTENT-GAP IMPLEMENT FAST PATH",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is not None
    assert "intent-gap repeated pre-edit reads" in reason





def test_phase_budget_allows_intent_gap_reads_after_helper_edit(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_INTENT_GAP_REPEAT_READ_BUDGET", "2")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_intent.log").write_text(
        """Query: work kanban task t_intent
  ┊ ⚡ kanban_sh   0.0s
  ┊ 💻 $         pwd && git branch --show-current  0.1s
  ┊ 💻 $         python3 ./scripts/maintenance/zoe_apply_intent_gap_contract.py say_exactly  0.2s
  ┊ 💻 $         python3 -m py_compile services/zoe-data/intent_router.py  0.2s
  ┊ 💻 $         PYTHONPATH=services/zoe-data python3 -m pytest -q services/zoe-data/tests/test_intent_open_domain.py  0.7s
  ┊ 💻 $         git diff HEAD --no-ext-diff -- services/zoe-data/intent_router.py services/zoe-data/tests/test_intent_open_domain.py  0.1s
  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s
  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s
  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s
  ┊ 📖 read      /work/services/zoe-data/tests/test_intent_open_domain.py  0.1s
  ┊ 🔎 grep      _AGENT_CHAT_RE  0.1s
""",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_intent",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": '{"source":"intent_gap:operator_single_lane_test"}',
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is None


def test_phase_budget_blocks_intent_gap_explore_budget_before_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_INTENT_GAP_PRE_EDIT_EXPLORE_BUDGET", "3")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_intent.log").write_text(
        """Query: work kanban task t_intent
  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s
  ┊ 🔎 grep      _AGENT_CHAT_RE /work/services/zoe-data/intent_router.py  0.1s
  ┊ 📖 read      /work/services/zoe-data/tests/test_intent_open_domain.py  0.1s
  ┊ 🔎 grep      list_show /work/services/zoe-data/tests/test_intent_open_domain.py  0.1s
""",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_intent",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "INTENT-GAP IMPLEMENT FAST PATH",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is not None
    assert "intent-gap pre-edit exploration exceeded" in reason


def test_phase_budget_does_not_apply_intent_gap_budget_to_plain_mentions(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_INTENT_GAP_PRE_EDIT_EXPLORE_BUDGET", "1")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        """Query: work kanban task t_impl
  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s
  ┊ 🔎 grep      intent_gap /work/services/zoe-data/intent_router.py  0.1s
""",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "General cleanup mentions intent_gap in prose but is not fast path",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is None


def test_phase_budget_blocks_intent_gap_broad_find_before_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_intent.log").write_text(
        """Query: work kanban task t_intent
  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s
  ┊ 💻 $         find /work -name '*.py' -exec grep -l 'say exactly' {} ;  1.4s
""",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_intent",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": '{"source":"intent_gap:operator_single_lane_test"}',
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is not None
    assert "intent-gap worker ran broad repo search" in reason

def test_phase_budget_reason_passes_harness_followup_body_to_pre_edit_guard(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_PRE_EDIT_EXPLORE_BUDGET", "12")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 💻 $         cd /work && PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup  3.3s\n"
        "  ┊ 📖 read      /work/services/zoe-data/executors/kanban_adapter.py  0.1s\n"
        "  ┊ 🔎 grep      ITERATION_BUDGET  0.1s\n"
        "  ┊ 🔎 grep      _ensure_blocker_followup_ticket  0.1s\n"
        "  ┊ 🔎 grep      another_symbol  0.1s\n"
        "  ┊ 🔎 grep      final_symbol  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": '{"source":"engineering_blocker_followup"}',
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is not None
    assert "BLOCKER=ALREADY_COVERED" in reason


def test_phase_budget_blocks_repeated_ambiguous_patch_attempts(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     services/zoe-ui/nginx.conf  0.0s [Found 2 matches for old_string. Provide more ...]\n"
        "  ┊ 💻 $         sed -n '315,340p' services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 🔧 patch     services/zoe-ui/nginx.conf  0.0s [Found 2 matches for old_string. Provide more ...]\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {"task": {"started_at": 100}, "runs": [{"started_at": 100}]},
        now=120,
    )

    assert reason is not None
    assert "PATCH_AMBIGUITY_DRIFT" in reason


def test_phase_budget_allows_ambiguous_patch_followed_by_successful_patch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     services/zoe-ui/nginx.conf  0.0s [Found 2 matches for old_string. Provide more ...]\n"
        "  ┊ 💻 $         sed -n '315,340p' services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 🔧 patch     services/zoe-ui/nginx.conf  0.2s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {"task": {"started_at": 100}, "runs": [{"started_at": 100}]},
        now=120,
    )

    assert reason is None


def test_phase_budget_keeps_patch_ambiguity_count_per_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     services/zoe-ui/nginx.conf  0.0s [Found 2 matches for old_string. Provide more ...]\n"
        "  ┊ 🔧 patch     services/zoe-data/main.py  0.2s\n"
        "  ┊ 🔧 patch     services/zoe-ui/nginx.conf  0.0s [Found 2 matches for old_string. Provide more ...]\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {"task": {"started_at": 100}, "runs": [{"started_at": 100}]},
        now=120,
    )

    assert reason is not None
    assert "PATCH_AMBIGUITY_DRIFT" in reason


def test_phase_budget_blocks_code_audit_post_patch_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_CODE_AUDIT_POST_PATCH_EXPLORE_BUDGET", "2")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 📖 read      /home/zoe/.worktrees/t_impl/docker-compose.modules.yml  0.1s\n"
        "  ┊ 🔎 grep      validate_structure  0.1s\n"
        "  ┊ 🔎 find      .github  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "CODE-AUDIT FAST PATH\nsource=code_audit_p0_security",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is not None
    assert "CODE_AUDIT_POST_PATCH_DRIFT" in reason
    assert "post_patch_explore_steps=3" in reason


def test_phase_budget_allows_code_audit_validation_after_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 📖 read      /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && python3 tools/audit/validate_structure.py  1.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "CODE-AUDIT FAST PATH\nsource=code_audit_p0_security",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is None


def test_phase_budget_allows_code_audit_ship_command_after_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 📖 read      /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && git push -u origin HEAD  1.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "CODE-AUDIT FAST PATH\nsource=code_audit_p0_security",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is None


def test_phase_budget_blocks_code_audit_git_add_after_validation_without_ship(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && python3 tools/audit/validate_structure.py  0.2s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && git add services/zoe-ui/nginx.conf  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "CODE-AUDIT FAST PATH\nsource=code_audit_p0_security",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is not None
    assert "CODE_AUDIT_STAGED_WITHOUT_SHIP" in reason


def test_phase_budget_allows_git_add_after_failed_code_audit_validation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && python3 tools/audit/validate_structure.py  0.2s [EXIT 1]\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && git add services/zoe-ui/nginx.conf  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "CODE-AUDIT FAST PATH\nsource=code_audit_p0_security",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is None


def test_phase_budget_blocks_git_add_after_failed_ship_then_validation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && git push -u origin HEAD  0.2s [exit 1]\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && python3 tools/audit/validate_structure.py  0.2s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && git add services/zoe-ui/nginx.conf  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "CODE-AUDIT FAST PATH\nsource=code_audit_p0_security",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is not None
    assert "CODE_AUDIT_STAGED_WITHOUT_SHIP" in reason


def test_phase_budget_allows_code_audit_chained_ship_after_validation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && python3 tools/audit/validate_structure.py  0.2s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && git add services/zoe-ui/nginx.conf && git commit -m security-headers && git push -u origin HEAD && gh pr create --fill  2.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "CODE-AUDIT FAST PATH\nsource=code_audit_p0_security",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is None


def test_phase_budget_blocks_wrong_order_code_audit_ship_chain_after_validation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && python3 tools/audit/validate_structure.py  0.2s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && git add services/zoe-ui/nginx.conf && gh pr create --fill && git push -u origin HEAD && git commit -m security-headers  2.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "CODE-AUDIT FAST PATH\nsource=code_audit_p0_security",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is not None
    assert "CODE_AUDIT_STAGED_WITHOUT_SHIP" in reason


def test_phase_budget_does_not_treat_shell_grep_as_validation_after_patch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_CODE_AUDIT_POST_PATCH_EXPLORE_BUDGET", "2")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 📖 read      /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 💻 $         cd /home/zoe/.worktrees/t_impl && grep validate_structure .  0.1s\n"
        "  ┊ 🔎 find      .github  0.1s\n"
        "  ┊ 🔎 grep      docker-compose  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "CODE-AUDIT FAST PATH\nsource=code_audit_p0_security",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is not None
    assert "CODE_AUDIT_POST_PATCH_DRIFT" in reason


def test_phase_budget_allows_code_audit_terminal_handoff_after_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 📖 read      /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ ✅ kanban_complete  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "CODE-AUDIT FAST PATH\nsource=code_audit_p0_security",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is None


def test_phase_budget_accumulates_code_audit_drift_across_patches(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_CODE_AUDIT_POST_PATCH_EXPLORE_BUDGET", "2")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 📖 read      /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 🔎 grep      validate_structure  0.1s\n"
        "  ┊ 🔎 find      .github  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "CODE-AUDIT FAST PATH\nsource=code_audit_p0_security",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is not None
    assert "CODE_AUDIT_POST_PATCH_DRIFT" in reason
    assert "post_patch_explore_steps=3" in reason


def test_phase_budget_does_not_apply_code_audit_post_patch_guard_to_generic_tasks(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_CODE_AUDIT_POST_PATCH_EXPLORE_BUDGET", "2")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 📖 read      /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 🔎 grep      nearby_symbol  0.1s\n"
        "  ┊ 🔎 find      services/zoe-data/tests  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "ordinary implement task",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is None


def test_phase_budget_does_not_treat_code_auditor_as_code_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_CODE_AUDIT_POST_PATCH_EXPLORE_BUDGET", "2")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "  ┊ 🔧 patch     /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 📖 read      /home/zoe/.worktrees/t_impl/services/zoe-ui/nginx.conf  0.1s\n"
        "  ┊ 🔎 grep      nearby_symbol  0.1s\n"
        "  ┊ 🔎 find      services/zoe-data/tests  0.1s\n",
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "body": "source=code_auditor_review",
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            },
            "runs": [{"started_at": 100}],
        },
        now=120,
    )

    assert reason is None


def test_phase_budget_reason_reuses_implement_log_session(monkeypatch):
    monkeypatch.setattr(kb, "tool_step_count", lambda *args, **kwargs: 1)
    monkeypatch.setattr(kb, "_started_timestamp", lambda detail: None)
    calls = []

    def fake_latest(task_id, *, max_lines=120):
        calls.append(max_lines)
        if max_lines == 0:
            return (
                "Query: work kanban task t_impl\n"
                "  ┊ 📖 read      /work/services/zoe-data/intent_router.py  0.1s\n"
            )
        return ""

    monkeypatch.setattr(kb, "_latest_log_session", fake_latest)

    assert kb.phase_budget_reason("t_impl", "implement", {"task": {}, "runs": []}) is None
    assert calls.count(0) == 1


def test_phase_budget_reason_ignores_stale_iteration_budget_log_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_impl.log").write_text(
        "Query: work kanban task t_impl\n"
        "⚠ Iteration budget reached (22/22) — response may be incomplete\n"
        "Query: work kanban task t_impl\n"
        "  ┊ ✅ kanban_block  0.1s\n",
        encoding="utf-8",
    )

    assert kb.phase_budget_reason_from_log("t_impl", "implement") is None


@pytest.mark.asyncio
async def test_poll_v4_blocked_protocol_violation_recovers_iteration_budget_from_log(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_implement.log").write_text(
        "Query: work kanban task t_implement\n"
        "  ┊ 🔎 grep      ITERATION_BUDGET  0.1s\n"
        "⚠ Iteration budget reached (22/22) — response may be incomplete\n",
        encoding="utf-8",
    )
    rows = [_row("implement", "blocked", chain_version="v4")]
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

    assert out["status"] == "blocked"
    assert out["blocker"] == (
        "ITERATION_BUDGET: Hermes iteration budget reached during implement "
        "(steps=22, limit=22)"
    )
    assert out["pipeline"]["status"] == "blocked"
    assert out["pipeline"]["block_reason"] == (
        "ITERATION_BUDGET: Hermes iteration budget reached during implement "
        "(steps=22, limit=22)"
    )


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


def test_review_budget_gets_wrapup_grace_after_mark_reviewed_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    lines = ["Query: work kanban task t_review"]
    lines.extend(f"  ┊ tool call {idx}  0.1s" for idx in range(13))
    lines.append(
        "  ┊ 💻 $ PYTHONPATH=services/zoe-data python3 "
        "services/zoe-data/pipeline_evidence_commands.py mark-reviewed "
        "multica:issue --critical-count 0 --summary approved  0.1s"
    )
    (log_dir / "t_review.log").write_text("\n".join(lines), encoding="utf-8")

    reason = kb.phase_budget_reason(
        "t_review",
        "review",
        {"task": {"started_at": 100}},
        now=110,
    )

    assert kb.tool_step_count("t_review") == 14
    assert kb.review_wrapup_tool_grace("t_review", "review") == 3
    assert reason is None


def test_review_budget_finds_verdict_in_verbose_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    lines = ["Query: work kanban task t_review"]
    lines.extend(f"  ┊ pre-verdict tool call {idx}  0.1s" for idx in range(8))
    lines.append(
        "  ┊ 💻 $ python3 services/zoe-data/pipeline_evidence_commands.py "
        "mark-reviewed multica:issue --critical-count 0 --summary approved  0.1s"
    )
    lines.extend(f"verbose review output line {idx}" for idx in range(220))
    lines.extend(f"  ┊ post-verdict tool call {idx}  0.1s" for idx in range(5))
    (log_dir / "t_review.log").write_text("\n".join(lines), encoding="utf-8")

    reason = kb.phase_budget_reason(
        "t_review",
        "review",
        {"task": {"started_at": 100}},
        now=110,
    )

    assert kb.tool_step_count("t_review") == 14
    assert kb.review_wrapup_tool_grace("t_review", "review") == 3
    assert reason is None


def test_review_budget_wrapup_grace_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_REVIEW_WRAPUP_TOOL_GRACE", "5")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    lines = ["Query: work kanban task t_review"]
    lines.append(
        "  ┊ 💻 $ python3 services/zoe-data/pipeline_evidence_commands.py "
        "mark-reviewed multica:issue --critical-count 0 --summary approved  0.1s"
    )
    (log_dir / "t_review.log").write_text("\n".join(lines), encoding="utf-8")

    assert kb.review_wrapup_tool_grace("t_review", "review") == 5


def test_review_budget_wrapup_grace_zero_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_REVIEW_WRAPUP_TOOL_GRACE", "0")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    lines = ["Query: work kanban task t_review"]
    lines.append(
        "  ┊ 💻 $ python3 services/zoe-data/pipeline_evidence_commands.py "
        "mark-reviewed multica:issue --critical-count 0 --summary approved  0.1s"
    )
    (log_dir / "t_review.log").write_text("\n".join(lines), encoding="utf-8")

    assert kb.review_wrapup_tool_grace("t_review", "review") == 0


def test_review_budget_wrapup_grace_bad_override_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("ZOE_KANBAN_REVIEW_WRAPUP_TOOL_GRACE", "soon")
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    lines = ["Query: work kanban task t_review"]
    lines.append(
        "  ┊ 💻 $ python3 services/zoe-data/pipeline_evidence_commands.py "
        "mark-reviewed multica:issue --critical-count 0 --summary approved  0.1s"
    )
    (log_dir / "t_review.log").write_text("\n".join(lines), encoding="utf-8")

    assert kb.review_wrapup_tool_grace("t_review", "review") == 3


def test_review_budget_without_verdict_still_blocks_at_normal_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "t_review.log").write_text(
        "Query: work kanban task t_review\n"
        + "\n".join(f"  ┊ tool call {idx}  0.1s" for idx in range(13)),
        encoding="utf-8",
    )

    reason = kb.phase_budget_reason(
        "t_review",
        "review",
        {"task": {"started_at": 100}},
        now=110,
    )

    assert reason is not None
    assert "BLOCKER=REVIEW_BUDGET" in reason
    assert "hard_limit=12" in reason


def test_review_budget_does_not_treat_mark_reviewed_help_as_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    lines = ["Query: work kanban task t_review"]
    lines.extend(f"  ┊ tool call {idx}  0.1s" for idx in range(13))
    lines.append(
        "  ┊ 💻 $ python3 services/zoe-data/pipeline_evidence_commands.py "
        "mark-reviewed --help  0.1s"
    )
    (log_dir / "t_review.log").write_text("\n".join(lines), encoding="utf-8")

    reason = kb.phase_budget_reason(
        "t_review",
        "review",
        {"task": {"started_at": 100}},
        now=110,
    )

    assert kb.review_wrapup_tool_grace("t_review", "review") == 0
    assert reason is not None
    assert "BLOCKER=REVIEW_BUDGET" in reason


def test_review_budget_does_not_treat_help_output_as_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    log_dir = tmp_path / "kanban" / "logs"
    log_dir.mkdir(parents=True)
    lines = ["Query: work kanban task t_review"]
    lines.extend(f"  ┊ tool call {idx}  0.1s" for idx in range(13))
    lines.append(
        "  ┊ 💻 $ python3 services/zoe-data/pipeline_evidence_commands.py "
        "mark-reviewed --help  0.1s"
    )
    lines.append(
        "usage: mark-reviewed multica:issue --critical-count 0 --summary approved"
    )
    (log_dir / "t_review.log").write_text("\n".join(lines), encoding="utf-8")

    reason = kb.phase_budget_reason(
        "t_review",
        "review",
        {"task": {"started_at": 100}},
        now=110,
    )

    assert kb.review_wrapup_tool_grace("t_review", "review") == 0
    assert reason is not None
    assert "BLOCKER=REVIEW_BUDGET" in reason


def test_verify_phase_budget_allows_pr_validation_headroom(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)
    monkeypatch.delenv("ZOE_KANBAN_VERIFY_TOOL_BUDGET", raising=False)

    log_path.write_text("\n".join(["  ┊ tool call"] * 17), encoding="utf-8")
    reason = kb.phase_budget_reason(
        "t_verify",
        "verify",
        {"task": {"started_at": 100}},
        now=110,
    )

    assert reason is None

    log_path.write_text("\n".join(["  ┊ tool call"] * 19), encoding="utf-8")
    reason = kb.phase_budget_reason(
        "t_verify",
        "verify",
        {"task": {"started_at": 100}},
        now=110,
    )

    assert reason is not None
    assert "VERIFY_BUDGET" in reason
    assert "guidance_limit=16" in reason
    assert "hard_limit=18" in reason


def test_phase_budget_blocks_worker_that_leaves_pinned_worktree(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    log_path.write_text(
        "\n".join(
            [
                "Query: work kanban task t_impl",
                "  ┊ 💻 $ cd /home/zoe/assistant && git status  0.1s",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            }
        },
        now=110,
    )

    assert reason is not None
    assert "BLOCKER=WORKTREE_PATH_VIOLATION" in reason
    assert "/home/zoe/assistant" in reason
    assert "/home/zoe/.worktrees/t_impl" in reason
    assert "`git status`" in reason


def test_phase_budget_blocks_verify_worker_that_leaves_pinned_worktree(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    log_path.write_text(
        "\n".join(
            [
                "Query: work kanban task t_verify",
                "  ┊ 💻 $ cd /home/zoe/assistant && python3 tools/audit/validate_structure.py  0.1s [retry=2]",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)

    reason = kb.phase_budget_reason(
        "t_verify",
        "verify",
        {
            "task": {
                "started_at": 100,
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_verify",
            }
        },
        now=110,
    )

    assert reason is not None
    assert "BLOCKER=WORKTREE_PATH_VIOLATION" in reason
    assert "/home/zoe/assistant" in reason
    assert "/home/zoe/.worktrees/t_verify" in reason
    assert "`python3 tools/audit/validate_structure.py`" in reason


def test_phase_budget_blocks_absolute_reads_outside_pinned_worktree(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    log_path.write_text(
        "\n".join(
            [
                "Query: work kanban task t_impl",
                "  ┊ 📖 read      /home/zoe/assistant/services/zoe-data/intent_router.py  0.1s",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
                "body": "INTENT-GAP IMPLEMENT FAST PATH",
            }
        },
        now=110,
    )

    assert reason is not None
    assert "BLOCKER=WORKTREE_PATH_VIOLATION" in reason
    assert "/home/zoe/assistant/services/zoe-data/intent_router.py" in reason
    assert "/home/zoe/.worktrees/t_impl" in reason


def test_phase_budget_allows_absolute_reads_inside_pinned_worktree(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    log_path.write_text(
        "\n".join(
            [
                "Query: work kanban task t_impl",
                "  ┊ 📖 read      /home/zoe/.worktrees/t_impl/services/zoe-data/intent_router.py  0.1s",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            }
        },
        now=110,
    )

    assert reason is None


def test_phase_budget_allows_commands_inside_pinned_worktree(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    log_path.write_text(
        "\n".join(
            [
                "Query: work kanban task t_impl",
                "  ┊ 💻 $ cd /home/zoe/.worktrees/t_impl && git status  0.1s",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            }
        },
        now=110,
    )

    assert reason is None


def test_phase_budget_allows_commands_in_pinned_worktree_subdirectory(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    log_path.write_text(
        "\n".join(
            [
                "Query: work kanban task t_impl",
                "  ┊ 💻 $ cd /home/zoe/.worktrees/t_impl/services/zoe-data && python3 -m pytest tests/test_kanban_adapter.py  0.1s",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl",
            }
        },
        now=110,
    )

    assert reason is None


def test_phase_budget_parses_quoted_worktree_paths(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    log_path.write_text(
        "\n".join(
            [
                "Query: work kanban task t_impl",
                "  ┊ 💻 $ cd '/home/zoe/.worktrees/t_impl spaced/services/zoe-data' && git status  0.1s",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)

    reason = kb.phase_budget_reason(
        "t_impl",
        "implement",
        {
            "task": {
                "started_at": 100,
                "workspace_kind": "worktree",
                "workspace_path": "/home/zoe/.worktrees/t_impl spaced",
            }
        },
        now=110,
    )

    assert reason is None


def test_existing_pr_revision_implement_budget_has_scoped_headroom(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)
    monkeypatch.delenv("ZOE_KANBAN_IMPLEMENT_TOOL_BUDGET", raising=False)
    monkeypatch.delenv("ZOE_KANBAN_IMPLEMENT_REVISION_TOOL_BUDGET", raising=False)

    log_path.write_text("\n".join(["  ┊ tool call"] * 27), encoding="utf-8")
    normal = kb.phase_budget_reason(
        "t_implement",
        "implement",
        {"task": {"started_at": 100, "body": "plain implement task"}},
        now=110,
    )
    revision = kb.phase_budget_reason(
        "t_revision",
        "implement",
        {"task": {"started_at": 100, "body": "EXISTING PR REVISION FAST PATH"}},
        now=110,
    )

    assert normal is not None
    assert "guidance_limit=24" in normal
    assert revision is None

    log_path.write_text("\n".join(["  ┊ tool call"] * 33), encoding="utf-8")
    revision = kb.phase_budget_reason(
        "t_revision",
        "implement",
        {"task": {"started_at": 100, "body": "EXISTING PR REVISION FAST PATH"}},
        now=110,
    )

    assert revision is not None
    assert "IMPLEMENT_BUDGET" in revision
    assert "guidance_limit=30" in revision
    assert "hard_limit=32" in revision

    log_path.write_text("\n".join(["  ┊ tool call"] * 27), encoding="utf-8")
    top_level_only = kb.phase_budget_reason(
        "t_top_level",
        "implement",
        {"task": {"started_at": 100}, "body": "EXISTING PR REVISION FAST PATH"},
        now=110,
    )
    assert top_level_only is not None
    assert "guidance_limit=24" in top_level_only


def test_existing_pr_revision_budget_inherits_generic_override(tmp_path, monkeypatch):
    log_path = tmp_path / "task.log"
    monkeypatch.setattr(kb, "_log_path", lambda _task_id: log_path)
    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_TOOL_BUDGET", "28")
    monkeypatch.delenv("ZOE_KANBAN_IMPLEMENT_REVISION_TOOL_BUDGET", raising=False)

    log_path.write_text("\n".join(["  ┊ tool call"] * 31), encoding="utf-8")
    inherited = kb.phase_budget_reason(
        "t_revision",
        "implement",
        {"task": {"started_at": 100, "body": "EXISTING PR REVISION FAST PATH"}},
        now=110,
    )
    assert inherited is not None
    assert "guidance_limit=28" in inherited
    assert "hard_limit=30" in inherited

    monkeypatch.setenv("ZOE_KANBAN_IMPLEMENT_REVISION_TOOL_BUDGET", "34")
    specific = kb.phase_budget_reason(
        "t_revision",
        "implement",
        {"task": {"started_at": 100, "body": "EXISTING PR REVISION FAST PATH"}},
        now=110,
    )
    assert specific is None


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
    assert "scripts/maintenance/run_greploop_guard.sh --pr N --once" in body
    assert "Do not call Greptile trigger-review directly" in body
    assert "python3 scripts/maintenance/greploop_guard.py" not in body
    assert "trigger-review jason-easyazz/zoe-ai-assistant" not in body


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


def test_implement_body_adds_code_audit_fast_path_for_actionable_bug():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-code-audit",
            "identifier": "ZOE-5354",
            "title": "GET /metrics endpoint is unauthenticated",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_p0_security",
                "acceptance_criteria": ["Require admin or internal token on /metrics"],
            },
        },
        "ZOE-5354",
    )

    assert "CODE-AUDIT FAST PATH" in body
    assert "Do not re-audit the whole repo" in body
    assert "Apply the smallest patch" in body
    assert "After the first patch, spend at most 2 more tool calls locating validation" in body
    assert "do not inspect docker-compose, .github" in body
    assert "do not run `git add` by itself" in body
    assert "code-audit ship instruction overrides the generic separate push/PR" in body
    assert "unless a phase fast path above explicitly requires a chained ship command" in body
    assert "on a second ambiguous patch, call `kanban_block`" in body
    assert "git push -u origin HEAD" in body
    assert body.index("CODE-AUDIT FAST PATH") < body.index("AUDIT/SMOKE FAST PATH")
    assert body.index("CODE-AUDIT FAST PATH") < body.index("codebase-memory map")


def test_implement_body_points_nginx_security_headers_to_helper():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-code-audit-nginx",
            "identifier": "ZOE-5355",
            "title": "nginx.conf missing all HTTP security headers",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_p0_security",
                "acceptance_criteria": [
                    "Add CSP, X-Frame-Options, HSTS, nosniff, Referrer-Policy, and Permissions-Policy"
                ],
            },
        },
        "ZOE-5355",
    )

    assert "do not hand-patch repeated `server {}` blocks" in body
    assert "tools/audit/ensure_nginx_security_headers.py" in body
    assert "--check" in body
    assert body.index("ensure_nginx_security_headers.py") < body.index("After `kanban_show`")


def test_implement_body_points_nginx_named_headers_to_helper():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-code-audit-nginx-csp",
            "identifier": "ZOE-5356",
            "title": "nginx.conf: add X-Frame-Options, CSP, and HSTS",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_p0_security",
                "acceptance_criteria": ["Add Content-Security-Policy and Strict-Transport-Security"],
            },
        },
        "ZOE-5356",
    )

    assert "tools/audit/ensure_nginx_security_headers.py" in body


def test_implement_body_points_nginx_header_terms_to_helper_without_p0_source():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-code-audit-nginx-header-term",
            "identifier": "ZOE-5357",
            "title": "nginx.conf: add Content-Security-Policy",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_scan",
                "acceptance_criteria": ["Add Content-Security-Policy"],
            },
        },
        "ZOE-5357",
    )

    assert "tools/audit/ensure_nginx_security_headers.py" in body


def test_implement_body_points_nginx_security_ticket_without_conf_suffix_to_helper():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-code-audit-nginx-term",
            "identifier": "ZOE-5358",
            "title": "nginx: missing HSTS and CSP",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_scan",
                "acceptance_criteria": ["Add HSTS and CSP"],
            },
        },
        "ZOE-5358",
    )

    assert "tools/audit/ensure_nginx_security_headers.py" in body


def test_implement_body_omits_nginx_header_helper_for_non_header_ticket():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-code-audit-nginx-cipher",
            "identifier": "ZOE-5359",
            "title": "nginx: tighten TLS cipher selection",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_p0_security",
                "acceptance_criteria": ["Disable weak TLS ciphers"],
            },
        },
        "ZOE-5359",
    )

    assert "tools/audit/ensure_nginx_security_headers.py" not in body


def test_implement_body_omits_code_audit_fast_path_without_acceptance_criteria():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-code-audit-open",
            "identifier": "ZOE-OPEN",
            "title": "Investigate unauthenticated endpoint",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_p0_security",
            },
        },
        "ZOE-OPEN",
    )

    assert "CODE-AUDIT FAST PATH" not in body


def test_implement_body_omits_code_audit_fast_path_for_non_code_audit():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-harness",
            "identifier": "ZOE-5449",
            "title": "Harness: follow up ITERATION_BUDGET",
            "metadata": {
                "zoe_kind": "harness_fix",
                "source": "engineering_blocker_followup",
                "acceptance_criteria": ["Focused tests cover blocker path"],
            },
        },
        "ZOE-5449",
    )

    assert "CODE-AUDIT FAST PATH" not in body


def test_implement_body_always_completes_after_pr_creation_even_for_security_review():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {"id": "uuid-generic", "identifier": "ZOE-GEN", "title": "Generic feature", "description": "Add a small endpoint"},
        "ZOE-GEN",
    )

    assert "Security-sensitive changes still complete implement after the PR is opened" in body
    assert "Do not call `kanban_block` merely because a human/security reviewer should inspect the PR" in body
    assert "CODE-AUDIT FAST PATH" not in body


def test_implement_body_requires_pinned_worktree_before_git_or_pr_commands():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {"id": "uuid-generic", "identifier": "ZOE-GEN", "title": "Generic feature"},
        "ZOE-GEN",
    )

    assert "cd <workspace_path> && pwd && git branch --show-current" in body
    assert "exact task `workspace_path`" in body
    assert "File reads must use paths under that" in body
    assert "never absolute live-checkout paths" in body
    assert "BLOCKER=WORKTREE_PATH_VIOLATION" in body
    # The live checkout must be off-limits for every command shape, including the
    # `git -C <liveroot>` orientation form that previously slipped past the guard.
    assert "NEVER touch the live checkout" in body
    # Anchor to the actual live root so a future edit dropping the negative
    # example is caught — a bare "git -C" would also match the positive
    # own-worktree orientation example in the prompt.
    assert f"git -C {ka.zoe_repo_root()}" in body
    assert "git worktree list" in body
    assert body.index("BLOCKER=WORKTREE_PATH_VIOLATION") < body.index("git push -u origin HEAD")


def test_verify_body_requires_exact_workspace_path_for_repo_commands():
    body = ka.KanbanAdapter()._build_body(
        "verify",
        {"id": "uuid-generic", "identifier": "ZOE-GEN", "title": "Generic feature"},
        "ZOE-GEN",
    )

    assert "exact task `workspace_path`" in body
    assert "pwd && git branch --show-current" in body
    assert "BLOCKER=WORKTREE_PATH_VIOLATION" in body
    assert "cd <workspace_path> && PYTHONPATH=services/zoe-data python3 -m pytest" in body
    assert "do not use relative `cd services/zoe-data`" in body
    assert body.index("BLOCKER=WORKTREE_PATH_VIOLATION") < body.index(
        "python3 tools/audit/validate_structure.py"
    )


def test_implement_body_documents_existing_pr_revision_fast_path_before_new_pr_creation(monkeypatch):
    monkeypatch.setenv("GREPTILE_MCP_BIN", "/opt/zoe/greptile-mcp")
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-pr-revision",
            "identifier": "ZOE-5354",
            "title": "GET /metrics endpoint is unauthenticated",
            "description": """```zoe-ticket
{"pr_url":"https://github.com/jason-easyazz/zoe-ai-assistant/pull/213","last_evidence":"Revision required from verify"}
```""",
            "metadata": {
                "zoe_kind": "bug",
                "source": "code_audit_p0_security",
                "acceptance_criteria": ["Require admin or internal token on /metrics"],
            },
        },
        "ZOE-5354",
    )

    assert "EXISTING PR REVISION FAST PATH" in body
    assert "Do not rediscover the original fix and do not create a new PR" in body
    assert "do not read, grep, or edit files until the existing PR checkout below succeeds" in body
    assert "/opt/zoe/greptile-mcp pr-comments --unaddressed-only" in body
    assert "may exit nonzero when it prints unresolved comments" in body
    assert "Zoe dispatch pre-checks this task worktree to the existing PR head" in body
    assert "headRefOid" in body
    assert "git rev-parse HEAD" in body
    assert "Do not run `gh pr checkout`, `git checkout`, `git fetch`, or `git reset` yourself" in body
    assert "git push origin HEAD:<headRefName>" in body
    assert "report the SAME PR_URL" in body
    assert "PYTHONPATH=services/zoe-data python3 -m pytest" in body
    assert "patch the module variable with monkeypatch/setattr after import" in body
    assert "BLOCKER=PR_REVISION_CHECKOUT_FAILED" in body
    assert "BLOCKER=PR_REVISION_BLOCKED" in body
    assert body.index("BLOCKER=PR_REVISION_CHECKOUT_FAILED") < body.index("BLOCKER=PR_REVISION_BLOCKED")
    assert body.index("EXISTING PR REVISION FAST PATH") < body.index("open ONE small PR")

    generic_body = ka.KanbanAdapter()._build_body(
        "implement",
        {"id": "uuid-generic", "identifier": "ZOE-GEN", "title": "Generic feature"},
        "ZOE-GEN",
    )
    assert "EXISTING PR REVISION FAST PATH" in generic_body
    assert "if the ticket block already contains `pr_url`/PR_URL" in generic_body


def test_implement_body_puts_bounded_fast_paths_before_codebase_memory():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Audit driver", "description": "evidence_profile: audit"},
        "ZOE-9",
    )

    assert "AUDIT/SMOKE FAST PATH" in body
    assert "TOOLS_USED=audit-read" in body
    assert "TESTS=not applicable/audit-only" in body
    assert "codebase-memory map" in body
    assert "explicitly says audit-only" in body
    assert "uses trace/map with an audit/no-code qualifier" in body
    assert "SMALL EXPLICIT CODE FAST PATH" in body
    assert "Start editing within 6 tool/model steps" in body
    assert "BLOCKER=IMPLEMENT_BUDGET" in body
    assert "Do NOT create additional Hermes/Kanban tasks" in body
    assert "scaffold subtasks" in body
    assert "broad or ambiguous code-changing tickets only" in body
    assert body.index("AUDIT/SMOKE FAST PATH") < body.index("codebase-memory map")
    assert body.index("SMALL EXPLICIT CODE FAST PATH") < body.index("codebase-memory map")


def test_implement_body_has_edit_safety_loop():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Small code change", "description": ""},
        "ZOE-9",
    )

    assert "EDIT SAFETY LOOP" in body
    assert "after every patch, immediately run the narrowest syntax check" in body
    assert "python3 -m py_compile <file>" in body
    assert "before any second patch or more exploration" in body
    assert "BLOCKER=IMPLEMENT_EDIT_SAFETY" in body
    assert "Never leave a malformed partial edit and keep exploring" in body
    assert body.index("EDIT SAFETY LOOP") < body.index("If the task needs more than one PR")


def test_implement_body_includes_harness_repo_map_for_harness_tickets():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-5435",
            "identifier": "ZOE-5435",
            "title": "retro-fallback-when-worktree-missing",
            "description": """Fix the missing worktree retro fallback.

```zoe-ticket
{"schema":1,"zoe_kind":"harness_fix","source":"retro_followup","acceptance_criteria":["small harness fix"],"evidence_expectations":["focused tests"]}
```""",
        },
        "ZOE-5435",
    )

    assert "HARNESS FAST PATH" in body
    assert "services/zoe-data/executors/kanban_adapter.py" in body
    assert "services/zoe-data/worktree_bootstrap.py" in body
    assert "For worktree-missing/retro fallback tickets" in body
    assert "Start editing within 6 tool/model steps" in body
    assert body.index("HARNESS FAST PATH") < body.index("codebase-memory map")


def test_implement_body_includes_harness_repo_map_for_blocker_followup_source():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-5454",
            "identifier": "ZOE-5454",
            "title": "Follow up iteration budget",
            "description": """Fix the blocked harness run.

```zoe-ticket
{"schema":1,"zoe_kind":"operator_task","source":"engineering_blocker_followup","source_blocker":"ITERATION_BUDGET","acceptance_criteria":["small harness fix"],"evidence_expectations":["focused tests"]}
```""",
        },
        "ZOE-5454",
    )

    assert "HARNESS FAST PATH" in body
    assert "services/zoe-data/executors/kanban_adapter.py" in body
    assert "services/zoe-data/worktree_bootstrap.py" in body
    assert "engineering_blocker_followup tickets" in body
    assert "services/zoe-data/main.py" in body
    assert "services/zoe-data/tests/test_main_multica_poll.py" in body
    assert (
        "PYTHONPATH=services/zoe-data python3 -m pytest -q "
        "services/zoe-data/tests/test_main_multica_poll.py::"
        "test_record_blocked_multica_chain_creates_iteration_budget_followup"
    ) in body
    assert "do not create `.venv`, run `pip install`" in body
    assert "BLOCKER=TEST_ENVIRONMENT" in body
    assert "If the focused test passes before any edit, do not inspect more blocker code" in body
    assert "use at most three symbol greps total, up to four reads of the focused test file, and two reads per other named file" in body
    assert "edit the named harness file already in scope" in body
    assert "services/zoe-data/main.py" in body
    assert "services/zoe-data/executors/kanban_adapter.py" in body
    assert "BLOCKER=ALREADY_COVERED" in body


@pytest.mark.parametrize(
    ("source_blocker", "expected_test"),
    [
        (
            "IMPLEMENT_BUDGET",
            "test_record_blocked_multica_chain_creates_budget_followup_once",
        ),
        (
            "IMPLEMENT_HANDOFF_DRIFT",
            "test_record_blocked_multica_chain_creates_budget_followup_once",
        ),
        (
            "PROTOCOL_VIOLATION",
            "test_record_blocked_multica_chain_creates_protocol_followup",
        ),
        (
            "",
            "services/zoe-data/tests/test_main_multica_poll.py`",
        ),
    ],
)
def test_implement_body_includes_harness_blocker_followup_focused_tests(
    source_blocker, expected_test
):
    source_blocker_json = f',"source_blocker":"{source_blocker}"' if source_blocker else ""
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-5454",
            "identifier": "ZOE-5454",
            "title": "Follow up harness blocker",
            "description": f"""Fix the blocked harness run.

```zoe-ticket
{{"schema":1,"zoe_kind":"operator_task","source":"engineering_blocker_followup"{source_blocker_json},"acceptance_criteria":["small harness fix"],"evidence_expectations":["focused tests"]}}
```""",
        },
        "ZOE-5454",
    )

    assert "engineering_blocker_followup tickets" in body
    assert expected_test in body
    assert "Use the existing repo/runtime environment only" in body
    assert "If the focused test passes before any edit, do not inspect more blocker code" in body
    assert "use at most three symbol greps total, up to four reads of the focused test file, and two reads per other named file" in body
    assert "edit the named harness file already in scope" in body
    assert "services/zoe-data/main.py" in body
    assert "services/zoe-data/executors/kanban_adapter.py" in body
    assert "BLOCKER=ALREADY_COVERED" in body


def test_implement_body_includes_harness_repo_map_for_harness_title():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-title",
            "identifier": "ZOE-TITLE",
            "title": "Harness: fix worktree path",
            "description": """Small operator task.

```zoe-ticket
{"schema":1,"zoe_kind":"operator_task","source":"manual","acceptance_criteria":["small fix"],"evidence_expectations":["focused tests"]}
```""",
        },
        "ZOE-TITLE",
    )

    assert "HARNESS FAST PATH" in body
    assert "services/zoe-data/executors/kanban_adapter.py" in body


def test_implement_body_omits_harness_repo_map_for_unrelated_harness_word():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-test-harness",
            "identifier": "ZOE-TEST",
            "title": "Fix CI test harness timeout",
            "description": "Update a product test fixture timeout.",
        },
        "ZOE-TEST",
    )

    assert "HARNESS FAST PATH" not in body


def test_implement_body_omits_harness_repo_map_for_generic_tickets():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-generic",
            "identifier": "ZOE-GEN",
            "title": "Generic feature",
            "description": "Add a small user setting.",
        },
        "ZOE-GEN",
    )

    assert "HARNESS FAST PATH" not in body
    assert "For worktree-missing/retro fallback tickets" not in body


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


def test_scout_body_has_intent_gap_fast_path():
    body = ka.KanbanAdapter()._build_body(
        "scout",
        {
            "id": "uuid-1",
            "identifier": "ZOE-5451",
            "title": "Intent gap: 'Tell me a joke.'",
            "description": "Intent router missed similar messages in the last 7 days.",
        },
        "ZOE-5451",
    )

    assert "INTENT GAP FAST PATH" in body
    assert "ticket evidence plus at most one" in body
    assert "at most one focused lookup of the routing/intent file" in body
    assert "IMPLEMENTATION_REQUIRED=true unless the exact behavior is already handled" in body
    assert body.index("INTENT GAP FAST PATH") < body.index("Keep this phase bounded")


def test_implement_body_includes_unconditional_scout_handoff_fast_path():
    # This is a static prompt-contract assertion. The agent decides whether
    # SCOUT_SUMMARY is present at runtime after `kanban_show`.
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-1",
            "identifier": "ZOE-5451",
            "title": "Intent gap: 'Tell me a joke.'",
            "description": "",
        },
        "ZOE-5451",
    )

    assert "SCOUT HANDOFF FAST PATH" in body
    assert "treat that as the accepted context" in body
    assert "Do not re-scout, re-map, or repeatedly read the same file" in body
    assert "intent-gap tickets" in body
    assert "start editing within 4 tool/model steps" in body
    assert body.index("SCOUT HANDOFF FAST PATH") < body.index("SMALL EXPLICIT CODE FAST PATH")


def test_implement_body_includes_intent_gap_fast_path():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-intent",
            "identifier": "ZOE-5451",
            "title": "Intent gap: 'Tell me a joke.'",
            "description": "SCOUT_SUMMARY says services/zoe-data/intent_router.py needs a narrow route.",
        },
        "ZOE-5451",
    )

    assert "INTENT-GAP IMPLEMENT FAST PATH" in body
    assert "services/zoe-data/intent_router.py" in body
    assert "nearest intent_router tests" in body
    assert "`_AGENT_CHAT_RE`" in body
    assert "Open-domain Q&A / creative" in body
    assert "do not grep `_CALCULATE_`, `_execute_`" in body
    assert "`Tell me a joke.`, `Tell me a joke`, and `Tell me another joke.`" in body
    assert "python3 ./scripts/maintenance/zoe_apply_intent_gap_contract.py joke --repo-root ." in body
    assert "python3 -m py_compile services/zoe-data/intent_router.py" in body
    assert "services/zoe-data/tests/test_intent_open_domain.py" in body
    assert "Intent(\"extend_capability\", {\"raw\": <original text>})" in body
    assert "Do not add a joke bank or a brittle per-joke executor" in body
    assert "Start editing within 4 tool/model steps after `kanban_show`" in body
    assert body.index("INTENT-GAP IMPLEMENT FAST PATH") < body.index("AUDIT/SMOKE FAST PATH")


def test_implement_body_does_not_add_joke_contract_for_other_intent_gaps():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-intent-weather",
            "identifier": "ZOE-5450",
            "title": "Intent gap: 'Can you tell me about me?'",
            "description": "This is no joke; the routing is broken for profile questions.",
        },
        "ZOE-5450",
    )

    assert "INTENT-GAP IMPLEMENT FAST PATH" in body
    assert "Concrete edit contract for this joke gap" not in body
    assert "`Tell me a joke.`, `Tell me a joke`, and `Tell me another joke.`" not in body


def test_implement_body_includes_bare_say_exactly_intent_gap_contract():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-intent-exact-bare",
            "identifier": "ZOE-5682",
            "title": "Intent gap: say exactly",
            "description": (
                "Representative: say exactly. Acceptance: Say exactly: "
                "Zoe chat integration ok routes to open-domain. Evidence points at "
                "services/zoe-data/evolution_notice.py:run_evolution_notice."
            ),
        },
        "ZOE-5682",
    )

    assert "INTENT-GAP IMPLEMENT FAST PATH" in body
    assert body.startswith("CRITICAL FIRST ACTION FOR THIS INTENT-GAP TICKET:")
    assert body.index("CRITICAL FIRST ACTION") < body.index("services/zoe-data/evolution_notice.py")
    assert "Concrete edit contract for this exact-repeat gap" in body
    assert "your NEXT tool call must be the terminal command" in body
    assert "cd <workspace_path> && python3 ./scripts/maintenance/zoe_apply_intent_gap_contract.py say_exactly --repo-root ." in body
    assert "--run-focused-checks --kanban-task <task_id_from_kanban_show>" in body
    assert "BLOCKER=INTENT_GAP_HELPER_UNAVAILABLE" in body
    assert body.index("your NEXT tool call must be the terminal command") < body.index(
        "AUDIT/SMOKE FAST PATH"
    )


def test_implement_body_includes_say_exactly_intent_gap_contract():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-intent-exact",
            "identifier": "ZOE-5458",
            "title": "Intent gap: say exactly Zoe chat integration ok",
            "description": "SCOUT_SUMMARY says Say exactly: Zoe chat integration ok should preserve the raw phrase.",
        },
        "ZOE-5458",
    )

    assert "INTENT-GAP IMPLEMENT FAST PATH" in body
    assert body.startswith("CRITICAL FIRST ACTION FOR THIS INTENT-GAP TICKET:")
    assert "Concrete edit contract for this exact-repeat gap" in body
    assert "Say exactly: Zoe chat integration ok" in body
    assert "cd <workspace_path> && python3 ./scripts/maintenance/zoe_apply_intent_gap_contract.py say_exactly --repo-root ." in body
    assert "--run-focused-checks --kanban-task <task_id_from_kanban_show>" in body
    assert "terminal_action: kanban_block:ALREADY_COVERED" in body
    assert "Never run this from the live" in body
    assert "to the agent path while preserving the raw phrase" in body
    assert "Do not add a bespoke" in body


def test_implement_body_does_not_add_say_exactly_contract_for_description_aside():
    body = ka.KanbanAdapter()._build_body(
        "implement",
        {
            "id": "uuid-intent-aside",
            "identifier": "ZOE-5450",
            "title": "Intent gap: profile routing regression",
            "description": "The user did not ask Zoe to say exactly this phrase; this is unrelated prose.",
        },
        "ZOE-5450",
    )

    assert "INTENT-GAP IMPLEMENT FAST PATH" in body
    assert "Concrete edit contract for this exact-repeat gap" not in body
    assert "zoe_apply_intent_gap_contract say_exactly" not in body


def test_implement_revision_body_includes_intent_gap_fast_path():
    body = ka.KanbanAdapter()._build_body(
        "implement_revision",
        {
            "id": "uuid-intent",
            "identifier": "ZOE-5451",
            "title": "Intent-gap revision: 'Tell me a joke.'",
            "description": "Existing PR still needs a focused intent_router.py revision.",
        },
        "ZOE-5451",
    )

    assert "INTENT-GAP IMPLEMENT FAST PATH" in body
    assert "services/zoe-data/intent_router.py" in body
    assert "`_AGENT_CHAT_RE`" in body
    assert "Open-domain Q&A / creative" in body
    assert "do not grep `_CALCULATE_`, `_execute_`" in body
    assert "`Tell me a joke.`, `Tell me a joke`, and `Tell me another joke.`" in body
    assert "python3 ./scripts/maintenance/zoe_apply_intent_gap_contract.py joke --repo-root ." in body
    assert "python3 -m py_compile services/zoe-data/intent_router.py" in body
    assert "services/zoe-data/tests/test_intent_open_domain.py" in body
    assert "Intent(\"extend_capability\", {\"raw\": <original text>})" in body
    assert "Do not add a joke bank or a brittle per-joke executor" in body
    assert "EXISTING PR REVISION FAST PATH" in body
    assert "After the existing-PR checkout checks succeed" in body
    assert "Start editing within 4 tool/model steps after `kanban_show`" not in body


def test_retro_body_has_post_closeout_fast_path():
    body = ka.KanbanAdapter()._build_body(
        "retro",
        {"id": "uuid-1", "identifier": "ZOE-9", "title": "Retro", "description": ""},
        "ZOE-9",
    )

    assert "Workspace: main repo checkout" in body
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


# ---------------------------------------------------------------------------
# Capture-on-exit: salvage a finished implement diff the worker never shipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recover_unshipped_diff_commits_pushes_and_opens_pr(tmp_path, monkeypatch):
    """Worker left a dirty worktree (no commit/push): salvage -> commit+push+PR+done."""
    monkeypatch.setattr(ka, "latest_log_session", lambda *a, **k: "no PR handed off here")
    wt = tmp_path / "wt"
    wt.mkdir()
    row = {
        "id": "t_implement",
        "status": "blocked",
        "title": "ZOE-9 fix thing",
        "workspace_path": str(wt),
    }

    class _A(_FakeAdapter):
        def __init__(self):
            super().__init__()
            self.wt_cmds = []

        async def _run_worktree_command(self, args, *, cwd, timeout=45.0):
            self.wt_cmds.append(args)
            if args[:3] == ["git", "branch", "--show-current"]:
                return "wt/t_implement"
            if args[:3] == ["git", "status", "--porcelain"]:
                return " M services/zoe-data/routers/voice_tts.py"
            if args[:2] == ["git", "add"]:
                return ""
            if args[:2] == ["git", "commit"]:
                return ""
            if args[:3] == ["git", "rev-list", "--count"]:
                return "1"
            if args[:2] == ["git", "push"]:
                return ""
            if args[:3] == ["gh", "pr", "view"]:
                raise ka.KanbanCLIError("no pull request found")
            if args[:3] == ["gh", "pr", "create"]:
                return "https://github.com/jason-easyazz/zoe-ai-assistant/pull/777"
            raise AssertionError(args)

    a = _A()
    pr = await a._maybe_recover_unshipped_diff(
        "t_implement", "implement", row, issue={"identifier": "ZOE-9", "title": "fix thing"}
    )

    assert pr == "https://github.com/jason-easyazz/zoe-ai-assistant/pull/777"
    assert row["status"] == "done"
    assert any(c[:2] == ["git", "commit"] for c in a.wt_cmds)
    push = next(c for c in a.wt_cmds if c[:2] == ["git", "push"])
    assert "--force" not in push and push == ["git", "push", "-u", "origin", "wt/t_implement"]
    complete = next(c for c in a.calls if c[0] == "complete")
    assert "PR_URL=https://github.com/jason-easyazz/zoe-ai-assistant/pull/777" in "\n".join(complete)


@pytest.mark.asyncio
async def test_recover_unshipped_diff_refuses_non_task_branch(tmp_path, monkeypatch):
    """Hard safety gate: never commit/push when not on the task's own wt/ branch."""
    monkeypatch.setattr(ka, "latest_log_session", lambda *a, **k: "no pr")
    wt = tmp_path / "wt"
    wt.mkdir()
    row = {"id": "t_implement", "status": "blocked", "workspace_path": str(wt)}

    class _A(_FakeAdapter):
        def __init__(self):
            super().__init__()
            self.wt_cmds = []

        async def _run_worktree_command(self, args, *, cwd, timeout=45.0):
            self.wt_cmds.append(args)
            if args[:3] == ["git", "branch", "--show-current"]:
                return "main"  # not wt/t_implement -> must bail before any mutation
            raise AssertionError(f"must not mutate on non-task branch: {args!r}")

    a = _A()
    pr = await a._maybe_recover_unshipped_diff("t_implement", "implement", row)

    assert pr is None
    assert row["status"] == "blocked"
    assert not any(c[:2] in (["git", "add"], ["git", "commit"], ["git", "push"]) for c in a.wt_cmds)


@pytest.mark.asyncio
async def test_recover_unshipped_diff_skips_when_nothing_to_ship(tmp_path, monkeypatch):
    """Clean worktree with no unpushed commits must not open an empty PR."""
    monkeypatch.setattr(ka, "latest_log_session", lambda *a, **k: "no pr")
    wt = tmp_path / "wt"
    wt.mkdir()
    row = {"id": "t_implement", "status": "blocked", "workspace_path": str(wt)}

    class _A(_FakeAdapter):
        def __init__(self):
            super().__init__()
            self.wt_cmds = []

        async def _run_worktree_command(self, args, *, cwd, timeout=45.0):
            self.wt_cmds.append(args)
            if args[:3] == ["git", "branch", "--show-current"]:
                return "wt/t_implement"
            if args[:3] == ["git", "status", "--porcelain"]:
                return ""  # clean
            if args[:3] == ["git", "rev-list", "--count"]:
                return "0"  # nothing unpushed
            raise AssertionError(f"must not push/PR with nothing to ship: {args!r}")

    a = _A()
    pr = await a._maybe_recover_unshipped_diff("t_implement", "implement", row)

    assert pr is None
    assert not any(c[:2] == ["git", "push"] for c in a.wt_cmds)
    assert not any(c[:3] == ["gh", "pr", "create"] for c in a.wt_cmds)


@pytest.mark.asyncio
async def test_recover_unshipped_diff_skips_when_pr_already_handed_off(monkeypatch):
    """If the worker already reported a PR_URL, leave it to the normal flow."""
    monkeypatch.setattr(
        ka, "latest_log_session", lambda *a, **k: "PR_URL=https://github.com/o/r/pull/5\n"
    )
    row = {"id": "t_implement", "status": "blocked"}

    class _A(_FakeAdapter):
        async def _run_worktree_command(self, args, *, cwd, timeout=45.0):
            raise AssertionError(f"must not touch the worktree when a PR exists: {args!r}")

    a = _A()
    assert await a._maybe_recover_unshipped_diff("t_implement", "implement", row) is None


# ---------------------------------------------------------------------------
# _maybe_auto_block_dead_worker — adapter glue that reaps zombie running tasks
# ---------------------------------------------------------------------------


def _dead_detail():
    # past-grace running run; liveness is decided by the mocked _is_expected_worker
    return {"runs": [{"status": "running", "worker_pid": 987654, "started_at": "2000-01-01T00:00:00+00:00"}]}


def test_auto_block_dead_worker_blocks_zombie(monkeypatch):
    monkeypatch.setattr(ka, "_REAP_DEAD_WORKERS", True)
    monkeypatch.setattr(kb, "_is_expected_worker", lambda pid: False)  # worker dead
    adapter = ka.KanbanAdapter()
    calls: list[list[str]] = []

    async def fake_run(args, *, expect_json=False):
        calls.append(args)
        return ""

    adapter._run = fake_run  # type: ignore[assignment]
    row = {"status": "running"}
    out = asyncio.run(adapter._maybe_auto_block_dead_worker("t_verify", "verify", row, _dead_detail()))

    assert out is True
    assert calls and calls[0][0] == "block" and calls[0][1] == "t_verify"
    assert calls[0][2].startswith("BLOCKER=WORKER_DIED")
    assert row["status"] == "blocked"
    assert row["block_reason"].startswith("BLOCKER=WORKER_DIED")


def test_auto_block_dead_worker_noop_when_worker_alive(monkeypatch):
    monkeypatch.setattr(ka, "_REAP_DEAD_WORKERS", True)
    monkeypatch.setattr(kb, "_is_expected_worker", lambda pid: True)  # worker alive
    adapter = ka.KanbanAdapter()

    async def fake_run(args, *, expect_json=False):  # pragma: no cover - must not run
        raise AssertionError("must not block a live worker")

    adapter._run = fake_run  # type: ignore[assignment]
    row = {"status": "running"}
    assert asyncio.run(adapter._maybe_auto_block_dead_worker("t", "verify", row, _dead_detail())) is False
    assert row["status"] == "running"


def test_auto_block_dead_worker_noop_when_row_not_running(monkeypatch):
    monkeypatch.setattr(ka, "_REAP_DEAD_WORKERS", True)
    monkeypatch.setattr(kb, "_is_expected_worker", lambda pid: False)
    adapter = ka.KanbanAdapter()

    async def fake_run(args, *, expect_json=False):  # pragma: no cover
        raise AssertionError("row not running -> no block")

    adapter._run = fake_run  # type: ignore[assignment]
    row = {"status": "todo"}
    assert asyncio.run(adapter._maybe_auto_block_dead_worker("t", "verify", row, _dead_detail())) is False


def test_auto_block_dead_worker_respects_kill_switch(monkeypatch):
    monkeypatch.setattr(ka, "_REAP_DEAD_WORKERS", False)  # disabled
    monkeypatch.setattr(kb, "_is_expected_worker", lambda pid: False)
    adapter = ka.KanbanAdapter()

    async def fake_run(args, *, expect_json=False):  # pragma: no cover
        raise AssertionError("reaper disabled -> no block")

    adapter._run = fake_run  # type: ignore[assignment]
    row = {"status": "running"}
    assert asyncio.run(adapter._maybe_auto_block_dead_worker("t", "verify", row, _dead_detail())) is False
    assert row["status"] == "running"


def test_auto_block_dead_worker_swallows_cli_error(monkeypatch):
    monkeypatch.setattr(ka, "_REAP_DEAD_WORKERS", True)
    monkeypatch.setattr(kb, "_is_expected_worker", lambda pid: False)
    adapter = ka.KanbanAdapter()

    async def fake_run(args, *, expect_json=False):
        raise ka.KanbanCLIError("block failed")

    adapter._run = fake_run  # type: ignore[assignment]
    row = {"status": "running"}
    # CLI failure -> returns False, row left untouched (next poll retries)
    assert asyncio.run(adapter._maybe_auto_block_dead_worker("t", "verify", row, _dead_detail())) is False
    assert row["status"] == "running"


# ---------------------------------------------------------------------------
# _maybe_converge_noop_implement — no-op implement (empty worktree) -> ALREADY_COVERED
# ---------------------------------------------------------------------------


def _noop_adapter(monkeypatch, tmp_path, *, dirty="", unpushed="0", branch="wt/t_x", log=""):
    import worktree_bootstrap as wb
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    monkeypatch.setattr(ka, "_CONVERGE_NOOP_IMPLEMENT", True)
    monkeypatch.setattr(ka, "latest_log_session", lambda task_id, max_lines=120: log)
    monkeypatch.setattr(wb, "worktree_branch", lambda task_id: "wt/t_x")
    monkeypatch.setattr(wb, "worktree_path", lambda task_id: str(wt))
    adapter = ka.KanbanAdapter()
    calls = {"run": [], "wt": []}

    async def fake_run(args, *, expect_json=False):
        calls["run"].append(args)
        return ""

    async def fake_wt(args, *, cwd, timeout=45.0):
        calls["wt"].append(args)
        if args[:3] == ["git", "branch", "--show-current"]:
            return branch
        if args[:2] == ["git", "status"]:
            return dirty
        if args[:3] == ["git", "rev-list", "--count"]:
            return unpushed
        raise AssertionError(args)

    adapter._run = fake_run  # type: ignore[assignment]
    adapter._run_worktree_command = fake_wt  # type: ignore[assignment]
    return adapter, calls, str(wt)


def _gate_row(wt, reason="BLOCKER=GATE_BLOCKED: missing required evidence pr"):
    return {"status": "blocked", "block_reason": reason, "workspace_path": wt}


def test_converge_noop_implement_blocks_already_covered(monkeypatch, tmp_path):
    a, calls, wt = _noop_adapter(monkeypatch, tmp_path)  # clean tree, nothing unpushed
    row = _gate_row(wt)
    out = asyncio.run(a._maybe_converge_noop_implement("t_x", "implement", row, detail={}))
    assert out is True
    assert calls["run"] and calls["run"][0][0] == "block" and calls["run"][0][1] == "t_x"
    assert "ALREADY_COVERED" in calls["run"][0][2]
    assert "ALREADY_COVERED" in row["block_reason"]


def test_converge_noop_implement_noop_when_dirty(monkeypatch, tmp_path):
    # A dirty tree means there IS a diff -> unshipped-diff salvage owns it, not this.
    a, calls, wt = _noop_adapter(monkeypatch, tmp_path, dirty=" M file.py")
    row = _gate_row(wt)
    assert asyncio.run(a._maybe_converge_noop_implement("t_x", "implement", row, detail={})) is False
    assert not calls["run"]


def test_converge_noop_implement_noop_when_unpushed_commits(monkeypatch, tmp_path):
    a, calls, wt = _noop_adapter(monkeypatch, tmp_path, unpushed="2")
    row = _gate_row(wt)
    assert asyncio.run(a._maybe_converge_noop_implement("t_x", "implement", row, detail={})) is False
    assert not calls["run"]


def test_converge_noop_implement_ignores_real_blockers(monkeypatch, tmp_path):
    a, calls, wt = _noop_adapter(monkeypatch, tmp_path)
    row = _gate_row(wt, reason="BLOCKER=TEST_ENVIRONMENT: import error")
    assert asyncio.run(a._maybe_converge_noop_implement("t_x", "implement", row, detail={})) is False
    assert not calls["run"]


def test_converge_noop_implement_skips_when_pr_in_log(monkeypatch, tmp_path):
    a, calls, wt = _noop_adapter(monkeypatch, tmp_path, log="PR_URL=https://x/pull/1")
    row = _gate_row(wt)
    assert asyncio.run(a._maybe_converge_noop_implement("t_x", "implement", row, detail={})) is False
    assert not calls["run"]


def test_converge_noop_implement_respects_kill_switch(monkeypatch, tmp_path):
    a, calls, wt = _noop_adapter(monkeypatch, tmp_path)
    monkeypatch.setattr(ka, "_CONVERGE_NOOP_IMPLEMENT", False)
    row = _gate_row(wt)
    assert asyncio.run(a._maybe_converge_noop_implement("t_x", "implement", row, detail={})) is False
    assert not calls["run"]


def test_converge_noop_implement_noop_when_not_blocked(monkeypatch, tmp_path):
    a, calls, wt = _noop_adapter(monkeypatch, tmp_path)
    row = {"status": "running", "block_reason": "", "workspace_path": wt}
    assert asyncio.run(a._maybe_converge_noop_implement("t_x", "implement", row, detail={})) is False
    assert asyncio.run(a._maybe_converge_noop_implement("t_x", "verify", _gate_row(wt), detail={})) is False
    assert not calls["run"]


@pytest.mark.parametrize("branch", ["main", "master", "wt/other_task"])
def test_converge_noop_implement_noop_wrong_branch(monkeypatch, tmp_path, branch):
    # Safety gate: never converge on main/master or a different task's worktree
    # branch — a regression here would converge on the wrong branch.
    a, calls, wt = _noop_adapter(monkeypatch, tmp_path, branch=branch)
    row = _gate_row(wt)
    assert asyncio.run(a._maybe_converge_noop_implement("t_x", "implement", row, detail={})) is False
    assert not calls["run"]


def test_converge_noop_implement_ignores_pr_substring_in_other_words(monkeypatch, tmp_path):
    # "pr" must match as a whole word: a missing-evidence reason naming e.g.
    # "prior_result" must NOT trip the missing-PR convergence.
    a, calls, wt = _noop_adapter(monkeypatch, tmp_path)
    row = _gate_row(wt, reason="BLOCKER=GATE_BLOCKED: missing required evidence prior_result")
    assert asyncio.run(a._maybe_converge_noop_implement("t_x", "implement", row, detail={})) is False
    assert not calls["run"]
