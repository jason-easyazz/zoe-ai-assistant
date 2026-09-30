"""Proactivity selector (``proactive/selector.py``, ``ZOE_PROACTIVE_SELECTOR``).

Fixtures only — no household text, no real ids. The DB edge is a real SQLite file
built by migration 0033 (plus the three source tables), behind db_pool's own cursor
types, so the upsert / cooldown / session SQL runs for real. Negative controls: break the flag read (every runtime test goes dark), drop the
brief check, the command check, the synthetic check or the ``produced`` guard — the
matching test goes red.
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

import db_compat
from db_pool import _Cursor, _ExecResult
import zoe_core_client as core
import zoe_flue_client as flue
from proactive import selector as sel

SVC = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
MEMBER = "member-a"


def _mig():
    spec = importlib.util.spec_from_file_location("mig_0033", SVC / "alembic/versions/0033_proactive_candidates.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _migrate(engine, fn):
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(_mig(), fn)()


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)

    def execute(self, sql, params=()):
        """The compat layer's own dual-mode result (await / async with)."""
        async def _run():
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


class _Mem:
    def __init__(self):
        self.refs = []

    async def load_recent_for_prompt(self, user_id, **kw):
        return list(self.refs)


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    _migrate(engine, "upgrade")
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

    import memory_service

    mem = _Mem()
    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: mem)
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")
    for key in ("ZOE_SEAM_RECALL_INJECT", "ZOE_SEAM_OFFER_INJECT", "ZOE_BRIEF_ON_FIRST_TURN",
                "ZOE_SYNTHETIC_USER_ALLOWLIST"):
        monkeypatch.delenv(key, raising=False)
    sel._reset_state()
    state = {"now": NOW, "db": db, "mem": mem}
    monkeypatch.setattr(sel, "_now", lambda: state["now"])
    yield state
    sel._reset_state()


def _loop(db, text, *, weight=4, age_h=10, due_h=1, user=MEMBER):
    created = (NOW - timedelta(hours=age_h)).strftime("%Y-%m-%d %H:%M:%S")
    due = None if due_h is None else (NOW + timedelta(hours=due_h)).strftime("%Y-%m-%d %H:%M:%S")
    db.conn.execute("INSERT INTO open_loops (user_id, loop_text, follow_up_hint, emotional_weight, "
                    "created_at, follow_up_after) VALUES (?, ?, '', ?, ?, ?)",
                    (user, text, weight, created, due))


async def _select(env, user=MEMBER):
    return await sel.select_for_user(user, now=env["now"])


def _surfaced(env):
    return env["db"].rows("SELECT kind, surfaced_count, last_surfaced_session, cooldown_until "
                          "FROM proactive_candidates WHERE surfaced_count > 0")


# ── salience (importance × recency × relevance), table-driven ────────────────
def test_ranking_is_importance_times_recency_times_relevance():
    loops = [
        {"id": 1, "text": "A relative is in hospital; user is travelling to visit", "weight": 5,
         "created": NOW - timedelta(hours=10), "due": NOW + timedelta(hours=1)},     # 1.0×.908×1
        {"id": 2, "text": "The user feels tired because work is busy", "weight": 3,
         "created": NOW - timedelta(hours=20), "due": None},                         # .6×.825×.7
        {"id": 3, "text": "User must renew the passport", "weight": 5,
         "created": NOW - timedelta(days=10), "due": NOW},                           # decayed <0.1
        {"id": 4, "text": "User wants to book the vet", "weight": 5,
         "created": NOW, "due": NOW + timedelta(days=5)},                            # not due
        {"id": 5, "text": "The user expressed a desire to talk continuously", "weight": 5,
         "created": NOW, "due": NOW},                                                # junk
    ]
    moments = [{"id": "m1", "text": "User is anxious about the job interview on Friday",
                "intensity": 0.9, "added": (NOW - timedelta(hours=2)).isoformat()},  # .9×.98×.8
               {"id": "m2", "text": "User is worried about the relative in hospital; travelling to visit",
                "intensity": 1.0, "added": NOW.isoformat()}]                        # dup of loop 1
    events = [{"id": e, "title": t, "start": NOW + timedelta(hours=h), "tz": timezone.utc}
              for e, t, h in (("e1", "Dentist", 20), ("e2", "School concert", 30),
                              ("e3", "Car service", 60), ("e4", "Gym", -1))]
    kept = sel.rank(sel.score_all(loops, moments, events, NOW))
    assert [c.source_ref for c in kept] == ["open_loops:1", "memory:m1", "events:e1",
                                            "events:e2", "open_loops:2"]
    assert [c.salience for c in kept] == pytest.approx([0.908, 0.7062, 0.6, 0.42, 0.3465], abs=1e-3)
    assert "hospital" in kept[0].cues.split() and "interview" in kept[1].cues.split()


