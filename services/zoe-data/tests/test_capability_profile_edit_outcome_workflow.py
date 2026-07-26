import importlib.util
import json
import sys

import httpx
import pytest
from pathlib import Path

pytestmark = pytest.mark.ci_safe


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "maintenance" / "capability_profile_edit_outcome_workflow.py"
DATA = ROOT / "services" / "zoe-data"
if str(DATA) not in sys.path:
    sys.path.insert(0, str(DATA))


def _load_module():
    spec = importlib.util.spec_from_file_location("capability_profile_edit_outcome_workflow_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def _write_json(path: Path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _pr_edit_plan(**overrides):
    payload = {
        "allowed_to_prepare_pr_edit": True,
        "ticket_id": "ZOE-777",
        "target_path": "services/zoe-data/zoe_capability_profile.py",
        "patch_text": "--- a/services/zoe-data/zoe_capability_profile.py\n+++ b/services/zoe-data/zoe_capability_profile.py\n@@ -1 +1 @@\n-old\n+new\n",
        "promoted_capability_ids": ["hindsight_reflective_memory"],
        "pr_refs": ["https://github.com/jason-easyazz/zoe-ai-assistant/pull/777"],
        "rollback_refs": ["rollback:revert-pr-777"],
        "verification_refs": ["pytest:test_profile_edit_outcome_workflow"],
        "greptile_refs": ["greptile:pass:777"],
        "blockers": [],
        "metadata": {"source": "capability_profile_pr_edit_gate"},
    }
    payload.update(overrides)
    return payload


def _trace(**overrides):
    payload = {
        "trace_id": "trace_profile_edit_777",
        "trace_type": "verification",
        "surface": "multica",
        "scope": "project",
        "outcome": "success",
        "summary": "Capability profile edit PR was reviewed, tested, and merged.",
        "evidence_refs": ["pr:777:merged", "pytest:test_profile_edit_outcome_workflow"],
        "user_id": "zoe_system",
        "subject_id": "ZOE-777",
        "confidence": 0.96,
    }
    payload.update(overrides)
    return payload


def _manifest(**record_overrides):
    record = {
        "capability_id": "hindsight_reflective_memory",
        "from_trust_level": "experimental",
        "to_trust_level": "assisted",
    }
    record.update(record_overrides)
    return {"records": [record]}


def test_main_builds_approved_profile_edit_outcome_plan(tmp_path, capsys):
    pr_plan = _write_json(tmp_path / "pr-plan.json", _pr_edit_plan())
    trace = _write_json(tmp_path / "trace.json", _trace())
    manifest = _write_json(tmp_path / "manifest.json", _manifest())

    rc = MODULE.main([
        "--pr-edit-plan-json-file",
        str(pr_plan),
        "--verification-trace-file",
        str(trace),
        "--promotion-manifest-file",
        str(manifest),
        "--user-id",
        "zoe_system",
        "--approval-ref",
        "approval:memory-admission:ZOE-777",
        "--metadata-json",
        '{"operator_surface":"capability_profile_edit_outcome_workflow"}',
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["allowed_to_admit_memory"] is True
    assert payload["admission_decision"]["status"] == "approved"
    assert payload["memory_candidate"]["event_id"] == "mem_evt_profile_edit_outcome_ZOE-777"
    assert payload["trust_records"][0]["to_trust_level"] == "assisted"
    assert "greptile:pass:777" in payload["trust_records"][0]["evidence_refs"]


def test_main_returns_1_for_pending_memory_approval(tmp_path, capsys):
    pr_plan = _write_json(tmp_path / "pr-plan.json", _pr_edit_plan())
    trace = _write_json(tmp_path / "trace.json", _trace())

    rc = MODULE.main([
        "--pr-edit-plan-json-file",
        str(pr_plan),
        "--verification-trace-file",
        str(trace),
        "--user-id",
        "zoe_system",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["allowed_to_admit_memory"] is False
    assert payload["admission_decision"]["status"] == "pending_review"
    assert payload["admission_decision"]["blockers"] == ["approval_required"]
    assert payload["trust_records"]


def test_main_execute_hindsight_rejects_pending_memory_approval(tmp_path, capsys):
    pr_plan = _write_json(tmp_path / "pr-plan.json", _pr_edit_plan())
    trace = _write_json(tmp_path / "trace.json", _trace())

    rc = MODULE.main([
        "--pr-edit-plan-json-file",
        str(pr_plan),
        "--verification-trace-file",
        str(trace),
        "--user-id",
        "zoe_system",
        "--target-backend",
        "hindsight",
        "--execute-hindsight",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["allowed_to_admit_memory"] is False
    assert payload["blockers"] == []
    assert payload["admission_decision"]["status"] == "pending_review"
    assert payload["admission_decision"]["blockers"] == ["approval_required"]
    assert payload["hindsight_execution"] == {
        "attempted": False,
        "retained": False,
        "reason": "profile_edit_outcome_not_admitted",
        "execution": None,
    }


def test_main_keeps_blocked_pr_edit_plan_blocked(tmp_path, capsys):
    pr_plan = _write_json(
        tmp_path / "pr-plan.json",
        _pr_edit_plan(
            allowed_to_prepare_pr_edit=False,
            patch_text="",
            promoted_capability_ids=[],
            blockers=["missing_greptile_refs"],
        ),
    )
    trace = _write_json(tmp_path / "trace.json", _trace())

    rc = MODULE.main([
        "--pr-edit-plan-json-file",
        str(pr_plan),
        "--verification-trace-file",
        str(trace),
        "--user-id",
        "zoe_system",
        "--approval-ref",
        "approval:memory-admission:ZOE-777",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "pr_edit_plan_not_allowed" in payload["blockers"]
    assert "missing_greptile_refs" in payload["blockers"]
    assert payload["memory_candidate"] is None


def test_main_execute_hindsight_reports_blocked_plan_without_attempt(tmp_path, capsys):
    pr_plan = _write_json(
        tmp_path / "pr-plan.json",
        _pr_edit_plan(
            allowed_to_prepare_pr_edit=False,
            patch_text="",
            promoted_capability_ids=[],
            blockers=["missing_greptile_refs"],
        ),
    )
    trace = _write_json(tmp_path / "trace.json", _trace())

    rc = MODULE.main([
        "--pr-edit-plan-json-file",
        str(pr_plan),
        "--verification-trace-file",
        str(trace),
        "--user-id",
        "zoe_system",
        "--approval-ref",
        "approval:memory-admission:ZOE-777",
        "--execute-hindsight",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "pr_edit_plan_not_allowed" in payload["blockers"]
    assert payload["hindsight_execution"] == {
        "attempted": False,
        "retained": False,
        "reason": "profile_edit_outcome_blocked",
        "execution": None,
    }


def test_main_returns_2_for_invalid_verification_trace(tmp_path, capsys):
    pr_plan = _write_json(tmp_path / "pr-plan.json", _pr_edit_plan())
    trace = _write_json(tmp_path / "trace.json", _trace(evidence_refs=[]))

    rc = MODULE.main([
        "--pr-edit-plan-json-file",
        str(pr_plan),
        "--verification-trace-file",
        str(trace),
        "--user-id",
        "zoe_system",
    ])

    captured = capsys.readouterr()
    assert rc == 2
    assert "invalid verification trace" in captured.err


def test_main_returns_2_for_unexpected_trace_field(tmp_path, capsys):
    pr_plan = _write_json(tmp_path / "pr-plan.json", _pr_edit_plan())
    trace_payload = _trace()
    trace_payload["unexpected"] = "field"
    trace = _write_json(tmp_path / "trace.json", trace_payload)

    rc = MODULE.main([
        "--pr-edit-plan-json-file",
        str(pr_plan),
        "--verification-trace-file",
        str(trace),
        "--user-id",
        "zoe_system",
    ])

    captured = capsys.readouterr()
    assert rc == 2
    assert "invalid verification trace" in captured.err
    assert "unexpected" in captured.err


@pytest.mark.asyncio
async def test_execute_profile_edit_outcome_plan_in_hindsight_posts_admitted_payload(tmp_path):
    args = MODULE.build_parser().parse_args([
        "--pr-edit-plan-json-file",
        str(_write_json(tmp_path / "pr-plan.json", _pr_edit_plan())),
        "--verification-trace-file",
        str(_write_json(tmp_path / "trace.json", _trace())),
        "--user-id",
        "zoe_system",
        "--target-backend",
        "hindsight",
        "--approval-ref",
        "approval:memory-admission:ZOE-777",
    ])
    plan = MODULE.build_profile_edit_outcome_plan_from_args(args)
    seen = {}

    async def handler(request):
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.read().decode())
        return httpx.Response(200, json={"success": True, "items_count": 1})

    from hindsight_memory import HindsightConfig, HindsightMemoryClient

    config = HindsightConfig(enabled=True, bank_prefix="zoe-test", async_retain=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HindsightMemoryClient(config, client=http_client)
        result = await MODULE.execute_profile_edit_outcome_plan_in_hindsight(plan, client=client)

    assert result["attempted"] is True
    assert result["retained"] is True
    assert result["reason"] == "retained"
    assert seen["path"] == "/v1/default/banks/zoe-test-project-zoe_system/memories"
    assert seen["payload"]["items"][0]["document_id"] == "mem_evt_profile_edit_outcome_ZOE-777"
    assert "approval:memory-admission:ZOE-777" in result["execution"]["evidence_refs"]


def test_main_execute_hindsight_returns_0_for_successful_retain(tmp_path, capsys, monkeypatch):
    pr_plan = _write_json(tmp_path / "pr-plan.json", _pr_edit_plan())
    trace = _write_json(tmp_path / "trace.json", _trace())

    async def fake_execute(plan):
        assert plan.allowed_to_admit_memory is True
        return {
            "attempted": True,
            "retained": True,
            "reason": "retained",
            "execution": {"event_id": "mem_evt_profile_edit_outcome_ZOE-777"},
        }

    monkeypatch.setattr(MODULE, "execute_profile_edit_outcome_plan_in_hindsight", fake_execute)
    rc = MODULE.main([
        "--pr-edit-plan-json-file",
        str(pr_plan),
        "--verification-trace-file",
        str(trace),
        "--user-id",
        "zoe_system",
        "--target-backend",
        "hindsight",
        "--approval-ref",
        "approval:memory-admission:ZOE-777",
        "--execute-hindsight",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["allowed_to_admit_memory"] is True
    assert payload["hindsight_execution"]["retained"] is True
    assert payload["hindsight_execution"]["reason"] == "retained"


def test_main_execute_hindsight_surfaces_disabled_execution(tmp_path, capsys, monkeypatch):
    pr_plan = _write_json(tmp_path / "pr-plan.json", _pr_edit_plan())
    trace = _write_json(tmp_path / "trace.json", _trace())

    async def fake_execute(plan):
        assert plan.allowed_to_admit_memory is True
        return {
            "attempted": False,
            "retained": False,
            "reason": "disabled",
            "execution": None,
        }

    monkeypatch.setattr(MODULE, "execute_profile_edit_outcome_plan_in_hindsight", fake_execute)
    rc = MODULE.main([
        "--pr-edit-plan-json-file",
        str(pr_plan),
        "--verification-trace-file",
        str(trace),
        "--user-id",
        "zoe_system",
        "--target-backend",
        "hindsight",
        "--approval-ref",
        "approval:memory-admission:ZOE-777",
        "--execute-hindsight",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["allowed_to_admit_memory"] is True
    assert payload["hindsight_execution"]["attempted"] is False
    assert payload["hindsight_execution"]["retained"] is False
    assert payload["hindsight_execution"]["reason"] == "disabled"


def test_main_execute_hindsight_reports_config_errors_cleanly(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HINDSIGHT_ENABLED", "true")
    monkeypatch.setenv("HINDSIGHT_BASE_URL", "https://example.com")
    pr_plan = _write_json(tmp_path / "pr-plan.json", _pr_edit_plan())
    trace = _write_json(tmp_path / "trace.json", _trace())

    rc = MODULE.main([
        "--pr-edit-plan-json-file",
        str(pr_plan),
        "--verification-trace-file",
        str(trace),
        "--user-id",
        "zoe_system",
        "--target-backend",
        "hindsight",
        "--approval-ref",
        "approval:memory-admission:ZOE-777",
        "--execute-hindsight",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 2
    assert payload["allowed_to_admit_memory"] is True
    assert payload["hindsight_execution"]["attempted"] is False
    assert payload["hindsight_execution"]["retained"] is False
    assert payload["hindsight_execution"]["reason"] == "hindsight_execution_error"
    assert "hindsight execution failed" in captured.err
