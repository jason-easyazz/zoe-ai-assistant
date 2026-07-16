import pytest
import importlib.util
import json
import sys
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_hybrid_stream_http_probe.py"
    spec = importlib.util.spec_from_file_location("pi_hybrid_stream_http_probe_test", path)
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


def _case(module, *, case_id="weather", text="rain later", expected="weather", group="weather", negative=False):
    return module.PiIntentEvalCase(
        case_id=case_id,
        text=text,
        expected_intent=expected,
        intent_group=group,
        route_class="fallback",
        negative=negative,
    )


def _events(*, intent="weather", response_preview="It is 18.5 C."):
    return [
        {
            "event": "processing_cue",
            "phase": "cue",
            "elapsed_ms": 0.08,
            "client_received_ms": 4.0,
            "cue": {"available": True, "text": "Let me check."},
        },
        {
            "event": "final",
            "phase": "final",
            "elapsed_ms": 2800.0,
            "client_received_ms": 2810.0,
            "result": {
                "pi": {
                    "intent": intent,
                    "confidence": 0.93,
                    "latency_ms": 2500.0,
                    "timed_out": False,
                    "error": None,
                },
                "safe_fulfillment": {
                    "requested": True,
                    "attempted": True,
                    "allowed": True,
                    "timed_out": False,
                    "error": None,
                    "latency_ms": 300.0,
                    "response_chars": len(response_preview),
                    "response_preview": response_preview,
                    "started_before_pi": True,
                    "validated_by_pi": True,
                    "speculative_safe_fulfillment": "used",
                },
                "simulated_hybrid_flow": {"final_completion_latency_ms": 2800.0},
            },
        },
    ]


