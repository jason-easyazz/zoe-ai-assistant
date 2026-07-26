"""Tests for the harness-side closeout merge runner (deterministic closeout)."""

import pytest
import json

import pipeline_closeout as pc
from pipeline_closeout import CloseoutResult, _pr_number

pytestmark = pytest.mark.ci_safe


def test_pr_number_parses():
    assert _pr_number("https://github.com/o/r/pull/678") == "678"
    assert _pr_number("https://github.com/o/r/pull/678/") == "678"
    assert _pr_number("https://github.com/o/r/tree/main") == ""


def test_run_closeout_merge_no_pr():
    assert pc.run_closeout_merge("").merged is False


def test_run_closeout_merge_already_merged(monkeypatch, tmp_path):
    # guard script must exist for the path to proceed past the existence check
    (tmp_path / "scripts" / "maintenance").mkdir(parents=True)
    (tmp_path / "scripts" / "maintenance" / "run_greploop_guard.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(pc, "_merge_state", lambda pr_url, *, cwd: ("MERGED", "abc123"))
    out = pc.run_closeout_merge("https://github.com/o/r/pull/9", repo_root=str(tmp_path))
    assert out.merged is True
    assert out.merge_sha == "abc123"
    assert "already merged" in out.reason


def test_run_closeout_merge_runs_guard_then_confirms_merge(monkeypatch, tmp_path):
    (tmp_path / "scripts" / "maintenance").mkdir(parents=True)
    (tmp_path / "scripts" / "maintenance" / "run_greploop_guard.sh").write_text("#!/bin/sh\n")
    states = [("OPEN", None), ("MERGED", "deadbeef")]  # before guard, after guard
    monkeypatch.setattr(pc, "_merge_state", lambda pr_url, *, cwd: states.pop(0))
    monkeypatch.setattr(pc, "_run", lambda cmd, *, cwd, timeout: (0, "guard ran"))
    out = pc.run_closeout_merge("https://github.com/o/r/pull/9", repo_root=str(tmp_path))
    assert out.merged is True
    assert out.merge_sha == "deadbeef"


def test_run_closeout_merge_not_merged_after_guard(monkeypatch, tmp_path):
    (tmp_path / "scripts" / "maintenance").mkdir(parents=True)
    (tmp_path / "scripts" / "maintenance" / "run_greploop_guard.sh").write_text("#!/bin/sh\n")
    monkeypatch.setattr(pc, "_merge_state", lambda pr_url, *, cwd: ("OPEN", None))
    monkeypatch.setattr(pc, "_run", lambda cmd, *, cwd, timeout: (2, "BLOCKED_NOT_READY"))
    out = pc.run_closeout_merge("https://github.com/o/r/pull/9", repo_root=str(tmp_path))
    assert out.merged is False
    assert "did not merge" in out.reason


def test_run_closeout_merge_fails_open_when_guard_missing(tmp_path):
    out = pc.run_closeout_merge("https://github.com/o/r/pull/9", repo_root=str(tmp_path))
    assert out.merged is False
    assert "guard script missing" in out.reason