def test_cap_keeps_the_top_five():
    titles = ("Dentist", "Physio", "Standup", "Wedding", "Funeral", "Flight", "Exam", "Haircut")
    events = [{"id": f"e{i}", "title": t, "start": NOW + timedelta(hours=i + 1), "tz": timezone.utc}
              for i, t in enumerate(titles)]
    assert len(sel.rank(sel.score_all([], [], events, NOW))) == sel.CAP == 5


# ── nightly precompute ───────────────────────────────────────────────────────
async def test_select_persists_logs_and_is_off_without_the_flag(env, monkeypatch, caplog):
    _loop(env["db"], "A relative is in hospital; user is travelling to visit", weight=5)
    _loop(env["db"], "The user expressed a desire to talk continuously", weight=2)
    with caplog.at_level(logging.INFO, logger="proactive.selector"):
        assert (await _select(env))["kept"] == 1
    assert f"PROACTIVE_SELECT user={MEMBER} candidates=1 kept=1" in caplog.text
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "0")
    assert await _select(env) is None


async def test_recompute_keeps_the_cooldown_and_expires_dropped_rows(env):
    _loop(env["db"], "A relative is in hospital; user is travelling to visit", weight=5)
    await _select(env)
    raised = await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1")
    assert await sel.settle(raised, produced=True)
    await _select(env)  # the next night: same source → same row, cooldown intact
    (kind, count, session, cooldown), = _surfaced(env)
    assert (kind, count, session) == ("open_loop", 1, "s1") and cooldown > sel._iso(NOW)
    env["db"].conn.execute("UPDATE open_loops SET resolved = TRUE")
    env["now"] = NOW + timedelta(minutes=1)
    await sel.select_for_user(MEMBER, now=env["now"])  # dropped but cooling: expired, kept
    assert env["db"].rows("SELECT expires_at FROM proactive_candidates") == [(sel._iso(env["now"]),)]


# ── runtime raise ────────────────────────────────────────────────────────────
@pytest.fixture
async def seeded(env):
    _loop(env["db"], "User is anxious about a job interview at the aquarium on Friday", weight=5)
    _loop(env["db"], "The kitchen renovation quote is overdue", weight=2)
    await _select(env)
    return env


async def test_greeting_raises_the_top_candidate_once_per_session(seeded, caplog):
    raised = await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1")
    assert raised.shape == "greeting" and "aquarium" in raised.block
    assert raised.block.startswith(sel.RAISE_OPEN) and raised.block.endswith(sel.RAISE_CLOSE)
    assert await sel.prepare("Hey Zoe, what's new?", MEMBER, "s1") is None  # held mid-reply
    with caplog.at_level(logging.INFO, logger="proactive.selector"):
        assert await sel.settle(raised, produced=True)
    assert "PROACTIVE_RAISE user=member-a kind=open_loop shape=greeting injected=1 settled=1" in caplog.text
    sel._reset_state()  # a restart: the durable record still holds the conversation
    assert await sel.prepare("Hey Zoe, what's new?", MEMBER, "s1") is None
    second = await sel.prepare("Hey Zoe, what's new?", MEMBER, "s2")  # cooldown: the next one
    assert second is not None and "kitchen" in second.text


async def test_cooldown_and_the_surfaced_cap(seeded):
    async def greet(at, sid):
        seeded["now"] = at
        await sel.select_for_user(MEMBER, now=at)  # the nightly run before that day
        return await sel.prepare("Hi Zoe, how are things?", MEMBER, sid)

    first = await greet(NOW, "s1")
    await sel.settle(first, produced=True)
    assert "kitchen" in (await greet(NOW + timedelta(hours=1), "s2")).text  # aquarium cooling
    sel._reset_state()
    again = await greet(NOW + sel.COOLDOWN + timedelta(hours=1), "s3")  # cooldown over
    assert "aquarium" in again.text
    await sel.settle(again, produced=True)
    sel._reset_state()
    last = await greet(NOW + 2 * sel.COOLDOWN + timedelta(hours=2), "s4")  # MAX_SURFACED reached
    assert last is None or "aquarium" not in last.text
    assert ("open_loop", 2) in [row[:2] for row in _surfaced(seeded)]


async def test_command_turns_never_cue_turns_do(seeded):
    assert await sel.prepare("turn on the kitchen lights", MEMBER, "s1") is None  # command
    assert await sel.prepare("I had pasta for dinner", MEMBER, "s1") is None       # no cue
    cue = await sel.prepare("the kitchen is nearly finished", MEMBER, "s1")
    assert cue is not None and cue.shape == "cue" and "kitchen" in cue.text


