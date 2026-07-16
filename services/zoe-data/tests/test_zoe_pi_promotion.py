from pathlib import Path

import pytest

from zoe_pi_promotion import (
    DEFAULT_PI_INTENT_EVAL_CASES,
    PiIntentEvalCase,
    PiPromotionPolicy,
    PiRouteSample,
    build_pi_candidate_wins,
    build_pi_failure_examples,
    build_pi_route_class_breakdown,
    build_pi_source_breakdown,
    build_pi_transport_breakdown,
    build_pi_promotion_actions,
    eval_cases_to_dict,
    evaluate_pi_promotion,
    intent_group_for_intent,
    load_pi_intent_eval_cases,
    merge_pi_intent_eval_cases,
    summarize_eval_case_sources,
    summarize_pi_promotion,
)

pytestmark = pytest.mark.ci_safe


def _sample(index, *, group="weather", zoe="reminder_list", pi="weather", expected="weather", zoe_ms=900, pi_ms=300, **kwargs):
    metadata = dict(kwargs.pop("metadata", {}) or {})
    metadata.setdefault("source", "intent_miss")
    return PiRouteSample(
        case_id=f"case_{index}",
        intent_group=group,
        expected_intent=expected,
        zoe_intent=zoe,
        pi_intent=pi,
        zoe_latency_ms=zoe_ms,
        pi_latency_ms=pi_ms,
        pi_confidence=0.91,
        metadata=metadata,
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


def test_committed_pi_intent_eval_cases_validate_requested_coverage():
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / "data" / "eval" / "pi_intent_eval_cases.jsonl"

    cases = load_pi_intent_eval_cases(path)
    by_id = {case.case_id: case for case in cases}
    new_synthetic_case_ids = {
        "eval_reminder_stretch",
        "eval_reminder_today",
        "eval_list_show_packing",
        "eval_list_add_tomatoes",
        "eval_list_remove_milk",
        "eval_list_remove_done",
        "eval_timer_tea",
        "eval_timer_laundry",
        "eval_calc_percent",
        "eval_calc_addition",
        "eval_briefing_evening",
        "eval_briefing_schedule",
        "eval_negative_timer_story",
        "eval_negative_shopping_memory",
    }

    assert new_synthetic_case_ids <= set(by_id)
    assert all(by_id[case_id].source == "synthetic" for case_id in new_synthetic_case_ids)
    assert {
        "reminder_create",
        "reminder_list",
        "list_add",
        "list_show",
        "list_remove",
        "timer_create",
        "calculate",
        "daily_briefing",
    } <= {case.expected_intent for case in cases if case.expected_intent}
    assert by_id["eval_negative_timer_story"].negative is True
    assert by_id["eval_negative_timer_story"].intent_group == "chat"
    assert by_id["eval_negative_shopping_memory"].negative is True
    assert by_id["eval_negative_shopping_memory"].intent_group == "chat"


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


def test_non_comparable_baseline_blocks_promotion():
    samples = [
        _sample(
            i,
            metadata={"baseline_comparable": False, "baseline_kind": "router_only_not_comparable"},
        )
        for i in range(10)
    ]
    policy = PiPromotionPolicy(min_samples=10)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "keep_shadow"
    assert "baseline_not_comparable" in decision.blockers


def test_non_comparable_baseline_does_not_roll_back_promoted_group_without_regression():
    samples = [
        _sample(
            i,
            metadata={"baseline_comparable": False, "baseline_kind": "router_only_not_comparable"},
        )
        for i in range(10)
    ]
    policy = PiPromotionPolicy(min_samples=10)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy, promoted=True)

    assert decision.state == "keep_shadow"
    assert "baseline_not_comparable" in decision.blockers


def test_policy_can_disable_comparable_baseline_requirement_for_smoke_data():
    samples = [_sample(i, metadata={"baseline_comparable": False}) for i in range(10)]
    policy = PiPromotionPolicy(min_samples=10, require_comparable_baseline=False)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "promote"
    assert "baseline_not_comparable" not in decision.blockers


