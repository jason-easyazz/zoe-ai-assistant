"""Open-loop lifecycle (``ZOE_LOOP_LIFECYCLE``): the week-in-the-life simulation's gaps.

scripts/perf/samantha_day_sim.py, 2026-10-03: (a) the first raise was voiced as "I don't
have any information about how your dentist appointment went"; (b) the ``[Today]`` brief
mentioned a loop and nothing marked it, so the next conversation could raise it again;
(c) "actually Bendigo" / "dropped the half-marathon" superseded the facts but left their
loops open; (d) loops are dated 3–14 days out and the selector excluded anything due
beyond 48 h, so nights 1–2 kept nothing — and the migraine worry was not even a loop.

Fixtures only — no household text, no real ids. The DB edge is a real SQLite file built by
migration 0033 plus ``open_loops``, so the upsert / resolve SQL runs for real. Every test
has its flag-off control; the negative controls are named ``test_negative_*``.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # every edge is stubbed; slim-dep modules only

import asyncio
import contextlib
import importlib.util
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import brief_first_turn as bft
import db_compat
import memory_digest
import memory_service
import memory_supersede as ms
import open_loop_lifecycle as olc
from db_pool import _Cursor, _ExecResult
from open_loop_quality import loop_is_concrete
from proactive import selector as sel

SVC = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 30, 8, 0, tzinfo=timezone.utc)  # inside the 05:00–12:00 brief window
MEMBER = "member-a"
GREET = "Hi Zoe, how are things?"

DENTIST = "User is nervous about the dentist appointment on Friday for a cracked molar."
MUM = "The user's mother, Ingrid, is recovering from a hip replacement in Ballarat."
RACE = "The user is training for the Rottnest half-marathon in February."
PROJECT = "The Kestrel billing migration at work needs to go live in November."
MIGRAINE = "The user has been getting migraines most afternoons, which is causing worry."


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)

    def execute(self, sql, params=()):
        async def _run():
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


class _Mem:
    async def load_recent_for_prompt(self, user_id, **kw):
        return []


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    spec = importlib.util.spec_from_file_location(
        "mig_0033", SVC / "alembic/versions/0033_proactive_candidates.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        mig.upgrade()
    db = _Sqlite(path)
    for ddl in (
        "CREATE TABLE open_loops (id INTEGER PRIMARY KEY, user_id TEXT, loop_text TEXT, "
        "follow_up_hint TEXT, emotional_weight INTEGER, created_at TEXT, follow_up_after TEXT, "
        "resolved BOOLEAN DEFAULT FALSE, resolved_at TEXT)",
        "CREATE TABLE events (id TEXT, user_id TEXT, title TEXT, start_date TEXT, start_time TEXT, "
        "deleted INTEGER DEFAULT 0)",
        "CREATE TABLE proactive_pending (id TEXT, trigger_type TEXT, item_id TEXT)",
    ):
        db.conn.execute(ddl)

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    mem = _Mem()
    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: mem)
    for key in ("ZOE_SEAM_RECALL_INJECT", "ZOE_SEAM_OFFER_INJECT", "ZOE_SYNTHETIC_USER_ALLOWLIST",
                "ZOE_PROACTIVE_RAISE_GAP_S", "ZOE_PROACTIVE_RAISE_PER_DAY",
                "ZOE_BRIEF_WINDOW_START", "ZOE_BRIEF_WINDOW_END"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "1")
    monkeypatch.setenv("ZOE_BRIEF_ON_FIRST_TURN", "1")
    monkeypatch.setenv("ZOE_LOOP_LIFECYCLE", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")
    sel._reset_state()
    bft._reset_state()
    state = {"now": NOW, "db": db}
    monkeypatch.setattr(sel, "_now", lambda: state["now"])
    monkeypatch.setattr(olc, "_now", lambda: state["now"])
    yield state
    sel._reset_state()
    bft._reset_state()


def _loop(db, text, *, weight=4, age_h=10, due_h=1, hint=""):
    created = (NOW - timedelta(hours=age_h)).strftime("%Y-%m-%d %H:%M:%S")
    due = None if due_h is None else (NOW + timedelta(hours=due_h)).strftime("%Y-%m-%d %H:%M:%S")
    cur = db.conn.execute(
        "INSERT INTO open_loops (user_id, loop_text, follow_up_hint, emotional_weight, created_at, "
        "follow_up_after) VALUES (?, ?, ?, ?, ?, ?)", (MEMBER, text, hint, weight, created, due))
    db.conn.commit()
    return cur.lastrowid


def _open(db):
    return [r[0] for r in db.rows("SELECT loop_text FROM open_loops WHERE resolved IS NOT TRUE")]


# ── (a) raise phrasing ───────────────────────────────────────────────────────
def _raise(kind="open_loop", hint="How did the dentist appointment go?", lifecycle=True):
    return sel.Raise(MEMBER, "s1", "c1", kind, "greeting", DENTIST, hint, "tok", lifecycle)


def test_raise_block_asks_one_gentle_question_from_the_hint():
    block = _raise().block
    r = _raise()
    head = sel.RAISE_OPEN_GREETING if r.shape == "greeting" else sel.RAISE_OPEN
    assert block.startswith(head + "\n") and block.endswith("\n" + sel.RAISE_CLOSE)
    assert f"Earlier they told you: {DENTIST}." in block
    assert 'for example: "How did the dentist appointment go?"' in block
    for must in ("ONE short, gentle question", "You are asking THEM",
                 "never say you have no information about it", "One sentence"):
        assert must in block
    # the escape hatch belongs to CUE raises only (2026-10-04: a greeting raise was
    # injected + settled and never voiced under "if it fits … leave it out")
    expect = "do raise it" if r.shape == "greeting" else "If it does not fit, leave it out."
    assert expect in block


def test_raise_block_hint_shapes():
    assert "(briefly and warmly ask how that is going)" in _raise(
        hint=sel._ASK["open_loop"]).block  # an instruction hint is guidance, not a quote
    assert '"' not in sel.ask_phrasing('Ask "how it went"').split("words")[1]
    assert "for example" not in _raise(hint="").block  # no hint: the rule alone


def test_flag_off_and_event_raises_keep_the_old_body():
    old = (f"Earlier they told you: {DENTIST}. If it fits, How did the dentist appointment go? "
           "— once, in your own words, never quoting them and never as a list or a reminder.")
    assert _raise(lifecycle=False).body == old
    assert _raise("event", hint=sel._ASK["event"]).body.startswith("On their calendar: ")
    assert "gentle question" not in _raise("event", hint=sel._ASK["event"]).body


async def test_prepare_snapshots_the_flag(env, monkeypatch):
    _loop(env["db"], DENTIST, hint="How are you feeling about the dentist?")
    await sel.select_for_user(MEMBER, now=NOW)
    assert "gentle question" in (await sel.prepare(GREET, MEMBER, "s1")).block
    sel._reset_state()
    monkeypatch.setenv("ZOE_LOOP_LIFECYCLE", "0")
    assert "gentle question" not in (await sel.prepare(GREET, MEMBER, "s2")).block


# ── (b) the brief marks what it mentioned ────────────────────────────────────
def _brief_ctx(ids_texts, moment=None):
    ctx = {"open_loops": [{"id": i, "text": t, "hint": "", "weight": 3, "due": None}
                          for i, t in ids_texts]}
    if moment:
        ctx["emotional_moments"], ctx["emotional_moment_ids"] = [moment[1]], [moment[0]]
    return ctx


@pytest.fixture
def brief(env, monkeypatch):
    async def claim_state(uid, now):
        return "free"

    async def take(uid, now):
        return True

    monkeypatch.setattr(bft, "_claim_state", claim_state)
    monkeypatch.setattr(bft, "_take_claim", take)
    monkeypatch.setattr(bft, "_now_utc", lambda: env["now"])

    def use(ctx):
        async def gather(uid, local_date):
            return ctx
        monkeypatch.setattr(bft, "_gather", gather)
    env["use"] = use
    return env


def _surfaced(db):
    return {r[0]: r[1:] for r in db.rows(
        "SELECT source_ref, surfaced_count, last_surfaced_session, cooldown_until, expires_at "
        "FROM proactive_candidates WHERE surfaced_count > 0")}


async def test_brief_marks_its_loops_and_the_next_session_does_not_reraise(brief, caplog):
    lid = _loop(brief["db"], DENTIST)
    await sel.select_for_user(MEMBER, now=NOW)  # the nightly candidate exists
    brief["use"](_brief_ctx([(lid, DENTIST)]))
    day = await bft.prepare("morning", MEMBER, "s1")
    assert day.surfaced == (("open_loop", f"open_loops:{lid}", DENTIST),)
    with caplog.at_level(logging.INFO, logger="proactive.selector"):
        await bft.settle(day, produced=True)
    assert "kind=brief shape=brief injected=1 settled=1 marked=1" in caplog.text
    count, session, cooldown, _ = _surfaced(brief["db"])[f"open_loops:{lid}"]
    assert (count, session) == (1, "s1") and cooldown > sel._iso(NOW)
    brief["now"] = NOW + timedelta(hours=3)  # past the member gap: only the cooldown holds it
    assert await sel.prepare(GREET, MEMBER, "s2") is None


async def test_negative_flag_off_brief_marks_nothing_and_the_loop_is_raised_again(brief, monkeypatch):
    monkeypatch.setenv("ZOE_LOOP_LIFECYCLE", "0")
    lid = _loop(brief["db"], DENTIST)
    await sel.select_for_user(MEMBER, now=NOW)
    brief["use"](_brief_ctx([(lid, DENTIST)]))
    day = await bft.prepare("morning", MEMBER, "s1")
    assert day.surfaced == ()
    await bft.settle(day, produced=True)
    brief["now"] = NOW + timedelta(hours=3)
    assert DENTIST.rstrip(".") in (await sel.prepare(GREET, MEMBER, "s2")).text  # the day-sim 7r gap


async def test_brief_marks_only_what_it_said(brief):
    """Bounded: a command turn carries only the overdue line, so only that loop is marked;
    nothing is marked when no reply text went out."""
    due = _loop(brief["db"], DENTIST, due_h=-2)
    other = _loop(brief["db"], PROJECT)
    ctx = _brief_ctx([(due, DENTIST), (other, PROJECT)])
    ctx["open_loops"][0]["due"] = (NOW - timedelta(hours=2)).isoformat()
    brief["use"](ctx)
    day = await bft.prepare("turn on the lights", MEMBER, "s1")
    assert [ref for _, ref, _ in day.surfaced] == [f"open_loops:{due}"]
    await bft.settle(day, produced=False)
    assert _surfaced(brief["db"]) == {}
    await bft.settle(day, produced=True)
    assert list(_surfaced(brief["db"])) == [f"open_loops:{due}"]


async def test_an_unselected_item_gets_a_cooling_row_the_nightly_keeps(brief):
    """The brief mentioned a loop and a moment the nightly never ranked: their rows carry
    the cooldown, so the next night cannot select them fresh and raise them."""
    lid = _loop(brief["db"], DENTIST)
    brief["use"](_brief_ctx([(lid, DENTIST)], moment=("m1", "User was nervous about the interview")))
    await bft.settle(await bft.prepare("morning", MEMBER, "s1"), produced=True)
    marked = _surfaced(brief["db"])
    assert set(marked) == {f"open_loops:{lid}", "memory:m1"}
    assert all(row[3] == sel._iso(NOW) for row in marked.values())  # expired: never raised as is
    brief["now"] = NOW + timedelta(hours=20)
    await sel.select_for_user(MEMBER, now=brief["now"])  # the next night upserts, keeps cooldown
    assert _surfaced(brief["db"])[f"open_loops:{lid}"][0] == 1
    assert await sel.prepare(GREET, MEMBER, "s2") is None


async def test_one_brief_counts_as_one_delivery_for_the_daily_cap(brief, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_GAP_S", "0")
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_PER_DAY", "2")
    a, b = _loop(brief["db"], DENTIST), _loop(brief["db"], PROJECT)
    _loop(brief["db"], MUM, weight=5)
    await sel.select_for_user(MEMBER, now=NOW)
    brief["use"](_brief_ctx([(a, DENTIST), (b, PROJECT)]))
    await bft.settle(await bft.prepare("morning", MEMBER, "s1"), produced=True)
    brief["now"] = NOW + timedelta(minutes=5)
    assert "Ingrid" in (await sel.prepare(GREET, MEMBER, "s2")).text  # 1 delivery so far, cap 2


def test_negative_counting_rows_not_deliveries_hits_the_cap():
    stamp = sel._iso(NOW)
    rows = [(None,) * 11 + (stamp,), (None,) * 11 + (stamp,)]
    assert sel._spacing(rows, NOW + timedelta(hours=3)) == ""  # one shared stamp = one
    rows.append((None,) * 11 + (sel._iso(NOW + timedelta(hours=1)),))
    assert sel._spacing(rows, NOW + timedelta(hours=4)) == "daily_cap"


# ── (c) a retired fact closes the loops that rest on it ──────────────────────
@pytest.mark.parametrize("loop, old, new, ended, closes", [
    (MUM, "User's mum Ingrid lives in Ballarat.", "User's mum Ingrid lives in Bendigo.", False, True),
    (RACE, "User is training for the Rottnest half-marathon in February.",
     "User dropped the Rottnest half-marathon.", True, True),
    (RACE, "User is training for the Rottnest half-marathon in February.",
     "User is doing the City to Surf 12k in August instead.", True, True),
    # an enrichment edit removes no anchor and is not an ending: the loop stays open
    (DENTIST, "User has a dentist appointment.",
     "User has a dentist appointment on Friday for a cracked molar.", False, False),
    (PROJECT, "User is training for the Rottnest half-marathon in February.",
     "User dropped the Rottnest half-marathon.", True, False),  # unrelated topic
    (RACE, "User is training for the Rottnest half-marathon in February.",
     "User dropped the Rottnest half-marathon.", False, False),  # not ended, nothing removed
])
def test_retired_match_table(loop, old, new, ended, closes):
    assert olc.retired_match(loop, old, new, ended=ended) is closes


async def test_resolve_closes_matching_loops_expires_their_candidates_and_logs(env, caplog):
    mum, project = _loop(env["db"], MUM, weight=5), _loop(env["db"], PROJECT)
    await sel.select_for_user(MEMBER, now=NOW)
    with caplog.at_level(logging.INFO, logger="memory_digest.open_loops"):
        n = await olc.resolve_for_supersede(
            MEMBER, [("User's mum Ingrid lives in Ballarat.", "User's mum Ingrid lives in Bendigo.")],
            ended=False, source="edit:turn_digest")
    assert n == 1 and _open(env["db"]) == [PROJECT]
    assert f"OPEN_LOOPS user={MEMBER} resolved_by_supersede=1 source=edit:turn_digest" in caplog.text
    (exp,), = env["db"].rows("SELECT expires_at FROM proactive_candidates WHERE source_ref = ?",
                             (f"open_loops:{mum}",))
    assert exp == sel._iso(NOW)  # expired now: not raisable this afternoon
    raised = await sel.prepare(GREET, MEMBER, "s1")  # the top candidate was the mum loop
    assert project and raised is not None and "Kestrel" in raised.text


async def test_negative_flag_off_resolves_nothing(env, monkeypatch):
    monkeypatch.setenv("ZOE_LOOP_LIFECYCLE", "0")
    _loop(env["db"], MUM)
    assert await olc.resolve_for_supersede(
        MEMBER, [("User's mum Ingrid lives in Ballarat.", "User's mum lives in Bendigo.")],
        ended=False, source="t") == 0
    assert _open(env["db"]) == [MUM]


class _FakeSvc:
    """``supersede_for_turn``'s two calls; the rows are approved person facts."""

    def __init__(self, rows):
        self.rows = rows

    async def list_by_status(self, **_kw):
        return self.rows

    async def supersede_by(self, *a, **k):
        return True


