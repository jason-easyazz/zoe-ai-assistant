"""The people graph under test (capability ``edges``): Zoe's own ``person_extractor._write_relationship`` over an in-memory SQLite.

The graph (``people`` + ``person_relationships``, Postgres in production) is NOT a memory-engine feature: under the adoption design
(docs/research/memory-system-decision-2026-10-05.md) it stays Zoe's whatever stores the facts. So every arm that has the Zoe layer exercises the SAME
machinery - Z0 (``arms.z0``) and the Hindsight arms (``arms.hindsight``'s ZoeLayer) both delegate here, and the A8 cells measure the authority wall on
edges (``_edge_may_change``: an inferred relationship may not close a user-stated edge; the refusal is a held-back ``disputed`` candidate that
points at the edge), temporal edges (a changed relationship closes the old edge, history kept) and the writer stamp (authority / origin).

The real writer runs unmodified over the 0007 / 0015 (temporal edges) / 0037 (authority stamp) table shapes. Two things reach outside it and are
scripted by the CALLER: the held-back candidate goes through ``memory_service.get_memory_service().record_candidate`` (Z0: the lab service;
a Hindsight arm: its side table, via ``CandidateSink``), and the person-offer lookup (``pending_suggestions``) is stubbed to zero.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import os
import sys
import types
from typing import Any, Callable, Iterator

#: the flags ``services/zoe-data/.env`` turns ON that the code leaves OFF (a bench of the defaults would measure a system that is not deployed):
#: the implicit supersede (nightly conflict pass, ``invalid_at``) and temporal edges
LIVE_FLAGS = {"ZOE_MEMORY_IMPLICIT_SUPERSEDE": "1", "ZOE_TEMPORAL_RELATIONSHIPS_ENABLED": "1"}

DDL = (
    "CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT, "
    "circle TEXT, context TEXT, notes TEXT, visibility TEXT, deleted INTEGER NOT NULL DEFAULT 0, "
    "is_partial INTEGER NOT NULL DEFAULT 0, last_contacted_at TEXT)",
    "CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, person_a_id TEXT NOT NULL, "
    "person_b_id TEXT NOT NULL, rel_type TEXT NOT NULL, rel_a_to_b TEXT NOT NULL, rel_b_to_a TEXT NOT NULL, "
    "rel_group TEXT NOT NULL, notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    # migrations 0015 (temporal edges) and 0037 (the writer's authority / origin stamp)
    "ALTER TABLE person_relationships ADD COLUMN valid_from TEXT",
    "ALTER TABLE person_relationships ADD COLUMN valid_to TEXT",
    "ALTER TABLE person_relationships ADD COLUMN superseded_by TEXT",
    "CREATE UNIQUE INDEX person_relationships_pair_active ON person_relationships(user_id, person_a_id, "
    "person_b_id) WHERE valid_to IS NULL",
    "ALTER TABLE person_relationships ADD COLUMN authority TEXT",
    "ALTER TABLE person_relationships ADD COLUMN origin TEXT",
)


def _digest(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:10]


@contextlib.contextmanager
def live_context() -> Iterator[None]:
    """The live flags on, and ``pending_suggestions`` stubbed (the person-offer lookup needs Postgres), for ONE operation; restored exactly."""
    stub = types.ModuleType("pending_suggestions")

    async def no_offers(_uid: str, _name: str) -> int:
        return 0
    stub.resolve_person_offers_by_name = no_offers
    had = sys.modules.get("pending_suggestions")
    sys.modules["pending_suggestions"] = stub
    saved = {k: os.environ.get(k) for k in LIVE_FLAGS}
    os.environ.update(LIVE_FLAGS)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if had is None:
            sys.modules.pop("pending_suggestions", None)
        else:
            sys.modules["pending_suggestions"] = had


class CandidateSink:
    """Stands in for ``MemoryService`` where the authority wall holds a relationship back: ``record_candidate`` hands the candidate to ``on_candidate``
    (the Hindsight arm's side table). Same call shape as ``MemoryService.record_candidate`` (``person_extractor._edge_may_change``)."""

    def __init__(self, on_candidate: "Callable[..., Any]"):
        self._on = on_candidate

    async def record_candidate(self, text: str, *, user_id: str, writer: str, contradicts: str = "", kind: str = "other", **kw: Any) -> str:
        return self._on(text, user_id=user_id, writer=writer, contradicts=contradicts, kind=kind, **kw)


@contextlib.contextmanager
def candidate_context(sink: CandidateSink) -> Iterator[None]:
    """``memory_service.get_memory_service`` returns ``sink`` for ONE operation (``_edge_may_change`` imports it at call time)."""
    ms = importlib.import_module("memory_service")
    real = ms.get_memory_service
    ms.get_memory_service = lambda: sink
    try:
        yield
    finally:
        ms.get_memory_service = real


class PeopleGraph:
    """One in-memory SQLite people graph. ``run`` executes a coroutine on the OWNER's event loop (the connection lives on it)."""

    def __init__(self, run: "Callable[[Any], Any]"):
        self._run = run
        self._db: Any = None

    async def _open(self) -> Any:
        import aiosqlite
        db = await aiosqlite.connect(":memory:")
        db.row_factory = aiosqlite.Row
        for ddl in DDL:
            await db.execute(ddl)
        await db.commit()
        return db

    async def _write(self, user: str, a: str, b: str, rel: str, group: str, authority: str, origin: str) -> None:
        pe = importlib.import_module("person_extractor")
        if self._db is None:
            self._db = await self._open()
        db = self._db
        for name in (a, b):  # real, non-partial people (a partial stub is never resolved: it would fork the pair)
            async with db.execute("SELECT 1 FROM people WHERE user_id=? AND name=?", (user, name)) as cur:
                if await cur.fetchone() is None:
                    await db.execute("INSERT INTO people (id, user_id, name, deleted, is_partial, visibility) "
                                     "VALUES (?,?,?,0,0,'personal')", (f"p-{_digest(user, name)}", user, name))
        await db.commit()
        await pe._write_relationship(user, a, b, rel, group, db, authority=authority, origin=origin)

    def write(self, user: str, a: str, b: str, rel: str, group: str, authority: str, origin: str) -> None:
        """The REAL ``person_extractor._write_relationship`` (temporal edges, the authority wall, the held-back candidate)."""
        self._run(self._write(user, a, b, rel, group, authority, origin))

    def edges(self, user: str) -> "list[dict[str, Any]]":
        """Every edge, open or closed, as names (never ids): ``a, b, rel_type, current, authority, origin``."""
        async def read() -> "list[dict[str, Any]]":
            if self._db is None:
                return []
            sql = ("SELECT pa.name AS a, pb.name AS b, r.rel_type, r.valid_to, r.authority, r.origin "
                   "FROM person_relationships r JOIN people pa ON pa.id = r.person_a_id "
                   "JOIN people pb ON pb.id = r.person_b_id WHERE r.user_id=? ORDER BY r.created_at, r.id")
            async with self._db.execute(sql, (user,)) as cur:
                return [{"a": r["a"], "b": r["b"], "rel_type": r["rel_type"], "current": r["valid_to"] is None,
                         "authority": r["authority"] or "", "origin": r["origin"] or ""} for r in await cur.fetchall()]
        return self._run(read())

    def close(self) -> None:
        """Drop the database (idempotent). An in-memory SQLite: nothing to lose."""
        if self._db is not None:
            try:
                self._run(self._db.close())
            except Exception:  # noqa: BLE001
                pass
        self._db = None
