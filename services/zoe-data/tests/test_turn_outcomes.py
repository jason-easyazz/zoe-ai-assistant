"""FAILURE CAPTURE (``turn_outcomes.py`` + its hooks in ``struggle_detector``): one outcome row per turn, failed turns proposed a route,
the owner confirms, ONLY confirmed pairs reach the router seam.

Every phrasing is invented (public repo). The detector and outcome stores are in-process; the seam tests use real SQLite + the real
migrations (0050, 0057, 0058).

Negative controls (the three the owner asked for, each a test below that went RED with its wall removed - run by hand 2026-10-19):
  * harness traffic never becomes a training example - ``test_control_harness_turn_never_trains``: make
    ``struggle_detector._traffic`` return household and the harness-bound row is failed and labelled (red);
  * a forgotten turn never becomes one - ``test_control_forgotten_turn_never_trains``: drop ``_erase_outcomes`` from ``erase_entity``
    and the confirmed example survives the forget (red); the seam's own forget re-check is pinned separately;
  * a correction without owner confirmation never trains - ``test_control_unconfirmed_never_trains``: let ``confirmed`` accept a
    ``proposed`` row AND ``confirmed_examples`` fall back to ``proposed_label`` (two layers: a proposed row has no ``label``) and the
    unconfirmed pair reaches the seam (red).
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import re
import sqlite3
import statistics
import time
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

pytestmark = pytest.mark.ci_safe

import db_pool
import struggle_detector as sd
import struggle_proposals as sp
import struggle_signals as sig
import traffic_class
import turn_outcomes as to
from db_pool import _Cursor, _ExecResult

SVC = Path(__file__).resolve().parents[1]
A, B = "member-a", "member-b"
VOICE = "voice-panel-hall-1a2b3c4d"


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    for env in ("ZOE_STRUGGLE_DETECT", "ZOE_STRUGGLE_WINDOW_S", "ZOE_HARNESS"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv(to.ENV, "on")
    monkeypatch.setenv("ZOE_VOICE_SAMPLE_DIR", str(tmp_path / "no-samples"))   # never list the real corpus
    sd.reset()
    sd.set_backend(sd.MemoryBackend())
    store = to.MemoryBackend()
    to.set_backend(store)
    yield store
    sd.reset()
    sd.set_backend(None)
    to.set_backend(None)


def item(text, reply="Done.", *, user=A, session="chat-s1", channel="chat", ts=None, panel=""):
    sid = session
    return (time.time() if ts is None else ts, channel, text, reply, user, sid, panel, False,
            sd._traffic(user, panel or sd._panel_from_session(sid)))


@contextlib.contextmanager
def as_traffic(cls="harness"):
    """Bind the turn's SERVER-derived traffic class (P-2: the request middleware does this in production)."""
    tok = traffic_class.bind(cls)
    try:
        yield
    finally:
        traffic_class.reset(tok)


async def feed(*turns):
    out = []
    for t in turns:
        out.extend(await sd.process(t))
    return out


def weather_note(text, sim=0.81):
    sd.note_route(text, {"domain": "weather", "score": sim, "scores": {"weather": sim},
                         "two_stage": {"domain": "weather", "head_conf": 0.97}}, two_stage_active=True)


async def failed_then_fixed(session=VOICE, user=A):
    """A forecast question the brain answered (the router said weather), then a re-ask the WEATHER fast path answered."""
    q1 = "will it be windy this afternoon"
    weather_note(q1)
    sd.note_brain(session, "flue", "ok")
    sd.note_tool(session, "recall_memory")
    t0 = time.time()
    await feed(item(q1, "Probably a little.", user=user, channel="voice", session=session, ts=t0))
    weather_note(q1, 0.9)                     # the same question again, and this time the weather fast path answers it
    sd.note_handler(session, user, "tier", "expert/weather")
    await feed(item(q1, "Today it is 24 degrees and windy.", user=user, channel="voice", session=session, ts=t0 + 8))


# ── the traffic class ────────────────────────────────────────────────────────

