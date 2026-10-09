"""night_store - where the night mind keeps its threads, observations and run counts (alembic 0040).

Two backends behind one contract, exactly as ``exact_words`` does it: ``SqlBackend`` (the 0040 tables over ``db_pool``; portable SQL with ``?``
placeholders and ``ON CONFLICT`` so the test suite runs the very same statements on SQLite) and ``MemoryBackend`` (the benchmark lab and the unit
tests: a dict, no network, no Postgres). Rows are plain dicts whose keys are the column names in ``alembic/versions/0040_night_mind.py``.

Every call carries a ``user_id`` and no read crosses users. Nothing here decides anything: the rules (verification, policy, validity) live in
``night_mind``.
"""
from __future__ import annotations

import contextlib
import json
import re
from typing import Any, Iterable, Optional, Protocol

THREAD_COLS = ("id", "user_id", "title", "anchors", "topic", "status", "first_day", "last_day", "mentions_n", "weight_max", "last_feeling",
               "raise_policy", "leave_reason", "next_raise_after", "last_raised_at", "ignored_raises", "source_ref", "opened_run", "closed_run")
OBS_COLS = ("id", "user_id", "thread_id", "turn_id", "quote", "kind", "who", "feeling", "valence", "weight", "later", "day", "said_at", "valid_from",
            "valid_to", "state", "origin", "authority_class", "basis", "run_id")
RUN_COLS = ("id", "user_id", "night_date", "model_id", "prompt_sha", "schema_sha", "watermark_msg_id", "status", "error_class", "wall_s", "counts",
            "created_at")

THREAD_DEFAULTS: "dict[str, Any]" = {"title": "", "anchors": "", "topic": "", "status": "open", "first_day": "", "last_day": "", "mentions_n": 0,
                                     "weight_max": 1, "last_feeling": "none", "raise_policy": "wait", "leave_reason": "", "next_raise_after": "",
                                     "last_raised_at": "", "ignored_raises": 0, "source_ref": "", "opened_run": "", "closed_run": ""}
OBS_DEFAULTS: "dict[str, Any]" = {"thread_id": "", "turn_id": "", "kind": "other", "who": "", "feeling": "none", "valence": 0, "weight": 1, "later": "na",
                                  "day": "", "said_at": 0.0, "valid_from": 0.0, "valid_to": None, "state": "current", "origin": "stated",
                                  "authority_class": "user_stated_derived", "basis": "", "run_id": ""}
RUN_DEFAULTS: "dict[str, Any]" = {"night_date": "", "model_id": "", "prompt_sha": "", "schema_sha": "", "watermark_msg_id": "", "status": "ok",
                                  "error_class": "", "wall_s": 0.0, "counts": "{}", "created_at": 0.0}


def thread_row(**kw: Any) -> "dict[str, Any]":
    return {**THREAD_DEFAULTS, **{k: v for k, v in kw.items() if k in THREAD_COLS}}


def obs_row(**kw: Any) -> "dict[str, Any]":
    return {**OBS_DEFAULTS, **{k: v for k, v in kw.items() if k in OBS_COLS}}


def run_row(**kw: Any) -> "dict[str, Any]":
    row = {**RUN_DEFAULTS, **{k: v for k, v in kw.items() if k in RUN_COLS}}
    if not isinstance(row["counts"], str):
        row["counts"] = json.dumps(row["counts"], sort_keys=True, separators=(",", ":"))
    return row


class Backend(Protocol):
    async def threads(self, user_id: str) -> "list[dict]": ...

    async def put_thread(self, row: "dict") -> None: ...

    async def observations(self, user_id: str, *, thread_id: Optional[str] = None, states: Optional[Iterable[str]] = None) -> "list[dict]": ...

    async def put_observation(self, row: "dict") -> bool: ...

    async def put_run(self, row: "dict") -> None: ...

    async def runs(self, user_id: str, limit: int = 20) -> "list[dict]": ...

    # the whole night in ONE transaction: every observation, every thread row, then the run row - all or nothing
    async def commit_plan(self, observations: "list[dict]", threads: "list[dict]", run: "dict") -> None: ...

    async def erase_matching(self, user_id: str, pattern: "re.Pattern[str]") -> int: ...

    async def delete_user(self, user_id: str) -> int: ...


