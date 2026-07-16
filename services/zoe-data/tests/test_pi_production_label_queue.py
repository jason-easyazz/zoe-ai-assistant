import pytest
import importlib.util
import json
import sys
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def _load_module(monkeypatch):
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_production_label_queue.py"
    spec = importlib.util.spec_from_file_location("pi_production_label_queue_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec.loader.exec_module(module)
    return module


def test_cli_reads_production_evidence_and_label_sidecar(tmp_path, capsys, monkeypatch):
    module = _load_module(monkeypatch)
    evidence_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    evidence_path.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                {
                    "text_hash": "weather",
                    "text_preview": "rain later",
                    "accepted": True,
                    "intent": "weather",
                    "intent_group": "weather",
                    "pi_intent": "weather",
                    "outcome_label": None,
                },
                {
                    "text_hash": "briefing",
                    "text_preview": "daily briefing",
                    "accepted": True,
                    "intent": "daily_briefing",
                    "intent_group": "daily_briefing",
                    "pi_intent": "daily_briefing",
                    "outcome_label": None,
                },
            ]
        ),
        encoding="utf-8",
    )
    labels_path.write_text(
        json.dumps({"text_hash": "briefing", "outcome_label": "daily_briefing", "source": "admin_review"}) + "\n",
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            "--evidence-path",
            str(evidence_path),
            "--labels-path",
            str(labels_path),
            "--format",
            "jsonl",
        ]
    )

    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    assert exit_code == 0
    assert [row["text_hash"] for row in rows] == ["weather"]
    assert rows[0]["suggested_outcome_label"] == "weather"
    assert rows[0]["label_example"] == {
        "outcome_label": "weather",
        "source": "admin_review",
        "text_hash": "weather",
    }


def test_cli_json_includes_paths_and_group_filter(tmp_path, capsys, monkeypatch):
    module = _load_module(monkeypatch)
    evidence_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    evidence_path.write_text(
        json.dumps({"text_hash": "weather", "accepted": True, "intent": "weather", "intent_group": "weather"}) + "\n"
        + json.dumps({"text_hash": "briefing", "accepted": True, "intent": "daily_briefing", "intent_group": "daily_briefing"}) + "\n",
        encoding="utf-8",
    )
    labels_path.write_text("", encoding="utf-8")

    exit_code = module.main(
        [
            "--evidence-path",
            str(evidence_path),
            "--labels-path",
            str(labels_path),
            "--group",
            "daily_briefing",
        ]
    )

    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["summary"]["path"] == str(evidence_path)
    assert payload["summary"]["labels_path"] == str(labels_path)
    assert [row["text_hash"] for row in payload["queue"]] == ["briefing"]