@pytest.mark.parametrize("user,panel,want", [
    (A, "hall", "household"),
    ("guest", "zoe-touch-pi", "household"),   # an unidentified voice at the real panel
    (A, "replay-harness", "probe"),
    ("demo_bar_ab12", "", "harness"),
    ("test-sec-b-1", "", "ci"),
    ("probe_x1", "", "probe"),
])
def test_traffic_class(user, panel, want):
    assert sd._traffic(user, panel) == want


def test_the_bound_server_class_applies():
    with as_traffic("probe"):
        assert sd._traffic(A, "hall") == "probe"
    assert sd._traffic(A, "hall") == "household", "a reset context is household again"


# ── the flag ─────────────────────────────────────────────────────────────────

async def test_off_writes_no_rows_and_keeps_no_notes(monkeypatch, fresh):
    monkeypatch.setenv(to.ENV, "off")
    sd.note_handler("s1", A, "tier", "calc/calculate")
    sd.note_tool("s1", "recall_memory")
    assert not sd._HANDLERS
    await failed_then_fixed()
    assert fresh.data == []
    assert sd.observe("chat", "hi", "Hello.", "demo_bar_ab12", "s9") is False
    assert sd._STATE.get("queue") is None, "off: a harness turn starts no worker"


def test_the_traffic_class_is_the_same_flag_on_or_off(monkeypatch):
    """P-2: the detector skips (or tags text-free) every non-household turn whatever ``ZOE_STRUGGLE_OUTCOMES`` says."""
    for mode in ("off", "on"):
        monkeypatch.setenv(to.ENV, mode)
        assert sd._traffic(A, "") == "household"
        assert sd._traffic(A, "replay-harness") == "probe"
        with as_traffic("harness"):
            assert sd._traffic(A, "") == "harness"


async def test_detect_off_beats_outcomes_on(monkeypatch, fresh):
    monkeypatch.setenv("ZOE_STRUGGLE_DETECT", "off")
    sd.note_handler("s1", A, "tier", "calc/calculate")
    assert not sd._HANDLERS


# ── what a row holds ─────────────────────────────────────────────────────────

async def test_a_fast_turn_records_route_handler_and_no_words(fresh):
    text = "what is seven times six"
    sd.note_route(text, {"domain": "chat", "score": 0.3})
    sd.note_handler("s1", A, "tier", "calc/calculate")
    await feed(item(text, "Seven times six is 42.", session="s1"))
    row = fresh.data[0]
    assert row["traffic"] == "household" and row["outcome"] == "did" and row["handler"] == "tier:calc/calculate"
    assert row["failed"] == 0 and row["summary"] == "" and row["label_status"] == ""
    assert row["route_source"] == "similarity" and row["latency_ms"] is not None


async def test_a_session_less_intent_note_joins_by_user(fresh):
    sd.note_handler("", A, "intent", "list_add")
    sd.note_handler("", B, "intent", "timer_set")
    await feed(item("add eggs to the shopping list", "Added eggs.", session="s1"))
    assert fresh.data[0]["handler"] == "intent:list_add"
    assert [h[4] for h in sd._HANDLERS] == ["timer_set"], "another user's note is left for that user's turn"


async def test_a_fallthrough_is_failed_with_words_tools_and_the_routers_label(fresh):
    q = "will it be windy this afternoon"
    weather_note(q)
    sd.note_brain(VOICE, "flue", "ok")
    sd.note_tool(VOICE, "recall_memory")
    sd.note_tool(VOICE, "web_search")
    await feed(item(q, "Probably a little.", channel="voice", session=VOICE))
    row = fresh.data[0]
    assert row["outcome"] == "fell_through" and row["failed"] == 1 and "FALLTHROUGH" in row["signals"]
    assert row["route_domain"] == "weather" and row["route_source"] == "two_stage" and row["route_conf"] == pytest.approx(0.97)
    assert row["tools"] == "recall_memory,web_search" and row["path_served"] == "brain:flue"
    assert row["summary"] == q and (row["proposed_label"], row["label_source"]) == ("weather", "router")


