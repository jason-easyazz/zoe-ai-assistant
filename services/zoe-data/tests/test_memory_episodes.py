"""memory_episodes: an episode is a turn-cluster POINTER (ids + times), never a copy of what was said.

Each rule has a negative control: the gap split is shown to fail at the boundary, a resolved episode loses a forgotten turn, and the
read is shown never to select ``content`` (a builder that did would fail the same assertion)."""
from __future__ import annotations

import asyncio
import dataclasses
import datetime

import pytest

import memory_episodes as me
from memory_episodes import TurnPointer

pytestmark = pytest.mark.ci_safe

T0 = datetime.datetime(2026, 10, 6, 9, 0, tzinfo=datetime.timezone.utc).timestamp()
MIN = 60.0


def _turns(*offsets_min: float, session: str = "s1") -> list:
    return [TurnPointer(f"m{i}", session, T0 + o * MIN) for i, o in enumerate(offsets_min)]


def test_turns_within_the_gap_are_one_episode_and_a_long_silence_starts_the_next():
    eps = me.build("member-a", _turns(0, 10, 30, 200, 210), gap=45 * MIN)
    assert [e.turn_ids for e in eps] == [("m0", "m1", "m2"), ("m3", "m4")]
    assert eps[0].started_at == T0 and eps[0].ended_at == T0 + 30 * MIN
    # negative control: the same turns with a gap that bridges the silence are ONE episode
    assert len(me.build("member-a", _turns(0, 10, 30, 200, 210), gap=200 * MIN)) == 1


def test_the_gap_boundary_is_inclusive():
    assert len(me.build("member-a", _turns(0, 45), gap=45 * MIN)) == 1
    assert len(me.build("member-a", _turns(0, 45.5), gap=45 * MIN)) == 2


def test_an_episode_holds_no_text_only_pointers():
    ep = me.build("member-a", _turns(0, 5))[0]
    fields = {f.name for f in dataclasses.fields(ep)}
    assert fields == {"id", "user_id", "turn_ids", "session_ids", "started_at", "ended_at"}
    assert set(ep.as_dict()) == fields | {"n_turns"}


def test_ids_are_stable_and_per_member():
    a = me.build("member-a", _turns(0, 5))[0]
    assert a.id == me.build("member-a", list(reversed(_turns(0, 5))))[0].id
    assert a.id != me.build("member-b", _turns(0, 5))[0].id


def test_order_and_duplicates_do_not_change_the_result():
    turns = _turns(0, 10, 20)
    assert me.build("member-a", turns + [turns[1]]) == me.build("member-a", turns)


def test_sessions_are_collected_in_order_of_first_use():
    turns = [TurnPointer("a", "s1", T0), TurnPointer("b", "s2", T0 + MIN), TurnPointer("c", "s1", T0 + 2 * MIN)]
    assert me.build("member-a", turns)[0].session_ids == ("s1", "s2")


def test_episode_of_finds_the_turn():
    eps = me.build("member-a", _turns(0, 10, 300), gap=45 * MIN)
    assert me.episode_of("m2", eps) is eps[1]
    assert me.episode_of("missing", eps) is None


def test_resolve_drops_a_forgotten_turn_and_a_fully_forgotten_episode_is_gone():
    ep = me.build("member-a", _turns(0, 5, 10))[0]
    assert me.resolve(ep, {"m0", "m1", "m2"}) is ep
    shrunk = me.resolve(ep, {"m1", "m2"})
    assert shrunk.turn_ids == ("m1", "m2") and shrunk.id == me.episode_id("member-a", "m1")
    assert me.resolve(ep, set()) is None
    # negative control: a build over a fresh read without the turn gives the same answer as resolve
    assert me.build("member-a", [t for t in _turns(0, 5, 10) if t.turn_id != "m0"])[0].turn_ids == shrunk.turn_ids


class _FakeDb:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, "", ()

    async def execute(self, sql, params):
        self.sql, self.params = sql, params
        rows = self.rows

        class _Cur:
            async def fetchall(self_inner):
                return rows

        return _Cur()


def test_the_read_selects_ids_and_times_never_content():
    db = _FakeDb([("m1", "s1", "2026-10-06 09:00:00.123+00"), ("m2", "s1", "2026-10-06T09:10:00Z"), ("m3", "s1", "not a time")])
    start = datetime.datetime(2026, 10, 6, tzinfo=datetime.timezone.utc)
    got = asyncio.run(me.load_turn_pointers("member-a", start, start + datetime.timedelta(days=1), db=db))
    select = db.sql.split(" FROM ")[0]
    assert "content" not in select and select == "SELECT cm.id, cm.session_id, cm.created_at"
    assert "cm.role = 'user'" in db.sql
    import memory_provenance

    assert memory_provenance.off_record_sql("cm") in db.sql                       # off-the-record turns are never pointed at
    assert db.params[0] == "member-a"
    assert [p.turn_id for p in got] == ["m1", "m2"]             # an unparseable time is skipped, never guessed
    assert got[0].at == pytest.approx(datetime.datetime(2026, 10, 6, 9, 0, 0, 123000, tzinfo=datetime.timezone.utc).timestamp())


def test_load_is_flag_gated_and_fails_open(monkeypatch):
    start = datetime.datetime(2026, 10, 6, tzinfo=datetime.timezone.utc)
    end = start + datetime.timedelta(days=1)
    db = _FakeDb([("m1", "s1", "2026-10-06T09:00:00Z")])
    monkeypatch.delenv(me.ENV, raising=False)
    assert asyncio.run(me.load("member-a", start, end, db=db)) == [] and db.sql == ""      # off: no read at all
    monkeypatch.setenv(me.ENV, "1")
    assert [e.turn_ids for e in asyncio.run(me.load("member-a", start, end, db=db))] == [("m1",)]

    class _Broken:
        async def execute(self, sql, params):
            raise RuntimeError("db down")

    assert asyncio.run(me.load("member-a", start, end, db=_Broken())) == []
