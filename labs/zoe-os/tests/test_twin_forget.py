"""Forgetting and the twin: the baseline is derived from a person's events, so a forget must
change or remove it. Planted sentinels, a negative control, and the twin's own window.
"""
from __future__ import annotations

import dataclasses
import glob

from twin_helpers import learn

from zoe_os.events import Kind
from zoe_os.replay import play
from zoe_os.retention import DAY, OPTIONS, Retention
from zoe_os.twin.baseline import NOT_ENOUGH, OFF, OK, Twin, describe
from zoe_os.twin.days import SAMPLE, generate

SENTINEL = "zx-sentinel-coral-bridge-4470"            # planted as the label of one of KID's routine steps
HOUSE = dataclasses.replace(SAMPLE, members=tuple(
    dataclasses.replace(m, routine=("teeth", SENTINEL)) if m.id == "kid" else m for m in SAMPLE.members))
ORDINARY = {i: "" for i in range(60)}


def taught(tmp_path, n=28, **kw):
    return learn(tmp_path, generate(HOUSE, days=n, seed=7, awkward=ORDINARY), **kw)


def on_disk(k) -> bytes:
    return b"".join(open(p, "rb").read() for p in glob.glob(k.path + "*"))


def known(twin, person="kid") -> str:
    return "\n".join(describe(twin.normal(person, "workday"))) + str(twin.days.get(person, ""))


def test_forgetting_a_person_removes_their_baseline_and_leaves_everyone_elses(tmp_path):
    k, twin, _ = taught(tmp_path)
    alex_before = twin.normal("alex", "workday")
    assert SENTINEL in known(twin) and SENTINEL.encode() in on_disk(k)        # positive control
    assert f"  morning/{SENTINEL}: done on 20 of 20 days" in describe(twin.normal("kid", "workday"))
    k.spine.forget("kid", actor="alex")                                       # the guardian asks
    k.pump()
    assert "kid" not in twin.days and twin.normal("kid", "workday").status == OFF
    assert SENTINEL not in known(twin) and SENTINEL.encode() not in on_disk(k)
    assert twin.normal("alex", "workday") == alex_before
    k.close()


def test_negative_control_a_twin_that_ignores_the_notice_still_knows_what_was_forgotten(tmp_path, monkeypatch):
    monkeypatch.setattr(Twin, "_forget", lambda self, notice: None)
    k, twin, _ = taught(tmp_path)
    k.spine.forget("kid", actor="alex")
    k.pump()
    assert SENTINEL.encode() not in on_disk(k)                                # the kernel forgot...
    assert SENTINEL in known(twin) and twin.normal("kid", "workday").status == OK    # ...the baseline did not


def test_forgetting_one_thing_changes_the_baseline_and_keeps_the_rest(tmp_path):
    k, twin, _ = taught(tmp_path)
    before = twin.normal("kid", "workday")
    gone = k.spine.forget("kid", actor="alex", entity=SENTINEL.replace("-", " ")).count
    k.pump()
    after = twin.normal("kid", "workday")
    assert gone == 28 and SENTINEL not in known(twin) and SENTINEL.encode() not in on_disk(k)
    assert after.status == OK and after.days == 20 and after.times == before.times
    assert set(before.steps) - set(after.steps) == {f"morning/{SENTINEL}"}
    assert after.steps["morning/teeth"] == (20, 20)
    k.close()


def test_a_forgotten_person_is_not_banned_new_days_start_a_new_baseline(tmp_path):
    days = generate(HOUSE, days=30, seed=7, awkward=ORDINARY)
    k, twin, now = learn(tmp_path, days[:28])
    k.spine.forget("kid", actor="alex")
    k.pump()
    for day in days[28:]:
        play(k, day, now)
    fresh = twin.normal("kid", "workday")
    assert (fresh.status, fresh.days) == (NOT_ENOUGH, 2) and list(twin.days["kid"]) == ["2030-04-01", "2030-04-02"]
    k.close()


# ---- the kernel's retention and the twin's own window ---------------------------------------------
def test_the_twins_reduced_copy_outlives_the_kernels_events_and_a_forget_still_reaches_it(tmp_path):
    k, twin, now = taught(tmp_path)
    now[0] += 40 * DAY
    swept = Retention(k.spine, OPTIONS["short"]).sweep()
    assert swept["expired"] > 500 and SENTINEL.encode() not in on_disk(k)     # the kernel's content is gone
    assert SENTINEL in known(twin)                                            # the twin's reduced copy is not
    k.spine.forget("kid", actor="alex")                                       # the skeletons still link to KID
    k.pump()
    assert SENTINEL not in known(twin) and "kid" not in twin.days
    assert [e.payload["subject"] for e in k.spine._fetch(0, frozenset({Kind.FORGET}), None, 10)] == ["kid"]
    k.close()


def test_HONEST_LIMIT_a_forget_by_name_after_the_events_aged_out_does_not_reach_the_twin(tmp_path):
    k, twin, now = taught(tmp_path)
    now[0] += 40 * DAY
    Retention(k.spine, OPTIONS["short"]).sweep()
    assert k.spine.forget("kid", actor="alex", entity=SENTINEL.replace("-", " ")).count == 0
    k.pump()
    assert SENTINEL in known(twin)                 # the kernel had no content left to match; the twin kept the label
    k.close()


def test_the_twins_window_is_its_retention_and_too_short_a_window_never_says_anything(tmp_path):
    k, twin, _ = taught(tmp_path / "28", n=35)                              # the decided window
    assert len(twin.days["alex"]) == 28 and twin.window_days == 28
    assert twin.normal("alex", "workday").days == 20 and twin.normal("alex", "weekend").days == 8
    k.close()
    k, twin, _ = taught(tmp_path / "14", twin=Twin(window_days=14))
    assert len(twin.days["alex"]) == 14 and min(twin.days["alex"]) == "2030-03-18"
    assert (twin.normal("alex", "workday").status, twin.normal("alex", "workday").days) == (OK, 10)
    assert twin.normal("alex", "weekend").status == NOT_ENOUGH               # 4 weekend days in 14: never 6
    k.close()
    k, twin, _ = taught(tmp_path / "13", twin=Twin(window_days=13))
    assert twin.normal("alex", "workday").status == NOT_ENOUGH               # 9 working days: silent for ever
    assert twin.expire("2030-06-01") == 3 * 13 and twin.days == {}           # nobody home: everything ages out
    k.close()