async def test_the_follow_up_that_worked_labels_the_failed_turn(fresh):
    await failed_then_fixed()
    first = min(fresh.data, key=lambda r: r["ts"])
    assert first["failed"] == 1 and (first["proposed_label"], first["label_source"]) == ("weather", "followup")
    assert first["label_status"] == "proposed" and first["summary"].startswith("will it be windy")
    second = max(fresh.data, key=lambda r: r["ts"])
    assert second["handler"] == "tier:expert/weather" and second["outcome"] == "did"


async def test_a_cant_do_and_an_error_are_failed(fresh):
    await feed(item("dim the porch lights to ten percent", "Sorry, I can't control that device.", session="s1"))
    sd.note_brain("s2", "flue", "fallback")
    await feed(item("how long is the drive to the coast", "I'm having trouble thinking right now.", session="s2"))
    outcomes = {r["session_id"]: (r["outcome"], r["failed"]) for r in fresh.data}
    assert outcomes == {"s1": ("cant_do", 1), "s2": ("error", 1)}


async def test_a_guest_failed_row_keeps_no_words_and_cannot_be_confirmed(fresh):
    await failed_then_fixed(session="voice-panel-hall-0000bbbb", user="guest")
    row = min(fresh.data, key=lambda r: r["ts"])
    assert row["failed"] == 1 and row["summary"] == "" and row["user_id"] == "guest"
    with pytest.raises(to.ReviewError):
        await to.decide(row["id"], "guest", "confirm", "weather")


async def test_a_verbose_answer_alone_is_not_a_failed_routing_turn(fresh):
    q = "what's the capital of chad"
    await feed(item(q, "N'Djamena is the capital. " * 40, session="s1"))
    row = fresh.data[0]
    assert "VERBOSE" in row["signals"] or row["signals"] == ""
    assert row["failed"] == 0 and row["summary"] == ""


# ── the owner's review ───────────────────────────────────────────────────────

async def test_review_shows_only_the_viewers_own_failed_turns(fresh):
    await failed_then_fixed(session=VOICE, user=A)
    await failed_then_fixed(session="voice-panel-den-9f9f9f9f", user=B)
    rep = await to.review(A)
    assert rep["pending"] == 1 and len(rep["items"]) == 1
    assert rep["items"][0]["proposed_label"] == "weather" and "windy" in rep["items"][0]["words"]
    other = min((r for r in fresh.data if r["user_id"] == B), key=lambda r: r["ts"])
    with pytest.raises(to.ReviewError, match="no such"):
        await to.decide(other["id"], A, "confirm")          # another member's row is not even acknowledged


async def test_confirm_relabel_and_reject(fresh):
    await failed_then_fixed()
    rid = (await to.review(A))["items"][0]["id"]
    with pytest.raises(to.ReviewError):
        await to.decide(rid, A, "confirm", "pizza")         # not a route
    out = await to.decide(rid, A, "confirm", "calendar")
    assert out["label_status"] == "confirmed" and out["label"] == "calendar" and out["label_source"] == "owner"
    out = await to.decide(rid, A, "reject")
    assert out["label_status"] == "rejected"


async def test_brief_line_counts_pending_only_when_on(monkeypatch, fresh):
    await failed_then_fixed()
    assert "1 of your requests" in (await to.brief_line(A) or "")
    monkeypatch.setenv(to.ENV, "off")
    assert await to.brief_line(A) is None


# ── the negative controls ────────────────────────────────────────────────────

async def test_control_harness_turn_never_trains(fresh):
    with as_traffic("harness"):
        await failed_then_fixed(session="replay-0123456789ab")
    assert fresh.data and all(r["traffic"] == "harness" for r in fresh.data)
    assert all(r["failed"] == 0 and r["summary"] == "" and r["label_status"] == "" for r in fresh.data)
    for r in fresh.data:
        with pytest.raises(to.ReviewError):
            await to.decide(r["id"], A, "confirm", "weather")
    assert await to.confirmed_examples() == []


async def test_control_forgotten_turn_never_trains(fresh):
    q = "will marlowe need an umbrella this afternoon"
    weather_note(q)
    sd.note_brain(VOICE, "flue", "ok")
    await feed(item(q, "Maybe.", channel="voice", session=VOICE))
    rid = fresh.data[0]["id"]
    await to.decide(rid, A, "confirm", "weather")
    assert [e["text"] for e in await to.confirmed_examples()] == [q]
    assert await sd.erase_entity(A, "Marlowe") >= 1
    assert await to.confirmed_examples() == []


