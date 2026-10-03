"""Pure scoring of scripts/perf/recall_evidence_probe.py (no network, no store)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "recall_evidence_probe", REPO / "scripts/perf/recall_evidence_probe.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["recall_evidence_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


probe = _load()
HEADER = "## What I know about you\n(authority rule)\n"
ON = (HEADER + "(Dates show when the user told you each note — use them for \"when\" "
      "questions; never guess a date that isn't shown.)\n"
      "- User's sister Marisol is flying in from Lisbon on Thursday (Wed 30 Sep, today) "
      "[mem:abcd1234] — you said: \"Just so you know, my sister Marisol is flying in from "
      "Lisbon on Thursday.\"")
OFF = HEADER + "- User's sister Marisol is flying in from Lisbon on Thursday [mem:abcd1234]"


def test_mode_is_read_from_the_packet():
    assert probe.observed_mode(ON) == "on"
    assert probe.observed_mode(OFF) == "off"
    assert probe.observed_mode(None) == probe.observed_mode("") == "unknown"


def test_packet_scoring_on_and_its_negative_control():
    assert probe.score_packet(ON) == {"bullet_found": True, "dated_today": True,
                                      "any_date": True, "quoted": True, "quoted_any": True}
    assert probe.score_packet(OFF) == {"bullet_found": True, "dated_today": False,
                                       "any_date": False, "quoted": False, "quoted_any": False}
    stale = ON.replace("today)", "8 days ago)")
    assert probe.score_packet(stale)["dated_today"] is False


def test_when_and_said_reply_scoring():
    assert probe.score_when("You told me earlier today.", "Wednesday")
    assert probe.score_when("You mentioned it on Wednesday.", "Wednesday")
    assert not probe.score_when("I'm not sure when you told me.", "Wednesday")
    assert probe.score_said("You said Marisol is flying in from Lisbon on Thursday.")
    assert not probe.score_said("You said Marisol is visiting.")


# The live 2026-09-30 packet shape (45db6119): two Marisol bullets from the one turn,
# both dated; the one distinct quote rode the turn-digest bullet, not the Lisbon one.
SIBLING = (HEADER + "(Dates show when the user told you each note — use them for \"when\" "
           "questions; never guess a date that isn't shown. Quote \"you said\" words only if "
           "asked what they said or if you're sure.)\n"
           "- Marisol: flying in from Lisbon on Thursday (Wed 30 Sep, today) [mem:bbbb2222]\n"
           "- User's sister is named Marisol (Wed 30 Sep, today) [mem:aaaa1111] — you said: "
           "\"Just so you know, my sister Marisol is flying in from Lisbon on Thursday.\"")


def test_verdict_is_the_users_experience():
    good = probe.score_packet(ON)
    assert probe.verdict("on", good, True, True) == "PASS"
    assert probe.verdict("on", good, False, True) == "FAIL"   # negative control: when
    assert probe.verdict("on", good, True, False) == "FAIL"   # negative control: said
    assert probe.verdict("off", probe.score_packet(OFF), False, False) == "BASELINE"
    # A dated packet without the instruction line means the mode is not what it seems.
    assert probe.verdict("off", {**probe.score_packet(OFF), "any_date": True}, False, False) == "ERROR"
    assert probe.verdict("unknown", good, True, True) == "ERROR"


def test_the_2026_09_30_run_passes_with_its_packet_kept_as_diagnostics():
    pkt = probe.score_packet(SIBLING)
    assert pkt == {"bullet_found": True, "dated_today": True, "any_date": True,
                   "quoted": False, "quoted_any": True}
    assert probe.verdict("on", pkt, True, True) == "PASS"  # was FAIL on packet.quoted
    # The Lisbon bullet is found by its FACT, never by a sibling's quote naming Lisbon.
    swapped = "\n".join([*SIBLING.splitlines()[:3], *reversed(SIBLING.splitlines()[3:])])
    assert probe.score_packet(swapped) == pkt


def test_live_run_needs_zoe_perf(monkeypatch, capsys):
    monkeypatch.delenv("ZOE_PERF", raising=False)
    assert probe.main([]) == 0
    assert "skipped" in capsys.readouterr().out
