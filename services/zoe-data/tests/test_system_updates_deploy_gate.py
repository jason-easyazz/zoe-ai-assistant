"""The Updates "Install" button applies deploy.yml's refusals before it moves the live checkout (gap audit 2026-11-13)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import system_updates  # noqa: E402

pytestmark = pytest.mark.ci_safe

PREV, TARGET = "a" * 40, "b" * 40


def _fake(results):
    """results: {key: (rc, out)} keyed by the command's distinguishing word; records every call."""
    calls = []

    async def run(*args, timeout=15.0, cwd=None):
        calls.append(args)
        if "rev-parse" in args:
            return results["origin" if any(a.startswith("origin/main") for a in args) else "head"]
        if "merge-base" in args:
            return results["ancestor"]
        if any(str(a).endswith("voice_gate_check.py") for a in args):
            return results["gate"]
        if "merge" in args:
            return results.get("merge", (0, "ok"))
        return 0, ""

    return run, calls


def _gate(monkeypatch, **over):
    results = {"head": (0, PREV + "\n"), "origin": (0, TARGET + "\n"), "ancestor": (0, ""), "gate": (0, "voice gate: pass")}
    results.update(over)
    run, calls = _fake(results)
    monkeypatch.setattr(system_updates, "_run_cmd", run)
    return asyncio.run(system_updates._deploy_gate()), calls


def test_passing_gate_returns_the_exact_target_sha(monkeypatch):
    (target, log), calls = _gate(monkeypatch)
    assert target == TARGET and "deploy gate: OK" in log
    gate_call = next(c for c in calls if any(str(a).endswith("voice_gate_check.py") for a in c))
    assert "--expect-tree-of" in gate_call and TARGET in gate_call and f"{PREV}..{TARGET}" in gate_call


def test_missing_replay_evidence_refuses(monkeypatch):
    (target, log), _ = _gate(monkeypatch, gate=(1, "BLOCK: no fresh passing replay for this tree"))
    assert target is None and "REFUSED" in log and "no fresh passing replay" in log


def test_checker_that_cannot_run_refuses(monkeypatch):
    (target, _), _ = _gate(monkeypatch, gate=(-1, "Command not found: python3"))
    assert target is None


def test_non_fast_forward_refuses_before_the_checker_runs(monkeypatch):
    (target, log), calls = _gate(monkeypatch, ancestor=(1, ""))
    assert target is None and "not a fast-forward" in log
    assert not any(str(a).endswith("voice_gate_check.py") for c in calls for a in c)


def test_unresolvable_ref_refuses(monkeypatch):
    (target, _), _ = _gate(monkeypatch, origin=(128, "fatal: bad revision"))
    assert target is None


def test_install_never_merges_when_the_gate_refuses(monkeypatch):
    run, calls = _fake({"head": (0, PREV), "origin": (0, TARGET), "ancestor": (0, ""), "gate": (1, "BLOCK")})

    async def fake_run(*args, timeout=15.0, cwd=None):
        if args[:4] == ("git", "-C", system_updates.COMPOSE_PROJECT_DIR, "status"):
            return 0, ""
        if "--abbrev-ref" in args:
            return 0, "main\n"
        return await run(*args, timeout=timeout, cwd=cwd)

    async def latest():
        return "v9.9.9"

    history = []

    async def write_history(db, comp, before, after, ok, log, user):
        history.append(ok)

    monkeypatch.setattr(system_updates, "_run_cmd", fake_run)
    monkeypatch.setattr(system_updates, "_fetch_github_latest_release", latest)
    monkeypatch.setattr(system_updates, "_read_local_version", lambda: "v1.0.0")
    monkeypatch.setattr(system_updates, "_write_update_history", write_history)
    # a broken gate must not let this test write the checkout's real VERSION file (seen in the negative control)
    monkeypatch.setattr(system_updates, "_write_local_version", lambda tag: None)
    res = asyncio.run(system_updates._install_zoe_platform(None, {"user_id": "u"}))
    assert res["ok"] is False and "REFUSED" in res["log"]
    assert not any("merge" in c and "--ff-only" in c for c in calls)
    assert history == [False]
