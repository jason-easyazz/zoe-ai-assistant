import asyncio
import json

import pytest

from pi_intent_classifier import PiIntentClassification
from pi_intent_shadow import (
    PiIntentShadowConfig,
    append_pi_intent_shadow_label,
    apply_pi_intent_shadow_labels,
    load_pi_intent_shadow_labels,
    load_pi_intent_shadow_records,
    maybe_record_pi_intent_shadow,
    pi_intent_shadow_status,
    shadow_records_to_route_samples,
    summarize_pi_intent_shadow,
)
from zoe_pi_promotion import PiPromotionPolicy

pytestmark = pytest.mark.ci_safe


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
    assert record["baseline_kind"] == "router"
    assert record["baseline_comparable"] is True
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
            "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(tmp_path / "no-labels.jsonl"),
        }
    )

    assert status["config"]["enabled"] is True
    assert status["record_count_window"] == 2
    assert status["report"]["agreement_rate"] == 0.5
    assert status["report"]["timeout_rate"] == 0.5
    assert status["report"]["no_result_rate"] == 0.5
    assert status["report"]["accuracy_available"] is False
    assert status["report"]["promotion_ready"] is False



def test_shadow_status_applies_trusted_sidecar_labels(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    text_hash = "weatherhash"
    shadow_path.write_text(
        json.dumps(
            {
                "text_hash": text_hash,
                "text_preview": "rain later",
                "route_class": "fallback",
                "zoe_intent": None,
                "zoe_latency_ms": 500,
                "pi_intent": "weather",
                "pi_latency_ms": 120,
                "pi_confidence": 0.91,
                "pi_transport": "rpc",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    labels_path.write_text(
        json.dumps(
            {
                "text_hash": text_hash,
                "outcome_label": "weather",
                "baseline_kind": "operator_fallback_override",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    status = pi_intent_shadow_status(
        {
            "ZOE_PI_INTENT_SHADOW_ENABLED": "true",
            "ZOE_PI_INTENT_SHADOW_PATH": str(shadow_path),
            "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(labels_path),
        }
    )

    assert status["label_count"] == 1
    assert status["record_count_window"] == 1
    assert status["report"]["accuracy_available"] is True
    assert status["report"]["labeled_sample_count_by_group"]["weather"] == 1
    decision = [
        item for item in status["promotion_report"]["decisions"] if item["intent_group"] == "weather"
    ][0]
    assert decision["sample_count"] == 1
    assert decision["pi_accuracy"] == 1.0
    assert decision["zoe_accuracy"] == 0.0
    assert "insufficient_samples" in decision["blockers"]


def test_append_shadow_label_requires_existing_record_and_persists_sidecar(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    shadow_path.write_text(
        json.dumps(
            {
                "text_hash": "weatherhash",
                "text_preview": "rain later",
                "route_class": "fallback",
                "zoe_intent": None,
                "zoe_latency_ms": 500,
                "pi_intent": "weather",
                "pi_latency_ms": 120,
                "pi_confidence": 0.91,
                "pi_transport": "rpc",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = append_pi_intent_shadow_label(
        text_hash="weatherhash",
        outcome_label="weather",
        reviewed_by="admin@example.test",
        config=PiIntentShadowConfig(enabled=True, path=str(shadow_path), labels_path=str(labels_path)),
    )

    saved = json.loads(labels_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["label"]["outcome_label"] == "weather"
    assert result["matched_record"]["text_preview"] == "rain later"
    assert result["labels_store"] == "shadow_labels_sidecar"
    assert "labels_path" not in result
    assert saved["text_hash"] == "weatherhash"
    assert saved["outcome_label"] == "weather"
    assert len(saved["reviewed_by_hash"]) == 64
    assert "admin@example.test" not in labels_path.read_text(encoding="utf-8")

    status = pi_intent_shadow_status(
        {
            "ZOE_PI_INTENT_SHADOW_ENABLED": "true",
            "ZOE_PI_INTENT_SHADOW_PATH": str(shadow_path),
            "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(labels_path),
        }
    )
    assert status["label_count"] == 1
    assert status["report"]["labeled_sample_count_by_group"]["weather"] == 1


def test_append_shadow_label_rejects_unknown_or_privileged_labels(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    shadow_path.write_text(json.dumps({"text_hash": "known", "text_preview": "upgrade yourself"}) + "\n")
    config = PiIntentShadowConfig(enabled=True, path=str(shadow_path), labels_path=str(labels_path))

    with pytest.raises(ValueError, match="most-recent"):
        append_pi_intent_shadow_label(text_hash="missing", outcome_label="weather", config=config)
    with pytest.raises(ValueError, match="source must be one of"):
        append_pi_intent_shadow_label(
            text_hash="known",
            outcome_label="weather",
            source="freeform",
            config=config,
        )
    with pytest.raises(ValueError, match="low-risk"):
        append_pi_intent_shadow_label(text_hash="known", outcome_label="extend_capability", config=config)
    with pytest.raises(ValueError, match="low-risk"):
        append_pi_intent_shadow_label(text_hash="known", outcome_label="weather", negative=True, config=config)
    assert not labels_path.exists()


def test_shadow_label_loader_ignores_unmapped_and_applies_negative_labels(tmp_path):
    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text(
        "\n".join(
            [
                json.dumps({"text_hash": "weather", "expected_intent": "weather"}),
                json.dumps({"text_hash": "casual", "negative": True}),
                json.dumps({"text_hash": "bad", "expected_intent": "extend_capability"}),
                json.dumps({"text_hash": "conflict", "negative": True, "outcome_label": "weather"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    labels = load_pi_intent_shadow_labels(str(labels_path))
    records = apply_pi_intent_shadow_labels(
        [{"text_hash": "weather"}, {"text_hash": "casual"}, {"text_hash": "bad"}, {"text_hash": "conflict"}],
        labels,
    )

    assert sorted(labels) == ["casual", "weather"]
    assert records[0]["outcome_label"] == "weather"
    assert records[0]["outcome_label_source"] == "shadow_label_sidecar"
    assert records[1]["negative"] is True
    assert records[1]["outcome_label"] is None
    assert "outcome_label" not in records[2]
    assert "outcome_label" not in records[3]


def test_shadow_summary_empty_is_explicitly_not_accuracy_ready():
    report = summarize_pi_intent_shadow([])

    assert report["sample_count"] == 0
    assert report["no_result_rate"] == 0.0
    assert report["accuracy_available"] is False
    assert report["labeled_sample_count_by_group"]["weather"] == 0
    assert report["unmapped_labeled_sample_count"] == 0
    assert report["sample_deficit_by_group"]["weather"] == PiPromotionPolicy().min_samples
    assert report["promotion_ready_groups"] == []


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
    assert calls[0]["baseline_kind"] == "router"
    assert calls[0]["baseline_comparable"] is True
    assert calls[0]["router_latency_ms"] is not None


@pytest.mark.asyncio
async def test_intent_router_shadow_extraction_failed_records_pre_pi_latency_and_baseline(monkeypatch):
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_PROMOTED_GROUPS", "reminders")
    calls = []

    async def fake_shadow(text, **kwargs):
        calls.append({"text": text, **kwargs})
        return {"pi_intent": "reminder_create", "agreement": False}

    async def fake_extract(_intent_name, _raw):
        return None

    async def fake_classify(text, *, context_turns="", env=None, config=None):
        return PiIntentClassification(
            intent="reminder_create",
            slots={"title": "call mum"},
            confidence=0.89,
            task_lane="fast_tool",
            source="pi_test",
            latency_ms=250.0,
        )

    from intent_router import detect_and_extract_intent
    import intent_router

    ticks = iter([100.0, 100.002, 100.006])
    monkeypatch.setattr(intent_router.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr("nlu_extractor.extract_slots_for_intent", fake_extract)
    monkeypatch.setattr("pi_intent_classifier.classify_with_pi_intent_governor", fake_classify)
    monkeypatch.setattr("pi_intent_shadow.maybe_record_pi_intent_shadow", fake_shadow)

    intent = await detect_and_extract_intent("remind me to call mum every Monday", user_id="jason")
    await asyncio.sleep(0)

    assert intent is not None
    assert intent.name == "reminder_create"
    assert calls
    assert calls[0]["route_class"] == "extraction_failed"
    assert calls[0]["baseline_kind"] == "router_extraction_failed_not_comparable"
    assert calls[0]["baseline_comparable"] is False
    assert calls[0]["zoe_latency_ms"] == pytest.approx(6.0)
    assert calls[0]["router_latency_ms"] == pytest.approx(2.0)
    with pytest.raises(StopIteration):
        next(ticks)


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
    assert record["baseline_kind"] == "router_only_not_comparable"
    assert record["baseline_comparable"] is False
    assert record["router_latency_ms"] is not None


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
                "user_corrected": "true",
                "rollback_blocked": False,
            },
            {"outcome_label": "extend_capability", "zoe_intent": None, "pi_intent": "extend_capability"},
        ]
    )

    assert len(samples) == 1
    assert samples[0].intent_group == "weather"
    assert samples[0].zoe_correct is False
    assert samples[0].pi_correct is True
    assert samples[0].user_corrected is True
    assert samples[0].rollback_blocked is False
    assert samples[0].metadata["baseline_kind"] == "router_only_not_comparable"
    assert samples[0].metadata["baseline_comparable"] is False


def test_labeled_shadow_record_with_explicit_comparable_kind_defaults_to_comparable():
    samples = shadow_records_to_route_samples(
        [
            {
                "text_hash": "reviewed_fallback",
                "outcome_label": "weather",
                "zoe_intent": "reminder_list",
                "pi_intent": "weather",
                "zoe_latency_ms": 500,
                "pi_latency_ms": 120,
                "pi_confidence": 0.9,
                "pi_transport": "rpc",
                "route_class": "fallback",
                "baseline_kind": "operator_fallback_override",
            }
        ]
    )

    assert len(samples) == 1
    assert samples[0].metadata["baseline_kind"] == "operator_fallback_override"
    assert samples[0].metadata["baseline_comparable"] is True


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


def test_shadow_summary_counts_duplicate_text_hash_labels_once():
    records = [
        {"text_hash": "same", "outcome_label": "weather"},
        {"text_hash": "same", "outcome_label": "weather"},
        {"text_hash": "other", "outcome_label": "weather"},
        {"outcome_label": "weather"},
        {"outcome_label": "weather"},
    ]

    report = summarize_pi_intent_shadow(records)

    assert report["sample_count"] == 5
    assert report["labeled_sample_count"] == 4
    assert report["labeled_sample_count_by_group"]["weather"] == 4
    assert report["sample_deficit_by_group"]["weather"] == PiPromotionPolicy().min_samples - 4


def test_shadow_summary_uses_last_label_for_duplicate_text_hash():
    records = [
        {"text_hash": "same", "outcome_label": "weather"},
        {"text_hash": "same", "outcome_label": "timer_create"},
    ]

    report = summarize_pi_intent_shadow(records)

    assert report["sample_count"] == 2
    assert report["labeled_sample_count"] == 1
    assert report["labeled_sample_count_by_group"]["weather"] == 0
    assert report["labeled_sample_count_by_group"]["timers"] == 1


def test_shadow_summary_needs_min_samples_for_promotion_ready():
    report = summarize_pi_intent_shadow([{"outcome_label": "weather"} for _ in range(29)])

    assert report["accuracy_available"] is True
    assert report["labeled_sample_count"] == 29
    assert report["labeled_sample_count_by_group"]["weather"] == 29
    assert report["sample_deficit_by_group"]["weather"] == 1
    assert report["promotion_ready"] is False
    assert report["promotion_ready_groups"] == []


def test_shadow_summary_requires_min_samples_in_one_group_for_promotion_ready():
    records = [{"outcome_label": "weather"} for _ in range(15)]
    records.extend({"outcome_label": "timer_create"} for _ in range(15))

    report = summarize_pi_intent_shadow(records)

    assert report["accuracy_available"] is True
    assert report["labeled_sample_count"] == 30
    assert report["labeled_sample_count_by_group"]["weather"] == 15
    assert report["labeled_sample_count_by_group"]["timers"] == 15
    assert report["sample_deficit_by_group"]["weather"] == 15
    assert report["sample_deficit_by_group"]["timers"] == 15
    assert report["promotion_ready"] is False
    assert report["promotion_ready_groups"] == []
    assert report["promotion_ready_reason"] == (
        "labeled outcome evidence exists, but no intent group has enough labels for promotion scoring"
    )


def test_shadow_summary_reports_unmapped_labeled_evidence_separately():
    report = summarize_pi_intent_shadow([{"outcome_label": "extend_capability"}])

    assert report["accuracy_available"] is False
    assert report["labeled_sample_count"] == 0
    assert report["unmapped_labeled_sample_count"] == 1
    assert report["promotion_ready"] is False
    assert report["promotion_ready_reason"] == (
        "labeled outcome evidence exists, but no labels map to a low-risk intent group"
    )


def test_shadow_summary_names_groups_with_enough_labeled_evidence():
    report = summarize_pi_intent_shadow([{"outcome_label": "weather"} for _ in range(PiPromotionPolicy().min_samples)])

    assert report["promotion_ready"] is True
    assert report["promotion_ready_groups"] == ["weather"]
    assert report["unmapped_labeled_sample_count"] == 0
    assert report["sample_deficit_by_group"]["weather"] == 0


def test_labeled_shadow_fallback_without_comparable_baseline_blocks_promotion(tmp_path):
    path = tmp_path / "shadow.jsonl"
    rows = []
    for index in range(PiPromotionPolicy().min_samples):
        rows.append(
            json.dumps(
                {
                    "text_hash": f"weather_router_only_{index}",
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
            "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(tmp_path / "no-labels.jsonl"),
        }
    )

    weather = next(
        decision for decision in status["promotion_report"]["decisions"] if decision["intent_group"] == "weather"
    )
    assert weather["state"] == "keep_shadow"
    assert "baseline_not_comparable" in weather["blockers"]
    assert "weather" not in status["promotion_report"]["promotable_groups"]


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
                    "baseline_kind": "operator_fallback_override",
                    "baseline_comparable": True,
                    "router_latency_ms": 5,
                }
            )
        )
    path.write_text("\n".join(rows) + "\n")

    status = pi_intent_shadow_status(
        {
            "ZOE_PI_INTENT_SHADOW_PATH": str(path),
            "ZOE_PI_INTENT_PROMOTED_GROUPS": "weather",
            "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(tmp_path / "no-labels.jsonl"),
        }
    )

    assert status["report"]["accuracy_available"] is True
    assert status["report"]["labeled_sample_count"] == 30
    assert status["report"]["promotion_ready"] is True
    assert status["report"]["promotion_ready_groups"] == ["weather"]
    assert status["report"]["sample_deficit_by_group"]["weather"] == 0
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
            "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(tmp_path / "no-labels.jsonl"),
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
            "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(tmp_path / "no-labels.jsonl"),
        }
    )

    assert "weather" in status["promotion_report"]["rollback_groups"]
    assert status["promotion_report"]["promotion_actions"]["next_promoted_groups"] == []


def test_shadow_status_rolls_back_promoted_group_on_reviewed_corrections(tmp_path):
    path = tmp_path / "shadow.jsonl"
    policy = PiPromotionPolicy()
    correction_count = int(policy.max_correction_rate * policy.min_samples) + 1
    rows = []
    for index in range(policy.min_samples):
        rows.append(
            json.dumps(
                {
                    "text_hash": f"weather_corrected_{index}",
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
                    "user_corrected": index < correction_count,
                }
            )
        )
    path.write_text("\n".join(rows) + "\n")

    status = pi_intent_shadow_status(
        {
            "ZOE_PI_INTENT_SHADOW_PATH": str(path),
            "ZOE_PI_INTENT_PROMOTED_GROUPS": "weather",
            "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(tmp_path / "no-labels.jsonl"),
        }
    )

    weather = next(
        decision for decision in status["promotion_report"]["decisions"] if decision["intent_group"] == "weather"
    )
    assert weather["state"] == "rollback"
    assert "correction_rate_too_high" in weather["blockers"]
    assert status["promotion_report"]["promotion_actions"]["rollback_groups"] == ["weather"]


def test_shadow_status_blocks_promoted_group_on_reviewed_rollback_block(tmp_path):
    path = tmp_path / "shadow.jsonl"
    rows = []
    for index in range(PiPromotionPolicy().min_samples):
        rows.append(
            json.dumps(
                {
                    "text_hash": f"weather_blocked_{index}",
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
                    "rollback_blocked": index == 0,
                }
            )
        )
    path.write_text("\n".join(rows) + "\n")

    status = pi_intent_shadow_status(
        {
            "ZOE_PI_INTENT_SHADOW_PATH": str(path),
            "ZOE_PI_INTENT_PROMOTED_GROUPS": "weather",
            "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(tmp_path / "no-labels.jsonl"),
        }
    )

    weather = next(
        decision for decision in status["promotion_report"]["decisions"] if decision["intent_group"] == "weather"
    )
    assert weather["state"] == "blocked"
    assert "rollback_blocked" in weather["blockers"]
    assert status["promotion_report"]["promotion_actions"] == {
        "promote_groups": [],
        "rollback_groups": [],
        "keep_promoted_groups": ["weather"],
        "next_promoted_groups": ["weather"],
        "env": {"ZOE_PI_INTENT_PROMOTED_GROUPS": "weather"},
        "requires_operator_apply": False,
    }


def test_shadow_status_includes_failure_examples_without_text(tmp_path):
    path = tmp_path / "shadow.jsonl"
    path.write_text(
        json.dumps(
            {
                "text_hash": "weather_failure_hash",
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
                "text_preview": "rain later",
            }
        )
        + "\n"
    )

    status = pi_intent_shadow_status(
        {
            "ZOE_PI_INTENT_SHADOW_PATH": str(path),
            "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(tmp_path / "no-labels.jsonl"),
        }
    )

    examples = status["promotion_report"]["failure_examples"]
    assert len(examples) == 1
    assert examples[0]["case_id"] == "weather_failure_hash"
    assert examples[0]["reasons"] == ["pi_wrong_intent"]
    assert examples[0]["source"] == "pi_intent_shadow"
    assert "text_preview" not in examples[0]
    assert "rain later" not in json.dumps(examples)
