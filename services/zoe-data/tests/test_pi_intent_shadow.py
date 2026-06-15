import asyncio
import json

import pytest

from pi_intent_classifier import PiIntentClassification
from pi_intent_shadow import (
    PiIntentShadowConfig,
    maybe_record_pi_intent_shadow,
    pi_intent_shadow_status,
    summarize_pi_intent_shadow,
)


@pytest.mark.asyncio
async def test_shadow_disabled_does_not_write(tmp_path):
    path = tmp_path / "shadow.jsonl"

    result = await maybe_record_pi_intent_shadow(
        "rain later",
        zoe_intent="weather",
        route_class="deterministic",
        config=PiIntentShadowConfig(enabled=False, path=str(path)),
    )

    assert result is None
    assert not path.exists()


@pytest.mark.asyncio
async def test_shadow_records_sanitized_pi_comparison(tmp_path, monkeypatch):
    path = tmp_path / "shadow.jsonl"

    async def fake_classify(text, *, context_turns="", env=None, config=None):
        assert text == "email jason@example.com if rain later"
        assert env["ZOE_PI_INTENT_ENABLED"] == "true"
        return PiIntentClassification(
            intent="weather",
            slots={},
            confidence=0.91,
            task_lane="fast_tool",
            source="pi_test",
            latency_ms=123.0,
        )

    monkeypatch.setattr("pi_intent_classifier.classify_with_pi_intent_governor", fake_classify)

    record = await maybe_record_pi_intent_shadow(
        "email jason@example.com if rain later",
        zoe_intent="weather",
        zoe_confidence=0.8,
        zoe_latency_ms=4.0,
        route_class="deterministic",
        user_id="jason",
        config=PiIntentShadowConfig(enabled=True, path=str(path)),
        env={},
    )

    assert record is not None
    assert record["agreement"] is True
    assert record["pi_latency_ms"] == 123.0
    saved = json.loads(path.read_text().strip())
    assert saved["text_preview"] == "email [EMAIL] if rain later"
    assert saved["text_hash"]
    assert "jason@example.com" not in path.read_text()


def test_shadow_status_summarizes_records(tmp_path):
    path = tmp_path / "shadow.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "agreement": True,
                        "timed_out": False,
                        "zoe_intent_group": "weather",
                        "pi_latency_ms": 100,
                        "zoe_latency_ms": 5,
                    }
                ),
                json.dumps(
                    {
                        "agreement": False,
                        "timed_out": True,
                        "pi_no_result": True,
                        "pi_intent_group": "reminders",
                        "pi_latency_ms": 250,
                        "zoe_latency_ms": 6,
                    }
                ),
            ]
        )
        + "\n"
    )

    status = pi_intent_shadow_status(
        {
            "ZOE_PI_INTENT_SHADOW_ENABLED": "true",
            "ZOE_PI_INTENT_SHADOW_PATH": str(path),
        }
    )

    assert status["config"]["enabled"] is True
    assert status["record_count_window"] == 2
    assert status["report"]["agreement_rate"] == 0.5
    assert status["report"]["timeout_rate"] == 0.5
    assert status["report"]["no_result_rate"] == 0.5
    assert status["report"]["accuracy_available"] is False
    assert status["report"]["promotion_ready"] is False


def test_shadow_summary_empty_is_explicitly_not_accuracy_ready():
    report = summarize_pi_intent_shadow([])

    assert report["sample_count"] == 0
    assert report["no_result_rate"] == 0.0
    assert report["accuracy_available"] is False


@pytest.mark.asyncio
async def test_intent_router_shadow_does_not_change_live_route(monkeypatch):
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    calls = []

    async def fake_shadow(text, **kwargs):
        calls.append({"text": text, **kwargs})
        return {"pi_intent": "reminder_list", "agreement": False}

    monkeypatch.setattr("pi_intent_shadow.maybe_record_pi_intent_shadow", fake_shadow)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent("what is the weather", user_id="jason")
    await asyncio.sleep(0)

    assert intent is not None
    assert intent.name == "weather"
    assert calls
    assert calls[0]["zoe_intent"] == "weather"
    assert calls[0]["route_class"] == "deterministic"
    assert calls[0]["user_id"] == "jason"


@pytest.mark.asyncio
async def test_intent_router_reuses_live_pi_result_for_shadow_fallback(tmp_path, monkeypatch):
    path = tmp_path / "shadow.jsonl"
    calls = 0

    async def fake_classify(text, *, context_turns="", env=None, config=None):
        nonlocal calls
        calls += 1
        return PiIntentClassification(
            intent="reminder_list",
            slots={},
            confidence=0.86,
            task_lane="fast_tool",
            source="pi_test",
            latency_ms=42.0,
        )

    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_PATH", str(path))
    monkeypatch.setattr("pi_intent_classifier.classify_with_pi_intent_governor", fake_classify)

    from intent_router import detect_and_extract_intent

    intent = await detect_and_extract_intent("anything I should remember right now", user_id="jason")
    await asyncio.sleep(0)

    assert intent is not None
    assert intent.name == "reminder_list"
    assert calls == 1
    record = json.loads(path.read_text().strip())
    assert record["route_class"] == "fallback"
    assert record["pi_intent"] == "reminder_list"
    assert record["pi_no_result"] is False
