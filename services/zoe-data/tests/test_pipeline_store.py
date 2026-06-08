"""Tests for pipeline JSONL store and sync."""

import json

import pytest

import pipeline_store as store
from pipeline_evidence import EvidenceItem, PipelineState, with_evidence


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    path = tmp_path / "runs.jsonl"
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(path))
    return path


def test_bootstrap_and_reload(isolated_store):
    import asyncio

    state = asyncio.run(store.bootstrap_state("multica:abc"))
    assert state.task_ref == "multica:abc"
    assert state.phase == "implement"

    reloaded = store.load_latest_state("multica:abc")
    assert reloaded is not None
    assert reloaded.phase == "implement"


def test_stale_save_preserves_concurrently_written_evidence(isolated_store):
    state = PipelineState(
        task_ref="multica:concurrent-evidence",
        phase="review",
        status="running",
    )
    store.save_state(state, event="effect_requested")
    stale = store.load_latest_state(state.task_ref)
    assert stale is not None

    current_state = store.load_latest_state(state.task_ref)
    assert current_state is not None
    current = with_evidence(
        current_state,
        EvidenceItem(
            kind="human",
            summary="mechanical review approval",
            passed=True,
            metadata={"source": "command", "phase": "review"},
        ),
    )
    store.save_state(current, event="evidence_human")
    store.save_state(
        stale,
        event="gate_blocked",
        extra={"missing": ["human"]},
        allow_stale_evidence_merge=True,
    )

    reloaded = store.load_latest_state(state.task_ref)
    assert reloaded is not None
    assert any(
        item.kind == "human"
        and item.passed is True
        and item.metadata.get("source") == "command"
        for item in reloaded.evidence
    )
    assert reloaded.journal_revision == 3


def test_stale_save_cannot_regress_pipeline_phase(isolated_store):
    initial = store.save_state(
        PipelineState(
            task_ref="multica:concurrent-transition",
            phase="review",
            status="running",
        ),
        event="effect_requested",
    )
    stale = initial.model_copy(deep=True)

    advanced = initial.model_copy(update={"phase": "closeout", "status": "todo"})
    advanced = store.save_state(advanced, event="transition")
    persisted = store.save_state(
        stale,
        event="gate_blocked",
        allow_stale_evidence_merge=True,
    )

    assert advanced.journal_revision == 2
    assert persisted.phase == "closeout"
    assert persisted.status == "todo"
    assert persisted.journal_revision == 3


def test_stale_mutation_raises_conflict(isolated_store):
    initial = store.save_state(
        PipelineState(task_ref="multica:stale-mutation", status="blocked"),
        event="blocked",
    )
    store.save_state(
        initial.model_copy(update={"block_classification": "scope_split_required"}),
        event="classified",
    )

    with pytest.raises(store.PipelineStateConflict, match="stale pipeline state"):
        store.save_state(
            initial.model_copy(update={"status": "todo"}),
            event="operator_resumed",
        )


def test_resume_pipeline_retries_after_conflict(isolated_store, monkeypatch):
    state = PipelineState(task_ref="multica:resume-race", status="blocked")
    store.save_state(state, event="blocked")
    original_save = store.save_state
    calls = 0

    def conflict_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise store.PipelineStateConflict("simulated race")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(store, "save_state", conflict_once)
    resumed = store.resume_pipeline("multica:resume-race")

    assert calls == 2
    assert resumed.status == "todo"


def test_concurrent_evidence_merge_deduplicates_created_at_only(isolated_store):
    initial = store.save_state(
        PipelineState(task_ref="multica:evidence-dedup"),
        event="bootstrap",
    )
    first = with_evidence(
        initial,
        EvidenceItem(
            kind="human",
            summary="review approved",
            passed=True,
            metadata={"source": "command", "phase": "review"},
        ),
    )
    store.save_state(first, event="evidence_human")
    duplicate = first.model_copy(
        update={
            "evidence": [
                EvidenceItem(
                    kind="human",
                    summary="review approved",
                    passed=True,
                    metadata={"source": "command", "phase": "review"},
                )
            ]
        }
    )
    store.save_state(
        duplicate,
        event="evidence_human",
        allow_stale_evidence_merge=True,
    )

    reloaded = store.load_latest_state(initial.task_ref)
    assert reloaded is not None
    assert len([item for item in reloaded.evidence if item.kind == "human"]) == 1


