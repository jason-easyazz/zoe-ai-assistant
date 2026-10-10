"""promise_cells - bar cells S32 and S33 (Zoe keeps her own record): the persisted "what did that answer stand on" ledger and the
commitment tracker. IN-PROCESS cells: they drive the REAL modules (``memory_provenance``, ``reply_ledger``, ``provenance_answers``,
``commitments``, ``proactive.pull``) against a throwaway SQLite database built from the real migrations, with the memory store and the
clock stubbed - no live brain, no live database, no service restart, nothing outside this process. (A live leg needs the code
deployed and a restart; it is queued behind the deploy - see docs/knowledge/samantha-bar.md.)

  S32 "why did you say that" after a zoe-data restart   The in-process ledger is CLEARED (``memory_provenance.reset()`` is what a restart
                                                        does to it); the persisted ledger still answers with the owner's own words, for
                                                        THAT conversation only - another session is told "I don't have a record".
  S33 a timed promise is kept or owned up to            Zoe's reply promises "I'll remind you at 5 about the dentist": a reminder that
                                                        exists means kept; a miss inside the grace is fulfilled through the reminder path;
                                                        a stale miss is owned up ONCE in the pull queue; the user's own promise-shaped
                                                        words record nothing.

Every cell carries its CONTROL (``run_controls``): the same flow with the feature switched off must score FAIL (``ZOE_PROVENANCE_PERSIST=0``,
``ZOE_COMMITMENTS=off``), a reply that is the canned "no record" must FAIL S32, and the right replies must PASS. The bar refuses to report a
cell whose control does not go the right way - a scorer that cannot go red measures nothing.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import os
import re
import sqlite3
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

_SERVICE = Path(__file__).resolve().parents[2] / "services" / "zoe-data"
if str(_SERVICE) not in sys.path:
    sys.path.insert(0, str(_SERVICE))

CELL_IDS = ("S32", "S33")
USER = "demo_bar_cafe0032"
SAY_SISTER = "My sister Marisol is flying in from Lisbon on Thursday and I need to pick her up."
ROW_SISTER = "User's sister Marisol is flying in from Lisbon on Thursday"
ASK_SISTER = "When is my sister arriving?"
REPLY_SISTER = "Your sister Marisol is arriving on Thursday from Lisbon."
ASK_WHY = "Why did you say that?"
NO_RECORD_MARK = "don't have a record"
PROMISE_REPLY = "Sure, I'll remind you at 5 about the dentist."
NOW = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)
DUE = datetime(2026, 10, 10, 17, 0, tzinfo=timezone.utc)
_ENV_KEYS = ("ZOE_PROVENANCE_PERSIST", "ZOE_MEMORY_PROVENANCE_ANSWERS", "ZOE_COMMITMENTS", "ZOE_TIMEZONE", "ZOE_PROACTIVE_SELECTOR",
             "ZOE_PULL_NOT_PUSH", "ZOE_RESTRAINT", "ZOE_DELIVERY_LEDGER", "ZOE_PROACTIVE_LEDGER", "ZOE_EXPERT_ENABLED")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower())).strip()


# ── scorers (pure) ─────────────────────────────────────────────────────────────────────────────────────

def score_s32(restart_same_session: str, restart_other_session: str) -> tuple:
    """Leg 1: after the in-process ledger is cleared, "why did you say that?" in the SAME conversation quotes the owner's own words
    (verbatim) and is not the canned "no record". Leg 2: the same question in ANOTHER conversation (a voice session asking about a
    chat reply) gets the canned "no record" - never the other conversation's sources."""
    one, two = normalize(restart_same_session), normalize(restart_other_session)
    ev = {"method": "deterministic",
          "quotes_own_words": normalize(SAY_SISTER).rstrip(" .") in one,
          "not_no_record": NO_RECORD_MARK not in one and bool(one),
          "other_session_told_no_record": NO_RECORD_MARK in two,
          "other_session_leaks": any(w in two for w in ("marisol", "lisbon"))}
    why = []
    if not ev["not_no_record"]:
        why.append("after the restart the answer was 'I don't have a record' - the persisted ledger was not used")
    elif not ev["quotes_own_words"]:
        why.append("the explanation does not quote the owner's own words verbatim")
    if ev["other_session_leaks"] or not ev["other_session_told_no_record"]:
        why.append("another conversation was shown (or not refused) the first conversation's sources")
    return ("FAIL", {**ev, "why": "; ".join(why)}) if why else ("PASS", ev)


