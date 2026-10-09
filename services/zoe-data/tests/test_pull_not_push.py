"""Pull, not push (register BH1) and the delivery-ledger lines + welcome tap (BH2).

``proactive/pull.py`` (``ZOE_PULL_NOT_PUSH``, default on), ``proactive/lines.py``
(``ZOE_DELIVERY_LEDGER``, default shadow), the selector hooks, ``fast_tiers._pull_tier``, the
``/api/proactive/inbox`` + ``/welcome`` handlers and the brief's pulled-today filter.

Fixtures only - synthetic names and ids, no household text. The DB edge is a real SQLite file built by
migrations 0033 + 0036 + 0041 behind db_pool's own cursor types, so the compare-and-set UPDATE, the
idempotent INSERT, the aggregate reads and the selector's own SQL run for real.

Negative controls (each was run red before commit):
  * ``pull_enabled`` always True                                  -> the flag-off tests red;
  * drop ``surfaced_count = ?`` from the pull's UPDATE            -> the concurrent-ask test red;
  * stop marking pulled candidates surfaced                       -> the second-ask / clears test red;
  * ``pending_items`` ignores the cooldown                        -> the cleared-by-a-raise test red;
  * ``_member`` lets a guest through                              -> the guest tests red;
  * put "whats new" into PULL_PHRASES                             -> the greeting-stays-a-greeting test red;
  * ``class_hold`` always ""                                      -> the "not now" back-off tests red;
  * apply the back-off in shadow mode                             -> the byte-identical test red;
  * let a welcome tap relax the hold                              -> the welcome-never-raises test red;
  * ``compose`` loses ``second_person``                           -> the voice-of-Zoe test red;
  * drop the ``outcome`` close of a pull row                      -> the ledger-row test red;
  * the endpoint returns the top item's text                      -> the content-free test red;
  * lines written with the mode ``off``                           -> the off test red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # every edge is stubbed; slim-dep modules only

import asyncio
import contextlib
import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import db_compat
from db_pool import _Cursor, _ExecResult
from proactive import ledger, lines, pull
from proactive import selector as sel

SVC = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc)   # 09:00 Perth, 01:00 UTC
MEMBER, OTHER = "member-a", "member-b"
INTERVIEW = "User is anxious about a job interview at the aquarium on Friday"
DENTIST_Q = "How did the dentist go?"
SECRET_WORDS = ("aquarium", "interview", "dentist", "kestrel", "juniper")


def _migrate(engine, fname, fn="upgrade"):
    spec = importlib.util.spec_from_file_location("mig", SVC / "alembic/versions" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(mod, fn)()
    return mod


class _Sqlite:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.calls = 0

    def execute(self, sql, params=()):
        self.calls += 1

        async def _run():
            await asyncio.sleep(0)   # a real await point: overlapping asks interleave like the pool's
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    for name in ("0033_proactive_candidates.py", "0036_proactive_deliveries.py",
                 "0041_proactive_ledger_lines.py"):
        _migrate(engine, name)
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

    class _Mem:
        async def load_recent_for_prompt(self, user_id, **kw):
            return []

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Mem())

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setenv("ZOE_PROACTIVE_SELECTOR", "1")
    monkeypatch.setenv("ZOE_PROACTIVE_LEDGER", "1")
    monkeypatch.setenv("ZOE_TIMEZONE", "UTC")
    for key in ("ZOE_PULL_NOT_PUSH", "ZOE_DELIVERY_LEDGER", "ZOE_BRIEF_ON_FIRST_TURN",
                "ZOE_SYNTHETIC_USER_ALLOWLIST", "ZOE_PROACTIVE_RAISE_GAP_S",
                "ZOE_PROACTIVE_RAISE_PER_DAY", "ZOE_LOOP_LIFECYCLE", "ZOE_SEAM_RECALL_INJECT",
                "ZOE_SEAM_OFFER_INJECT"):
        monkeypatch.delenv(key, raising=False)
    sel._reset_state()
    lines._reset_state()
    state = {"now": NOW, "db": db, "n": 0}
    monkeypatch.setattr(sel, "_now", lambda: state["now"])
    monkeypatch.setattr(pull, "_now", lambda: state["now"])
    monkeypatch.setattr(lines, "_now", lambda: state["now"])
    yield state
    sel._reset_state()
    lines._reset_state()


def _cand(env, *, kind="open_loop", text=INTERVIEW, hint="", sal=0.8, cues="interview job",
          user=MEMBER, ref=None, expires=None, cooldown=None, surfaced=0, last=None):
    env["n"] += 1
    ref = ref or f"{ {'open_loop': 'open_loops', 'emotional': 'memory', 'event': 'events'}[kind] }:{env['n']}"
    stamp = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    env["db"].conn.execute(
        "INSERT INTO proactive_candidates (id, user_id, kind, source_ref, text, hint, salience, "
        "on_open, cue_words, expires_at, cooldown_until, surfaced_count, last_surfaced_at, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
        (f"c{env['n']}", user, kind, ref, text, hint, sal, cues,
         (expires or NOW + timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
         cooldown.strftime("%Y-%m-%dT%H:%M:%SZ") if cooldown else None, surfaced, last, stamp, stamp))
    env["db"].conn.commit()
    return f"c{env['n']}"


def _lines(env, line=None):
    sql = "SELECT line, kind, reason, shape, signal, source_ref FROM proactive_ledger_lines"
    if line:
        return env["db"].rows(sql + " WHERE line = ? ORDER BY created_at, id", (line,))
    return env["db"].rows(sql + " ORDER BY created_at, id")


def _deliveries(env):
    return env["db"].rows("SELECT kind, shape, delivered_by, voiced, outcome FROM proactive_deliveries")


def _surfaced(env, cid):
    return env["db"].rows("SELECT surfaced_count, cooldown_until, last_surfaced_session "
                          "FROM proactive_candidates WHERE id = ?", (cid,))[0]


# ── migration ─────────────────────────────────────────────────────────────────────────────
def test_migration_0041_is_idempotent_and_chained():
    engine = sa.create_engine("sqlite://")
    mod = _migrate(engine, "0041_proactive_ledger_lines.py")
    assert (mod.revision, mod.down_revision) == ("0041", "0040")
    _migrate(engine, "0041_proactive_ledger_lines.py")  # already present: a no-op
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(proactive_ledger_lines)")}
        idx = {r[1] for r in conn.exec_driver_sql("PRAGMA index_list(proactive_ledger_lines)")}
    assert cols == {"id", "idem_key", "user_id", "line", "kind", "klass", "source_ref", "reason",
                    "score", "shape", "channel", "session_id", "sensitivity", "signal", "target_id",
                    "created_at"}
    assert {"idx_proactive_ledger_lines_user", "idx_proactive_ledger_lines_line"} <= idx
    _migrate(engine, "0041_proactive_ledger_lines.py", "downgrade")
    with engine.connect() as conn:
        assert not conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name='proactive_ledger_lines'").fetchall()


def test_no_request_time_ddl():
    for rel in ("proactive/pull.py", "proactive/lines.py"):
        src = (SVC / rel).read_text()
        assert "CREATE TABLE" not in src and "ALTER TABLE" not in src, rel


# ── flags ─────────────────────────────────────────────────────────────────────────────────
def test_flag_defaults_and_kill_values(monkeypatch):
    monkeypatch.delenv("ZOE_PULL_NOT_PUSH", raising=False)
    monkeypatch.delenv("ZOE_DELIVERY_LEDGER", raising=False)
    assert pull.pull_enabled() is True and lines.delivery_ledger_mode() == "shadow"
    for off in ("0", "off", "false", "no", "OFF"):
        monkeypatch.setenv("ZOE_PULL_NOT_PUSH", off)
        assert pull.pull_enabled() is False, off
    for raw, want in (("off", "off"), ("0", "off"), ("on", "on"), ("1", "on"), ("shadow", "shadow"),
                      ("garbage", "shadow"), ("", "shadow")):
        monkeypatch.setenv("ZOE_DELIVERY_LEDGER", raw)
        assert lines.delivery_ledger_mode() == want, raw


# ── the phrases ───────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "What's up?", "whats up", "Hey Zoe, what's up?", "what's up Zoe", "Anything for me?",
    "anything for me zoe", "Do you have anything for me?", "Have you got anything for me",
    "What have I got?", "what have you got for me", "Is there anything for me?",
    "anything new for me", "Anything I need to know?", "what's waiting for me", "what's pending",
])
def test_pull_phrases(text):
    assert pull.classify(text) == "pull" and pull.is_pull(text)


@pytest.mark.parametrize("text", [
    "Hi Zoe, how are things?", "Hey Zoe, what's new?", "what's happening", "what's going on",
    "what's on today", "what have I got today", "what's on my calendar", "what's up with the lights",
    "turn on the lights", "what's up with my mum", "do I have anything on tomorrow", "good morning",
    "Hey Zoe, what's up with the weather", "what is the time", "anything else",
])
def test_greetings_and_commands_are_not_pulls(text):
    """'what's new' stays a greeting: the day brief and the single raise (bar S5) answer it."""
    assert pull.classify(text) is None