def _ref(i, text):
    return memory_service.MemoryRef(id=i, text=text, metadata={
        "status": "approved", "memory_type": "event", "user_id": MEMBER})


async def test_implicit_supersede_closes_the_race_loop_not_the_others(env):
    _loop(env["db"], RACE), _loop(env["db"], PROJECT)
    svc = _FakeSvc([_ref("old", "User is training for the Rottnest half-marathon in February.")])
    out = await ms.supersede_for_turn(svc, MEMBER, "change of plan", [
        _ref("t", "User dropped the Rottnest half-marathon."),
        _ref("n", "User is doing the City to Surf 12k in August instead.")])
    assert out["superseded"] == 1 and _open(env["db"]) == [PROJECT]


async def test_review_edit_hands_the_retired_text_to_the_loops(monkeypatch):
    seen = []

    async def record(uid, pairs, *, ended, source):
        seen.append((uid, list(pairs), ended, source))
        return 0

    monkeypatch.setattr(olc, "resolve_for_supersede", record)

    class _Col:
        rows = {}

        def upsert(self, *, ids, documents, metadatas, **_kw):
            for i, d, m in zip(ids, documents, metadatas):
                self.rows[i] = (d, dict(m))

        def get(self, *, ids=None, **_kw):
            keys = [i for i in ids or [] if i in self.rows]
            return {"ids": keys, "documents": [self.rows[i][0] for i in keys],
                    "metadatas": [dict(self.rows[i][1]) for i in keys]}

    svc = memory_service.MemoryService(data_dir="/nonexistent/zoe-test-loop-lifecycle")
    col = _Col()
    svc._collection = lambda: col

    async def nothing(*_a, **_k):
        return None

    async def opted_in(_uid):
        return False

    svc._append_audit = nothing
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    ref = await svc.ingest("User's mum lives in Ballarat.", user_id=MEMBER, source="turn_digest",
                           memory_type="relationship", confidence=0.8, status="approved")
    await svc.review(ref.id, decision="edit", actor="turn_digest",
                     edits="User's mum lives in Bendigo.")
    assert seen == [(MEMBER, [("User's mum lives in Ballarat.", "User's mum lives in Bendigo.")],
                     False, "edit:turn_digest")]


