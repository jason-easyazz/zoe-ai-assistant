"""A member-scoped LAB household: the real routers and resolvers, as a specific member, on a scratch SQLite store.

Not a test module (no ``test_`` prefix). ``test_lab_household.py`` drives it; ``tools/qa_round`` runs QA rounds on it (``--lab``).

Why it exists: QA rounds against the live service use synthetic DEMO users, and a demo user is not a member, so every case that
needs a member scope (a repeating event, a timer or alarm tied to a member and a panel, a private-until-shared list, a reminder)
was BLOCKED round after round (QA round 10a: "a member-scoped lab fixture is needed"). Here the household is invented and the
store is a temp file, so those cases run in CI with nothing live touched.

The household (all invented; TEST-NET addresses):
  * ``ari`` and ``bo`` - two adults (members, role ``user``); ``cy`` - a child (a member, role ``user``, ``users.role='child'``);
  * ``guest`` - an anonymous caller on the LAN, NOT at the panel; ``panel`` - the registered wall panel (``lab-panel``) as nginx
    hands it to zoe-data (peer = our proxy, ``X-Real-IP`` = the panel's registered address). ``household`` = ``family-admin``.

REAL (runs unmodified):
  * the routers ``routers.calendar``, ``routers.lists``, ``routers.reminders``, ``routers.alarms``, ``routers.skybridge`` over real
    HTTP (``TestClient``, no lifespan), with the REAL ``auth.get_current_user``, the REAL ``panel_principal`` (registry SQL runs
    against the lab ``panels`` table), the REAL role matrix (``guest_policy``), and every service module they call
    (``calendar_service``, ``calendar_read`` repeat expansion and write guard, ``list_service``, ``alarms`` ring engine,
    ``skybridge_service`` classifier + resolvers + timer store, ``voice_announce``);
  * the SCHEMA: ``0001_initial_schema`` (its SQL with four mechanical Postgres->SQLite rewrites, below), then the real
    ``0052`` (calendar), ``0063`` + ``0070`` (alarms) and ``0025`` + ``0032`` (voice announcements) migrations.

FAKED (and exactly how):
  * PostgreSQL -> one SQLite file (``LabDb``), autocommit like ``AsyncpgCompat``. SQL rewrites at execute time: ``$n`` -> ``?``
    (re-ordered), `` FOR SHARE`` / `` FOR UPDATE`` dropped, ``ILIKE`` -> ``LIKE``, ``::text`` / ``::int`` casts dropped, ``NOW()``
    = a fixed SQL function. Schema rewrites: ``NOW()::TEXT`` -> ``CURRENT_TIMESTAMP``, ``SERIAL``/``BIGSERIAL`` -> integer keys,
    ``JSONB`` -> ``TEXT``. A statement SQLite rejects is RECORDED in ``LabDb.errors`` (and re-raised), never swallowed.
  * zoe-auth: ``auth._validate_with_auth_service`` maps the lab session ids to the members; no PIN, no passkey, no expiry.
  * push / WebSocket broadcasts: recorded in ``LabHousehold.pushed``, sent nowhere.
  * the brain: ``brain_dispatch.brain_streaming`` / ``brain_oneshot`` answer ``BRAIN_SENTINEL`` and record the call
    (``LabHousehold.brain_calls``), so "this turn reached the model" is a measured fact and no model loads. The lab's paths
    (routers + Skybridge) never need it; a non-empty ``brain_calls`` means a case fell through.
  * the voice turn: ``voice(member, text)`` does what ``routers/voice_tts._require_voice_auth`` does for a device-token turn
    (``bind_timer_panel``) and then calls ``skybridge_service.resolve_skybridge_request`` - STT, TTS, wake word and the panel
    daemon are NOT in the lab.
  * time: timers are rung by ``ring_timer`` (sets the expiry in the past, then runs the real ``_ring_when_done``); alarms by
    ``tick_alarms(at)`` (the real ``alarms.tick`` at an instant). Quiet hours are off.
NOT covered (no fake exists here): memory teach/recall/correct/forget (the palace + extraction need the brain), the chat route
and its fast tiers, phone push delivery, the panel UI. Those stay live-harness cases (``tools/qa_round`` ``--live``).
"""
from __future__ import annotations

import contextlib
import importlib.util
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

SVC = Path(__file__).resolve().parents[1]
MIGRATIONS = SVC / "alembic" / "versions"
BRAIN_SENTINEL = "<<LAB-BRAIN>>"

