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
async def test_sync_pipeline_fingerprint_abort(isolated_store):
    await store.bootstrap_state("multica:fp")

    async def fetch_detail(_task_id: str):
        return {"latest_summary": "BLOCKER=WORKTREE_NOT_READY", "comments": []}

    phases = {"implement": {"id": "t1", "status": "blocked", "block_reason": "WORKTREE_NOT_READY"}}
    state = await store.sync_pipeline_from_chain("multica:fp", phases, fetch_detail)
    assert state.status == "blocked"
    assert state.repeated_block_count == 1

    state = await store.sync_pipeline_from_chain("multica:fp", phases, fetch_detail)
    assert state.status == "blocked"
    assert any("fingerprint_abort" in (rec.reason or "") for rec in state.history)
    assert any("fingerprint_abort" in line for line in isolated_store.read_text(encoding="utf-8").splitlines())


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
