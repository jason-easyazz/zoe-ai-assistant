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
    state = store.bootstrap_state("multica:abc")
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
    store.bootstrap_state("multica:sync")

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
async def test_sync_pipeline_gate_blocks_missing_evidence(isolated_store):
    store.bootstrap_state("multica:gate")

    async def fetch_detail(_task_id: str):
        return {"latest_summary": "SUMMARY=no tools listed", "comments": []}

    phases = {"implement": {"id": "t1", "status": "done"}}
    state = await store.sync_pipeline_from_chain("multica:gate", phases, fetch_detail)
    assert state.phase == "implement"
    assert any("gate_blocked" in line for line in isolated_store.read_text(encoding="utf-8").splitlines())