async def test_control_forgotten_outcome_wording_is_struck_under_any_id(fresh, tmp_path, monkeypatch):
    """An outcome-only turn (no struggle_events row) that is forgotten still tombstones its WORDING in the retrain ledger: the same words
    under a NEW turn id (the entity shield released by a re-teach) are struck too. Control: drop ``extra_texts`` from ``_erase_outcomes``
    and the new turn's key is not tombstoned (red)."""
    import router_retrain_ledger as rrl

    monkeypatch.setenv("ZOE_ROUTER_RETRAIN_DIR", str(tmp_path / "rr"))
    q = "will marlowe need an umbrella this afternoon"
    weather_note(q)
    sd.note_brain(VOICE, "flue", "ok")
    await feed(item(q, "Maybe.", channel="voice", session=VOICE))
    old = fresh.data[0]["id"]
    await to.decide(old, A, "confirm", "weather")
    sd.get_backend().data.clear()        # outcome-ONLY: no struggle_events row to read the wording from
    assert await sd.erase_entity(A, "Marlowe") >= 1
    assert fresh.data == []
    sd.reset()
    weather_note(q)
    sd.note_brain(VOICE, "flue", "ok")
    await feed(item(q, "Maybe.", channel="voice", session=VOICE))
    new = fresh.data[0]["id"]
    assert new != old
    await to.decide(new, A, "confirm", "weather")
    got = await to.confirmed_examples()
    assert [e["text"] for e in got] == [q]
    rows = [{**e, "text_key": rrl.text_key(e["text"])} for e in got]
    assert rrl.tombstoned(rows) == rows, "the same words under a new turn id must stay out of training"


async def test_control_unconfirmed_never_trains(fresh):
    await failed_then_fixed()
    assert await to.confirmed_examples() == [], "a follow-up-proven label is still only a PROPOSAL"
    rid = (await to.review(A))["items"][0]["id"]
    await to.decide(rid, A, "reject")
    assert await to.confirmed_examples() == []
    await to.decide(rid, A, "confirm")
    got = await to.confirmed_examples()
    assert [(e["label"], e["label_source"], e["decided_by"]) for e in got] == [("weather", "followup", f"owner:{A}")]


async def test_a_chat_confirmation_trains_nothing(fresh):
    await failed_then_fixed()
    rid = (await to.review(A))["items"][0]["id"]
    await to.decide(rid, A, "confirm", "chat")
    assert await to.confirmed_examples() == []


async def test_delete_user_takes_the_rows(fresh):
    await failed_then_fixed()
    assert await sd.delete_user(A) >= 2
    assert fresh.data == []


async def test_retention_sweeps_the_rows(fresh, monkeypatch):
    await failed_then_fixed()
    for r in fresh.data:
        r["ts"] -= 100 * 86400.0
    sd._STATE["last_sweep"] = 0.0
    await sd._maybe_sweep(time.time())
    assert fresh.data == []


# ── cost on the turn ─────────────────────────────────────────────────────────

def test_note_overhead_is_tiny():
    """What the TURN pays for the capture: one ``note_handler`` (a tier / intent / Skybridge) and one ``note_tool`` per turn, both deque
    appends behind two env reads. Asserted with a generous CI bound on the p99; the measured numbers are printed."""
    samples = []
    for i in range(4000):
        t0 = time.perf_counter()
        sd.note_handler(f"s{i % 7}", A, "tier", "expert/weather")
        sd.note_tool(f"s{i % 7}", "recall_memory")
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    p50, p99 = statistics.median(samples), samples[int(len(samples) * 0.99)]
    print(f"failure-capture note overhead: p50={p50:.4f}ms p99={p99:.4f}ms max={samples[-1]:.3f}ms")
    assert p50 < 0.2 and p99 < 1.0, (p50, p99)
    assert len(sd._HANDLERS) == sd._HANDLERS.maxlen, "bounded"


