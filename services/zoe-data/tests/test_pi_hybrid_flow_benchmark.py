import pytest
import importlib.util
import json
import sys
import types
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def _install_fake_lab(monkeypatch, *, result_by_text):
    module = types.ModuleType("pi_intent_lab")
    calls = []

    async def compare_pi_intent_lab(text, **kwargs):
        calls.append({"text": text, "kwargs": kwargs})
        return result_by_text[text]

    module.compare_pi_intent_lab = compare_pi_intent_lab
    monkeypatch.setitem(sys.modules, "pi_intent_lab", module)
    return calls


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_hybrid_flow_benchmark.py"
    spec = importlib.util.spec_from_file_location("pi_hybrid_flow_benchmark_test", path)
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


def _lab_result(*, intent, cue_ms=0.05, pi_ms=2500.0, fulfillment_ms=100.0, fulfilled=True):
    return {
        "zoe_router": {"intent": None},
        "pi": {
            "intent": intent,
            "confidence": 0.95,
            "latency_ms": pi_ms,
            "timed_out": False,
            "error": None,
        },
        "safe_fulfillment": {
            "requested": True,
            "attempted": fulfilled,
            "allowed": fulfilled,
            "latency_ms": fulfillment_ms if fulfilled else None,
            "timed_out": False,
            "error": None,
            "response_chars": 12 if fulfilled else 0,
            "response_preview": "It is 18.5 C.",
        },
        "simulated_hybrid_flow": {
            "cue_latency_ms": cue_ms,
            "final_completion_latency_ms": pi_ms + (fulfillment_ms if fulfilled else 0.0),
            "natural_flow_candidate": True,
        },
    }


def test_hybrid_flow_report_summarizes_safe_fulfillment(monkeypatch):
    calls = _install_fake_lab(monkeypatch, result_by_text={"rain later": _lab_result(intent="weather")})
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback", source="intent_miss")

    observations = module.asyncio.run(
        module.run_benchmark(
            [case],
            repeat=2,
            run_pi=True,
            allow_execution=True,
            local_model_configured=True,
            include_safe_fulfillment=True,
        )
    )
    report = module.build_report(
        [case],
        observations,
        repeat=2,
        run_pi=True,
        transport="rpc",
        include_safe_fulfillment=True,
        include_observations=True,
    )

    assert len(calls) == 2
    assert calls[0]["kwargs"]["include_safe_fulfillment"] is True
    assert calls[0]["kwargs"]["include_hybrid_status"] is False
    assert report["benchmark_kind"] == "pi_hybrid_flow_observations_not_promotion_samples"
    assert "FastAPI startup initialization" in report["note"]
    assert report["safe_fulfillment_side_effects"] == "read_only_external_only"
    assert report["summary"]["overall"]["pi_accuracy"] == 1.0
    assert report["summary"]["overall"]["natural_flow_rate"] == 1.0
    assert report["summary"]["overall"]["safe_fulfillment_success_rate"] == 1.0
    assert report["summary"]["overall"]["final_completion_latency_ms"]["p95"] == 2600.0
    assert report["observations"][0]["response_preview"] == "It is 18.5 C."


def test_empty_safe_fulfillment_response_is_not_success(monkeypatch):
    calls = _install_fake_lab(
        monkeypatch,
        result_by_text={
            "rain later": {
                **_lab_result(intent="weather"),
                "safe_fulfillment": {
                    "requested": True,
                    "attempted": True,
                    "allowed": True,
                    "latency_ms": 10.0,
                    "timed_out": False,
                    "error": None,
                    "response_chars": 0,
                    "response_preview": "",
                },
            }
        },
    )
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    observations = module.asyncio.run(
        module.run_benchmark([case], repeat=1, run_pi=True, include_safe_fulfillment=True)
    )
    report = module.build_report(
        [case],
        observations,
        repeat=1,
        run_pi=True,
        transport="rpc",
        include_safe_fulfillment=True,
    )

    assert len(calls) == 1
    assert observations[0]["safe_fulfillment_success"] is False
    assert report["summary"]["overall"]["safe_fulfillment_success_rate"] == 0.0


def test_empty_report_keeps_stats_schema(monkeypatch):
    _install_fake_lab(monkeypatch, result_by_text={})
    module = _load_module()

    report = module.build_report(
        [],
        [],
        repeat=1,
        run_pi=False,
        transport="rpc",
        include_safe_fulfillment=False,
    )
    overall = report["summary"]["overall"]

    assert overall["observation_count"] == 0
    assert overall["pi_accuracy"] is None
    assert overall["pi_timeout_rate"] is None
    assert overall["natural_flow_rate"] is None
    assert overall["safe_fulfillment_success_rate"] is None


def test_pi_disabled_report_uses_null_pi_metrics(monkeypatch):
    _install_fake_lab(monkeypatch, result_by_text={})
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")
    observations = [
        {
            "case_id": "weather",
            "intent_group": "weather",
            "source": "intent_miss",
            "pi_correct": False,
            "pi_timed_out": False,
            "natural_flow_candidate": False,
        }
    ]

    report = module.build_report(
        [case],
        observations,
        repeat=1,
        run_pi=False,
        transport="rpc",
        include_safe_fulfillment=False,
    )

    for stats in (
        report["summary"]["overall"],
        report["summary"]["by_intent_group"]["weather"],
        report["summary"]["by_source"]["intent_miss"],
    ):
        assert stats["pi_accuracy"] is None
        assert stats["pi_timeout_rate"] is None
        assert stats["natural_flow_rate"] is None


def test_cli_runs_cases_file(monkeypatch, tmp_path, capsys):
    _install_fake_lab(monkeypatch, result_by_text={"rain later": _lab_result(intent="weather")})
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
            "--run-pi",
            "--allow-execution",
            "--local-model-configured",
            "--include-safe-fulfillment",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["unique_case_count"] == 1
    assert payload["observation_count"] == 1
    assert payload["safe_fulfillment_enabled"] is True
    assert payload["summary"]["overall"]["pi_accuracy"] == 1.0
