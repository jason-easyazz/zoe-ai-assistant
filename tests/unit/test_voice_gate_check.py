"""Heartbeat-check tests for the voice replay gate's deploy-path assertion.

"A gate that can silently not-run is not a gate." The voice replay gate
(scripts/maintenance/voice_regression_probe.py) writes a result artifact on
EVERY run; scripts/maintenance/voice_gate_check.py is the cheap deploy-path
counterpart that refuses a voice-path deploy unless that artifact proves a
FRESH pass. These tests pin the three load-bearing cases the fix exists for:
a missing artifact blocks, a stale artifact blocks, a fresh pass clears — plus
skip/error/baseline-drift (skip != pass) and the voice-path diff gate.

Pure-logic only (stdlib), so this runs in the fast `ci_safe` lane.
"""
from __future__ import annotations

import calendar
import importlib.util
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load(mod_name: str, rel: str):
    spec = importlib.util.spec_from_file_location(mod_name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod  # register before exec (dataclass/annotation resolution)
    spec.loader.exec_module(mod)
    return mod


vgc = _load("voice_gate_check", "scripts/maintenance/voice_gate_check.py")
vrp = _load("voice_regression_probe", "scripts/maintenance/voice_regression_probe.py")

NOW = calendar.timegm(time.strptime("2026-07-15T12:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
DAY = 24 * 3600.0


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def artifact(status="pass", age_h=1.0, baseline_created="2026-07-14T00:00:00Z",
             n_samples=20):
    return {
        "status": status,
        "timestamp": _iso(NOW - age_h * 3600.0),
        "created_at": _iso(NOW - age_h * 3600.0),
        "reason": "",
        "said_vs_did_regressions": [],
        "per_stage_speed_deltas": {},
        "baseline_ref": {"path": "/x", "created_at": baseline_created, "ok_rate": 0.9},
        "summary": {"n_samples": n_samples, "ok_rate": 0.95},
    }


# --- the three cases the fix exists for ------------------------------------
def test_missing_artifact_blocks():
    """An ABSENT artifact must never be read as 'nothing wrong' (skip != pass)."""
    ok, why = vgc.evaluate(None, now_epoch=NOW, max_age_s=DAY)
    assert ok is False
    assert "never ran" in why or "NOT a pass" in why


def test_stale_artifact_blocks():
    """A pass that is older than the freshness window is not proof the CURRENT
    voice path works — it must not clear a deploy."""
    ok, why = vgc.evaluate(artifact(status="pass", age_h=48.0),
                           now_epoch=NOW, max_age_s=DAY)
    assert ok is False
    assert "STALE" in why


def test_fresh_pass_clears():
    ok, why = vgc.evaluate(artifact(status="pass", age_h=1.0),
                           now_epoch=NOW, max_age_s=DAY,
                           baseline={"created_at": "2026-07-14T00:00:00Z"})
    assert ok is True
    assert "PASS" in why


# --- skip / fail / error are not a pass ------------------------------------
@pytest.mark.parametrize("status", ["skip", "fail", "error", None, "unknown"])
def test_non_pass_status_blocks(status):
    ok, why = vgc.evaluate(artifact(status=status, age_h=0.5),
                           now_epoch=NOW, max_age_s=DAY)
    assert ok is False
    assert "NOT a pass" in why


# --- baseline identity ------------------------------------------------------
def test_baseline_drift_blocks():
    """A fresh pass produced against an OLD baseline must not clear the deploy
    once the baseline has moved."""
    art = artifact(status="pass", age_h=1.0, baseline_created="2026-07-01T00:00:00Z")
    ok, why = vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY,
                           baseline={"created_at": "2026-07-14T00:00:00Z"})
    assert ok is False
    assert "bar moved" in why


def test_baseline_check_is_lenient_when_baseline_identity_missing():
    """The identity check can only tighten — a baseline without created_at, or no
    baseline at all, must not manufacture a mismatch."""
    art = artifact(status="pass", age_h=1.0)
    assert vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY, baseline=None)[0] is True
    assert vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY, baseline={})[0] is True


