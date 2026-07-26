import pytest
import asyncio
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "pi_promotion_eval.py"
    spec = importlib.util.spec_from_file_location("pi_promotion_eval_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    old_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
    return module


def _install_fake_intent_router(monkeypatch, *, intent_name="weather", confidence=0.75):
    module = types.ModuleType("intent_router")

    async def detect_and_extract_intent(_text):
        if intent_name is None:
            return None
        return types.SimpleNamespace(name=intent_name, confidence=confidence)

    module.detect_and_extract_intent = detect_and_extract_intent
    monkeypatch.setitem(sys.modules, "intent_router", module)


def _install_fake_zoe_agent(monkeypatch, calls, *, response="agent answer", sleep_seconds=0, raises=None):
    module = types.ModuleType("zoe_agent")

    async def run_zoe_agent(message, session_id, user_id="family-admin", **kwargs):
        calls.append({"message": message, "session_id": session_id, "user_id": user_id, "kwargs": kwargs})
        if sleep_seconds:
            await asyncio.sleep(sleep_seconds)
        if raises:
            raise raises
        return response

    module.run_zoe_agent = run_zoe_agent
    monkeypatch.setitem(sys.modules, "zoe_agent", module)


def _install_fake_pi_classifier(monkeypatch, *, result=None, sleep_seconds=0):
    module = types.ModuleType("pi_intent_classifier")

    async def classify_with_pi_intent_governor(_text):
        if sleep_seconds:
            await asyncio.sleep(sleep_seconds)
        return result

    module.classify_with_pi_intent_governor = classify_with_pi_intent_governor
    monkeypatch.setitem(sys.modules, "pi_intent_classifier", module)


def test_load_zoe_env_loads_env_files_and_shell_env_wins(tmp_path, monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "DEFAULT_ENV_FILES", ())
    monkeypatch.delenv("ZOE_PI_INTENT_TIMEOUT_SECONDS", raising=False)
    env_file = tmp_path / "zoe.env"
    env_file.write_text(
        "export ZOE_PI_INTENT_TIMEOUT_SECONDS=8\n"
        "ZOE_PI_LOCAL_MODEL_CONFIGURED=from-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOE_PI_LOCAL_MODEL_CONFIGURED", "from-shell")

    env = module.load_zoe_env([str(env_file)])

    assert env["ZOE_PI_INTENT_TIMEOUT_SECONDS"] == "8"
    assert env["ZOE_PI_LOCAL_MODEL_CONFIGURED"] == "from-shell"


def test_cli_loads_cases_file_without_default_cases(tmp_path, capsys):
    module = _load_module()
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        '{"case_id":"weather_file","text":"rain later","expected_intent":"weather","intent_group":"weather","route_class":"fallback","source":"intent_miss"}\n',
        encoding="utf-8",
    )

    exit_code = module.main(["--cases-file", str(cases_path), "--no-default-cases"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["eval_case_files"] == [str(cases_path)]
    assert [case["case_id"] for case in payload["eval_cases"]] == ["weather_file"]
    assert payload["eval_case_source_counts"] == {"intent_miss": 1}
    assert payload["promotion_report"]["sample_count"] == 0


def test_zoe_baseline_uses_current_router_hit_for_stale_fallback_fixture(monkeypatch):
    _install_fake_intent_router(monkeypatch)
    calls = []
    _install_fake_zoe_agent(monkeypatch, calls)
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(module._run_zoe_baseline(case, measure_zoe_agent_baseline=True))

    assert result["baseline_kind"] == "router"
    assert result["baseline_comparable"] is True
    assert result["latency_ms"] == result["router_latency_ms"]
    assert result["correct"] is True
    assert result["baseline_timed_out"] is False
    assert result["baseline_response_chars"] is None
    assert result["baseline_error"] is None
    assert calls == []


def test_zoe_baseline_disables_shadow_evidence_while_measuring(monkeypatch):
    seen_env = []
    router = types.ModuleType("intent_router")

    async def detect_and_extract_intent(_text):
        seen_env.append({
            "enabled": os.environ.get("ZOE_PI_INTENT_ENABLED"),
            "shadow": os.environ.get("ZOE_PI_INTENT_SHADOW_ENABLED"),
        })
        return types.SimpleNamespace(name="weather", confidence=0.75)

    router.detect_and_extract_intent = detect_and_extract_intent
    monkeypatch.setitem(sys.modules, "intent_router", router)
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "true")
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(module._run_zoe_baseline(case))

    assert result["intent"] == "weather"
    assert seen_env == [{"enabled": "false", "shadow": "false"}]
    assert os.environ["ZOE_PI_INTENT_SHADOW_ENABLED"] == "true"
    assert os.environ["ZOE_PI_INTENT_ENABLED"] == "true"


def test_zoe_baseline_uses_operator_fallback_latency_override(monkeypatch):
    _install_fake_intent_router(monkeypatch, intent_name=None)
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(module._run_zoe_baseline(case, fallback_baseline_latency_ms=4321.0))

    assert result["baseline_kind"] == "operator_fallback_override"
    assert result["baseline_comparable"] is True
    assert result["latency_ms"] == 4321.0
    assert result["router_latency_ms"] >= 0
    assert result["baseline_timed_out"] is False
    assert result["baseline_response_chars"] is None
    assert result["baseline_error"] is None


def test_zoe_baseline_operator_override_wins_over_agent_measurement(monkeypatch):
    _install_fake_intent_router(monkeypatch, intent_name=None)
    calls = []
    _install_fake_zoe_agent(monkeypatch, calls)
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(
        module._run_zoe_baseline(
            case,
            fallback_baseline_latency_ms=4321.0,
            measure_zoe_agent_baseline=True,
        )
    )

    assert result["baseline_kind"] == "operator_fallback_override"
    assert result["baseline_comparable"] is True
    assert result["latency_ms"] == 4321.0
    assert calls == []


def test_zoe_baseline_uses_operator_extraction_failed_latency_override(monkeypatch):
    _install_fake_intent_router(monkeypatch, intent_name=None)
    module = _load_module()
    case = module.PiIntentEvalCase(
        "reminder", "remind me to call mum", "reminder_create", "reminders", "extraction_failed"
    )

    result = asyncio.run(module._run_zoe_baseline(case, extraction_failed_baseline_latency_ms=987.0))

    assert result["baseline_kind"] == "operator_extraction_failed_override"
    assert result["baseline_comparable"] is True
    assert result["latency_ms"] == 987.0

    assert result["baseline_timed_out"] is False
    assert result["baseline_response_chars"] is None
    assert result["baseline_error"] is None


def test_zoe_baseline_current_router_hit_skips_agent_for_stale_extraction_fixture(monkeypatch):
    _install_fake_intent_router(monkeypatch, intent_name="reminder_create", confidence=0.91)
    calls = []
    _install_fake_zoe_agent(monkeypatch, calls)
    module = _load_module()
    case = module.PiIntentEvalCase(
        "reminder", "remind me to call mum", "reminder_create", "reminders", "extraction_failed"
    )

    result = asyncio.run(module._run_zoe_baseline(case, measure_zoe_agent_baseline=True))

    assert result["baseline_kind"] == "router"
    assert result["baseline_comparable"] is True
    assert result["latency_ms"] == result["router_latency_ms"]
    assert result["intent"] == "reminder_create"
    assert result["correct"] is True
    assert calls == []


def test_zoe_baseline_can_measure_comparable_zoe_agent_fallback(monkeypatch):
    _install_fake_intent_router(monkeypatch, intent_name=None)
    calls = []
    _install_fake_zoe_agent(monkeypatch, calls, response="fallback answer")
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(module._run_zoe_baseline(case, measure_zoe_agent_baseline=True))

    assert result["baseline_kind"] == "zoe_agent_fallback_baseline"
    assert result["baseline_comparable"] is True
    assert result["latency_ms"] >= 0
    assert len(calls) == 1
    assert calls[0]["message"] == "rain later"
    assert result["baseline_timed_out"] is False
    assert result["baseline_response_chars"] == len("fallback answer")
    assert result["baseline_error"] is None
    assert calls[0]["user_id"] == "pi-eval"
    assert calls[0]["kwargs"]["history"] == []
    assert calls[0]["kwargs"]["db_memory_context"] == ""
    assert calls[0]["kwargs"]["portrait"] == ""
    assert calls[0]["kwargs"]["max_tokens_override"] == 256


def test_zoe_agent_baseline_disables_pi_shadow_evidence(monkeypatch):
    _install_fake_intent_router(monkeypatch, intent_name=None)
    seen_env = []
    agent = types.ModuleType("zoe_agent")

    async def run_zoe_agent(message, session_id, user_id="family-admin", **kwargs):
        seen_env.append({
            "enabled": os.environ.get("ZOE_PI_INTENT_ENABLED"),
            "shadow": os.environ.get("ZOE_PI_INTENT_SHADOW_ENABLED"),
        })
        return "fallback answer"

    agent.run_zoe_agent = run_zoe_agent
    monkeypatch.setitem(sys.modules, "zoe_agent", agent)
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(module._run_zoe_baseline(case, measure_zoe_agent_baseline=True))

    assert result["baseline_kind"] == "zoe_agent_fallback_baseline"
    assert seen_env == [{"enabled": "false", "shadow": "false"}]
    assert os.environ["ZOE_PI_INTENT_ENABLED"] == "true"
    assert os.environ["ZOE_PI_INTENT_SHADOW_ENABLED"] == "true"


def test_zoe_baseline_timeout_is_not_comparable(monkeypatch):
    _install_fake_intent_router(monkeypatch, intent_name=None)
    calls = []
    _install_fake_zoe_agent(monkeypatch, calls, sleep_seconds=0.05)
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(
        module._run_zoe_baseline(
            case,
            measure_zoe_agent_baseline=True,
            zoe_agent_baseline_timeout_seconds=0.001,
        )
    )

    assert result["baseline_kind"] == "zoe_agent_fallback_timeout"
    assert result["baseline_comparable"] is False
    assert result["latency_ms"] >= 0
    assert len(calls) == 1

    assert result["baseline_timed_out"] is True
    assert result["baseline_response_chars"] == 0

def test_run_cases_preserves_eval_case_source_in_sample_metadata(monkeypatch):
    _install_fake_intent_router(monkeypatch, intent_name=None)
    _install_fake_zoe_agent(monkeypatch, [], response="fallback answer")
    _install_fake_pi_classifier(monkeypatch, result=types.SimpleNamespace(intent="weather", confidence=0.93))
    module = _load_module()
    case = module.PiIntentEvalCase(
        "weather_miss",
        "will it rain later",
        "weather",
        "weather",
        "fallback",
        source="intent_miss",
    )

    _comparisons, samples = asyncio.run(
        module._run_cases(
            [case],
            transport="rpc",
            enable_execution=True,
            local_model_configured=True,
            measure_zoe_agent_baseline=True,
        )
    )

    assert len(samples) == 1
    assert samples[0].metadata["source"] == "intent_miss"
    assert samples[0].metadata["baseline_kind"] == "zoe_agent_fallback_baseline"


def test_run_pi_uses_loaded_timeout_env(monkeypatch):
    _install_fake_pi_classifier(monkeypatch, result=None, sleep_seconds=0.02)
    monkeypatch.setenv("ZOE_PI_INTENT_TIMEOUT_SECONDS", "1")
    module = _load_module()
    case = module.PiIntentEvalCase("casual", "that movie was good", None, "chat", "fallback", negative=True)

    result = asyncio.run(
        module._run_pi(case, transport="rpc", enable_execution=True, local_model_configured=True)
    )

    assert result["timed_out"] is False


def test_run_pi_fast_no_result_is_not_timeout(monkeypatch):
    _install_fake_pi_classifier(monkeypatch, result=None)
    monkeypatch.setenv("ZOE_PI_INTENT_PREFILTER_ENABLED", "false")
    module = _load_module()
    case = module.PiIntentEvalCase("casual", "that movie was good", None, "chat", "fallback", negative=True)

    result = asyncio.run(
        module._run_pi(case, transport="rpc", enable_execution=True, local_model_configured=True)
    )

    assert result["intent"] is None
    assert result["timed_out"] is False
    assert result["prefilter_enabled"] == "false"
    assert result["correct"] is True


def test_run_pi_disables_shadow_evidence_while_measuring(monkeypatch):
    seen_env = []
    classifier = types.ModuleType("pi_intent_classifier")

    async def classify_with_pi_intent_governor(_text):
        seen_env.append({
            "enabled": os.environ.get("ZOE_PI_INTENT_ENABLED"),
            "shadow": os.environ.get("ZOE_PI_INTENT_SHADOW_ENABLED"),
        })
        return types.SimpleNamespace(intent="weather", confidence=0.9)

    classifier.classify_with_pi_intent_governor = classify_with_pi_intent_governor
    monkeypatch.setitem(sys.modules, "pi_intent_classifier", classifier)
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    module = _load_module()
    case = module.PiIntentEvalCase("weather", "rain later", "weather", "weather", "fallback")

    result = asyncio.run(
        module._run_pi(case, transport="rpc", enable_execution=True, local_model_configured=True)
    )

    assert result["intent"] == "weather"
    assert seen_env == [{"enabled": "true", "shadow": "false"}]
    assert os.environ["ZOE_PI_INTENT_SHADOW_ENABLED"] == "true"



def test_cli_combines_default_and_cases_file(tmp_path, capsys):
    module = _load_module()
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "weather_file",
                        "text": "rain later",
                        "expected_intent": "weather",
                        "intent_group": "weather",
                        "route_class": "fallback",
                        "source": "synthetic",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    exit_code = module.main(["--cases-file", str(cases_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    case_ids = {case["case_id"] for case in payload["eval_cases"]}
    assert "weather_file" in case_ids
    assert len(case_ids) > 1


def test_build_eval_readiness_collects_real_source_evidence_for_candidate_wins():
    module = _load_module()
    report = {
        "promotable_groups": [],
        "rollback_groups": [],
        "promotion_actions": {"requires_operator_apply": False, "promote_groups": [], "rollback_groups": []},
        "candidate_wins": {
            "groups": ["weather"],
            "blocked_groups": ["weather"],
            "promotion_ready_groups": [],
            "details": [
                {
                    "intent_group": "weather",
                    "status": "needs_more_evidence",
                    "unique_case_deficit": 0,
                    "sample_deficit": 0,
                    "real_source_sample_deficit": 30,
                    "promotion_blockers": ["insufficient_real_source_samples"],
                }
            ],
        },
    }

    readiness = module.build_eval_readiness(report)

    assert readiness["state"] == "collect_more_evidence"
    assert readiness["next_actions"] == [
        {
            "kind": "collect_labeled_evidence",
            "priority": "p1",
            "intent_group": "weather",
            "needed_unique_cases": 0,
            "needed_real_source_cases": 30,
        }
    ]


def test_build_eval_readiness_collects_more_evidence_for_candidate_wins():
    module = _load_module()
    report = {
        "promotable_groups": [],
        "rollback_groups": [],
        "promotion_actions": {"requires_operator_apply": False, "promote_groups": [], "rollback_groups": []},
        "candidate_wins": {
            "groups": ["weather"],
            "blocked_groups": ["weather"],
            "promotion_ready_groups": [],
            "details": [
                {
                    "intent_group": "weather",
                    "status": "needs_more_evidence",
                    "unique_case_deficit": 27,
                    "sample_deficit": 27,
                    "promotion_blockers": ["insufficient_samples"],
                }
            ],
        },
    }

    readiness = module.build_eval_readiness(report)

    assert readiness["state"] == "collect_more_evidence"
    assert readiness["summary"]["candidate_win_groups"] == ["weather"]
    assert readiness["summary"]["blocked_candidate_groups"] == ["weather"]
    assert readiness["next_actions"] == [
        {
            "kind": "collect_labeled_evidence",
            "priority": "p1",
            "intent_group": "weather",
            "needed_unique_cases": 27,
        }
    ]


def test_build_eval_readiness_reports_apply_and_rollback_actions():
    module = _load_module()
    promote = module.build_eval_readiness(
        {
            "promotable_groups": ["weather"],
            "promotion_actions": {
                "promote_groups": ["weather"],
                "rollback_groups": [],
                "requires_operator_apply": True,
                "env": {"ZOE_PI_INTENT_PROMOTED_GROUPS": "weather"},
            },
            "candidate_wins": {"groups": ["weather"], "promotion_ready_groups": ["weather"]},
        }
    )
    rollback = module.build_eval_readiness(
        {
            "rollback_groups": ["weather"],
            "promotion_actions": {
                "promote_groups": [],
                "rollback_groups": ["weather"],
                "requires_operator_apply": True,
                "env": {"ZOE_PI_INTENT_PROMOTED_GROUPS": ""},
            },
            "candidate_wins": {},
        }
    )

    assert promote["state"] == "promotion_apply_ready"
    assert promote["next_actions"][0]["kind"] == "apply_promotion"
    assert promote["next_actions"][0]["groups"] == ["weather"]
    assert rollback["state"] == "rollback_required"
    assert rollback["next_actions"][0]["kind"] == "rollback"
    assert rollback["next_actions"][0]["groups"] == ["weather"]


def test_cli_output_includes_readiness_summary(tmp_path, capsys):
    module = _load_module()
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        '{"case_id":"weather_file","text":"rain later","expected_intent":"weather","intent_group":"weather","route_class":"fallback","source":"intent_miss"}\n',
        encoding="utf-8",
    )

    exit_code = module.main(["--cases-file", str(cases_path), "--no-default-cases"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["readiness"]["state"] == "keep_shadow"
    assert payload["readiness"]["summary"]["candidate_win_groups"] == []
    assert payload["readiness"]["next_actions"] == [{"kind": "continue_shadow_mode", "priority": "p2"}]


def test_build_eval_readiness_reviews_sparse_candidate_evidence():
    module = _load_module()
    report = {
        "promotion_actions": {"promote_groups": [], "rollback_groups": []},
        "candidate_wins": {"groups": ["weather"]},
    }

    readiness = module.build_eval_readiness(report)

    assert readiness["state"] == "collect_more_evidence"
    assert readiness["next_actions"] == [
        {"kind": "review_candidate_evidence", "priority": "p1", "groups": ["weather"]}
    ]


def test_cli_writes_output_file(tmp_path, capsys):
    module = _load_module()
    output_path = tmp_path / "report.json"

    exit_code = module.main(["--no-default-cases", "--output", str(output_path)])

    assert exit_code == 0
    assert capsys.readouterr().out == ""
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["eval_cases"] == []
    assert payload["promotion_report"]["sample_count"] == 0
