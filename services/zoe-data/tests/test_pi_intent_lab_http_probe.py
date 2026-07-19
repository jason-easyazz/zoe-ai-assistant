import pytest
import importlib.util
import json
import sys
from pathlib import Path

pytestmark = pytest.mark.ci_safe


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
            "cue_text": "Let me check.",
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
        request_timeout_seconds=9.0,
        wake_ack_text="Yes Jason.",
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
        wake_ack_text="Yes Jason.",
    )

    assert len(calls) == 2
    assert calls[0]["url"] == "http://127.0.0.1:8013/api/pi-intent-lab/compare"
    assert calls[0]["payload"]["include_safe_fulfillment"] is True
    assert calls[0]["payload"]["include_hybrid_status"] is False
    assert calls[0]["payload"]["request_timeout_seconds"] == 9.0
    assert report["safe_fulfillment_side_effects"] == "read_only_external_only"
    assert report["summary"]["overall"]["pi_accuracy"] == 1.0
    assert report["summary"]["overall"]["natural_flow_rate"] == 1.0
    assert report["summary"]["overall"]["conversation_final_available_rate"] == 1.0
    assert report["summary"]["overall"]["cue_within_budget_rate"] == 1.0
    assert report["summary"]["overall"]["final_within_budget_rate"] == 1.0
    assert report["summary"]["overall"]["safe_fulfillment_success_rate"] == 1.0
    assert report["conversation_contract"]["strategy"] == "wake_ack_then_processing_cue_then_pi_final"
    assert report["conversation_contract"]["wake_ack_configured"] is True
    assert report["conversation_contract"]["production_route_change"] is False
    assert report["observations"][0]["conversation_flow"]["events"] == [
        {"offset_ms": 0.0, "role": "zoe", "phase": "wake_ack", "text": "Yes Jason."},
        {"offset_ms": 0.05, "role": "zoe", "phase": "processing_cue", "text": "Let me check."},
        {"offset_ms": 2600.0, "role": "zoe", "phase": "final_answer", "text": "It is 18.5 C."},
    ]
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
    assert observations[0]["natural_flow_candidate"] is False
    assert observations[0]["conversation_flow"]["final_response_available"] is False
    assert observations[0]["conversation_flow"]["final_within_budget"] is False
    assert report["summary"]["overall"]["safe_fulfillment_success_rate"] == 0.0
    assert report["summary"]["overall"]["conversation_final_available_rate"] == 0.0
    assert report["summary"]["overall"]["final_within_budget_rate"] == 0.0


def test_slow_final_answer_fails_natural_flow_budget():
    module = _load_module()

    def fake_post(url, payload, timeout_seconds):
        return {
            **_endpoint_result(),
            "simulated_hybrid_flow": {
                "cue_latency_ms": 0.05,
                "cue_text": "Let me check.",
                "final_completion_latency_ms": 6000.0,
                "natural_flow_candidate": True,
            },
        }

    observations = module.run_probe(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        base_url="http://127.0.0.1:8013",
        repeat=1,
        run_pi=True,
        include_safe_fulfillment=True,
        allow_pi_execution=True,
        local_model_configured=True,
        timeout_seconds=12.0,
        natural_final_max_ms=4500.0,
        post_json=fake_post,
    )
    report = module.build_report(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        observations,
        base_url="http://127.0.0.1:8013",
        repeat=1,
        run_pi=True,
        include_safe_fulfillment=True,
        natural_final_max_ms=4500.0,
    )

    assert observations[0]["conversation_flow"]["final_within_budget"] is False
    assert observations[0]["natural_flow_candidate"] is False
    assert report["summary"]["overall"]["final_within_budget_rate"] == 0.0
    assert report["summary"]["overall"]["natural_flow_rate"] == 0.0


def test_missing_cue_text_is_not_fabricated():
    module = _load_module()

    def fake_post(url, payload, timeout_seconds):
        return {
            **_endpoint_result(),
            "simulated_hybrid_flow": {
                "cue_latency_ms": 0.05,
                "final_completion_latency_ms": 2600.0,
                "natural_flow_candidate": True,
            },
        }

    observations = module.run_probe(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        base_url="http://127.0.0.1:8013",
        repeat=1,
        run_pi=True,
        include_safe_fulfillment=True,
        allow_pi_execution=True,
        local_model_configured=True,
        timeout_seconds=12.0,
        wake_ack_text="Yes Jason.",
        post_json=fake_post,
    )

    flow = observations[0]["conversation_flow"]
    assert flow["processing_cue_available"] is False
    assert flow["cue_within_budget"] is False
    assert flow["natural_flow_candidate"] is False
    assert [event["phase"] for event in flow["events"]] == ["wake_ack", "final_answer"]
    assert all(event["text"] != "Let me check." for event in flow["events"])


