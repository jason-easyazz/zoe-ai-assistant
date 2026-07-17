import hashlib
import json
import threading
from pathlib import Path

import pytest

import pi_intent_evidence
from intent_router import detect_intent
from pi_intent_evidence import (
    append_pi_hybrid_production_label,
    apply_pi_hybrid_production_labels,
    build_pi_hybrid_production_label_queue,
    load_pi_hybrid_production_labels,
    production_records_to_route_samples,
    record_intent_miss_evidence,
    record_pi_hybrid_production_evidence,
    sanitize_evidence_text,
)

pytestmark = pytest.mark.ci_safe


def test_record_intent_miss_evidence_disabled_does_not_write(tmp_path):
    path = tmp_path / "misses.jsonl"

    result = record_intent_miss_evidence(
        "email jason@example.com if rain later",
        env={"ZOE_PI_INTENT_MISS_EVIDENCE_PATH": str(path)},
    )

    assert result is None
    assert not path.exists()


def test_record_intent_miss_evidence_writes_sanitized_jsonl(tmp_path):
    path = tmp_path / "misses.jsonl"

    result = record_intent_miss_evidence(
        "email jason@example.com if rain later",
        user_id="jason",
        env={
            "ZOE_PI_INTENT_MISS_EVIDENCE_ENABLED": "true",
            "ZOE_PI_INTENT_MISS_EVIDENCE_PATH": str(path),
        },
    )

    assert result is not None
    saved = json.loads(path.read_text(encoding="utf-8"))
    expected_user_hash = hashlib.sha256(b"jason").hexdigest()[:16]
    assert saved["source"] == "intent_miss"
    assert saved["user_hash"] == expected_user_hash
    assert saved["route_class"] == "fallback"
    assert saved["text"] == "email [EMAIL] if rain later"
    assert saved["text_hash"]
    assert saved["user_hash"]
    assert saved["expected_intent"] is None
    assert saved["outcome_label"] is None
    assert "jason@example.com" not in path.read_text(encoding="utf-8")


def test_record_intent_miss_evidence_skips_secret_like_text(tmp_path):
    path = tmp_path / "misses.jsonl"

    result = record_intent_miss_evidence(
        "my api key is abc123",
        env={
            "ZOE_PI_INTENT_MISS_EVIDENCE_ENABLED": "true",
            "ZOE_PI_INTENT_MISS_EVIDENCE_PATH": str(path),
        },
    )

    assert result is None
    assert not path.exists()


def _production_decision():
    return {
        "config": {"groups": ["weather"], "transport": "rpc"},
        "accepted": True,
        "reason": "accepted",
        "production_route_change": True,
        "intent": "weather",
        "intent_group": "weather",
        "agreement_kind": "zoe_router",
        "lab_result": {
            "zoe_router": {
                "intent": "weather",
                "route_class": "deterministic",
                "baseline_kind": "router",
                "latency_ms": 12.0,
            },
            "pi": {
                "intent": "weather",
                "intent_group": "weather",
                "confidence": 0.94,
                "latency_ms": 123.0,
                "transport": "rpc",
            },
            "safe_fulfillment": {
                "intent": "weather",
                "latency_ms": 45.0,
                "timed_out": False,
                "error": None,
                "response_chars": 25,
            },
        },
    }


def test_record_pi_hybrid_production_evidence_disabled_does_not_write(tmp_path):
    path = tmp_path / "production.jsonl"

    result = record_pi_hybrid_production_evidence(
        "will it rain later",
        user_id="jason",
        decision=_production_decision(),
        env={"ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH": str(path)},
    )

    assert result is None
    assert not path.exists()


def test_record_pi_hybrid_production_evidence_writes_compact_sanitized_jsonl(tmp_path):
    path = tmp_path / "production.jsonl"

    result = record_pi_hybrid_production_evidence(
        "will it rain later for Jason Smith",
        user_id="jason",
        decision=_production_decision(),
        env={
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED": "true",
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH": str(path),
        },
    )

    assert result is not None
    saved = json.loads(path.read_text(encoding="utf-8"))
    expected_user_hash = hashlib.sha256(b"jason").hexdigest()[:16]
    assert saved["source"] == "pi_hybrid_production"
    assert saved["user_hash"] == expected_user_hash
    assert saved["text_preview"] == "will it rain later for [NAME]"
    assert saved["accepted"] is True
    assert saved["reason"] == "accepted"
    assert saved["intent"] == "weather"
    assert saved["pi_intent"] == "weather"
    assert saved["pi_confidence"] == 0.94
    assert saved["pi_latency_ms"] == 123.0
    assert saved["zoe_latency_ms"] == 12.0
    assert saved["safe_fulfillment_latency_ms"] == 45.0
    assert saved["response_chars"] == 25
    assert saved["enabled_groups"] == ["weather"]
    assert saved["outcome_label"] is None
    assert "Jason Smith" not in path.read_text(encoding="utf-8")



