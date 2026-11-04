"""scripts/perf/continuity_cells.py: continuity cells C1 (thread carry), C3 (stale serve), C4 (re-telling) on a simulated clock.

Each cell must discriminate: a system built to break exactly that cell turns exactly that cell red, and the perfect system turns none.
A scorer that cannot go red measures nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "perf"))
import continuity_cells as cc  # noqa: E402

pytestmark = pytest.mark.ci_safe

T = {t.key: t for t in cc.THREADS}


def test_the_instrument_controls_all_go_the_declared_way():
    assert cc.run_controls() == []


def test_perfect_memory_passes_every_cell_with_full_carry():
    r = cc.run(cc.PerfectMemory())
    assert r["verdict"] == {"C1": "PASS", "C3": "PASS", "C4": "PASS"}
    assert all(h["rate"] == 1.0 for h in r["cells"]["C1"]["by_horizon"].values())
    assert r["cells"]["C1"]["by_horizon"]["30d"]["n"] == 12   # 4 live + 4 dormant + 4 resolved probed at 30 d


def test_amnesiac_fails_carry_and_is_counted_as_retelling():
    r = cc.run(cc.Amnesiac())
    assert r["verdict"]["C1"] == "FAIL" and r["verdict"]["C4"] == "FAIL"
    assert r["cells"]["C4"]["rate"] == 1.0


def test_control_a_silent_non_answer_is_not_retelling():
    r = cc.run(cc.Silent())
    assert r["verdict"]["C1"] == "FAIL" and r["cells"]["C4"]["rate"] == 0.0


def test_stale_server_fails_only_the_stale_cell():
    r = cc.run(cc.StaleServer())
    assert r["verdict"] == {"C1": "PASS", "C3": "FAIL", "C4": "PASS"}
    stale = {p["thread"] for p in r["probes"] if p["stale"]}
    assert stale == {"D1", "D2", "D3", "D4", "R1", "R2", "R3", "R4"}


def test_decaying_memory_loses_the_long_horizons_only():
    h = cc.run(cc.DecayingMemory(keep_days=3))["cells"]["C1"]["by_horizon"]
    assert (h["1d"]["rate"], h["7d"]["rate"], h["30d"]["rate"]) == (1.0, 0.0, 0.0)


def test_the_timeline_orders_tells_before_probes_and_uses_only_simulated_days():
    ev = cc.timeline()
    assert [e["day"] for e in ev] == sorted(e["day"] for e in ev)
    assert {e["day"] for e in ev} == {0, 1, 2, 3, 7, 30}
    d1 = [e for e in ev if e["thread"] == "D1"]
    assert [e["kind"] for e in d1] == ["tell", "tell", "probe"] and d1[-1]["day"] == 30
    assert cc.timeline() == ev   # deterministic: no clock, no randomness


# --- scorer unit cases, each with its negative control -----------------------------------------------------------------------------

def test_carry_needs_the_current_value_not_the_old_one():
    assert cc.score_carry(T["D1"], "It is in Harbourton now.")
    assert not cc.score_carry(T["D1"], "It is in Marlow.")


def test_a_resolved_thread_carries_by_being_known_to_be_done():
    assert cc.score_carry(T["R1"], "That's filed and sorted.")
    assert not cc.score_carry(T["R1"], "I'm not sure which one you mean.")


@pytest.mark.parametrize("reply,stale", [
    ("It is Marlow.", True),                                          # old value alone
    ("It is Harbourton now, no longer Marlow.", False),               # new value + superseding word
    ("Marlow or Harbourton, one of those.", True),                    # both, nothing says which is current
    ("Nothing on that.", False),                                      # silence is not stale
])
def test_stale_serve_on_a_changed_thread(reply, stale):
    assert cc.score_stale(T["D1"], reply) is stale


@pytest.mark.parametrize("reply,stale", [
    ("You still need to file the tax paperwork.", True),
    ("The tax paperwork is filed and sorted.", False),
    ("You still need to check, but it is filed.", False),             # open words beside a done word: not asserted as open
])
def test_stale_serve_on_a_resolved_thread(reply, stale):
    assert cc.score_stale(T["R1"], reply) is stale


def test_a_live_thread_cannot_be_stale():
    assert cc.score_stale(T["L1"], "It is sage, still need to finish.") is False


@pytest.mark.parametrize("reply,retold", [
    ("What was it again?", True),
    ("Tell me about the garden shed.", False),                         # no question mark: not a question
    ("The colour was sage, what else do you need?", False),           # holds the needle
    ("OK.", False),
])
def test_retelling_is_a_question_for_a_slot_already_given(reply, retold):
    assert cc.score_retell(T["L1"], reply) is retold


def test_wilson_interval_is_sane_and_wide_for_small_n():
    lo, hi = cc.wilson(12, 12)
    assert hi == 1.0 and 0.7 < lo < 0.8
    assert cc.wilson(0, 0) == (0.0, 1.0)
    lo0, hi0 = cc.wilson(0, 12)
    assert lo0 == 0.0 and 0.2 < hi0 < 0.3


def test_cli_selftest_exits_zero_and_dry_run_prints_the_plan(capsys):
    assert cc.main(["--dry-run"]) == 0
    assert "12 threads" in capsys.readouterr().out
    assert cc.main(["--selftest"]) == 0


def test_selftest_goes_red_when_a_control_is_neutered(monkeypatch):
    """The self-test itself must be able to fail: make the 'stale server' serve fresh values and run_controls must complain."""
    class Fresh(cc.PerfectMemory):
        pass
    monkeypatch.setattr(cc, "StaleServer", Fresh)
    assert any("stale server" in p for p in cc.run_controls())
