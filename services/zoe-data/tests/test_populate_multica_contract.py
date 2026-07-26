"""Static contract tests for the Zoe Multica bootstrap script."""
from __future__ import annotations

import pytest
import ast
from pathlib import Path

pytestmark = pytest.mark.ci_safe


def _source() -> str:
    root = Path(__file__).resolve().parents[3]
    return (root / "scripts/setup/populate_multica.py").read_text(encoding="utf-8")


def _module() -> ast.Module:
    return ast.parse(_source())


def _assigned_literal(name: str):
    for node in _module().body:
        value = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    value = node.value
                    break
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            value = node.value
        if value is not None:
            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError) as exc:
                raise AssertionError(f"{name} must remain a literal contract") from exc
    raise AssertionError(f"{name} assignment not found")



def _function_literal_assignment(function_name: str, assignment_name: str):
    for node in _module().body:
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Assign):
                continue
            if any(isinstance(target, ast.Name) and target.id == assignment_name for target in child.targets):
                try:
                    return ast.literal_eval(child.value)
                except (ValueError, SyntaxError) as exc:
                    raise AssertionError(
                        f"{function_name}.{assignment_name} must remain a literal contract"
                    ) from exc
    raise AssertionError(f"{assignment_name} assignment not found in {function_name}")

def test_managed_agents_keep_provider_specific_runtime_contract():
    agent_defs = _assigned_literal("_AGENT_DEFS")
    providers = {agent["name"]: agent.get("runtime_provider") for agent in agent_defs}
    fallback_models = {agent["name"]: agent.get("fallback_model") for agent in agent_defs}

    assert providers["Zoe Core"] == "zoe"
    assert providers["OpenClaw"] == "hermes"
    assert fallback_models["OpenClaw"] == "main"
    assert providers["Hermes"] == "hermes"
    assert fallback_models["Hermes"] == "main"
    assert providers["Agent Zero"] == "zoe"
    assert providers["Auto Research Engineer"] == "hermes"
    assert providers["Self-Improvement Agent"] == "zoe"

    source = _source()
    assert "def _runtime_ids_by_provider" in source
    assert "target_runtime_id = runtime_ids.get" in source
    assert 'target_model = str(defn.get("fallback_model") or target_model)' in source
    assert "model={_sql_literal(target_model)}" in source
    assert "runtime_id={_sql_literal(target_runtime_id)}" in source
    assert '"model": target_model' in source
    assert '"runtime_id": target_runtime_id' in source


def test_autoresearch_skill_agent_and_scope_are_managed():
    skill_defs = _assigned_literal("_SKILL_DEFS")
    skill_names = {name for _, name in skill_defs}
    skill_paths = {name: path for path, name in skill_defs}
    agents = {agent["name"]: agent for agent in _assigned_literal("_AGENT_DEFS")}
    assignments = _assigned_literal("_SKILL_ASSIGNMENTS")
    projects = {title: description for title, _, description in _assigned_literal("_PROJECTS")}
    labels = {name for name, _ in _assigned_literal("_LABELS")}

    assert "autoresearch" in labels
    assert "Autoresearch Lab" in projects
    assert "fixed-budget asset optimization" in projects["Autoresearch Lab"]
    assert "Auto Research Engineer" in skill_names
    assert skill_paths["Auto Research Engineer"] == "skills/autoresearch-engineer/SKILL.md"

    agent = agents["Auto Research Engineer"]
    assert agent["runtime_provider"] == "hermes"
    assert agent["fallback_model"] == "main"
    assert "locked scoring file" in agent["instructions"]
    assert "Never edit the instructions/program file" in agent["instructions"]
    assert assignments["Auto Research Engineer"] == ["Auto Research Engineer"]
    assert "Auto Research Engineer" in assignments["Hermes"]

    squads = {
        squad["name"]: squad
        for squad in _function_literal_assignment("step_h_create_squads", "squads_to_create")
    }
    assert squads["Autoresearch Lab"]["leader"] == "Auto Research Engineer"
    assert squads["Autoresearch Lab"]["members"] == ["Hermes", "Self-Improvement Agent"]
    assert "locked" in squads["Autoresearch Lab"]["instructions"]
    assert squads["Research & Planning Squad"]["members"] == ["Zoe Core", "Auto Research Engineer"]


def test_managed_autopilots_keep_execution_modes_and_templates():
    autopilots = {ap["title"]: ap for ap in _assigned_literal("_AUTOPILOTS")}

    assert autopilots["Morning Checkin"]["status"] == "paused"
    assert autopilots["Morning Checkin"]["execution_mode"] == "run_only"
    assert autopilots["Morning Checkin"]["issue_title_template"] == ""
    assert autopilots["Evening Wind Down"]["status"] == "paused"
    assert autopilots["Evening Wind Down"]["execution_mode"] == "run_only"
    assert autopilots["Evening Wind Down"]["issue_title_template"] == ""
    assert autopilots["Reminder Scan"]["status"] == "paused"
    assert autopilots["Reminder Scan"]["execution_mode"] == "run_only"
    assert autopilots["Platform Health Check"]["agent"] == "Hermes"
    assert autopilots["Platform Health Check"]["execution_mode"] == "create_issue"

    source = _source()
    assert "update autopilot set" in source
    assert "status_sql = (" in source
    assert "if \"status\" in apdef else \"\"" in source
    assert "status={_sql_literal(apdef.get('status', 'active'))}" not in source
    assert "execution_mode={_sql_literal(apdef['execution_mode'])}" in source
    assert "issue_title_template={_sql_literal(apdef.get('issue_title_template', ''))}" in source
    assert "continue" in source[source.index("refresh failed"):source.index("else:", source.index("refresh failed"))]



def test_runtime_fallback_and_trigger_refresh_are_observable():
    source = _source()

    assert "Provider '{runtime_provider}' not online" in source
    assert "and provider in ('hermes')" in source
    assert "openclaw', 'cursor" not in source
    assert "delete from autopilot_trigger" in source
    assert "kind='schedule'" in source
    assert 'desired_cron = apdef["cron"]' in source
    assert 't.get("timezone") == desired_timezone' in source
    assert '(t.get("timezone") or desired_timezone)' not in source
    assert "Replaced schedule trigger with" in source
    assert source.index("trig = _post") < source.index("delete from autopilot_trigger")
    assert "id <> {_sql_literal(trig['id'])}" in source
