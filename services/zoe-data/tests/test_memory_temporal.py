"""Two timelines on every memory row (audit P2.1): the stated-validity parser, the half-open interval, invalidate-never-delete,
the history read ("where did I live before?") and ``search(as_of=...)``.

Pure parser tests plus the REAL ``MemoryService`` over the Zoe Memory Bench's in-memory store. Synthetic names only; no network,
no model, no Postgres (``ci_safe``). Every behaviour test names the break that turns it red.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path

import pytest

import memory_temporal as mt

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO.joinpath("scripts", "perf")))

from zmb import lab_driver, world  # noqa: E402
from zmb.arms.base import Turn  # noqa: E402
from zmb.arms.z0 import Z0Arm  # noqa: E402

NOW = dt.datetime(2026, 10, 6, 3, 0)
DEMO = "demo_bar_0a1b2c3d"


def _d(ts):
    return None if ts is None else dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def _v(span, fact=""):
    v = mt.parse_validity(span, fact, now=NOW)
    return _d(v.start), v.start_precision, _d(v.end), v.end_precision


# ── the parser: a stated event time, from the person's words only ─────────────────────

@pytest.mark.parametrize("span,fact,want", [
    ("User has lived in Bendigo since 2018.", "", ("2018-01-01", "year", None, "")),
    ("since March 2019 I have had a dog", "", ("2019-03-01", "month", None, "")),
    ("I moved to Perth in March 2022", "User lives in Perth.", ("2022-03-01", "month", None, "")),
    ("I moved last March", "User moved.", ("2026-03-01", "month", None, "")),
    ("I moved here 3 years ago", "", ("2023-01-01", "year", None, "")),
    ("I got the dog yesterday", "", ("2026-10-05", "day", None, "")),
    ("I've lived here for five years", "User lives here.", ("2021-01-01", "year", None, "")),
    ("since 3/5/2019 I have had a dog", "", ("2019-05-03", "day", None, "")),               # day first (the household)
    ("I have been vegetarian since 2020-02-14", "", ("2020-02-14", "day", None, "")),
    ("I am staying with my sister until June", "", (None, "", "2027-06-01", "month")),        # exclusive: it ends AS June begins
    ("I worked at Acme from 2015 to 2019", "", ("2015-01-01", "year", "2019-01-01", "year")),
    ("I left Acme in 2019", "User left Acme.", (None, "", "2019-01-01", "year")),
])
def test_a_stated_event_time_is_read_from_the_persons_words(span, fact, want):
    assert _v(span, fact) == want


@pytest.mark.parametrize("span,fact", [
    ("I live in Perth", ""),                                              # no phrase: the capture time stands
    ("I live in Perth in 2019", "User lives in Perth."),                  # a year with no start verb is not a start
    ("my dentist appointment is on 26 October 2026", "User's dentist appointment is on 26 October 2026."),
    ("I will move to Perth in March 2027", "User will move to Perth."),   # an intention
    ("I live in Bendigo since 2030", ""),                                 # a start in the future is not a validity
    ("I moved to 2019 Smith Street", ""),                                 # 'to <year>' with no 'from' is an address
    ("I am going away for 3 days", ""),                                   # a duration with no 'have been' is not a start
    ("", ""),
])
def test_no_event_time_without_a_stated_one(span, fact):
    assert not mt.parse_validity(span, fact, now=NOW)


def test_each_fact_in_one_turn_gets_its_own_phrase_and_a_pointer_back_binds_to_its_clause():
    span = "I live in Bendigo since 2015 and I work at Acme since 2020"
    assert _v(span, "User lives in Bendigo.")[0] == "2015-01-01"
    assert _v(span, "User works at Acme.")[0] == "2020-01-01"
    typed = "I live in Bendigo, I have been here since 2015"          # 'here' points back at the first clause
    assert _v(typed, "User lives in Bendigo, I have been here since 2015")[0] == "2015-01-01"
    assert not mt.parse_validity("I work at Acme since 2020", "User lives in Bendigo.", now=NOW)


def test_the_parser_never_raises_on_junk():
    for junk in ("since " * 500, "until " + "9" * 200, "\x00since 2018\x00", "in 99-99-9999 moved"):
        mt.parse_validity(junk, "User lives in X.", now=NOW)


# ── the interval ────────────────────────────────────────────────────────────────────

def test_the_interval_is_half_open_and_a_replaced_rows_end_is_its_successors_start():
    old = dict(status="superseded", valid_from=100.0, invalid_at=200.0)
    new = dict(status="approved", valid_from=200.0)
    assert mt.valid_at(old, 100.0) and mt.valid_at(old, 199.9) and not mt.valid_at(old, 200.0)   # [100, 200)
    assert mt.valid_at(new, 200.0) and not mt.valid_at(new, 199.9)                                # no gap, no overlap
    assert not mt.valid_at(dict(status="archived", valid_from=1.0), 5.0)                          # forgotten is not history
    assert not mt.valid_at(dict(status="pending", valid_from=1.0), 5.0)
    # a legacy superseded row (no invalid_at) is NOT claimed valid unless its successor's start bounds it
    legacy = dict(status="superseded", added_ts=100.0)
    assert not mt.valid_at(legacy, 150.0) and mt.valid_at(legacy, 150.0, successor_start=200.0)
    # a stated end closes an approved row too
    assert not mt.valid_at(dict(status="approved", valid_from=1.0, valid_until=50.0), 50.0)


def test_retire_never_ends_a_row_before_it_began_or_after_its_own_stated_end():
    old = dict(valid_from=500.0)
    assert mt.retire_fields(old, dict(valid_from=100.0), now=900.0)["invalid_at"] == 500.0       # clamp to its start
    assert mt.retire_fields(old, dict(valid_from=700.0), now=900.0)["invalid_at"] == 700.0
    assert mt.retire_fields(old, None, now=900.0) == dict(invalid_at=900.0, expired_at=900.0)
    assert mt.retire_fields(dict(valid_from=1.0, valid_until=600.0), dict(valid_from=700.0), now=900.0)["invalid_at"] == 600.0


def test_history_questions_are_questions_with_a_history_cue_and_never_statements():
    for q in ("where did I live before?", "where did I live before Perth", "what did I used to do?",
              "who was my dentist previously?", "do you remember my old address"):
        assert mt.is_history_question(q), q
    for q in ("where do I live", "I used to live in Perth", "what did I say about the dentist",
              "remember that I lived in Perth before", ""):
        assert not mt.is_history_question(q), q


def test_the_backfill_plan_is_pure_idempotent_and_touches_only_metadata():
    rows = [
        ("a", "User lives in X.", dict(status="superseded", added_ts=100.0, superseded_by_id="b")),
        ("b", "User lives in Y.", dict(status="approved", added_ts=200.0)),
        ("c", "User likes tea.", dict(status="archived", added_ts=50.0, reviewed_at="1970-01-01T00:01:40Z")),
        ("d", "User lives in Z.", dict(status="superseded", added_ts=10.0, superseded_by_id="gone")),
        ("e", "User has lived in W since 2018.", dict(status="approved", added_ts=1.79e9, valid_from=1.79e9,
                                                       source_excerpt="I have lived in W since 2018",
                                                       authority_class="user_stated")),
    ]
    plan = mt.backfill_plan(rows)
    ups = plan["updates"]
    assert ups["a"] == dict(valid_from=100.0, valid_from_basis="backfill", invalid_at=200.0)     # ends where b began
    assert ups["b"] == dict(valid_from=200.0, valid_from_basis="backfill")
    assert ups["c"]["invalid_at"] == 100.0 and "text" not in ups["c"]
    assert "invalid_at" not in ups["d"] and plan["counts"]["unbounded"] == 1                    # no findable successor
    assert plan["counts"]["restated"] == 1 and "e" not in ups                                    # a reviewed step, not applied
    applied = [(rid, t, dict(m, **ups.get(rid, dict()))) for rid, t, m in rows]
    assert not mt.backfill_plan(applied)["updates"]                                               # second run: nothing to do


def test_the_backfill_tool_is_dry_run_only_and_prints_counts_never_text(capsys):
    sys.path.insert(0, str(REPO.joinpath("scripts", "maintenance")))
    import memory_validity_backfill as tool
    rows = [dict(id="a", user_id="u", text="SECRET-TEXT-1", meta=dict(status="approved", added_ts=100.0)),
            dict(id="b", user_id="v", text="SECRET-TEXT-2", meta=dict(status="archived", added_ts=5.0))]
    out = tool.report(rows, "u")
    assert out["rows"] == 1 and out["valid_from"] == 1 and out["would_update"] == 1 and "SECRET" not in str(out)
    assert tool.main(["--palace", "/nonexistent"]) == 2                     # no --dry-run: refused, nothing read
    assert "--dry-run is required" in capsys.readouterr().err


# ── on the real service ─────────────────────────────────────────────────────────────

@pytest.fixture()
def arm():
    a = Z0Arm()
    a.reset(DEMO)
    try:
        yield a
    finally:
        a.close()


def _raw(arm, row):
    return arm.service._collection().rows[row["id"]][1]


def test_valid_from_is_the_event_time_the_owner_said_else_the_capture_time(arm):
    s = world.make_world().slots
    arm.ingest([Turn(f"I live in {s['home']}, I have been here since 2015", "owner_typed"),
                Turn("User has a cat named Pixel.", "owner_taught")])
    home = next(r for r in arm.stats()["rows"] if s["home"] in r["text"])
    assert _d(home["valid_from"]) == "2015-01-01"
    cat = next(r for r in arm.stats()["rows"] if "cat" in r["text"])
    assert cat["valid_from"] > 1.7e9                                        # no phrase: the capture time, 2026
    raw = _raw(arm, home)
    assert raw["valid_from_basis"] == "stated" and raw["valid_from_precision"] == "year"
    assert raw["added_ts"] > raw["valid_from"] and raw["added_ts"] > 1.7e9  # learned_at (added_ts) is NOT the event time


def test_a_model_written_row_never_gets_an_event_time(arm):
    s = world.make_world().slots
    arm.ingest([Turn(f"I live in {s['home']} since 2012", "system_writer", writer="turn_digest",
                     proposes=(f"User lives in {s['home']} since 2012",))])
    (row,) = [r for r in arm.stats()["rows"] if s["home"] in r["text"]]
    raw = _raw(arm, row)
    assert raw["valid_from_basis"] == "captured" and raw["valid_from"] == raw["added_ts"]


def test_the_replaced_row_is_kept_with_both_ends_and_its_end_is_the_new_rows_start(arm):
    s = world.make_world().slots
    arm.ingest([Turn(f"I live in {s['home_old']}", "owner_typed"), Turn(f"I live in {s['home']}", "owner_typed")])
    assert arm.run_conflict_pass()["superseded"] == 1
    old = next(r for r in arm.stats()["rows"] if s["home_old"] in r["text"])
    new = next(r for r in arm.stats()["rows"] if s["home"] in r["text"])
    assert old["status"] == "superseded" and old["invalid_at"] == new["valid_from"]
    assert _raw(arm, old)["expired_at"] >= old["invalid_at"]                 # the transaction time of the retirement
    assert old["text"] == f"User lives in {s['home_old']}"                   # kept, text intact


def test_a_stated_start_is_where_the_old_row_ends(arm):
    s = world.make_world().slots
    arm.ingest([Turn(f"I live in {s['home_old']}", "owner_typed"),
                Turn(f"I live in {s['home']}, I have been here since 2020", "owner_typed")])
    arm.run_conflict_pass()
    old = next(r for r in arm.stats()["rows"] if s["home_old"] in r["text"])
    new = next(r for r in arm.stats()["rows"] if s["home"] in r["text"])
    assert _d(new["valid_from"]) == "2020-01-01"
    # the old row was learned AFTER the stated change: its interval is empty, never inverted
    assert old["invalid_at"] == old["valid_from"]


def test_archive_stamps_invalid_at_and_keeps_the_row(arm):
    arm.ingest([Turn("User likes rowing.", "owner_taught")])
    rid = arm.stats()["rows"][0]["id"]
    asyncio.run(arm.service.review(rid, decision="archive", actor=DEMO))
    (row,) = arm.stats()["rows"]
    assert row["status"] == "archived" and row["invalid_at"] and row["text"] == "User likes rowing."


def test_a_history_question_gets_the_old_fact_labelled_and_a_plain_question_does_not(arm):
    s = world.make_world().slots
    arm.ingest([Turn(f"I live in {s['home_old']}", "owner_typed"), Turn(f"I live in {s['home']}", "owner_typed")])
    arm.run_conflict_pass()
    plain = [r["text"] for r in arm.recall("where do I live", 5)]
    assert plain == [f"User lives in {s['home']}"]                            # unchanged: no history unasked
    hist = [r["text"] for r in arm.recall(f"where did I live before {s['home']}", 5)]
    assert hist[0] == f"User lives in {s['home']}"                            # the current fact first
    assert hist[1].startswith("Before that") and s["home_old"] in hist[1]     # ... then the old, LABELLED
    # a limit of 1 is a limit of 1: history never inflates the packet
    assert len(asyncio.run(arm.service.search("where did I live before", user_id=arm._user, limit=1))) == 1
    # history=False is the write path's escape hatch
    off = asyncio.run(arm.service.search("where did I live before", user_id=arm._user, limit=5, history=False))
    assert not any(s["home_old"] in r.text for r in off)


def test_history_does_not_resurface_a_forgotten_name(arm):
    s = world.make_world().slots
    arm.ingest([Turn(f"I live in {s['home_old']}", "owner_typed"), Turn(f"I live in {s['home']}", "owner_typed")])
    arm.run_conflict_pass()
    assert any(s["home_old"] in r["text"] for r in arm.recall("where did I live before", 5))
    assert "forgotten" in arm.forget(s["home_old"]).lower()
    # the forget sweep reaches the replaced (superseded) row too: it is archived, and history cannot return it
    assert next(r for r in arm.stats()["rows"] if s["home_old"] in r["text"])["status"] == "archived"
    assert not any(s["home_old"] in r["text"] for r in arm.recall("where did I live before", 5))
    assert not any(s["home_old"] in r["text"] for r in arm.as_of("where did I live", "2999-01-01T00:00:00Z"))


def test_the_ledger_and_the_tombstone_hide_a_history_row_even_if_the_sweep_missed_it(arm):
    svc = lab_driver.load_service()
    svc.memory_tombstones.add(arm._user, "Marlowe")
    try:
        assert asyncio.run(arm.service._names_forgotten(arm._user, "User lives in Marlowe."))
        assert not asyncio.run(arm.service._names_forgotten(arm._user, "User lives in Perth."))
    finally:
        svc.memory_tombstones.clear_all(arm._user)


def test_as_of_reads_a_legacy_superseded_row_by_its_successor_and_refuses_an_unreadable_time(arm):
    import memory_service
    col = arm.service._collection()
    base = dict(user_id=arm._user, wing=arm._user, visibility="personal", memory_type="fact")
    col.upsert(ids=["o"], documents=["User lives in Aldgate."],
               metadatas=[dict(base, status="superseded", added_ts=1000.0, superseded_by_id="n")])     # a pre-stamp row
    col.upsert(ids=["n"], documents=["User lives in Brookvale."], metadatas=[dict(base, status="approved", added_ts=2000.0)])
    got = lambda ts: [r["text"] for r in arm.as_of("where do I live", ts)]                              # noqa: E731
    assert got("1970-01-01T00:25:00Z") == ["User lives in Aldgate."]                                     # 1500 s
    assert got("1970-01-01T00:33:20Z") == ["User lives in Brookvale."]                                   # 2000 s: [.., 2000)
    assert got("1970-01-01T00:01:00Z") == []                                                              # before either
    with pytest.raises(memory_service.MemoryServiceError):
        asyncio.run(arm.service.search("x", user_id=arm._user, as_of="last tuesday-ish"))


def test_breaking_the_stamp_turns_the_cells_red():
    """The instrument check: parser off (``event_time``) -> C4 red; history read off -> C2's labelled cell red; the
    replaced row deleted instead of invalidated -> C2 red."""
    from zmb import cells as cellmod, runner, spec
    cells = spec.load_cells()
    for control, cid in (("event_time", "C4.valid_from_is_event_time"), ("history", "C2.history_is_labelled")):
        cp = runner.control_pass([c for c in cells if c.id == cid], world.make_world(), frozenset([control]))
        assert cp["ok"] and cp["red"] == 1 and cp["green"] == [], (control, cp)
    # C2.history_read also names `invalidate` (with `history`): deleting the replaced row instead of invalidating it
    # leaves no history to read, and the cell is red with that switch alone
    w = world.make_world()
    c2 = next(c for c in cells if c.id == "C2.history_read").rendered(w)
    ok, broken = Z0Arm(), Z0Arm(off=frozenset(["invalidate"]))
    try:
        assert cellmod.run_cell(c2, w, ok).verdict == "PASS"
        assert cellmod.run_cell(c2, w, broken).verdict == "FAIL"
    finally:
        ok.close()
        broken.close()
