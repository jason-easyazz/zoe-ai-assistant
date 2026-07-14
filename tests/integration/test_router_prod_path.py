"""Production-path two-stage router eval as a pytest gate.

Runs labs/router-90-campaign/prod_path_eval.py — the frozen 81-case corpus
through the REAL `semantic_router.route_two_stage()` with the FunctionGemma
r2 sidecar launched ad hoc on :11436 — and asserts the campaign gates
(overall >= 90%, chat false positives == 0, latency p50 < 600 ms).

Sidecar-dependent and Jetson-only, so it is OPT-IN: skipped unless
ZOE_ROUTER_PROD_PATH_EVAL=1. Not ci_safe (needs the lab GGUF, fastembed and
~500 MB free RAM); the integration marker keeps it out of the default lanes
per tests/AGENTS.md.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
EVAL = REPO / "labs" / "router-90-campaign" / "prod_path_eval.py"


@pytest.mark.skipif(
    os.environ.get("ZOE_ROUTER_PROD_PATH_EVAL") != "1",
    reason="opt-in sidecar eval: set ZOE_ROUTER_PROD_PATH_EVAL=1 "
           "(launches llama-server on :11436, needs the lab r2 GGUF)")
def test_prod_path_gates(tmp_path):
    out_json = tmp_path / "prod-path.json"
    proc = subprocess.run(
        [sys.executable, str(EVAL), "--launch-sidecar", "--no-assert",
         "--out", str(out_json)],
        cwd=REPO, capture_output=True, text=True, timeout=1800)
    assert proc.returncode == 0, (
        f"eval harness failed rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}")
    summary = json.loads(out_json.read_text())["summary"]
    assert summary["n"] == 81
    assert summary["accuracy_overall_pct"] >= 90.0, summary
    assert summary["chat_false_positive_pct"] == 0.0, summary
    assert summary["latency_ms_p50"] < 600.0, summary
    # fallback rate is informational but must be present in the report
    assert "brain_fallback_pct" in summary and "fallback_by_source" in summary
