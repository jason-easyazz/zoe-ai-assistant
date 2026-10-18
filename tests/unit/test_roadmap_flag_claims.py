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
def inventory() -> dict:
    return json.loads((_REPO / "docs" / "knowledge" / "flag-inventory.json").read_text())


@pytest.fixture(scope="module")
def built(inventory) -> dict:
    """The full derivation exactly as ``live_state.py`` writes it (reader shapes included)."""
    return live_state.build(inventory, live_state.reader_kinds(_REPO, inventory))


@pytest.fixture(scope="module")
def derived(built) -> dict:
    return built["flags"]


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
    # a default of 1/0 is on/off ONLY for a flag read as a bool ...
    assert n("'0'", "bool") == "off" and n("'1'", "bool") == "on"
    # ... Python bools and words that cannot be numbers are unambiguous whatever the reader is
    assert n("False") == "off" and n("True") == "on" and n("'true'") == "on" and n("'no'") == "off" and n("'off'", "number") == "off"
    assert n("''") == "unset" and n("-") == "unset" and n("'Shadow'") == "shadow"
    assert n("dynamic") == "dynamic" and n("(required)") == "required"


def test_numeric_default_stays_numeric_unless_the_flag_is_read_as_a_bool():
    n = live_state.normalise_default
    for kind in ("number", "unknown", "str"):
        assert n("'1'", kind) == "1" and n("0", kind) == "0" and n("1.0", kind) == "1.0", kind
    # negative control: the pre-fix behaviour (every 1/0 is a switch) is exactly what "bool" does, so the assertions above can fail
    assert n("'1'", "bool") == "on" and n("0", "bool") == "off"


# -- the flags the review caught: integers recorded as "on" -----------------------------------------------------------------------

_INT_FLAGS = {
    "ZOE_ROUTER_SELFTRAIN_HOUR": ["1"],
    "ZOE_SMART_TURN_THREADS": ["1"],
    "ZOE_PIPELINE_VERIFY_EVIDENCE_RETRY_LIMIT": ["1"],
    "ZOE_VAD_CLEAN_TAIL_MS": ["0"],
}


def test_integer_flags_are_recorded_as_integers(derived):
    for name, want in _INT_FLAGS.items():
        assert derived[name]["defaults"] == want and derived[name]["kind"] == "number", (name, derived[name])


def test_real_switches_are_still_on_off(derived):
    """Negative control for the test above: the fix must not have turned every 1/0 into a literal."""
    assert derived["ZOE_EXPERT_ENABLED"]["defaults"] == ["on"] and derived["ZOE_EXPERT_ENABLED"]["kind"] == "bool"  # in ("1", ...)
    assert derived["ZOE_BUFFER_PHRASES"]["defaults"] == ["on"]  # not in {"0", "false", ...}
    assert derived["ZOE_WEB_SEARCH_TOOL"]["defaults"] == ["off"]


def test_no_numeric_default_is_on_or_off_without_a_bool_reader(derived, inventory):
    """The invariant over the whole inventory, so a new int flag cannot regress: a flag not read as a bool never says on/off for a
    default the code spelled as a bare 0/1."""
    prod = inventory["flags"]["prod"]
    bad = []
    for name, rec in derived.items():
        if rec["kind"] == "bool":
            continue
        for raw in prod[name]["defaults"]:
            if raw.strip("'\"") in ("0", "1") and live_state.normalise_default(raw, rec["kind"]) in ("on", "off"):
                bad.append(name)
    assert bad == []


def test_committed_live_state_is_exactly_the_derivation(built):
    committed = (_REPO / "docs" / "knowledge" / "live-state.json").read_text()
    assert committed == live_state.render(built), "docs/knowledge/live-state.json is stale - run python3 tools/audit/live_state.py"


# -- reader-shape classification, on a throwaway repo so each shape is pinned independently --------------------------------------

_SHAPES = '''\
import os
from env_typed import env_bool, env_int


def _wrapped(name, default):
    return env_int(name, default)


A_INT_CALL = int(os.environ.get("ZOE_T_INT_CALL", "1"))
B_BOOL_CMP = os.environ.get("ZOE_T_BOOL_CMP", "1").strip().lower() in ("1", "true", "yes")
C_BOOL_NEG = (os.environ.get("ZOE_T_BOOL_NEG", "1") or "1").strip().lower() not in {"0", "false", "no"}
D_ENV_INT = env_int("ZOE_T_ENV_INT", 0)
E_ENV_BOOL = env_bool("ZOE_T_ENV_BOOL", True)
F_WRAPPED = _wrapped("ZOE_T_WRAPPED_INT", 1)
G_MODE = os.environ.get("ZOE_T_MODE", "off").lower() == "enforce"
H_UNKNOWN = print(os.environ.get("ZOE_T_UNKNOWN", "1"))
I_MODE_NUM = os.environ.get("ZOE_T_MODE_NUM", "1") == "enforce"


def alias():
    raw = os.environ.get("ZOE_T_ALIAS_INT", "1")
    return int(raw) + 1


def truthy():
    if os.environ.get("ZOE_T_TRUTHINESS", "0"):
        return True
'''


@pytest.fixture(scope="module")
def shapes(tmp_path_factory):
    repo = tmp_path_factory.mktemp("shapes")
    (repo / "svc.py").write_text(_SHAPES)
    inv = live_state._load_flag_inventory().scan_repo(repo, files=["svc.py"])
    return live_state.build(inv, live_state.reader_kinds(repo, inv))["flags"]


def test_reader_shapes_decide_whether_one_is_a_switch(shapes):
    want = {
        "ZOE_T_INT_CALL": ("number", ["1"]),
        "ZOE_T_ENV_INT": ("number", ["0"]),
        "ZOE_T_WRAPPED_INT": ("number", ["1"]),
        "ZOE_T_ALIAS_INT": ("number", ["1"]),
        "ZOE_T_BOOL_CMP": ("bool", ["on"]),
        "ZOE_T_BOOL_NEG": ("bool", ["on"]),
        "ZOE_T_ENV_BOOL": ("bool", ["on"]),
        # no positive bool evidence -> the literal, never a guess
        "ZOE_T_UNKNOWN": ("unknown", ["1"]),
        "ZOE_T_TRUTHINESS": ("unknown", ["0"]),
        "ZOE_T_MODE_NUM": ("str", ["1"]),
        "ZOE_T_MODE": ("str", ["off"]),
    }
    assert {n: (shapes[n]["kind"], shapes[n]["defaults"]) for n in want} == want


def test_control_without_reader_kinds_no_numeric_default_becomes_a_switch(inventory):
    """Pre-fix behaviour was 'every 1/0 is a switch'; with no reader information the builder must now refuse to guess."""
    blind = live_state.build(inventory)["flags"]
    assert blind["ZOE_SMART_TURN_THREADS"]["defaults"] == ["1"]
    assert blind["ZOE_EXPERT_ENABLED"]["defaults"] == ["1"], "a real switch is not recognised without the reader shape"