def score_s33(ev: dict) -> tuple:
    """``ev`` from ``_flow_s33``. PASS needs: the kept promise read as kept; the in-grace miss fulfilled through the reminder path (a
    reminder row exists); the stale miss owned up exactly once (the pull says it, the second pull does not); the user's promise-shaped
    words recorded nothing."""
    owned_text = normalize(ev.get("pull_first", ""))
    checks = {"recorded": ev.get("rows", 0) >= 3,
              "kept": ev.get("kept") == "kept",
              "fulfilled_late": ev.get("fulfilled") == "fulfilled" and ev.get("reminders_made", 0) == 1,
              "owned_up": ev.get("owned") in ("owned", "surfaced") and "i said i'd remind you about the bins and didn't" in owned_text,
              "owned_once": "nothing new" in normalize(ev.get("pull_second", "")),
              "user_words_recorded_nothing": ev.get("user_rows", 1) == 0}
    out = {"method": "deterministic", **checks, "statuses": ev.get("statuses")}
    bad = [k for k, v in checks.items() if not v]
    return ("FAIL", {**out, "why": "not met: " + ", ".join(bad)}) if bad else ("PASS", out)


SCORERS = {"S32": lambda ev: score_s32(*ev), "S33": score_s33}


# ── the in-process rig ─────────────────────────────────────────────────────────────────────────────────

class _Sqlite:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql: str, params=()):
        from db_pool import _Cursor, _ExecResult

        async def _run():
            await asyncio.sleep(0)
            cur = self.conn.execute(sql, tuple(params))
            rows = cur.fetchall()
            self.conn.commit()
            return _Cursor(rows, rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self) -> None:
        self.conn.commit()


def _migrate(path: str, *names: str) -> None:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = sa.create_engine(f"sqlite:///{path}")
    for fname in names:
        spec = importlib.util.spec_from_file_location("barmig_" + fname[:4], _SERVICE / "alembic" / "versions" / fname)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()