def test_unparseable_timestamp_blocks():
    art = artifact()
    art["timestamp"] = art["created_at"] = "not-a-date"
    ok, why = vgc.evaluate(art, now_epoch=NOW, max_age_s=DAY)
    assert ok is False
    assert "timestamp" in why


# --- the voice-path diff gate ----------------------------------------------
def test_voice_path_detection():
    pats = vgc.VOICE_PATH_PATTERNS
    changed = [
        "services/zoe-data/routers/voice_tts.py",
        "services/zoe-ui/index.html",
        "scripts/setup/kokoro_sidecar.py",
        "docs/README.md",
    ]
    hits = vgc.touched_voice_files(changed, pats)
    assert "services/zoe-data/routers/voice_tts.py" in hits
    assert "scripts/setup/kokoro_sidecar.py" in hits  # *kokoro* glob
    assert "services/zoe-ui/index.html" not in hits
    assert "docs/README.md" not in hits


def test_non_voice_change_needs_no_gate():
    assert vgc.touched_voice_files(
        ["services/zoe-ui/index.html", "docs/x.md"], vgc.VOICE_PATH_PATTERNS) == []


def test_parse_iso_z_roundtrips_utc():
    assert vgc.parse_iso_z("2026-07-15T12:00:00Z") == NOW
    assert vgc.parse_iso_z(None) is None
    assert vgc.parse_iso_z("garbage") is None


# --- the producer side: skip/error leave a non-pass artifact, never absent --
class _Args:
    """Minimal stand-in for the probe's argparse namespace."""
    def __init__(self, tmp_path):
        self.results = tmp_path / "voice_regression_last.json"
        self.trend = tmp_path / "trend.jsonl"
        self.baseline = tmp_path / "baseline.json"


def test_probe_skip_emits_non_pass_artifact(tmp_path):
    """The bug this whole change addresses: a skip must leave an artifact whose
    status != 'pass', so the deploy checker sees skip != pass rather than an
    absent file it could misread as 'nothing wrong'."""
    import json as _json
    args = _Args(tmp_path)
    vrp.emit_result(args, status="skip", summary=dict(vrp.EMPTY_SUMMARY),
                    said_vs_did=[], speed_deltas={}, baseline={}, reason="box too tight")
    assert args.results.exists(), "skip produced NO artifact — the exact silent-gate bug"
    payload = _json.loads(args.results.read_text())
    assert payload["status"] == "skip"
    # and the checker must block on it
    ok, why = vgc.evaluate(payload, now_epoch=time.time(), max_age_s=DAY)
    assert ok is False
    assert "NOT a pass" in why


def test_probe_pass_artifact_clears_the_checker(tmp_path):
    """End-to-end contract: a status='pass' artifact the probe writes is accepted
    by the deploy checker while fresh."""
    import json as _json
    args = _Args(tmp_path)
    summary = {"n_samples": 20, "ok_rate": 0.95, "medians_ms": {"stt_ms": 100}}
    vrp.emit_result(args, status="pass", summary=summary, said_vs_did=[],
                    speed_deltas={}, baseline={"created_at": "2026-07-14T00:00:00Z"})
    payload = _json.loads(args.results.read_text())
    assert payload["status"] == "pass"
    ok, _ = vgc.evaluate(payload, now_epoch=time.time(), max_age_s=DAY,
                         baseline={"created_at": "2026-07-14T00:00:00Z"})
    assert ok is True


# --- scope classification (the PR-time gate's first half) -------------------
# `--scope-only` runs on a hosted runner where the replay artifact CANNOT exist.
# It classifies and never asserts, which is what lets the required `voice-gate`
# context report a conclusion on every PR instead of only on voice-path ones.
def test_scope_clear_when_no_voice_files():
    needs, hits, why = vgc.scope_verdict(
        ["docs/PLANS.md", "services/zoe-ui/index.html"], vgc.VOICE_PATH_PATTERNS)
    assert needs is False
    assert hits == []
    assert "not required" in why


