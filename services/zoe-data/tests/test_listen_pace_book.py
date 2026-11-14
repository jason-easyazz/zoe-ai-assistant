"""Listen pace: persistence, the per-turn token, and the narrow calls the voice path will make (services/zoe-data/listen_pace.py).
Synthetic ids only; files live in tmp_path; the module-level book is in memory under pytest."""
from __future__ import annotations

import json
import os
import stat

import pytest

import listen_pace as lp

pytestmark = pytest.mark.ci_safe

T0 = 1_800_000_000.0
CLEAN = dict(rule="deep", win="wake", still_talking=0, talk_chunks=0, obs_ms=1500, muted_ms=0, close_ms=640, quiet_ms=720,
             fall="clean", reflex=0, frozen="-")


def body(tok, **kw):
    return dict(CLEAN, token=tok, **kw)


def test_state_round_trips_and_a_bad_entry_is_dropped_alone():
    s = lp.State(level=-2, run=7, ring="0102", after_lengthen=True, bad=((0, T0),), last_step_at=T0, pin="longer",
                 pin_until=T0 + 9, quicker_guard=3, eligible=50, events_half=4, last_heard_at=T0)
    text = lp.dumps({"panel-a|*": s, "panel-a|member-1": lp.new_state(child=True)})
    back = lp.loads(text)
    assert back["panel-a|*"] == s and back["panel-a|member-1"].floor == lp.CHILD_FLOOR
    raw = json.loads(text)
    raw["subjects"]["panel-a|*"]["level"] = 9
    raw["subjects"]["bad|key|x"] = lp.state_to_dict(s)
    raw["subjects"]["panel-b|*"] = dict(lp.state_to_dict(s), ring="0x")
    assert set(lp.loads(json.dumps(raw))) == {"panel-a|member-1"}


@pytest.mark.parametrize("text", ["", "{", "[]", '{"v": 2, "subjects": {}}', '{"v": 1, "subjects": []}', "null"])
def test_a_malformed_file_is_an_empty_book(text):
    assert lp.loads(text) == {}


def test_bounded_keeps_the_most_recently_heard():
    many = {f"panel-{i}|*": lp.State(last_heard_at=T0 + i) for i in range(lp.MAX_SUBJECTS + 20)}
    kept = lp.loads(lp.dumps(many))
    assert len(kept) == lp.MAX_SUBJECTS and "panel-0|*" not in kept and f"panel-{lp.MAX_SUBJECTS + 19}|*" in kept


def test_save_is_atomic_0600_and_loads_back(tmp_path):
    path = str(tmp_path / "d" / "listen-pace.json")
    assert lp.save(path, {"panel-a|*": lp.State(level=1)})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert lp.load(path)["panel-a|*"].level == 1
    assert [p.name for p in (tmp_path / "d").iterdir()] == ["listen-pace.json"]   # no temp file left behind
    assert lp.load(str(tmp_path / "missing.json")) == {}


def test_the_frame_carries_the_panel_level_and_the_member_level_only_when_carried():
    b = lp.Book()
    f = b.frame("panel-a", None, now=T0)
    assert f["panel"] == {"deep": 640, "quiet": 800, "clean": 0, "level": 0} and "next" not in f and f["apply"] is False
    f2 = b.frame("panel-a", "member-1", now=T0, apply=True)
    assert f2["next"]["level"] == 0 and f2["next"]["ttl_s"] == lp.WINDOW_TTL_S and f2["apply"] is True
    assert "member-1" not in json.dumps(f2)                             # the panel never learns who
    assert b.frame("bad|panel", None, now=T0) is None


def test_a_token_is_one_shot_and_expires():
    b = lp.Book()
    tok = b.frame("panel-a", None, now=T0)["token"]
    assert b.record(body(tok), now=T0 + 1) is not None
    assert b.record(body(tok), now=T0 + 2) is None                    # reused
    tok2 = b.frame("panel-a", None, now=T0)["token"]
    assert b.record(body(tok2), now=T0 + lp.TOKEN_TTL_S) is None      # expired
    assert b.record(body("nope"), now=T0) is None
    tok3 = b.frame("panel-a", None, now=T0)["token"]
    assert b.record(body(tok3, win="barge"), now=T0 + 1) is None
    tok4 = b.frame("panel-a", None, now=T0)["token"]
    assert b.record(body(tok4, still_talking="maybe"), now=T0 + 1) is None
    assert b.states() == {"panel-a|*": b.states()["panel-a|*"]}


def test_wake_outcomes_teach_the_household_window_outcomes_the_member():
    b = lp.Book()
    for i in range(40):                                                 # shadow: every outcome is an L0 observation
        tok = b.frame("panel-a", "member-1", now=T0 + i)["token"]
        b.record(body(tok), now=T0 + i)
    assert b.states()["panel-a|*"].level == -1 and "panel-a|member-1" not in b.states()
    for i in range(2):
        tok = b.frame("panel-a", "member-1", now=T0 + 100 + i)["token"]
        b.record(body(tok, win="followup", still_talking=1, talk_chunks=5), now=T0 + 100 + i)
    st = b.states()
    assert st["panel-a|member-1"].level == 0                           # seeded from the household (-1), then lengthened
    assert st["panel-a|*"].level == -1


def test_the_book_persists_and_reloads(tmp_path):
    path = str(tmp_path / "listen-pace.json")
    b = lp.Book(path)
    tok = b.frame("panel-a", None, now=T0)["token"]
    b.record(body(tok, still_talking=1, talk_chunks=4), now=T0)
    assert lp.Book(path).states()["panel-a|*"].events_half == lp.HALF


def test_spoken_override_member_household_and_guest():
    b = lp.Book()
    assert b.say("panel-a", "member-1", "longer", now=T0).reason == "pin_longer"
    assert b.states()["panel-a|member-1"].level == 2
    assert b.say("panel-a", None, "quicker", now=T0).reason == "pin_quicker"
    assert b.states()["panel-a|*"].level == -1
    assert b.say("panel-a", None, "longer", now=T0, guest=True) is None
    assert b.states()["panel-a|*"].level == -1


def test_forget_user_removes_the_member_everywhere_and_keeps_the_household(tmp_path):
    path = str(tmp_path / "listen-pace.json")
    b = lp.Book(path)
    b.say("panel-a", "member-1", "longer", now=T0)
    b.say("panel-b", "member-1", "longer", now=T0)
    b.say("panel-a", None, "quicker", now=T0)
    assert b.forget_user("member-1") == 2
    assert set(lp.load(path)) == {"panel-a|*"}
    assert "member-1" not in open(path).read()


def test_the_narrow_calls_are_no_ops_while_off(monkeypatch):
    monkeypatch.delenv("ZOE_LISTEN_PACE", raising=False)
    assert lp.done_frame_field("panel-a", "member-1", now=T0) is None
    assert lp.record_outcome(body("x"), now=T0) is None
    monkeypatch.setenv("ZOE_LISTEN_PACE", "shadow")
    f = lp.done_frame_field("panel-a", None, now=T0)
    assert f["apply"] is False
    assert lp.record_outcome(body(f["token"]), now=T0 + 1) == "none"
    monkeypatch.setenv("ZOE_LISTEN_PACE", "on")
    assert lp.done_frame_field("panel-a", None, now=T0)["apply"] is True
    assert lp.book().path is None                                      # under pytest the live file is never touched
    assert lp.forget_user("member-9") == 0
