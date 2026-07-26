import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe


def _load_module(monkeypatch):
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_shadow_label_queue.py"
    spec = importlib.util.spec_from_file_location("pi_shadow_label_queue_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec.loader.exec_module(module)
    return module


def test_build_label_queue_prioritizes_unlabeled_real_pi_candidates(monkeypatch):
    module = _load_module(monkeypatch)
    payload = module.build_label_queue(
        [
            {
                "text_hash": "weather-old",
                "text_preview": "new rain",
                "ts": 2,
                "pi_intent": "weather",
                "pi_intent_group": "weather",
                "pi_confidence": 0.95,
                "pi_latency_ms": 100,
                "zoe_latency_ms": 800,
            },
            {
                "text_hash": "weather-old",
                "text_preview": "old rain",
                "ts": 1,
                "pi_intent": "weather",
                "pi_intent_group": "weather",
                "pi_confidence": 0.5,
                "pi_latency_ms": 300,
                "zoe_latency_ms": 900,
            },
            {
                "text_hash": "timer",
                "text_preview": "set a timer",
                "pi_intent": "timer_create",
                "pi_intent_group": "timers",
                "pi_confidence": 0.8,
                "outcome_label": "timer_create",
            },
            {
                "text_hash": "chat",
                "text_preview": "how are you",
                "pi_intent": None,
                "pi_no_result": True,
            },
        ],
        limit=10,
    )

    assert payload["summary"]["raw_record_count"] == 4
    assert payload["summary"]["unique_text_count"] == 3
    assert payload["summary"]["skipped_labeled_count"] == 1
    assert payload["summary"]["skipped_no_result_count"] == 1
    assert payload["summary"]["queue_count_by_group"] == {"weather": 1}
    assert len(payload["queue"]) == 1
    row = payload["queue"][0]
    assert row["text_hash"] == "weather-old"
    assert row["text_preview"] == "new rain"
    assert row["suggested_outcome_label"] == "weather"
    assert row["label_example"]["outcome_label"] == "weather"


def test_build_label_queue_can_include_no_result_as_negative_chat(monkeypatch):
    module = _load_module(monkeypatch)

    payload = module.build_label_queue(
        [
            {
                "text_hash": "chat",
                "text_preview": "how are you",
                "pi_intent": None,
                "pi_no_result": True,
                "route_class": "fallback",
            }
        ],
        include_no_result=True,
    )

    row = payload["queue"][0]
    assert row["intent_group"] == "chat"
    assert row["suggested_outcome_label"] is None
    assert row["suggested_negative"] is True
    assert row["label_example"]["negative"] is True


def test_build_label_queue_filters_groups_and_rejects_privileged_groups(monkeypatch):
    module = _load_module(monkeypatch)
    records = [
        {"text_hash": "weather", "text_preview": "rain", "pi_intent": "weather", "pi_intent_group": "weather"},
        {"text_hash": "timer", "text_preview": "timer", "pi_intent": "timer_create", "pi_intent_group": "timers"},
    ]

    payload = module.build_label_queue(records, groups=["weather"], limit=10)

    assert [row["text_hash"] for row in payload["queue"]] == ["weather"]
    assert payload["summary"]["skipped_group_count"] == 1
    with pytest.raises(ValueError, match="unsupported intent group"):
        module.build_label_queue(records, groups=["self_evolution"])


def test_cli_reads_shadow_and_label_sidecar(tmp_path, capsys, monkeypatch):
    module = _load_module(monkeypatch)
    shadow_path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    shadow_path.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                {"text_hash": "weather", "text_preview": "rain", "pi_intent": "weather", "pi_intent_group": "weather"},
                {"text_hash": "timer", "text_preview": "timer", "pi_intent": "timer_create", "pi_intent_group": "timers"},
            ]
        ),
        encoding="utf-8",
    )
    labels_path.write_text(json.dumps({"text_hash": "timer", "outcome_label": "timer_create"}) + "\n", encoding="utf-8")

    exit_code = module.main(
        [
            "--shadow-path",
            str(shadow_path),
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
