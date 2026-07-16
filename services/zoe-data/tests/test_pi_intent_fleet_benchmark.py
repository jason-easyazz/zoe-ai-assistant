import pytest
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_intent_fleet_benchmark.py"
    spec = importlib.util.spec_from_file_location("pi_intent_fleet_benchmark_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    old_path = list(sys.path)
    old_modules = set(sys.modules)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
        for name in set(sys.modules) - old_modules:
            sys.modules.pop(name, None)
    return module


def _install_fake_intent_router(monkeypatch, intent_by_text):
    module = types.ModuleType("intent_router")

    async def detect_and_extract_intent(text):
        intent_name = intent_by_text.get(text)
        if intent_name is None:
            return None
        return types.SimpleNamespace(name=intent_name, confidence=0.91)

    module.detect_and_extract_intent = detect_and_extract_intent
    monkeypatch.setitem(sys.modules, "intent_router", module)


def _install_fake_zoe_agent(monkeypatch, calls):
    module = types.ModuleType("zoe_agent")

    async def run_zoe_agent(message, session_id, user_id="family-admin", **kwargs):
        calls.append({"message": message, "session_id": session_id, "user_id": user_id, "kwargs": kwargs})
        return "fallback answer"

    module.run_zoe_agent = run_zoe_agent
    monkeypatch.setitem(sys.modules, "zoe_agent", module)


def _install_fake_pi_classifier(monkeypatch, intent_by_text):
    module = types.ModuleType("pi_intent_classifier")

    async def classify_with_pi_intent_governor(text):
        intent_name = intent_by_text.get(text)
        if intent_name is None:
            return None
        return types.SimpleNamespace(intent=intent_name, confidence=0.88)

    module.classify_with_pi_intent_governor = classify_with_pi_intent_governor
    monkeypatch.setitem(sys.modules, "pi_intent_classifier", module)


def test_build_report_keeps_repeats_separate_from_unique_cases():
    module = _load_module()
    cases = [
        module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback", source="intent_miss"),
        module.PiIntentEvalCase("casual", "I like breakfast", None, "chat", "fallback", negative=True),
    ]
    observations = [
        {
            "case_id": "weather",
            "intent_group": "weather",
            "route_class": "fallback",
            "source": "intent_miss",
            "zoe_correct": False,
            "zoe_latency_ms": 900.0,
            "pi_correct": True,
            "pi_latency_ms": 300.0,
            "pi_timed_out": False,
        },
        {
            "case_id": "weather",
            "intent_group": "weather",
            "route_class": "fallback",
            "source": "intent_miss",
            "zoe_correct": False,
            "zoe_latency_ms": 800.0,
            "pi_correct": True,
            "pi_latency_ms": 310.0,
            "pi_timed_out": False,
        },
        {
            "case_id": "casual",
            "intent_group": "chat",
            "route_class": "fallback",
            "source": "synthetic",
            "zoe_correct": True,
            "zoe_latency_ms": 2.0,
            "pi_correct": True,
            "pi_latency_ms": 40.0,
            "pi_timed_out": False,
        },
    ]

    report = module.build_report(cases, observations, repeat=2, run_pi=True, transport="rpc")

    assert report["benchmark_kind"] == "speed_accuracy_observations_not_promotion_samples"
    assert report["unique_case_count"] == 2
    assert report["observation_count"] == 3
    assert "unique labeled evidence" in report["note"]
    assert report["summary"]["overall"]["unique_case_count"] == 2
    assert report["summary"]["overall"]["observation_count"] == 3
    assert report["summary"]["by_source"]["intent_miss"]["unique_case_count"] == 1
    assert report["summary"]["by_baseline_lane"]["fallback:unknown"]["unique_case_count"] == 2


def test_run_benchmark_repeats_without_pi(monkeypatch):
    _install_fake_intent_router(monkeypatch, {"rain later": "weather"})
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    observations = asyncio.run(module.run_benchmark([case], repeat=3, run_pi=False))

    assert [item["repeat_index"] for item in observations] == [1, 2, 3]
    assert {item["zoe_intent"] for item in observations} == {"weather"}
    assert {item["pi_intent"] for item in observations} == {None}
    assert {item["pi_correct"] for item in observations} == {None}


def test_cli_runs_cases_file_with_pi_and_agent_baseline(tmp_path, monkeypatch, capsys):
    _install_fake_intent_router(monkeypatch, {"rain later": None})
    _install_fake_pi_classifier(monkeypatch, {"rain later": "weather"})
    agent_calls = []
    _install_fake_zoe_agent(monkeypatch, agent_calls)
    module = _load_module()
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        "{\"case_id\":\"weather\",\"text\":\"rain later\",\"expected_intent\":\"weather\",\"intent_group\":\"weather\",\"route_class\":\"fallback\",\"source\":\"intent_miss\"}\n",
        encoding="utf-8",
    )

    exit_code = module.main(
        [
            "--cases-file",
            str(cases_path),
            "--no-default-cases",
            "--repeat",
            "2",
            "--run-pi",
            "--allow-execution",
            "--local-model-configured",
            "--measure-zoe-agent-baseline",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["unique_case_count"] == 1
    assert payload["observation_count"] == 2
    assert payload["summary"]["overall"]["pi_accuracy"] == 1.0
    assert payload["summary"]["overall"]["unique_case_count"] == 1
    assert len(agent_calls) == 2
