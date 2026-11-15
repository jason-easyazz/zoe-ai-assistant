"""The replay harness: a recorded day in, a deterministic, diffable report out."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from replay_skills import EagerLeavingCatch, PushyHeadsUp
from world import ALEX, KID, NOW, SAM, TOMORROW_0630, emit, household, presence, step

from zoe_os.events import Kind
from zoe_os.replay import Recording, diff, record, replay
from zoe_os.skills.calendar_headsup import CalendarHeadsUp
from zoe_os.skills.leaving_catch import LeavingCatch

LAB = Path(__file__).parent.parent
ANSWERS = {SAM: {"calendar-heads-up": "accepted"}, ALEX: {"*": "declined"}}


def a_day(tmp_path) -> Recording:
    """A synthetic day on a real lab kernel, recorded. Times step one minute per event."""
    clock = [NOW]
    k = household(tmp_path)
    k.spine._clock = lambda: clock[0]

    def at(minutes: int) -> None:
        clock[0] = NOW + 60 * minutes

    at(0), step(k, SAM, "gym bag")
    at(1), step(k, SAM, "water bottle"), step(k, SAM, "water bottle", done=True)
    at(5), presence(k, SAM, "left")                               # sure: an offer
    at(9), step(k, ALEX, "laptop")
    at(12), presence(k, ALEX, "left", confidence=0.5)             # not sure it was ALEX: silence
    at(30), emit(k, Kind.CALENDAR_CHANGE, "calendar", SAM, event_id="e1", change="created",
                 starts_at=TOMORROW_0630)
    at(31), emit(k, Kind.CALENDAR_CHANGE, "calendar", ALEX, event_id="e2", change="created",
                 starts_at=TOMORROW_0630)
    at(40), emit(k, Kind.UTTERANCE, "voice", KID, text="synthetic words nobody reads")
    rec = record(k, answers=ANSWERS)
    k.close()
    return rec


GOLDEN = """\
# zoe-os replay report v1
# recording {digest}: 9 events, 3 people
# skills: calendar-heads-up, leaving-catch
2030-03-04 18:05:00 @5     OFFER    leaving-catch -> sam [card, unanswered] "You left without: gym bag"
2030-03-04 18:30:00 @8     OFFER    calendar-heads-up -> sam [card, accepted] "Tomorrow starts at 06:30. Move your alarm to 05:45?"
2030-03-04 18:30:00 @8     ACTION   calendar-heads-up alarm.move for sam [verified]
2030-03-04 18:30:00 @8     CLAIM    calendar-heads-up claims alarm.move for sam
2030-03-04 18:31:00 @9     OFFER    calendar-heads-up -> alex [card, declined] "Tomorrow starts at 06:30. Move your alarm to 05:45?"
# totals: offers=3 actions=1 refusals=0 claims=1 findings=0
# calendar-heads-up: offers=2 actions=1 refusals=0
# leaving-catch: offers=1 actions=0 refusals=0
"""


def test_a_recorded_day_replays_to_the_expected_report(tmp_path):
    rec = a_day(tmp_path)
    rec.save(tmp_path / "day.jsonl")
    loaded = Recording.load(tmp_path / "day.jsonl")
    assert loaded.dumps() == rec.dumps() and len(loaded.events) == 9
    assert all(e["kind"] != "offer_response" and e["source"] != "kernel" for e in loaded.events)
    report = replay(loaded, [LeavingCatch(), CalendarHeadsUp()])
    digest = report.lines[1].split()[2].rstrip(":")
    assert report.text == GOLDEN.format(digest=digest)
    assert report.counts == {"offers": 3, "actions": 1, "refusals": 0, "claims": 1, "findings": 0}


def test_the_same_recording_gives_the_same_bytes_in_another_process(tmp_path):
    a_day(tmp_path).save(tmp_path / "day.jsonl")
    spec = ["zoe_os.skills.leaving_catch:LeavingCatch", "zoe_os.skills.calendar_headsup:CalendarHeadsUp"]
    outs = set()
    for seed in ("0", "1", "4242"):                               # a different hash seed each time
        run = subprocess.run([sys.executable, "-B", str(LAB / "zoe_os/replay.py"),
                              str(tmp_path / "day.jsonl"), *spec], capture_output=True, cwd="/",
                             env={"PATH": os.environ["PATH"], "PYTHONHASHSEED": seed, "TZ": "Asia/Tokyo"})
        assert run.returncode == 0, run.stderr
        outs.add(run.stdout)
    assert len(outs) == 1
    here = replay(Recording.load(tmp_path / "day.jsonl"), [LeavingCatch(), CalendarHeadsUp()])
    assert outs == {here.text.encode()}                           # and the same as in this process


def test_two_versions_of_a_skill_print_exactly_what_changed(tmp_path):
    rec = a_day(tmp_path)
    current = replay(rec, [LeavingCatch(), CalendarHeadsUp()])
    assert diff(current, replay(rec, [LeavingCatch(), CalendarHeadsUp()])) == ""
    changed = replay(rec, [EagerLeavingCatch(), CalendarHeadsUp()])
    assert diff(current, changed) == """\
--- current
+++ changed
@@ -3,0 +4 @@
+2030-03-04 18:12:00 @7     OFFER    leaving-catch -> alex [card, declined] "You left without: laptop"
@@ -8 +9 @@
-# totals: offers=3 actions=1 refusals=0 claims=1 findings=0
+# totals: offers=4 actions=1 refusals=0 claims=1 findings=0
@@ -10 +11 @@
-# leaving-catch: offers=1 actions=0 refusals=0
+# leaving-catch: offers=2 actions=0 refusals=0
"""


def test_a_change_that_tries_to_act_without_a_yes_shows_up_as_refusals(tmp_path):
    rec = a_day(tmp_path)
    changed = replay(rec, [LeavingCatch(), PushyHeadsUp()])
    assert changed.counts["refusals"] == 2 and changed.counts["actions"] == 1     # the kernel held
    refused = [line for line in changed.lines if " REFUSAL " in line]
    assert refused[0].endswith("calendar-heads-up: act alarm.move: the person has not accepted an offer for this")
    out = diff(replay(rec, [LeavingCatch(), CalendarHeadsUp()]), changed)
    assert out.count("\n+2030") == 2 and "REFUSAL" in out


def test_the_command_line_prints_a_report_or_a_diff_and_its_exit_code_says_which(tmp_path):
    a_day(tmp_path).save(tmp_path / "day.jsonl")
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(LAB / "tests")}
    same = ["zoe_os.skills.leaving_catch:LeavingCatch"]
    base = [sys.executable, "-B", str(LAB / "zoe_os/replay.py"), str(tmp_path / "day.jsonl")]
    run = subprocess.run([*base, *same, "--against", *same], capture_output=True, text=True, env=env)
    assert (run.returncode, run.stdout) == (0, "no difference\n")
    run = subprocess.run([*base, *same, "--against", "replay_skills:EagerLeavingCatch"],
                         capture_output=True, text=True, env=env)
    assert run.returncode == 1 and run.stdout.startswith("--- current\n+++ changed\n")
    assert "leaving-catch -> alex" in run.stdout


def test_a_file_that_is_not_a_recording_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="not a recording"):
        Recording.loads('{"recording": 99, "people": []}\n')