PANEL_ID, PANEL_IP, PHONE_IP, PROXY = "lab-panel", "192.0.2.60", "192.0.2.77", "127.0.0.1"
HOUSEHOLD = "family-admin"


@dataclass(frozen=True)
class Member:
    key: str
    user_id: str
    name: str
    role: str = "user"          # the zoe-auth role
    kind: str = "adult"         # adult | child (``users.role``)

    @property
    def session(self) -> str:
        return f"lab-sid-{self.key}"


MEMBERS = {
    "ari": Member("ari", "lab-ari", "Ari Lab"),
    "bo": Member("bo", "lab-bo", "Bo Lab"),
    "cy": Member("cy", "lab-cy", "Cy Lab", kind="child"),
}
CALLERS = (*MEMBERS, "guest", "panel")


# ── the store ────────────────────────────────────────────────────────────────────────────────────────────────────────────────

class LabRow:
    """An asyncpg ``Record``-shaped row: ``row["col"]``, ``row[0]``, ``row.get``, ``dict(row)``; iteration yields VALUES."""

    __slots__ = ("_k", "_v")

    def __init__(self, cursor, values):
        self._k = [d[0] for d in cursor.description]
        self._v = tuple(values)

    def __getitem__(self, key):
        return self._v[key] if isinstance(key, (int, slice)) else self._v[self._k.index(key)]

    def get(self, key, default=None):
        return self._v[self._k.index(key)] if key in self._k else default

    def keys(self):
        return list(self._k)

    def values(self):
        return list(self._v)

    def items(self):
        return list(zip(self._k, self._v))

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)

    def __contains__(self, key):
        return key in self._k

    def __repr__(self):
        return f"LabRow({dict(self.items())!r})"


class _Cursor:
    def __init__(self, cur: sqlite3.Cursor):
        self._rows = cur.fetchall() if cur.description else []
        self.rowcount = cur.rowcount if not cur.description else len(self._rows)
        self.lastrowid = cur.lastrowid

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)

    def __aiter__(self):
        self._it = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


class _Exec:
    """Awaitable AND ``async with`` (aiosqlite / ``AsyncpgCompat`` both allow either)."""

    def __init__(self, fn):
        self._fn = fn

    def __await__(self):
        async def _run():
            return self._fn()
        return _run().__await__()

    async def __aenter__(self):
        return self._fn()

    async def __aexit__(self, *_):
        return False


_PG_PARAM = re.compile(r"\$(\d+)")
_REWRITES = ((re.compile(r"\s+FOR\s+(SHARE|UPDATE)\b", re.I), ""), (re.compile(r"\bILIKE\b", re.I), "LIKE"),
             (re.compile(r"::(text|int|integer|bigint|float|numeric|date)\b", re.I), ""))


class LabDb:
    """The ``AsyncpgCompat`` surface the routers use (``execute`` with ``?`` or ``$n``, ``fetch``/``fetchrow``/``fetchval``,
    ``commit``, ``transaction``) over one synchronous SQLite connection (loop-agnostic, so TestClient's thread can share it)."""

    def __init__(self, path: Path):
        self.path = path
        self.con = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.con.row_factory = LabRow
        self.con.create_function("NOW", 0, lambda: "2026-11-14T00:00:00")
        self.errors: list[tuple[str, str]] = []

    @staticmethod
    def adapt(sql: str, params) -> tuple[str, tuple]:
        params = tuple(params or ())
        if _PG_PARAM.search(sql):
            order = [int(n) - 1 for n in _PG_PARAM.findall(sql)]
            sql, params = _PG_PARAM.sub("?", sql), tuple(params[i] for i in order)
        for pat, rep in _REWRITES:
            sql = pat.sub(rep, sql)
        return sql, params

    def _run(self, sql: str, params) -> _Cursor:
        q, p = self.adapt(sql, params)
        try:
            return _Cursor(self.con.execute(q, p))
        except sqlite3.Error as exc:
            self.errors.append((type(exc).__name__ + ": " + str(exc), " ".join(q.split())[:160]))
            raise

    def execute(self, sql: str, *params) -> _Exec:
        if len(params) == 1 and isinstance(params[0], (list, tuple)):
            params = params[0]
        return _Exec(lambda: self._run(sql, params))

    async def execute_fetchall(self, sql: str, params=()):
        return await (await self.execute(sql, params)).fetchall()

    async def fetch(self, sql: str, *args):
        return await self._run(sql, args).fetchall()

    async def fetchrow(self, sql: str, *args):
        return await self._run(sql, args).fetchone()

    async def fetchval(self, sql: str, *args):
        row = await self._run(sql, args).fetchone()
        return row[0] if row else None

    async def commit(self):
        return None

    async def rollback(self):
        return None

    async def close(self):
        return None

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    # synchronous reads for assertions
    def rows(self, sql: str, params=()) -> list[dict]:
        return [dict(r.items()) for r in self.con.execute(*self.adapt(sql, params)).fetchall()]


