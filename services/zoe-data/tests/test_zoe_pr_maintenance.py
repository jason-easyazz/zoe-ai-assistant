import importlib.util
import sys
import types
from pathlib import Path


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