@pytest.mark.parametrize("text, want", [
    ("Not now", "tap:not_now"), ("not right now, Zoe", "tap:not_now"),
    ("That was welcome", "tap:welcome"), ("that was helpful", "tap:welcome"),
    ("That was fine", "tap:neutral"), ("that was okay", "tap:neutral"),
])
def test_tap_phrases(text, want):
    assert pull.classify(text) == want


# ── the pending queue and the orb state ───────────────────────────────────────────────────
async def test_pending_state_is_a_count_and_a_class_never_content(env):
    _cand(env, kind="event", text="Dentist (Thu 10:00)", sal=0.5, cues="dentist")
    _cand(env, kind="open_loop", text=INTERVIEW, sal=0.9)
    state = await pull.pending_state(MEMBER, NOW)
    assert state["enabled"] is True and state["count"] == 2
    assert state["top"] == "question"                       # the highest-salience item's coarse class
    blob = json.dumps(state).lower()
    assert not any(w in blob for w in SECRET_WORDS), blob   # no content, no kind, no ref
    assert set(state) == {"enabled", "count", "top", "quiet"}


async def test_pending_excludes_expired_cooled_and_twice_raised(env):
    _cand(env, text="User has a vet visit on Monday", cues="vet")
    _cand(env, expires=NOW - timedelta(hours=1))                      # expired
    _cand(env, cooldown=NOW + timedelta(days=2), surfaced=1)          # raised, cooling down
    _cand(env, surfaced=2)                                            # raised twice: never again
    _cand(env, user=OTHER)                                            # another member's
    state = await pull.pending_state(MEMBER, NOW)
    assert state["count"] == 1


async def test_guest_flag_off_and_selector_off_read_zero(env, monkeypatch):
    _cand(env)
    for guest in ("guest", "anonymous", "voice-guest", ""):
        assert (await pull.pending_state(guest, NOW))["count"] == 0, guest
    monkeypatch.setenv("ZOE_PULL_NOT_PUSH", "off")
    off = await pull.pending_state(MEMBER, NOW)
    assert off["enabled"] is False and off["count"] == 0
    monkeypatch.delenv("ZOE_PULL_NOT_PUSH")
    monkeypatch.delenv("ZOE_PROACTIVE_SELECTOR")
    assert (await pull.pending_state(MEMBER, NOW))["count"] == 0     # nothing generated here


async def test_quiet_flag_follows_the_household_quiet_hours(env):
    _cand(env)
    assert (await pull.pending_state(MEMBER, NOW))["quiet"] is False
    night = datetime(2026, 10, 8, 16, 0, tzinfo=timezone.utc)        # 00:00 Perth
    assert (await pull.pending_state(MEMBER, night))["quiet"] is True