def test_scope_requires_gate_on_voice_files():
    needs, hits, why = vgc.scope_verdict(
        ["docs/PLANS.md", "services/zoe-data/fast_tiers.py"], vgc.VOICE_PATH_PATTERNS)
    assert needs is True
    assert hits == ["services/zoe-data/fast_tiers.py"]
    assert "REQUIRED" in why


def test_scope_fails_closed_when_the_diff_is_unknown():
    """An uncomputable diff is NOT 'no voice files changed'. Reading it as clear
    would let a voice-path PR through on a git failure — the same fail-closed rule
    the deploy path uses, applied one gate earlier."""
    needs, hits, why = vgc.scope_verdict(None, vgc.VOICE_PATH_PATTERNS)
    assert needs is True
    assert hits == []
    assert "not a pass" in why


def test_scope_only_always_exits_zero(tmp_path, monkeypatch, capsys):
    """The scope job CLASSIFIES; it must never be the thing that fails.

    If scope could exit non-zero it would fail the job, and the summary job would
    then have to distinguish 'scope failed' from 'scope says voice' — the exact
    ambiguity that produces a required check with no conclusion. Both branches,
    including the unreadable-diff branch, exit 0."""
    import os as _os
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert _os.environ["GITHUB_OUTPUT"] == str(out)

    # unreadable range (not a git repo) -> fail-closed classification, still exit 0
    assert vgc.main(["--scope-only", "--repo", str(tmp_path), "--diff", "a...b"]) == 0
    assert "voice=true" in out.read_text()
    assert "VOICE" in capsys.readouterr().out

    # no --diff at all -> unknown -> gate required, still exit 0
    out.write_text("")
    assert vgc.main(["--scope-only", "--repo", str(tmp_path)]) == 0
    assert "voice=true" in out.read_text()


def test_scope_only_reports_clear_for_a_non_voice_diff(tmp_path, monkeypatch, capsys):
    """The common case, end to end through the CLI: a real git repo whose diff
    touches no voice file must publish voice=false so the Jetson is never involved."""
    import os as _os
    import subprocess as sp
    repo = tmp_path / "r"
    repo.mkdir()
    env = {**_os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
    sp.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "README.md").write_text("base\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True, env=env)
    base = sp.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    (repo / "docs.md").write_text("docs only\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "docs"], check=True, env=env)

    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert vgc.main(["--scope-only", "--repo", str(repo), "--diff", f"{base}...HEAD"]) == 0
    assert "voice=false" in out.read_text()
    assert "CLEAR" in capsys.readouterr().out

    # and the same repo with a voice-path file flips it to true
    (repo / "services" / "zoe-data").mkdir(parents=True)
    (repo / "services" / "zoe-data" / "fast_tiers.py").write_text("x = 1\n")
    sp.run(["git", "-C", str(repo), "add", "-A"], check=True)
    sp.run(["git", "-C", str(repo), "commit", "-qm", "voice"], check=True, env=env)
    out.write_text("")
    assert vgc.main(["--scope-only", "--repo", str(repo), "--diff", f"{base}...HEAD"]) == 0
    body = out.read_text()
    assert "voice=true" in body
    assert "services/zoe-data/fast_tiers.py" in body


def test_w11_delivery_modules_are_voice_path():
    """A deploy touching only the W11 mapper or the waterfall changes what Zoe
    SOUNDS like and must hit the replay gate (Codex P1, #1579 — which was
    itself such a deploy and would have bypassed it)."""
    from voice_gate_check import touched_voice_files, voice_path_patterns
    touched = touched_voice_files(
        ["services/zoe-data/voice_delivery.py",
         "services/zoe-data/tts_waterfall.py",
         "docs/PLANS.md"],
        voice_path_patterns())
    assert touched == ["services/zoe-data/voice_delivery.py",
                       "services/zoe-data/tts_waterfall.py"], touched
