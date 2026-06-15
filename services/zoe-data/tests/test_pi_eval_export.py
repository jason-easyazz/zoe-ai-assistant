import importlib.util
import json
import sys
from pathlib import Path

import pytest


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


def test_rejects_duplicate_exported_case_ids():
    module = _load_module()
    rows = [
        {"case_id": "same", "text": "rain later", "expected_intent": "weather"},
        {"case_id": "same", "text": "rain tonight", "expected_intent": "weather"},
    ]

    with pytest.raises(ValueError, match="duplicate eval case_id"):
        module.export_eval_cases(rows, source="intent_miss")


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