async def test_the_day_brief_wins_the_turn(seeded, caplog):
    with caplog.at_level(logging.INFO, logger="proactive.selector"):
        assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1", brief_active=True) is None
    assert "injected=0 settled=0 reason=brief" in caplog.text
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is not None  # it waited


async def test_continuity_turns_defer(seeded):
    assert await sel.prepare("Ugh, I'm so anxious about the interview.", MEMBER, "s1") is None


@pytest.mark.parametrize("uid, eligible", [
    ("test-sec-b-a1b2c3", False), ("demo-user", False), ("guest", False),
    ("member-b", True),
    ("demo_bar_0a1b2c3d", True),  # harness-minted: only the internal S5 hook seeds it
])
async def test_synthetic_users_never_raise(env, uid, eligible):
    _loop(env["db"], "User has a dentist appointment tomorrow", user=uid)
    await sel.select_for_user(uid, now=NOW)
    assert (await sel.prepare("Hi Zoe, how are things?", uid, "s1") is not None) is eligible


async def test_settle_only_on_produced_text(seeded):
    raised = await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1")
    assert await sel.settle(raised, produced=False) is False
    assert not _surfaced(seeded)
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is not None  # hold released


async def test_negative_control_the_flag_read_gates_the_runtime(seeded, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "off")
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is None


# ── both lanes: block placement + settle-in-finally by text emitted ──────────
def _flue_turn(monkeypatch, mode):
    seen = {}

    async def fake_turn(message, session_id, user_id="", **kwargs):
        seen["raise"] = kwargs.get("raise_block")
        if mode == "fallback":
            yield flue._FALLBACK_TEXT
            return
        yield "Good, thanks! "
        if mode == "fail_after_text":
            raise RuntimeError("stream died")

    monkeypatch.setattr(flue, "_run_flue_brain_streaming_turn", fake_turn)
    return seen


@pytest.mark.parametrize("mode, settled", [("ok", 1), ("fail_after_text", 1), ("fallback", 0)])
async def test_flue_lane_settles_by_text_emitted(seeded, monkeypatch, mode, settled):
    seen = _flue_turn(monkeypatch, mode)
    with contextlib.suppress(RuntimeError):
        async for _ in flue.run_flue_brain_streaming("Hi Zoe, how are things?", "s1", MEMBER):
            pass
    assert seen["raise"].startswith(sel.RAISE_OPEN)
    assert len(_surfaced(seeded)) == settled


async def test_flue_lane_brief_wins(seeded, monkeypatch):
    import brief_first_turn

    class _Brief:
        block = "[Today 2026-09-30]\n- x\n[END Today]"

    async def fake_prepare(*a):
        return _Brief()

    async def fake_settle(*a, **k):
        return True

    monkeypatch.setattr(brief_first_turn, "prepare", fake_prepare)
    monkeypatch.setattr(brief_first_turn, "settle", fake_settle)
    seen = _flue_turn(monkeypatch, "ok")
    async for _ in flue.run_flue_brain_streaming("Hi Zoe, how are things?", "s1", MEMBER):
        pass
    assert seen["raise"] == "" and not _surfaced(seeded)


async def test_flue_turn_puts_the_raise_after_the_words_and_defers_the_offer(monkeypatch, caplog):
    import httpx

    monkeypatch.setenv("ZOE_FLUE_WIRE", "1")
    monkeypatch.setenv("ZOE_SEAM_OFFER_INJECT", "1")
    captured = {}

    async def offer(uid):
        return "[PENDING CONTACT OFFER — do not mention this block]\nask\n[END PENDING CONTACT OFFER]"

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, content=None, headers=None):
            import json as _j
            captured["msg"] = _j.loads(content)["message"]
            return type("R", (), {"status_code": 200, "raise_for_status": lambda s: None,
                                  "json": lambda s: {"result": {"text": "ok"}}})()

    monkeypatch.setattr(flue, "_pending_offer_block", offer)
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    block = f"{sel.RAISE_OPEN}\nEarlier they told you: x.\n{sel.RAISE_CLOSE}"
    with caplog.at_level(logging.INFO):
        _ = [d async for d in flue._run_flue_brain_streaming_turn(
            "Hi Zoe", "s1", MEMBER, raise_block=block)]
    assert captured["msg"].index("Hi Zoe") < captured["msg"].index(sel.RAISE_OPEN)
    assert "PENDING CONTACT OFFER" not in captured["msg"] and "reason=raise" in caplog.text
    assert ("[RAISE", sel.RAISE_CLOSE) in flue._FLUE_CONTEXT_BLOCKS


