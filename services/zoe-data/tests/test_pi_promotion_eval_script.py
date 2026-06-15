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


def _install_fake_zoe_agent(monkeypatch, calls, *, response="agent answer", sleep_seconds=0, raises=None):
    module = types.ModuleType("zoe_agent")

    async def run_zoe_agent(message, session_id, user_id="family-admin", **kwargs):
        calls.append({"message": message, "session_id": session_id, "user_id": user_id, "kwargs": kwargs})
        if sleep_seconds:
            await asyncio.sleep(sleep_seconds)
        if raises:
            raise raises
        return response

    module.run_zoe_agent = run_zoe_agent
    monkeypatch.setitem(sys.modules, "zoe_agent", module)


def _install_fake_pi_classifier(monkeypatch, *, result=None, sleep_seconds=0):
    module = types.ModuleType("pi_intent_classifier")

    async def classify_with_pi_intent_governor(_text):
        if sleep_seconds:
            await asyncio.sleep(sleep_seconds)
        return result

    module.classify_with_pi_intent_governor = classify_with_pi_intent_governor
    monkeypatch.setitem(sys.modules, "pi_intent_classifier", module)


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
    assert result["baseline_timed_out"] is False
    assert result["baseline_response_chars"] is None
    assert result["baseline_error"] is None


def test_zoe_baseline_uses_operator_fallback_latency_override(monkeypatch):
    _install_fake_intent_router(monkeypatch)
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(module._run_zoe_baseline(case, fallback_baseline_latency_ms=4321.0))

    assert result["baseline_kind"] == "operator_fallback_override"
    assert result["baseline_comparable"] is True
    assert result["latency_ms"] == 4321.0
    assert result["router_latency_ms"] >= 0
    assert result["baseline_timed_out"] is False
    assert result["baseline_response_chars"] is None
    assert result["baseline_error"] is None


def test_zoe_baseline_operator_override_wins_over_agent_measurement(monkeypatch):
    _install_fake_intent_router(monkeypatch)
    calls = []
    _install_fake_zoe_agent(monkeypatch, calls)
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(
        module._run_zoe_baseline(
            case,
            fallback_baseline_latency_ms=4321.0,
            measure_zoe_agent_baseline=True,
        )
    )

    assert result["baseline_kind"] == "operator_fallback_override"
    assert result["baseline_comparable"] is True
    assert result["latency_ms"] == 4321.0
    assert calls == []


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

    assert result["baseline_timed_out"] is False
    assert result["baseline_response_chars"] is None
    assert result["baseline_error"] is None


def test_zoe_baseline_can_measure_comparable_zoe_agent_fallback(monkeypatch):
    _install_fake_intent_router(monkeypatch, intent_name=None)
    calls = []
    _install_fake_zoe_agent(monkeypatch, calls, response="fallback answer")
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(module._run_zoe_baseline(case, measure_zoe_agent_baseline=True))

    assert result["baseline_kind"] == "zoe_agent_fallback_baseline"
    assert result["baseline_comparable"] is True
    assert result["latency_ms"] >= 0
    assert len(calls) == 1
    assert calls[0]["message"] == "rain later"
    assert result["baseline_timed_out"] is False
    assert result["baseline_response_chars"] == len("fallback answer")
    assert result["baseline_error"] is None
    assert calls[0]["user_id"] == "pi-eval"
    assert calls[0]["kwargs"]["history"] == []
    assert calls[0]["kwargs"]["db_memory_context"] == ""
    assert calls[0]["kwargs"]["portrait"] == ""
    assert calls[0]["kwargs"]["max_tokens_override"] == 256


def test_zoe_baseline_timeout_is_not_comparable(monkeypatch):
    _install_fake_intent_router(monkeypatch, intent_name=None)
    calls = []
    _install_fake_zoe_agent(monkeypatch, calls, sleep_seconds=0.05)
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(
        module._run_zoe_baseline(
            case,
            measure_zoe_agent_baseline=True,
            zoe_agent_baseline_timeout_seconds=0.001,
        )
    )

    assert result["baseline_kind"] == "zoe_agent_fallback_timeout"
    assert result["baseline_comparable"] is False
    assert result["latency_ms"] >= 0
    assert len(calls) == 1

    assert result["baseline_timed_out"] is True
    assert result["baseline_response_chars"] == 0

def test_run_pi_fast_no_result_is_not_timeout(monkeypatch):
    _install_fake_pi_classifier(monkeypatch, result=None)
    monkeypatch.setenv("ZOE_PI_INTENT_PREFILTER_ENABLED", "false")
    module = _load_module()
    case = module.PiIntentEvalCase("casual", "that movie was good", None, "chat", "fallback", negative=True)

    result = asyncio.run(
        module._run_pi(case, transport="rpc", enable_execution=True, local_model_configured=True)
    )

    assert result["intent"] is None
    assert result["timed_out"] is False
    assert result["prefilter_enabled"] == "false"
    assert result["correct"] is True



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
