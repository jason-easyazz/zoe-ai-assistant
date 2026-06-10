"""Static contract tests for the Zoe Multica bootstrap script."""
from __future__ import annotations

import ast
from pathlib import Path


def _source() -> str:
    root = Path(__file__).resolve().parents[3]
    return (root / "scripts/setup/populate_multica.py").read_text(encoding="utf-8")


def _module() -> ast.Module:
    return ast.parse(_source())


def _assigned_literal(name: str):
    for node in _module().body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} assignment not found")


def test_managed_agents_keep_provider_specific_runtime_contract():
    agent_defs = _assigned_literal("_AGENT_DEFS")
    providers = {agent["name"]: agent.get("runtime_provider") for agent in agent_defs}

    assert providers["Zoe Core"] == "zoe"
    assert providers["OpenClaw"] == "hermes"
    assert providers["Hermes"] == "hermes"
    assert providers["Agent Zero"] == "zoe"
    assert providers["Self-Improvement Agent"] == "zoe"

    source = _source()
    assert "def _runtime_ids_by_provider" in source
    assert "target_runtime_id = runtime_ids.get" in source
    assert "runtime_id={_sql_literal(target_runtime_id)}" in source
    assert '"runtime_id": target_runtime_id' in source


def test_managed_autopilots_keep_execution_modes_and_templates():
    autopilots = {ap["title"]: ap for ap in _assigned_literal("_AUTOPILOTS")}

    assert autopilots["Morning Checkin"]["execution_mode"] == "run_only"
    assert autopilots["Morning Checkin"]["issue_title_template"] == ""
    assert autopilots["Evening Wind Down"]["execution_mode"] == "run_only"
    assert autopilots["Evening Wind Down"]["issue_title_template"] == ""
    assert autopilots["Reminder Scan"]["execution_mode"] == "run_only"
    assert autopilots["Platform Health Check"]["agent"] == "Hermes"
    assert autopilots["Platform Health Check"]["execution_mode"] == "create_issue"

    source = _source()
    assert "update autopilot set" in source
    assert "execution_mode={_sql_literal(apdef['execution_mode'])}" in source
    assert "issue_title_template={_sql_literal(apdef.get('issue_title_template', ''))}" in source
