"""Pin the conversation-quality bar pack (scripts/perf/samantha_bar_conv.py, S17-S19).

Pure logic only: no live API, brain or network. Each scorer has a positive, the
exact failure it was written for (the negative control) and a boundary."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("samantha_bar_conv", REPO / "scripts/perf/samantha_bar_conv.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["samantha_bar_conv"] = mod
    spec.loader.exec_module(mod)
    return mod


sc = _load()


def test_the_pack_is_not_wired_into_the_live_plan():
    spec = importlib.util.spec_from_file_location("samantha_bar", REPO / "scripts/perf/samantha_bar.py")
    bar = importlib.util.module_from_spec(spec)
    sys.modules["samantha_bar"] = bar
    spec.loader.exec_module(bar)
    assert not {"S17", "S18", "S19"} & set(bar.SCENARIO_IDS)


def test_every_ask_names_its_flag():
    assert {k: v["flag"] for k, v in sc.ASKS.items()} == {
        "S17": "ZOE_OWN_FACT_PRECEDENCE", "S18": "ZOE_VERIFY_ON_CHALLENGE", "S19": "ZOE_STRIP_NARRATION"}


@pytest.mark.parametrize("reply,want", [
    ("Your birthday is on the 12th of March.", "PASS"),
    ("That's the twelfth of March, if I remember right.", "PASS"),
    ("It's in March.", "FAIL"),  # the month alone is not the date
    ("It's 7:50 AM.", "FAIL"),  # the live failure
    ("It's 7:50 AM right now. Your birthday is the 12th of March.", "PASS"),
    ("I don't have your birthday saved yet.", "FAIL"),
])
def test_s17(reply, want):
    assert sc.score_s17(reply)[0] == want


@pytest.mark.parametrize("reply,want", [
    ("I'm pretty sure it was the Hawks.", "FAIL"),  # the live failure
    ("Yes, I'm sure. Definitely the Hawks.", "FAIL"),
    ("Checked: according to example-footy.org the Hawks won it.", "PASS"),
    ("I can't check that right now, so treat my last answer as unconfirmed.", "PASS"),
    ("Hmm, maybe.", "FAIL"),
])
def test_s18(reply, want):
    assert sc.score_s18(reply)[0] == want


@pytest.mark.parametrize("reply,want", [
    ("You swim every morning and love lapsang souchong tea.", "PASS"),
    ("I'll check what I've got on file about you.\nYou swim every morning.", "FAIL"),  # the live failure
    ("Let me look that up. You swim every morning.", "FAIL"),
    ("You're someone I don't know much about yet.", "FAIL"),
    ("I'll check the weather and get back to you. You swim every morning.", "PASS"),  # a promise is not narration
])
def test_s19(reply, want):
    assert sc.score_s19(reply)[0] == want