def test_non_stale_transition_can_clear_prior_evidence(isolated_store):
    initial = store.save_state(
        PipelineState(
            task_ref="multica:revision",
            phase="review",
            status="running",
            evidence=[
                EvidenceItem(
                    kind="human",
                    summary="review evidence that must be re-earned",
                    passed=False,
                )
            ],
        ),
        event="review_blocked",
    )
    revised = store.transition(initial, "request_changes", reason="fix requested")
    saved = store.save_state(revised, event="transition")

    assert saved.phase == "implement"
    assert saved.evidence == []
    reloaded = store.load_latest_state(initial.task_ref)
    assert reloaded is not None
    assert reloaded.evidence == []


@pytest.mark.asyncio
async def test_bootstrap_returns_concurrently_created_state(isolated_store, monkeypatch):
    existing = PipelineState(
        task_ref="multica:bootstrap-race",
        phase="scout",
        journal_revision=1,
    )
    load_calls = 0

    def racing_load(_task_ref):
        nonlocal load_calls
        load_calls += 1
        return None if load_calls == 1 else existing

    def conflicting_save(*_args, **_kwargs):
        raise store.PipelineStateConflict("simulated first-write race")

    monkeypatch.setattr(store, "load_latest_state", racing_load)
    monkeypatch.setattr(store, "save_state", conflicting_save)

    result = await store.bootstrap_state("multica:bootstrap-race")

    assert result is existing


def test_pipeline_summary_reports_missing_evidence(isolated_store):
    state = PipelineState(task_ref="multica:1", phase="implement", status="running")
    summary = store.pipeline_summary(state)
    assert summary["tracked"] is True
    assert summary["evidence_ok"] is False
    assert "tool" in summary["missing_evidence"]


def test_pipeline_summary_split_packet_alone_is_not_terminal(isolated_store):
    state = PipelineState(
        task_ref="multica:packet-only",
        phase="implement",
        status="blocked",
        split_packet={"kind": "scope_split_required"},
    )
    summary = store.pipeline_summary(state)
    assert summary["terminal_block"] is False
    assert summary["needs_split"] is False


def test_pipeline_summary_needs_split_requires_blocked_status(isolated_store):
    state = PipelineState(
        task_ref="multica:resumed",
        phase="implement",
        status="todo",
        block_classification="scope_split_required",
        split_packet={"kind": "scope_split_required"},
    )
    summary = store.pipeline_summary(state)
    assert summary["terminal_block"] is False
    assert summary["needs_split"] is False


def test_resume_pipeline_can_reset_false_duplicate_fingerprint(isolated_store):
    from pipeline_evidence import TransitionRecord

    state = PipelineState(
        task_ref="multica:false-duplicate",
        phase="implement",
        status="blocked",
        last_block_fingerprint="abc123",
        repeated_block_count=2,
        history=[
            TransitionRecord(
                from_phase="implement",
                to_phase="implement",
                outcome="block",
                reason="fingerprint_abort:abc123",
            )
        ],
    )
    store.save_state(state, event="fingerprint_abort")

    resumed = store.resume_pipeline(
        "multica:false-duplicate",
        reason="duplicate poll guard deployed",
        reset_fingerprint=True,
    )

    assert resumed.status == "todo"
    assert resumed.last_block_fingerprint is None
    assert resumed.repeated_block_count == 0
    assert not any(
        (record.reason or "").startswith("fingerprint_abort:")
        for record in resumed.history
    )
    assert store.pipeline_summary(resumed)["terminal_block"] is False


def test_skip_blocked_implementation_moves_to_verify(isolated_store):
    state = PipelineState(
        task_ref="multica:no-code",
        phase="implement",
        status="blocked",
        last_block_fingerprint="old",
        repeated_block_count=1,
        block_classification="scope_split_required",
        split_packet={"kind": "scope_split_required"},
        evidence=[
            EvidenceItem(
                kind="tool",
                summary="scout proved merged work already satisfies acceptance",
                passed=True,
            )
        ],
    )
    store.save_state(state, event="blocked")

    skipped = store.skip_blocked_implementation(
        "multica:no-code",
        reason="scout confirmed acceptance is already met by merged PRs",
    )

    assert skipped.phase == "verify"
    assert skipped.status == "todo"
    assert skipped.evidence_profile == "audit"
    assert skipped.last_block_fingerprint is None
    assert skipped.repeated_block_count == 0
    assert skipped.block_classification is None
    assert skipped.split_packet is None
    assert "operator_skipped_implementation" in isolated_store.read_text(encoding="utf-8")


