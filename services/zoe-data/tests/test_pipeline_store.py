"""Tests for pipeline JSONL store and sync."""

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

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


def test_complete_pipeline_after_external_merge_journals_done(isolated_store):
    state = PipelineState(task_ref="multica:merged", phase="implement", status="blocked")
    state = with_evidence(
        state,
        EvidenceItem(
            kind="greptile",
            summary="5/5",
            passed=True,
            metadata={"source": "pr_maintenance", "phase": "closeout", "merge_sha": "deadbeef"},
        ),
        EvidenceItem(
            kind="log",
            summary="PR maintenance recorded merged PR",
            passed=True,
            metadata={"source": "pr_maintenance", "phase": "retro", "merge_sha": "deadbeef"},
        ),
    )
    store.save_state(state, event="blocked")

    completed = store.complete_pipeline_after_external_merge(
        "multica:merged",
        pr_url="https://github.com/o/r/pull/9",
        merge_sha="deadbeef",
        greptile_status="5/5",
    )

    assert completed is not None
    assert completed.phase == "retro"
    assert completed.status == "done"
    assert completed.block_classification is None
    assert completed.split_packet is None
    assert completed.history[-1].from_phase == "implement"
    assert completed.history[-1].to_phase == "retro"
    assert any(item.kind == "pr" and item.artifact == "https://github.com/o/r/pull/9" for item in completed.evidence)
    assert any(item.kind == "greptile" and item.metadata.get("merge_sha") == "deadbeef" for item in completed.evidence)
    assert any(item.kind == "log" and item.metadata.get("phase") == "retro" for item in completed.evidence)
    assert sum(
        1
        for item in completed.evidence
        if item.kind == "greptile" and item.metadata.get("source") == "pr_maintenance"
    ) == 1
    assert sum(
        1
        for item in completed.evidence
        if item.kind == "log" and item.metadata.get("source") == "pr_maintenance"
    ) == 1
    last = json.loads(isolated_store.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["event"] == "external_merge_completed"


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
async def test_skip_blocked_code_implementation_allows_validator_only_verify(isolated_store):
    from pipeline_evidence import TransitionRecord

    state = PipelineState(
        task_ref="multica:code-gate-no-code-skip",
        phase="implement",
        status="blocked",
        evidence_profile="code",
        history=[
            TransitionRecord(
                from_phase="implement",
                to_phase="implement",
                outcome="block",
                reason="GATE_BLOCKED: missing required evidence pr",
            )
        ],
        evidence=[
            EvidenceItem(
                kind="tool",
                summary="scout proved acceptance is already satisfied by merged work",
                passed=True,
            )
        ],
    )
    store.save_state(state, event="gate_blocked", extra={"missing": ["pr"]})

    skipped = store.skip_blocked_implementation(
        "multica:code-gate-no-code-skip",
        reason="operator confirmed no code change is needed",
    )

    assert skipped.phase == "verify"
    assert skipped.status == "todo"
    assert skipped.evidence_profile == "audit"

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "VALIDATORS=validate_structure.py passed",
            "comments": [],
        }

    verified = await store.sync_pipeline_from_chain(
        "multica:code-gate-no-code-skip",
        {"verify": {"id": "t_verify", "status": "done"}},
        fetch_detail,
    )

    assert verified.phase == "review"
    assert verified.status == "todo"
    assert verified.evidence_profile == "audit"
    assert verified.history[-1].outcome == "complete"
    assert verified.history[-1].reason != "GATE_BLOCKED: missing required evidence test"


@pytest.mark.asyncio
async def test_sync_pipeline_advances_on_complete_handoff(isolated_store):
    await store.bootstrap_state("multica:sync")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "TOOLS_USED=codebase-memory\nTESTS=pytest -q pass\nPR_URL=https://github.com/o/r/pull/9",
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
async def test_sync_pipeline_skips_already_covered_implement_block(isolated_store):
    await store.bootstrap_state(
        "multica:already-covered",
        issue={"metadata": {"evidence_profile": "code"}},
    )
    phases = {
        "implement": {
            "id": "t_impl",
            "status": "blocked",
            "block_reason": "ALREADY_COVERED: focused harness test passed before edit",
        }
    }

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "BLOCKER=ALREADY_COVERED: focused harness test passed before edit",
            "comments": [],
        }

    state = await store.sync_pipeline_from_chain("multica:already-covered", phases, fetch_detail)

    assert state.phase == "verify"
    assert state.status == "todo"
    assert state.evidence_profile == "audit"
    assert state.last_block_fingerprint is None
    assert state.repeated_block_count == 0
    assert state.history[-1].outcome == "skip_implementation"
    events = [
        json.loads(line)["event"]
        for line in isolated_store.read_text(encoding="utf-8").strip().splitlines()
    ]
    assert "already_covered_implementation_skipped" in events