class MemoryBackend:
    """In-process store (tests, the benchmark lab). Same contract as the SQL one; the dicts are exposed so a test can look at exactly what is stored."""

    def __init__(self) -> None:
        self.thread_rows: "dict[str, dict]" = {}
        self.obs_rows: "dict[str, dict]" = {}
        self.run_rows: "dict[str, dict]" = {}

    async def threads(self, user_id):
        return sorted((dict(r) for r in self.thread_rows.values() if r["user_id"] == user_id), key=lambda r: (r["last_day"], r["id"]), reverse=True)

    async def put_thread(self, row):
        self.thread_rows[row["id"]] = dict(thread_row(**row))

    async def observations(self, user_id, *, thread_id=None, states=None):
        st = set(states) if states is not None else None
        out = [dict(r) for r in self.obs_rows.values()
               if r["user_id"] == user_id and (thread_id is None or r["thread_id"] == thread_id) and (st is None or r["state"] in st)]
        out.sort(key=lambda r: (r["said_at"], r["id"]))
        return out

    async def put_observation(self, row):
        row = obs_row(**row)
        for r in self.obs_rows.values():
            if r["user_id"] == row["user_id"] and r["turn_id"] == row["turn_id"] and r["quote"] == row["quote"] and r["id"] != row["id"]:
                return False                    # the same words from the same turn: one row (the unique index; ids are content-derived, so this is a backstop)
        new = row["id"] not in self.obs_rows
        self.obs_rows[row["id"]] = dict(row)
        return new

    async def put_run(self, row):
        self.run_rows[row["id"]] = dict(run_row(**row))

    async def commit_plan(self, observations, threads, run):
        obs_before, thr_before, run_before = dict(self.obs_rows), dict(self.thread_rows), dict(self.run_rows)
        try:
            for o in observations:
                await self.put_observation(o)
            for t in threads:
                await self.put_thread(t)
            await self.put_run(run)
        except BaseException:
            self.obs_rows, self.thread_rows, self.run_rows = obs_before, thr_before, run_before
            raise

    async def runs(self, user_id, limit=20):
        rows = [dict(r) for r in self.run_rows.values() if r["user_id"] == user_id]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[:limit]

    async def erase_matching(self, user_id, pattern):
        gone = [i for i, r in self.obs_rows.items() if r["user_id"] == user_id and pattern.search(r["quote"] or "")]
        for i in gone:
            del self.obs_rows[i]
        alive = {r["thread_id"] for r in self.obs_rows.values() if r["user_id"] == user_id}
        orphan = [i for i, t in self.thread_rows.items()
                  if t["user_id"] == user_id and (pattern.search(t["title"] or "") or (i not in alive and gone))]
        for i in orphan:
            del self.thread_rows[i]
        return len(gone) + len(orphan)

    async def delete_user(self, user_id):
        n = 0
        for store in (self.obs_rows, self.thread_rows, self.run_rows):
            for k in [k for k, r in store.items() if r["user_id"] == user_id]:
                del store[k]
                n += 1
        return n


def _upsert_sql(table: str, cols: "tuple[str, ...]") -> str:
    marks = ", ".join("?" for _ in cols)
    sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
    return f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks}) ON CONFLICT (id) DO UPDATE SET {sets}"