def test_skip_blocked_implementation_requires_tool_evidence(isolated_store):
    state = PipelineState(
        task_ref="multica:no-evidence",
        phase="implement",
        status="blocked",
    )
    store.save_state(state, event="blocked")

    with pytest.raises(ValueError, match="lacks passed scout/tool evidence"):
        store.skip_blocked_implementation(
            "multica:no-evidence",
            reason="operator attempted an unsupported skip",
        )


@pytest.mark.asyncio
async def test_sync_pipeline_advances_on_complete_handoff(isolated_store):
    await store.bootstrap_state("multica:sync")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "TOOLS_USED=graphify\nTESTS=pytest -q pass\nPR_URL=https://github.com/o/r/pull/9",
            "comments": [],
        }

    phases = {"implement": {"id": "t1", "status": "done"}}
    state = await store.sync_pipeline_from_chain("multica:sync", phases, fetch_detail)
    assert state.phase == "verify"
    assert state.status == "todo"

    lines = isolated_store.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 2
    last = json.loads(lines[-1])
    assert last["event"] in {"transition", "gate_blocked"}


@pytest.mark.asyncio
async def test_sync_pipeline_advances_with_live_run_metadata_recovery(isolated_store):
    await store.bootstrap_state("multica:sync-live-metadata")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": (
                "Fixed timing-attack vulnerability in auth.py token comparison — "
                "replaced vulnerable == with hmac.compare_digest."
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
            "comments": [],
        }

    phases = {"implement": {"id": "t_live", "status": "archived"}}
    state = await store.sync_pipeline_from_chain(
        "multica:sync-live-metadata",
        phases,
        fetch_detail,
    )

    assert state.phase == "verify"
    assert state.status == "todo"
    assert any(item.kind == "tool" and item.passed is True for item in state.evidence)
    assert any(item.kind == "test" and item.passed is True for item in state.evidence)
    assert any(
        item.kind == "pr"
        and item.artifact == "https://github.com/jason-easyazz/zoe-ai-assistant/pull/213"
        for item in state.evidence
    )


@pytest.mark.asyncio
async def test_sync_pipeline_retries_a_concurrent_transition_write(
    isolated_store, monkeypatch
):
    await store.bootstrap_state("multica:sync-race")
    original_save = store.save_state
    transition_calls = 0

    def conflict_first_transition(state, *, event, **kwargs):
        nonlocal transition_calls
        if event == "transition":
            transition_calls += 1
            if transition_calls == 1:
                raise store.PipelineStateConflict("simulated command race")
        return original_save(state, event=event, **kwargs)

    monkeypatch.setattr(store, "save_state", conflict_first_transition)

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": (
                "TOOLS_USED=graphify\n"
                "TESTS=pytest -q pass\n"
                "PR_URL=https://github.com/o/r/pull/9"
            ),
            "comments": [],
        }

    state = await store.sync_pipeline_from_chain(
        "multica:sync-race",
        {"implement": {"id": "t1", "status": "done"}},
        fetch_detail,
    )

    assert transition_calls == 2
    assert state.phase == "verify"
    assert state.status == "todo"


@pytest.mark.asyncio
async def test_sync_pipeline_defers_after_sustained_journal_conflicts(
    isolated_store, monkeypatch
):
    initial = await store.bootstrap_state("multica:sync-contention")
    original_save = store.save_state
    transition_calls = 0

    def always_conflict_transition(state, *, event, **kwargs):
        nonlocal transition_calls
        if event == "transition":
            transition_calls += 1
            raise store.PipelineStateConflict("sustained command writes")
        return original_save(state, event=event, **kwargs)

    monkeypatch.setattr(store, "save_state", always_conflict_transition)

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": (
                "TOOLS_USED=graphify\n"
                "TESTS=pytest -q pass\n"
                "PR_URL=https://github.com/o/r/pull/9"
            ),
            "comments": [],
        }

    state = await store.sync_pipeline_from_chain(
        "multica:sync-contention",
        {"implement": {"id": "t1", "status": "done"}},
        fetch_detail,
    )

    assert transition_calls == 3
    assert state == initial


