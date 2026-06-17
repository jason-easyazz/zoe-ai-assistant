import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_touch_hybrid_production_probe.py"
    spec = importlib.util.spec_from_file_location("pi_touch_hybrid_production_probe_test", path)
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


def test_probe_observes_panel_processing_cue_and_final_pi_response():
    module = _load_module()

    def sender(url, payload, timeout_seconds, headers):
        assert url == "http://testserver/api/voice/command"
        assert payload["text"] == "will it rain later"
        assert headers == {"X-Device-Token": "token-secret"}
        return {
            "ok": True,
            "reply": "It should stay dry later.",
            "audio_base64": "UklGRg==",
            "intent": "weather",
            "pi_hybrid": {
                "accepted": True,
                "reason": "accepted",
                "intent_group": "weather",
                "agreement_kind": "zoe_router",
            },
        }

    def panel_observer(ws_url, panel_id, device_token, timeout_seconds, action):
        assert ws_url == "ws://testserver/ws/push?panel_id=panel-touch"
        assert panel_id == "panel-touch"
        assert device_token == "token-secret"
        response = action()
        return response, [
            {
                "offset_ms": 8.0,
                "message": {"type": "voice:transcript", "data": {"panel_id": "panel-touch", "text": "will it rain later"}},
            },
            {
                "offset_ms": 12.0,
                "message": {
                    "type": "voice:responding",
                    "data": {
                        "panel_id": "panel-touch",
                        "text": "Let me check.",
                        "processing_ack": True,
                        "source": "pi_hybrid_production",
                    },
                },
            },
            {
                "offset_ms": 3900.0,
                "message": {
                    "type": "voice:responding",
                    "data": {"panel_id": "panel-touch", "text": "It should stay dry later.", "pi_hybrid": True},
                },
            },
            {"offset_ms": 3920.0, "message": {"type": "voice:done", "data": {"panel_id": "panel-touch"}}},
        ], None

    observations = module.run_probe(
        [{"case_id": "weather", "text": "will it rain later", "expected_intent": "weather"}],
        base_url="http://testserver",
        panel_id="panel-touch",
        device_token="token-secret",
        repeat=1,
        timeout_seconds=15.0,
        websocket_timeout_seconds=5.0,
        sender=sender,
        panel_observer=panel_observer,
    )
    report = module.build_report(
        [{"case_id": "weather", "text": "will it rain later", "expected_intent": "weather"}],
        observations,
        base_url="http://testserver",
        panel_id="panel-touch",
        repeat=1,
        include_observations=True,
    )

    obs = observations[0]
    assert obs["processing_ack_available"] is True
    assert obs["processing_ack_text"] == "Let me check."
    assert obs["processing_ack_latency_ms"] == 12.0
    assert obs["final_panel_response_available"] is True
    assert obs["done_available"] is True
    assert obs["pi_hybrid_accepted"] is True
    assert obs["expected_intent_matched"] is True
    assert obs["audio_returned"] is True
    assert obs["natural_flow_pass"] is True
    assert report["readiness"] == {"ready_for_touch_panel_smoke": True, "blockers": []}
    assert report["summary"]["overall"]["processing_ack_rate"] == 1.0


def test_default_probe_cases_cover_low_risk_production_groups():
    module = _load_module()

    cases = {case["case_id"]: case for case in module.DEFAULT_CASES}

    assert cases["weather_rain_later"]["expected_intent"] == "weather"
    assert cases["daily_briefing"]["expected_intent"] == "daily_briefing"
    assert cases["list_show_shopping"]["expected_intent"] == "list_show"
    assert cases["list_show_shopping"]["intent_group"] == "lists"


def test_probe_marks_missing_panel_cue_as_not_ready():
    module = _load_module()

    def sender(url, payload, timeout_seconds, headers):
        return {
            "ok": True,
            "reply": "Ready.",
            "audio_base64": "UklGRg==",
            "intent": "weather",
            "pi_hybrid": {"accepted": True, "intent_group": "weather"},
        }

    def panel_observer(ws_url, panel_id, device_token, timeout_seconds, action):
        response = action()
        return response, [
            {"offset_ms": 100.0, "message": {"type": "voice:responding", "data": {"panel_id": panel_id, "text": "Ready.", "pi_hybrid": True}}},
            {"offset_ms": 120.0, "message": {"type": "voice:done", "data": {"panel_id": panel_id}}},
        ], None

    observations = module.run_probe(
        [{"case_id": "weather", "text": "weather", "expected_intent": "weather"}],
        base_url="http://testserver",
        panel_id="panel-touch",
        device_token="token-secret",
        repeat=1,
        timeout_seconds=15.0,
        websocket_timeout_seconds=5.0,
        sender=sender,
        panel_observer=panel_observer,
    )
    report = module.build_report(
        [{"case_id": "weather", "text": "weather", "expected_intent": "weather"}],
        observations,
        base_url="http://testserver",
        panel_id="panel-touch",
        repeat=1,
    )

    assert observations[0]["processing_ack_available"] is False
    assert observations[0]["natural_flow_pass"] is False
    assert report["readiness"]["ready_for_touch_panel_smoke"] is False
    assert "panel_processing_ack_missing" in report["readiness"]["blockers"]


def test_probe_requires_device_token():
    module = _load_module()
    try:
        module.run_probe(
            [{"case_id": "weather", "text": "weather", "expected_intent": "weather"}],
            base_url="http://testserver",
            panel_id="panel-touch",
            device_token="",
            repeat=1,
            timeout_seconds=15.0,
            websocket_timeout_seconds=5.0,
        )
    except ValueError as exc:
        assert "device_token is required" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_cli_report_redacts_device_token(capsys, monkeypatch):
    module = _load_module()

    def fake_run_probe(cases, **kwargs):
        assert kwargs["device_token"] == "secret-token"
        return []

    monkeypatch.setattr(module, "run_probe", fake_run_probe)
    assert module.main([
        "--base-url", "http://testserver",
        "--panel-id", "panel-touch",
        "--device-token", "secret-token",
        "--no-default-cases",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "secret-token" not in json.dumps(payload)
    assert payload["panel_id"] == "panel-touch"