def test_promotion_requires_five_percent_accuracy_win_and_latency_win():
    samples = [_sample(i) for i in range(10)]
    policy = PiPromotionPolicy(min_samples=10, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "promote"
    assert decision.pi_accuracy == 1.0
    assert decision.zoe_accuracy == 0.0
    assert decision.latency_delta_ms and decision.latency_delta_ms > 0
    assert decision.blockers == ()


def test_collapsed_mixed_source_case_counts_real_evidence():
    samples = [
        _sample(1, metadata={"source": "synthetic"}),
        _sample(1, metadata={"source": "intent_miss"}),
    ]
    policy = PiPromotionPolicy(min_samples=1, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)
    summary = build_pi_candidate_wins(samples, policy=policy)

    assert decision.state == "promote"
    assert "insufficient_real_source_samples" not in decision.blockers
    assert summary["details"][0]["real_source_sample_deficit"] == 0


def test_synthetic_only_evidence_blocks_promotion_until_real_sources_exist():
    samples = [_sample(i, metadata={"source": "synthetic"}) for i in range(10)]
    policy = PiPromotionPolicy(min_samples=10, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "keep_shadow"
    assert "insufficient_real_source_samples" in decision.blockers
    assert "insufficient_samples" not in decision.blockers


def test_policy_can_disable_real_source_gate_for_smoke_data():
    samples = [_sample(i, metadata={"source": "synthetic"}) for i in range(10)]
    policy = PiPromotionPolicy(min_samples=10, require_real_source_evidence=False)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "promote"
    assert "insufficient_real_source_samples" not in decision.blockers


def test_promotion_counts_unique_cases_not_repeated_observations():
    samples = [_sample(1) for _ in range(10)]
    policy = PiPromotionPolicy(min_samples=2, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "keep_shadow"
    assert decision.sample_count == 1
    assert decision.pi_accuracy == 1.0
    assert decision.zoe_accuracy == 0.0
    assert "insufficient_samples" in decision.blockers


def test_promotion_accuracy_uses_unique_case_evidence_not_duplicate_observations():
    samples = []
    for index in range(30):
        pi_intent = "weather" if index < 15 else "reminder_list"
        zoe_intent = "weather" if index < 14 else "reminder_list"
        samples.append(_sample(index, pi=pi_intent, zoe=zoe_intent))
    samples.extend([_sample(14), _sample(14)])
    policy = PiPromotionPolicy(min_samples=30, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "keep_shadow"
    assert decision.sample_count == 30
    assert decision.pi_accuracy == 0.5
    assert decision.zoe_accuracy == 14 / 30
    assert decision.accuracy_delta == pytest.approx(1 / 30)
    assert "accuracy_delta_below_threshold" in decision.blockers


def test_candidate_wins_separate_speed_accuracy_evidence_from_promotion_readiness():
    samples = [_sample(1) for _ in range(10)]
    policy = PiPromotionPolicy(min_samples=2, accuracy_win_margin=0.05)

    summary = build_pi_candidate_wins(samples, policy=policy)

    assert summary["groups"] == ["weather"]
    assert summary["blocked_groups"] == ["weather"]
    assert summary["promotion_ready_groups"] == []
    detail = summary["details"][0]
    assert detail["status"] == "needs_more_evidence"
    assert detail["observation_count"] == 10
    assert detail["unique_case_count"] == 1
    assert "sample_count" not in detail
    assert detail["sample_deficit"] == 1
    assert detail["unique_case_deficit"] == 1
    assert detail["promotion_blockers"] == ["insufficient_samples", "insufficient_real_source_samples"]
    assert detail["real_source_sample_deficit"] == 1


def test_promotion_blocks_when_accuracy_delta_is_too_small():
    samples = [_sample(i, zoe="weather", pi="weather") for i in range(10)]
    policy = PiPromotionPolicy(min_samples=10, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "keep_shadow"
    assert "accuracy_delta_below_threshold" in decision.blockers


def test_promotion_blocks_when_pi_absolute_accuracy_is_too_low():
    samples = []
    for index in range(30):
        pi_intent = "weather" if index < 18 else "reminder_list"
        zoe_intent = "weather" if index < 3 else "reminder_list"
        samples.append(_sample(index, pi=pi_intent, zoe=zoe_intent))
    policy = PiPromotionPolicy(min_samples=30, accuracy_win_margin=0.05, min_pi_accuracy=0.90)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "keep_shadow"
    assert decision.pi_accuracy == 0.6
    assert decision.zoe_accuracy == 0.1
    assert decision.accuracy_delta == 0.5
    assert "pi_accuracy_below_threshold" in decision.blockers
    assert "accuracy_delta_below_threshold" not in decision.blockers

    summary = build_pi_candidate_wins(samples, policy=policy)
    assert summary["groups"] == []
    assert summary["promotion_ready_groups"] == []


def test_promotion_blocks_when_pi_is_not_faster_than_zoe():
    samples = [_sample(i, zoe_ms=250, pi_ms=450) for i in range(10)]
    policy = PiPromotionPolicy(min_samples=10, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "keep_shadow"
    assert "latency_not_faster_than_zoe" in decision.blockers


def test_promotion_blocks_when_pi_loses_a_fast_baseline_lane():
    samples = [
        _sample(
            index,
            zoe_ms=6000,
            pi_ms=2500,
            metadata={"baseline_comparable": True, "baseline_kind": "zoe_agent_fallback_baseline"},
        )
        for index in range(15)
    ]
    samples.extend(
        _sample(
            index + 100,
            zoe_ms=10,
            pi_ms=2500,
            metadata={"baseline_comparable": True, "baseline_kind": "router"},
        )
        for index in range(15)
    )
    policy = PiPromotionPolicy(min_samples=30, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "keep_shadow"
    assert decision.latency_delta_ms and decision.latency_delta_ms > 0
    assert "latency_not_faster_than_zoe" not in decision.blockers
    assert "baseline_lane_not_faster_than_zoe" in decision.blockers
    assert decision.baseline_lane_latency["fallback:zoe_agent_fallback_baseline"]["latency_delta_ms"] > 0
    assert decision.baseline_lane_latency["fallback:router"]["latency_delta_ms"] < 0


def test_promoted_group_rolls_back_when_pi_loses_a_fast_baseline_lane():
    samples = [
        _sample(
            index,
            zoe_ms=6000,
            pi_ms=2500,
            metadata={"baseline_comparable": True, "baseline_kind": "zoe_agent_fallback_baseline"},
        )
        for index in range(15)
    ]
    samples.extend(
        _sample(
            index + 100,
            zoe_ms=10,
            pi_ms=2500,
            metadata={"baseline_comparable": True, "baseline_kind": "router"},
        )
        for index in range(15)
    )
    policy = PiPromotionPolicy(min_samples=30, accuracy_win_margin=0.05)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy, promoted=True)

    assert decision.state == "rollback"
    assert "baseline_lane_not_faster_than_zoe" in decision.blockers


def test_policy_can_disable_baseline_lane_gate_for_smoke_data():
    samples = [
        _sample(
            index,
            zoe_ms=6000,
            pi_ms=2500,
            metadata={"baseline_comparable": True, "baseline_kind": "zoe_agent_fallback_baseline"},
        )
        for index in range(15)
    ]
    samples.extend(
        _sample(
            index + 100,
            zoe_ms=10,
            pi_ms=2500,
            metadata={"baseline_comparable": True, "baseline_kind": "router"},
        )
        for index in range(15)
    )
    policy = PiPromotionPolicy(
        min_samples=30,
        accuracy_win_margin=0.05,
        require_baseline_lane_latency_win=False,
    )

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy)

    assert decision.state == "promote"
    assert "baseline_lane_not_faster_than_zoe" not in decision.blockers


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


def test_promoted_group_rolls_back_when_pi_absolute_accuracy_regresses():
    samples = []
    for index in range(30):
        pi_intent = "weather" if index < 18 else "reminder_list"
        zoe_intent = "weather" if index < 3 else "reminder_list"
        samples.append(_sample(index, pi=pi_intent, zoe=zoe_intent))
    policy = PiPromotionPolicy(min_samples=30, accuracy_win_margin=0.05, min_pi_accuracy=0.90)

    decision = evaluate_pi_promotion(samples, intent_group="weather", policy=policy, promoted=True)

    assert decision.state == "rollback"
    assert "pi_accuracy_below_threshold" in decision.blockers


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


def test_failure_examples_prioritize_review_and_timeout_signals():
    examples = build_pi_failure_examples(
        [
            _sample(1, pi="reminder_list", pi_ms=200, metadata={"source": "synthetic"}),
            _sample(2, pi=None, pi_ms=900, timed_out=True),
            _sample(3, user_corrected=True, pi_ms=150),
            _sample(4, rollback_blocked=True, pi_ms=100),
            _sample(5, pi="weather"),
        ],
        limit=3,
    )

    assert [item["case_id"] for item in examples] == ["case_4", "case_3", "case_2"]
    assert examples[0]["reasons"] == ["rollback_blocked"]
    assert examples[1]["reasons"] == ["user_corrected"]
    assert examples[2]["reasons"] == ["timed_out", "pi_wrong_intent"]
    assert examples[2]["pi_intent"] is None
    assert "text" not in examples[0]


def test_failure_examples_separate_timeout_from_wrong_intent():
    examples = build_pi_failure_examples([_sample(1, pi="weather", timed_out=True)])

    assert examples[0]["reasons"] == ["timed_out"]


def test_route_class_breakdown_compares_baselines_independently():
    samples = [
        _sample(1, route_class="deterministic", zoe="weather", pi="weather", zoe_ms=40, pi_ms=80),
        _sample(2, route_class="fallback", zoe="reminder_list", pi="weather", zoe_ms=900, pi_ms=300),
        _sample(3, route_class="fallback", zoe="reminder_list", pi=None, zoe_ms=800, pi_ms=1200, timed_out=True),
    ]

    breakdown = build_pi_route_class_breakdown(samples)

    assert sorted(breakdown) == ["deterministic", "extraction_failed", "fallback"]
    assert breakdown["deterministic"] == {
        "sample_count": 1,
        "zoe_accuracy": 1.0,
        "pi_accuracy": 1.0,
        "accuracy_delta": 0.0,
        "zoe_p95_latency_ms": 40.0,
        "pi_p95_latency_ms": 80.0,
        "latency_delta_ms": -40.0,
        "timeout_rate": 0.0,
        "correction_rate": 0.0,
    }
    assert breakdown["fallback"]["sample_count"] == 2
    assert breakdown["fallback"]["zoe_accuracy"] == 0.0
    assert breakdown["fallback"]["pi_accuracy"] == 0.5
    assert breakdown["fallback"]["accuracy_delta"] == 0.5
    assert breakdown["fallback"]["zoe_p95_latency_ms"] == 895.0
    assert breakdown["fallback"]["pi_p95_latency_ms"] == 1155.0
    assert breakdown["fallback"]["latency_delta_ms"] == -260.0
    assert breakdown["fallback"]["timeout_rate"] == 0.5
    assert breakdown["extraction_failed"]["sample_count"] == 0
    assert breakdown["extraction_failed"]["zoe_p95_latency_ms"] is None


def test_source_breakdown_counts_real_synthetic_and_unknown_sources():
    samples = [
        _sample(1, metadata={"source": "synthetic"}),
        _sample(2, metadata={"source": "intent_miss"}),
        _sample(3, metadata={"source": "unknown"}),
        _sample(4, group="timers", expected="timer_create", zoe="weather", pi="timer_create", metadata={"source": "pi_intent_shadow"}),
    ]

    breakdown = build_pi_source_breakdown(samples, policy=PiPromotionPolicy(min_samples=2))

    assert breakdown["sample_count"] == 4
    assert breakdown["source_counts"] == {"intent_miss": 1, "pi_intent_shadow": 1, "synthetic": 1, "unknown": 1}
    assert breakdown["real_source_sample_count"] == 2
    assert breakdown["synthetic_sample_count"] == 1
    assert breakdown["unknown_source_sample_count"] == 1
    assert breakdown["source_counts_by_group"]["weather"] == {"intent_miss": 1, "synthetic": 1, "unknown": 1}
    assert breakdown["source_counts_by_group"]["timers"] == {"pi_intent_shadow": 1}
    assert breakdown["real_source_sample_count_by_group"]["weather"] == 1
    assert breakdown["real_source_sample_count_by_group"]["timers"] == 1
    assert breakdown["real_source_sample_deficit_by_group"]["weather"] == 1
    assert breakdown["real_source_sample_deficit_by_group"]["timers"] == 1
    assert breakdown["real_source_ready_groups"] == []


def test_transport_breakdown_separates_print_and_rpc_latency():
    samples = [
        _sample(1, pi_transport="print", zoe_ms=900, pi_ms=700),
        _sample(2, pi_transport="rpc", zoe_ms=900, pi_ms=300),
        _sample(
            3,
            pi_transport="rpc",
            zoe="weather",
            pi="weather",
            zoe_ms=50,
            pi_ms=1200,
            timed_out=True,
            user_corrected=True,
        ),
    ]

    breakdown = build_pi_transport_breakdown(samples)

    assert sorted(breakdown) == ["print", "rpc"]
    assert breakdown["print"] == {
        "sample_count": 1,
        "zoe_accuracy": 0.0,
        "pi_accuracy": 1.0,
        "accuracy_delta": 1.0,
        "zoe_p95_latency_ms": 900.0,
        "pi_p95_latency_ms": 700.0,
        "latency_delta_ms": 200.0,
        "timeout_rate": 0.0,
        "correction_rate": 0.0,
    }
    assert breakdown["rpc"]["sample_count"] == 2
    assert breakdown["rpc"]["zoe_accuracy"] == 0.5
    assert breakdown["rpc"]["pi_accuracy"] == 0.5
    assert breakdown["rpc"]["accuracy_delta"] == 0.0
    assert breakdown["rpc"]["zoe_p95_latency_ms"] == 857.5
    assert breakdown["rpc"]["pi_p95_latency_ms"] == 1155.0
    assert breakdown["rpc"]["latency_delta_ms"] == -297.5
    assert breakdown["rpc"]["timeout_rate"] == 0.5
    assert breakdown["rpc"]["correction_rate"] == 0.5


def test_summary_lists_promotable_groups():
    samples = [_sample(i) for i in range(10)]
    report = summarize_pi_promotion(samples, policy=PiPromotionPolicy(min_samples=10))

    assert "weather" in report["promotable_groups"]
    assert report["promoted_groups"] == []
    assert report["sample_count"] == 10
    assert report["unique_case_count"] == 10
    assert report["candidate_wins"]["promotion_ready_groups"] == ["weather"]
    assert report["policy"]["accuracy_win_margin"] == 0.05
    assert report["policy"]["min_pi_accuracy"] == 0.90
    assert report["route_class_breakdown"]["fallback"]["sample_count"] == 10
    assert report["route_class_breakdown"]["fallback"]["latency_delta_ms"] > 0
    assert report["transport_breakdown"]["rpc"]["sample_count"] == 10
    assert report["transport_breakdown"]["rpc"]["latency_delta_ms"] > 0
    assert report["source_breakdown"]["unknown_source_sample_count"] == 0
    assert report["source_breakdown"]["real_source_sample_deficit_by_group"]["weather"] == 0


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
            metadata={"source": "intent_miss"},
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
            metadata={"source": "intent_miss"},
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
