import pytest
import importlib.util
import json
import sys
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "conversation_flow_probe.py"
    spec = importlib.util.spec_from_file_location("conversation_flow_probe_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    for name in ("voice_presence", "pi_intent_fleet_benchmark", "zoe_pi_promotion"):
        sys.modules.pop(name, None)
    old_path = list(sys.path)
    old_modules = set(sys.modules)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
        for name in set(sys.modules) - old_modules:
            sys.modules.pop(name, None)
    return module


def test_presence_report_is_honest_about_unconfigured_wake_ack():
    module = _load_module()
    report = module.build_conversation_flow_report(env={}, repeat=3, perceived_budget_ms=150.0)

    assert report["report_kind"] == "zoe_conversation_flow_probe"
    assert report["presence"]["wake_ack"]["configured"] is False
    assert report["presence"]["processing_ack"]["configured"] is True
    assert report["presence"]["gate"]["processing_ack_ready"] is True
    assert "wake_ack_not_configured" in report["presence"]["gate"]["blockers"]
    assert report["decision"]["state"] == "buffer_ready_pi_shadow"


def test_demo_defaults_make_full_wake_to_processing_flow_ready():
    module = _load_module()
    env = module._demo_env({})
    report = module.build_conversation_flow_report(env=env, repeat=3, perceived_budget_ms=150.0)

    assert report["presence"]["wake_ack"]["configured"] is True
    assert report["presence"]["processing_ack"]["configured"] is True
    assert report["presence"]["gate"]["full_wake_to_processing_ready"] is True
    assert report["presence"]["wake_ack"]["first_payload"]["events"][1]["text"] == "Understood."
    assert report["presence"]["processing_ack"]["first_payload"]["event"]["text"] == "Let me check."


def test_presence_env_loader_reads_only_presence_keys(tmp_path):
    module = _load_module()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=do-not-load",
                'ZOE_WAKE_ACK_PHRASES="Yes Jason.|Hi Jason."',
                "ZOE_PROCESSING_ACK_PHRASES='Let me check.|One moment.'",
            ]
        ),
        encoding="utf-8",
    )

    env = module._presence_env_from_files({}, env_files=[env_path])

    assert env["ZOE_WAKE_ACK_PHRASES"] == "Yes Jason.|Hi Jason."
    assert env["ZOE_PROCESSING_ACK_PHRASES"] == "Let me check.|One moment."
    assert "OPENAI_API_KEY" not in env


def test_pi_benchmark_winner_is_reported_without_promotion_claim():
    module = _load_module()
    pi_benchmark = {
        "benchmark_kind": "speed_accuracy_observations_not_promotion_samples",
        "observation_count": 2,
        "unique_case_count": 1,
        "summary": {
            "by_intent_group": {
                "weather": {
                    "pi_accuracy": 1.0,
                    "accuracy_delta": 0.5,
                    "zoe_latency_ms": {"p95": 1200.0},
                    "pi_latency_ms": {"p95": 300.0},
                }
            }
        },
    }

    report = module.build_conversation_flow_report(env=module._demo_env({}), repeat=1, pi_benchmark=pi_benchmark)

    assert report["decision"]["pi_candidate"]["state"] == "candidate_speed_accuracy_win"
    assert report["decision"]["pi_candidate"]["winning_groups"] == ["weather"]
    assert "unique labeled evidence" in " ".join(report["decision"]["recommendations"])


def test_pi_http_flow_natural_observation_is_reported_without_promotion_claim():
    module = _load_module()
    pi_http_flow = {
        "report_kind": "pi_intent_lab_http_probe",
        "observation_count": 2,
        "unique_case_count": 2,
        "conversation_contract": {"production_route_change": False},
        "summary": {
            "overall": {
                "natural_flow_rate": 1.0,
                "request_error_rate": 0.0,
                "safe_fulfillment_success_rate": 1.0,
                "final_completion_latency_ms": {"p95": 3900.0},
            },
            "by_intent_group": {"weather": {}},
        },
    }

    report = module.build_conversation_flow_report(
        env=module._demo_env({}),
        repeat=1,
        pi_http_flow=pi_http_flow,
    )

    assert report["pi_http_flow"]["report_kind"] == "pi_intent_lab_http_probe"
    assert report["decision"]["pi_http_flow"]["state"] == "natural_flow_observed"
    assert report["decision"]["pi_http_flow"]["intent_groups"] == ["weather"]
    assert report["decision"]["pi_http_flow"]["production_route_change"] is False
    assert "human voice trials" in " ".join(report["decision"]["recommendations"])


