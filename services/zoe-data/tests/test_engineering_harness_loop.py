"""Tests for the deterministic engineering harness loop helpers."""

import pytest
import importlib.util
import json
from pathlib import Path

pytestmark = pytest.mark.ci_safe


ROOT = Path(__file__).resolve().parents[3]
HARNESS_PATH = ROOT / "scripts" / "maintenance" / "engineering_harness_loop.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("engineering_harness_loop", HARNESS_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_pipeline_findings_does_not_double_count_explicit_scope_split(tmp_path, monkeypatch):
    store = tmp_path / "runs.jsonl"
    row = {
        "event": "scope_split_required",
        "task_ref": "multica:hard",
        "phase": "implement",
        "meta": {"reason": "TOO_BROAD"},
        "state": {
            "status": "blocked",
            "block_classification": "scope_split_required",
            "split_packet": {"kind": "scope_split_required"},
        },
    }
    store.write_text(json.dumps(row) + "\n", encoding="utf-8")
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(store))

    findings = _load_harness().parse_pipeline_findings(tail_lines=20)
    split_findings = [finding for finding in findings if finding["kind"] == "scope_split_required"]

    assert len(split_findings) == 1
    assert split_findings[0]["task_ref"] == "multica:hard"


def test_parse_pipeline_findings_does_not_double_count_fingerprint_split(tmp_path, monkeypatch):
    store = tmp_path / "runs.jsonl"
    row = {
        "event": "fingerprint_abort",
        "task_ref": "multica:hard",
        "phase": "implement",
        "meta": {"reason": "PROTOCOL_VIOLATION"},
        "state": {
            "status": "blocked",
            "block_classification": "scope_split_required",
            "split_packet": {"kind": "scope_split_required"},
        },
    }
    store.write_text(json.dumps(row) + "\n", encoding="utf-8")
    monkeypatch.setenv("ZOE_PIPELINE_STORE_PATH", str(store))

    findings = _load_harness().parse_pipeline_findings(tail_lines=20)
    critical_findings = [
        finding
        for finding in findings
        if finding["kind"] in {"fingerprint_abort", "scope_split_required"}
    ]

    assert len(critical_findings) == 1
    assert critical_findings[0]["kind"] == "fingerprint_abort"
    assert critical_findings[0]["task_ref"] == "multica:hard"