# ── the pull ──────────────────────────────────────────────────────────────────────────────
async def test_pull_delivers_in_priority_order_once_and_clears(env):
    a = _cand(env, text="User has a vet visit on Monday", sal=0.4, cues="vet")
    b = _cand(env, text=INTERVIEW, sal=0.9)
    res = await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    assert res.delivered == 2 and res.kinds == ["open_loop", "open_loop"]
    first, second = res.reply.lower().index("interview"), res.reply.lower().index("vet")
    assert first < second                                              # salience order
    assert res.reply.startswith("I've got two things for you.")
    for cid in (a, b):
        count, cooldown, session = _surfaced(env, cid)
        assert count == 1 and cooldown and session == "s1"             # marked exactly as a raise
    again = await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    assert again.reply == pull.EMPTY_REPLY and again.delivered == 0     # a second ask says nothing new
    assert (await pull.pending_state(MEMBER, NOW))["count"] == 0       # the orb clears


async def test_a_pull_clears_the_selector_too_and_is_never_re_raised(env):
    _cand(env, sal=0.9)
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is not None   # would raise now
    sel._reset_state()
    await pull.pull(MEMBER, "s2", channel="chat", now=NOW)
    sel._reset_state()
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s3") is None       # cooled by the pull


async def test_pull_ignores_the_raise_gap_and_the_daily_cap(env, monkeypatch):
    """The person asked: a raise 5 minutes ago and a full daily cap do not stop a pull."""
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_GAP_S", "7200")
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_PER_DAY", "1")
    _cand(env, text="User has a vet visit on Monday", cues="vet", surfaced=1,
          cooldown=NOW + timedelta(days=3), last=(NOW - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    _cand(env, sal=0.9)
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is None       # held: gap
    res = await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    assert res.delivered == 1 and "interview" in res.reply.lower()


async def test_spoken_pull_says_three_and_keeps_the_rest_pending(env):
    for i in range(5):
        _cand(env, text=f"User has a task number{i} due soon", sal=0.9 - i * 0.1, cues=f"number{i}")
    res = await pull.pull(MEMBER, "s1", channel="voice", now=NOW)
    assert res.delivered == 3 and "2 more" in res.reply and "ask me again" in res.reply
    assert "number0" in res.reply and "number2" in res.reply and "number3" not in res.reply
    assert (await pull.pending_state(MEMBER, NOW))["count"] == 2        # not marked, not lost
    res2 = await pull.pull(MEMBER, "s1", channel="voice", now=NOW)
    assert res2.delivered == 2 and "more" not in res2.reply
    assert (await pull.pull(MEMBER, "s1", channel="voice", now=NOW)).reply == pull.EMPTY_REPLY


async def test_chat_pull_delivers_everything_with_a_one_tap_row(env):
    for i in range(5):
        _cand(env, text=f"User has a task number{i} due soon", sal=0.9 - i * 0.1, cues=f"number{i}")
    res = await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    assert res.delivered == 5 and "number4" in res.reply and "more" not in res.reply
    comp = res.ui["zoe_component"]
    assert [a["query"] for a in comp["actions"]] == ["That was welcome", "That was fine", "Not now"]
    assert all(pull.tap_signal(a["query"]) for a in comp["actions"])    # each tap is a phrase we catch
    spoken = await pull.pull(MEMBER, "s2", channel="voice", now=NOW)    # nothing left: no row, no ui
    assert spoken.ui in (None, {"kind": "pull"}) or "zoe_component" not in spoken.ui


async def test_voice_of_zoe_is_second_person_and_event_aware(env):
    _cand(env, kind="event", text="Dentist (Thu 10:00)", sal=0.9, cues="dentist")
    _cand(env, kind="open_loop", text=INTERVIEW, sal=0.8)
    _cand(env, kind="emotional", text="User felt overwhelmed about moving house", sal=0.7, cues="moving")
    _cand(env, kind="open_loop", text="Dentist follow-up", hint=DENTIST_Q, sal=0.6, cues="dentist")
    _cand(env, kind="open_loop", text="Tidy the shed", hint="briefly and warmly ask how that is going",
          sal=0.5, cues="shed")
    reply = (await pull.pull(MEMBER, "s1", channel="chat", now=NOW)).reply
    assert "You've got Dentist on Thursday at 10:00." in reply
    assert "you're anxious about a job interview at the aquarium on Friday - how's that going?" in reply
    assert "you felt overwhelmed about moving house - how is that going now?" in reply
    assert f"I wanted to ask: {DENTIST_Q}" in reply
    assert "tidy the shed - how's that going?" in reply                 # an INSTRUCTION hint is never spoken
    assert "briefly and warmly" not in reply and "User" not in reply


@pytest.mark.parametrize("text, want", [
    ("User is anxious about X", "you're anxious about X"),
    ("User was sick", "you were sick"),
    ("User has a vet visit", "you have a vet visit"),
    ("User's mum is visiting", "your mum is visiting"),
    ("The user will travel", "you'll travel"),
    ("Dentist on Thursday", "dentist on Thursday"),
])
def test_second_person(text, want):
    assert pull.second_person(text) == want


async def test_pull_records_the_ledger_rows_closed_and_voiced(env):
    _cand(env, sal=0.9)
    await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    assert _deliveries(env) == [("open_loop", "pull", "pull", 1, "accepted")]   # nothing for the sweep
    (row,) = _lines(env, "pulled")
    assert row[0] == "pulled" and row[2] == "asked" and row[3] == "pull"
    assert await ledger.sweep(now=NOW + timedelta(hours=2)) == 0               # a pull needs no judging


async def test_a_guest_asking_gets_nothing(env):
    _cand(env, sal=0.9)
    for guest in ("guest", "voice-guest", "anonymous", ""):
        assert await pull.pull(guest, "s1", channel="chat", now=NOW) is None
    assert _surfaced(env, "c1")[0] == 0 and _lines(env) == []


async def test_even_a_candidate_filed_under_a_guest_id_is_never_delivered(env):
    """Belt and braces: whatever a bug files under a guest sentinel, the pull and the orb refuse."""
    for guest in ("guest", "voice-guest"):
        _cand(env, user=guest, sal=0.9)
        assert await pull.pull(guest, "s1", channel="chat", now=NOW) is None
        assert (await pull.pending_state(guest, NOW))["count"] == 0


async def test_another_member_never_sees_it(env):
    _cand(env, sal=0.9)
    res = await pull.pull(OTHER, "s1", channel="chat", now=NOW)
    assert res.reply == pull.EMPTY_REPLY and _surfaced(env, "c1")[0] == 0


async def test_flag_off_and_selector_off_are_not_ours(env, monkeypatch):
    _cand(env, sal=0.9)
    monkeypatch.setenv("ZOE_PULL_NOT_PUSH", "off")
    assert await pull.pull(MEMBER, "s1", channel="chat", now=NOW) is None
    monkeypatch.delenv("ZOE_PULL_NOT_PUSH")
    monkeypatch.delenv("ZOE_PROACTIVE_SELECTOR")
    assert await pull.pull(MEMBER, "s1", channel="chat", now=NOW) is None
    assert _surfaced(env, "c1")[0] == 0


async def test_two_overlapping_asks_deliver_once(env):
    _cand(env, sal=0.9)
    a, b = await asyncio.gather(pull.pull(MEMBER, "s1", channel="chat", now=NOW),
                                pull.pull(MEMBER, "s2", channel="chat", now=NOW))
    got = [r for r in (a, b) if r is not None and r.delivered]
    assert len(got) == 1 and _surfaced(env, "c1")[0] == 1


async def test_empty_hands_the_turn_on_when_the_morning_brief_is_about_to_speak(env, monkeypatch):
    async def will_fire(uid, now):
        return True
    monkeypatch.setattr(pull, "_brief_will_fire", will_fire)
    assert await pull.pull(MEMBER, "s1", channel="chat", now=NOW) is None        # the brain gives the brief
    _cand(env, sal=0.9)
    assert (await pull.pull(MEMBER, "s1", channel="chat", now=NOW)).delivered == 1  # pending wins


async def test_the_brief_skips_what_was_pulled_today(env):
    from brief_first_turn import without_refs

    ctx = {"open_loops": [{"id": 1, "text": INTERVIEW}, {"id": 2, "text": "vet visit"}],
           "emotional_moments": ["felt low", "felt great"], "emotional_moment_ids": ["m1", "m2"],
           "calendar": [{"title": "Dentist"}]}
    out = without_refs(ctx, {"open_loops:1", "memory:m2"})
    assert [lp["id"] for lp in out["open_loops"]] == [2]
    assert out["emotional_moments"] == ["felt low"] and out["emotional_moment_ids"] == ["m1"]
    assert out["calendar"] == ctx["calendar"] and len(ctx["open_loops"]) == 2      # the input is not mutated
    assert without_refs(ctx, set()) is ctx and without_refs(ctx, {"open_loops:9"}) is ctx
    _cand(env, sal=0.9, ref="open_loops:5")
    await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    assert await pull.pulled_refs(MEMBER, NOW) == {"open_loops:5"}
    assert await pull.pulled_refs(OTHER, NOW) == set()


async def test_pulled_refs_off_with_the_flag_or_the_ledger(env, monkeypatch):
    _cand(env, sal=0.9, ref="open_loops:5")
    await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    monkeypatch.setenv("ZOE_PULL_NOT_PUSH", "off")
    assert await pull.pulled_refs(MEMBER, NOW) == set()


# ── the restraint tier, by name, when it exists ───────────────────────────────────────────
async def test_without_the_restraint_column_a_plain_item_is_spoken(env):
    _cand(env, text="User has a vet visit on Monday", sal=0.9, cues="vet")
    lines._reset_state()
    assert await lines._has_sensitivity_column() is False
    res = await pull.pull(MEMBER, "s1", channel="voice", speaker_verified=False, now=NOW)
    assert res.delivered == 1                                  # no class, nothing to hold


async def test_without_the_column_the_restraint_class_still_holds_a_sensitive_item_from_an_unverified_voice(env, monkeypatch):
    # the shipped schema has no proactive_candidates.sensitivity: the class is restraint's (computed from the text, or stored in restraint_classes)
    monkeypatch.setenv("ZOE_RESTRAINT", "enforce")
    _cand(env, text="User has a clinic appointment for the blood test results", sal=0.9, cues="clinic")
    _cand(env, text="User has a vet visit on Monday", sal=0.5, cues="vet")
    lines._reset_state()
    assert await lines._has_sensitivity_column() is False
    spoken = await pull.pull(MEMBER, "s1", channel="voice", speaker_verified=False, now=NOW)
    assert spoken.delivered == 1 and "vet" in spoken.reply and "clinic" not in spoken.reply and "something private" in spoken.reply
    assert (await pull.pending_state(MEMBER, NOW))["count"] == 1            # the clinic item is still pending for chat
    chat = await pull.pull(MEMBER, "s2", channel="chat", now=NOW)
    assert chat.delivered == 1 and "clinic" in chat.reply


async def test_the_restraint_off_switch_is_honoured_by_the_pull(env, monkeypatch):
    monkeypatch.setenv("ZOE_RESTRAINT", "off")
    _cand(env, text="User has a clinic appointment for the blood test results", sal=0.9, cues="clinic")
    lines._reset_state()
    res = await pull.pull(MEMBER, "s1", channel="voice", speaker_verified=False, now=NOW)
    assert res.delivered == 1


async def test_a_sensitive_item_is_not_spoken_to_an_unverified_voice_but_chat_has_it(env):
    env["db"].conn.execute("ALTER TABLE proactive_candidates ADD COLUMN sensitivity TEXT")
    cid = _cand(env, text="User has a clinic appointment", sal=0.9, cues="clinic")
    env["db"].conn.execute("UPDATE proactive_candidates SET sensitivity = 'health' WHERE id = ?", (cid,))
    _cand(env, text="User has a vet visit on Monday", sal=0.5, cues="vet")
    env["db"].conn.commit()
    lines._reset_state()
    spoken = await pull.pull(MEMBER, "s1", channel="voice", speaker_verified=False, now=NOW)
    assert spoken.delivered == 1 and "vet" in spoken.reply and "clinic" not in spoken.reply
    assert "something private" in spoken.reply
    (held,) = _lines(env, "withheld")
    assert held[2] == "sensitivity_unverified"
    assert (await pull.pending_state(MEMBER, NOW))["count"] == 1        # still pending for chat
    chat = await pull.pull(MEMBER, "s2", channel="chat", now=NOW)
    assert chat.delivered == 1 and "clinic" in chat.reply
    (pulled,) = _lines(env, "pulled")[-1:]
    assert pulled[0] == "pulled"
    assert env["db"].rows("SELECT sensitivity FROM proactive_ledger_lines WHERE line = 'pulled'")[-1][0] == "health"
    verified = await pull.pull(MEMBER, "s3", channel="voice", speaker_verified=True, now=NOW)
    assert verified.reply == pull.EMPTY_REPLY


# ── the tier in front of the router ───────────────────────────────────────────────────────
async def test_the_tier_answers_before_the_router_and_only_then(env, monkeypatch):
    import fast_tiers

    _cand(env, sal=0.9)
    res = await fast_tiers.resolve("Hey Zoe, what's up?", MEMBER, "s1", channel="chat")
    assert res is not None and res.tier == "pull" and res.intent == "pull" and res.domain == "proactive"
    assert "interview" in res.reply.lower() and res.ui["zoe_component"]["actions"]
    second = await fast_tiers.resolve("what's up?", MEMBER, "s1", channel="chat")
    assert second.reply == pull.EMPTY_REPLY and second.tier == "pull"
    # the greeting keeps going to the brain (resolve returns None for it: the router is off here)
    monkeypatch.setenv("ZOE_PULL_NOT_PUSH", "off")
    assert await fast_tiers.resolve("what's up?", MEMBER, "s1", channel="chat") is None


async def test_the_tier_for_a_guest_is_silent(env):
    import fast_tiers

    _cand(env, sal=0.9)
    assert await fast_tiers.resolve("what's up?", "guest", "s1", channel="chat") is None
    assert await fast_tiers.resolve("anything for me?", "voice-guest", "s1", channel="voice") is None
    assert _surfaced(env, "c1")[0] == 0


async def test_the_voice_lane_passes_speaker_verification_through(env):
    import fast_tiers

    env["db"].conn.execute("ALTER TABLE proactive_candidates ADD COLUMN sensitivity TEXT")
    cid = _cand(env, text="User has a clinic appointment", sal=0.9, cues="clinic")
    env["db"].conn.execute("UPDATE proactive_candidates SET sensitivity = 'health' WHERE id = ?", (cid,))
    env["db"].conn.commit()
    lines._reset_state()
    res = await fast_tiers.resolve("anything for me?", MEMBER, "s1", channel="voice",
                                   extra_ctx={"speaker_verified": False})
    assert "clinic" not in res.reply and "private" in res.reply


def test_the_chat_stream_emits_the_one_tap_row():
    src = (SVC / "routers/chat.py").read_text()
    assert '(getattr(_fp_res, "ui", None) or {}).get("zoe_component")' in src
    assert 'CustomEvent(name="zoe.component", value=_fp_comp)' in src


def test_the_orb_shows_a_dot_and_nothing_else():
    html = (SVC.parents[1] / "services/zoe-ui/dist/touch/home.html").read_text()
    assert "#orb.has::after" in html and "/api/proactive/inbox" in html
    start = html.index("function paint(on){orb.classList.toggle('has'")
    code = html[start: html.index("visibilitychange", start)]       # the IIFE's code, not its comments
    assert "toast(" not in code and "speak" not in code.lower() and "panelVoice" not in code
    assert "Audio" not in code and "/api/voice" not in code


# ── the endpoint (handlers called directly) ───────────────────────────────────────────────
async def test_inbox_endpoint_is_content_free_and_guest_is_zero(env):
    from routers import proactive as r

    _cand(env, sal=0.9)
    member = await r.pending_inbox({"user_id": MEMBER, "role": "user"})
    assert member["count"] == 1 and member["top"] == "question"
    assert not any(w in json.dumps(member).lower() for w in SECRET_WORDS)
    assert (await r.pending_inbox({"user_id": "guest", "role": "guest"}))["count"] == 0
    assert (await r.pending_inbox({"user_id": OTHER, "role": "user"}))["count"] == 0
    await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    assert (await r.pending_inbox({"user_id": MEMBER, "role": "user"}))["count"] == 0   # asked: cleared


# ── BH2: the lines ────────────────────────────────────────────────────────────────────────
async def _raise(env, sid="s1", greet="Hi Zoe, how are things?"):
    raised = await sel.prepare(greet, MEMBER, sid)
    assert raised is not None
    assert await sel.settle(raised, produced=True)
    return raised


async def test_a_raise_is_a_line_with_its_reason_and_score(env):
    _cand(env, sal=0.87)
    await _raise(env)
    ((line, kind, reason, shape, _s, ref),) = _lines(env, "raised")
    assert (line, kind, reason, shape) == ("raised", "open_loop", "open_turn", "greeting")
    assert env["db"].rows("SELECT score, klass, session_id, user_id FROM proactive_ledger_lines")[0] == (
        0.87, "question", "s1", MEMBER)


async def test_a_cue_raise_says_cue(env):
    _cand(env, sal=0.8, cues="interview")
    raised = await sel.prepare("I am still thinking about that interview", MEMBER, "s1")
    await sel.settle(raised, produced=True)
    assert _lines(env, "raised")[0][2] == "cue"


async def test_every_held_candidate_is_a_withheld_line_with_the_gate_named(env, monkeypatch):
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_GAP_S", "7200")
    _cand(env, sal=0.9)
    _cand(env, text="User has a vet visit on Monday", sal=0.5, cues="vet")
    await _raise(env, "s1")
    env["now"] = NOW + timedelta(minutes=5)
    assert await sel.prepare("Hey Zoe, how are you?", MEMBER, "s2") is None
    held = _lines(env, "withheld")
    assert [(h[0], h[2]) for h in held] == [("withheld", "gap")]
    assert await sel.prepare("Hey Zoe, how are you?", MEMBER, "s2") is None       # next turn, same day
    sel._reset_state()
    assert await sel.prepare("Hey Zoe, how are you?", MEMBER, "s9") is None       # another conversation, same day
    assert len(_lines(env, "withheld")) == 1                                        # one line per item/reason/day
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_GAP_S", "0")
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_PER_DAY", "1")
    assert await sel.prepare("Hey Zoe, how are you?", MEMBER, "s3") is None
    assert {h[2] for h in _lines(env, "withheld")} == {"gap", "daily_cap"}


async def test_the_brief_owning_the_turn_is_a_withheld_line(env):
    _cand(env, sal=0.9)
    assert await sel.prepare("Good morning Zoe", MEMBER, "s1", brief_active=True) is None
    assert [(h[0], h[2]) for h in _lines(env, "withheld")] == [("withheld", "brief")]


async def test_a_brief_mention_is_a_raised_line(env):
    await sel.mark_brief_surfaced(MEMBER, "s9", [("open_loop", "open_loops:7", "The aquarium interview")])
    ((line, _k, reason, shape, _s, ref),) = _lines(env, "raised")
    assert (line, reason, shape, ref) == ("raised", "brief", "brief", "open_loops:7")


async def test_no_text_is_ever_stored_or_logged(env, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    _cand(env, sal=0.9)
    await _raise(env)
    await pull.pull(MEMBER, "s2", channel="chat", now=NOW)
    await lines.record_tap(MEMBER, "not_now", now=NOW)
    dump = json.dumps(env["db"].rows("SELECT * FROM proactive_ledger_lines")).lower()
    logs = " ".join(r.getMessage() for r in caplog.records).lower()
    for word in ("aquarium", "interview", "anxious"):
        assert word not in dump and word not in logs


async def test_mode_off_writes_nothing_and_touches_no_db(env, monkeypatch):
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "off")
    _cand(env, sal=0.9)
    await _raise(env)
    env["now"] = NOW + timedelta(minutes=1)
    await sel.prepare("Hey Zoe, how are you?", MEMBER, "s2")
    before = env["db"].calls
    assert await lines.record_tap(MEMBER, "not_now", now=NOW) == 0
    assert await lines.log_held(candidate_id="c1", user_id=MEMBER, reason="gap", shape="x",
                                session_id="s") is None
    assert env["db"].calls == before
    assert _lines(env) == []
    assert await pull.tap(MEMBER, "not now") is None


async def test_shadow_changes_no_reply_byte_for_byte(env, monkeypatch):
    """The ledger on (shadow) vs off: the block the brain would get is identical."""
    _cand(env, sal=0.9)
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "off")
    off = await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1")
    sel._reset_state()
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "shadow")
    on = await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1")
    assert off is not None and on is not None
    assert (off.block, off.shape, off.kind) == (on.block, on.shape, on.kind)