def test_pi_http_flow_failure_stays_lab_only():
    module = _load_module()
    pi_http_flow = {
        "observation_count": 1,
        "unique_case_count": 1,
        "conversation_contract": {"production_route_change": False},
        "summary": {
            "overall": {
                "natural_flow_rate": 0.0,
                "request_error_rate": 0.0,
                "safe_fulfillment_success_rate": 1.0,
            },
            "by_intent_group": {"weather": {}},
        },
    }

    report = module.build_conversation_flow_report(env=module._demo_env({}), repeat=1, pi_http_flow=pi_http_flow)

    assert report["decision"]["pi_http_flow"]["state"] == "keep_lab_only"
    assert "did not meet the natural-flow gate" in " ".join(report["decision"]["recommendations"])


def test_cli_can_attach_stubbed_pi_benchmark(monkeypatch, capsys):
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    def _fake_load_cases(_paths, *, no_default_cases):
        assert no_default_cases is True
        return [case]

    async def _fake_run_benchmark(cases, **kwargs):
        assert cases == [case]
        assert kwargs["run_pi"] is True
        return [
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
            }
        ]

    monkeypatch.setattr(module, "_load_cases", _fake_load_cases)
    monkeypatch.setattr(module, "run_pi_fleet_benchmark", _fake_run_benchmark)

    exit_code = module.main(["--demo-defaults", "--run-pi", "--no-default-cases", "--benchmark-repeat", "1"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["pi_benchmark"]["observation_count"] == 1
    assert payload["decision"]["pi_candidate"]["state"] == "candidate_speed_accuracy_win"


def test_cli_can_attach_stubbed_pi_http_flow(monkeypatch, capsys):
    module = _load_module()
    calls = {}

    def fake_run_probe(cases, **kwargs):
        calls["cases"] = cases
        calls["kwargs"] = kwargs
        return [
            {
                "case_id": "weather",
                "intent_group": "weather",
                "source": "synthetic",
                "pi_correct": True,
                "request_error": None,
                "natural_flow_candidate": True,
                "safe_fulfillment_success": True,
                "conversation_flow": {
                    "final_response_available": True,
                    "cue_within_budget": True,
                    "final_within_budget": True,
                },
                "final_completion_latency_ms": 3000.0,
            }
        ]

    monkeypatch.setattr(module, "run_pi_http_flow_probe", fake_run_probe)

    exit_code = module.main(
        [
            "--demo-defaults",
            "--run-pi-http-flow",
            "--include-safe-fulfillment",
            "--local-model-configured",
            "--allow-execution",
            "--pi-http-repeat",
            "1",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert calls["kwargs"]["wake_ack_text"] == "Understood."
    assert calls["kwargs"]["include_safe_fulfillment"] is True
    assert calls["kwargs"]["natural_cue_max_ms"] == 150.0
    assert payload["pi_http_flow"]["observation_count"] == 1
    assert payload["decision"]["pi_http_flow"]["state"] == "natural_flow_observed"


def test_cli_loads_presence_env_file_without_demo_defaults(tmp_path, capsys):
    module = _load_module()
    env_path = tmp_path / ".env"
    env_path.write_text(
        'ZOE_WAKE_ACK_PHRASES="Yes Jason.|Hi Jason."\n'
        'ZOE_PROCESSING_ACK_PHRASES="Let me check.|One moment."\n',
        encoding="utf-8",
    )

    exit_code = module.main(["--presence-env-file", str(env_path), "--repeat", "1"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["presence"]["gate"]["full_wake_to_processing_ready"] is True
    assert payload["presence"]["wake_ack"]["first_payload"]["events"][1]["text"] == "Yes Jason."
    assert payload["presence"]["processing_ack"]["first_payload"]["event"]["text"] == "Let me check."
