import pytest
import importlib.util
import asyncio
import sys
import types
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "zoe_pr_maintenance.py"
    spec = importlib.util.spec_from_file_location("zoe_pr_maintenance_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_run_guard_calls_packet_guard(monkeypatch):
    async def run_guard_once(pr, *, packet_only):
        return {"ok": True, "state": "READY_TO_MERGE", "pr": pr, "packet_only": packet_only}

    async def merge_pr_when_ready(pr):
        raise AssertionError("merge path should not run")

    monkeypatch.setitem(
        sys.modules,
        "greploop_guard",
        types.SimpleNamespace(run_guard_once=run_guard_once, merge_pr_when_ready=merge_pr_when_ready),
    )

    result = _load_module()._run_guard(141, merge_when_ready=False)
    assert result == {"ok": True, "state": "READY_TO_MERGE", "pr": 141, "packet_only": True, "exit_code": 0}


def test_run_guard_calls_merge_guard(monkeypatch):
    async def run_guard_once(pr, *, packet_only):
        raise AssertionError("packet path should not run")

    async def merge_pr_when_ready(pr):
        return {"ok": True, "state": "MERGED", "pr": pr}

    monkeypatch.setitem(
        sys.modules,
        "greploop_guard",
        types.SimpleNamespace(run_guard_once=run_guard_once, merge_pr_when_ready=merge_pr_when_ready),
    )

    result = _load_module()._run_guard(141, merge_when_ready=True)
    assert result == {"ok": True, "state": "MERGED", "pr": 141, "exit_code": 0}


def test_record_issue_progress_marks_pipeline_done_on_merge(monkeypatch):
    calls = []

    class Client:
        def is_configured(self):
            return True

        async def record_progress(self, issue_id, **kwargs):
            calls.append(("progress", issue_id, kwargs))

    def complete_pipeline_after_external_merge(task_ref, **kwargs):
        calls.append(("complete", task_ref, kwargs))

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: Client()),
    )
    monkeypatch.setitem(
        sys.modules,
        "pipeline_store",
        types.SimpleNamespace(
            complete_pipeline_after_external_merge=complete_pipeline_after_external_merge
        ),
    )

    result = asyncio.run(
        _load_module()._record_issue_progress(
            "issue-1",
            {
                "state": "MERGED",
                "greptile": "5/5",
                "merge_commit": "deadbeef",
                "pr_url": "https://github.com/o/r/pull/9",
            },
        )
    )

    assert result["journal_ok"] is True
    assert calls[0] == (
        "progress",
        "issue-1",
        {
            "phase": "closeout",
            "greptile_status": "5/5",
            "merge_sha": "deadbeef",
            "blocker": None,
            "clear_blocker": True,
            "status": "done",
        },
    )
    assert calls[1] == (
        "complete",
        "multica:issue-1",
        {
            "pr_url": "https://github.com/o/r/pull/9",
            "merge_sha": "deadbeef",
            "greptile_status": "5/5",
            "reason": "PR maintenance recorded merged PR",
        },
    )


def test_record_issue_progress_preserves_json_contract_on_journal_error(monkeypatch):
    class Client:
        def is_configured(self):
            return True

        async def record_progress(self, issue_id, **kwargs):
            return {}

    def complete_pipeline_after_external_merge(task_ref, **kwargs):
        raise RuntimeError("journal unavailable")

    monkeypatch.setitem(
        sys.modules,
        "multica_client",
        types.SimpleNamespace(get_multica_client=lambda: Client()),
    )
    monkeypatch.setitem(
        sys.modules,
        "pipeline_store",
        types.SimpleNamespace(
            complete_pipeline_after_external_merge=complete_pipeline_after_external_merge
        ),
    )

    result = asyncio.run(
        _load_module()._record_issue_progress(
            "issue-1",
            {"ok": True, "state": "MERGED", "merge_commit": "deadbeef"},
        )
    )

    assert result["journal_ok"] is False
    assert result["journal_error"] == "journal unavailable"
    assert result["exit_code"] == 1
