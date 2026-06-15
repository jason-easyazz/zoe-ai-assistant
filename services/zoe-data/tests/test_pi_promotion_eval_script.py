import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_promotion_eval.py"
    spec = importlib.util.spec_from_file_location("pi_promotion_eval_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    old_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
    return module


def test_cli_loads_cases_file_without_default_cases(tmp_path, capsys):
    module = _load_module()
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        '{"case_id":"weather_file","text":"rain later","expected_intent":"weather","intent_group":"weather","route_class":"fallback","source":"intent_miss"}\n',
        encoding="utf-8",
    )

    exit_code = module.main(["--cases-file", str(cases_path), "--no-default-cases"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["eval_case_files"] == [str(cases_path)]
    assert [case["case_id"] for case in payload["eval_cases"]] == ["weather_file"]
    assert payload["eval_case_source_counts"] == {"intent_miss": 1}
    assert payload["promotion_report"]["sample_count"] == 0


def test_cli_combines_default_and_cases_file(tmp_path, capsys):
    module = _load_module()
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "weather_file",
                        "text": "rain later",
                        "expected_intent": "weather",
                        "intent_group": "weather",
                        "route_class": "fallback",
                        "source": "synthetic",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    exit_code = module.main(["--cases-file", str(cases_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    case_ids = {case["case_id"] for case in payload["eval_cases"]}
    assert "weather_file" in case_ids
    assert len(case_ids) > 1
