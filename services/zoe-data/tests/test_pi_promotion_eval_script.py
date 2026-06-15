import asyncio
import importlib.util
import json
import sys
import types
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


def _install_fake_intent_router(monkeypatch, *, intent_name="weather", confidence=0.75):
    module = types.ModuleType("intent_router")

    async def detect_and_extract_intent(_text):
        if intent_name is None:
            return None
        return types.SimpleNamespace(name=intent_name, confidence=confidence)

    module.detect_and_extract_intent = detect_and_extract_intent
    monkeypatch.setitem(sys.modules, "intent_router", module)


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


def test_zoe_baseline_marks_fallback_router_only_as_not_comparable(monkeypatch):
    _install_fake_intent_router(monkeypatch)
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(module._run_zoe_baseline(case))

    assert result["baseline_kind"] == "router_only_not_comparable"
    assert result["baseline_comparable"] is False
    assert result["latency_ms"] == result["router_latency_ms"]


def test_zoe_baseline_uses_operator_fallback_latency_override(monkeypatch):
    _install_fake_intent_router(monkeypatch)
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(module._run_zoe_baseline(case, fallback_baseline_latency_ms=4321.0))

    assert result["baseline_kind"] == "operator_fallback_override"
    assert result["baseline_comparable"] is True
    assert result["latency_ms"] == 4321.0
    assert result["router_latency_ms"] >= 0


def test_zoe_baseline_uses_operator_extraction_failed_latency_override(monkeypatch):
    _install_fake_intent_router(monkeypatch, intent_name=None)
    module = _load_module()
    case = module.PiIntentEvalCase(
        "reminder", "remind me to call mum", "reminder_create", "reminders", "extraction_failed"
    )

    result = asyncio.run(module._run_zoe_baseline(case, extraction_failed_baseline_latency_ms=987.0))

    assert result["baseline_kind"] == "operator_extraction_failed_override"
    assert result["baseline_comparable"] is True
    assert result["latency_ms"] == 987.0


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