# ── BH2: the welcome tap ──────────────────────────────────────────────────────────────────
async def test_not_now_after_a_raise_is_logged_and_replaced_by_the_last_tap(env):
    _cand(env, sal=0.9)
    await _raise(env)
    assert await lines.record_tap(MEMBER, "not_now", now=NOW) == 1
    assert await lines.record_tap(MEMBER, "welcome", now=NOW) == 1           # the last tap wins
    taps = _lines(env, "welcome")
    assert len(taps) == 1 and taps[0][4] == "welcome" and taps[0][1] == "open_loop"


async def test_a_tap_with_nothing_recent_is_not_ours(env):
    _cand(env, sal=0.9)
    assert await lines.record_tap(MEMBER, "not_now", now=NOW) == 0
    await _raise(env)
    assert await lines.record_tap(MEMBER, "not_now", now=NOW + timedelta(minutes=11)) == 0   # too late
    assert await lines.record_tap(MEMBER, "banana", now=NOW) == 0
    assert await lines.record_tap("", "not_now", now=NOW) == 0
    assert await lines.record_tap(OTHER, "not_now", now=NOW) == 0                            # not their raise
    assert await pull.tap(MEMBER, "Not now", now=NOW + timedelta(minutes=11)) is None


async def test_the_spoken_not_now_binds_to_the_pull_just_made(env):
    _cand(env, text="User wants to book the car service", sal=0.9, cues="car service")       # a plain thread: a sensitive one is not SPOKEN to an unconfirmed voice
    await pull.pull(MEMBER, "s1", channel="voice", now=NOW)
    reply = await pull.tap(MEMBER, "Not now, Zoe", channel="voice", now=NOW + timedelta(seconds=20))
    assert reply == "Okay, noted."                                            # shadow: it only logs
    ((_l, kind, _r, _s, signal, _ref),) = _lines(env, "welcome")
    assert (kind, signal) == ("open_loop", "not_now")