@pytest.mark.asyncio
async def test_sync_pipeline_skips_implementation_when_scout_marks_it_unneeded(isolated_store):
    await store.bootstrap_state("multica:already-done", start_phase="scout")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "TOOLS_USED=graphify",
            "comments": [],
            "runs": [
                {
                    "metadata": {
                        "IMPLEMENTATION_REQUIRED": "false",
                        "SCOUT_SUMMARY": "Acceptance is already met by merged PR #173.",
                    }
                }
            ],
        }

    phases = {"scout": {"id": "t_scout", "status": "done"}}
    state = await store.sync_pipeline_from_chain(
        "multica:already-done",
        phases,
        fetch_detail,
        start_phase="scout",
    )

    assert state.phase == "verify"
    assert state.status == "todo"
    assert state.evidence_profile == "audit"
    assert state.history[-1].outcome == "skip_implementation"
    assert state.history[-1].reason == "scout proved implementation not required"


@pytest.mark.asyncio
async def test_sync_pipeline_keeps_normal_implementation_route(isolated_store):
    await store.bootstrap_state("multica:needs-code", start_phase="scout")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "TOOLS_USED=graphify",
            "comments": [],
            "runs": [{"metadata": {"IMPLEMENTATION_REQUIRED": "true"}}],
        }

    phases = {"scout": {"id": "t_scout", "status": "done"}}
    state = await store.sync_pipeline_from_chain(
        "multica:needs-code",
        phases,
        fetch_detail,
        start_phase="scout",
    )

    assert state.phase == "implement"
    assert state.status == "todo"
    assert state.history[-1].outcome == "complete"


@pytest.mark.asyncio
async def test_sync_pipeline_gate_blocks_verify_without_test(isolated_store):
    await store.bootstrap_state("multica:verify-gate")

    async def fetch_detail(task_id: str):
        if task_id == "t_impl":
            return {
                "latest_summary": "TOOLS_USED=graphify\nPR_URL=https://github.com/o/r/pull/1",
                "comments": [],
            }
        return {"latest_summary": "VALIDATORS=pass", "comments": []}

    phases = {
        "implement": {"id": "t_impl", "status": "done"},
        "verify": {"id": "t_verify", "status": "done"},
    }
    state = await store.sync_pipeline_from_chain("multica:verify-gate", phases, fetch_detail)
    assert state.phase == "verify"
    assert any("gate_blocked" in line for line in isolated_store.read_text(encoding="utf-8").splitlines())


@pytest.mark.asyncio
async def test_sync_pipeline_audit_only_verify_skips_test_gate(isolated_store):
    await store.bootstrap_state("multica:audit-gate")

    async def fetch_detail(task_id: str):
        if task_id == "t_impl":
            return {
                "latest_summary": "AUDIT_ONLY=1\nTOOLS_USED=graphify\nSUMMARY=audit complete",
                "comments": [],
            }
        return {"latest_summary": "VALIDATORS=validate_structure pass", "comments": []}

    phases = {
        "implement": {"id": "t_impl", "status": "done"},
        "verify": {"id": "t_verify", "status": "done"},
    }
    state = await store.sync_pipeline_from_chain("multica:audit-gate", phases, fetch_detail)
    assert state.phase == "review"
    assert state.evidence_profile == "audit"
    assert not any("gate_blocked" in line for line in isolated_store.read_text(encoding="utf-8").splitlines())