def test_flue_tool_sentinel_parsing():
    import zoe_flue_client as zf

    zf._note_tool_sentinel(VOICE, '__TOOL__:{"phase": "start", "id": "c1", "name": "list_add_item"}')
    zf._note_tool_sentinel(VOICE, '__TOOL__:{"phase": "args", "id": "c1", "name": "should_not_count", "args": {}}')
    zf._note_tool_sentinel(VOICE, "__THINKING__:hmm")
    zf._note_tool_sentinel(VOICE, '__TOOL__:{"phase": "start", broken')
    assert [(h[1], h[3], h[4]) for h in sd._HANDLERS] == [(VOICE, "tool", "list_add_item")]


# ── the seam (real SQLite, the real migrations) ──────────────────────────────

class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)

    def execute(self, sql, params=()):
        async def _run():
            await asyncio.sleep(0)
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()


def _migration(fname):
    spec = importlib.util.spec_from_file_location(f"mig_{fname[:4]}", SVC / "alembic/versions" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def sql(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    for f in ("0050_struggle_events.py", "0057_struggle_proposals.py", "0058_turn_outcomes.py"):
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            _migration(f).upgrade()
    db = _Sqlite(path)
    db.conn.execute("CREATE TABLE chat_messages (id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, created_at TEXT)")

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_pool, "get_db_ctx", fake_db)
    sd.set_backend(sd.SqlBackend())
    to.set_backend(to.SqlBackend())
    return db


async def test_seam_hands_out_only_confirmed_household_pairs(sql):
    await failed_then_fixed()
    with as_traffic("harness"):
        await failed_then_fixed(session="replay-0123456789ab")
    assert await sp.queued_router_examples(sources=(sp.SOURCE_FOLLOWUP, sp.SOURCE_ROUTER)) == []
    rid = (await to.review(A))["items"][0]["id"]
    await to.decide(rid, A, "confirm")
    got = await sp.queued_router_examples(sources=(sp.SOURCE_FOLLOWUP, sp.SOURCE_ROUTER))
    assert len(got) == 1 and got[0]["event_id"] == f"turn:{rid}" and got[0]["label"] == "weather"
    assert got[0]["status"] == "accepted" and got[0]["decided_by"] == f"owner:{A}" and got[0]["split"] in ("train", "holdout")
    assert got[0]["text"].startswith("will it be windy")


async def test_seam_rechecks_the_forget_ledger(sql, monkeypatch):
    await failed_then_fixed()
    rid = (await to.review(A))["items"][0]["id"]
    await to.decide(rid, A, "confirm")
    import memory_forgotten

    async def names_it(_u, t):
        return "windy" in t

    monkeypatch.setattr(memory_forgotten, "matches", names_it)
    assert await sp.queued_router_examples() == []


async def test_seam_survives_a_missing_table(monkeypatch, tmp_path):
    path = str(tmp_path / "old.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    for f in ("0050_struggle_events.py", "0057_struggle_proposals.py"):
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            _migration(f).upgrade()
    db = _Sqlite(path)

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_pool, "get_db_ctx", fake_db)
    to.set_backend(to.SqlBackend())
    assert await sp.queued_router_examples() == []


async def test_the_thumbs_down_button_marks_the_rated_turn(sql):
    q = "what's the capital of chad"
    sql.conn.execute("INSERT INTO chat_messages VALUES ('m1', 'web_1', 'assistant', 'Lyon.', '2026-10-19 10:00:00')")
    await feed(item(q, "Lyon.", session="web_1"))
    assert sd.observe_feedback(A, "m1", "thumbs_up") is False, "only a wrong verdict marks a turn"
    assert sd.observe_feedback(A, "m1", "thumbs_down") is True      # the button path: queued for the ONE worker that writes rows
    await sd.drain(timeout=10)
    row = (await to.review(A))["items"][0]
    assert "NEGATIVE_FEEDBACK" in row["signals"] and row["words"] == q
    assert await to.note_feedback(A, "m2", "thumbs_down") is False, "a reply with no row of its own is not guessed at"


async def test_a_rating_never_lands_on_a_neighbouring_turn(sql, monkeypatch):
    """The newest turn's outcome insert failed: its thumbs-down must not fall back to the PREVIOUS turn's row (the old latest-in-session
    lookup did, and the newest-assistant-message check could not tell)."""
    first, second = "what's the capital of chad", "what's the capital of mali"
    sql.conn.execute("INSERT INTO chat_messages VALUES ('m1', 'web_1', 'assistant', 'N Djamena.', '2026-10-19 10:00:00')")
    await feed(item(first, "N Djamena.", session="web_1"))
    sql.conn.execute("INSERT INTO chat_messages VALUES ('m2', 'web_1', 'assistant', 'Lagos.', '2026-10-19 10:05:00')")
    real_add = to.SqlBackend.add

    async def flaky(self, row):
        raise RuntimeError("db blip")

    monkeypatch.setattr(to.SqlBackend, "add", flaky)
    await feed(item(second, "Lagos.", session="web_1"))
    monkeypatch.setattr(to.SqlBackend, "add", real_add)
    assert await to.note_feedback(A, "m2", "thumbs_down") is False
    assert (await to.review(A))["items"] == [], "the earlier turn was not marked failed"
    assert (await to.get_backend().by_message(A, "m1"))[0]["failed"] == 0


# ── review round 2 (PR #2141): flag read path, forget race, harness facts, logging, typed env ──────

async def test_capture_off_stops_confirmed_examples_reaching_training(sql, monkeypatch):
    await failed_then_fixed()
    rid = (await to.review(A))["items"][0]["id"]
    await to.decide(rid, A, "confirm")
    assert len(await to.confirmed_examples()) == 1
    assert len(await sp.queued_router_examples(sources=(sp.SOURCE_FOLLOWUP, sp.SOURCE_ROUTER))) == 1
    monkeypatch.setenv(to.ENV, "off")           # on, then off
    assert await to.confirmed_examples() == []
    assert await sp.queued_router_examples(sources=(sp.SOURCE_FOLLOWUP, sp.SOURCE_ROUTER)) == []
    monkeypatch.setenv(to.ENV, "on")
    assert len(await to.confirmed_examples()) == 1, "the rows were kept; only the read path is gated"


async def test_an_insert_paused_across_a_forget_cannot_resurrect_the_words(fresh, monkeypatch):
    """A background outcome write is past its keep-words check when a forget runs. Without the per-user lock the forget finds nothing,
    finishes, and the paused insert then stores the forgotten name."""
    import memory_forgotten

    gate, entered = asyncio.Event(), asyncio.Event()
    real = memory_forgotten.matches

    async def slow_matches(uid, text):
        entered.set()
        await gate.wait()                 # the write is parked between its words check and its insert
        return await real(uid, text)

    monkeypatch.setattr(memory_forgotten, "matches", slow_matches)
    q = "will marlowe need an umbrella this afternoon"
    weather_note(q)
    sd.note_brain(VOICE, "flue", "ok")
    writer = asyncio.create_task(sd.process(item(q, "Maybe.", channel="voice", session=VOICE)))
    await asyncio.wait_for(entered.wait(), 5)
    eraser = asyncio.create_task(sd.erase_entity(A, "Marlowe"))
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.wait_for(asyncio.gather(writer, eraser), 10)
    assert not [r for r in fresh.data if "marlowe" in (r["summary"] or "").lower()]
    assert not [r for r in sd.get_backend().data if "marlowe" in (r["summary"] or "").lower()]


async def test_an_insert_paused_across_a_user_delete_leaves_nothing(fresh, monkeypatch):
    import memory_forgotten

    gate, entered = asyncio.Event(), asyncio.Event()

    async def slow_matches(uid, text):
        entered.set()
        await gate.wait()
        return False

    monkeypatch.setattr(memory_forgotten, "matches", slow_matches)
    q = "will it be windy this afternoon"
    weather_note(q)
    sd.note_brain(VOICE, "flue", "ok")
    writer = asyncio.create_task(sd.process(item(q, "Probably a little.", channel="voice", session=VOICE)))
    await asyncio.wait_for(entered.wait(), 5)
    deleter = asyncio.create_task(sd.delete_user(A))
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.wait_for(asyncio.gather(writer, deleter), 10)
    assert fresh.data == [] and sd.get_backend().data == []


async def test_a_harness_turn_keeps_its_route_latency_and_cant_do_but_no_words(fresh):
    text, reply = "dim the porch lights to ten percent", "Sorry, I can't control that device."
    sd.note_route(text, {"domain": "lights", "score": 0.8, "scores": {"lights": 0.8}})
    await asyncio.sleep(0.02)
    assert sd.observe("voice", text, reply, A, "replay-0123456789ab", panel_id="replay-harness") is False
    await sd.drain(timeout=10)
    row = fresh.data[0]
    assert row["traffic"] == "probe" and row["outcome"] == "cant_do", "the reply decides the outcome class"
    assert row["route_domain"] == "lights" and row["route_source"] == "similarity" and row["latency_ms"] is not None
    assert row["failed"] == 0 and row["summary"] == "" and row["label_status"] == ""
    assert not any(isinstance(v, str) and (text in v or "porch" in v or "control that" in v) for v in row.values()), "text-free"


async def test_failed_writes_warn_without_household_words(fresh, caplog):
    secret = "marlowe's surprise party"

    async def boom(*_a, **_k):
        raise RuntimeError("db down " + secret)

    fresh.add, fresh.update, fresh.by_turn = boom, boom, boom
    caplog.set_level("WARNING")
    row = to.new_row(sig.Turn(ts=time.time(), channel="chat", user_id=A, is_guest=False, conv_key="c", turn_id="t-1"),
                     traffic="household", outcome="did")
    assert await to.record(row) is False
    assert await to.mark_failed(A, "t-1", ["X"]) is False
    sd.set_backend(None)
    msgs = [r for r in caplog.records if r.levelname == "WARNING" and "turn_outcomes" in r.getMessage()]
    assert len(msgs) == 2 and all("RuntimeError" in m.getMessage() for m in msgs)
    assert all(secret not in m.getMessage() and "marlowe" not in m.getMessage() for m in msgs)


def test_the_flag_goes_through_typed_env(monkeypatch, caplog):
    import typed_env

    typed_env._warned.clear()
    for v, want in (("on", True), ("1", True), ("enforce", True), ("off", False), ("", False)):
        monkeypatch.setenv(to.ENV, v)
        assert to.enabled() is want, v
    caplog.set_level("WARNING")
    monkeypatch.setenv(to.ENV, "maybe-later")
    assert to.enabled() is False
    assert any(to.ENV in r.getMessage() for r in caplog.records), "an unparseable value earns the shared warning"


# ── migration 0058 ───────────────────────────────────────────────────────────

def test_migration_0058_chains_reruns_and_downgrades():
    mod = _migration("0058_turn_outcomes.py")
    revs, downs = set(), set()
    for f in (SVC / "alembic/versions").glob("*.py"):
        src = f.read_text()
        r = re.search(r'^revision = "(\w+)"', src, re.M)
        d = re.search(r'^down_revision = "(\w+)"', src, re.M)
        revs.add(r.group(1)) if r else None
        downs.add(d.group(1)) if d else None
    assert mod.down_revision in revs and len(revs - downs) == 1
    engine = sa.create_engine("sqlite://")
    for _ in range(2):
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
    cols = {c["name"] for c in sa.inspect(engine).get_columns("turn_outcomes")}
    assert cols == set(to.COLUMNS)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        mod.downgrade()
    assert "turn_outcomes" not in sa.inspect(engine).get_table_names()


def test_no_request_time_ddl():
    assert "CREATE TABLE" not in (SVC / "turn_outcomes.py").read_text().upper()


def test_rows_never_hold_the_reply():
    assert "reply" not in to.COLUMNS and json.dumps(to.COLUMNS).count("summary") == 1


# ── review round 3 (PR #2141): decide-vs-mark race, review gated on the flag, voice audio window ──────

async def _racing_decide_survives(label):
    """``decide`` lands between ``mark_failed``'s read of the row and its write: the owner's decision must survive."""
    q = "will marlowe need an umbrella this afternoon"
    weather_note(q)
    sd.note_brain(VOICE, "flue", "ok")
    await feed(item(q, "Maybe.", channel="voice", session=VOICE))
    store = to.get_backend()
    row = (await store.rows(0))[0]
    rid, turn = row["id"], row["turn_id"]
    assert await to.mark_failed(A, turn, ["REASK"], ("weather", "router"), q) is True      # proposed, awaiting the owner
    real = store.by_turn

    async def read_then_owner_decides(user_id, turn_id):
        rows = await real(user_id, turn_id)         # mark_failed holds this (proposed) copy ...
        await to.decide(rid, A, "reject")           # ... and the owner decides before its write
        return rows

    store.by_turn = read_then_owner_decides
    try:
        await to.mark_failed(A, turn, ["CORRECTION"], ("calendar", "router"), q)
    finally:
        store.by_turn = real
    after = (await store.by_id(rid))[0]
    assert after["label_status"] == "rejected" and after["decided_by"] == f"owner:{A}", label
    assert "CORRECTION" in after["signals"], "the new signal is still recorded"


async def test_a_background_mark_never_overwrites_an_owner_decision(fresh):
    await _racing_decide_survives("memory")


async def test_a_background_mark_never_overwrites_an_owner_decision_in_sql(sql):
    await _racing_decide_survives("sql")


async def test_review_reads_nothing_once_capture_is_off(fresh, monkeypatch):
    await failed_then_fixed()
    assert (await to.review(A))["items"], "on: the saved words are shown"
    monkeypatch.setenv(to.ENV, "off")           # on, then off
    reads = []
    real_rows = fresh.rows

    async def spy(*a, **k):
        reads.append(1)
        return await real_rows(*a, **k)

    fresh.rows = spy
    rep = await to.review(A)
    assert rep == {"days": to.REVIEW_DAYS, "enabled": False, "pending": 0, "items": []} and reads == []


async def test_confirmed_voice_example_audio_uses_the_turns_latency(fresh, monkeypatch):
    q = "will it be windy this afternoon"
    weather_note(q)
    sd.note_brain(VOICE, "flue", "ok")
    await feed(item(q, "Probably.", channel="voice", session=VOICE))
    row = fresh.data[0]
    row["latency_ms"] = 1234
    await to.decide(row["id"], A, "confirm", "weather")
    seen = []
    monkeypatch.setattr(sp, "audio_ref", lambda ts, **kw: seen.append(kw) or None)
    assert len(await to.confirmed_examples()) == 1
    assert seen == [{"latency_ms": 1234}], "without it the 90 s fallback can attach an earlier turn's clip"


# ── review round 4 (PR #2141): a late rating never copies an off-the-record turn's words ────────────

def _turn_id(user=A):
    return next(t.turn_id for h in sd._HISTORY.values() for t in h if t.user_id == user)


async def test_an_off_record_turn_rated_after_the_mark_expired_stores_no_words(monkeypatch):
    import memory_provenance as mp

    mark = {"live": True}
    monkeypatch.setattr(mp, "is_off_record", lambda user, text, **kw: mark["live"])
    await feed(item("this one is off the record please", ts=time.time()))
    mark["live"] = False                                   # the 900 s mark has expired by the time the rating lands
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 16 * 60)
    assert await sd.words_for_turn(A, _turn_id()) == ""


async def test_an_off_record_turn_rated_within_the_ttl_still_stores_no_words(monkeypatch):
    import memory_provenance as mp

    mark = {"live": True}
    monkeypatch.setattr(mp, "is_off_record", lambda user, text, **kw: mark["live"])
    await feed(item("secret thing, off the record", ts=time.time()))
    mark["live"] = False                                   # status was captured at turn time, not re-derived
    assert await sd.words_for_turn(A, _turn_id()) == ""


async def test_a_normal_turn_rated_within_the_ttl_keeps_its_words():
    q = "what's the capital of chad"
    await feed(item(q, "Lyon."))
    assert await sd.words_for_turn(A, _turn_id()) == q


async def test_a_normal_turn_rated_after_the_ttl_stores_no_words(monkeypatch):
    await feed(item("what's the capital of chad", "Lyon."))
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + sd.WORDS_TTL_S + 60)
    assert await sd.words_for_turn(A, _turn_id()) == ""