class _Store:
    """The memory store, stubbed: one approved row owned by USER whose ``source_excerpt`` is the owner's own sentence."""

    async def get(self, row_id: str):
        if row_id != "row-sister":
            return None
        meta = {"user_id": USER, "status": "approved", "source": "chat_regex", "source_excerpt": SAY_SISTER,
                "captured_at": (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        return types.SimpleNamespace(id=row_id, text=ROW_SISTER, metadata=meta)


@contextlib.contextmanager
def rig(env: dict):
    """A throwaway database (the real migrations), ``db_compat`` pointed at it, the environment set, everything restored after."""
    import db_compat
    import commitments
    import memory_provenance as mp
    import proactive.triggers.reminder_scan as reminder_scan
    import proactive.triggers.reminders as reminder_scheduler
    import reply_ledger
    import user_erase_gate
    import zoneinfo

    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    saved_tz = reminder_scan._ZOE_TZ       # read from the environment when the module is first imported: pin it to the rig's clock
    saved_db = db_compat.get_compat_db
    saved_scheduler = reminder_scheduler.schedule_reminder     # the real one binds ``_get_compat_db`` at import: stub the whole boundary
    with tempfile.TemporaryDirectory(prefix="promise_cells_") as tmp:
        path = str(Path(tmp) / "bar.db")
        _migrate(path, "0033_proactive_candidates.py", "0036_proactive_deliveries.py", "0041_proactive_ledger_lines.py",
                 "0044_reply_sources_commitments.py")
        db = _Sqlite(path)
        db.conn.execute(
            "CREATE TABLE reminders (id TEXT PRIMARY KEY, user_id TEXT, title TEXT, description TEXT, reminder_type TEXT, category TEXT, "
            "priority TEXT, due_date TEXT, due_time TEXT, recurring_pattern TEXT, is_active INTEGER DEFAULT 1, acknowledged INTEGER DEFAULT 0, "
            "snoozed_until TEXT, visibility TEXT, deleted INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT)")
        db.conn.execute(      # the table reminder_service's creation notification writes to (NOW() is registered below)
            "CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT, type TEXT, title TEXT, message TEXT, data TEXT, "
            "delivered INTEGER DEFAULT 0, created_at TEXT)")
        db.conn.create_function("NOW", 0, lambda: NOW.strftime("%Y-%m-%d %H:%M:%S+00"))
        db.conn.commit()

        @contextlib.asynccontextmanager
        async def fake_db():
            yield db

        async def fake_schedule(user_id, message, send_at, item_id=""):
            """The reminder scheduler's boundary, stubbed: the real one binds ``get_compat_db`` when its module is imported and would
            write outside this rig (or keep this rig's database after it exits)."""
            db.scheduled.append((user_id, message, item_id))
            return "stub"
        db.scheduled = []

        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        os.environ.update({"ZOE_TIMEZONE": "UTC", "ZOE_PROACTIVE_SELECTOR": "1", "ZOE_RESTRAINT": "off", "ZOE_EXPERT_ENABLED": "0", **env})
        db_compat.get_compat_db = fake_db
        reminder_scheduler.schedule_reminder = fake_schedule
        reminder_scan._ZOE_TZ = zoneinfo.ZoneInfo("UTC")
        mp.reset()
        user_erase_gate.reset()
        saved_globals = {(m, n): getattr(m, n) for m, n in ((reply_ledger, "_pending"), (reply_ledger, "_last_purge"),
                                                            (commitments, "_pending"), (commitments, "_last_purge"))}
        reply_ledger._pending, reply_ledger._last_purge = set(), 0.0
        commitments._pending, commitments._last_purge = set(), 0.0
        try:
            yield db
        finally:
            db_compat.get_compat_db = saved_db
            reminder_scheduler.schedule_reminder = saved_scheduler
            reminder_scan._ZOE_TZ = saved_tz
            for (mod, name), value in saved_globals.items():
                setattr(mod, name, value)
            user_erase_gate.reset()
            mp.reset()
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


async def _flow_s32() -> tuple:
    import memory_provenance as mp
    import provenance_answers as pa
    import reply_ledger

    store = _Store()
    mp.note_user_turn(USER, ASK_SISTER, "chat-1")
    token = mp.begin_turn(USER)
    mp.note_served(USER, [("row-sister", ROW_SISTER)])
    mp.commit_brain_reply(USER, REPLY_SISTER, ASK_SISTER, "chat-1", token=token, tools=("recall_memory",))
    await reply_ledger.flush()
    mp.reset()                                                      # the restart: every in-process ledger is gone
    mp.note_user_turn(USER, ASK_WHY, "chat-1")
    same = await pa.explain(USER, svc=store, session_id="chat-1")
    mp.reset()
    mp.note_user_turn(USER, ASK_WHY, "voice-9")
    other = await pa.explain(USER, svc=store, channel="voice", session_id="voice-9")
    return same, other


async def _flow_s33() -> dict:
    import commitments as cm
    import memory_provenance as mp
    from proactive import pull

    import db_compat

    async def q(sql, params=()):
        async with db_compat.get_compat_db() as db:
            cur = await db.execute(sql, params)
            return list(await cur.fetchall())

    async def say(reply, *, message, session, reply_id, at=NOW):
        mp.note_user_turn(USER, message, session)
        mp.claim_turn(USER, message)
        cm.schedule_record(USER, session, reply_id, reply, message, (), now=at)
        await cm.flush()

    # three of Zoe's own promises; the third is never made good; a fourth turn is the USER's promise-shaped words with a promise-free reply
    await say(PROMISE_REPLY, message="remind me about the dentist later", session="s-kept", reply_id="r-kept")
    await say("I'll remind you at 7pm about the school pickup.", message="can you remind me about pickup", session="s-grace", reply_id="r-grace")
    await say("I'll remind you at 9pm about the bins.", message="and the bins tonight", session="s-stale", reply_id="r-stale")
    before_user = len(await q("SELECT id FROM commitments"))
    await say("Okay, noted.", message="I'll remind you at 5 about the dentist", session="s-user", reply_id="r-user")
    user_rows = len(await q("SELECT id FROM commitments")) - before_user
    async with db_compat.get_compat_db() as db:                     # the tool "worked" for the first promise only
        await db.execute("INSERT INTO reminders (id, user_id, title, due_date, due_time, is_active, acknowledged, deleted, created_at) "
                         "VALUES ('rem-1', ?, 'Dentist', '2026-10-10', '17:00', 1, 0, 0, ?)", (USER, NOW.strftime("%Y-%m-%d %H:%M:%S+00")))
        await db.commit()
    await cm.sweep(now=DUE + timedelta(minutes=2))                  # the dentist reminder exists -> kept
    await cm.sweep(now=datetime(2026, 10, 10, 19, 3, tzinfo=timezone.utc))      # pickup: 3 minutes late, with a subject -> made now
    await cm.sweep(now=datetime(2026, 10, 10, 23, 30, tzinfo=timezone.utc))     # bins: 2.5 h late -> owned up in the pull
    later = datetime(2026, 10, 10, 23, 31, tzinfo=timezone.utc)
    first = await pull.pull(USER, "s-pull", channel="chat", now=later)
    second = await pull.pull(USER, "s-pull", channel="chat", now=later + timedelta(minutes=1))
    await cm.sweep(now=later + timedelta(minutes=10))
    statuses = {r["reply_id"]: r["status"] for r in await q("SELECT reply_id, status FROM commitments")}
    return {"rows": len(statuses), "statuses": statuses, "kept": statuses.get("r-kept"), "fulfilled": statuses.get("r-grace"),
            "owned": statuses.get("r-stale"), "reminders_made": len(await q("SELECT id FROM reminders WHERE id <> 'rem-1'")),
            "pull_first": first.reply if first else "", "pull_second": second.reply if second else "", "user_rows": user_rows}


def run_inprocess(env_extra: dict | None = None) -> dict:
    """Both cells, in this process: ``{"S32": (verdict, evidence), "S33": (verdict, evidence)}``. ``env_extra`` flips a flag (the controls)."""
    out = {}
    with rig({"ZOE_COMMITMENTS": "enforce", **(env_extra or {})}):
        out["S32"] = SCORERS["S32"](asyncio.run(_flow_s32()))
    with rig({"ZOE_COMMITMENTS": "enforce", **(env_extra or {})}):
        out["S33"] = SCORERS["S33"](asyncio.run(_flow_s33()))
    return out


#: (cell, label, env flip, expected verdict) - the same flow with the feature switched off must go red
CONTROLS = (
    ("S32", "persisted ledger switched off (ZOE_PROVENANCE_PERSIST=0)", {"ZOE_PROVENANCE_PERSIST": "0"}, "FAIL"),
    ("S33", "commitment tracker switched off (ZOE_COMMITMENTS=off)", {"ZOE_COMMITMENTS": "off"}, "FAIL"),
    ("S33", "tracker in shadow (records and logs, never acts)", {"ZOE_COMMITMENTS": "shadow"}, "FAIL"),
)
#: replies the S32 scorer must refuse / accept without any flow
S32_REPLY_CONTROLS = (
    ("the canned 'no record' answer", ("I don't have a record of what that answer came from.", "I don't have a record of what that answer came from."), "FAIL"),
    ("a paraphrase instead of the owner's words", ("I said that because you mentioned a sister arriving.", "I don't have a record of what that answer came from."), "FAIL"),
    ("the other conversation was shown the sources",
     ("I said that because earlier today you told me, \"%s\"." % SAY_SISTER.rstrip("."), "I said that because you told me, \"%s\"." % SAY_SISTER), "FAIL"),
    ("the right pair", ("I said that because earlier today you told me, \"%s\". If that's wrong, tell me the right answer, or say forget it."
                        % SAY_SISTER.rstrip("."), "I don't have a record of what that answer came from."), "PASS"),
)


def run_controls() -> list:
    """Problems with the instrument ([] = the scorers can go red and green)."""
    problems = []
    for label, pair, expect in S32_REPLY_CONTROLS:
        got = score_s32(*pair)[0]
        if got != expect:
            problems.append(f"S32 control '{label}': expected {expect}, scorer said {got}")
    try:
        for cell, label, env, expect in CONTROLS:
            got = run_inprocess(env)[cell][0]
            if got != expect:
                problems.append(f"{cell} control '{label}': expected {expect}, scorer said {got}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"controls could not run: {type(exc).__name__}: {exc}")
    return problems


def run(live: Any, user: str, want: Callable[[str], bool], samples: int, log: Callable[[str], None]) -> list:
    """The bar's cell hook: ``[(id, verdict, evidence)]``. In-process - ``live`` and ``user`` are not used (the cells never touch the live
    service, the live database or the brain; a live leg is queued behind the deploy)."""
    ids = [c for c in CELL_IDS if want(c)]
    problems = run_controls()
    if problems:
        return [(c, "ERROR", {"why": "instrument controls failed: " + "; ".join(problems)}) for c in ids]
    res = run_inprocess()
    out = []
    for cid in ids:
        verdict, ev = res[cid]
        log(f"  {cid}: {verdict} (in-process)")
        out.append((cid, verdict, {**ev, "mode": "in-process", "controls": "ok"}))
    return out
