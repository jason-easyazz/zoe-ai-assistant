"""Listen pace: persistence, the per-turn token, and the narrow calls the voice path will make (services/zoe-data/listen_pace.py).
Synthetic ids only; files live in tmp_path; the module-level book is in memory under pytest."""
from __future__ import annotations

import json
import os
import stat
import threading

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


def test_a_valid_token_recorded_after_the_flag_goes_off_changes_nothing(monkeypatch, tmp_path):
    """The off check, proven with a token that WOULD record: issued while the flag was on, still unexpired and unused."""
    path = str(tmp_path / "listen-pace.json")
    monkeypatch.setattr(lp, "_book", lp.Book(path))
    monkeypatch.setenv("ZOE_LISTEN_PACE", "shadow")
    tok = lp.done_frame_field("panel-a", None, now=T0)["token"]
    before = lp.book().states()
    monkeypatch.delenv("ZOE_LISTEN_PACE", raising=False)
    assert lp.record_outcome(body(tok, still_talking=1, talk_chunks=5), now=T0 + 1) is None
    assert lp.book().states() == before and not os.path.exists(path)          # nothing in memory, nothing on disk
    monkeypatch.setenv("ZOE_LISTEN_PACE", "shadow")                           # the same token was good all along
    assert lp.record_outcome(body(tok, still_talking=1, talk_chunks=5), now=T0 + 2) == "none"
    assert lp.book().states()["panel-a|*"].events_half == lp.HALF and os.path.exists(path)


# ── forgotten means forever: a forget that did not reach the disk is a failure, and no pending outcome outlives a forget ─────
def test_a_forget_whose_file_write_fails_raises_and_a_retry_erases(monkeypatch, tmp_path):
    path = str(tmp_path / "listen-pace.json")
    b = lp.Book(path)
    b.say("panel-a", "member-1", "longer", now=T0)
    b.say("panel-a", None, "quicker", now=T0)
    real_save = lp.save
    monkeypatch.setattr(lp, "save", lambda *_a, **_k: False)
    with pytest.raises(lp.ListenPaceEraseError):
        b.forget_user("member-1")
    assert "member-1" in open(path).read()                                    # (the failure the caller is now told about)
    monkeypatch.setattr(lp, "save", real_save)
    assert b.forget_user("member-1") == 0                                     # the retry rewrites the file
    assert "member-1" not in open(path).read() and set(lp.Book(path).states()) == {"panel-a|*"}   # and a restart agrees


def test_the_module_forget_passes_the_erase_failure_up(monkeypatch, tmp_path):
    path = str(tmp_path / "listen-pace.json")
    monkeypatch.setattr(lp, "_book", lp.Book(path))
    lp.book().say("panel-a", "member-1", "longer", now=T0)
    monkeypatch.setattr(lp, "save", lambda *_a, **_k: False)
    with pytest.raises(lp.ListenPaceEraseError):
        lp.forget_user("member-1")


async def test_member_delete_fails_closed_when_the_listen_pace_erase_is_not_durable(monkeypatch, tmp_path):
    from memory_service import MemoryService, MemoryServiceError

    path = str(tmp_path / "listen-pace.json")
    monkeypatch.setattr(lp, "_book", lp.Book(path))
    lp.book().say("panel-a", "member-1", "longer", now=T0)
    svc = MemoryService(data_dir="/nonexistent/zoe-test-listen-pace")
    monkeypatch.setattr(svc, "_list_ids_for_user", lambda uid: [])
    monkeypatch.setattr(svc, "_delete_audit_for_user_sync", lambda uid: 0)
    real_save = lp.save
    monkeypatch.setattr(lp, "save", lambda *_a, **_k: False)
    with pytest.raises(MemoryServiceError, match="listen-pace erasure failed"):
        await svc.delete_user("member-1", actor="admin", reason="rtbf")
    monkeypatch.setattr(lp, "save", real_save)
    await svc.delete_user("member-1", actor="admin", reason="rtbf")           # the caller's retry
    assert "member-1" not in open(path).read()


def test_a_forget_landing_inside_record_leaves_nothing_of_the_member(monkeypatch, tmp_path):
    """Deterministic: the forget is started from INSIDE record (at the outcome parse, the point after the token is consumed and
    before the state update). Whether it runs there or has to wait for record, the member must be gone afterwards."""
    path = str(tmp_path / "listen-pace.json")
    b = lp.Book(path)
    tok = b.frame("panel-a", "member-1", now=T0)["token"]
    real_parse = lp.outcome_from_fields
    forget = threading.Thread(target=b.forget_user, args=("member-1",))

    def parse_then_forget(m):
        forget.start()
        if not b._lock.locked():          # nothing holds the book: the forget runs to completion right here, in the gap
            forget.join(10)
        return real_parse(m)

    monkeypatch.setattr(lp, "outcome_from_fields", parse_then_forget)
    b.record(body(tok, win="followup", still_talking=1, talk_chunks=5), now=T0 + 1)
    forget.join(10)
    assert not forget.is_alive()
    assert "panel-a|member-1" not in b.states()
    assert "member-1" not in open(path).read() and "panel-a|member-1" not in lp.Book(path).states()


def test_a_token_issued_before_a_forget_never_writes_after_it(tmp_path):
    path = str(tmp_path / "listen-pace.json")
    b = lp.Book(path)
    tok = b.frame("panel-a", "member-1", now=T0)["token"]
    b.forget_user("member-1")
    assert b.record(body(tok, win="followup", still_talking=1, talk_chunks=5), now=T0 + 1) is None
    assert b.record(body(tok, still_talking=1, talk_chunks=5), now=T0 + 1) is None     # nor through its household half
    assert b.states() == {} and "member-1" not in open(path).read()


def test_a_child_turn_or_override_never_stores_below_the_floor_on_a_row_made_before_a_role_correction():
    b = lp.Book()
    b._states["panel-a|member-1"] = lp.State(level=-2)          # stored with the adult floor, before the role was corrected
    tok = b.frame("panel-a", "member-1", now=T0, child=True)["token"]
    b.record(body(tok, win="followup", still_talking=1, talk_chunks=5), now=T0 + 1)
    s = b.states()["panel-a|member-1"]
    assert s.floor == lp.CHILD_FLOOR and s.level >= lp.CHILD_FLOOR and s.events_half == lp.HALF
    b._states["panel-a|member-2"] = lp.State(level=-2)
    b.say("panel-a", "member-2", "quicker", now=T0, child=True)
    s2 = b.states()["panel-a|member-2"]
    assert s2.floor == lp.CHILD_FLOOR and s2.level == lp.CHILD_FLOOR
    b._states["panel-a|member-3"] = lp.State(level=-2)
    b.say("panel-a", "member-3", "quicker", now=T0)                   # an adult keeps its own floor
    assert b.states()["panel-a|member-3"].level == -3