class _Worker:
    def __init__(self, mode):
        self.mode, self.composed = mode, ""

    async def stream(self, compose, *, timeout_s):
        self.composed = await compose()
        if self.mode == "fail_before_text":
            raise RuntimeError("worker died")
        yield "Good, thanks!"


@pytest.mark.parametrize("mode, settled", [("ok", 1), ("fail_before_text", 0)])
async def test_core_lane_block_before_the_utterance_and_settles_by_text(seeded, monkeypatch, mode, settled):
    worker = _Worker(mode)

    async def fake_worker_for(*a, **k):
        return worker

    async def no_packet(*a, **k):
        return ""

    monkeypatch.setattr(core, "_worker_for", fake_worker_for)
    monkeypatch.setattr(core, "_memory_packet_block", no_packet)
    with contextlib.suppress(RuntimeError):
        async for _ in core.run_zoe_core_streaming("Hi Zoe, how are things?", "s1", MEMBER):
            pass
    lines = worker.composed.splitlines()
    assert core._RAISE_LABEL in lines and lines.index(core._RAISE_CLOSE) < lines.index(core._UTTERANCE_MARKER)
    assert (core._RAISE_LABEL, core._RAISE_CLOSE) in core._CONTEXT_BLOCKS
    assert len(_surfaced(seeded)) == settled


# ── migration ────────────────────────────────────────────────────────────────
def test_migration_is_idempotent_and_reversible(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    _migrate(engine, "upgrade")
    _migrate(engine, "upgrade")  # re-run: a no-op
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(proactive_candidates)")}
    assert {"user_id", "kind", "source_ref", "salience", "on_open", "cue_words", "expires_at",
            "cooldown_until", "surfaced_count", "last_surfaced_session"} <= cols
    _migrate(engine, "downgrade")
    with engine.connect() as conn:
        assert not conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name='proactive_candidates'").fetchall()


async def test_prepare_never_raises_and_is_time_boxed(seeded, monkeypatch):
    async def slow(uid):
        await asyncio.sleep(5)

    monkeypatch.setattr(sel, "_load", slow)
    monkeypatch.setattr(sel, "_PREPARE_TIMEOUT_S", 0.05)
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is None


# ── the Samantha-bar S5 hook: forget-synthetic guards, fail closed ───────────
class _Req:
    headers = {"X-Internal-Token": "t"}


@pytest.fixture
def hook(monkeypatch):
    import auth
    import memory_digest
    import routers.memories as memories
    from routers import proactive as route

    calls = {"extract": [], "select": [], "registered": False, "token": True}

    async def registered(uid):
        if calls["registered"] is None:
            raise RuntimeError("auth_users unreachable")
        return calls["registered"]

    async def extract(uid, db=None):
        calls["extract"].append(uid)
        return {"status": "ok"}

    async def select(uid, **kw):
        calls["select"].append(uid)
        return {"kept": 1, "kinds": ["emotional"]}

    monkeypatch.setattr(auth, "_has_valid_internal_token", lambda r: calls["token"])
    monkeypatch.setattr(memories, "_registered_account", registered)
    monkeypatch.setattr(memory_digest, "_extract_open_loops", extract)
    monkeypatch.setattr(sel, "select_for_user", select)
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "1")
    return route, calls


async def test_hook_runs_extraction_then_selection_for_a_harness_id(hook):
    route, calls = hook
    out = await route.run_selector_synthetic("demo_bar_0a1b2c3d", _Req())
    assert out == {"enabled": True, "open_loops": "ok", "kept": 1, "kinds": ["emotional"]}
    assert calls["extract"] == calls["select"] == ["demo_bar_0a1b2c3d"]


@pytest.mark.parametrize("uid, token, registered, code", [
    ("demo_bar_0a1b2c3d", False, False, 403),   # no/invalid internal token
    ("member-a", True, False, 403),             # a real member: never through the hook
    ("demo-user", True, False, 403),            # synthetic but not harness-minted
    ("demo_bar_0a1b2c3d", True, True, 403),     # a registered account named like a demo id
    ("demo_bar_0a1b2c3d", True, None, 409),     # registration unverifiable: fail closed
])
async def test_hook_refuses_everything_else(hook, uid, token, registered, code):
    from fastapi import HTTPException

    route, calls = hook
    calls["token"], calls["registered"] = token, registered
    with pytest.raises(HTTPException) as err:
        await route.run_selector_synthetic(uid, _Req())
    assert err.value.status_code == code and not calls["extract"] and not calls["select"]


async def test_hook_is_inert_with_the_flag_off(hook, monkeypatch):
    route, calls = hook
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "0")
    assert await route.run_selector_synthetic("demo_bar_0a1b2c3d", _Req()) == {"enabled": False}
    assert not calls["extract"]