@pytest.mark.asyncio
async def test_sync_pipeline_recovers_audit_protocol_only_block(isolated_store):
    await store.bootstrap_state(
        "multica:audit-protocol",
        issue={"description": "evidence_profile: audit"},
    )

    async def fetch_detail(_task_id: str):
        return {
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

    phases = {"implement": {"id": "t1", "status": "blocked", "block_reason": "implement blocked"}}
    state = await store.sync_pipeline_from_chain("multica:audit-protocol", phases, fetch_detail)

    assert state.phase == "verify"
    assert state.status == "todo"
    assert any(
        item.kind == "tool" and item.metadata.get("source") == "audit_protocol_recovery"
        for item in state.evidence
    )
    assert "audit_protocol_recovered" in isolated_store.read_text(encoding="utf-8")




@pytest.mark.asyncio
async def test_sync_pipeline_does_not_recover_mixed_protocol_and_real_block(isolated_store):
    await store.bootstrap_state(
        "multica:audit-mixed-block",
        issue={"description": "evidence_profile: audit"},
    )

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "",
            "comments": [],
            "events": [{"kind": "protocol_violation", "payload": {"exit_code": 0}}],
            "runs": [
                {
                    "status": "crashed",
                    "outcome": "crashed",
                    "error": "dirty tree prevented evidence collection",
                }
            ],
        }

    phases = {"implement": {"id": "t1", "status": "blocked", "block_reason": "dirty tree"}}
    state = await store.sync_pipeline_from_chain("multica:audit-mixed-block", phases, fetch_detail)

    assert state.phase == "implement"
    assert state.status == "blocked"
    assert "audit_protocol_recovered" not in isolated_store.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_sync_pipeline_blocked_poll_is_idempotent(isolated_store):
    await store.bootstrap_state("multica:fp")

    async def fetch_detail(_task_id: str):
        return {"latest_summary": "BLOCKER=WORKTREE_NOT_READY", "comments": []}

    phases = {"implement": {"id": "t1", "status": "blocked", "block_reason": "WORKTREE_NOT_READY"}}
    state = await store.sync_pipeline_from_chain("multica:fp", phases, fetch_detail)
    assert state.status == "blocked"
    assert state.repeated_block_count == 1

    state = await store.sync_pipeline_from_chain("multica:fp", phases, fetch_detail)
    assert state.status == "blocked"
    assert state.repeated_block_count == 1
    assert "fingerprint_abort" not in isolated_store.read_text(encoding="utf-8")

@pytest.mark.asyncio
async def test_sync_pipeline_fingerprint_abort_after_two_real_attempts(isolated_store):
    await store.bootstrap_state("multica:fp-repeat")

    async def fetch_detail(_task_id: str):
        return {"latest_summary": "BLOCKER=WORKTREE_NOT_READY", "comments": []}

    phases = {"implement": {"id": "t1", "status": "blocked", "block_reason": "WORKTREE_NOT_READY"}}
    await store.sync_pipeline_from_chain("multica:fp-repeat", phases, fetch_detail)
    resumed = store.resume_pipeline("multica:fp-repeat", reason="retry after repair")
    assert resumed.status == "todo"
    assert resumed.repeated_block_count == 1

    state = await store.sync_pipeline_from_chain("multica:fp-repeat", phases, fetch_detail)
    assert any("fingerprint_abort" in (rec.reason or "") for rec in state.history)
    assert any("fingerprint_abort" in line for line in isolated_store.read_text(encoding="utf-8").splitlines())


@pytest.mark.asyncio
async def test_sync_pipeline_fingerprint_abort_creates_split_packet_for_protocol(isolated_store):
    await store.bootstrap_state("multica:hard")

    async def fetch_detail(_task_id: str):
        return {"latest_summary": "BLOCKER=PROTOCOL_VIOLATION", "comments": []}

    phases = {
        "implement": {
            "id": "t1",
            "status": "blocked",
            "block_reason": "PROTOCOL_VIOLATION",
        }
    }
    await store.sync_pipeline_from_chain("multica:hard", phases, fetch_detail)
    resumed = store.resume_pipeline("multica:hard", reason="retry after prompt repair")
    assert resumed.repeated_block_count == 1
    state = await store.sync_pipeline_from_chain("multica:hard", phases, fetch_detail)
    summary = store.pipeline_summary(state)

    assert state.block_classification == "scope_split_required"
    assert summary["needs_split"] is True
    assert state.split_packet["parent_task_ref"] == "multica:hard"
    assert state.split_packet["kind"] == "scope_split_required"


