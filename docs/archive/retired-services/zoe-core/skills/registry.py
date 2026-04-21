"""
Skills Registry
================

Phase 1a: Hot-reload registry with SHA-256 lockfile for change detection.

The registry maintains an in-memory cache of loaded skills and provides:
- Trigger-based lookup (Tier 3 in the intent pipeline)
- LLM context injection (Tier 4)
- Hot-reload with filesystem watching
- Lockfile-based integrity checking (skills.lock)

If a skill's SHA-256 doesn't match the lockfile, the skill is deactivated
until the user approves the change.
"""

import json
import os
import logging
import time
from typing import List, Optional, Dict
from datetime import datetime

from skills.loader import Skill, discover_all_skills

logger = logging.getLogger(__name__)

LOCKFILE_PATH = os.getenv("SKILLS_LOCKFILE", "/app/data/skills.lock")


class SkillsRegistry:
    """In-memory registry of active skills with hot-reload support."""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}
        self._trigger_index: Dict[str, str] = {}  # trigger_keyword -> skill_name
        self._last_reload: float = 0
        self._lockfile: Dict[str, dict] = {}

    def load(self):
        """Load all skills and build trigger index."""
        self._lockfile = self._read_lockfile()
        all_skills = discover_all_skills()

        self._skills.clear()
        self._trigger_index.clear()

        for skill in all_skills:
            # Check lockfile integrity
            if skill.name in self._lockfile:
                expected_hash = self._lockfile[skill.name].get("sha256")
                if expected_hash and expected_hash != skill.sha256:
                    logger.warning(
                        f"Skill {skill.name} hash changed! "
                        f"Expected: {expected_hash[:16]}... Got: {skill.sha256[:16]}... "
                        f"DEACTIVATING until user approves."
                    )
                    skill.active = False

            self._skills[skill.name] = skill

            # Build trigger index (only for active skills)
            if skill.active:
                for trigger in skill.triggers:
                    if trigger in self._trigger_index:
                        existing = self._trigger_index[trigger]
                        logger.debug(
                            f"Trigger '{trigger}' already mapped to {existing}, "
                            f"overriding with {skill.name} (higher precedence)"
                        )
                    self._trigger_index[trigger] = skill.name

        self._last_reload = time.time()

        # Auto-approve new skills (first load) by writing lockfile
        self._write_lockfile()

        active = sum(1 for s in self._skills.values() if s.active)
        inactive = len(self._skills) - active
        logger.info(
            f"Skills registry loaded: {active} active, {inactive} deactivated, "
            f"{len(self._trigger_index)} triggers indexed"
        )

    def match_triggers(self, message: str) -> Optional[Skill]:
        """Match a message against skill triggers (Tier 3).

        Returns the first matching skill or None.
        Matching is exact prefix/keyword match -- no fuzzy or semantic.
        """
        message_lower = message.lower().strip()

        for trigger, skill_name in self._trigger_index.items():
            # Check if message starts with or contains the trigger keyword
            if trigger in message_lower:
                skill = self._skills.get(skill_name)
                if skill and skill.active:
                    logger.info(f"Tier 3 skill match: '{trigger}' -> {skill.name}")
                    return skill

        return None

    def get_llm_context(self, message: str = "") -> str:
        """Build skill context for LLM injection (Tier 4).

        Returns a formatted string listing available skills and their
        instructions for the LLM to use when determining how to respond.
        """
        if not self._skills:
            return ""

        active_skills = [s for s in self._skills.values() if s.active]
        if not active_skills:
            return ""

        lines = ["## Available Skills\n"]
        for skill in sorted(active_skills, key=lambda s: -s.priority):
            lines.append(f"### {skill.name}: {skill.description}")
            lines.append(f"Triggers: {', '.join(skill.triggers)}")
            lines.append(f"Endpoints: {', '.join(skill.allowed_endpoints)}")
            lines.append(skill.instructions)
            lines.append("")

        return "\n".join(lines)

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name."""
        return self._skills.get(name)

    def get_all_skills(self) -> List[Skill]:
        """Get all loaded skills."""
        return list(self._skills.values())

    def approve_skill(self, name: str) -> bool:
        """Approve a deactivated skill (after hash change or new addition).

        Reactivates the skill and updates the lockfile with the new hash.
        """
        skill = self._skills.get(name)
        if not skill:
            return False

        skill.active = True
        self._lockfile[name] = {
            "sha256": skill.sha256,
            "approved_at": datetime.utcnow().isoformat() + "Z"
        }
        self._write_lockfile()

        # Rebuild trigger index
        for trigger in skill.triggers:
            self._trigger_index[trigger] = skill.name

        logger.info(f"Skill {name} approved and reactivated")
        return True

    def _read_lockfile(self) -> Dict[str, dict]:
        """Read the skills.lock file."""
        try:
            if os.path.exists(LOCKFILE_PATH):
                with open(LOCKFILE_PATH, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read skills lockfile: {e}")
        return {}

    def _write_lockfile(self):
        """Write the skills.lock file with current hashes."""
        try:
            # Ensure data directory exists
            os.makedirs(os.path.dirname(LOCKFILE_PATH), exist_ok=True)

            lockdata = {}
            for name, skill in self._skills.items():
                if skill.active:
                    lockdata[name] = {
                        "sha256": skill.sha256,
                        "approved_at": self._lockfile.get(name, {}).get(
                            "approved_at",
                            datetime.utcnow().isoformat() + "Z"
                        )
                    }

            with open(LOCKFILE_PATH, "w") as f:
                json.dump(lockdata, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to write skills lockfile: {e}")


# Singleton instance
skills_registry = SkillsRegistry()