@pytest.mark.asyncio
async def test_sync_pipeline_ignores_older_later_phase_block_after_retry(isolated_store):
    await store.bootstrap_state(
        "multica:already-covered-retry",
        issue={"metadata": {"evidence_profile": "code"}},
    )
    phases = {
        "verify": {
            "id": "t_old_verify",
            "status": "blocked",
            "created_at": 100.0,
        },
        "implement": {
            "id": "t_new_impl",
            "status": "blocked",
            "created_at": 200.0,
            "block_reason": None,
        },
    }

    async def fetch_detail(task_id: str):
        if task_id == "t_new_impl":
            return {
                "latest_summary": "BLOCKER=ALREADY_COVERED: focused tests passed; no PR required",
                "comments": [],
            }
        return {
            "latest_summary": "BLOCKER=WORKTREE_PATH_VIOLATION: stale verify from previous attempt",
            "comments": [],
        }

    state = await store.sync_pipeline_from_chain(
        "multica:already-covered-retry",
        phases,
        fetch_detail,
    )

    assert state.phase == "verify"
    assert state.status == "todo"
    assert state.history[-1].outcome == "skip_implementation"
    events = [
        json.loads(line)["event"]
        for line in isolated_store.read_text(encoding="utf-8").strip().splitlines()
    ]
    assert "already_covered_implementation_skipped" in events
    assert "verification_failed" not in events


@pytest.mark.asyncio
async def test_sync_pipeline_accepts_audit_closeout_run_summary_log(isolated_store):
    state = store.PipelineState(
        task_ref="multica:audit-closeout",
        phase="closeout",
        status="running",
        evidence_profile="audit",
        attempts={"closeout": 1},
    )
    store.save_state(state, event="effect_requested")
    phases = {
        "closeout": {
            "id": "t_closeout",
            "status": "done",
        }
    }

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": None,
            "comments": [],
            "runs": [
                {
                    "summary": "Completed the audit closeout using recorded pipeline evidence.",
                    "metadata": {"result": "audit path executed"},
                }
            ],
            "log_tail": "PR_URL=<url>\nSUMMARY=template placeholder, not a real PR",
        }

    state = await store.sync_pipeline_from_chain(
        "multica:audit-closeout",
        phases,
        fetch_detail,
    )

    assert state.phase == "retro"
    assert state.status == "todo"
    assert any(item.kind == "log" and item.passed is True for item in state.evidence)
    assert not any(item.kind == "pr" for item in state.evidence)


@pytest.mark.asyncio
async def test_sync_pipeline_blocks_code_implement_without_pr(isolated_store):
    await store.bootstrap_state("multica:no-pr-code", issue={"metadata": {"evidence_profile": "code"}})

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "TOOLS_USED=codebase-memory\nTESTS=validate_structure.py passed\nSUMMARY=investigated only",
            "comments": [],
        }

    phases = {"implement": {"id": "t_impl", "status": "done"}}
    state = await store.sync_pipeline_from_chain("multica:no-pr-code", phases, fetch_detail)

    assert state.phase == "implement"
    assert state.status == "blocked"
    assert state.history[-1].reason == "GATE_BLOCKED: missing required evidence pr"
    assert store.pipeline_summary(state)["missing_evidence"] == ["pr"]
    last = json.loads(isolated_store.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["event"] == "gate_blocked"
    assert last["meta"]["row_phase"] == "implement"
    assert last["meta"]["missing"] == ["pr"]


@pytest.mark.asyncio
async def test_sync_pipeline_blocks_default_implement_without_pr(isolated_store):
    await store.bootstrap_state("multica:no-pr-default")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "TOOLS_USED=codebase-memory\nSUMMARY=investigated only",
            "comments": [],
        }

    state = await store.sync_pipeline_from_chain(
        "multica:no-pr-default",
        {"implement": {"id": "t_impl", "status": "done"}},
        fetch_detail,
    )

    assert state.evidence_profile == "default"
    assert state.phase == "implement"
    assert state.status == "blocked"
    assert state.history[-1].reason == "GATE_BLOCKED: missing required evidence pr"
    assert store.pipeline_summary(state)["missing_evidence"] == ["pr"]