async def test_not_now_is_honest_about_its_effect_per_mode(env, monkeypatch):
    _cand(env, sal=0.9)
    await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "on")
    assert await pull.tap(MEMBER, "not now", now=NOW) == "Okay, I'll hold off on those."
    assert await pull.tap(MEMBER, "that was welcome", now=NOW) == "Glad that helped."
    assert await pull.tap(MEMBER, "that was fine", now=NOW) == "Noted."


async def test_the_tier_catches_the_taps_only_with_something_recent(env):
    import fast_tiers

    _cand(env, sal=0.9)
    assert await fast_tiers.resolve("Not now", MEMBER, "s1", channel="chat") is None     # nothing to tap
    await fast_tiers.resolve("what's up?", MEMBER, "s1", channel="chat")
    res = await fast_tiers.resolve("Not now", MEMBER, "s1", channel="chat")
    assert res is not None and res.tier == "pull" and res.intent == "pull_not_now"
    assert await fast_tiers.resolve("not now", "guest", "s1", channel="chat") is None


# ── BH2: the taps tune RAISING, per class, and only in mode ``on`` ────────────────────────
async def _tap_not_now(env, kind, *, at):
    """One 'not now' about a delivery of ``kind`` made at ``at``."""
    await lines.record(env["db"], user_id=MEMBER, line="pulled", kind=kind, source_ref=f"{kind}:x",
                       now=at, idem=f"d|{kind}|{at.isoformat()}")
    assert await lines.record_tap(MEMBER, "not_now", now=at) == 1


