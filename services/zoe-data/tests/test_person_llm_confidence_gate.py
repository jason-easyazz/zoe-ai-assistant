"""P2 (ADR-contacts-production-hardening): confidence gate on LLM person-fact
extraction — high→apply, below-threshold→discard. Dark-flagged
(ZOE_PERSON_LLM_CONFIDENCE_GATE); OFF keeps every item (today's behaviour).
"""
import pytest

pytestmark = pytest.mark.ci_safe

import person_extractor_llm as pl


def test_off_keeps_every_item(monkeypatch):
    monkeypatch.delenv("ZOE_PERSON_LLM_CONFIDENCE_GATE", raising=False)
    assert pl.confidence_gate_enabled() is False
    # gate off → keep regardless of confidence (even missing / 0.0)
    assert pl._keep_item({"confidence": 0.0}, gated=False, min_conf=0.4) is True
    assert pl._keep_item({}, gated=False, min_conf=0.4) is True


@pytest.mark.parametrize("conf,keep", [
    (0.95, True), (0.4, True), (0.39, False), (0.0, False), ("0.8", True),
    (None, False), ("junk", False),
    # out-of-range / wrong-scale confidence fails CLOSED (not a real high signal)
    (5, False), (80, False), (1.0001, False), (-0.1, False),
])
def test_on_thresholds(conf, keep):
    item = {} if conf is None else {"confidence": conf}
    assert pl._keep_item(item, gated=True, min_conf=0.4) is keep


def test_missing_confidence_is_dropped_when_gated():
    # a gated item with no confidence field counts as 0.0 → dropped
    assert pl._keep_item({"name": "X", "value": "y"}, gated=True, min_conf=0.4) is False


def test_confidence_min_default_and_clamp(monkeypatch):
    monkeypatch.delenv("ZOE_PERSON_LLM_CONFIDENCE_MIN", raising=False)
    assert pl._confidence_min() == 0.4
    monkeypatch.setenv("ZOE_PERSON_LLM_CONFIDENCE_MIN", "0.7")
    assert pl._confidence_min() == 0.7
    monkeypatch.setenv("ZOE_PERSON_LLM_CONFIDENCE_MIN", "5")   # clamp high
    assert pl._confidence_min() == 1.0
    monkeypatch.setenv("ZOE_PERSON_LLM_CONFIDENCE_MIN", "-1")  # clamp low
    assert pl._confidence_min() == 0.0
    monkeypatch.setenv("ZOE_PERSON_LLM_CONFIDENCE_MIN", "junk")  # fallback
    assert pl._confidence_min() == 0.4


def test_flag_enables(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_LLM_CONFIDENCE_GATE", "1")
    assert pl.confidence_gate_enabled() is True
