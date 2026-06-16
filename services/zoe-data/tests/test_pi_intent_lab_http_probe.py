import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_intent_lab_http_probe.py"
    spec = importlib.util.spec_from_file_location("pi_intent_lab_http_probe_test", path)
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


def _endpoint_result(*, intent="weather", response_chars=12, response_preview="It is 18.5 C."):
    return {
        "contract": {"side_effects": "read_only_external_only"},
        "zoe_router": {"intent": None},
        "pi": {
            "intent": intent,
            "confidence": 0.95,
            "latency_ms": 2500.0,
            "timed_out": False,
            "error": None,
        },
        "safe_fulfillment": {
            "attempted": True,
            "allowed": True,
            "timed_out": False,
            "error": None,
            "latency_ms": 100.0,
            "response_chars": response_chars,
            "response_preview": response_preview,
        },
        "simulated_hybrid_flow": {
            "cue_latency_ms": 0.05,
            "final_completion_latency_ms": 2600.0,
            "natural_flow_candidate": True,
        },
    }


def test_http_probe_summarises_endpoint_results():
    module = _load_module()
    calls = []

    def fake_post(url, payload, timeout_seconds):
        calls.append({"url": url, "payload": payload, "timeout_seconds": timeout_seconds})
        return _endpoint_result()

    cases = [
        {
            "case_id": "weather",
            "text": "rain later",
            "expected_intent": "weather",
            "intent_group": "weather",
            "source": "intent_miss",
        }
    ]
    observations = module.run_probe(
        cases,
        base_url="http://127.0.0.1:8013/",
        repeat=2,
        run_pi=True,
        include_safe_fulfillment=True,
        allow_pi_execution=True,
        local_model_configured=True,
        timeout_seconds=12.0,
        post_json=fake_post,
    )
    report = module.build_report(
        cases,
        observations,
        base_url="http://127.0.0.1:8013/",
        repeat=2,
        run_pi=True,
        include_safe_fulfillment=True,
        include_observations=True,
    )

    assert len(calls) == 2
    assert calls[0]["url"] == "http://127.0.0.1:8013/api/pi-intent-lab/compare"
    assert calls[0]["payload"]["include_safe_fulfillment"] is True
    assert calls[0]["payload"]["include_hybrid_status"] is False
    assert report["safe_fulfillment_side_effects"] == "read_only_external_only"
    assert report["summary"]["overall"]["pi_accuracy"] == 1.0
    assert report["summary"]["overall"]["natural_flow_rate"] == 1.0
    assert report["summary"]["overall"]["safe_fulfillment_success_rate"] == 1.0
    assert report["observations"][0]["response_preview"] == "It is 18.5 C."


def test_empty_endpoint_response_is_not_success():
    module = _load_module()

    def fake_post(url, payload, timeout_seconds):
        return _endpoint_result(response_chars=0, response_preview="")

    observations = module.run_probe(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        base_url="http://127.0.0.1:8013",
        repeat=1,
        run_pi=True,
        include_safe_fulfillment=True,
        allow_pi_execution=True,
        local_model_configured=True,
        timeout_seconds=12.0,
        post_json=fake_post,
    )
    report = module.build_report(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        observations,
        base_url="http://127.0.0.1:8013",
        repeat=1,
        run_pi=True,
        include_safe_fulfillment=True,
    )

    assert observations[0]["safe_fulfillment_success"] is False
    assert report["summary"]["overall"]["safe_fulfillment_success_rate"] == 0.0


def test_http_errors_are_reported():
    module = _load_module()

    def fake_post(url, payload, timeout_seconds):
        raise RuntimeError("HTTP 404: not found")

    observations = module.run_probe(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        base_url="http://127.0.0.1:8000",
        repeat=1,
        run_pi=False,
        include_safe_fulfillment=False,
        allow_pi_execution=False,
        local_model_configured=False,
        timeout_seconds=3.0,
        post_json=fake_post,
    )
    report = module.build_report(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        observations,
        base_url="http://127.0.0.1:8000",
        repeat=1,
        run_pi=False,
        include_safe_fulfillment=False,
    )

    assert "HTTP 404" in observations[0]["request_error"]
    assert report["summary"]["overall"]["request_error_rate"] == 1.0


def test_cli_runs_cases_file(tmp_path, capsys, monkeypatch):
    module = _load_module()
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [
                {
                    "case_id": "weather",
                    "text": "rain later",
                    "expected_intent": "weather",
                    "intent_group": "weather",
                }
            ]
        ),
        encoding="utf-8",
    )

    def fake_run_probe(cases_arg, **kwargs):
        assert len(cases_arg) == 1
        assert kwargs["base_url"] == "http://testserver"
        return [
            {
                "case_id": "weather",
                "intent_group": "weather",
                "source": "synthetic",
                "expected_intent": "weather",
                "http_latency_ms": 10.0,
                "pi_intent": "weather",
                "pi_correct": True,
                "natural_flow_candidate": True,
                "safe_fulfillment_success": True,
            }
        ]

    monkeypatch.setattr(module, "run_probe", fake_run_probe)
    exit_code = module.main(
        [
            "--base-url",
            "http://testserver",
            "--cases-file",
            str(cases),
            "--no-default-cases",
            "--run-pi",
            "--include-safe-fulfillment",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["base_url"] == "http://testserver"
    assert payload["observation_count"] == 1
    assert payload["summary"]["overall"]["pi_accuracy"] == 1.0
