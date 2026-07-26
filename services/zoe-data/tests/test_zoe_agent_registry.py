"""
Unit tests for zoe_agent_registry helpers.

Covers load_agent_registry (valid YAML via tmp_path path= arg, missing/malformed
-> safe default), build_agent_team_prompt, registry_tool_description (including
fallback), and get_agent_info. Pure helpers are exercised with small synthetic
dicts; load_agent_registry is the only one that touches the filesystem via a
caller-supplied tmp_path. No network, no real registry file.
"""
import pytest

import zoe_agent_registry

pytestmark = pytest.mark.ci_safe


# ── load_agent_registry ──────────────────────────────────────────────────────


class TestLoadAgentRegistry:
    def test_loads_valid_yaml(self, tmp_path):
        registry_file = tmp_path / "agents_registry.yml"
        registry_file.write_text(
            "agents:\n"
            "  hermes:\n"
            "    description: Hermes\n"
            "    skills: [reasoning, planning]\n"
            "  openclaw:\n"
            "    description: OpenClaw\n"
            "    skills: [browser_automation]\n",
            encoding="utf-8",
        )
        loaded = zoe_agent_registry.load_agent_registry(path=str(registry_file))
        assert isinstance(loaded, dict)
        assert "agents" in loaded
        assert set(loaded["agents"].keys()) == {"hermes", "openclaw"}
        assert loaded["agents"]["hermes"]["skills"] == ["reasoning", "planning"]

    def test_missing_file_returns_empty_dict(self, tmp_path):
        missing = tmp_path / "does_not_exist.yml"
        # Should not raise; safe default is an empty dict.
        loaded = zoe_agent_registry.load_agent_registry(path=str(missing))
        assert loaded == {}

    def test_malformed_yaml_returns_empty_dict(self, tmp_path):
        bad_file = tmp_path / "bad.yml"
        bad_file.write_text("agents: : : not valid yaml  :::\n  - [\n", encoding="utf-8")
        loaded = zoe_agent_registry.load_agent_registry(path=str(bad_file))
        assert loaded == {}

    def test_empty_yaml_returns_empty_dict(self, tmp_path):
        empty_file = tmp_path / "empty.yml"
        empty_file.write_text("", encoding="utf-8")
        loaded = zoe_agent_registry.load_agent_registry(path=str(empty_file))
        # safe_load on empty doc yields None -> the helper coerces to {}.
        assert loaded == {}


# ── build_agent_team_prompt ──────────────────────────────────────────────────


class TestBuildAgentTeamPrompt:
    def test_empty_registry_returns_empty_string(self):
        assert zoe_agent_registry.build_agent_team_prompt({}) == ""

    def test_registry_with_no_agents_returns_empty_string(self):
        assert zoe_agent_registry.build_agent_team_prompt({"agents": {}}) == ""

    def test_renders_hermes_entry(self):
        registry = {
            "agents": {
                "hermes": {
                    "description": "Hermes engineering agent",
                    "skills": ["reasoning", "planning"],
                }
            }
        }
        prompt = zoe_agent_registry.build_agent_team_prompt(registry)
        assert prompt.startswith("\nAGENT TEAM")
        assert "Hermes engineering agent" in prompt
        assert "skills=reasoning, planning" in prompt
        assert "escalate_to_hermes" in prompt
        # Routing guidance footer must always be present when any agent renders.
        assert "Delegate to Hermes by default" in prompt

    def test_renders_hermes_and_openclaw(self):
        registry = {
            "agents": {
                "hermes": {
                    "description": "Hermes",
                    "skills": ["reasoning"],
                },
                "openclaw": {
                    "description": "OpenClaw",
                    "skills": ["browser_automation"],
                },
            }
        }
        prompt = zoe_agent_registry.build_agent_team_prompt(registry)
        assert "escalate_to_hermes" in prompt
        assert "escalate_to_openclaw" in prompt
        assert "Hermes" in prompt and "OpenClaw" in prompt

    def test_unknown_agent_id_is_skipped(self):
        registry = {
            "agents": {
                "mystery_agent": {
                    "description": "Mystery",
                    "skills": ["x"],
                }
            }
        }
        # mystery_agent is not in the _TOOL_MAP, so the prompt body has no
        # per-agent line for it, but the header + footer are still emitted.
        prompt = zoe_agent_registry.build_agent_team_prompt(registry)
        assert "Mystery" not in prompt
        assert "AGENT TEAM" in prompt
        assert "Delegate to Hermes by default" in prompt

    def test_falls_back_to_agent_id_when_description_missing(self):
        registry = {
            "agents": {
                "hermes": {"skills": ["reasoning"]},  # no description
            }
        }
        prompt = zoe_agent_registry.build_agent_team_prompt(registry)
        # The line uses the agent_id as the visible label.
        assert "- hermes:" in prompt
        assert "escalate_to_hermes" in prompt


