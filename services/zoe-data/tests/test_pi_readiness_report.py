import pytest
import json

from pi_readiness_report import pi_readiness_report

pytestmark = pytest.mark.ci_safe


def _env(tmp_path, shadow_path):
    return {
        "ZOE_WAKE_ACK_PHRASES": "Yes Jason.",
        "ZOE_PROCESSING_ACK_PHRASES": "Let me check.",
        "ZOE_PI_INTENT_ENABLED": "false",
        "ZOE_PI_INTENT_SHADOW_ENABLED": "true",
        "ZOE_PI_INTENT_TRANSPORT": "rpc",
        "ZOE_PI_INTENT_SHADOW_PATH": str(shadow_path),
        "ZOE_PI_INTENT_SHADOW_LABELS_PATH": str(tmp_path / "labels.jsonl"),
        "ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH": str(tmp_path / "production.jsonl"),
        "ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH": str(tmp_path / "production-labels.jsonl"),
        "ZOE_PI_PROMOTION_EVAL_REPORT_PATH": str(tmp_path / "missing-eval-report.json"),
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
            "promotion_blockers": ["insufficient_samples", "insufficient_real_source_samples"],
            "real_source_sample_deficit": 27,
        }
    ]
    assert {
        "kind": "collect_labeled_evidence",
        "priority": "p1",
        "intent_group": "weather",
        "needed_unique_cases": 27,
        "needed_real_source_cases": 27,
        "detail": "Collect and label 27 more unique weather cases (27 must be real/log-derived) before promotion.",
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


def test_readiness_real_source_only_deficit_has_clear_detail():
    from pi_readiness_report import _evidence_collection_actions

    actions = _evidence_collection_actions(
        {
            "candidate_wins": {
                "details": [
                    {
                        "intent_group": "weather",
                        "status": "needs_more_evidence",
                        "unique_case_deficit": 0,
                        "sample_deficit": 0,
                        "real_source_sample_deficit": 5,
                        "promotion_blockers": ["insufficient_real_source_samples"],
                    }
                ]
            }
        }
    )

    assert actions == [
        {
            "kind": "collect_labeled_evidence",
            "priority": "p1",
            "intent_group": "weather",
            "needed_unique_cases": 0,
            "detail": "Collect 5 real/log-derived weather samples (pi_intent_shadow, intent_miss, chat_log, etc.) before promotion.",
            "needed_real_source_cases": 5,
        }
    ]


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
            "blockers": [
                "insufficient_samples",
                "latency_not_faster_than_zoe",
                "baseline_not_comparable",
                "insufficient_real_source_samples",
            ],
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


def test_readiness_report_uses_persisted_eval_benchmark_for_candidate_wins(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text("", encoding="utf-8")
    eval_report_path = tmp_path / "pi-eval.json"
    eval_report_path.write_text(
        json.dumps(
            {
                "promotion_report": {
                    "candidate_wins": {
                        "groups": ["weather"],
                        "details": [
                            {
                                "intent_group": "weather",
                                "status": "needs_more_evidence",
                                "unique_case_count": 3,
                                "unique_case_deficit": 27,
                                "sample_deficit": 27,
                                "real_source_sample_deficit": 27,
                                "accuracy_delta": 1.0,
                                "latency_delta_ms": 5000.0,
                                "pi_p95_latency_ms": 3000.0,
                                "zoe_p95_latency_ms": 8000.0,
                                "promotion_blockers": ["insufficient_samples", "insufficient_real_source_samples"],
                            }
                        ],
                    },
                    "decisions": [],
                    "promotion_actions": {},
                },
                "readiness": {"state": "collect_more_evidence"},
            }
        ),
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_PROMOTION_EVAL_REPORT_PATH"] = str(eval_report_path)

    report = pi_readiness_report(env)

    assert report["state"] == "collect_more_evidence"
    assert report["benchmark"]["loaded"] is True
    assert report["summary"]["benchmark_candidate_win_groups"] == ["weather"]
    assert report["evidence"]["benchmark_loaded"] is True
    assert report["candidates"][0]["intent_group"] == "weather"
    assert {
        "kind": "collect_labeled_evidence",
        "priority": "p1",
        "intent_group": "weather",
        "needed_unique_cases": 27,
        "needed_real_source_cases": 27,
        "detail": "Collect and label 27 more unique weather cases (27 must be real/log-derived) before promotion.",
        "evidence_source": "benchmark",
    } in report["next_actions"]




def test_readiness_report_reports_operator_hybrid_as_operational(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text("", encoding="utf-8")
    eval_report_path = tmp_path / "pi-eval.json"
    eval_report_path.write_text(
        json.dumps(
            {
                "promotion_report": {
                    "candidate_wins": {
                        "groups": ["weather"],
                        "details": [
                            {
                                "intent_group": "weather",
                                "status": "needs_more_evidence",
                                "unique_case_deficit": 27,
                                "sample_deficit": 27,
                                "real_source_sample_deficit": 27,
                                "accuracy_delta": 1.0,
                                "latency_delta_ms": 5000.0,
                                "pi_p95_latency_ms": 3000.0,
                                "zoe_p95_latency_ms": 8000.0,
                                "promotion_blockers": ["insufficient_samples", "insufficient_real_source_samples"],
                            }
                        ],
                    },
                    "decisions": [],
                    "promotion_actions": {},
                }
            }
        ),
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_PROMOTION_EVAL_REPORT_PATH"] = str(eval_report_path)
    env["ZOE_PI_HYBRID_PRODUCTION_ENABLED"] = "true"
    env["ZOE_PI_HYBRID_PRODUCTION_GROUPS"] = "weather,daily_briefing"

    report = pi_readiness_report(env)

    assert report["state"] == "production_hybrid_operational"
    assert report["production_hybrid"] == {
        "enabled": True,
        "groups": ["weather", "daily_briefing"],
        "ignored_groups": [],
        "operational": True,
        "route": "pi_intent_buffer_plus_zoe_safe_fulfillment",
    }
    assert report["summary"]["production_hybrid_operational"] is True
    assert report["summary"]["production_hybrid_groups"] == ["weather", "daily_briefing"]
    assert report["next_actions"][0] == {
        "kind": "monitor_production_hybrid",
        "priority": "p1",
        "detail": "Production Pi hybrid is live for operator-approved groups; keep formal default-route promotion evidence separate.",
        "groups": ["weather", "daily_briefing"],
    }
    assert {
        "kind": "collect_labeled_evidence",
        "priority": "p2",
        "intent_group": "weather",
        "needed_unique_cases": 27,
        "needed_real_source_cases": 27,
        "detail": "Collect and label 27 more unique weather cases (27 must be real/log-derived) before formal default-route promotion.",
        "evidence_source": "benchmark",
    } in report["next_actions"]


def test_readiness_report_flags_unsupported_operator_hybrid_group(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text("", encoding="utf-8")
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_HYBRID_PRODUCTION_ENABLED"] = "true"
    env["ZOE_PI_HYBRID_PRODUCTION_GROUPS"] = "weather,device_control"

    report = pi_readiness_report(env)

    assert report["state"] == "configuration_blocked"
    assert report["production_hybrid"] == {
        "enabled": True,
        "groups": ["weather"],
        "ignored_groups": ["device_control"],
        "operational": False,
        "route": "pi_intent_buffer_plus_zoe_safe_fulfillment",
    }
    assert report["next_actions"][0] == {
        "kind": "fix_configuration",
        "priority": "p0",
        "detail": "Remove unsupported Pi hybrid production groups from ZOE_PI_HYBRID_PRODUCTION_GROUPS.",
        "groups": ["device_control"],
        "env": {"ZOE_PI_HYBRID_PRODUCTION_GROUPS": "weather"},
    }


def test_readiness_report_ignores_stale_operator_groups_when_hybrid_disabled(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text(
        "".join(json.dumps(_winning_weather_row(index)) + "\n" for index in range(3)),
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_HYBRID_PRODUCTION_ENABLED"] = "false"
    env["ZOE_PI_HYBRID_PRODUCTION_GROUPS"] = "weather,device_control"

    report = pi_readiness_report(env)

    assert report["state"] == "collect_more_evidence"
    assert report["production_hybrid"]["enabled"] is False
    assert report["production_hybrid"]["ignored_groups"] == ["device_control"]
    assert all(
        not (
            action.get("kind") == "fix_configuration"
            and action.get("groups") == ["device_control"]
        )
        for action in report["next_actions"]
    )


def test_readiness_report_deduplicates_shadow_and_benchmark_collection_actions(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text(json.dumps(_winning_weather_row(1)) + "\n", encoding="utf-8")
    eval_report_path = tmp_path / "pi-eval.json"
    eval_report_path.write_text(
        json.dumps(
            {
                "promotion_report": {
                    "candidate_wins": {
                        "groups": ["weather"],
                        "details": [
                            {
                                "intent_group": "weather",
                                "status": "needs_more_evidence",
                                "unique_case_deficit": 28,
                                "sample_deficit": 28,
                                "real_source_sample_deficit": 30,
                                "accuracy_delta": 1.0,
                                "latency_delta_ms": 2500.0,
                                "pi_p95_latency_ms": 3000.0,
                                "zoe_p95_latency_ms": 5500.0,
                                "promotion_blockers": ["insufficient_samples", "insufficient_real_source_samples"],
                            }
                        ],
                    },
                    "decisions": [],
                    "promotion_actions": {},
                }
            }
        ),
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_PROMOTION_EVAL_REPORT_PATH"] = str(eval_report_path)

    report = pi_readiness_report(env)

    weather_actions = [
        action
        for action in report["next_actions"]
        if action.get("kind") == "collect_labeled_evidence" and action.get("intent_group") == "weather"
    ]
    assert len(weather_actions) == 1
    assert "evidence_source" not in weather_actions[0]


def test_readiness_report_keeps_baseline_action_for_groups_not_covered_by_benchmark(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    row = _winning_weather_row(1)
    row["zoe_latency_ms"] = 10
    row["baseline_kind"] = "router_only_not_comparable"
    row["baseline_comparable"] = False
    shadow_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    eval_report_path = tmp_path / "pi-eval.json"
    eval_report_path.write_text(
        json.dumps(
            {
                "promotion_report": {
                    "candidate_wins": {
                        "groups": ["timers"],
                        "details": [
                            {
                                "intent_group": "timers",
                                "status": "needs_more_evidence",
                                "unique_case_deficit": 29,
                                "sample_deficit": 29,
                                "real_source_sample_deficit": 30,
                                "accuracy_delta": 1.0,
                                "latency_delta_ms": 1800.0,
                                "pi_p95_latency_ms": 2400.0,
                                "zoe_p95_latency_ms": 4200.0,
                                "promotion_blockers": ["insufficient_samples", "insufficient_real_source_samples"],
                            }
                        ],
                    },
                    "decisions": [],
                    "promotion_actions": {},
                }
            }
        ),
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_PROMOTION_EVAL_REPORT_PATH"] = str(eval_report_path)

    report = pi_readiness_report(env)

    assert report["state"] == "collect_more_evidence"
    assert {
        "kind": "measure_comparable_baseline",
        "priority": "p1",
        "detail": "Measure Pi against Zoe Agent or operator fallback latency before judging promotion.",
        "groups": ["weather"],
    } in report["next_actions"]


def test_readiness_report_missing_eval_benchmark_is_nonblocking(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text("", encoding="utf-8")
    env = _env(tmp_path, shadow_path)

    report = pi_readiness_report(env)

    assert report["benchmark"]["loaded"] is False
    assert report["evidence"]["benchmark_loaded"] is False
    assert report["state"] == "shadow_collecting"


def test_readiness_report_surfaces_production_evidence_summary(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text("", encoding="utf-8")
    production_path = tmp_path / "production.jsonl"
    production_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "ts": 1.0,
                    "text_hash": None,
                    "source": "pi_hybrid_production",
                    "accepted": True,
                    "reason": "accepted",
                    "intent": "weather",
                    "intent_group": "weather",
                    "pi_intent": "weather",
                    "pi_latency_ms": 4100.0,
                    "safe_fulfillment_latency_ms": 900.0,
                    "production_route_change": True,
                    "text_preview": "will it rain later",
                    "outcome_label": None,
                },
                {
                    "ts": 2.0,
                    "text_hash": None,
                    "source": "pi_hybrid_production",
                    "accepted": False,
                    "reason": "timeout",
                    "intent_group": "weather",
                    "pi_latency_ms": 8000.0,
                    "safe_fulfillment_latency_ms": None,
                    "production_route_change": False,
                    "text_preview": "weather tomorrow",
                    "outcome_label": "weather_timeout",
                },
                {
                    "ts": 3.0,
                    "text_hash": None,
                    "source": "pi_hybrid_production",
                    "accepted": True,
                    "reason": "accepted",
                    "intent": "daily_briefing",
                    "intent_group": "daily_briefing",
                    "pi_intent": "daily_briefing",
                    "pi_latency_ms": 2200.0,
                    "safe_fulfillment_latency_ms": 1100.0,
                    "production_route_change": True,
                    "text_preview": "give me my daily briefing",
                    "outcome_label": None,
                },
            ]
        )
        + "\nnot-json\n",
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED"] = "true"
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH"] = str(production_path)

    report = pi_readiness_report(env)

    assert report["summary"]["production_record_count"] == 3
    assert report["summary"]["production_accepted_count"] == 2
    assert report["summary"]["production_unlabeled_count"] == 2
    assert report["summary"]["production_groups"] == ["daily_briefing", "weather"]
    assert report["summary"]["production_pi_p95_latency_ms_by_group"] == {"daily_briefing": 2200.0, "weather": 8000.0}
    assert report["summary"]["production_safe_fulfillment_p95_latency_ms_by_group"] == {
        "daily_briefing": 1100.0,
        "weather": 900.0,
    }
    assert report["evidence"]["production_record_count_by_group"] == {"daily_briefing": 1, "weather": 2}
    assert report["evidence"]["production_pi_p95_latency_ms_by_group"] == {"daily_briefing": 2200.0, "weather": 8000.0}
    assert report["evidence"]["production_safe_fulfillment_p95_latency_ms_by_group"] == {
        "daily_briefing": 1100.0,
        "weather": 900.0,
    }
    assert report["evidence"]["production_sample_count"] == 0
    assert report["production_evidence"]["promotion_report"]["sample_count"] == 0
    production_evidence = dict(report["production_evidence"])
    production_evidence.pop("promotion_report")
    assert production_evidence == {
        "enabled": True,
        "loaded": True,
        "path": str(production_path),
        "labels_path": str(tmp_path / "production-labels.jsonl"),
        "label_count": 0,
        "invalid_lines": 1,
        "record_count": 3,
        "accepted_count": 2,
        "rejected_count": 1,
        "route_change_count": 2,
        "unlabeled_count": 2,
        "by_group": {
            "daily_briefing": {
                "record_count": 1,
                "accepted_count": 1,
                "rejected_count": 0,
                "route_change_count": 1,
                "unlabeled_count": 1,
                "pi_p95_latency_ms": 2200.0,
                "safe_fulfillment_p95_latency_ms": 1100.0,
            },
            "weather": {
                "record_count": 2,
                "accepted_count": 1,
                "rejected_count": 1,
                "route_change_count": 1,
                "unlabeled_count": 1,
                "pi_p95_latency_ms": 8000.0,
                "safe_fulfillment_p95_latency_ms": 900.0,
            },
        },
        "recent": [
            {
                "ts": 1.0,
                "text_hash": None,
                "intent_group": "weather",
                "accepted": True,
                "reason": "accepted",
                "intent": "weather",
                "pi_intent": "weather",
                "pi_latency_ms": 4100.0,
                "safe_fulfillment_latency_ms": 900.0,
                "production_route_change": True,
                "outcome_label": None,
                "outcome_label_source": None,
                "text_preview": "will it rain later",
            },
            {
                "ts": 2.0,
                "text_hash": None,
                "intent_group": "weather",
                "accepted": False,
                "reason": "timeout",
                "intent": None,
                "pi_intent": None,
                "pi_latency_ms": 8000.0,
                "safe_fulfillment_latency_ms": None,
                "production_route_change": False,
                "outcome_label": "weather_timeout",
                "outcome_label_source": None,
                "text_preview": "weather tomorrow",
            },
            {
                "ts": 3.0,
                "text_hash": None,
                "intent_group": "daily_briefing",
                "accepted": True,
                "reason": "accepted",
                "intent": "daily_briefing",
                "pi_intent": "daily_briefing",
                "pi_latency_ms": 2200.0,
                "safe_fulfillment_latency_ms": 1100.0,
                "production_route_change": True,
                "outcome_label": None,
                "outcome_label_source": None,
                "text_preview": "give me my daily briefing",
            },
        ],
    }
    assert {
        "kind": "label_production_evidence",
        "priority": "p1",
        "detail": "Label 2 Pi hybrid production evidence records before promotion scoring.",
        "path": str(production_path),
        "labels_path": str(tmp_path / "production-labels.jsonl"),
        "groups": ["daily_briefing", "weather"],
    } in report["next_actions"]


def test_readiness_report_applies_production_label_sidecar(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text("", encoding="utf-8")
    production_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    production_path.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                {
                    "ts": 1.0,
                    "source": "pi_hybrid_production",
                    "text_hash": "weather-hash",
                    "accepted": True,
                    "reason": "accepted",
                    "intent": "weather",
                    "intent_group": "weather",
                    "pi_intent": "weather",
                    "pi_latency_ms": 4100.0,
                    "safe_fulfillment_latency_ms": 900.0,
                    "production_route_change": True,
                    "text_preview": "will it rain later",
                    "outcome_label": None,
                },
                {
                    "ts": 2.0,
                    "source": "pi_hybrid_production",
                    "text_hash": "briefing-hash",
                    "accepted": True,
                    "reason": "accepted",
                    "intent": "daily_briefing",
                    "intent_group": "daily_briefing",
                    "pi_intent": "daily_briefing",
                    "pi_latency_ms": 2200.0,
                    "safe_fulfillment_latency_ms": 1100.0,
                    "production_route_change": True,
                    "text_preview": "give me my daily briefing",
                    "outcome_label": None,
                },
                {
                    "ts": 3.0,
                    "source": "pi_hybrid_production",
                    "text_hash": "chat-hash",
                    "accepted": False,
                    "reason": "pi_disagreed",
                    "intent_group": "weather",
                    "pi_latency_ms": 1800.0,
                    "safe_fulfillment_latency_ms": None,
                    "production_route_change": False,
                    "text_preview": "just chatting",
                    "outcome_label": None,
                },
            ]
        ),
        encoding="utf-8",
    )
    labels_path.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                {"text_hash": "weather-hash", "outcome_label": "weather", "source": "admin_review"},
                {"text_hash": "briefing-hash", "outcome_label": "daily_briefing", "source": "admin_review"},
                {"text_hash": "chat-hash", "outcome_label": "chat", "source": "admin_review"},
            ]
        ),
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED"] = "true"
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH"] = str(production_path)
    env["ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH"] = str(labels_path)

    report = pi_readiness_report(env)

    assert report["summary"]["production_label_count"] == 3
    assert report["summary"]["production_unlabeled_count"] == 0
    assert report["production_evidence"]["label_count"] == 3
    assert report["production_evidence"]["unlabeled_count"] == 0
    assert {row["outcome_label"] for row in report["production_evidence"]["recent"]} == {
        None,
        "weather",
        "daily_briefing",
    }
    assert {row["outcome_label_source"] for row in report["production_evidence"]["recent"]} == {
        "production_label_sidecar"
    }
    assert not [action for action in report["next_actions"] if action.get("kind") == "label_production_evidence"]


def test_readiness_report_scores_labeled_production_evidence_conservatively(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text("", encoding="utf-8")
    production_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    production_path.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                {
                    "ts": 1.0,
                    "source": "pi_hybrid_production",
                    "text_hash": "weather-hash",
                    "accepted": True,
                    "reason": "accepted",
                    "intent": "weather",
                    "intent_group": "weather",
                    "pi_intent": "weather",
                    "zoe_intent": "weather",
                    "route_class": "deterministic",
                    "baseline_kind": "router",
                    "zoe_latency_ms": 1.0,
                    "pi_latency_ms": 4100.0,
                    "safe_fulfillment_latency_ms": 900.0,
                    "production_route_change": True,
                    "text_preview": "will it rain later",
                    "outcome_label": None,
                },
                {
                    "ts": 2.0,
                    "source": "pi_hybrid_production",
                    "text_hash": "briefing-hash",
                    "accepted": True,
                    "reason": "accepted",
                    "intent": "daily_briefing",
                    "intent_group": "daily_briefing",
                    "pi_intent": "daily_briefing",
                    "route_class": "fallback",
                    "baseline_kind": "router_only_not_comparable",
                    "zoe_latency_ms": 1.0,
                    "pi_latency_ms": 2200.0,
                    "safe_fulfillment_latency_ms": 1100.0,
                    "production_route_change": True,
                    "text_preview": "give me my daily briefing",
                    "outcome_label": None,
                },
            ]
        ),
        encoding="utf-8",
    )
    labels_path.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in [
                {"text_hash": "weather-hash", "outcome_label": "weather", "source": "admin_review"},
                {"text_hash": "briefing-hash", "outcome_label": "daily_briefing", "source": "admin_review"},
            ]
        ),
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED"] = "true"
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH"] = str(production_path)
    env["ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH"] = str(labels_path)

    report = pi_readiness_report(env)

    production_report = report["production_evidence"]["promotion_report"]
    assert production_report["sample_count"] == 2
    assert report["evidence"]["production_sample_count"] == 2
    assert report["evidence"]["production_real_source_sample_count_by_group"]["weather"] == 1
    assert report["evidence"]["production_real_source_sample_count_by_group"]["daily_briefing"] == 1
    weather_decision = [
        item for item in production_report["decisions"] if item["intent_group"] == "weather"
    ][0]
    assert weather_decision["sample_count"] == 1
    assert weather_decision["zoe_accuracy"] == 1.0
    assert weather_decision["pi_accuracy"] == 1.0
    assert "latency_not_faster_than_zoe" in weather_decision["blockers"]
    briefing_decision = [
        item for item in production_report["decisions"] if item["intent_group"] == "daily_briefing"
    ][0]
    assert "baseline_not_comparable" in briefing_decision["blockers"]
    assert report["summary"]["production_candidate_win_groups"] == []



def test_readiness_report_scores_labeled_production_baseline_override(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text("", encoding="utf-8")
    production_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    production_path.write_text(
        json.dumps(
            {
                "ts": 1.0,
                "source": "pi_hybrid_production",
                "text_hash": "briefing-hash",
                "accepted": True,
                "reason": "accepted",
                "intent": "daily_briefing",
                "intent_group": "daily_briefing",
                "pi_intent": "daily_briefing",
                "route_class": "fallback",
                "baseline_kind": "router_only_not_comparable",
                "baseline_comparable": False,
                "zoe_latency_ms": 1.0,
                "pi_latency_ms": 2200.0,
                "safe_fulfillment_latency_ms": 1100.0,
                "production_route_change": True,
                "text_preview": "give me my daily briefing",
                "outcome_label": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    labels_path.write_text(
        json.dumps(
            {
                "text_hash": "briefing-hash",
                "outcome_label": "daily_briefing",
                "source": "admin_review",
                "route_class": "fallback",
                "baseline_kind": "zoe_agent_fallback_baseline",
                "baseline_comparable": True,
                "zoe_latency_ms": 4800.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED"] = "true"
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH"] = str(production_path)
    env["ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH"] = str(labels_path)

    report = pi_readiness_report(env)

    decision = [
        item
        for item in report["production_evidence"]["promotion_report"]["decisions"]
        if item["intent_group"] == "daily_briefing"
    ][0]
    assert "baseline_not_comparable" not in decision["blockers"]
    assert "latency_not_faster_than_zoe" not in decision["blockers"]
    assert decision["zoe_p95_latency_ms"] == 4800.0
    assert decision["pi_p95_latency_ms"] == 2200.0
    assert decision["baseline_lane_latency"]["fallback:zoe_agent_fallback_baseline"]["latency_delta_ms"] == 2600.0


def test_readiness_report_surfaces_production_label_file_error(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text("", encoding="utf-8")
    production_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    labels_path.mkdir()
    production_path.write_text(
        json.dumps(
            {
                "source": "pi_hybrid_production",
                "text_hash": "weather-hash",
                "accepted": True,
                "intent_group": "weather",
                "outcome_label": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED"] = "true"
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH"] = str(production_path)
    env["ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH"] = str(labels_path)

    report = pi_readiness_report(env)

    assert report["production_evidence"]["label_count"] == 0
    assert report["production_evidence"]["label_error"] == "IsADirectoryError"
    assert report["summary"]["production_unlabeled_count"] == 1


def test_readiness_report_missing_production_evidence_is_nonblocking(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text("", encoding="utf-8")
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED"] = "true"

    report = pi_readiness_report(env)

    assert report["production_evidence"] == {
        "enabled": True,
        "loaded": False,
        "path": str(tmp_path / "production.jsonl"),
        "labels_path": str(tmp_path / "production-labels.jsonl"),
        "record_count": 0,
    }
    assert report["state"] == "shadow_collecting"


def test_readiness_report_disabled_production_evidence_does_not_load_existing_file(tmp_path):
    shadow_path = tmp_path / "shadow.jsonl"
    shadow_path.write_text("", encoding="utf-8")
    production_path = tmp_path / "production.jsonl"
    production_path.write_text(
        json.dumps(
            {
                "source": "pi_hybrid_production",
                "accepted": True,
                "intent_group": "weather",
                "outcome_label": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    env = _env(tmp_path, shadow_path)
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_ENABLED"] = "false"
    env["ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH"] = str(production_path)

    report = pi_readiness_report(env)

    assert report["production_evidence"] == {
        "enabled": False,
        "loaded": False,
        "path": str(production_path),
        "labels_path": str(tmp_path / "production-labels.jsonl"),
        "record_count": 0,
        "disabled": True,
    }
    assert not [action for action in report["next_actions"] if action.get("kind") == "label_production_evidence"]
