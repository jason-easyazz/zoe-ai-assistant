"""Tests for background_runner Hermes worker-profile routing."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import background_runner as br


def test_background_profile_defaults_to_zoe_coder(monkeypatch):
    monkeypatch.delenv("HERMES_BACKGROUND_PROFILE", raising=False)
    monkeypatch.delenv("HERMES_BACKGROUND_MODEL", raising=False)
    monkeypatch.delenv("HERMES_MODEL", raising=False)
    assert br._background_profile() == "zoe-coder"


def test_background_profile_honours_env_override(monkeypatch):
    monkeypatch.setenv("HERMES_BACKGROUND_PROFILE", "zoe-planner")
    assert br._background_profile() == "zoe-planner"


@pytest.mark.asyncio
async def test_run_hermes_background_task_uses_worker_cli(monkeypatch):
    captured = {}

    async def fake_communicate():
        return b"done", b""

    proc = MagicMock()
    proc.communicate = fake_communicate
    proc.returncode = 0
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    async def fake_exec(*cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("HERMES_BACKGROUND_PROFILE", "zoe-coder")

    result = await br._run_hermes_background_task("audit validators only", user_id="u1", task_id=99)

    assert result == "done"
    cmd = captured["cmd"]
    assert "-p" in cmd and "zoe-coder" in cmd
    assert "--accept-hooks" in cmd and "-z" in cmd
    assert "audit validators only" in cmd[-1]
