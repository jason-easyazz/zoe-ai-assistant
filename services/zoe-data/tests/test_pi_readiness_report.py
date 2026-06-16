import json

from pi_readiness_report import pi_readiness_report


def _env(tmp_path, shadow_path):
    return {
        "ZOE_WAKE_ACK_PHRASES": "Yes Jason.",
        "ZOE_PROCESSING_ACK_PHRASES": "Let me check.",
        "ZOE_PI_INTENT_ENABLED": "false",
        "ZOE_PI_INTENT_SHADOW_ENABLED": "true",
        "ZOE_PI_INTENT_TRANSPORT": "rpc",
        "ZOE_PI_INTENT_SHADOW_PATH": str(shadow_path),
        "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(tmp_path / "labels.jsonl"),
    }


def _winning_weather_row(index):
    return {
        "text_hash": f"weather_{index}",
        "outcome_label": "weather",
        "zoe_intent": None,
        "pi_intent": "weather",
        "zoe_latency_ms": 12000,
        "pi_latency_ms": 3000,
        "pi_confidence": 0.9,
        "pi_transport": "rpc",
        "route_class": "fallback",
        "agreement": False,
        "timed_out": False,
        "pi_no_result": False,
        "baseline_kind": "operator_fallback_override",
        "baseline_comparable": True,
        "router_latency_ms": 5,
        "source": "pi_intent_shadow",
    }


def test_readiness_report_collects_more_evidence_for_candidate_wins(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text(
        "".join(json.dumps(_winning_weather_row(index)) + "\n" for index in range(3)),
        encoding="utf-8",
    )

    report = pi_readiness_report(_env(tmp_path, shadow_path))

    assert report["report_kind"] == "zoe_pi_readiness_report"
    assert report["state"] == "collect_more_evidence"
    assert report["hybrid"]["ready"] is True
    assert report["summary"]["candidate_win_groups"] == ["weather"]
    assert report["summary"]["promotion_ready_groups"] == []
    assert report["evidence"]["labeled_sample_count"] == 3
    assert report["candidates"] == [
        {
            "intent_group": "weather",
            "status": "needs_more_evidence",
            "unique_case_count": 3,
            "unique_case_deficit": 27,
            "sample_deficit": 27,
            "accuracy_delta": 1.0,
            "latency_delta_ms": 9000.0,
            "pi_p95_latency_ms": 3000.0,
            "zoe_p95_latency_ms": 12000.0,
            "promotion_blockers": ["insufficient_samples"],
        }
    ]
    assert {
        "kind": "collect_labeled_evidence",
        "priority": "p1",
        "intent_group": "weather",
        "needed_unique_cases": 27,
        "detail": "Collect and label 27 more unique weather cases before promotion.",
    } in report["next_actions"]


def test_readiness_report_surfaces_operator_apply_when_promotion_ready(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text(
        "".join(json.dumps(_winning_weather_row(index)) + "\n" for index in range(30)),
        encoding="utf-8",
    )

    report = pi_readiness_report(_env(tmp_path, shadow_path))

    assert report["state"] == "promotion_apply_ready"
    assert report["summary"]["promotion_ready_groups"] == ["weather"]
    assert report["summary"]["requires_operator_apply"] is True
    assert report["promotion_actions"]["promote_groups"] == ["weather"]
    assert report["promotion_actions"]["env"] == {"ZOE_PI_INTENT_PROMOTED_GROUPS": "weather"}
    assert report["next_actions"][0] == {
        "kind": "apply_promotion",
        "priority": "p1",
        "detail": "Operator can apply these low-risk Pi groups after reviewing the report.",
        "groups": ["weather"],
        "env": {"ZOE_PI_INTENT_PROMOTED_GROUPS": "weather"},
    }


def test_readiness_report_treats_enabled_pi_without_promotions_as_shadow_mode(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text(
        "".join(json.dumps(_winning_weather_row(index)) + "\n" for index in range(30)),
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_INTENT_ENABLED"] = "true"
    env["ZOE_PI_INTENT_PROMOTED_GROUPS"] = ""

    report = pi_readiness_report(env)

    assert report["state"] == "promotion_apply_ready"
    assert report["hybrid"]["mode"] == "shadow_buffer"
    assert report["hybrid"]["ready"] is True
    assert "pi_execution_enabled_without_promoted_groups" not in report["hybrid"]["blockers"]
    assert "pi_classifier_enabled_without_promoted_groups_runs_shadow_only" in report["hybrid"]["warnings"]
    assert report["promotion_actions"]["promote_groups"] == ["weather"]


def test_readiness_report_requests_comparable_baseline_for_router_only_shadow_data(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    row = _winning_weather_row(1)
    row["zoe_latency_ms"] = 10
    row["baseline_kind"] = "router_only_not_comparable"
    row["baseline_comparable"] = False
    shadow_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    report = pi_readiness_report(_env(tmp_path, shadow_path))

    assert report["state"] == "measure_comparable_baseline"
    assert report["blocked_decisions"] == [
        {
            "intent_group": "weather",
            "sample_count": 1,
            "blockers": ["insufficient_samples", "latency_not_faster_than_zoe", "baseline_not_comparable"],
            "accuracy_delta": 1.0,
            "latency_delta_ms": -2990.0,
        }
    ]
    assert {
        "kind": "measure_comparable_baseline",
        "priority": "p1",
        "detail": "Measure Pi against Zoe Agent or operator fallback latency before judging promotion.",
        "groups": ["weather"],
    } in report["next_actions"]
