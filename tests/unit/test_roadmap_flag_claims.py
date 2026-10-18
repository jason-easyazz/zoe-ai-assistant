"""docs/ROADMAP.md may not contradict the flag DEFAULTS the code ships.

The roadmap writes a claim about a code default as ``[default ZOE_NAME=value]``. This test derives every flag's default from
``docs/knowledge/flag-inventory.json`` (itself extracted from the code) through ``tools/audit/live_state.py`` and fails when a
claim names an unknown flag, names a flag whose default is not statically known, or disagrees with the derived default. It
deliberately verifies DEFAULTS only: what is ON in the running processes is not in the repo, so no claim form for live values
exists and none is checked (``scripts/maintenance/zoe_ground_truth.sh`` reads those).

Negative controls below prove the checker can fail: a wrong value, an unknown flag, and a dynamic default each go red. Without
them a green run would only show that nobody wrote a claim.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("live_state", _REPO / "tools" / "audit" / "live_state.py")
live_state = importlib.util.module_from_spec(_spec)
sys.modules["live_state"] = live_state
_spec.loader.exec_module(live_state)

CLAIM = re.compile(r"\[default (ZOE_[A-Z0-9_]+)=([A-Za-z0-9_.:-]+)\]")


def check_claims(text: str, flags: dict) -> list[str]:
    """Every problem with the ``[default ZOE_X=v]`` claims in ``text``, given ``flags`` = live_state ``flags``."""
    problems = []
    for name, value in CLAIM.findall(text):
        rec = flags.get(name)
        if rec is None:
            problems.append(f"{name}: not a production flag in the inventory")
            continue
        defaults = rec["defaults"]
        if "dynamic" in defaults or "required" in defaults:
            problems.append(f"{name}: default is {defaults}, not statically known - state it in prose, not as a claim")
            continue
        if not live_state.compatible(value, defaults):
            problems.append(f"{name}: ROADMAP claims default {value!r} but the code ships {defaults}")
    return problems


@pytest.fixture(scope="module")
def derived() -> dict:
    inv = json.loads((_REPO / "docs" / "knowledge" / "flag-inventory.json").read_text())
    return live_state.build(inv)["flags"]


def test_roadmap_exists_with_the_three_horizons():
    text = (_REPO / "docs" / "ROADMAP.md").read_text()
    for heading in ("## Now", "## Next", "## Later"):
        assert heading in text, f"ROADMAP.md lost its {heading!r} section"
    now = text.split("## Now", 1)[1].split("## Next", 1)[0]
    nxt = text.split("## Next", 1)[1].split("## Later", 1)[0]
    assert len(re.findall(r"^\d+\. ", now, re.M)) <= 5, "Now holds at most 5 items"
    assert len(re.findall(r"^\d+\. ", nxt, re.M)) <= 10, "Next holds at most 10 items"


def test_roadmap_flag_claims_match_code_defaults(derived):
    text = (_REPO / "docs" / "ROADMAP.md").read_text()
    assert CLAIM.findall(text), "ROADMAP.md carries no [default ZOE_X=value] claim at all - the check would be vacuous"
    assert check_claims(text, derived) == []


def test_committed_live_state_agrees_with_the_inventory_for_every_claimed_flag(derived):
    """The generated file is what readers open; for the flags the roadmap cites it must say what the inventory says."""
    committed = json.loads((_REPO / "docs" / "knowledge" / "live-state.json").read_text())
    assert committed["kind"] == "code-defaults"
    text = (_REPO / "docs" / "ROADMAP.md").read_text()
    for name, _ in CLAIM.findall(text):
        assert committed["flags"][name]["defaults"] == derived[name]["defaults"], (
            f"{name}: docs/knowledge/live-state.json is stale - run python3 tools/audit/live_state.py"
        )


# -- negative controls: the checker must be able to fail ------------------------------------------------------------------------


def test_control_a_wrong_default_is_caught(derived):
    flags = {"ZOE_RISK_CUES": derived["ZOE_RISK_CUES"]}
    assert flags["ZOE_RISK_CUES"]["defaults"] == ["off"], "fixture assumption: ZOE_RISK_CUES ships off"
    assert check_claims("[default ZOE_RISK_CUES=off]", flags) == []
    assert check_claims("[default ZOE_RISK_CUES=enforce]", flags), "a claim that contradicts the default must fail"


def test_control_an_unknown_flag_is_caught(derived):
    assert check_claims("[default ZOE_NO_SUCH_FLAG_XYZ=off]", derived)


def test_control_a_dynamic_default_cannot_be_claimed():
    flags = {"ZOE_X": {"defaults": ["dynamic"], "readers": 1}}
    assert check_claims("[default ZOE_X=on]", flags)


def test_control_off_accepts_an_empty_default_but_a_mode_does_not():
    flags = {"ZOE_Y": {"defaults": ["unset"], "readers": 1}}
    assert check_claims("[default ZOE_Y=off]", flags) == []
    assert check_claims("[default ZOE_Y=shadow]", flags)


def test_default_normalisation():
    n = live_state.normalise_default
    assert n("'0'") == "off" and n("False") == "off" and n("'1'") == "on" and n("True") == "on"
    assert n("''") == "unset" and n("-") == "unset" and n("'Shadow'") == "shadow"
    assert n("dynamic") == "dynamic" and n("(required)") == "required"