async def test_one_not_now_doubles_the_gap_for_that_class_only(env, monkeypatch):
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "on")
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_GAP_S", "3600")
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_PER_DAY", "0")
    last = (NOW - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")      # past the 1 h gap, inside 2 h
    _cand(env, kind="open_loop", text="User has a vet visit", cues="vet", sal=0.3, surfaced=1,
          cooldown=NOW + timedelta(days=2), last=last)
    _cand(env, kind="open_loop", sal=0.9)
    _cand(env, kind="event", text="Dentist (Thu 10:00)", sal=0.8, cues="dentist")
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(minutes=80))
    # the open-loop class waits twice as long (the event class is untouched, so it is raised)...
    held_for = await sel.prepare("Hi Zoe, how are things?", MEMBER, "s0")
    assert held_for is not None and held_for.kind == "event"
    sel._reset_state()
    env["db"].conn.execute("DELETE FROM proactive_candidates WHERE kind = 'event'")
    env["db"].conn.commit()
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is None
    assert [(h[0], h[2], h[1]) for h in _lines(env, "withheld")] == [("withheld", "class_backoff", "open_loop")]
    # ...once the doubled gap has passed it is raised again
    env["now"] = NOW + timedelta(hours=1)
    sel._reset_state()
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s2") is not None


async def test_two_not_nows_switch_the_class_off_for_the_week_then_it_returns(env, monkeypatch):
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "on")
    _cand(env, kind="open_loop", sal=0.9)
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(days=2))
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(days=1))
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is None
    assert _lines(env, "withheld")[-1][2] == "class_off"
    env["now"] = NOW + timedelta(days=7)                                     # the week has passed
    sel._reset_state()
    env["db"].conn.execute("UPDATE proactive_candidates SET expires_at = '2099-01-01T00:00:00Z'")
    env["db"].conn.commit()
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s2") is not None


