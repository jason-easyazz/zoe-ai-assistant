import json

import pytest

import greploop_guard


def _packet(**overrides):
    data = {
        "task_type": "FIX_GREPTILE_FINDING",
        "pr": 66,
        "head_sha": "abc123",
        "base_branch": "main",
        "allowed_files": ["services/zoe-data/example.py"],
        "max_files": 1,
        "max_changed_lines": 50,
        "issue_text": "Fix the narrow bug",
        "commands_to_run": ["git diff --check"],
        "success_condition": "focused fix",
        "stop_condition": "stop on ambiguity",
        "forbidden_actions": greploop_guard.FORBIDDEN_ACTIONS,
    }
    data.update(overrides)
    return greploop_guard.GuardPacket(**data)


def test_validate_packet_rejects_broad_missing_context():
    packet = _packet(issue_text="")

    with pytest.raises(greploop_guard.GuardError, match="BLOCKED_MISSING_CONTEXT"):
        greploop_guard.validate_packet(packet)


def test_redact_removes_secret_like_values():
    payload = {"message": "Authorization: Bearer abc123", "nested": ["api_key=secret-value"]}

    redacted = greploop_guard.redact(payload)

    assert "abc123" not in json.dumps(redacted)
    assert "secret-value" not in json.dumps(redacted)


def test_analyze_result_rejects_files_outside_allowlist(monkeypatch):
    monkeypatch.setattr(greploop_guard, "_diff_files", lambda: ["services/zoe-data/other.py"])
    monkeypatch.setattr(greploop_guard, "_diff_changed_lines", lambda: 5)

    result = greploop_guard.analyze_result(_packet(), "done")

    assert result["classification"] == "REJECTED"
    assert result["outside_allowlist"] == ["services/zoe-data/other.py"]


def test_analyze_result_accepts_focused_diff(monkeypatch):
    monkeypatch.setattr(greploop_guard, "_diff_files", lambda: ["services/zoe-data/example.py"])
    monkeypatch.setattr(greploop_guard, "_diff_changed_lines", lambda: 5)

    result = greploop_guard.analyze_result(_packet(), "TESTS=git diff --check")

    assert result["classification"] == "APPLIED"


def test_lock_prevents_duplicate_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)

    with greploop_guard.acquire_lock(66):
        with pytest.raises(greploop_guard.GuardError, match="already running"):
            with greploop_guard.acquire_lock(66):
                pass


def test_write_json_redacts_state(tmp_path, monkeypatch):
    monkeypatch.setattr(greploop_guard, "STATE_ROOT", tmp_path)

    greploop_guard._write_json(66, "status.json", {"token": "token=abc123"})

    assert "abc123" not in (tmp_path / "pr-66" / "status.json").read_text()


@pytest.mark.asyncio
async def test_cheap_runner_blocks_before_budget_exceeded(monkeypatch):
    monkeypatch.setenv("ZOE_CHEAP_PR_AGENT_CMD", "false")
    monkeypatch.setenv("ZOE_CHEAP_PR_AGENT_ESTIMATED_COST_USD", "99")
    monkeypatch.setattr(greploop_guard, "MAX_COST_USD", 1.0)

    status, output = await greploop_guard._run_cheap_agent(_packet())

    assert status == "BLOCKED_BUDGET_EXCEEDED"
    assert "max_cost_usd=1.0" in output