def test_stream_probe_summarises_cue_final_and_fulfillment():
    module = _load_module()
    calls = []

    def fake_stream(url, payload, timeout_seconds):
        calls.append({"url": url, "payload": payload, "timeout_seconds": timeout_seconds})
        return _events()

    cases = [_case(module)]
    observations = module.run_probe(
        cases,
        base_url="http://127.0.0.1:8013/",
        repeat=2,
        run_pi=True,
        include_safe_fulfillment=True,
        allow_pi_execution=True,
        local_model_configured=True,
        timeout_seconds=20.0,
        request_timeout_seconds=18.0,
        stream_post=fake_stream,
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
    assert calls[0]["url"] == "http://127.0.0.1:8013/api/pi-intent-lab/hybrid-stream"
    assert calls[0]["payload"]["include_safe_fulfillment"] is True
    assert calls[0]["payload"]["request_timeout_seconds"] == 18.0
    assert report["report_kind"] == "pi_hybrid_stream_http_probe"
    assert report["conversation_contract"]["transport"] == "application/x-ndjson"
    assert report["summary"]["overall"]["cue_packet_rate"] == 1.0
    assert report["summary"]["overall"]["final_packet_rate"] == 1.0
    assert report["summary"]["overall"]["pi_accuracy"] == 1.0
    assert report["summary"]["overall"]["stream_natural_rate"] == 1.0
    assert report["summary"]["overall"]["conversation_natural_rate"] == 1.0
    assert report["summary"]["overall"]["safe_fulfillment_success_rate"] == 1.0
    assert report["summary"]["overall"]["speculative_used_rate"] == 1.0
    assert report["summary"]["overall"]["speculative_discard_rate"] == 0.0
    assert report["summary"]["overall"]["speculative_timeout_rate"] == 0.0
    assert report["summary"]["overall"]["started_before_pi_rate"] == 1.0
    assert report["summary"]["overall"]["validated_by_pi_rate"] == 1.0
    assert report["summary"]["overall"]["simulated_final_completion_latency_ms"]["p95"] == 2800.0
    assert report["observations"][0]["cue_client_latency_ms"] == 4.0
    assert report["observations"][0]["final_client_latency_ms"] == 2810.0
    assert report["observations"][0]["safe_fulfillment_started_before_pi"] is True
    assert report["observations"][0]["safe_fulfillment_validated_by_pi"] is True
    assert report["observations"][0]["speculative_safe_fulfillment"] == "used"
    assert report["observations"][0]["response_preview"] == "It is 18.5 C."


def test_stream_probe_summarises_speculative_discard_and_timeout():
    module = _load_module()
    cases = [
        _case(module, case_id="discard", text="show list", expected="list_show", group="lists"),
        _case(module, case_id="timeout", text="rain later", expected="weather", group="weather"),
    ]
    events = []
    for state in ("discarded", "timed_out"):
        item = _events(intent="list_show" if state == "discarded" else "weather", response_preview="ok")
        safe = item[1]["result"]["safe_fulfillment"]
        safe["speculative_safe_fulfillment"] = state
        safe["started_before_pi"] = state == "timed_out"
        safe["validated_by_pi"] = state == "timed_out"
        if state == "discarded":
            safe["speculative_intent"] = "weather"
            safe["speculative_discard_reason"] = "speculative_pi_disagreed"
        events.append(item)

    observations = module.run_probe(
        cases,
        base_url="http://127.0.0.1:8000",
        repeat=1,
        run_pi=True,
        include_safe_fulfillment=True,
        allow_pi_execution=True,
        local_model_configured=True,
        timeout_seconds=20.0,
        stream_post=lambda url, payload, timeout: events.pop(0),
    )
    report = module.build_report(
        cases,
        observations,
        base_url="http://127.0.0.1:8000",
        repeat=1,
        run_pi=True,
        include_safe_fulfillment=True,
        include_observations=True,
    )

    assert report["summary"]["overall"]["speculative_used_rate"] == 0.0
    assert report["summary"]["overall"]["speculative_discard_rate"] == 0.5
    assert report["summary"]["overall"]["speculative_timeout_rate"] == 0.5
    assert report["summary"]["overall"]["started_before_pi_rate"] == 0.5
    assert report["summary"]["overall"]["validated_by_pi_rate"] == 0.5
    assert report["observations"][0]["speculative_safe_fulfillment"] == "discarded"
    assert report["observations"][0]["speculative_intent"] == "weather"
    assert report["observations"][0]["speculative_discard_reason"] == "speculative_pi_disagreed"
    assert report["observations"][1]["speculative_safe_fulfillment"] == "timed_out"


def test_stream_error_packet_is_reported_without_final_packet():
    module = _load_module()

    def fake_stream(url, payload, timeout_seconds):
        return [
            {
                "event": "processing_cue",
                "client_received_ms": 3.0,
                "elapsed_ms": 0.04,
                "cue": {"available": True, "text": "Let me check."},
            },
            {
                "event": "error",
                "client_received_ms": 1010.0,
                "elapsed_ms": 1000.0,
                "error_type": "timeout",
                "error": "Pi intent lab comparison timed out",
            },
        ]

    cases = [_case(module)]
    observations = module.run_probe(
        cases,
        base_url="http://127.0.0.1:8000",
        repeat=1,
        run_pi=True,
        include_safe_fulfillment=False,
        allow_pi_execution=True,
        local_model_configured=True,
        timeout_seconds=20.0,
        stream_post=fake_stream,
    )
    report = module.build_report(
        cases,
        observations,
        base_url="http://127.0.0.1:8000",
        repeat=1,
        run_pi=True,
        include_safe_fulfillment=False,
    )

    assert observations[0]["cue_packet_available"] is True
    assert observations[0]["final_packet_available"] is False
    assert observations[0]["error_packet_available"] is True
    assert "timeout" in observations[0]["request_error"]
    assert observations[0]["stream_natural_candidate"] is False
    assert report["summary"]["overall"]["error_packet_rate"] == 1.0
    assert report["summary"]["overall"]["request_error_rate"] == 1.0


def test_negative_case_counts_false_positive():
    module = _load_module()
    cases = [
        _case(
            module,
            case_id="casual_breakfast",
            text="I like the breakfast service",
            expected=None,
            group="chat",
            negative=True,
        )
    ]

    observations = module.run_probe(
        cases,
        base_url="http://127.0.0.1:8000",
        repeat=1,
        run_pi=True,
        include_safe_fulfillment=False,
        allow_pi_execution=False,
        local_model_configured=True,
        timeout_seconds=20.0,
        stream_post=lambda url, payload, timeout: _events(intent="weather", response_preview=""),
    )
    report = module.build_report(
        cases,
        observations,
        base_url="http://127.0.0.1:8000",
        repeat=1,
        run_pi=True,
        include_safe_fulfillment=False,
    )

    assert observations[0]["negative"] is True
    assert observations[0]["pi_correct"] is False
    assert report["summary"]["overall"]["negative_false_positive_rate"] == 1.0
    assert report["summary"]["overall"]["pi_accuracy"] == 0.0


def test_auth_headers_are_attached_to_stream_sender(monkeypatch):
    module = _load_module()
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield json.dumps(_events()[0]).encode("utf-8") + b"\n"
            yield json.dumps(_events()[1]).encode("utf-8") + b"\n"

    def fake_urlopen(request, timeout):
        seen["timeout"] = timeout
        seen["headers"] = dict(request.header_items())
        return FakeResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    events = module._post_stream_json(
        "http://127.0.0.1:8000/api/pi-intent-lab/hybrid-stream",
        {"text": "rain later"},
        20.0,
        headers=module._auth_headers(session_id="session-secret", device_token="device-secret"),
    )

    assert [event["event"] for event in events] == ["processing_cue", "final"]
    assert seen["timeout"] == 20.0
    assert seen["headers"]["X-session-id"] == "session-secret"
    assert seen["headers"]["X-device-token"] == "device-secret"


def test_cli_runs_cases_file(tmp_path, capsys, monkeypatch):
    module = _load_module()
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(
        json.dumps(
            [
                {
                    "case_id": "weather",
                    "text": "rain later",
                    "expected_intent": "weather",
                    "intent_group": "weather",
                    "route_class": "fallback",
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
        assert kwargs["natural_final_max_ms"] == 4200.0
        return [
            {
                "case_id": "weather",
                "intent_group": "weather",
                "source": "synthetic",
                "expected_intent": "weather",
                "cue_packet_available": True,
                "final_packet_available": True,
                "pi_intent": "weather",
                "pi_correct": True,
                "stream_natural_candidate": True,
                "conversation_natural_candidate": False,
            }
        ]

    monkeypatch.setattr(module, "run_probe", fake_run_probe)
    exit_code = module.main(
        [
            "--base-url",
            "http://testserver",
            "--cases-file",
            str(cases_file),
            "--no-default-cases",
            "--run-pi",
            "--request-timeout-seconds",
            "9",
            "--session-id",
            "session-secret",
            "--device-token",
            "device-secret",
            "--natural-final-max-ms",
            "4200",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["base_url"] == "http://testserver"
    assert payload["observation_count"] == 1
    assert payload["summary"]["overall"]["pi_accuracy"] == 1.0


def test_select_cases_can_keep_safe_fulfillment_eligible_defaults_only():
    module = _load_module()

    selected = module._select_cases(
        module.DEFAULT_PI_INTENT_EVAL_CASES,
        safe_fulfillment_eligible_only=True,
    )

    assert [case.case_id for case in selected] == [
        "weather_rain_later",
        "weather_jacket",
        "list_show",
        "calc",
        "briefing",
    ]
    assert all(case.expected_intent in module.SAFE_FULFILLMENT_INTENTS for case in selected)


def test_select_cases_supports_group_and_case_filters():
    module = _load_module()

    selected = module._select_cases(
        module.DEFAULT_PI_INTENT_EVAL_CASES,
        intent_groups=["weather", "chat"],
        case_ids=["weather_rain_later"],
    )

    assert [case.case_id for case in selected] == ["weather_rain_later"]


def test_custom_stream_post_rejects_auth_headers():
    module = _load_module()

    try:
        module.run_probe(
            [_case(module)],
            base_url="http://127.0.0.1:8000",
            repeat=1,
            run_pi=True,
            include_safe_fulfillment=False,
            allow_pi_execution=False,
            local_model_configured=True,
            timeout_seconds=20.0,
            session_id="session-secret",
            stream_post=lambda url, payload, timeout: _events(),
        )
    except ValueError as exc:
        assert "cannot be used with a custom stream_post" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_observation_pi_correct_is_none_when_pi_did_not_run():
    module = _load_module()

    observations = module.run_probe(
        [_case(module)],
        base_url="http://127.0.0.1:8000",
        repeat=1,
        run_pi=False,
        include_safe_fulfillment=False,
        allow_pi_execution=False,
        local_model_configured=False,
        timeout_seconds=20.0,
        stream_post=lambda url, payload, timeout: _events(intent=None, response_preview=""),
    )
    report = module.build_report(
        [_case(module)],
        observations,
        base_url="http://127.0.0.1:8000",
        repeat=1,
        run_pi=False,
        include_safe_fulfillment=False,
    )

    assert observations[0]["pi_correct"] is None
    assert report["summary"]["overall"]["pi_accuracy"] is None


def test_cli_reports_timeout_argument_error(capsys):
    module = _load_module()

    try:
        module.main(["--timeout-seconds", "5"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected SystemExit")

    captured = capsys.readouterr()
    assert "--request-timeout-seconds must be less than --timeout-seconds" in captured.err