# ── (d) horizon: decayed relevance + the extractor's guidance ────────────────
@pytest.mark.parametrize("due_h, decayed, legacy", [
    (None, 0.7, 0.7), (-5, 1.0, 1.0), (24, 1.0, 1.0), (36, 0.6, 0.6), (48, 0.6, 0.6),
    (72, 0.4, None), (24 * 7, 0.4, None), (24 * 7 + 1, 0.25, None), (24 * 14, 0.25, None),
])
def test_relevance_decay_table(due_h, decayed, legacy):
    due = None if due_h is None else NOW + timedelta(hours=due_h)
    assert sel.loop_relevance(due, NOW, decay=True) == decayed
    assert sel.loop_relevance(due, NOW) == legacy  # flag off: byte-identical


# follow_up_in_days → the selector's relevance on the night the loop was extracted.
@pytest.mark.parametrize("days, relevance", [(1, 1.0), (2, 0.6), (3, 0.4), (5, 0.4), (7, 0.4),
                                             (10, 0.25), (14, 0.25)])
def test_extractor_horizon_table(days, relevance):
    assert sel.loop_relevance(NOW + timedelta(days=days), NOW, decay=True) == relevance


async def test_night_one_keeps_later_due_loops_salience_ranked(env, monkeypatch):
    """The day-sim night 1: loops 3–10 days out. Off: nothing kept. On: all kept, the heavy
    worry first, still capped."""
    for text, weight, days in ((MUM, 3, 3), (PROJECT, 2, 7), (RACE, 2, 10), (MIGRAINE, 4, 5)):
        _loop(env["db"], text, weight=weight, age_h=0, due_h=24 * days)
    monkeypatch.setenv("ZOE_LOOP_LIFECYCLE", "0")
    assert (await sel.select_for_user(MEMBER, now=NOW))["kept"] == 0
    monkeypatch.setenv("ZOE_LOOP_LIFECYCLE", "1")
    assert (await sel.select_for_user(MEMBER, now=NOW))["kept"] == 4
    top = env["db"].rows("SELECT text FROM proactive_candidates ORDER BY salience DESC")[0][0]
    assert "migraines" in top


