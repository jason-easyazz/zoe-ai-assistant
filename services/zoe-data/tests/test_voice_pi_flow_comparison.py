import pytest
import importlib.util
import json
import sys
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "voice_pi_flow_comparison.py"
    spec = importlib.util.spec_from_file_location("voice_pi_flow_comparison_test", path)
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


def test_voice_probe_parses_json_response():
    module = _load_module()

    def sender(url, payload, timeout_seconds, headers):
        assert url == "http://testserver/api/voice/command?stream=true"
        assert payload["text"] == "rain later"
        assert timeout_seconds == 15.0
        assert headers == {"X-Session-ID": "session-secret"}
        return {
            "status": "ok",
            "content_type": "application/json",
            "lines": [
                {
                    "offset_ms": 120.0,
                    "line": json.dumps({"ok": True, "reply": "It is raining.", "intent": "weather"}),
                }
            ],
        }

    observations = module.run_voice_command_probe(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        base_url="http://testserver",
        repeat=1,
        timeout_seconds=15.0,
        session_id="session-secret",
        sender=sender,
    )
    report = module.build_report(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        observations,
        base_url="http://testserver",
        repeat=1,
        include_observations=True,
    )

    assert observations[0]["transport"] == "json"
    assert observations[0]["final_response_available"] is True
    assert observations[0]["processing_ack_available"] is False
    assert observations[0]["route_intent"] == "weather"
    assert report["voice_current"]["summary"]["overall"]["final_response_rate"] == 1.0
    assert report["comparison"]["state"] == "voice_only_measured"


def test_voice_probe_empty_reply_does_not_count_as_final_latency():
    module = _load_module()

    def sender(url, payload, timeout_seconds, headers):
        return {
            "status": "ok",
            "content_type": "application/json",
            "lines": [{"offset_ms": 120.0, "line": json.dumps({"ok": True, "reply": ""})}],
        }

    observations = module.run_voice_command_probe(
        [{"case_id": "empty", "text": "empty", "expected_intent": ""}],
        base_url="http://testserver",
        repeat=1,
        timeout_seconds=15.0,
        sender=sender,
    )
    overall = module.build_report(
        [{"case_id": "empty", "text": "empty", "expected_intent": ""}],
        observations,
        base_url="http://testserver",
        repeat=1,
    )["voice_current"]["summary"]["overall"]

    assert observations[0]["final_response_available"] is False
    assert observations[0]["final_completion_latency_ms"] is None
    assert overall["final_completion_latency_ms"]["p95"] is None


def test_voice_probe_error_does_not_count_as_final_latency():
    module = _load_module()

    def sender(url, payload, timeout_seconds, headers):
        raise RuntimeError("HTTP 403: forbidden")

    observations = module.run_voice_command_probe(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        base_url="http://testserver",
        repeat=1,
        timeout_seconds=15.0,
        sender=sender,
    )
    overall = module.build_report(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        observations,
        base_url="http://testserver",
        repeat=1,
    )["voice_current"]["summary"]["overall"]

    assert observations[0]["request_error"] == "RuntimeError: HTTP 403: forbidden"
    assert observations[0]["final_response_available"] is False
    assert observations[0]["final_completion_latency_ms"] is None
    assert overall["final_completion_latency_ms"]["p95"] is None



def test_voice_probe_parses_streaming_processing_ack_and_done():
    module = _load_module()

    def sender(url, payload, timeout_seconds, headers):
        return {
            "status": "ok",
            "content_type": "application/x-ndjson",
            "lines": [
                {"offset_ms": 5.0, "line": json.dumps({"processing_ack": True, "text": "Let me check."})},
                {"offset_ms": 300.0, "line": json.dumps({"chunk": 0, "text": "The answer is ready."})},
                {"offset_ms": 360.0, "line": json.dumps({"done": True, "reply": "The answer is ready."})},
            ],
        }

    observations = module.run_voice_command_probe(
        [{"case_id": "slow", "text": "think about this", "expected_intent": ""}],
        base_url="http://testserver",
        repeat=1,
        timeout_seconds=15.0,
        sender=sender,
    )
    overall = module.build_report(
        [{"case_id": "slow", "text": "think about this", "expected_intent": ""}],
        observations,
        base_url="http://testserver",
        repeat=1,
    )["voice_current"]["summary"]["overall"]

    assert observations[0]["transport"] == "stream"
    assert observations[0]["processing_ack_available"] is True
    assert observations[0]["processing_ack_latency_ms"] == 5.0
    assert observations[0]["final_completion_latency_ms"] == 360.0
    assert observations[0]["done_seen"] is True
    assert overall["processing_ack_rate"] == 1.0


def test_report_compares_pi_and_voice_final_latency():
    module = _load_module()
    voice_observations = [
        {
            "case_id": "weather",
            "intent_group": "weather",
            "source": "synthetic",
            "final_response_available": True,
            "final_completion_latency_ms": 1000.0,
            "processing_ack_available": True,
            "processing_ack_latency_ms": 10.0,
            "request_error": None,
        }
    ]
    pi_flow = {
        "summary": {
            "overall": {
                "final_completion_latency_ms": {"p95": 3000.0},
                "cue_latency_ms": {"p95": 1.0},
            }
        }
    }

    report = module.build_report(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        voice_observations,
        base_url="http://testserver",
        repeat=1,
        pi_http_flow=pi_flow,
    )

    assert report["comparison"]["state"] == "comparison_observed"
    assert report["comparison"]["voice_final_p95_ms"] == 1000.0
    assert report["comparison"]["pi_final_p95_ms"] == 3000.0
    assert report["comparison"]["pi_minus_voice_final_p95_ms"] == 2000.0
    assert report["contract"]["production_route_change"] is False
    assert report["contract"]["current_voice_side_effects"] == "live_voice_command_endpoint"
    assert report["contract"]["current_voice_memory_writes"] == "possible_when_endpoint_writes_for_effective_user"


