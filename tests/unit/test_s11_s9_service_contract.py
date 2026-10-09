"""S11 (ask-to-remember) and S9a / S9b (personalisation hop): the harness's scorers vs the service's own output.

The service-side behaviour is pinned in services/zoe-data/tests/test_ask_to_remember.py and
test_personalisation_hop.py (a separate lane, a separate process). This is the other half: what the live harness
would SEE. The replies and the packet section the pure service modules produce must score PASS, and the
flag-off / pre-feature behaviour (the brain says it will remember and stores nothing; generic advice with no
"Shape the answer by" section) must score FAIL - so a scorer edit cannot quietly accept the miss, and a service edit
cannot quietly drift from what the bar measures. Pure logic, no network. Synthetic data only.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "services" / "zoe-data"
PERF = REPO / "scripts" / "perf"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sb():
    sys.path.insert(0, str(PERF / ""))
    return _load("samantha_bar", PERF / "samantha_bar.py")


@pytest.fixture(scope="module")
def ds(sb):
    sys.path.insert(0, str(PERF))
    return _load("samantha_day_sim", PERF / "samantha_day_sim.py")


@pytest.fixture
def svc_mods(monkeypatch):
    monkeypatch.syspath_prepend(str(SERVICE))
    for name in ("ask_to_remember", "personalisation_hop", "typed_env"):
        sys.modules.pop(name, None)
    import ask_to_remember
    import personalisation_hop

    monkeypatch.delenv(ask_to_remember.ENV, raising=False)
    monkeypatch.delenv(personalisation_hop.ENV, raising=False)
    return ask_to_remember, personalisation_hop


# ── S11 ──────────────────────────────────────────────────────────────────────────────────────

def test_the_bars_asks_are_unmistakable_asks_to_the_service_parser(sb, svc_mods):
    atr, _ = svc_mods
    assert atr.parse(sb.SAY_REMEMBER).clause == "my favourite tea is lapsang souchong"
    assert atr.parse(sb.SAY_KEEP_IN_MIND).clause == "I can't stand coriander"
    assert atr.parse(sb.ASK_REMEMBERED).kind == "recall"
    assert atr.parse(sb.ASK_TEA) is None and atr.parse(sb.SAY_FORGET_THAT) is None     # not asks: the recall + forget paths


def test_the_services_replies_score_pass(sb, svc_mods):
    atr, _ = svc_mods
    for reply in (*atr._CONFIRM, atr.ALREADY):
        c = sb.short_confirmation(reply)
        assert c["short"] and c["confirms"] and not c["narrates"], reply
    recall = ("You asked me to remember: I can't stand coriander; and my favourite tea is lapsang souchong.")
    t = {"say_tea": atr._confirm("my favourite tea is lapsang souchong"),
         "say_keep": atr._confirm("I can't stand coriander"),
         "packet_after_say": "- my favourite tea is lapsang souchong",
         "packet_after_keep": "- I can't stand coriander\n- my favourite tea is lapsang souchong",
         "count_once": 6, "count_repeat": 6, "repeat_reply": atr.ALREADY,
         "tea_reply": "Your favourite tea is lapsang souchong.", "recall_reply": recall,
         "forget_reply": 'Done — I forgot: "I can\'t stand coriander".',
         "packet_final": "- my favourite tea is lapsang souchong"}
    assert sb.score_s11(t)[0] == "PASS"


def test_every_refusal_the_service_gives_is_not_a_confirmation(sb, svc_mods):
    atr, _ = svc_mods
    for refusal in (atr.UNVERIFIED, atr.NAME_IS_ACCOUNTS, atr.PRIVATE, atr.NOT_THEIR_WORDS, atr.DROPPED, atr.NO_STORE):
        assert "i'll remember" not in refusal.lower() and "got it" not in refusal.lower(), refusal


def test_flag_off_the_brain_says_it_will_and_stores_nothing_scores_fail(sb, svc_mods):
    atr, _ = svc_mods
    brain = ("Sure thing! I'll remember that your favourite tea is lapsang souchong, a lovely smoky cup. "
             "Anything else you would like me to remember?")
    assert not sb.short_confirmation(brain)["short"]
    t = {"say_tea": brain, "say_keep": brain, "packet_after_say": "", "packet_after_keep": "",
         "count_once": 4, "count_repeat": 4, "repeat_reply": brain, "tea_reply": "I don't know.",
         "recall_reply": "I'm not sure.", "forget_reply": "Okay.", "packet_final": ""}
    v, ev = sb.score_s11(t)
    assert v == "FAIL" and "no row" in ev["why"]


# ── S9a / S9b ────────────────────────────────────────────────────────────────────────────────

def _row(i, text):
    return SimpleNamespace(id=f"m{i}", text=text, metadata={})


def test_the_hop_section_for_the_day_sim_asks_carries_the_facts_the_scorer_looks_for(ds, svc_mods):
    _, hop = svc_mods
    rows = [_row(1, ds.SAY["d1-shift"]), _row(2, ds.SAY["d1-dog"]), _row(3, ds.SAY["d1-diet"])]
    sleep = hop.select(ds.ASK_SLEEP, rows)
    cold = hop.select(ds.ASK_COLD, rows)
    for aid, got, needle in (("S9a", sleep, ds.HOP_ASKS["S9a"][2]), ("S9b", cold, ds.HOP_ASKS["S9b"][2])):
        sec = got.section()
        assert hop.HEADING in sec and ds.HOP_HEADING in sec and needle in sec.lower(), aid
    assert [f.id for f in sleep.facts] == ["m1"] and [f.id for f in cold.facts] == ["m2"]


def test_a_reply_shaped_by_the_hop_scores_pass_and_the_generic_one_fails(ds):
    judge = lambda: ("PASS", "tailored")  # noqa: E731
    assert ds.score_personal("S9a", "Since you sleep during the day after night shifts, try blackout curtains.", judge)[0] == "PASS"
    assert ds.score_personal("S9a", "Keep a regular bedtime and avoid screens at night.", judge)[0] == "FAIL"
    assert ds.score_personal("S9b", "Layer up for the 6am walk with Juniper.", judge)[0] == "PASS"
    assert ds.score_personal("S9b", "Fine without a jacket, it's around 22 degrees.", judge)[0] == "FAIL"


def test_hop_flag_off_the_section_is_empty_so_the_bar_attributes_it(ds, svc_mods, monkeypatch):
    _, hop = svc_mods
    import asyncio
    monkeypatch.setenv(hop.ENV, "0")
    assert not asyncio.run(hop.build("demo_bar_0a1b2c3d", ds.ASK_SLEEP, svc=object()))   # never even reads the store


def test_s9_is_scored_without_a_card_in_the_default_mode(ds):
    for aid in ("S9a", "S9b"):
        needs = next(a["needs"] for a in ds.ASKS if a["id"] == aid)
        assert needs == "any" and ds.mode_covers("default", needs)