@pytest.mark.parametrize("on", [True, False])
def test_health_worry_is_concrete_only_under_the_flag(monkeypatch, on):
    monkeypatch.setenv("ZOE_LOOP_LIFECYCLE", "1" if on else "0")
    assert loop_is_concrete(MIGRAINE) is on
    assert loop_is_concrete("User has had a rough week and wants to talk") is False  # still junk


class _Cur:
    def __init__(self, rows=(), rowcount=0):
        self._rows, self.rowcount = list(rows), rowcount

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetchall(self):
        return self._rows


@pytest.mark.parametrize("on", [True, False])
async def test_extractor_prompt_and_resolved_recently_dedupe(monkeypatch, on):
    """On: the prompt carries the horizon guidance, and a loop resolved in the transcript
    window (the superseded Ballarat one) is not re-extracted from the turns that made it."""
    monkeypatch.setenv("ZOE_LOOP_LIFECYCLE", "1" if on else "0")
    calls, inserts = [], []

    class _Db:
        def execute(self, sql, params=()):
            head = " ".join(sql.split())
            calls.append(head)
            if "FROM chat_messages" in head:
                return _Cur([("My mum Ingrid lives in Ballarat and is recovering from a hip op",)])
            if head.startswith("SELECT loop_text"):
                recent = ("(resolved IS NOT TRUE OR resolved_at > CURRENT_TIMESTAMP - "
                          "INTERVAL '2 days')")
                return _Cur([(MUM,)] if head.endswith(f"WHERE user_id = ? AND {recent}") else [])
            if head.startswith("SELECT id, loop_text"):
                return _Cur([])
            if head.startswith("INSERT"):
                inserts.append(params)
            return _Cur(rowcount=0)

    class _Ctx:
        async def __aenter__(self):
            return _Db()

        async def __aexit__(self, *exc):
            return False

    posts = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            posts.append(json)
            import json as _j

            reply = _j.dumps([{"loop_text": MUM, "follow_up_hint": "How is Ingrid?",
                               "emotional_weight": 3, "follow_up_in_days": 1}])
            return type("R", (), {"raise_for_status": lambda s: None,
                                  "json": lambda s: {"choices": [{"message": {"content": reply}}]}})()

    monkeypatch.setattr(db_compat, "get_compat_db", _Ctx)
    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", _Client)
    result = await memory_digest._extract_open_loops("user-1")
    prompt = posts[0]["messages"][1]["content"]
    assert (memory_digest._OPEN_LOOPS_HORIZON in prompt) is on
    assert ("1 for a worry, a health concern" in prompt) is on
    assert (result["skipped_dup"], len(inserts)) == ((1, 0) if on else (0, 1))


