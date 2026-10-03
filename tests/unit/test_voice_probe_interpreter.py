"""The voice gate must run on the zoe-data SERVICE interpreter, and must never
report pass for a run that scored brain turns without memory recall.

Incident (found 2026-10-03): since B0.8 (2026-09-25) the palace on disk is
chromadb 1.x, but the nightly unit and the landing scripts launched the probe
with /usr/bin/python3 (3.10, chromadb 0.6.3). The replay imports zoe-data
in-process, so every palace open was refused ("palace is chromadb 1.x format but
the installed client is 0.6.3"), every recall reader swallowed it, and the gate
went on scoring brain turns WITHOUT recall while the live service had it.

Pinned here: the interpreter ladder, its explicit hand-off to measure_voice, the
probe's self re-exec, the recall field in the summary, and the rule that any
recall state but "ok" makes the run status=error and never writes a baseline —
with negative controls for each.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


vrp = _load("vrp_interpreter", "scripts/maintenance/voice_regression_probe.py")
mv = _load("mv_interpreter", "scripts/perf/measure_voice.py")
sp = _load("service_python_t", "scripts/lib/service_python.py")


def _exe(tmp_path: Path, name: str) -> str:
    p = tmp_path / name
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    return str(p)


# ── the ladder ───────────────────────────────────────────────────────────────
class TestResolveServicePython:
    def test_explicit_wins_over_everything(self, tmp_path):
        a, b = _exe(tmp_path, "a"), _exe(tmp_path, "b")
        assert sp.resolve_service_python(a, env={sp.ENV_VAR: b}, query=lambda: b) == (a, "--python")

    def test_env_wins_over_the_unit(self, tmp_path):
        a, b = _exe(tmp_path, "a"), _exe(tmp_path, "b")
        assert sp.resolve_service_python(None, env={sp.ENV_VAR: a}, query=lambda: b) == (a, sp.ENV_VAR)

    def test_unit_is_the_default_authority_not_venv_presence(self, tmp_path):
        unit, venv = _exe(tmp_path, "unit"), _exe(tmp_path, "venv")
        py, src = sp.resolve_service_python(None, env={}, query=lambda: unit, venv=Path(venv))
        assert (py, src) == (unit, "zoe-data unit")

    def test_venv_only_when_systemd_cannot_answer(self, tmp_path):
        venv = _exe(tmp_path, "venv")
        py, src = sp.resolve_service_python(None, env={}, query=lambda: None, venv=Path(venv))
        assert py == venv and "systemd unavailable" in src

    def test_falls_back_to_current_and_says_so(self, tmp_path):
        py, src = sp.resolve_service_python(None, env={}, query=lambda: None,
                                            venv=tmp_path / "absent")
        assert py == sys.executable and "unknown" in src

    def test_bad_explicit_choice_is_a_hard_error_not_a_silent_fallback(self, tmp_path):
        with pytest.raises(SystemExit, match="not an executable"):
            sp.resolve_service_python(str(tmp_path / "nope"), env={}, query=lambda: None)

    def test_same_python_does_not_resolve_venv_symlinks(self, tmp_path):
        base = _exe(tmp_path, "python3.12")
        link = tmp_path / "venv-python"
        link.symlink_to(base)
        assert not sp.same_python(str(link), base)   # different site-packages
        assert sp.same_python(str(tmp_path / "x" / ".." / "python3.12"), base)


# ── the probe hands its interpreter on, explicitly ───────────────────────────
def test_run_measure_passes_the_chosen_interpreter_on_both_hops(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        Path(cmd[cmd.index("--json") + 1]).write_text('{"aggregate_ms": {}}')
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(vrp.subprocess, "run", fake_run)
    py = _exe(tmp_path, "svc-python")
    vrp.run_measure(1, "/nonexistent", "u", 5, "remote", python=py)
    assert seen["cmd"][0] == py
    assert seen["cmd"][seen["cmd"].index("--python") + 1] == py


class TestReexec:
    def _capture(self, monkeypatch):
        calls = []
        monkeypatch.setattr(vrp.os, "execve", lambda *a: calls.append(a))
        monkeypatch.delenv(vrp._REEXEC_ENV, raising=False)
        return calls

    def test_reexecs_onto_a_different_interpreter_with_a_loop_guard(self, monkeypatch, tmp_path):
        calls = self._capture(monkeypatch)
        py = _exe(tmp_path, "svc-python")
        vrp._reexec_if_needed(py, "zoe-data unit")
        (path, argv, env), = calls
        assert path == py and argv[0] == py and argv[1].endswith("voice_regression_probe.py")
        assert env[vrp._REEXEC_ENV] == py

    def test_no_reexec_when_already_on_it(self, monkeypatch):
        calls = self._capture(monkeypatch)
        vrp._reexec_if_needed(sys.executable, "zoe-data unit")
        assert calls == []

    def test_a_reexeced_child_never_reexecs_again(self, monkeypatch, tmp_path):
        calls = self._capture(monkeypatch)
        py = _exe(tmp_path, "svc-python")
        monkeypatch.setenv(vrp._REEXEC_ENV, py)
        vrp._reexec_if_needed(py, "zoe-data unit")
        assert calls == []


# ── recall evidence: summary field + the fail-closed rule ────────────────────
def _report(recall: str | None) -> dict:
    r = {"n_samples": 4, "verdicts": {"OK": 4},
         "aggregate_ms": {"stt_ms": {"median": 100}, "brain_ms": {"median": 200},
                          "e2e_ms": {"median": 300}},
         "interpreter": {"python": "/x/python", "version": "3.12.13", "chromadb": "1.5.9"}}
    if recall is not None:
        r["memory_recall"] = recall
        r["memory_recall_detail"] = "detail"
    return r


def test_summary_carries_recall_and_interpreter():
    s = vrp.summarize(_report("ok"))
    assert s["memory_recall"] == "ok" and s["interpreter"]["chromadb"] == "1.5.9"
    assert vrp.summarize(_report(None))["memory_recall"] == "unreported"


@pytest.mark.parametrize("state", ["mismatch", "error", "disabled", "unreported"])
def test_every_state_but_ok_is_not_evidence(state):
    assert vrp.recall_gate({"memory_recall": state}) is not None


def test_ok_recall_is_evidence():
    assert vrp.recall_gate(vrp.summarize(_report("ok"))) is None


def _drive_main(tmp_path, monkeypatch, recall, *extra):
    monkeypatch.setattr(vrp, "_acquire_harness_lock", lambda: None)
    monkeypatch.setattr(vrp, "mem_available_mb", lambda: 10_000)
    monkeypatch.setattr(vrp, "cleanup_replay_artifacts", lambda *a, **k: True)
    monkeypatch.setattr(vrp, "service_revision", lambda *_: None)
    monkeypatch.setattr(vrp, "resolve_service_python", lambda x: (sys.executable, "test"))
    monkeypatch.setattr(vrp, "run_measure", lambda *a, **k: _report(recall))
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"created_at": "2026-09-01T00:00:00Z",
                                    "summary": vrp.summarize(_report("ok"))}))
    before = baseline.read_text()
    results = tmp_path / "last.json"
    monkeypatch.setattr(sys, "argv", [
        "probe", "--results", str(results), "--baseline", str(baseline),
        "--trend", str(tmp_path / "t.jsonl"), "--service-dir", str(tmp_path),
        "--no-vad-check", "--no-cleanup", *extra])
    rc = vrp.main()
    return rc, json.loads(results.read_text()), baseline.read_text() != before


def test_recall_ok_run_passes(tmp_path, monkeypatch):
    rc, art, _ = _drive_main(tmp_path, monkeypatch, "ok")
    assert (rc, art["status"]) == (0, "pass")
    assert art["summary"]["memory_recall"] == "ok"


@pytest.mark.parametrize("recall", ["mismatch", None])
def test_recall_off_run_is_error_and_never_becomes_the_baseline(tmp_path, monkeypatch, recall):
    """NEGATIVE CONTROL: a perfect said-vs-did run WITHOUT recall must not pass,
    and --update-baseline must not record it as the bar."""
    rc, art, rewrote = _drive_main(tmp_path, monkeypatch, recall, "--update-baseline")
    assert rc == 2 and art["status"] == "error"
    assert "WITHOUT recall" in art["reason"]
    assert art["summary"]["ok_rate"] == 1.0   # the replay itself was clean
    assert rewrote is False


def test_bad_interpreter_still_leaves_an_error_artifact(tmp_path, monkeypatch):
    results = tmp_path / "last.json"
    monkeypatch.setattr(sys, "argv", [
        "probe", "--results", str(results), "--baseline", str(tmp_path / "b.json"),
        "--trend", str(tmp_path / "t.jsonl"), "--service-dir", str(tmp_path),
        "--python", str(tmp_path / "missing-python")])
    assert vrp.main() == 2
    assert json.loads(results.read_text())["status"] == "error"


# ── measure_voice passes recall through and goes non-zero without it ────────
@pytest.mark.parametrize("recall,rc", [("ok", 0), ("mismatch", 1), (None, 1)])
def test_measure_voice_reports_recall(monkeypatch, tmp_path, recall, rc):
    replay_json = tmp_path / "replay.json"
    payload = {"counts": {"OK": 1}, "rows": [{"file": "a.wav", "verdict": "OK",
                                              "stt_ms": 10, "resolve_ms": 1, "brain_ms": 5}],
               "interpreter": {"python": "/x"}}
    if recall:
        payload["memory_recall"] = recall
    replay_json.write_text(json.dumps(payload))
    monkeypatch.setattr(mv.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    out = tmp_path / "out.json"
    args = SimpleNamespace(timeout=5, json=str(out))
    assert mv._run_and_report(["py"], str(tmp_path), str(replay_json), args) == rc
    assert json.loads(out.read_text())["memory_recall"] == (recall or "unreported")


def test_unit_runs_the_probe_on_the_service_venv():
    unit = (REPO / "scripts" / "setup" / "systemd" / "zoe-voice-regression.service").read_text()
    start = next(l for l in unit.splitlines() if l.startswith("ExecStart="))
    assert "%h/.zoe/venvs/zoe-data-py312/bin/python" in start
    assert "/usr/bin/python3" not in start   # negative control: the 3.10 launcher is gone
