import pytest

from zoe_pi_promotion import (
    DEFAULT_PI_INTENT_EVAL_CASES,
    PiIntentEvalCase,
    PiPromotionPolicy,
    PiRouteSample,
    build_pi_promotion_actions,
    eval_cases_to_dict,
    evaluate_pi_promotion,
    intent_group_for_intent,
    load_pi_intent_eval_cases,
    merge_pi_intent_eval_cases,
    summarize_eval_case_sources,
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


def test_eval_case_from_mapping_parses_string_false_negative():
    case = PiIntentEvalCase.from_mapping(
        {
            "case_id": "weather_string_bool",
            "text": "rain later",
            "expected_intent": "weather",
            "intent_group": "weather",
            "route_class": "fallback",
            "negative": "false",
        }
    )

    assert case.negative is False


def test_summarize_eval_case_sources_counts_sources():
    cases = [
        PiIntentEvalCase("one", "rain later", "weather", "weather", "fallback", source="synthetic"),
        PiIntentEvalCase("two", "rain tonight", "weather", "weather", "fallback", source="intent_miss"),
        PiIntentEvalCase("three", "rain tomorrow", "weather", "weather", "fallback", source="synthetic"),
    ]

    assert summarize_eval_case_sources(cases) == {"intent_miss": 1, "synthetic": 2}


def test_load_pi_intent_eval_cases_from_jsonl(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '\n'.join(
            [
                '{"case_id":"weather_file","text":"rain later","expected_intent":"weather","intent_group":"weather","route_class":"fallback","source":"synthetic"}',
                '{"case_id":"casual_file","text":"I like the breakfast service","expected_intent":null,"intent_group":"chat","route_class":"fallback","source":"known_failure","negative":true}',
            ]
        )
        + '\n',
        encoding="utf-8",
    )

    cases = load_pi_intent_eval_cases(path)

    assert [case.case_id for case in cases] == ["weather_file", "casual_file"]
    assert cases[0].source == "synthetic"
    assert cases[1].negative is True


def test_load_pi_intent_eval_cases_from_json_object(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(
        '{"cases":[{"case_id":"timer_file","text":"timer for five","expected_intent":"timer_create","intent_group":"timers","route_class":"fallback","source":"intent_miss"}]}',
        encoding="utf-8",
    )

    cases = load_pi_intent_eval_cases(path)

    assert len(cases) == 1
    assert cases[0].case_id == "timer_file"
    assert cases[0].source == "intent_miss"


def test_load_pi_intent_eval_cases_rejects_duplicate_case_ids(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '\n'.join(
            [
                '{"case_id":"dup","text":"rain later","expected_intent":"weather","intent_group":"weather","route_class":"fallback"}',
                '{"case_id":"dup","text":"rain tonight","expected_intent":"weather","intent_group":"weather","route_class":"fallback"}',
            ]
        )
        + '\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate eval case_id"):
        load_pi_intent_eval_cases(path)


def test_merge_pi_intent_eval_cases_rejects_duplicate_case_ids():
    case = PiIntentEvalCase("same", "rain later", "weather", "weather", "fallback")

    with pytest.raises(ValueError, match="duplicate eval case_id"):
        merge_pi_intent_eval_cases([case], [case])


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


def test_summary_includes_operator_promotion_actions():
    samples = [
        PiRouteSample(
            case_id=f"weather_{index}",
            intent_group="weather",
            expected_intent="weather",
            zoe_intent="reminder_list",
            pi_intent="weather",
            zoe_latency_ms=500,
            pi_latency_ms=100,
            pi_confidence=0.9,
        )
        for index in range(30)
    ] + [
        PiRouteSample(
            case_id=f"reminder_{index}",
            intent_group="reminders",
            expected_intent="reminder_list",
            zoe_intent="weather",
            pi_intent="reminder_list",
            zoe_latency_ms=450,
            pi_latency_ms=90,
            pi_confidence=0.9,
        )
        for index in range(30)
    ]

    report = summarize_pi_promotion(samples, promoted_groups=["reminders"])

    assert report["promotion_actions"]["promote_groups"] == ["weather"]
    assert report["promotion_actions"] == {
        "promote_groups": ["weather"],
        "rollback_groups": [],
        "keep_promoted_groups": ["reminders"],
        "next_promoted_groups": ["reminders", "weather"],
        "env": {"ZOE_PI_INTENT_PROMOTED_GROUPS": "reminders,weather"},
        "requires_operator_apply": True,
    }


def test_summary_includes_operator_rollback_actions():
    samples = [
        PiRouteSample(
            case_id=f"weather_bad_{index}",
            intent_group="weather",
            expected_intent="weather",
            zoe_intent="weather",
            pi_intent="reminder_list",
            zoe_latency_ms=100,
            pi_latency_ms=500,
            pi_confidence=0.9,
        )
        for index in range(30)
    ] + [
        PiRouteSample(
            case_id=f"reminder_good_{index}",
            intent_group="reminders",
            expected_intent="reminder_list",
            zoe_intent="weather",
            pi_intent="reminder_list",
            zoe_latency_ms=450,
            pi_latency_ms=90,
            pi_confidence=0.9,
        )
        for index in range(30)
    ]

    report = summarize_pi_promotion(samples, promoted_groups=["weather", "reminders"])

    assert report["rollback_groups"] == ["weather"]
    assert report["promotion_actions"]["rollback_groups"] == ["weather"]
    assert report["promotion_actions"]["keep_promoted_groups"] == ["reminders"]
    assert report["promotion_actions"]["next_promoted_groups"] == ["reminders"]
    assert report["promotion_actions"]["env"] == {"ZOE_PI_INTENT_PROMOTED_GROUPS": "reminders"}


def test_build_pi_promotion_actions_reports_no_change_steady_state():
    actions = build_pi_promotion_actions(
        current_promoted_groups=["weather"],
        promotable_groups=["weather"],
        rollback_groups=[],
    )

    assert actions == {
        "promote_groups": [],
        "rollback_groups": [],
        "keep_promoted_groups": ["weather"],
        "next_promoted_groups": ["weather"],
        "env": {"ZOE_PI_INTENT_PROMOTED_GROUPS": "weather"},
        "requires_operator_apply": False,
    }


def test_build_pi_promotion_actions_rejects_conflicting_groups():
    with pytest.raises(ValueError, match="conflicting promotion action groups"):
        build_pi_promotion_actions(
            current_promoted_groups=[],
            promotable_groups=["weather"],
            rollback_groups=["weather"],
        )


def test_build_pi_promotion_actions_rejects_unknown_groups():
    with pytest.raises(ValueError, match="unknown promotion action groups"):
        build_pi_promotion_actions(
            current_promoted_groups=["weather"],
            promotable_groups=["device_control"],
            rollback_groups=[],
        )


def test_summary_rejects_unknown_promoted_groups():
    with pytest.raises(ValueError, match="unknown promoted_groups"):
        summarize_pi_promotion([], promoted_groups=["device_control"])