def test_greeting_raise_is_brought_up_and_cue_raise_keeps_its_escape_hatch():
    # Confirmation day-sim 2026-10-04: a greeting raise was injected and settled but the
    # reply never voiced it under the cue wording. Greeting = do raise it; cue = if it fits.
    g = sel.ask_phrasing("How did the dentist go?", shape="greeting")
    c = sel.ask_phrasing("How did the dentist go?", shape="cue")
    assert g.startswith("Bring this up") and "do raise it" in g and "leave it out" not in g
    assert c.startswith("If it fits") and "leave it out" in c
    for text in (g, c):
        assert "never say you have no information" in text and "ONE short, gentle question" in text
    assert sel.ask_phrasing("How did the dentist go?") == c  # default shape is cue


def test_raise_body_passes_its_shape_to_the_phrasing():
    r = sel.Raise(user_id="u", session_id="s", candidate_id="c", kind="open_loop", shape="greeting",
                  text="the dentist on Friday", hint="How did the dentist go?", token="t", lifecycle=True)
    assert "Bring this up" in r.body and r.block.startswith(sel.RAISE_OPEN_GREETING)
    r2 = sel.Raise(user_id="u", session_id="s", candidate_id="c", kind="open_loop", shape="cue",
                   text="the dentist on Friday", hint="How did the dentist go?", token="t", lifecycle=True)
    assert "If it fits" in r2.body and r2.block.startswith(sel.RAISE_OPEN + "\n")