@pytest.mark.asyncio
async def test_sync_pipeline_reports_validator_hash_mismatch_gate(isolated_store):
    state = PipelineState(
        task_ref="multica:verify-hash-mismatch",
        phase="verify",
        status="todo",
        evidence=[
            EvidenceItem(kind="test", summary="pytest passed", passed=True),
            EvidenceItem(
                kind="validator",
                summary="implement validator",
                passed=True,
                content_hash="a" * 64,
                metadata={"phase": "implement", "source": "handoff"},
            ),
            EvidenceItem(
                kind="validator",
                summary="verify validator",
                passed=True,
                content_hash="b" * 64,
                metadata={"phase": "verify", "source": "handoff"},
            ),
        ],
    )
    store.save_state(state, event="verify_ready")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "TESTS=pytest passed\nVALIDATORS=validate_structure.py passed",
            "comments": [],
        }

    state = await store.sync_pipeline_from_chain(
        "multica:verify-hash-mismatch",
        {"verify": {"id": "t_verify", "status": "done"}},
        fetch_detail,
    )

    assert state.phase == "verify"
    assert state.status == "blocked"
    assert state.history[-1].reason == "GATE_BLOCKED: validator hash mismatch"
    last = json.loads(isolated_store.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert last["event"] == "gate_blocked"
    assert last["meta"]["missing"] == []
    assert last["meta"]["validator_hash_mismatch"] is True


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
    assert "zoe-engineering" in store._PHASE_SKILLS["implement"]
    assert any(
        item.kind == "tool"
        and item.passed is True
        and item.metadata.get("source") == "skills"
        for item in state.evidence
    )
    assert any(item.kind == "test" and item.passed is True for item in state.evidence)
    assert any(
        item.kind == "pr"
        and item.artifact == "https://github.com/jason-easyazz/zoe-ai-assistant/pull/213"
        for item in state.evidence
    )


def test_phase_skills_implement_only_credits_pinned_skills():
    # _PHASE_SKILLS feeds the no-TOOLS_USED evidence fallback, which credits each
    # listed skill as "pinned skills" tool evidence. implement is dispatched with
    # only zoe-engineering (kanban_adapter._CHAIN), so it must not list runtime-only
    # tools like codebase-memory/serena — that would advance the pipeline on tool
    # evidence the implement worker was never given.
    assert store._PHASE_SKILLS["implement"] == ("zoe-engineering",)
    assert "codebase-memory" not in store._PHASE_SKILLS["implement"]
    assert "serena" not in store._PHASE_SKILLS["implement"]


@pytest.mark.asyncio
async def test_sync_pipeline_ignores_stale_duplicate_implement_block_after_verify_success(
    isolated_store,
):
    state = await store.bootstrap_state(
        "multica:duplicate-implement-block",
        issue={"metadata": {"evidence_profile": "code"}},
    )
    state = with_evidence(
        state,
        EvidenceItem(
            kind="tool",
            summary="implementation used repo context",
            passed=True,
            metadata={"phase": "implement", "source": "handoff"},
        ),
        EvidenceItem(
            kind="pr",
            summary="https://github.com/jason-easyazz/zoe-ai-assistant/pull/342",
            artifact="https://github.com/jason-easyazz/zoe-ai-assistant/pull/342",
            passed=True,
            metadata={"phase": "implement", "source": "handoff"},
        ),
    )
    store.save_state(state, event="implement_evidence_recorded")

    phases = {
        "implement": {
            "id": "t_duplicate",
            "status": "blocked",
            "block_reason": "DUPLICATE_REDISPATCH PR_URL=https://github.com/jason-easyazz/zoe-ai-assistant/pull/342",
        },
        "verify": {"id": "t_verify", "status": "done"},
    }

    async def fetch_detail(task_id: str):
        if task_id == "t_verify":
            return {
                "latest_summary": (
                    "TESTS=pytest passed\n"
                    "VALIDATORS=validate_structure.py passed\n"
                    "SUMMARY=verify passed"
                ),
                "comments": [],
            }
        return {"latest_summary": "BLOCKER=DUPLICATE_REDISPATCH", "comments": []}

    synced = await store.sync_pipeline_from_chain(
        "multica:duplicate-implement-block",
        phases,
        fetch_detail,
    )

    assert synced.phase == "review"
    assert synced.status == "todo"
    assert synced.attempts.get("implement") is None
    assert synced.last_block_fingerprint is None
    assert synced.repeated_block_count == 0
    assert synced.block_classification is None
    assert synced.history[-1].from_phase == "verify"
    assert synced.history[-1].outcome == "complete"
    assert store.pipeline_summary(synced)["block_reason"] is None
    events = [
        json.loads(line)["event"]
        for line in isolated_store.read_text(encoding="utf-8").strip().splitlines()
    ]
    assert "phase_catch_up" in events


@pytest.mark.asyncio
async def test_sync_pipeline_keeps_legitimate_implement_block_with_later_orphan_success(
    isolated_store,
):
    state = await store.bootstrap_state(
        "multica:legitimate-block-with-orphan-verify",
        issue={"metadata": {"evidence_profile": "code"}},
    )
    state = with_evidence(
        state,
        EvidenceItem(
            kind="tool",
            summary="implementation used repo context",
            passed=True,
            metadata={"phase": "implement", "source": "handoff"},
        ),
        EvidenceItem(
            kind="pr",
            summary="https://github.com/jason-easyazz/zoe-ai-assistant/pull/999",
            artifact="https://github.com/jason-easyazz/zoe-ai-assistant/pull/999",
            passed=True,
            metadata={"phase": "implement", "source": "handoff"},
        ),
    )
    store.save_state(state, event="implement_evidence_recorded")
    phases = {
        "implement": {
            "id": "t_blocked",
            "status": "blocked",
            "block_reason": "GATE_BLOCKED: missing required evidence pr",
        },
        "verify": {"id": "t_orphan_verify", "status": "done"},
    }

    async def fetch_detail(task_id: str):
        if task_id == "t_blocked":
            return {
                "latest_summary": (
                    "BLOCKER=GATE_BLOCKED: missing required evidence pr\n"
                    "SUMMARY=implementation did not produce a PR"
                ),
                "comments": [],
            }
        return {"latest_summary": "TESTS=pytest passed", "comments": []}

    synced = await store.sync_pipeline_from_chain(
        "multica:legitimate-block-with-orphan-verify",
        phases,
        fetch_detail,
    )

    assert synced.phase == "implement"
    assert synced.status == "blocked"
    assert synced.history[-1].outcome == "block"
    assert synced.history[-1].reason == "GATE_BLOCKED: missing required evidence pr"
    events = [
        json.loads(line)["event"]
        for line in isolated_store.read_text(encoding="utf-8").strip().splitlines()
    ]
    assert "phase_catch_up" not in events


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
                "TOOLS_USED=codebase-memory\n"
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
                "TOOLS_USED=codebase-memory\n"
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
            "latest_summary": "TOOLS_USED=codebase-memory",
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
            "latest_summary": "TOOLS_USED=codebase-memory",
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
async def test_sync_pipeline_gate_blocks_verify_without_test(isolated_store, monkeypatch):
    monkeypatch.setenv("ZOE_PIPELINE_VERIFY_EVIDENCE_RETRY_LIMIT", "1")
    monkeypatch.setenv("ZOE_PIPELINE_HARNESS_VERIFY_TESTS", "false")  # isolate retry behavior
    await store.bootstrap_state("multica:verify-gate")

    async def fetch_detail(task_id: str):
        if task_id == "t_impl":
            return {
                "latest_summary": "TOOLS_USED=codebase-memory\nPR_URL=https://github.com/o/r/pull/1",
                "comments": [],
            }
        return {"latest_summary": "VALIDATORS=pass", "comments": []}

    phases = {
        "implement": {"id": "t_impl", "status": "done"},
        "verify": {"id": "t_verify", "status": "done"},
    }
    state = await store.sync_pipeline_from_chain("multica:verify-gate", phases, fetch_detail)
    # First time verify completes with validator+pr but no `test` evidence, the
    # gate re-arms verify to todo (bounded retry) rather than terminally stranding,
    # so the re-dispatched worker can supply the missing focused-pytest evidence.
    assert state.phase == "verify"
    assert state.status == "todo"
    lines = isolated_store.read_text(encoding="utf-8").splitlines()
    assert any("verify_evidence_retry" in line for line in lines)
    assert not any("gate_blocked" in line for line in lines)


@pytest.mark.asyncio
async def test_sync_pipeline_gate_blocks_verify_after_retry_budget(isolated_store, monkeypatch):
    monkeypatch.setenv("ZOE_PIPELINE_VERIFY_EVIDENCE_RETRY_LIMIT", "1")
    monkeypatch.setenv("ZOE_PIPELINE_HARNESS_VERIFY_TESTS", "false")  # isolate gate behavior
    # Once verify has been re-armed past the retry budget, a still-missing `test`
    # gate becomes a terminal block (no infinite retry loop).
    seeded = PipelineState(
        task_ref="multica:verify-budget",
        phase="verify",
        status="running",
        attempts={"implement": 1, "verify": 2},  # already beyond the default limit (1)
    )
    store.save_state(seeded, event="seed")

    async def fetch_detail(task_id: str):
        if task_id == "t_impl":
            return {
                "latest_summary": "TOOLS_USED=codebase-memory\nPR_URL=https://github.com/o/r/pull/2",
                "comments": [],
            }
        return {"latest_summary": "VALIDATORS=pass", "comments": []}

    phases = {
        "implement": {"id": "t_impl", "status": "done"},
        "verify": {"id": "t_verify", "status": "done"},
    }
    state = await store.sync_pipeline_from_chain("multica:verify-budget", phases, fetch_detail)
    assert state.phase == "verify"
    assert state.status == "blocked"
    lines = isolated_store.read_text(encoding="utf-8").splitlines()
    assert any("gate_blocked" in line for line in lines)


def _patch_focused_runner(monkeypatch, *, ran: bool, passed: bool):
    import pipeline_focused_tests as pft

    def _fake(pr_url, *, repo_root=None):
        return pft.FocusedTestResult(
            ran=ran,
            passed=passed,
            summary=f"focused pytest: {'pass' if passed else 'fail'}",
            content_hash="hh" if ran else "",
            test_paths=("services/zoe-data/tests/test_x.py",) if ran else (),
        )

    monkeypatch.setattr(pft, "run_focused_pr_tests", _fake)


@pytest.mark.asyncio
async def test_harness_verify_completes_done_row_missing_agent_test(isolated_store, monkeypatch):
    # Verify worker completed without `test` evidence, but the harness runs the
    # PR's focused tests itself and they pass -> verify completes -> review.
    _patch_focused_runner(monkeypatch, ran=True, passed=True)
    await store.bootstrap_state("multica:hv-done")

    async def fetch_detail(task_id: str):
        if task_id == "t_impl":
            return {
                "latest_summary": "TOOLS_USED=codebase-memory\nPR_URL=https://github.com/o/r/pull/9",
                "comments": [],
            }
        return {"latest_summary": "VALIDATORS=pass", "comments": []}

    phases = {
        "implement": {"id": "t_impl", "status": "done"},
        "verify": {"id": "t_verify", "status": "done"},
    }
    state = await store.sync_pipeline_from_chain("multica:hv-done", phases, fetch_detail)
    assert state.phase == "review"
    assert any(e.kind == "test" and e.passed and e.metadata.get("source") == "harness" for e in state.evidence)


@pytest.mark.asyncio
async def test_harness_verify_overrides_spurious_block(isolated_store, monkeypatch):
    # Verify worker spuriously BLOCKED ("no PR to test"), but the harness focused
    # tests pass -> the objective run overrides the block -> verify -> review.
    _patch_focused_runner(monkeypatch, ran=True, passed=True)
    await store.bootstrap_state("multica:hv-block")

    async def fetch_detail(task_id: str):
        if task_id == "t_impl":
            return {
                "latest_summary": "TOOLS_USED=codebase-memory\nPR_URL=https://github.com/o/r/pull/10",
                "comments": [],
            }
        return {
            "latest_summary": "BLOCKER=verification requires the actual PR",
            "comments": [],
        }

    phases = {
        "implement": {"id": "t_impl", "status": "done"},
        "verify": {"id": "t_verify", "status": "blocked"},
    }
    state = await store.sync_pipeline_from_chain("multica:hv-block", phases, fetch_detail)
    assert state.phase == "review"


@pytest.mark.asyncio
async def test_harness_verify_failing_tests_do_not_complete(isolated_store, monkeypatch):
    # Harness ran the PR's tests and they FAILED -> verify must not complete.
    _patch_focused_runner(monkeypatch, ran=True, passed=False)
    monkeypatch.setenv("ZOE_PIPELINE_VERIFY_EVIDENCE_RETRY_LIMIT", "0")
    await store.bootstrap_state("multica:hv-fail")

    async def fetch_detail(task_id: str):
        if task_id == "t_impl":
            return {
                "latest_summary": "TOOLS_USED=codebase-memory\nPR_URL=https://github.com/o/r/pull/11",
                "comments": [],
            }
        return {"latest_summary": "VALIDATORS=pass", "comments": []}

    phases = {
        "implement": {"id": "t_impl", "status": "done"},
        "verify": {"id": "t_verify", "status": "done"},
    }
    state = await store.sync_pipeline_from_chain("multica:hv-fail", phases, fetch_detail)
    assert state.phase == "verify"
    assert state.status != "review"
    # A failing test evidence item was recorded (passed False) for diagnosis.
    assert any(e.kind == "test" and e.passed is False for e in state.evidence)


def _patch_review_ready(monkeypatch, *, ready: bool):
    import pipeline_review as prv
    from pipeline_review import ReviewReadiness

    monkeypatch.setattr(
        prv,
        "assess_pr_review_ready",
        lambda pr_url, *, repo_root=None: ReviewReadiness(ready, "test"),
    )


def _review_chain_fetch_detail():
    async def fetch_detail(task_id: str):
        if task_id == "t_impl":
            return {
                "latest_summary": "TOOLS_USED=codebase-memory\nPR_URL=https://github.com/o/r/pull/20",
                "comments": [],
            }
        if task_id == "t_verify":
            return {
                "latest_summary": "TESTS=pytest 5 passed\nVALIDATORS=validate_structure pass",
                "comments": [],
            }
        return {"latest_summary": "", "comments": []}  # review row

    return fetch_detail


@pytest.mark.asyncio
async def test_harness_review_approves_when_pr_objectively_ready(isolated_store, monkeypatch):
    # Deterministic review: PR objectively ready (CI green + 0 unresolved) -> the
    # harness writes human evidence and review completes -> closeout, regardless
    # of the (here, no-op) review agent signal.
    monkeypatch.setenv("ZOE_PIPELINE_HARNESS_VERIFY_TESTS", "false")  # isolate the review path
    _patch_review_ready(monkeypatch, ready=True)
    await store.bootstrap_state("multica:hr-ok")

    phases = {
        "implement": {"id": "t_impl", "status": "done"},
        "verify": {"id": "t_verify", "status": "done"},
        "review": {"id": "t_review", "status": "done"},
    }
    state = await store.sync_pipeline_from_chain("multica:hr-ok", phases, _review_chain_fetch_detail())
    assert state.phase == "closeout"
    assert any(
        e.kind == "human" and e.passed and e.metadata.get("source") == "harness"
        for e in state.evidence
    )


@pytest.mark.asyncio
async def test_harness_review_does_not_approve_when_not_ready(isolated_store, monkeypatch):
    # PR not objectively ready (e.g. unresolved threads) -> no harness approval;
    # review does not advance to closeout.
    monkeypatch.setenv("ZOE_PIPELINE_HARNESS_VERIFY_TESTS", "false")
    _patch_review_ready(monkeypatch, ready=False)
    await store.bootstrap_state("multica:hr-no")

    phases = {
        "implement": {"id": "t_impl", "status": "done"},
        "verify": {"id": "t_verify", "status": "done"},
        "review": {"id": "t_review", "status": "done"},
    }
    state = await store.sync_pipeline_from_chain("multica:hr-no", phases, _review_chain_fetch_detail())
    assert state.phase == "review"
    assert not any(e.kind == "human" and e.metadata.get("source") == "harness" for e in state.evidence)


@pytest.mark.asyncio
async def test_harness_review_disabled_by_env_does_not_approve(isolated_store, monkeypatch):
    # Kill switch: ZOE_PIPELINE_HARNESS_REVIEW_APPROVE=false -> no harness approval
    # even when the PR is objectively ready; review must not advance to closeout.
    monkeypatch.setenv("ZOE_PIPELINE_HARNESS_VERIFY_TESTS", "false")
    monkeypatch.setenv("ZOE_PIPELINE_HARNESS_REVIEW_APPROVE", "false")
    _patch_review_ready(monkeypatch, ready=True)  # ready, but the gate is disabled
    await store.bootstrap_state("multica:hr-off")

    phases = {
        "implement": {"id": "t_impl", "status": "done"},
        "verify": {"id": "t_verify", "status": "done"},
        "review": {"id": "t_review", "status": "done"},
    }
    state = await store.sync_pipeline_from_chain("multica:hr-off", phases, _review_chain_fetch_detail())
    assert state.phase == "review"
    assert not any(e.kind == "human" and e.metadata.get("source") == "harness" for e in state.evidence)


def _patch_closeout_merge(monkeypatch, *, merged: bool):
    import pipeline_closeout as pco
    from pipeline_closeout import CloseoutResult

    monkeypatch.setattr(
        pco,
        "run_closeout_merge",
        lambda pr_url, *, repo_root=None: CloseoutResult(
            merged, "sha123" if merged else None, "merged" if merged else "not ready"
        ),
    )


def _seed_closeout_state(task_ref: str, *, agent_greptile: bool = False):
    pr = "https://github.com/o/r/pull/30"
    evidence = [
        EvidenceItem(kind="pr", summary=pr, artifact=pr, passed=True, metadata={"phase": "implement"}),
    ]
    if agent_greptile:
        # Greptile evidence as the closeout AGENT would record it (source != harness,
        # no merge_sha) — recorded without an actual merge.
        evidence.append(
            EvidenceItem(
                kind="greptile",
                summary="greptile loop done",
                passed=True,
                metadata={"source": "skills", "phase": "closeout"},
            )
        )
    state = PipelineState(
        task_ref=task_ref,
        phase="closeout",
        status="running",
        attempts={"implement": 1, "verify": 1, "review": 1, "closeout": 1},
        evidence=evidence,
    )
    store.save_state(state, event="seed")


async def _noop_fetch_detail(task_id: str):
    return {"latest_summary": "", "comments": []}


@pytest.mark.asyncio
async def test_harness_closeout_merges_and_completes(isolated_store, monkeypatch):
    # Deterministic closeout: the harness runs the greploop guard; when the PR
    # merges, greptile evidence is recorded and closeout completes -> retro.
    _patch_closeout_merge(monkeypatch, merged=True)
    _seed_closeout_state("multica:co-ok")

    phases = {"closeout": {"id": "t_co", "status": "done"}}
    state = await store.sync_pipeline_from_chain("multica:co-ok", phases, _noop_fetch_detail)
    assert state.phase == "retro"
    assert any(
        e.kind == "greptile" and e.passed and e.metadata.get("source") == "harness"
        and e.metadata.get("merge_sha") == "sha123"
        for e in state.evidence
    )


@pytest.mark.asyncio
async def test_harness_closeout_does_not_complete_when_not_merged(isolated_store, monkeypatch):
    _patch_closeout_merge(monkeypatch, merged=False)
    _seed_closeout_state("multica:co-no")

    phases = {"closeout": {"id": "t_co", "status": "done"}}
    state = await store.sync_pipeline_from_chain("multica:co-no", phases, _noop_fetch_detail)
    assert state.phase == "closeout"
    assert not any(e.kind == "greptile" and e.metadata.get("source") == "harness" for e in state.evidence)


@pytest.mark.asyncio
async def test_harness_closeout_ignores_agent_greptile_without_real_merge(isolated_store, monkeypatch):
    # Regression for the #679 bypass: the closeout AGENT recorded greptile evidence
    # (source='skills') but there was NO actual merge. Closeout must NOT advance to
    # retro on that unverified claim — it must hold pending a harness-confirmed merge.
    _patch_closeout_merge(monkeypatch, merged=False)
    _seed_closeout_state("multica:co-agent", agent_greptile=True)

    phases = {"closeout": {"id": "t_co", "status": "done"}}
    state = await store.sync_pipeline_from_chain("multica:co-agent", phases, _noop_fetch_detail)
    assert state.phase == "closeout"  # did NOT false-complete to retro on agent greptile
    assert not any(
        e.kind == "greptile" and e.metadata.get("source") == "harness" for e in state.evidence
    )


@pytest.mark.asyncio
async def test_harness_closeout_merges_even_with_agent_greptile_present(isolated_store, monkeypatch):
    # The harness merge runs regardless of an agent greptile item; on a real merge
    # closeout completes -> retro (authoritative harness merge, not the agent claim).
    _patch_closeout_merge(monkeypatch, merged=True)
    _seed_closeout_state("multica:co-agent-ok", agent_greptile=True)

    phases = {"closeout": {"id": "t_co", "status": "done"}}
    state = await store.sync_pipeline_from_chain("multica:co-agent-ok", phases, _noop_fetch_detail)
    assert state.phase == "retro"
    assert any(
        e.kind == "greptile" and e.metadata.get("source") == "harness" and e.metadata.get("merge_sha") == "sha123"
        for e in state.evidence
    )


@pytest.mark.asyncio
async def test_harness_closeout_disabled_by_env(isolated_store, monkeypatch):
    monkeypatch.setenv("ZOE_PIPELINE_HARNESS_CLOSEOUT_MERGE", "false")
    _patch_closeout_merge(monkeypatch, merged=True)  # would merge, but gate disabled
    _seed_closeout_state("multica:co-off")

    phases = {"closeout": {"id": "t_co", "status": "done"}}
    state = await store.sync_pipeline_from_chain("multica:co-off", phases, _noop_fetch_detail)
    assert state.phase == "closeout"
    assert not any(e.kind == "greptile" and e.metadata.get("source") == "harness" for e in state.evidence)


@pytest.mark.asyncio
async def test_sync_pipeline_audit_only_verify_skips_test_gate(isolated_store):
    await store.bootstrap_state("multica:audit-gate")

    async def fetch_detail(task_id: str):
        if task_id == "t_impl":
            return {
                "latest_summary": "AUDIT_ONLY=1\nTOOLS_USED=codebase-memory\nSUMMARY=audit complete",
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
async def test_already_covered_run_converges_instead_of_review_implement_loop(isolated_store, monkeypatch):
    # Regression: an already-covered run (implementation proven unnecessary) used
    # to bounce review -> implement forever, because review's no-PR request_changes
    # is not "protocol-only" and so was never recovered. It must now converge.
    ref = "multica:already-covered-converge"
    await store.bootstrap_state(ref, issue={"metadata": {"evidence_profile": "code"}})

    async def fetch_impl(_task_id):
        return {
            "latest_summary": "BLOCKER=ALREADY_COVERED: focused tests passed; no PR required",
            "comments": [],
        }

    # 1) implement proves the work already covered -> skip to verify, audit profile.
    state = await store.sync_pipeline_from_chain(
        ref,
        {"implement": {"id": "t_impl", "status": "blocked",
                       "block_reason": "ALREADY_COVERED: focused tests passed; no PR required"}},
        fetch_impl,
    )
    assert state.phase == "verify"
    assert state.evidence_profile == "audit"
    assert _run_is_already_covered_marker(state)

    async def fetch_plain(_task_id):
        return {"latest_summary": "", "comments": []}

    # Force the harness validators to FAIL so the verify gate cannot be satisfied
    # the normal way. An already-covered run has no diff for validators to pass on,
    # so this pins the audit no-op recovery path rather than passing trivially in
    # environments where the repo validators happen to succeed.
    from pipeline_validators import ValidatorRunResult

    monkeypatch.setattr(
        "pipeline_validators.run_repo_validators",
        lambda: ValidatorRunResult(exit_code=1, summary="no diff", content_hash="deadbeef", passed=False),
    )

    # 2) verify converges for the audit no-op run despite failing validators.
    state = await store.sync_pipeline_from_chain(
        ref, {"verify": {"id": "t_verify", "status": "done"}}, fetch_plain
    )
    assert state.phase == "review", f"expected verify to advance to review, got {state.phase!r}"

    # 3) review on an already-covered run has no PR -> normally request_changes ->
    # implement (the loop). With the fix it recovers to complete and moves forward.
    state = await store.sync_pipeline_from_chain(
        ref,
        {"review": {"id": "t_review", "status": "blocked", "block_reason": "no PR to review"}},
        fetch_plain,
    )
    assert state.phase != "implement", "already-covered run must not loop back to implement"
    assert store.PHASE_ORDER.index(state.phase) >= store.PHASE_ORDER.index("review")


def _run_is_already_covered_marker(state) -> bool:
    return any(
        r.outcome == "skip_implementation" and "ALREADY_COVERED" in str(getattr(r, "reason", "") or "").upper()
        for r in state.history
    )


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
async def test_sync_pipeline_verify_budget_blocks_without_revision(isolated_store):
    state = PipelineState(task_ref="multica:verify-budget", phase="verify", status="running")
    store.save_state(state, event="effect_requested")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "BLOCKER=VERIFY_BUDGET: code-enforced tool budget exceeded",
            "comments": [],
        }

    phases = {"verify": {"id": "t_verify", "status": "blocked"}}
    state = await store.sync_pipeline_from_chain("multica:verify-budget", phases, fetch_detail)

    assert state.phase == "verify"
    assert state.status == "blocked"
    assert state.history[-1].outcome == "block"
    assert "VERIFY_BUDGET" in (state.history[-1].reason or "")


@pytest.mark.asyncio
async def test_sync_pipeline_auto_validators_on_implement_done(isolated_store, monkeypatch):
    await store.bootstrap_state("multica:val")

    async def fetch_detail(_task_id: str):
        return {
            "latest_summary": "TOOLS_USED=codebase-memory\nTESTS=pytest -q pass\nPR_URL=https://github.com/o/r/pull/9",
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


# ── streaming reads ──────────────────────────────────────────────────────────
# This is an event-sourced store: save_state re-appends a FULL state snapshot on
# every write, so it grows without bound (1.59 GB before a compactor existed).
# Reading it whole pulls all of that into RAM on a memory-tight Jetson.
#
# These tests pin the MECHANISM, not the outcome: a caller that materialises the
# file still returns the correct state, so asserting on the return value alone
# would let a future edit silently reintroduce the whole-file load with every
# test still green.


class _NoReadHandle:
    """An iterable file handle that EXPLODES on read()/readlines().

    A streaming caller iterates and never trips it; a caller that materialises
    the file fails loudly. Everything else delegates to the real handle.
    """

    def __init__(self, handle):
        self._handle = handle

    def __iter__(self):
        return iter(self._handle)

    def read(self, *_a, **_k):
        raise AssertionError(
            "read() called — the pipeline store must be STREAMED, never "
            "materialised (it grows unbounded; 1.59 GB before compaction)"
        )

    def readlines(self, *_a, **_k):
        raise AssertionError("readlines() called — iterate the handle instead")

    def __getattr__(self, name):  # seek / fileno / write / flush / close
        return getattr(self._handle, name)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._handle.close()
        return False


@pytest.fixture
def no_read_store(isolated_store, monkeypatch):
    """Make every open() of the store return a handle that bans read()."""
    from pathlib import Path

    real_open = Path.open

    def _open(self, *a, **k):
        handle = real_open(self, *a, **k)
        if self == isolated_store:
            return _NoReadHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", _open)
    return isolated_store


def test_latest_state_from_lines_accepts_any_iterable(isolated_store):
    """The helper takes an ITERABLE of lines, not just a list — that is what lets
    the callers hand it an open handle and stream."""
    import asyncio

    asyncio.run(store.bootstrap_state("multica:iter"))
    raw = isolated_store.read_text(encoding="utf-8").splitlines()

    latest = store._latest_state_from_lines(iter(raw), "multica:iter")

    assert latest is not None
    assert latest.task_ref == "multica:iter"


def test_load_latest_state_streams_never_materialises(no_read_store):
    import asyncio

    asyncio.run(store.bootstrap_state("multica:stream"))

    loaded = store.load_latest_state("multica:stream")

    assert loaded is not None
    assert loaded.task_ref == "multica:stream"


def test_save_state_streams_and_its_append_still_lands(no_read_store):
    """save_state's read-back sits inside its LOCK_EX critical section, so it must
    stream there too — and its append must still land (it re-seeks to SEEK_END
    after the scan leaves the position at EOF)."""
    import asyncio

    asyncio.run(store.bootstrap_state("multica:savestream"))
    state = store.load_latest_state("multica:savestream")
    assert state is not None

    saved = store.save_state(state, event="streamed")
    assert saved.journal_revision > state.journal_revision

    reloaded = store.load_latest_state("multica:savestream")
    assert reloaded is not None
    assert reloaded.journal_revision == saved.journal_revision
