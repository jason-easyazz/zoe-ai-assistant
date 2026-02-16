"""
Skills Loader
==============

Phase 1a: Parse SKILL.md files with YAML frontmatter.
Discovers skills from three locations with precedence:
  1. User skills (~/.zoe/skills/) -- highest
  2. Module skills (modules/{name}/skills/) -- middle
  3. Core skills (assistant/skills/) -- lowest

Skill files are Markdown with YAML frontmatter containing metadata,
triggers, allowed_endpoints, and instructions.
"""

import os
import re
import hashlib
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Base paths (relative to working directory /app in Docker)
CORE_SKILLS_DIR = os.getenv("CORE_SKILLS_DIR", "/app/skills")
MODULES_DIR = os.getenv("MODULES_DIR", "/app/modules")
USER_SKILLS_DIR = os.path.expanduser(os.getenv("USER_SKILLS_DIR", "~/.zoe/skills"))
MODULES_CONFIG = os.getenv("MODULES_CONFIG", "/app/config/modules.yaml")


@dataclass
class Skill:
    """Parsed skill definition."""
    name: str
    description: str
    version: str
    author: str
    api_only: bool
    triggers: List[str]
    allowed_endpoints: List[str]
    instructions: str          # The markdown body
    source: str                # "core", "module:{name}", "user"
    file_path: str             # Full path to SKILL.md
    sha256: str                # Hash for lockfile verification
    active: bool = True        # Whether the skill is active (can be disabled by lockfile mismatch)
    # Optional metadata
    tags: List[str] = field(default_factory=list)
    priority: int = 0          # Higher = checked first in trigger matching


def parse_skill_file(file_path: str, source: str = "core") -> Optional[Skill]:
    """Parse a SKILL.md file into a Skill object.

    Expected format:
        ---
        name: skill-name
        description: What this skill does
        version: 1.0.0
        author: zoe-team
        api_only: true
        triggers:
          - "keyword1"
          - "keyword2"
        allowed_endpoints:
          - "POST /api/some/endpoint"
          - "GET /api/other/endpoint"
        ---
        # Skill Title

        ## When to Use
        Instructions for the LLM...

    Args:
        file_path: Path to the SKILL.md file
        source: Origin of the skill ("core", "module:agent-zero", "user")

    Returns:
        Skill object or None if parsing fails
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Calculate SHA-256 hash
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Parse YAML frontmatter
        frontmatter_match = re.match(
            r"^---\s*\n(.*?)\n---\s*\n(.*)",
            content,
            re.DOTALL
        )

        if not frontmatter_match:
            logger.warning(f"Skill file {file_path} has no YAML frontmatter, skipping")
            return None

        yaml_str = frontmatter_match.group(1)
        markdown_body = frontmatter_match.group(2).strip()

        meta = yaml.safe_load(yaml_str)
        if not isinstance(meta, dict):
            logger.warning(f"Skill file {file_path} has invalid YAML frontmatter")
            return None

        # Validate required fields
        name = meta.get("name")
        if not name:
            logger.warning(f"Skill file {file_path} missing 'name' field")
            return None

        # Enforce api_only
        api_only = meta.get("api_only", True)
        if not api_only:
            logger.error(f"Skill {name} has api_only=false -- this is FORBIDDEN. Skipping.")
            return None

        # Build Skill object
        return Skill(
            name=name,
            description=meta.get("description", ""),
            version=meta.get("version", "0.0.1"),
            author=meta.get("author", "unknown"),
            api_only=True,  # Always enforce
            triggers=[t.lower().strip() for t in meta.get("triggers", [])],
            allowed_endpoints=meta.get("allowed_endpoints", []),
            instructions=markdown_body,
            source=source,
            file_path=file_path,
            sha256=sha256,
            tags=meta.get("tags", []),
            priority=meta.get("priority", 0),
        )

    except yaml.YAMLError as e:
        logger.error(f"YAML parse error in {file_path}: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to parse skill file {file_path}: {e}")
        return None


def scan_directory(base_dir: str, source: str = "core") -> List[Skill]:
    """Scan a directory for SKILL.md files.

    Expects structure: base_dir/{skill-name}/SKILL.md

    Args:
        base_dir: Directory to scan
        source: Origin label for found skills

    Returns:
        List of parsed Skill objects
    """
    skills = []
    base_path = Path(base_dir)

    if not base_path.exists():
        logger.debug(f"Skills directory {base_dir} does not exist (not an error)")
        return skills

    for skill_dir in sorted(base_path.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue

        skill = parse_skill_file(str(skill_file), source)
        if skill:
            skills.append(skill)
            logger.info(f"Loaded skill: {skill.name} (source: {source}, triggers: {skill.triggers})")

    return skills


def get_enabled_modules() -> List[str]:
    """Get list of enabled module names from modules.yaml."""
    try:
        if not os.path.exists(MODULES_CONFIG):
            return []
        with open(MODULES_CONFIG, "r") as f:
            config = yaml.safe_load(f)
        return config.get("enabled_modules", [])
    except Exception as e:
        logger.error(f"Failed to read modules config: {e}")
        return []


def discover_all_skills() -> List[Skill]:
    """Discover all skills from all sources.

    Scans in order (lowest to highest precedence):
    1. Core skills (assistant/skills/)
    2. Module skills (modules/{name}/skills/) for each enabled module
    3. User skills (~/.zoe/skills/)

    Higher-precedence skills with the same name override lower ones.

    Returns:
        Deduplicated list of skills, highest precedence wins
    """
    all_skills: Dict[str, Skill] = {}

    # 1. Core skills (lowest precedence)
    for skill in scan_directory(CORE_SKILLS_DIR, "core"):
        all_skills[skill.name] = skill

    # 2. Module skills (middle precedence)
    for module_name in get_enabled_modules():
        module_skills_dir = os.path.join(MODULES_DIR, module_name, "skills")
        for skill in scan_directory(module_skills_dir, f"module:{module_name}"):
            if skill.name in all_skills:
                logger.info(f"Module skill {skill.name} overrides core skill")
            all_skills[skill.name] = skill

    # 3. User skills (highest precedence)
    for skill in scan_directory(USER_SKILLS_DIR, "user"):
        if skill.name in all_skills:
            logger.info(f"User skill {skill.name} overrides {all_skills[skill.name].source} skill")
        all_skills[skill.name] = skill

    skills = list(all_skills.values())
    logger.info(f"Discovered {len(skills)} skills total (after deduplication)")
    return skills
