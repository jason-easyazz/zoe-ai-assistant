"""The lab's own checks (hand-run; labs are not CI-wired). ``python3 -m pytest -q labs/presence/test_lab.py``"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness  # noqa: E402
import household as hh  # noqa: E402


def test_traces_are_deterministic_and_complete():
    a, b = hh.generate(3, 7), hh.generate(3, 7)
    assert a.obs == b.obs and a.truth == b.truth
    assert all(len(v) == 3 * 1440 for v in a.truth.values())
    assert a.obs == sorted(a.obs, key=lambda o: o[0])


def test_scenarios_only_filter_families():
    tr = hh.generate(2, 1)
    today = tr.observations(harness.SCENARIOS["today"])
    phone = tr.observations(harness.SCENARIOS["phone"])
    assert set(today) <= set(phone) and len(phone) > len(today)
    assert not [o for o in today if o[1] in hh.HARDWARE + hh.PHONE]


def test_without_an_away_signal_nobody_ever_leaves():
    tr = hh.generate(7, 2)
    _, ev, *_ = harness.run(tr, harness.SCENARIOS["today"])
    assert not [e for e in ev if e.kind in ("left", "arrived")]


def test_the_phone_tracker_finds_departures():
    tr = hh.generate(7, 2)
    _, ev, conf, room, gate = harness.run(tr, harness.SCENARIOS["phone"])
    s = harness.score(tr, ev, conf, room, gate)
    assert s["adult_b"]["left_hit"] >= s["adult_b"]["left_n"] // 2