def test_record_pi_hybrid_production_evidence_prunes_to_configured_limit(tmp_path):
    path = tmp_path / "production.jsonl"

    for index in range(3):
        decision = _production_decision()
        decision["intent"] = f"weather-{index}"
        record_pi_hybrid_production_evidence(
            f"will it rain later {index}",
            user_id="jason",
            decision=decision,
            env={
                "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED": "true",
                "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH": str(path),
                "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_MAX_RECORDS": "2",
            },
        )

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert len(rows) == 2
    assert [row["intent"] for row in rows] == ["weather-1", "weather-2"]



def test_build_pi_hybrid_production_label_queue_prioritizes_unlabeled_accepted_records():
    payload = build_pi_hybrid_production_label_queue(
        [
            {
                "ts": 1,
                "text_hash": "weather-old",
                "text_preview": "old rain",
                "accepted": True,
                "intent": "weather",
                "intent_group": "weather",
                "pi_intent": "weather",
                "pi_latency_ms": 500.0,
                "safe_fulfillment_latency_ms": 50.0,
                "outcome_label": None,
            },
            {
                "ts": 2,
                "text_hash": "weather-old",
                "text_preview": "new rain",
                "accepted": True,
                "intent": "weather",
                "intent_group": "weather",
                "pi_intent": "weather",
                "route_class": "deterministic",
                "baseline_kind": "router",
                "baseline_comparable": True,
                "zoe_latency_ms": 10.0,
                "pi_latency_ms": 120.0,
                "safe_fulfillment_latency_ms": 45.0,
                "production_route_change": True,
                "outcome_label": None,
            },
            {
                "ts": 3,
                "text_hash": "briefing",
                "text_preview": "daily briefing",
                "accepted": True,
                "intent": "daily_briefing",
                "intent_group": "daily_briefing",
                "pi_intent": "daily_briefing",
                "outcome_label": "daily_briefing",
            },
            {
                "ts": 4,
                "text_hash": "timeout",
                "text_preview": "weather tomorrow",
                "accepted": False,
                "reason": "timeout",
                "intent_group": "weather",
                "outcome_label": "weather",
            },
        ],
        limit=10,
    )

    assert payload["summary"]["raw_record_count"] == 4
    assert payload["summary"]["unique_text_count"] == 3
    assert payload["summary"]["skipped_labeled_count"] == 2
    assert payload["summary"]["skipped_rejected_count"] == 1
    assert payload["summary"]["queue_count_by_group"] == {"weather": 1}
    assert len(payload["queue"]) == 1
    row = payload["queue"][0]
    assert row["text_hash"] == "weather-old"
    assert row["text_preview"] == "new rain"
    assert row["suggested_outcome_label"] == "weather"
    assert row["label_example"] == {
        "text_hash": "weather-old",
        "source": "admin_review",
        "outcome_label": "weather",
        "route_class": "deterministic",
        "baseline_kind": "router",
        "baseline_comparable": True,
        "zoe_latency_ms": 10.0,
    }


def test_build_pi_hybrid_production_label_queue_filters_groups_and_rejected():
    records = [
        {"text_hash": "weather", "accepted": True, "intent": "weather", "intent_group": "weather"},
        {"text_hash": "briefing", "accepted": True, "intent": "daily_briefing", "intent_group": "daily_briefing"},
        {"text_hash": "chat", "accepted": False, "reason": "pi_disagreed", "intent_group": "weather"},
    ]

    payload = build_pi_hybrid_production_label_queue(records, groups=["daily_briefing"], include_rejected=True)

    assert [row["text_hash"] for row in payload["queue"]] == ["briefing"]
    assert payload["summary"]["skipped_group_count"] == 2
    with pytest.raises(ValueError, match="unsupported production label group"):
        build_pi_hybrid_production_label_queue(records, groups=["self_evolution"])


def test_pi_hybrid_production_label_sidecar_applies_latest_valid_label(tmp_path):
    evidence_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    record = record_pi_hybrid_production_evidence(
        "will it rain later",
        user_id="jason",
        decision=_production_decision(),
        env={
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED": "true",
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH": str(evidence_path),
        },
    )
    assert record is not None

    result = append_pi_hybrid_production_label(
        text_hash=record["text_hash"],
        outcome_label="weather",
        reviewed_by="jason",
        evidence_path=str(evidence_path),
        labels_path=str(labels_path),
    )

    labels = load_pi_hybrid_production_labels(str(labels_path))
    labeled = apply_pi_hybrid_production_labels([record], labels)

    assert result["ok"] is True
    assert result["labels_store"] == "production_labels_sidecar"
    assert result["matched_record"]["text_preview"] == "will it rain later"
    assert labels[record["text_hash"]]["outcome_label"] == "weather"
    assert labeled[0]["outcome_label"] == "weather"
    assert labeled[0]["outcome_label_source"] == "production_label_sidecar"
    assert "reviewed_by_hash" in labels_path.read_text(encoding="utf-8")