class SqlBackend:
    """The 0040 tables over ``db_pool``."""

    @staticmethod
    def _ctx():
        from db_pool import get_db_ctx  # type: ignore[import]
        return get_db_ctx()

    async def _rows(self, sql: str, params: tuple, cols: "tuple[str, ...]") -> "list[dict]":
        async with self._ctx() as db:
            cur = await db.execute(sql, params)
            return [dict(zip(cols, r)) for r in await cur.fetchall()]

    async def threads(self, user_id):
        return await self._rows(f"SELECT {', '.join(THREAD_COLS)} FROM night_threads WHERE user_id = ? ORDER BY last_day DESC, id", (user_id,), THREAD_COLS)

    async def put_thread(self, row):
        row = thread_row(**row)
        async with self._ctx() as db:
            await db.execute(_upsert_sql("night_threads", THREAD_COLS), tuple(row[c] for c in THREAD_COLS))
            await db.commit()

    async def observations(self, user_id, *, thread_id=None, states=None):
        sql, params = f"SELECT {', '.join(OBS_COLS)} FROM night_observations WHERE user_id = ?", [user_id]
        if thread_id is not None:
            sql += " AND thread_id = ?"
            params.append(thread_id)
        if states is not None:
            st = list(states)
            if not st:
                return []
            sql += f" AND state IN ({', '.join('?' for _ in st)})"
            params += st
        return await self._rows(sql + " ORDER BY said_at, id", tuple(params), OBS_COLS)

    async def put_observation(self, row):
        row = obs_row(**row)
        async with self._ctx() as db:
            cur = await db.execute(
                _upsert_sql("night_observations", OBS_COLS), tuple(row[c] for c in OBS_COLS))
            await db.commit()
            return int(getattr(cur, "rowcount", 1) or 0) > 0

    async def put_run(self, row):
        row = run_row(**row)
        async with self._ctx() as db:
            await db.execute(_upsert_sql("night_runs", RUN_COLS), tuple(row[c] for c in RUN_COLS))
            await db.commit()

    async def commit_plan(self, observations, threads, run):
        obs = [obs_row(**o) for o in observations]
        thr = [thread_row(**t) for t in threads]
        rn = run_row(**run)
        async with self._ctx() as db:
            tx = getattr(db, "transaction", None)
            async with (tx() if callable(tx) else contextlib.nullcontext()):
                for row in obs:
                    await db.execute(_upsert_sql("night_observations", OBS_COLS), tuple(row[c] for c in OBS_COLS))
                for row in thr:
                    await db.execute(_upsert_sql("night_threads", THREAD_COLS), tuple(row[c] for c in THREAD_COLS))
                await db.execute(_upsert_sql("night_runs", RUN_COLS), tuple(rn[c] for c in RUN_COLS))
            await db.commit()

    async def runs(self, user_id, limit=20):
        return await self._rows(f"SELECT {', '.join(RUN_COLS)} FROM night_runs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                                (user_id, int(limit)), RUN_COLS)

    async def erase_matching(self, user_id, pattern):
        obs = await self.observations(user_id)
        gone = [r["id"] for r in obs if pattern.search(r["quote"] or "")]
        n = 0
        async with self._ctx() as db:
            for i in gone:
                cur = await db.execute("DELETE FROM night_observations WHERE user_id = ? AND id = ?", (user_id, i))
                n += int(getattr(cur, "rowcount", 0) or 0)
            await db.commit()
        alive = {r["thread_id"] for r in obs if r["id"] not in set(gone)}
        orphan = [t["id"] for t in await self.threads(user_id) if pattern.search(t["title"] or "") or (t["id"] not in alive and gone)]
        async with self._ctx() as db:
            for i in orphan:
                cur = await db.execute("DELETE FROM night_threads WHERE user_id = ? AND id = ?", (user_id, i))
                n += int(getattr(cur, "rowcount", 0) or 0)
            await db.commit()
        return n

    async def delete_user(self, user_id):
        n = 0
        async with self._ctx() as db:
            for table in ("night_observations", "night_threads", "night_runs"):
                cur = await db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
                n += int(getattr(cur, "rowcount", 0) or 0)
            await db.commit()
        return n


_backend: Optional[Backend] = None


def get_backend() -> Backend:
    global _backend
    if _backend is None:
        _backend = SqlBackend()
    return _backend


def set_backend(backend: Optional[Backend]) -> None:
    """Swap the store (tests / lab); ``None`` restores the SQL default."""
    global _backend
    _backend = backend