def _migration(name: str):
    spec = importlib.util.spec_from_file_location("lab_mig_" + name[:4], MIGRATIONS / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pg_sql_to_sqlite(sql: str) -> str:
    sql = sql.replace("NOW()::TEXT", "CURRENT_TIMESTAMP").replace("JSONB", "TEXT")
    sql = re.sub(r"\bBIGSERIAL PRIMARY KEY\b|\bSERIAL PRIMARY KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", sql)
    return re.sub(r"\bADD COLUMN IF NOT EXISTS\b", "ADD COLUMN", sql)


def _raw_upgrade_sql(name: str) -> list[str]:
    """The ``op.execute`` strings of a raw-SQL migration's ``upgrade()``, rewritten for SQLite."""
    import ast

    tree = ast.parse((MIGRATIONS / name).read_text())
    upgrade = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade")
    return [_pg_sql_to_sqlite(node.args[0].value) for node in ast.walk(upgrade)
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "execute"
            and node.args and isinstance(node.args[0], ast.Constant)]


def build_schema(path: Path) -> None:
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    con = sqlite3.connect(str(path))
    for name in ("0001_initial_schema.py", "0025_voice_announcements.py", "0032_voice_announcement_played.py"):
        for stmt in _raw_upgrade_sql(name):
            con.executescript(stmt)
    con.commit()
    con.close()
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        for name in ("0052_calendar_data_model.py", "0063_alarms.py", "0070_alarm_overrides.py"):
            _migration(name).upgrade()
    engine.dispose()


# ── the household ────────────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class LabHousehold:
    db: LabDb
    pushed: list = field(default_factory=list)
    brain_calls: list = field(default_factory=list)
    _app: Any = None

    def member(self, who: str) -> Member:
        return MEMBERS[who]

    def user_id(self, who: str) -> str:
        return MEMBERS[who].user_id if who in MEMBERS else ("guest" if who in ("guest", "panel") else who)

    @property
    def app(self):
        if self._app is None:
            from fastapi import FastAPI

            from database import get_db
            from routers import alarms as alarms_router, calendar, lists, reminders, skybridge

            app = FastAPI()
            for mod in (calendar, lists, reminders, alarms_router, skybridge):
                app.include_router(mod.router)
            app.dependency_overrides[get_db] = lambda: self.db
            self._app = app
        return self._app

    def http(self, who: str, method: str, path: str, body: Optional[dict] = None, **params):
        """One real HTTP request as ``who`` (``ari``/``bo``/``cy``/``guest``/``panel``)."""
        from fastapi.testclient import TestClient

        if who in MEMBERS:
            peer, headers = PHONE_IP, {"X-Session-ID": MEMBERS[who].session}
        elif who == "panel":
            peer, headers = PROXY, {"X-Real-IP": PANEL_IP}
        elif who == "guest":
            peer, headers = PHONE_IP, {}
        else:
            raise ValueError(f"unknown caller {who!r}; one of {CALLERS}")
        client = TestClient(self.app, client=(peer, 50000))
        return client.request(method, path, json=body, headers=headers, params=params or None)

    def say(self, who: str, text: str, context: Optional[dict] = None) -> dict:
        """A typed Skybridge command (the card / panel text path: ``POST /api/skybridge/resolve``)."""
        r = self.http(who, "POST", "/api/skybridge/resolve", {"message": text, "context": context})
        assert r.status_code == 200, r.text
        return r.json()

    async def voice(self, who: str, text: str, context: Optional[dict] = None, *, at_panel: bool = True) -> dict:
        """A spoken turn as the panel daemon delivers it after STT: device-token panel bound, then the Skybridge resolver."""
        import skybridge_service as sky

        sky.bind_timer_panel(PANEL_ID if at_panel else "")
        try:
            return await sky.resolve_skybridge_request(text, self.user_id(who), context=context, db=self.db)
        finally:
            sky.bind_timer_panel("")

    # time ------------------------------------------------------------------------------------------------------------------
    async def ring_timer(self, timer_id: str) -> None:
        import time

        import skybridge_service as sky

        hit = sky._timers.find_by_id(timer_id)
        assert hit is not None, f"no running timer {timer_id}"
        hit[1]["expires_at"] = time.time() - 1
        await sky._ring_when_done(sky._timers, timer_id)

    async def tick_alarms(self, at: float):
        import alarms

        return await alarms.tick(self.db, at)

    def announcements(self) -> list[dict]:
        return self.db.rows("SELECT user_id, panel_id, message, trigger_type, expired FROM voice_announcements ORDER BY created_at")


def _seed(db: LabDb) -> None:
    db.con.execute("INSERT INTO users (id, name, role) VALUES (?, 'Household', 'admin')", (HOUSEHOLD,))
    for m in MEMBERS.values():
        db.con.execute("INSERT INTO users (id, name, role) VALUES (?, ?, ?)", (m.user_id, m.name, "child" if m.kind == "child" else "member"))
    db.con.execute("INSERT INTO panels (panel_id, name, ip_address, is_active, allow_guest) VALUES (?, 'Lab hall panel', ?, 1, 1)",
                   (PANEL_ID, PANEL_IP))


def install(monkeypatch, tmp_path: Path) -> LabHousehold:
    """Build the household on ``tmp_path`` and pin every seam listed in the module docstring (all undone by ``monkeypatch``)."""
    import auth
    import brain_dispatch
    import db_compat
    import db_pool
    import panel_principal
    import push
    import skybridge_service as sky

    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "lab-household.db"
    build_schema(path)
    db = LabDb(path)
    _seed(db)
    hh = LabHousehold(db=db)

    for var in ("ZOE_HA_CONTROL_SCOPE", "ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE", "ZOE_TRUSTED_PROXY_NETS", "ZOE_PANEL_KIOSK_CREDENTIAL",
                "ZOE_DEVICE_TOKEN_BINDS_MEMBER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ZOE_ALARMS", "on")
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.setenv("ZOE_TIMER_STORE", str(tmp_path / "timers.json"))

    monkeypatch.setattr(auth, "_UNAUTH_ROLE", "guest")
    auth._session_cache.clear()
    sessions = {m.session: {"user_id": m.user_id, "role": m.role, "username": m.key, "permissions": []} for m in MEMBERS.values()}

    async def _validate(session_id):
        return dict(sessions[session_id]) if session_id in sessions else None

    monkeypatch.setattr(auth, "_validate_with_auth_service", _validate)

    @contextlib.asynccontextmanager
    async def _ctx():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", _ctx)
    monkeypatch.setattr(db_pool, "get_db_ctx", _ctx)

    async def _with_registry(_db, check, value):
        return await check(db, value)

    monkeypatch.setattr(panel_principal, "_with_registry", _with_registry)

    async def _broadcast(channel, kind, data=None, user_id=None, **_kw):
        hh.pushed.append((channel, kind, user_id))
        return 1

    monkeypatch.setattr(push.broadcaster, "broadcast", _broadcast)

    async def _oneshot(message, session_id, user_id="", **_kw):
        hh.brain_calls.append((user_id, message))
        return BRAIN_SENTINEL

    async def _streaming(message, session_id, user_id="", **_kw):
        hh.brain_calls.append((user_id, message))
        yield BRAIN_SENTINEL

    monkeypatch.setattr(brain_dispatch, "brain_oneshot", _oneshot)
    monkeypatch.setattr(brain_dispatch, "brain_streaming", _streaming)

    monkeypatch.setattr(sky._timers, "_by_owner", {})
    monkeypatch.setattr(sky._timers, "_loaded", True)
    monkeypatch.setattr(sky._timers, "_last_write", None)
    monkeypatch.setattr(sky._timers, "_rings", {})
    monkeypatch.setattr(sky, "_timer_quiet_now", lambda: False)
    return hh


def teardown(hh: LabHousehold) -> None:
    import auth
    import skybridge_service as sky

    for task in list(sky._timers._rings.values()):
        task.cancel()
    sky._timers.wait_saved()
    auth._session_cache.clear()
    hh.db.con.close()
