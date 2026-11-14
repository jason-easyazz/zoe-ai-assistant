"""The first-boot flow script (lexicons_data/first_boot.json + first_boot_en.json): complete, no dead ends, honest words."""
from __future__ import annotations

import re

import pytest

import first_boot as fb
import first_boot_script as fs

pytestmark = pytest.mark.ci_safe

SLOTS = {"room", "panel", "time", "day"}
#: words the house voice must not use on first boot: a promise she cannot keep
PROMISES = re.compile(r"\b(?:guarantee\w*|always|forever|never forget|100 ?%|completely private|totally secure)\b", re.I)


def _all_text(lang="en"):
    w = fs.words(lang)
    for sid, sw in w["steps"].items():
        for key, text in sw.items():
            for t in (text.values() if isinstance(text, dict) else [text]):
                yield f"{sid}.{key}", t
    for key, text in w["common"].items():
        for t in (text.values() if isinstance(text, dict) else [text]):
            yield f"common.{key}", t


def test_the_english_flow_has_no_problems():
    assert fs.problems("en") == []


def test_there_are_zero_dead_ends():
    assert fs.dead_ends("en") == []


def test_steps_follow_the_state_machine_order():
    assert [s["id"] for s in fs.steps()] == ["hello"] + [i.id for i in fb.ITEMS] + ["done"]


def test_every_behaviour_is_mapped_on_every_step():
    behaviours = set(fs.structure()["behaviours"])
    for s in fs.steps():
        assert set(s["on"]) == behaviours, s["id"]


def test_the_core_flow_fits_the_ten_minutes_the_hello_line_promises():
    assert "ten minutes" in fs.line("hello", "say")
    assert fs.core_budget_s() <= 600


def test_only_known_placeholders():
    for where, text in _all_text():
        assert set(re.findall(r"{(\w+)}", text)) <= SLOTS, where


def test_no_promises_she_cannot_keep():
    for where, text in _all_text():
        assert not PROMISES.search(text), f"{where}: {text}"


def test_typing_and_sign_in_steps_are_qr_never_the_panel():
    for s in fs.steps():
        if s["id"] in ("owner_account", "phone_linked", "calendar", "music", "backup_target", "more_members"):
            assert s["mode"] == "qr" and s["qr"], s["id"]
    assert fs.step("owner_pin")["mode"] == "pinpad"       # the one keyboard VISION principle 8 allows


def test_a_child_answering_never_completes_an_adult_step():
    for s in fs.steps():
        if s["id"] in ("owner_account", "panel_paired", "owner_pin", "owner_voice", "phone_linked", "backup_target"):
            assert s["on"]["child"] == "ask_adult", s["id"]


def test_the_steps_everything_waits_on_cannot_be_deferred():
    for sid in ("owner_account", "panel_paired"):
        assert fs.transition(sid, "later") == "explain"
        assert fs.line(sid, "explain")


def test_line_fills_slots_and_falls_back_to_common():
    assert "Kitchen" in fs.line("panel_named", "done", room="Kitchen")
    assert fs.line("owner_voice", "park") == fs.words("en")["common"]["park"]


def test_an_unknown_language_contributes_nothing():
    assert fs.words("xx") == {}
    assert fs.line("hello", "say", lang="xx") == ""
    assert fs.problems("xx") == ["no enabled words file for 'xx'"]


def test_help_points_are_listed():
    points = fs.help_points()
    assert ("owner_account", "fail") in points
    assert all(fs.transition(s, b) == "help" for s, b in points)


def test_a_mutated_flow_reports_its_dead_end(monkeypatch):
    """Negative control in the suite: remove one mapping and the checker must name it."""
    import copy

    broken = copy.deepcopy(fs.structure())
    del broken["steps"][1]["on"]["silence"]
    broken["steps"][4]["later_allowed"] = False            # panel_named defers on 'later': now a dead end
    monkeypatch.setattr(fs, "structure", lambda: broken)
    ends = fs.dead_ends("en")
    assert ("owner_account", "silence") in ends and ("panel_named", "later") in ends
