"""Tests for services/zoe-data/skill_discovery.py.

Covers the OpenClaw and Hermes skill-file parsers. The parsers both accept a
``skills_dir=`` argument, so every test materialises synthetic skill files in a
``tmp_path`` directory and passes that path in — no real directories, no
network, no mutation of the operator's home.

A small per-test fixture invalidates the module-level cache (the parsers are
memoised and several unit suites run inside the same process) so each test
gets a fresh parse.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import skill_discovery as sd  # noqa: E402

pytestmark = pytest.mark.ci_safe


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_caches(monkeypatch):
    """Force a fresh parse each test by marking both caches dirty.

    Sets the module-level dirty flags via ``monkeypatch.setattr`` (per the repo
    test rule for module globals) so pytest restores them automatically after
    each test — equivalent to calling ``invalidate_*_cache()`` but self-cleaning.
    """
    monkeypatch.setattr(sd, "_openclaw_cache_dirty", True)
    monkeypatch.setattr(sd, "_hermes_cache_dirty", True)


def _write_skill_dir(parent: Path, name: str, body: str) -> Path:
    """Create ``parent/name/SKILL.md`` with ``body`` and return the skill dir."""
    skill = parent / name
    skill.mkdir()
    (skill / "SKILL.md").write_text(body, encoding="utf-8")
    return skill


# ── OpenClaw: parse_openclaw_skills ───────────────────────────────────────────


class TestParseOpenclawSkills:
    def test_format_a_metadata_when_html_comment(self, tmp_path):
        _write_skill_dir(
            tmp_path,
            "weather-skill",
            "<!-- metadata.when: surface the local weather forecast -->\n",
        )
        skills = sd.parse_openclaw_skills(skills_dir=str(tmp_path))

        assert skills == [
            {
                "id": "weather-skill",
                "name": "Weather Skill",
                "description": "surface the local weather forecast",
                "inputModes": ["text"],
                "outputModes": ["text"],
            }
        ]

    def test_format_b_when_to_use_section(self, tmp_path):
        body = (
            "# Some skill\n"
            "\n"
            "## When to Use\n"
            "- Use this for translating text\n"
            "- Use this for spell-checking prose\n"
        )
        _write_skill_dir(tmp_path, "translator", body)
        skills = sd.parse_openclaw_skills(skills_dir=str(tmp_path))

        assert len(skills) == 1
        assert skills[0]["id"] == "translator"
        assert "translating text" in skills[0]["description"]
        assert "spell-checking prose" in skills[0]["description"]

    def test_format_c_trigger_conditions_section(self, tmp_path):
        body = (
            "# Some skill\n"
            "\n"
            "## Trigger conditions\n"
            "- User asks for unit conversion\n"
        )
        _write_skill_dir(tmp_path, "unit-converter", body)
        skills = sd.parse_openclaw_skills(skills_dir=str(tmp_path))

        assert len(skills) == 1
        assert "unit conversion" in skills[0]["description"]

    def test_fallback_uses_first_non_heading_line(self, tmp_path):
        body = "First real line of the skill file\n\nMore text below.\n"
        _write_skill_dir(tmp_path, "mystery", body)
        skills = sd.parse_openclaw_skills(skills_dir=str(tmp_path))

        assert len(skills) == 1
        assert skills[0]["description"] == "First real line of the skill file"

    def test_lowercase_skill_md_is_accepted(self, tmp_path):
        """`skill.md` (lowercase) is the documented case-insensitive fallback."""
        d = tmp_path / "lowercase-skill"
        d.mkdir()
        (d / "skill.md").write_text("<!-- metadata.when: a lowercase file -->\n")
        skills = sd.parse_openclaw_skills(skills_dir=str(tmp_path))

        assert len(skills) == 1
        assert skills[0]["id"] == "lowercase-skill"
        assert skills[0]["description"] == "a lowercase file"

    def test_directory_without_skill_file_is_silently_skipped(self, tmp_path):
        (tmp_path / "no-skill-file").mkdir()
        (tmp_path / "another-empty").mkdir()
        skills = sd.parse_openclaw_skills(skills_dir=str(tmp_path))
        assert skills == []

    def test_malformed_or_bizarre_file_falls_back_gracefully(self, tmp_path):
        """A SKILL.md with no metadata, no sections, and no prose still parses.

        The parser must not raise; it should fall back to the per-directory
        description so a corrupt file can't poison the whole directory.
        """
        body = "!!!@@@###\n<!-- unterminated comment\n"  # no headings, no metadata
        _write_skill_dir(tmp_path, "weird", body)
        skills = sd.parse_openclaw_skills(skills_dir=str(tmp_path))

        assert len(skills) == 1
        # Either the first non-heading line wins, or the explicit fallback
        # string is used — both prove the parser did not raise.
        assert isinstance(skills[0]["description"], str)
        assert skills[0]["description"]

    def test_mix_of_valid_and_invalid_dirs(self, tmp_path):
        _write_skill_dir(
            tmp_path,
            "good",
            "<!-- metadata.when: a good skill -->\n",
        )
        # An empty dir next to a valid one should be ignored.
        (tmp_path / "empty").mkdir()
        skills = sd.parse_openclaw_skills(skills_dir=str(tmp_path))

        ids = [s["id"] for s in skills]
        assert ids == ["good"]

    def test_non_existent_skills_dir_returns_empty_list(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        skills = sd.parse_openclaw_skills(skills_dir=str(missing))
        assert skills == []

    def test_loose_file_at_root_is_ignored(self, tmp_path):
        """Files at the top level (not in a directory) are not skills."""
        (tmp_path / "loose.md").write_text("<!-- metadata.when: loose -->\n")
        _write_skill_dir(
            tmp_path,
            "real",
            "<!-- metadata.when: a real skill -->\n",
        )
        skills = sd.parse_openclaw_skills(skills_dir=str(tmp_path))
        assert [s["id"] for s in skills] == ["real"]


# ── Hermes: parse_hermes_skills ───────────────────────────────────────────────


class TestParseHermesSkills:
    def test_direct_skill_with_yaml_frontmatter(self, tmp_path):
        body = (
            "---\n"
            "name: My Direct Skill\n"
            "description: A skill placed directly under the skills root.\n"
            "---\n"
            "\n# My Direct Skill\n"
        )
        _write_skill_dir(tmp_path, "direct", body)
        skills = sd.parse_hermes_skills(skills_dir=str(tmp_path))

        assert skills == [
            {
                "id": "direct",
                "name": "My Direct Skill",
                "description": "A skill placed directly under the skills root.",
                "inputModes": ["text"],
                "outputModes": ["text"],
            }
        ]

    def test_direct_skill_without_frontmatter_uses_fallback_name(self, tmp_path):
        _write_skill_dir(tmp_path, "bare-direct", "# bare direct skill\n")
        skills = sd.parse_hermes_skills(skills_dir=str(tmp_path))

        assert len(skills) == 1
        assert skills[0]["id"] == "bare-direct"
        # No `name:` in frontmatter, so the parser falls back to the dir name.
        assert skills[0]["name"] == "Bare Direct"
        # No `description:` in frontmatter, so it uses the explicit fallback.
        assert skills[0]["description"] == "Hermes skill: bare-direct"

    def test_category_with_description_and_sub_skills(self, tmp_path):
        cat = tmp_path / "weather"
        cat.mkdir()
        (cat / "DESCRIPTION.md").write_text(
            "---\n"
            "description: Weather-related capabilities.\n"
            "---\n",
            encoding="utf-8",
        )
        for name, slug in [("Current", "current"), ("Forecast", "forecast")]:
            sub = cat / slug
            sub.mkdir()
            (sub / "SKILL.md").write_text(
                "---\n"
                f"name: Weather {name}\n"
                f"description: {name} weather helpers.\n"
                "---\n",
                encoding="utf-8",
            )

        skills = sd.parse_hermes_skills(skills_dir=str(tmp_path))

        ids = sorted(s["id"] for s in skills)
        assert ids == ["weather/current", "weather/forecast"]
        for s in skills:
            assert s["inputModes"] == ["text"]
            assert s["outputModes"] == ["text"]
            # The frontmatter on each sub-skill supplies its own description;
            # the category's DESCRIPTION.md should not clobber it.
            assert s["description"].endswith("weather helpers.")

    def test_category_with_no_sub_skills_exposes_itself(self, tmp_path):
        cat = tmp_path / "lonely"
        cat.mkdir()
        (cat / "DESCRIPTION.md").write_text(
            "---\n"
            "description: A category with no sub-skills.\n"
            "---\n",
            encoding="utf-8",
        )

        skills = sd.parse_hermes_skills(skills_dir=str(tmp_path))

        assert len(skills) == 1
        assert skills[0]["id"] == "lonely"
        assert skills[0]["description"] == "A category with no sub-skills."

    def test_sub_skill_without_frontmatter_inherits_category_description(self, tmp_path):
        cat = tmp_path / "shared"
        cat.mkdir()
        (cat / "DESCRIPTION.md").write_text(
            "---\n"
            "description: Shared category description.\n"
            "---\n",
            encoding="utf-8",
        )
        _write_skill_dir(cat, "unmarked", "# unmarked\n")

        skills = sd.parse_hermes_skills(skills_dir=str(tmp_path))

        assert len(skills) == 1
        assert skills[0]["id"] == "shared/unmarked"
        # No frontmatter → parser falls back to "Hermes skill: ..." and then
        # the category helper should overwrite it with the category's
        # description.
        assert skills[0]["description"] == "Shared category description."

    def test_non_existent_skills_dir_returns_empty_list(self, tmp_path):
        missing = tmp_path / "missing"
        skills = sd.parse_hermes_skills(skills_dir=str(missing))
        assert skills == []

    def test_loose_file_at_root_is_ignored(self, tmp_path):
        (tmp_path / "stray.md").write_text(
            "---\nname: Stray\ndescription: A loose markdown file.\n---\n"
        )
        _write_skill_dir(
            tmp_path,
            "real",
            "---\nname: Real\ndescription: A real skill.\n---\n",
        )
        skills = sd.parse_hermes_skills(skills_dir=str(tmp_path))
        assert [s["id"] for s in skills] == ["real"]


# ── Cache invalidation helpers ───────────────────────────────────────────────


class TestCacheInvalidation:
    def test_openclaw_cache_returns_same_object_until_invalidated(self, tmp_path):
        _write_skill_dir(
            tmp_path,
            "cached",
            "<!-- metadata.when: a cached skill -->\n",
        )
        first = sd.parse_openclaw_skills(skills_dir=str(tmp_path))
        second = sd.parse_openclaw_skills(skills_dir=str(tmp_path))
        assert first == second
        # Same list object — the parser is memoising the result.
        assert first is second

        # Mutate the directory and invalidate; the next call must reflect it.
        (tmp_path / "cached" / "SKILL.md").write_text(
            "<!-- metadata.when: a refreshed skill -->\n",
            encoding="utf-8",
        )
        sd.invalidate_openclaw_cache()
        third = sd.parse_openclaw_skills(skills_dir=str(tmp_path))
        assert third is not first
        assert third[0]["description"] == "a refreshed skill"

    def test_hermes_cache_returns_same_object_until_invalidated(self, tmp_path):
        _write_skill_dir(
            tmp_path,
            "cached",
            "---\nname: Cached\ndescription: cached value.\n---\n",
        )
        first = sd.parse_hermes_skills(skills_dir=str(tmp_path))
        second = sd.parse_hermes_skills(skills_dir=str(tmp_path))
        assert first == second
        assert first is second

        _write_skill_dir(
            tmp_path,
            "fresh",
            "---\nname: Fresh\ndescription: fresh value.\n---\n",
        )
        sd.invalidate_hermes_cache()
        third = sd.parse_hermes_skills(skills_dir=str(tmp_path))
        assert third is not first
        assert sorted(s["id"] for s in third) == ["cached", "fresh"]

    def test_invalidate_helpers_are_idempotent(self, tmp_path):
        """Calling the invalidators repeatedly must not raise and must remain a
        valid precondition for the next parse."""
        # Use disjoint subdirs so the two parsers can't see each other's files.
        oc_dir = tmp_path / "oc"
        hm_dir = tmp_path / "hm"
        oc_dir.mkdir()
        hm_dir.mkdir()

        _write_skill_dir(oc_dir, "one", "<!-- metadata.when: one -->\n")
        _write_skill_dir(
            hm_dir,
            "two",
            "---\nname: Two\ndescription: two.\n---\n",
        )
        sd.parse_openclaw_skills(skills_dir=str(oc_dir))
        sd.parse_hermes_skills(skills_dir=str(hm_dir))

        # Multiple invalidations are fine.
        sd.invalidate_openclaw_cache()
        sd.invalidate_openclaw_cache()
        sd.invalidate_hermes_cache()
        sd.invalidate_hermes_cache()

        # And the next parse still works.
        assert len(sd.parse_openclaw_skills(skills_dir=str(oc_dir))) == 1
        assert len(sd.parse_hermes_skills(skills_dir=str(hm_dir))) == 1