def test_auth_headers_are_attached_to_real_http_sender(monkeypatch):
    module = _load_module()
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(_endpoint_result()).encode("utf-8")

    def fake_urlopen(request, timeout):
        seen["timeout"] = timeout
        seen["headers"] = dict(request.header_items())
        return FakeResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    result = module._post_json(
        "http://127.0.0.1:8013/api/pi-intent-lab/compare",
        {"text": "rain later"},
        12.0,
        headers=module._auth_headers(session_id="session-secret", device_token="device-secret"),
    )

    assert result["pi"]["intent"] == "weather"
    assert seen["timeout"] == 12.0
    assert seen["headers"]["X-session-id"] == "session-secret"
    assert seen["headers"]["X-device-token"] == "device-secret"


def test_custom_transport_rejects_auth_headers():
    module = _load_module()

    def fake_post(url, payload, timeout_seconds):
        return _endpoint_result()

    try:
        module.run_probe(
            [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
            base_url="http://127.0.0.1:8013",
            repeat=1,
            run_pi=True,
            include_safe_fulfillment=False,
            allow_pi_execution=True,
            local_model_configured=True,
            timeout_seconds=12.0,
            session_id="session-secret",
            post_json=fake_post,
        )
    except ValueError as exc:
        assert "default HTTP sender" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_request_timeout_must_be_below_http_timeout():
    module = _load_module()

    try:
        module.run_probe(
            [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
            base_url="http://127.0.0.1:8013",
            repeat=1,
            run_pi=True,
            include_safe_fulfillment=False,
            allow_pi_execution=True,
            local_model_configured=True,
            timeout_seconds=12.0,
            request_timeout_seconds=12.0,
        )
    except ValueError as exc:
        assert "less than timeout_seconds" in str(exc)
    else:
        raise AssertionError("expected ValueError")


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


def test_empty_report_keeps_stats_schema():
    module = _load_module()

    report = module.build_report(
        [],
        [],
        base_url="http://127.0.0.1:8000",
        repeat=1,
        run_pi=False,
        include_safe_fulfillment=False,
    )
    overall = report["summary"]["overall"]

    assert overall["observation_count"] == 0
    assert overall["request_error_rate"] is None
    assert overall["pi_accuracy"] is None
    assert overall["pi_timeout_rate"] is None
    assert overall["safe_fulfillment_success_rate"] is None
    assert overall["conversation_final_available_rate"] is None
    assert overall["cue_within_budget_rate"] is None
    assert overall["final_within_budget_rate"] is None
    assert overall["safe_fulfillment_latency_ms"] == {
        "avg": None,
        "p50": None,
        "p95": None,
        "min": None,
        "max": None,
    }


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
        assert kwargs["request_timeout_seconds"] == 9.0
        assert kwargs["session_id"] == "session-secret"
        assert kwargs["device_token"] == "device-secret"
        assert kwargs["wake_ack_text"] == "Yes Jason."
        assert kwargs["natural_final_max_ms"] == 4200.0
        return [
            {
                "case_id": "weather",
                "intent_group": "weather",
                "source": "synthetic",
                "expected_intent": "weather",
                "http_latency_ms": 10.0,
                "pi_intent": "weather",
                "pi_correct": True,
                "conversation_flow": {"final_response_available": True, "cue_within_budget": True, "final_within_budget": True},
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
            "--request-timeout-seconds",
            "9",
            "--session-id",
            "session-secret",
            "--device-token",
            "device-secret",
            "--wake-ack-text",
            "Yes Jason.",
            "--natural-final-max-ms",
            "4200",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["base_url"] == "http://testserver"
    assert payload["observation_count"] == 1
    assert payload["conversation_contract"]["wake_ack_text_preview"] == "Yes Jason."
    assert payload["summary"]["overall"]["pi_accuracy"] == 1.0
