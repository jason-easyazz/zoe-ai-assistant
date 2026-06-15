import hashlib
import json
from pathlib import Path

from intent_router import detect_intent
from pi_intent_evidence import record_intent_miss_evidence, sanitize_evidence_text


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


def test_sanitize_evidence_text_redacts_common_pii():
    assert sanitize_evidence_text("Call Jason Smith on 0400 111 222 via https://example.com") == (
        "Call [NAME] on [NUMBER] via [URL]"
    )
    assert sanitize_evidence_text("Jason Smith asked about the weather") == "[NAME] asked about the weather"
    assert sanitize_evidence_text("Will Smith called about the meeting") == "[NAME] called about the meeting"
    assert sanitize_evidence_text("Can Chen asked about the plan") == "[NAME] asked about the plan"


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
