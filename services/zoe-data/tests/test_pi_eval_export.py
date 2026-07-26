import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_eval_export.py"
    spec = importlib.util.spec_from_file_location("pi_eval_export_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    old_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
    return module


def _load_eval_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_promotion_eval.py"
    spec = importlib.util.spec_from_file_location("pi_promotion_eval_export_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    old_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
    return module


def test_exports_labeled_shadow_rows_as_sanitized_eval_cases():
    module = _load_module()
    rows = [
        {
            "text_preview": "email jason@example.com if rain later",
            "text_hash": "abc123456789ffff",
            "outcome_label": "weather",
            "route_class": "fallback",
            "source": "intent_miss",
        },
        {"text_preview": "unlabeled chat", "text_hash": "skipme"},
        {"text_preview": "upgrade yourself", "outcome_label": "extend_capability"},
    ]

    cases = module.export_eval_cases(rows, source="intent_miss", case_prefix="shadow")

    assert len(cases) == 1
    assert cases[0].case_id == "shadow_abc123456789"
    assert cases[0].text == "email [EMAIL] if rain later"
    assert cases[0].expected_intent == "weather"
    assert cases[0].intent_group == "weather"
    assert cases[0].source == "intent_miss"


def test_sanitize_eval_text_reuses_evidence_sanitizer_for_name_prefixes():
    module = _load_module()

    assert module.sanitize_eval_text("Jason Smith asked about the weather") == "[NAME] asked about the weather"
    assert module.sanitize_eval_text("Will Smith called about the meeting") == "[NAME] called about the meeting"
    assert module.sanitize_eval_text("Can Chen asked about the plan") == "[NAME] asked about the plan"


def test_exports_negative_chat_rows():
    module = _load_module()

    cases = module.export_eval_cases(
        [
            {
                "message": "I like the breakfast service here",
                "expected_intent": "chat",
                "route_class": "fallback",
            }
        ],
        source="known_failure",
        case_prefix="negative",
    )

    assert len(cases) == 1
    assert cases[0].negative is True
    assert cases[0].expected_intent is None
    assert cases[0].intent_group == "chat"


def test_skips_secret_and_overlong_rows():
    module = _load_module()
    rows = [
        {"text": "my api key is abc123", "expected_intent": "weather"},
        {"text": " ".join(["weather"] * 40), "expected_intent": "weather"},
    ]

    assert module.export_eval_cases(rows, source="chat_log", max_words=32) == []


def test_invalid_route_class_falls_back_to_default():
    module = _load_module()

    cases = module.export_eval_cases(
        [{"text": "rain later", "expected_intent": "weather", "route_class": "weird"}],
        source="intent_miss",
        default_route_class="fallback",
    )

    assert len(cases) == 1
    assert cases[0].route_class == "fallback"


def test_skips_duplicate_implicit_shadow_case_ids():
    module = _load_module()
    rows = [
        {"text_hash": "samehash", "text_preview": "rain later", "outcome_label": "weather"},
        {"text_hash": "samehash", "text_preview": "rain later", "outcome_label": "weather"},
    ]

    cases = module.export_eval_cases(rows, source="intent_miss", case_prefix="shadow")

    assert len(cases) == 1
    assert cases[0].case_id == "shadow_samehash"


def test_rejects_conflicting_duplicate_implicit_case_ids():
    module = _load_module()
    rows = [
        {"text_hash": "samehash", "text_preview": "rain later", "outcome_label": "weather"},
        {"text_hash": "samehash", "text_preview": "will it snow", "outcome_label": "weather"},
    ]

    with pytest.raises(ValueError, match="conflicting duplicate implicit"):
        module.export_eval_cases(rows, source="intent_miss", case_prefix="shadow")


def test_rejects_duplicate_exported_case_ids():
    module = _load_module()
    rows = [
        {"case_id": "same", "text": "rain later", "expected_intent": "weather"},
        {"case_id": "same", "text": "rain tonight", "expected_intent": "weather"},
    ]

    with pytest.raises(ValueError, match="duplicate eval case_id"):
        module.export_eval_cases(rows, source="intent_miss")


def test_skips_privileged_intent_with_explicit_intent_group():
    module = _load_module()
    rows = [
        {
            "text_preview": "upgrade yourself",
            "expected_intent": "extend_capability",
            "intent_group": "weather",
        }
    ]

    cases = module.export_eval_cases(rows, source="intent_miss")
    assert len(cases) == 0



def test_cli_applies_sidecar_labels_before_export(tmp_path, capsys):
    module = _load_module()
    source_path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    output_path = tmp_path / "cases.jsonl"
    source_path.write_text(
        json.dumps({"text_preview": "rain later", "text_hash": "abc123", "route_class": "fallback"}) + "\n",
        encoding="utf-8",
    )
    labels_path.write_text(
        json.dumps({"text_hash": "abc123", "outcome_label": "weather"}) + "\n",
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            str(source_path),
            "--labels-file",
            str(labels_path),
            "--output",
            str(output_path),
            "--source",
            "intent_miss",
            "--case-prefix",
            "shadow",
            "--summary",
        ]
    )

    summary = json.loads(capsys.readouterr().err)
    rows = [json.loads(line) for line in output_path.read_text().splitlines()]
    assert exit_code == 0
    assert summary == {"exported_cases": 1, "input_rows": 1, "label_count": 1}
    assert rows[0]["case_id"] == "shadow_abc123"
    assert rows[0]["expected_intent"] == "weather"
    assert rows[0]["intent_group"] == "weather"

def test_cli_output_feeds_pi_promotion_eval(tmp_path, capsys):
    export_module = _load_module()
    eval_module = _load_eval_module()
    source_path = tmp_path / "shadow.jsonl"
    output_path = tmp_path / "cases.jsonl"
    source_path.write_text(
        json.dumps(
            {
                "text_preview": "will it rain later",
                "text_hash": "weatherhash1234",
                "outcome_label": "weather",
                "route_class": "fallback",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    export_code = export_module.main(
        [str(source_path), "--output", str(output_path), "--source", "intent_miss", "--case-prefix", "shadow"]
    )
    assert export_code == 0

    eval_code = eval_module.main(["--cases-file", str(output_path), "--no-default-cases"])

    payload = json.loads(capsys.readouterr().out)
    assert eval_code == 0
    assert [case["case_id"] for case in payload["eval_cases"]] == ["shadow_weatherhash1"]
    assert payload["eval_case_source_counts"] == {"intent_miss": 1}
