"""The design charter and its rule mirrors agree (audit row B11, 2026-11-16).

``docs/governance/ZOE_DESIGN_PRINCIPLES.md`` says its enforceable rules are mirrored in ``.cursor/rules/*.mdc`` and the legacy
``.cursorrules``, and its amendment process says a rule change updates both. Nothing read either side, so they drifted. Each
section 3 enforceable rule and section 6 hard rule has a stable key phrase (a regex) below: it must appear in the charter's own
section AND in at least one mirror, so one of THESE listed rules edited or dropped on one side fails until the other follows.

What it does not do: it checks only the rules listed below, by phrase, in that one direction. A rule ADDED to the charter or to
a mirror with no entry here is not noticed; adding a rule means adding its key phrase to this test in the same change.

Two ratchets, reviewed 2026-11-16, that may only shrink:
- ``UNMIRRORED``: charter rules no mirror carries yet.
- ``RETIRED_ROUTING``: mirror sentences that still send work to a retired system (Hermes and OpenClaw are retired: root
  AGENTS.md, "Engineering agents"). Pinned per file by count.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
CHARTER = REPO / "docs/governance/ZOE_DESIGN_PRINCIPLES.md"

# (charter section, rule id) -> key phrase, matched case-insensitively with whitespace collapsed.
RULES = {
    ("3", "no-unscoped-writes"): r"no unscoped writes",
    ("3", "per-user-calibration"): r"personality and trust calibration are per-user",
    ("3", "personal-scope-wall"): r"personal scope of user a is never visible to user b",
    ("6", "no-household-in-kernel"): r"no `home` / `family` / `household` concepts",
    ("6", "scope-from-day-one"): r"scope\b.{0,25}\bfrom day one",
    ("6", "credentials-per-user-scope"): r"credentials are keyed per user and per scope",
    ("6", "tool-allow-list"): r"tools register through an allow-list mechanism",
    ("6", "skills-are-files"): r"skills .{0,20}filesystem files\**\s*\(skill\.md-style\), discovered by the router",
    ("6", "non-jetson-core"): r"non-jetson hardware without surgery",
}
# Charter rules no mirror carries yet (2026-11-16). Add the mirror line, then drop the entry here.
UNMIRRORED = {"per-user-calibration", "personal-scope-wall"}

RETIRED_ROUTING = re.compile(r"\b(use|delegate to|prefer)\b[^.\n]{0,60}\b(hermes|openclaw)\b|\b(hermes|openclaw)-backed\b", re.I)
DISOWNED = re.compile(r"retire|never|deleted", re.I)   # "... (OpenClaw/Hermes are retirement targets - never destinations)"
# mirror file -> count of sentences still routing work to a retired system (2026-11-16). May only go down.
RETIRED_ROUTING_BASELINE = {
    ".cursor/rules/agentic-workflow.mdc": 4,   # "use Hermes" (delegation); "Hermes-backed" + "prefer the local Hermes skills";
                                               # "delegate to Hermes" (greptile loop)
}


def retired_routing(text: str) -> int:
    """Sentences that send work to Hermes/OpenClaw, not counting one that disowns it in the next few words."""
    return sum(1 for m in RETIRED_ROUTING.finditer(text) if not DISOWNED.search(text[m.end():m.end() + 40]))


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def charter_section(text: str, number: str) -> str:
    m = re.search(rf"^## {number}\. .*?(?=^## |\Z)", text, re.M | re.S)
    assert m, f"charter section {number} not found"
    return m.group(0)


def mirrors() -> dict[str, str]:
    files = sorted((REPO / ".cursor/rules").glob("*.mdc")) + [REPO / ".cursorrules"]
    return {str(p.relative_to(REPO)): p.read_text(encoding="utf-8") for p in files}


def rule_findings(charter: str, mirror_texts: dict[str, str]) -> tuple[list[str], set[str]]:
    """``(rules missing from their charter section, rules carried by no mirror)``."""
    missing, unmirrored = [], set()
    for (section, rid), phrase in RULES.items():
        if not re.search(phrase, _norm(charter_section(charter, section))):
            missing.append(f"section {section}: {rid}")
        if not any(re.search(phrase, _norm(t)) for t in mirror_texts.values()):
            unmirrored.add(rid)
    return missing, unmirrored


def test_every_charter_rule_is_in_the_charter_and_a_mirror():
    missing, unmirrored = rule_findings(CHARTER.read_text(encoding="utf-8"), mirrors())
    assert not missing, f"charter rule(s) no longer stated in their section (update RULES with the amendment): {missing}"
    assert unmirrored - UNMIRRORED == set(), f"charter rule(s) missing from every .mdc/.cursorrules mirror: {unmirrored - UNMIRRORED}"
    assert UNMIRRORED - unmirrored == set(), f"now mirrored - drop from UNMIRRORED: {UNMIRRORED - unmirrored}"


def test_mirrors_route_no_new_work_to_retired_systems():
    counts = {f: retired_routing(t) for f, t in mirrors().items()}
    counts = {f: n for f, n in counts.items() if n}
    assert counts == RETIRED_ROUTING_BASELINE, (
        f"retired-system routing in the mirrors changed: now {counts}, pinned {RETIRED_ROUTING_BASELINE} (remove the routing; "
        "lower the pin when one is fixed)")


def test_negative_control_an_edit_on_one_side_fails():
    charter, mirror_texts = CHARTER.read_text(encoding="utf-8"), mirrors()
    edited = charter.replace("Credentials are keyed per user and per scope", "Credentials are keyed per site")
    assert "section 6: credentials-per-user-scope" in rule_findings(edited, mirror_texts)[0]
    dropped = {f: t.replace("Credentials are keyed per user and per scope", "Credentials live in env") for f, t in mirror_texts.items()}
    assert "credentials-per-user-scope" in rule_findings(charter, dropped)[1]
    assert retired_routing("For agent delegation, use OpenClaw. It is fast.") == 1
    assert retired_routing("use the router or Zoe Agent (OpenClaw/Hermes are retirement targets - never destinations)") == 0