def test_pi_hybrid_production_label_can_override_comparable_baseline(tmp_path):
    evidence_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    record = {
        "text_hash": "briefing-hash",
        "source": "pi_hybrid_production",
        "outcome_label": None,
        "zoe_intent": None,
        "pi_intent": "daily_briefing",
        "pi_latency_ms": 2200.0,
        "pi_confidence": 0.93,
        "route_class": "fallback",
        "baseline_kind": "router_only_not_comparable",
        "baseline_comparable": False,
        "zoe_latency_ms": 1.0,
    }
    evidence_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    result = append_pi_hybrid_production_label(
        text_hash="briefing-hash",
        outcome_label="daily_briefing",
        route_class="fallback",
        baseline_kind="zoe_agent_fallback_baseline",
        baseline_comparable=True,
        zoe_latency_ms=4800.0,
        evidence_path=str(evidence_path),
        labels_path=str(labels_path),
    )

    labels = load_pi_hybrid_production_labels(str(labels_path))
    labeled = apply_pi_hybrid_production_labels([record], labels)
    samples = production_records_to_route_samples(labeled)

    assert result["label"]["baseline_kind"] == "zoe_agent_fallback_baseline"
    assert result["label"]["baseline_comparable"] is True
    assert result["label"]["zoe_latency_ms"] == 4800.0
    assert labeled[0]["zoe_latency_ms"] == 4800.0
    assert samples[0].zoe_latency_ms == 4800.0
    assert samples[0].metadata["baseline_kind"] == "zoe_agent_fallback_baseline"
    assert samples[0].metadata["baseline_comparable"] is True


