"""Day-sim 6n guard: the day-sim's OWN seed turns (``samantha_day_sim.SAY``) must keep the owner's retraction
("Good news: I no longer get the migraines since I switched to new glasses.") promotable to the owner's word.

Pure logic - no live API, no brain, no Postgres. The end-to-end digest proof is
``services/zoe-data/tests/test_owner_retraction_lands.py``; this pins the seed text itself so a reword of the sim
(or a stemmer edit) cannot silently turn 6n back into "setup not exercised: d2-migraine-neg (never landed)".
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import memory_authority as ma

pytestmark = pytest.mark.ci_safe

PERF = Path(__file__).resolve().parents[2] / "scripts" / "perf"


def _sim():
    sys.path.insert(0, str(PERF))
    spec = importlib.util.spec_from_file_location("samantha_day_sim", PERF / "samantha_day_sim.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["samantha_day_sim"] = mod
    spec.loader.exec_module(mod)
    return mod


SAY = _sim().SAY
HEALTH, NEG = SAY["d1-health"], SAY["d2-migraine-neg"]


@pytest.mark.parametrize("fact", [
    "User no longer gets migraines",
    "User no longer gets migraines since switching to new glasses",
    "User has stopped getting migraines since switching to new glasses",
    "User is no longer getting migraines since switching to new glasses",
])
def test_each_wording_of_the_retraction_earns_the_verbatim_promotion(fact):
    res = ma.resolve_write("turn_digest", fact, anchor_text=NEG, user_id="demo_bar_x")
    assert res.cls == ma.USER_STATED_DERIVED and res.promoted, fact


def test_the_seeded_migraine_row_is_promoted_too_so_equal_power_lets_the_retraction_retire_it():
    old = ma.resolve_write("turn_digest", "User has been getting migraines most afternoons lately",
                           anchor_text=HEALTH, user_id="demo_bar_x")
    assert old.promoted
    new = ma.resolve_write("turn_digest", "User has stopped getting migraines since switching to new glasses",
                           anchor_text=NEG, user_id="demo_bar_x")
    assert new.power >= old.power


def test_a_live_statement_still_does_not_support_the_retraction():
    assert not ma.supports("User no longer gets migraines", HEALTH)
    assert not ma.supports("User has stopped getting migraines since switching to new glasses", HEALTH)
