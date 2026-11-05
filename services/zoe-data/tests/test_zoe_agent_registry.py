"""
Unit tests for zoe_agent_registry helpers.

Covers load_agent_registry (valid YAML via tmp_path path= arg, missing/malformed
-> safe default) and get_agent_info. Pure helpers are exercised with small synthetic
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
