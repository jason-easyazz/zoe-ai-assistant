import asyncio
import json

import pytest

from pi_intent_classifier import PiIntentClassification
from pi_intent_shadow import (
    PiIntentShadowConfig,
    load_pi_intent_shadow_records,
    maybe_record_pi_intent_shadow,
    pi_intent_shadow_status,
    shadow_records_to_route_samples,
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
    monkeypatch.setenv("ZOE_PI_INTENT_PROMOTED_GROUPS", "reminders")
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


def test_shadow_config_uses_default_for_invalid_max_words():
    config = PiIntentShadowConfig.from_env({"ZOE_PI_INTENT_SHADOW_MAX_WORDS": "abc"})

    assert config.max_words == 32


def test_shadow_record_loader_reads_only_tail_window(tmp_path):
    path = tmp_path / "shadow.jsonl"
    path.write_text("".join(json.dumps({"index": i}) + "\n" for i in range(10)))

    records = load_pi_intent_shadow_records(str(path), limit=3)

    assert [record["index"] for record in records] == [7, 8, 9]


def test_labeled_shadow_records_convert_to_route_samples():
    samples = shadow_records_to_route_samples(
        [
            {
                "text_hash": "abc",
                "outcome_label": "weather",
                "zoe_intent": "reminder_list",
                "pi_intent": "weather",
                "zoe_latency_ms": 500,
                "pi_latency_ms": 120,
                "pi_confidence": 0.9,
                "pi_transport": "rpc",
                "route_class": "fallback",
            },
            {"outcome_label": "extend_capability", "zoe_intent": None, "pi_intent": "extend_capability"},
        ]
    )

    assert len(samples) == 1
    assert samples[0].intent_group == "weather"
    assert samples[0].zoe_correct is False
    assert samples[0].pi_correct is True


def test_labeled_shadow_records_skip_missing_latency():
    samples = shadow_records_to_route_samples(
        [
            {
                "text_hash": "missing_zoe_latency",
                "outcome_label": "weather",
                "zoe_intent": "reminder_list",
                "pi_intent": "weather",
                "zoe_latency_ms": None,
                "pi_latency_ms": 120,
                "pi_transport": "rpc",
                "route_class": "fallback",
            },
            {
                "text_hash": "missing_pi_latency",
                "outcome_label": "weather",
                "zoe_intent": "reminder_list",
                "pi_intent": "weather",
                "zoe_latency_ms": 500,
                "pi_latency_ms": None,
                "pi_transport": "rpc",
                "route_class": "fallback",
            },
        ]
    )

    assert samples == []


def test_shadow_summary_needs_min_samples_for_promotion_ready():
    report = summarize_pi_intent_shadow([{"outcome_label": "weather"} for _ in range(29)])

    assert report["accuracy_available"] is True
    assert report["labeled_sample_count"] == 29
    assert report["promotion_ready"] is False


def test_shadow_status_includes_promotion_report_for_labeled_records(tmp_path):
    path = tmp_path / "shadow.jsonl"
    rows = []
    for index in range(30):
        rows.append(
            json.dumps(
                {
                    "text_hash": f"weather_{index}",
                    "outcome_label": "weather",
                    "zoe_intent": "reminder_list",
                    "pi_intent": "weather",
                    "zoe_latency_ms": 500,
                    "pi_latency_ms": 120,
                    "pi_confidence": 0.9,
                    "pi_transport": "rpc",
                    "route_class": "fallback",
                    "agreement": False,
                    "timed_out": False,
                    "pi_no_result": False,
                }
            )
        )
    path.write_text("\n".join(rows) + "\n")

    status = pi_intent_shadow_status(
        {
            "ZOE_PI_INTENT_SHADOW_PATH": str(path),
            "ZOE_PI_INTENT_PROMOTED_GROUPS": "weather",
        }
    )

    assert status["report"]["accuracy_available"] is True
    assert status["report"]["labeled_sample_count"] == 30
    assert status["report"]["promotion_ready"] is True
    assert "weather" in status["promotion_report"]["promotable_groups"]
    assert status["promotion_report"]["promoted_groups"] == ["weather"]
    assert status["promotion_report"]["promotion_actions"]["next_promoted_groups"] == ["weather"]


def test_shadow_status_ignores_unknown_promoted_groups(tmp_path):
    path = tmp_path / "shadow.jsonl"
    path.write_text("")

    status = pi_intent_shadow_status(
        {
            "ZOE_PI_INTENT_SHADOW_PATH": str(path),
            "ZOE_PI_INTENT_PROMOTED_GROUPS": "weather,device_control",
        }
    )

    assert status["promotion_report"]["promoted_groups"] == ["weather"]
    assert status["ignored_promoted_groups"] == ["device_control"]


def test_shadow_status_lists_rollback_groups_for_labeled_promoted_regression(tmp_path):
    path = tmp_path / "shadow.jsonl"
    rows = []
    for index in range(30):
        rows.append(
            json.dumps(
                {
                    "text_hash": f"weather_bad_{index}",
                    "outcome_label": "weather",
                    "zoe_intent": "weather",
                    "pi_intent": "reminder_list",
                    "zoe_latency_ms": 120,
                    "pi_latency_ms": 500,
                    "pi_confidence": 0.9,
                    "pi_transport": "rpc",
                    "route_class": "fallback",
                    "agreement": False,
                    "timed_out": False,
                    "pi_no_result": False,
                }
            )
        )
    path.write_text("\n".join(rows) + "\n")

    status = pi_intent_shadow_status(
        {
            "ZOE_PI_INTENT_SHADOW_PATH": str(path),
            "ZOE_PI_INTENT_PROMOTED_GROUPS": "weather",
        }
    )

    assert "weather" in status["promotion_report"]["rollback_groups"]
    assert status["promotion_report"]["promotion_actions"]["next_promoted_groups"] == []