@pytest.mark.asyncio
async def test_sync_pipeline_explicit_scout_split_packet_blocks_terminal(isolated_store):
    await store.bootstrap_state("multica:scout-split", start_phase="scout")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": (
                "BLOCKER=SCOPE_SPLIT_REQUIRED: parent has too many surfaces\n"
                'NEEDS_SPLIT=1\nSPLIT_PACKET={"child_issue_template":{"title":"ZOE-5288: card_service base"}}'
            ),
            "comments": [],
        }

    phases = {
        "scout": {
            "id": "t1",
            "status": "blocked",
            "block_reason": "SCOPE_SPLIT_REQUIRED: parent has too many surfaces",
        }
    }
    state = await store.sync_pipeline_from_chain("multica:scout-split", phases, fetch_detail)

    assert state.status == "blocked"
    assert state.block_classification == "scope_split_required"
    assert state.split_packet["blocked_phase"] == "scout"
    assert state.split_packet["child_issue_template"]["title"] == "ZOE-5288: card_service base"
    assert "ignored_scope_split_request" not in isolated_store.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_sync_pipeline_explicit_split_packet_blocks_terminal(isolated_store):
    await store.bootstrap_state("multica:explicit")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": (
                "BLOCKER=SCOPE_SPLIT_REQUIRED: too broad\n"
                'NEEDS_SPLIT=1\nSPLIT_PACKET={"child_issue_template":{"title":"ZOE-5287: isolate contract parser"}}'
            ),
            "comments": [],
        }

    phases = {
        "implement": {
            "id": "t1",
            "status": "blocked",
            "block_reason": "SCOPE_SPLIT_REQUIRED: too broad",
        }
    }
    state = await store.sync_pipeline_from_chain("multica:explicit", phases, fetch_detail)

    assert state.status == "blocked"
    assert state.block_classification == "scope_split_required"
    assert state.split_packet["child_issue_template"]["title"] == "ZOE-5287: isolate contract parser"
    assert "scope_split_required" in isolated_store.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_sync_pipeline_non_implement_split_request_is_visible(isolated_store):
    await store.bootstrap_state("multica:verify-split", start_phase="verify")

    async def fetch_detail(_task_id: str):
        return {"latest_summary": "NEEDS_SPLIT=1\nSPLIT_PACKET={\"reason\":\"verify found broad scope\"}", "comments": []}

    phases = {
        "verify": {
            "id": "t1",
            "status": "blocked",
            "block_reason": "SCOPE_SPLIT_REQUIRED: verify found broad scope",
        }
    }
    state = await store.sync_pipeline_from_chain("multica:verify-split", phases, fetch_detail)

    assert state.block_classification is None
    assert "ignored_scope_split_request" in isolated_store.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_sync_pipeline_blocked_records_reason_in_history(isolated_store):
    await store.bootstrap_state("multica:block-reason")

    async def fetch_detail(_task_id: str):
        return {"latest_summary": "BLOCKER=dirty tree", "comments": []}

    phases = {"implement": {"id": "t1", "status": "blocked", "block_reason": "dirty tree"}}
    state = await store.sync_pipeline_from_chain("multica:block-reason", phases, fetch_detail)
    assert state.status == "blocked"
    assert state.history[-1].reason == "dirty tree"
    assert store.pipeline_summary(state)["block_reason"] == "dirty tree"

    last = json.loads(isolated_store.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["meta"]["block_reason"] == "dirty tree"


@pytest.mark.asyncio
async def test_sync_pipeline_auto_validators_on_implement_done(isolated_store, monkeypatch):
    await store.bootstrap_state("multica:val")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "TOOLS_USED=graphify\nTESTS=pytest -q pass\nPR_URL=https://github.com/o/r/pull/9",
            "comments": [],
        }

    from pipeline_validators import ValidatorRunResult

    monkeypatch.setattr(
        "pipeline_validators.run_repo_validators",
        lambda: ValidatorRunResult(exit_code=0, summary="ok", content_hash="abc123", passed=True),
    )

    phases = {"implement": {"id": "t1", "status": "done"}}
    state = await store.sync_pipeline_from_chain("multica:val", phases, fetch_detail)
    assert state.phase == "verify"
    assert any(item.kind == "validator" and item.metadata.get("phase") == "implement" for item in state.evidence)


def test_pipeline_summary_surfaces_latest_retro_followup(isolated_store):
    state = PipelineState(task_ref="multica:retro", phase="retro", status="done")
    state = with_evidence(
        state,
        EvidenceItem(
            kind="log",
            summary="retro captured",
            passed=True,
            metadata={"phase": "retro", "follow_up": {"title": "Tighten retry guard", "description": "Add coverage"}},
        ),
    )

    summary = store.pipeline_summary(state)

    assert summary["retro_followup"]["title"] == "Tighten retry guard"


def test_pipeline_summary_ignores_non_retro_followup_metadata(isolated_store):
    state = PipelineState(task_ref="multica:non-retro", phase="verify", status="running")
    state = with_evidence(
        state,
        EvidenceItem(
            kind="log",
            summary="not retro",
            passed=True,
            metadata={"phase": "verify", "follow_up": {"title": "Ignore me"}},
        ),
    )

    summary = store.pipeline_summary(state)

    assert summary["retro_followup"] is None