def test_report_marks_voice_unavailable_when_no_voice_final_latency():
    module = _load_module()
    voice_observations = [
        {
            "case_id": "weather",
            "intent_group": "weather",
            "source": "synthetic",
            "final_response_available": False,
            "final_completion_latency_ms": None,
            "processing_ack_available": False,
            "processing_ack_latency_ms": None,
            "request_error": "RuntimeError: HTTP 401",
        }
    ]
    pi_flow = {
        "summary": {
            "overall": {
                "final_completion_latency_ms": {"p95": 3000.0},
                "cue_latency_ms": {"p95": 1.0},
            }
        }
    }

    report = module.build_report(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        voice_observations,
        base_url="http://testserver",
        repeat=1,
        pi_http_flow=pi_flow,
    )

    assert report["comparison"]["state"] == "pi_observed_voice_unavailable"
    assert report["comparison"]["voice_final_p95_ms"] is None
    assert report["comparison"]["pi_final_p95_ms"] == 3000.0
    assert report["comparison"]["pi_minus_voice_final_p95_ms"] is None



def test_report_marks_pi_unavailable_when_no_pi_final_latency():
    module = _load_module()
    voice_observations = [
        {
            "case_id": "weather",
            "intent_group": "weather",
            "source": "synthetic",
            "final_response_available": True,
            "final_completion_latency_ms": 1000.0,
            "processing_ack_available": False,
            "processing_ack_latency_ms": None,
            "request_error": None,
        }
    ]
    pi_flow = {
        "summary": {
            "overall": {
                "final_completion_latency_ms": {"p95": None},
                "cue_latency_ms": {"p95": 1.0},
            }
        }
    }

    report = module.build_report(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        voice_observations,
        base_url="http://testserver",
        repeat=1,
        pi_http_flow=pi_flow,
    )

    assert report["comparison"]["state"] == "voice_observed_pi_unavailable"
    assert report["comparison"]["voice_final_p95_ms"] == 1000.0
    assert report["comparison"]["pi_final_p95_ms"] is None
    assert report["comparison"]["pi_minus_voice_final_p95_ms"] is None


def test_report_marks_both_unavailable_when_neither_has_final_latency():
    module = _load_module()
    voice_observations = [
        {
            "case_id": "weather",
            "intent_group": "weather",
            "source": "synthetic",
            "final_response_available": False,
            "final_completion_latency_ms": None,
            "processing_ack_available": False,
            "processing_ack_latency_ms": None,
            "request_error": "RuntimeError: HTTP 401",
        }
    ]
    pi_flow = {
        "summary": {
            "overall": {
                "final_completion_latency_ms": {"p95": None},
                "cue_latency_ms": {"p95": None},
            }
        }
    }

    report = module.build_report(
        [{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}],
        voice_observations,
        base_url="http://testserver",
        repeat=1,
        pi_http_flow=pi_flow,
    )

    assert report["comparison"]["state"] == "both_unavailable"
    assert report["comparison"]["voice_final_p95_ms"] is None
    assert report["comparison"]["pi_final_p95_ms"] is None



def test_cli_can_attach_stubbed_pi_flow(monkeypatch, tmp_path, capsys):
    module = _load_module()
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps([{"case_id": "weather", "text": "rain later", "expected_intent": "weather"}]),
        encoding="utf-8",
    )

    def fake_voice_probe(cases, **kwargs):
        assert kwargs["base_url"] == "http://testserver"
        assert kwargs["device_token"] == "device-secret"
        return [
            {
                "case_id": "weather",
                "intent_group": "weather",
                "source": "synthetic",
                "final_response_available": True,
                "final_completion_latency_ms": 1000.0,
                "processing_ack_available": False,
                "request_error": None,
            }
        ]

    def fake_pi_probe(cases, **kwargs):
        assert kwargs["include_safe_fulfillment"] is True
        assert kwargs["device_token"] == "device-secret"
        return [
            {
                "case_id": "weather",
                "intent_group": "weather",
                "source": "synthetic",
                "expected_intent": "weather",
                "pi_intent": "weather",
                "pi_correct": True,
                "request_error": None,
                "natural_flow_candidate": True,
                "safe_fulfillment_success": True,
                "final_completion_latency_ms": 3000.0,
                "conversation_flow": {
                    "final_response_available": True,
                    "cue_within_budget": True,
                    "final_within_budget": True,
                },
            }
        ]

    def fake_pi_report(cases, observations, **kwargs):
        assert [item["case_id"] for item in observations] == ["weather"]
        assert kwargs["include_safe_fulfillment"] is True
        return {
            "report_kind": "pi_intent_lab_http_probe",
            "observation_count": len(observations),
            "summary": {
                "overall": {
                    "final_completion_latency_ms": {"p95": 3000.0},
                    "cue_latency_ms": {"p95": 1.0},
                }
            },
        }

    monkeypatch.setattr(module, "run_voice_command_probe", fake_voice_probe)
    monkeypatch.setattr(module, "run_pi_http_probe", fake_pi_probe)
    monkeypatch.setattr(module, "build_pi_http_report", fake_pi_report)

    exit_code = module.main(
        [
            "--base-url",
            "http://testserver",
            "--cases-file",
            str(cases_path),
            "--no-default-cases",
            "--include-pi-http-flow",
            "--include-safe-fulfillment",
            "--device-token",
            "device-secret",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["observation_count"] == 1
    assert payload["pi_http_flow"]["observation_count"] == 1
    assert payload["comparison"]["state"] == "comparison_observed"
