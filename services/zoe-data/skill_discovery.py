"""skill_discovery.py — parse skill files from OpenClaw and Hermes.

Returns A2A v1.0 AgentSkill dicts:
  {"id": str, "name": str, "description": str, "inputModes": [...], "outputModes": [...]}

OpenClaw format support (3 variants):
  A: <!-- metadata.when: ... --> HTML comment
  B: ## When to Use prose section
  C: ## Trigger conditions section
  Case variation: skill.md (lowercase)

Hermes format support (2 levels):
  Category: DESCRIPTION.md with YAML frontmatter description:
  Sub-skill: <category>/<skill>/SKILL.md with YAML frontmatter name:/description:
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_OPENCLAW_SKILLS_DIR = os.path.expanduser("~/.openclaw/workspace/skills")
_HERMES_SKILLS_DIR = os.path.expanduser("~/.hermes/skills")

# Cache invalidation flags — set True by skills_watcher on file events
_openclaw_cache_dirty: bool = True
_hermes_cache_dirty: bool = True

_openclaw_cache: list[dict] = []
_hermes_cache: list[dict] = []


# ── OpenClaw ──────────────────────────────────────────────────────────────────

_METADATA_WHEN_RE = re.compile(
    r'<!--\s*metadata\.when:\s*(?P<desc>[^-]+?)(?:\s*-->|\Z)', re.I | re.S
)
_WHEN_TO_USE_RE = re.compile(
    r'##\s+When to Use\b.*?\n+(?P<lines>(?:[^\n]+\n?){1,10})', re.I
)
_TRIGGER_RE = re.compile(
    r'##\s+Trigger conditions?\b.*?\n+(?P<lines>(?:[^\n]+\n?){1,10})', re.I
)


def _parse_openclaw_skill(skill_dir: Path) -> dict | None:
    """Parse a single OpenClaw skill directory. Returns None if no description found."""
    # Case-insensitive: prefer SKILL.md, fall back to skill.md
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        skill_file = skill_dir / "skill.md"
    if not skill_file.exists():
        return None

    try:
        content = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    name = skill_dir.name.replace("-", " ").replace("_", " ").title()
    description = ""

    # Format A: metadata.when HTML comment
    m = _METADATA_WHEN_RE.search(content)
    if m:
        description = m.group("desc").strip()

    # Format B: ## When to Use section
    if not description:
        m = _WHEN_TO_USE_RE.search(content)
        if m:
            lines = [
                l.strip().lstrip("- ").strip()
                for l in m.group("lines").splitlines()
                if l.strip() and not l.startswith("#")
            ]
            description = "; ".join(lines[:3])

    # Format C: ## Trigger conditions section
    if not description:
        m = _TRIGGER_RE.search(content)
        if m:
            lines = [
                l.strip().lstrip("- ").strip()
                for l in m.group("lines").splitlines()
                if l.strip() and not l.startswith("#")
            ]
            description = "; ".join(lines[:3])

    # Fallback: use the first non-heading line of the file
    if not description:
        for line in content.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped and not stripped.startswith("<!--"):
                description = stripped[:150]
                break

    return {
        "id": skill_dir.name,
        "name": name,
        "description": description or f"OpenClaw skill: {skill_dir.name}",
        "inputModes": ["text"],
        "outputModes": ["text"],
    }


def parse_openclaw_skills(skills_dir: str = _OPENCLAW_SKILLS_DIR) -> list[dict]:
    """Return parsed OpenClaw skills, using cache unless dirty."""
    global _openclaw_cache_dirty, _openclaw_cache

    if not _openclaw_cache_dirty:
        return _openclaw_cache

    root = Path(skills_dir)
    if not root.is_dir():
        logger.warning("OpenClaw skills dir not found: %s", skills_dir)
        _openclaw_cache = []
        _openclaw_cache_dirty = False
        return _openclaw_cache

    skills = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        parsed = _parse_openclaw_skill(entry)
        if parsed:
            skills.append(parsed)

    logger.debug("Parsed %d OpenClaw skills from %s", len(skills), skills_dir)
    _openclaw_cache = skills
    _openclaw_cache_dirty = False
    return skills


# ── Hermes ────────────────────────────────────────────────────────────────────

_YAML_FRONT_MATTER_RE = re.compile(r'^---\s*\n(.*?)\n---', re.S)
_YAML_KV_RE = re.compile(r'^(?P<key>\w+)\s*:\s*(?P<val>.+)', re.M)


def _parse_yaml_frontmatter(content: str) -> dict:
    """Minimal YAML frontmatter parser — avoids full PyYAML for speed."""
    m = _YAML_FRONT_MATTER_RE.match(content)
    if not m:
        return {}
    result = {}
    for kv in _YAML_KV_RE.finditer(m.group(1)):
        key = kv.group("key").strip()
        val = kv.group("val").strip().strip('"').strip("'")
        result[key] = val
    return result


def _parse_hermes_category(category_dir: Path) -> list[dict]:
    """Parse one Hermes skill category — returns zero or more AgentSkill dicts."""
    skills = []
    seen_ids: set[str] = set()

    # Category description from DESCRIPTION.md
    desc_file = category_dir / "DESCRIPTION.md"
    category_desc = ""
    if desc_file.exists():
        try:
            content = desc_file.read_text(encoding="utf-8", errors="replace")
            fm = _parse_yaml_frontmatter(content)
            category_desc = fm.get("description", "")
        except OSError:
            pass

    # Sub-skill directories inside the category
    for sub in sorted(category_dir.iterdir()):
        if not sub.is_dir():
            continue
        skill_file = sub / "SKILL.md"
        if not skill_file.exists():
            continue
        try:
            content = skill_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        fm = _parse_yaml_frontmatter(content)
        skill_name = fm.get("name") or sub.name.replace("-", " ").replace("_", " ").title()
        description = fm.get("description") or category_desc or f"Hermes skill: {sub.name}"

        skill_id = f"{category_dir.name}/{sub.name}"
        if skill_id in seen_ids:
            continue
        seen_ids.add(skill_id)

        skills.append({
            "id": skill_id,
            "name": skill_name,
            "description": description[:200],
            "inputModes": ["text"],
            "outputModes": ["text"],
        })

    # If no sub-skills, expose the category itself as a skill
    if not skills and category_desc:
        category_name = category_dir.name.replace("-", " ").replace("_", " ").title()
        skills.append({
            "id": category_dir.name,
            "name": category_name,
            "description": category_desc[:200],
            "inputModes": ["text"],
            "outputModes": ["text"],
        })

    return skills


def parse_hermes_skills(skills_dir: str = _HERMES_SKILLS_DIR) -> list[dict]:
    """Return parsed Hermes skills, using cache unless dirty.

    Walks only one canonical root (``~/.hermes/skills/``) to avoid double-counting
    mirror paths. Deduplicates by skill id.
    """
    global _hermes_cache_dirty, _hermes_cache

    if not _hermes_cache_dirty:
        return _hermes_cache

    root = Path(skills_dir)
    if not root.is_dir():
        logger.warning("Hermes skills dir not found: %s", skills_dir)
        _hermes_cache = []
        _hermes_cache_dirty = False
        return _hermes_cache

    skills = []
    seen_ids: set[str] = set()

    for category in sorted(root.iterdir()):
        if not category.is_dir():
            continue
        for skill in _parse_hermes_category(category):
            if skill["id"] not in seen_ids:
                seen_ids.add(skill["id"])
                skills.append(skill)

    logger.debug("Parsed %d Hermes skills from %s", len(skills), skills_dir)
    _hermes_cache = skills
    _hermes_cache_dirty = False
    return skills


def invalidate_openclaw_cache() -> None:
    global _openclaw_cache_dirty
    _openclaw_cache_dirty = True


def invalidate_hermes_cache() -> None:
    global _hermes_cache_dirty
    _hermes_cache_dirty = True