async def test_a_different_class_is_untouched_by_the_taps(env, monkeypatch):
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "on")
    _cand(env, kind="event", text="Dentist (Thu 10:00)", sal=0.9, cues="dentist")
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(days=1))
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(hours=5))
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is not None


async def test_another_members_taps_do_not_tune_this_member(env, monkeypatch):
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "on")
    _cand(env, kind="open_loop", sal=0.9)
    await lines.record(env["db"], user_id=OTHER, line="pulled", kind="open_loop", source_ref="x",
                       now=NOW - timedelta(hours=3), idem="o1")
    await lines.record(env["db"], user_id=OTHER, line="pulled", kind="open_loop", source_ref="y",
                       now=NOW - timedelta(hours=2), idem="o2")
    await lines.record_tap(OTHER, "not_now", now=NOW - timedelta(hours=2))
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is not None


async def test_shadow_logs_what_the_backoff_would_do_and_does_not_do_it(env, monkeypatch):
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "shadow")
    _cand(env, kind="open_loop", sal=0.9)
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(days=2))
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(days=1))
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is not None     # still raised
    assert [(h[0], h[2]) for h in _lines(env, "would_withhold")] == [("would_withhold", "class_off")]
    assert _lines(env, "withheld") == []


async def test_welcome_and_neutral_never_make_raising_more_likely(env, monkeypatch):
    """A tap that could only ever raise the rate of raising is the engagement trap: welcome and
    neutral are labels, never levers."""
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "on")
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_GAP_S", "3600")
    last = (NOW - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _cand(env, kind="open_loop", text="User has a vet visit", cues="vet", sal=0.3, surfaced=1,
          cooldown=NOW + timedelta(days=2), last=last)
    _cand(env, kind="open_loop", sal=0.9)
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(minutes=80))
    for sig in ("welcome", "neutral"):
        await lines.record(env["db"], user_id=MEMBER, line="pulled", kind="open_loop",
                           source_ref=f"o:{sig}", now=NOW - timedelta(minutes=5), idem=f"w|{sig}")
        await lines.record_tap(MEMBER, sig, now=NOW - timedelta(minutes=4))
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is None          # the not-now still holds
    assert _lines(env, "withheld")[-1][2] == "class_backoff"


async def test_a_pull_is_never_held_by_the_backoff(env, monkeypatch):
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "on")
    _cand(env, kind="open_loop", sal=0.9)
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(days=2))
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(days=1))
    assert (await pull.pull(MEMBER, "s1", channel="chat", now=NOW)).delivered == 1


async def test_summary_reads_the_intrusive_rate_per_class(env):
    for i in range(4):
        await lines.record(env["db"], user_id=MEMBER, line="pulled", kind="open_loop",
                           source_ref=f"r{i}", now=NOW - timedelta(hours=i + 1), idem=f"s{i}")
    await lines.record(env["db"], user_id=MEMBER, line="raised", kind="event", source_ref="e1", now=NOW,
                       idem="ev")
    await lines.record_tap(MEMBER, "not_now", target_id=_lines_id(env, "r0"), now=NOW)
    await lines.record(env["db"], user_id=MEMBER, line="withheld", kind="open_loop", source_ref="r9",
                       reason="gap", now=NOW, idem="wh")
    out = await lines.summary(env["db"], MEMBER, days=7, now=NOW)
    assert out["delivered"] == {"open_loop": 4, "event": 1}
    assert out["taps"] == {"not_now": 1} and out["held_by_reason"] == {"gap": 1}
    assert out["intrusive_rate"] == {"open_loop": 0.25, "event": 0.0}


def _lines_id(env, ref):
    return env["db"].rows("SELECT id FROM proactive_ledger_lines WHERE source_ref = ?", (ref,))[0][0]


async def test_the_welcome_endpoint(env):
    from fastapi import HTTPException
    from routers import proactive as r

    _cand(env, sal=0.9)
    await pull.pull(MEMBER, "s1", channel="chat", now=NOW)
    ok = await r.welcome_tap(r.WelcomeBody(signal="not_now"), {"user_id": MEMBER, "role": "user"})
    assert ok["ok"] is True and ok["covered"] == 1 and ok["mode"] == "shadow"
    assert (await r.welcome_tap(r.WelcomeBody(signal="welcome"),
                                {"user_id": "guest", "role": "guest"}))["ok"] is False
    with pytest.raises(HTTPException) as err:
        await r.welcome_tap(r.WelcomeBody(signal="love_it"), {"user_id": MEMBER, "role": "user"})
    assert err.value.status_code == 422


