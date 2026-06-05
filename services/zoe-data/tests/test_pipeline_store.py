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
    state = PipelineState(
        task_ref="multica:false-duplicate",
        phase="implement",
        status="blocked",
        last_block_fingerprint="abc123",
        repeated_block_count=2,
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
    assert store.pipeline_summary(resumed)["terminal_block"] is False


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
