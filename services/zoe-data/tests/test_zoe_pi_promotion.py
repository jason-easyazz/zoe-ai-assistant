import pytest

from zoe_pi_promotion import (
    DEFAULT_PI_INTENT_EVAL_CASES,
    PiIntentEvalCase,
    PiPromotionPolicy,
    PiRouteSample,
    eval_cases_to_dict,
    evaluate_pi_promotion,
    intent_group_for_intent,
    summarize_pi_promotion,
)


def _sample(index, *, group="weather", zoe="reminder_list", pi="weather", expected="weather", zoe_ms=900, pi_ms=300, **kwargs):
    return PiRouteSample(
        case_id=f"case_{index}",
        intent_group=group,
        expected_intent=expected,
        zoe_intent=zoe,
        pi_intent=pi,
        zoe_latency_ms=zoe_ms,
        pi_latency_ms=pi_ms,
        pi_confidence=0.91,
        **kwargs,
    )


def test_default_eval_cases_validate_and_include_negative_chat_cases():
    payload = eval_cases_to_dict(DEFAULT_PI_INTENT_EVAL_CASES)

    assert any(case["case_id"] == "weather_rain_later" for case in payload)
    assert any(case["negative"] and case["intent_group"] == "chat" for case in payload)


def test_eval_case_rejects_privileged_expected_intent():
    case = PiIntentEvalCase(
        case_id="bad",
        text="upgrade yourself",
        expected_intent="extend_capability",
        intent_group="weather",
        route_class="fallback",
    )

    with pytest.raises(ValueError, match="privileged intents"):
        case.validate()


def test_eval_case_rejects_expected_intent_outside_group():
    case = PiIntentEvalCase(
        case_id="bad_group",
        text="rain later",
        expected_intent="reminder_list",
        intent_group="weather",
        route_class="fallback",
    )

    with pytest.raises(ValueError, match="expected_intent does not belong"):
        case.validate()


def test_intent_group_for_intent_maps_low_risk_groups_only():
    assert intent_group_for_intent("weather") == "weather"
    assert intent_group_for_intent("reminder_create") == "reminders"
    assert intent_group_for_intent("extend_capability") is None


def test_promotion_requires_five_percent_accuracy_win_and_latency_win():
    samples = [_sample(i) for i in range(10)]
    policy = PiPromotionPolicy(min_samples=10, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "promote"
    assert decision.pi_accuracy == 1.0
    assert decision.zoe_accuracy == 0.0
    assert decision.latency_delta_ms and decision.latency_delta_ms > 0
    assert decision.blockers == ()


def test_promotion_blocks_when_accuracy_delta_is_too_small():
    samples = [_sample(i, zoe="weather", pi="weather") for i in range(10)]
    policy = PiPromotionPolicy(min_samples=10, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "keep_shadow"
    assert "accuracy_delta_below_threshold" in decision.blockers


def test_promotion_blocks_when_pi_is_not_faster_than_zoe():
    samples = [_sample(i, zoe_ms=250, pi_ms=450) for i in range(10)]
    policy = PiPromotionPolicy(min_samples=10, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "keep_shadow"
    assert "latency_not_faster_than_zoe" in decision.blockers


def test_promotion_blocks_non_allowlisted_groups():
    decision = evaluate_pi_promotion([], intent_group="device_control", policy=PiPromotionPolicy(min_samples=1))

    assert decision.state == "blocked"
    assert decision.blockers == ("intent_group_not_allowlisted",)


def test_promoted_group_rolls_back_on_accuracy_regression():
    samples = [_sample(i, zoe="weather", pi="reminder_list") for i in range(10)]
    policy = PiPromotionPolicy(min_samples=10, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy, promoted=True)

    assert decision.state == "rollback"
    assert "accuracy_delta_below_threshold" in decision.blockers


def test_promoted_group_rolls_back_when_evidence_stops():
    decision = evaluate_pi_promotion([], intent_group="weather", policy=PiPromotionPolicy(min_samples=10), promoted=True)

    assert decision.state == "rollback"
    assert decision.blockers == ("insufficient_samples",)


def test_promoted_group_rolls_back_when_evidence_is_too_thin():
    samples = [_sample(1)]
    policy = PiPromotionPolicy(min_samples=10)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy, promoted=True)

    assert decision.state == "rollback"
    assert "insufficient_samples" in decision.blockers


def test_promoted_group_rolls_back_on_timeout_regression():
    samples = [_sample(i) for i in range(9)] + [_sample(99, timed_out=True, pi=None, pi_ms=1200)]
    policy = PiPromotionPolicy(min_samples=10, max_timeout_rate=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy, promoted=True)

    assert decision.state == "rollback"
    assert "timeout_rate_too_high" in decision.blockers


def test_promoted_group_rolls_back_on_user_corrections():
    samples = [_sample(i) for i in range(9)] + [_sample(99, user_corrected=True)]
    policy = PiPromotionPolicy(min_samples=10, max_correction_rate=0.03)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy, promoted=True)

    assert decision.state == "rollback"
    assert "correction_rate_too_high" in decision.blockers


def test_rollback_blocked_overrides_promoted_regression():
    samples = [_sample(i, rollback_blocked=i == 0) for i in range(10)]
    policy = PiPromotionPolicy(min_samples=10)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy, promoted=True)

    assert decision.state == "blocked"
    assert "rollback_blocked" in decision.blockers


def test_sample_rejects_secret_metadata():
    sample = _sample(1, metadata={"api_key": "nope"})

    with pytest.raises(ValueError, match="secret field"):
        sample.validate()


def test_summary_lists_promotable_groups():
    samples = [_sample(i) for i in range(10)]
    report = summarize_pi_promotion(samples, policy=PiPromotionPolicy(min_samples=10))

    assert "weather" in report["promotable_groups"]
    assert report["promoted_groups"] == []
    assert report["sample_count"] == 10
    assert report["policy"]["accuracy_win_margin"] == 0.05


def test_summary_lists_rollback_groups_for_active_promotions():
    samples = [_sample(i, zoe="weather", pi="reminder_list") for i in range(10)]
    report = summarize_pi_promotion(samples, policy=PiPromotionPolicy(min_samples=10), promoted_groups=["weather"])

    assert report["promoted_groups"] == ["weather"]
    assert "weather" in report["rollback_groups"]


def test_summary_rejects_unknown_promoted_groups():
    with pytest.raises(ValueError, match="unknown promoted_groups"):
        summarize_pi_promotion([], promoted_groups=["device_control"])