# ── the bar's S5 -> S12 -> S26 -> S27 order, offline, on the real selector ───────────────────
async def test_the_bar_sequence_offline_s5_s12_then_the_pull(env):
    """What samantha_bar does to the demo user, with the real nightly selector: the worry is raised
    ONCE on the first open turn, "what's new?" neither raises it again nor counts as a pull, the
    candidate is re-armed, "What's up?" delivers it once and the second ask says nothing new."""
    import fast_tiers

    created = (NOW - timedelta(hours=26)).strftime("%Y-%m-%d %H:%M:%S")
    due = (NOW + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    env["db"].conn.execute(
        "INSERT INTO open_loops (user_id, loop_text, follow_up_hint, emotional_weight, created_at, "
        "follow_up_after) VALUES (?, ?, '', 5, ?, ?)", (MEMBER, INTERVIEW, created, due))
    env["db"].conn.commit()
    assert (await sel.select_for_user(MEMBER, now=NOW))["kept"] == 1
    assert (await pull.pending_state(MEMBER, NOW))["count"] == 1                # S27: pending, content-free
    raised = await sel.prepare("Hi Zoe, how are things?", MEMBER, "open-1")      # S5: raised on the open turn
    assert raised is not None and await sel.settle(raised, produced=True)
    env["now"] = NOW + timedelta(minutes=2)
    assert await sel.prepare("Hey Zoe, what's new?", MEMBER, "open-2") is None   # S12: spaced
    assert await fast_tiers.resolve("Hey Zoe, what's new?", MEMBER, "open-2", channel="chat") is None
    assert (await pull.pending_state(MEMBER, env["now"]))["count"] == 0          # raised: no longer pending
    env["db"].conn.execute("UPDATE proactive_candidates SET cooldown_until = NULL, surfaced_count = 0, "
                           "last_surfaced_session = NULL, last_surfaced_at = NULL")   # the bar's re-arm
    env["db"].conn.commit()
    assert (await pull.pending_state(MEMBER, env["now"]))["count"] == 1
    first = await fast_tiers.resolve("What's up?", MEMBER, "pull-1", channel="chat")
    second = await fast_tiers.resolve("What's up?", MEMBER, "pull-2", channel="chat")
    assert "interview" in first.reply.lower() and "nothing new" in second.reply.lower()
    assert "interview" not in second.reply.lower()
    assert env["db"].rows("SELECT surfaced_count FROM proactive_candidates")[0][0] == 1
    assert (await pull.pending_state(MEMBER, env["now"]))["count"] == 0
    assert (await pull.pending_state("guest", env["now"]))["count"] == 0
    # the ledger saw both deliveries, each with its reason (the one candidate was cooling after its
    # raise, so there was nothing for the spacing gate to hold on the second open turn)
    assert [(r[0], r[2]) for r in _lines(env)] == [("raised", "open_turn"), ("pulled", "asked")]


# ── review sweep (PR #1935 pass 1) ────────────────────────────────────────────────────────
async def test_an_unknown_speaker_verdict_is_not_verified_for_a_sensitive_item(env):
    """LiveKit passes no verdict (None): a sensitive item must be HELD, spoken only on True."""
    env["db"].conn.execute("ALTER TABLE proactive_candidates ADD COLUMN sensitivity TEXT")
    cid = _cand(env, text="User has a clinic appointment", sal=0.9, cues="clinic")
    env["db"].conn.execute("UPDATE proactive_candidates SET sensitivity = 'health' WHERE id = ?", (cid,))
    env["db"].conn.commit()
    lines._reset_state()
    res = await pull.pull(MEMBER, "s1", channel="livekit", speaker_verified=None, now=NOW)
    assert res.delivered == 0 and "clinic" not in res.reply and "something private" in res.reply
    assert (await pull.pending_state(MEMBER, NOW))["count"] == 1


async def test_one_not_now_covering_two_items_is_one_tap_not_two(env, monkeypatch):
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "on")
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_GAP_S", "3600")
    monkeypatch.setenv("ZOE_PROACTIVE_RAISE_PER_DAY", "0")
    at = NOW - timedelta(minutes=80)
    for ref in ("open_loops:1", "open_loops:2"):
        await lines.record(env["db"], user_id=MEMBER, line="pulled", kind="open_loop",
                           source_ref=ref, now=at)
    assert await lines.record_tap(MEMBER, "not_now", now=at) == 2          # two labelled rows...
    last = (NOW - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _cand(env, kind="open_loop", text="User has a vet visit", cues="vet", sal=0.3, surfaced=1,
          cooldown=NOW + timedelta(days=2), last=last)
    _cand(env, kind="open_loop", sal=0.9)
    # ...but ONE tap: the class doubles its gap, it is not switched off for the week.
    assert await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1") is None
    assert _lines(env, "withheld")[-1][2] == "class_backoff"


async def test_a_held_class_does_not_hide_another_class(env, monkeypatch):
    monkeypatch.setenv("ZOE_DELIVERY_LEDGER", "on")
    _cand(env, kind="open_loop", sal=0.9)
    _cand(env, kind="event", text="Dentist (Thu 10:00)", sal=0.8, cues="dentist")
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(days=2))
    await _tap_not_now(env, "open_loop", at=NOW - timedelta(days=1))
    raised = await sel.prepare("Hi Zoe, how are things?", MEMBER, "s1")
    assert raised is not None and raised.kind == "event"


def test_the_new_flag_readers_use_typed_env_and_the_inbox_poll_is_quiet():
    import middleware.logging as mw

    for mod in (pull, lines):
        src = Path(mod.__file__).read_text()
        assert "os.environ" not in src and "typed_env" in src, mod.__name__
    assert mw.is_quiet_poll("/api/proactive/inbox", 200, 12) is True