def test_pi_hybrid_production_label_rejects_unknown_route_class_and_baseline_kind(tmp_path):
    evidence_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    evidence_path.write_text(
        json.dumps({"text_hash": "briefing-hash", "source": "pi_hybrid_production"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="low-risk"):
        append_pi_hybrid_production_label(
            text_hash="briefing-hash",
            outcome_label="daily_briefing",
            route_class="unknown_class",
            evidence_path=str(evidence_path),
            labels_path=str(labels_path),
        )
    with pytest.raises(ValueError, match="low-risk"):
        append_pi_hybrid_production_label(
            text_hash="briefing-hash",
            outcome_label="daily_briefing",
            baseline_kind="made_up_baseline",
            evidence_path=str(evidence_path),
            labels_path=str(labels_path),
        )
    assert not labels_path.exists()


def test_pi_hybrid_production_label_rejects_missing_or_privileged_label(tmp_path):
    evidence_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    record = record_pi_hybrid_production_evidence(
        "will it rain later",
        user_id="jason",
        decision=_production_decision(),
        env={
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED": "true",
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH": str(evidence_path),
        },
    )
    assert record is not None

    with pytest.raises(ValueError, match="text_hash not found"):
        append_pi_hybrid_production_label(
            text_hash="missing",
            outcome_label="weather",
            evidence_path=str(evidence_path),
            labels_path=str(labels_path),
        )
    with pytest.raises(ValueError, match="low-risk"):
        append_pi_hybrid_production_label(
            text_hash=record["text_hash"],
            outcome_label="extend_capability",
            evidence_path=str(evidence_path),
            labels_path=str(labels_path),
        )
    with pytest.raises(ValueError, match="low-risk"):
        append_pi_hybrid_production_label(
            text_hash=record["text_hash"],
            outcome_label="weather",
            negative=True,
            evidence_path=str(evidence_path),
            labels_path=str(labels_path),
        )


def test_production_records_to_route_samples_uses_reviewed_positive_labels_only():
    records = [
        {
            "text_hash": "weather-hash",
            "outcome_label": "weather",
            "zoe_intent": "weather",
            "pi_intent": "weather",
            "zoe_latency_ms": 1.0,
            "pi_latency_ms": 4200.0,
            "pi_confidence": 0.92,
            "pi_transport": "rpc",
            "route_class": "deterministic",
            "baseline_kind": "router",
            "safe_fulfillment_latency_ms": 900.0,
            "production_route_change": True,
            "accepted": True,
            "outcome_label_source": "production_label_sidecar",
        },
        {
            "text_hash": "chat-hash",
            "outcome_label": None,
            "negative": True,
            "zoe_latency_ms": 1.0,
            "pi_latency_ms": 2000.0,
            "route_class": "fallback",
        },
        {
            "text_hash": "missing-latency",
            "outcome_label": "weather",
            "pi_intent": "weather",
            "pi_latency_ms": 2000.0,
            "route_class": "fallback",
        },
    ]

    samples = production_records_to_route_samples(records)

    assert len(samples) == 1
    sample = samples[0]
    assert sample.case_id == "weather-hash"
    assert sample.intent_group == "weather"
    assert sample.expected_intent == "weather"
    assert sample.zoe_intent == "weather"
    assert sample.pi_intent == "weather"
    assert sample.zoe_latency_ms == 1.0
    assert sample.pi_latency_ms == 4200.0
    assert sample.metadata["source"] == "pi_hybrid_production"
    assert sample.metadata["baseline_kind"] == "router"
    assert sample.metadata["baseline_comparable"] is True
    assert sample.metadata["safe_fulfillment_latency_ms"] == 900.0



def test_record_pi_hybrid_production_evidence_skips_secret_like_text(tmp_path):
    path = tmp_path / "production.jsonl"

    result = record_pi_hybrid_production_evidence(
        "my bearer token is abc123",
        user_id="jason",
        decision=_production_decision(),
        env={
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED": "true",
            "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH": str(path),
        },
    )

    assert result is None
    assert not path.exists()


def test_sanitize_evidence_text_redacts_common_pii():
    assert sanitize_evidence_text("Call Jason Smith on 0400 111 222 via https://example.com") == (
        "Call [NAME] on [NUMBER] via [URL]"
    )
    assert sanitize_evidence_text("Jason Smith asked about the weather") == "[NAME] asked about the weather"
    assert sanitize_evidence_text("Will Smith called about the meeting") == "[NAME] called about the meeting"
    assert sanitize_evidence_text("Can Chen asked about the plan") == "[NAME] asked about the plan"


def test_append_jsonl_does_not_lose_records_under_concurrent_writers(tmp_path):
    """Concurrent evidence writers must not clobber each other's records.

    `_append_jsonl` prunes via a read-modify-write, so without a lock a writer
    that reads the file before a second writer appends will truncate that
    second record away. Production hits this for real: a router fast-accept
    writes its accepted decision while the fire-and-forget Pi audit task writes
    a disagreement, both through `asyncio.to_thread` onto the same path.

    Unlocked, this loses ~40% of records on every run; locked it is exact.
    """
    evidence_path = tmp_path / "concurrent-evidence.jsonl"
    writers = 8
    per_writer = 25
    start = threading.Barrier(writers)
    errors: list[BaseException] = []

    def write_records(writer_id: int) -> None:
        try:
            start.wait(timeout=30)
            for index in range(per_writer):
                pi_intent_evidence._append_jsonl(
                    str(evidence_path),
                    {"writer": writer_id, "index": index},
                    max_records=10000,
                )
        except BaseException as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)

    threads = [threading.Thread(target=write_records, args=(writer_id,)) for writer_id in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert not [thread for thread in threads if thread.is_alive()]

    lines = evidence_path.read_text(encoding="utf-8").splitlines()
    # Lost update: a concurrent prune truncates away records that landed after
    # it read the file, so the file comes up short.
    assert len(lines) == writers * per_writer
    # Torn line: an interleaved rewrite can also leave a half-written record.
    records = [json.loads(line) for line in lines]
    # Every writer's every record survived, exactly once.
    assert sorted((record["writer"], record["index"]) for record in records) == sorted(
        (writer_id, index) for writer_id in range(writers) for index in range(per_writer)
    )


def test_append_jsonl_prune_keeps_newest_records_under_max(tmp_path):
    """The lock must not cost the prune its job: the cap still holds."""
    evidence_path = tmp_path / "pruned-evidence.jsonl"
    for index in range(10):
        pi_intent_evidence._append_jsonl(str(evidence_path), {"index": index}, max_records=4)

    records = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines()]
    assert [record["index"] for record in records] == [6, 7, 8, 9]


def test_detect_intent_miss_produces_pi_evidence_when_enabled(tmp_path, monkeypatch):
    home = tmp_path / "home"
    evidence_path = tmp_path / "pi-misses.jsonl"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ZOE_PI_INTENT_MISS_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_MISS_EVIDENCE_PATH", str(evidence_path))

    intent = detect_intent("email jason@example.com if rain later", log_miss=True, user_id="jason")

    assert intent is None
    legacy_path = home / "training" / "data" / "intent-misses.jsonl"
    assert legacy_path.exists()
    assert evidence_path.exists()
    saved = json.loads(evidence_path.read_text(encoding="utf-8"))
    expected_user_hash = hashlib.sha256(b"jason").hexdigest()[:16]
    assert saved["text"] == "email [EMAIL] if rain later"
    assert saved["source"] == "intent_miss"
    assert saved["user_hash"] == expected_user_hash
