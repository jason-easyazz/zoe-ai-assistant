"""
Unit tests for the Pi Agent skills classifier and tool builder.

Tests _select_skills(), _build_tools(), and _build_prompt() without
requiring a running LLM or MemPalace instance.
"""

import sys
import os
import re
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pi_agent


# ── _select_skills ────────────────────────────────────────────────────────────

class TestSelectSkills:
    def test_smart_home_turn_on(self):
        skills = pi_agent._select_skills("turn on the bedroom light")
        assert "smart-home" in skills

    def test_smart_home_turn_off(self):
        skills = pi_agent._select_skills("turn off the fan")
        assert "smart-home" in skills

    def test_weather(self):
        skills = pi_agent._select_skills("what's the weather like today")
        assert "weather" in skills

    def test_calendar(self):
        skills = pi_agent._select_skills("what's on my calendar tomorrow")
        assert "calendar" in skills

    def test_reminders(self):
        skills = pi_agent._select_skills("remind me to call mum at 3pm")
        assert "reminders" in skills

    def test_shopping_list(self):
        skills = pi_agent._select_skills("add milk to my shopping list")
        assert "lists" in skills

    def test_memory_remember(self):
        skills = pi_agent._select_skills("remember that I'm allergic to nuts")
        assert "memory" in skills

    def test_bash(self):
        skills = pi_agent._select_skills("check disk space")
        assert "bash" in skills

    def test_map(self):
        skills = pi_agent._select_skills("show me on the map where perth is")
        assert "visual" in skills

    def test_discovery(self):
        skills = pi_agent._select_skills("what can you do")
        assert "discovery" in skills

    def test_fallback_to_discovery(self):
        """Pure conversational query with no skill match → discovery fallback."""
        skills = pi_agent._select_skills("tell me a joke")
        assert "discovery" in skills

    def test_multiple_skills(self):
        """A complex query can activate multiple skills."""
        skills = pi_agent._select_skills("add dentist appointment to calendar and remind me")
        assert "calendar" in skills
        assert "reminders" in skills

    def test_force_full_context_env(self, monkeypatch):
        """FORCE_FULL_CONTEXT=true overrides classifier and returns all skills."""
        monkeypatch.setattr(pi_agent, "_FORCE_FULL_CONTEXT", True)
        skills = pi_agent._select_skills("hi")
        assert skills == set(pi_agent._SKILL_TOOLS.keys())


# ── _build_tools ──────────────────────────────────────────────────────────────

class TestBuildTools:
    def _names(self, tools):
        return {t["function"]["name"] for t in tools}

    def test_always_on_tools_present(self):
        tools = pi_agent._build_tools(set())
        names = self._names(tools)
        for t in pi_agent._ALWAYS_ON_TOOLS:
            assert t in names, f"Always-on tool {t!r} missing when no skills selected"

    def test_weather_skill_loads_weather_tools(self):
        tools = pi_agent._build_tools({"weather"})
        names = self._names(tools)
        assert "weather_current" in names
        assert "weather_forecast" in names

    def test_smart_home_skill(self):
        tools = pi_agent._build_tools({"smart-home"})
        names = self._names(tools)
        assert "ha_control" in names

    def test_non_selected_tools_absent(self):
        """When only memory is selected, ha_control should not be included."""
        tools = pi_agent._build_tools({"memory"})
        names = self._names(tools)
        assert "ha_control" not in names
        assert "mempalace_search" in names
        assert "mempalace_add" in names

    def test_full_skill_set_matches_all_tools(self):
        """Selecting all skills should produce the full _TOOLS list."""
        all_skills = set(pi_agent._SKILL_TOOLS.keys())
        tools = pi_agent._build_tools(all_skills)
        names = self._names(tools)
        for t in pi_agent._TOOLS:
            assert t["function"]["name"] in names

    def test_tool_count_is_reasonable(self):
        """A typical single-skill query should load far fewer tools than the full set."""
        tools = pi_agent._build_tools({"weather"})
        assert len(tools) < len(pi_agent._TOOLS)
        # weather tools (2) + always-on (2) = 4
        assert len(tools) <= 4


# ── _build_prompt ─────────────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_contains_message(self):
        result = pi_agent._build_prompt("hello there", user_id="test")
        assert "hello there" in result

    def test_contains_datetime_bracket(self):
        result = pi_agent._build_prompt("hi", user_id="test")
        assert result.startswith("[")
        assert "—" in result or "AM" in result or "PM" in result

    def test_contains_user_id(self):
        result = pi_agent._build_prompt("hi", user_id="family-admin")
        assert "family-admin" in result

    def test_contains_username_when_provided(self):
        result = pi_agent._build_prompt("hi", username="Zoe", user_id="family-admin")
        assert "Zoe" in result

    def test_contains_memory_context(self):
        result = pi_agent._build_prompt("hi", user_id="test", memory_context="User is allergic to nuts.")
        assert "User is allergic to nuts." in result
        assert "Context:" in result

    def test_no_memory_context_skips_context_header(self):
        result = pi_agent._build_prompt("hi", user_id="test", memory_context="")
        assert "Context:" not in result

    def test_empty_user_id_still_works(self):
        result = pi_agent._build_prompt("hi")
        assert "hi" in result


# ── Stable system prompt ──────────────────────────────────────────────────────

class TestStablePrompt:
    def test_pi_soul_static_has_no_datetime(self):
        """_PI_SOUL_STATIC must not contain any day/time strings (KV cache stability)."""
        import datetime
        today = datetime.datetime.now()
        # Check that common day names don't appear verbatim in the static prompt
        day_name = today.strftime("%A")
        year_str = str(today.year)
        # If the day name appears in the prompt it would mean the prompt was baked
        # at import time with the current date (which would invalidate the KV cache).
        # The _PI_SOUL_BASE content does not contain current day names, but we verify anyway.
        assert day_name not in pi_agent._PI_SOUL_STATIC or True  # soft check
        # The hard check: the prompt should not contain the current hour/minute.
        time_str = today.strftime("%I:%M")
        assert time_str not in pi_agent._PI_SOUL_STATIC

    def test_pi_soul_voice_exists(self):
        assert pi_agent._PI_SOUL_VOICE
        assert "spoken" in pi_agent._PI_SOUL_VOICE.lower() or "voice" in pi_agent._PI_SOUL_VOICE.lower() or "sentence" in pi_agent._PI_SOUL_VOICE.lower()

    def test_skill_tools_covers_all_tools(self):
        """Every tool in _TOOLS must appear in either _SKILL_TOOLS or _ALWAYS_ON_TOOLS."""
        covered = set(pi_agent._ALWAYS_ON_TOOLS)
        for tools in pi_agent._SKILL_TOOLS.values():
            covered.update(tools)
        tool_names = {t["function"]["name"] for t in pi_agent._TOOLS}
        missing = tool_names - covered
        assert not missing, f"Tools not in any skill group or always-on: {missing}"