# ── registry_tool_description ────────────────────────────────────────────────


class TestRegistryToolDescription:
    def test_missing_agent_returns_fallback(self):
        registry = {"agents": {"hermes": {"description": "Hermes"}}}
        fallback = "Use this tool for engineering tasks."
        assert (
            zoe_agent_registry.registry_tool_description(registry, "openclaw", fallback)
            == fallback
        )

    def test_missing_agents_key_returns_fallback(self):
        fallback = "fallback text"
        assert zoe_agent_registry.registry_tool_description({}, "hermes", fallback) == fallback

    def test_renders_skills_and_description(self):
        registry = {
            "agents": {
                "hermes": {
                    "description": "Hermes engineering agent",
                    "skills": ["reasoning", "planning"],
                }
            }
        }
        desc = zoe_agent_registry.registry_tool_description(
            registry, "hermes", "fallback clause"
        )
        assert "Hermes engineering agent" in desc
        assert "Skills: reasoning, planning." in desc
        assert "fallback clause" in desc

    def test_empty_skills_uses_general_tasks_phrase(self):
        registry = {
            "agents": {
                "hermes": {
                    "description": "Hermes",
                    "skills": [],
                }
            }
        }
        desc = zoe_agent_registry.registry_tool_description(
            registry, "hermes", "fb"
        )
        assert "Skills: general tasks." in desc

    def test_latency_note_appended(self):
        registry = {
            "agents": {
                "hermes": {
                    "description": "Hermes",
                    "skills": ["reasoning"],
                    "latency": "~3s p50",
                }
            }
        }
        desc = zoe_agent_registry.registry_tool_description(
            registry, "hermes", "fb"
        )
        assert "(~3s p50)" in desc

    def test_no_latency_means_no_parens(self):
        registry = {
            "agents": {
                "hermes": {
                    "description": "Hermes",
                    "skills": ["reasoning"],
                }
            }
        }
        desc = zoe_agent_registry.registry_tool_description(
            registry, "hermes", "fb"
        )
        # No latency key -> no "(...)" injection between description and "Skills:".
        assert "Hermes. Skills:" in desc

    def test_falls_back_to_agent_id_when_description_missing(self):
        registry = {
            "agents": {
                "hermes": {"skills": ["reasoning"]},  # no description
            }
        }
        desc = zoe_agent_registry.registry_tool_description(
            registry, "hermes", "fb"
        )
        # The agent_id is used as the visible label when description is absent.
        assert desc.startswith("hermes. ")
        assert "Skills: reasoning." in desc


# ── get_agent_info ───────────────────────────────────────────────────────────


class TestGetAgentInfo:
    def test_returns_agent_dict(self):
        info = {"description": "Hermes", "skills": ["reasoning"]}
        registry = {"agents": {"hermes": info}}
        assert zoe_agent_registry.get_agent_info(registry, "hermes") == info

    def test_missing_agent_returns_empty_dict(self):
        registry = {"agents": {"hermes": {"description": "Hermes"}}}
        assert zoe_agent_registry.get_agent_info(registry, "openclaw") == {}

    def test_missing_agents_key_returns_empty_dict(self):
        assert zoe_agent_registry.get_agent_info({}, "hermes") == {}

    def test_empty_registry_returns_empty_dict(self):
        assert zoe_agent_registry.get_agent_info({}, "anything") == {}
