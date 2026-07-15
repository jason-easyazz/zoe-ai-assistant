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
