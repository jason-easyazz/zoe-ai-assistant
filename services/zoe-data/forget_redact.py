"""Forgetting reaches the box: redact forgotten text from the verbatim transcript, the in-process turn marks, and every plaintext copy.

"Forgotten means forever" (owner, 2026-10-06). The palace rows, the exact-words index and the night-mind rows were already erased by
``memory_forget_entity``; the ledger (``memory_forgotten``) stopped the name coming back through a write. What was left is the TEXT:
the verbatim ``chat_messages`` rows (the digest's source), the plaintext JSON exports of the palace, and the brain's durable
conversation store. This module redacts it - a span is replaced by a fixed marker, never the whole message, never a delete:

  * ``on_forget``         at forget time, with the name in hand: ``chat_messages`` rows of the owner (user AND assistant turns, the
                          session titles) get the name's span replaced by ``[forgotten]`` IN PLACE (id, timestamps, role kept);
                          the per-turn marks other modules hold in process are dropped. One log line, no text:
                          ``FORGET_REDACT user= rows= spans=``.
  * ``LedgerRedactor``    afterwards and for old copies, with NO name: it asks the ledger's hashes which spans of a text are
                          forgotten (exact words and near spellings), so an export, a backup or the Flue store can be redacted
                          by anything that can load the ledger.
  * ``redact_export``     the two plaintext palace export shapes (``export_memory_store`` and ``compact_drawers_index``), record
                          counts and ids unchanged; ``verify_export`` is what the backup-verify step checks.

Everything is best effort at forget time (a failure is logged by class, the forget itself stands) and counts-only in logs.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Iterable, Optional

import forget_match as fm

logger = logging.getLogger(__name__)

MARKER = fm.MARKER
_PAGE = 200


def enabled() -> bool:
    """On (default) unless ``ZOE_FORGET_REDACT`` is 0 / off: the kill switch for the transcript redaction."""
    import os
    return (os.environ.get("ZOE_FORGET_REDACT") or "").strip().lower() not in ("0", "off", "false", "no", "disabled")


# ── the verbatim transcript, at forget time ──────────────────────────────────

async def _with_db(fn: Callable[..., Any]) -> "tuple[int, int]":
    """Run ``fn(db)`` on a pooled connection; ``(0, 0)`` when this process has no database pool at all (a lab / a bare unit test - the
    forget cascade treats that the same way). A pool that exists and then fails raises from ``fn``."""
    import contextlib
    stack = contextlib.AsyncExitStack()
    try:
        from db_pool import get_db_ctx  # type: ignore[import]
        conn = await stack.enter_async_context(get_db_ctx())
    except RuntimeError:
        logger.warning("forget_redact: no database in this process - the transcript was not redacted")
        return 0, 0
    async with stack:
        return await fn(conn)


def _owner_expr(db) -> str:
    """The owner of a ``chat_messages cm JOIN chat_sessions cs`` row: the production expression (per-turn metadata first), or the
    session owner on the SQLite the test suite runs these statements on (the production one uses Postgres regexes)."""
    if type(db).__module__.startswith("aiosqlite"):
        return "cs.user_id"
    from user_filters import message_owner_expr
    return message_owner_expr()


async def _candidates(db, user_id: str, needle: str, after: str) -> list:
    """The owner's ``chat_messages`` rows (user and assistant) holding ``needle``, keyset-paged: ``(id, content, metadata)``."""
    sql = ("SELECT cm.id, cm.content, cm.metadata FROM chat_messages cm JOIN chat_sessions cs ON cm.session_id = cs.id "
           "WHERE " + _owner_expr(db) + " = ? AND cm.id > ? "
           "AND (LOWER(cm.content) LIKE ? OR LOWER(COALESCE(cm.metadata, '')) LIKE ?) ORDER BY cm.id LIMIT " + str(_PAGE))
    return [tuple(r) for r in await (await db.execute(sql, (user_id, after, f"%{needle}%", f"%{needle}%"))).fetchall()]


async def _titles(db, user_id: str, needle: str) -> list:
    cur = await db.execute("SELECT id, title FROM chat_sessions WHERE user_id = ? AND LOWER(title) LIKE ?", (user_id, f"%{needle}%"))
    return [tuple(r) for r in await cur.fetchall()]


async def redact_transcripts(user_id: str, name: str, *, db=None) -> "tuple[int, int]":
    """Replace every span naming ``name`` (whole word, case-blind, separator-blind - the forget sweep's pattern) in the owner's
    ``chat_messages`` content and metadata and in their session titles. Returns ``(rows changed, spans replaced)``. RAISES on a
    store failure: the caller decides whether the forget is confirmed."""
    from memory_forgotten import name_pattern, normalise_key
    words = (normalise_key(name) or str(name).strip().lower()).split(" ")
    needle = max(words, key=len).lower()
    if not (user_id or "").strip() or not needle:
        return 0, 0
    rx = name_pattern(name)
    if db is None:
        return await _with_db(lambda conn: redact_transcripts(user_id, name, db=conn))
    rows = spans = 0
    after = ""
    while True:
        page = await _candidates(db, user_id, needle, after)
        for rid, content, meta in page:
            c2, n1 = rx.subn(MARKER, str(content or ""))
            m2, n2 = rx.subn(MARKER, str(meta)) if meta else (meta, 0)
            if n1 or n2:
                await db.execute("UPDATE chat_messages SET content = ?, metadata = ? WHERE id = ?", (c2, m2, rid))
                rows += 1
                spans += n1 + n2
        if len(page) < _PAGE:
            break
        after = str(page[-1][0])
    for sid, title in await _titles(db, user_id, needle):
        t2, n = rx.subn(MARKER, str(title or ""))
        if n:
            await db.execute("UPDATE chat_sessions SET title = ? WHERE id = ?", (t2, sid))
            rows += 1
            spans += n
    commit = getattr(db, "commit", None)
    if callable(commit):
        await commit()
    return rows, spans


async def sweep_transcripts(user_id: str, *, hours: int = 72, db=None, apply: bool = True) -> "tuple[int, int]":
    """Redact, by the LEDGER alone (exact words, never a near spelling - that is the owner's call), the owner's recent
    ``chat_messages`` that still name a forgotten entity: the reply saved after the forget turn, a later mention. Run by the nightly
    digest before it reads the day; a no-op for a user with nothing forgotten. Returns ``(rows, spans)``; raises on a store failure."""
    import datetime as _dt
    import memory_forgotten as mf
    if not enabled() or not (user_id or "").strip() or not mf.configured() or not await mf.active_hashes(user_id):
        return 0, 0
    if db is None:
        return await _with_db(lambda conn: sweep_transcripts(user_id, hours=hours, db=conn))
    # created_at is TEXT ("YYYY-MM-DD HH:MM:SS+TZ"): a 14 h margin covers any server offset, the exact window does not matter
    since = (_dt.datetime.utcnow() - _dt.timedelta(hours=hours + 14)).strftime("%Y-%m-%d %H:%M:%S")
    sql = ("SELECT cm.id, cm.content FROM chat_messages cm JOIN chat_sessions cs ON cm.session_id = cs.id "
           "WHERE " + _owner_expr(db) + " = ? AND cm.created_at >= ? AND cm.id > ? ORDER BY cm.id LIMIT " + str(_PAGE * 5))
    rows = spans = 0
    after = ""
    while True:
        page = [tuple(r) for r in await (await db.execute(sql, (user_id, since, after))).fetchall()]
        for rid, content in page:
            found = await mf.spans(user_id, str(content or ""), near=False)
            if found:
                new, n = fm.redact(str(content), found)
                if apply:
                    await db.execute("UPDATE chat_messages SET content = ? WHERE id = ?", (new, rid))
                rows += 1
                spans += n
        if len(page) < _PAGE * 5:
            break
        after = str(page[-1][0])
    commit = getattr(db, "commit", None)
    if rows and apply and callable(commit):
        await commit()
    if rows and apply:
        logger.info("FORGET_REDACT user=%s rows=%d spans=%d (nightly sweep)", user_id, rows, spans)
    return rows, spans


def clear_turn_caches(user_id: str) -> int:
    """Drop the per-turn text other modules keep in process for this user (the exact-words turn mark, the retirement turn / offer /
    decision notes): they hold the last message verbatim and would hand it to the next read. Returns how many entries went."""
    n = 0
    for mod, attrs in (("exact_words", ("_marks",)), ("memory_retire", ("_turns", "_offers", "_decided"))):
        try:
            m = __import__(mod)
            for a in attrs:
                if getattr(m, a, {}).pop(user_id, None) is not None:
                    n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


async def on_forget(user_id: str, name: str) -> "tuple[int, int]":
    """The forget-time hook (``memory_forget_entity``): redact the transcript, drop the in-process marks, log the counts.
    Raises only what ``redact_transcripts`` raises. A no-op when ``ZOE_FORGET_REDACT`` is off."""
    if not enabled():
        return 0, 0
    rows, spans = await redact_transcripts(user_id, name)
    clear_turn_caches(user_id)
    logger.info("FORGET_REDACT user=%s rows=%d spans=%d", user_id, rows, spans)
    return rows, spans


# ── the ledger-driven redactor (no name needed) ──────────────────────────────

class LedgerRedactor:
    """Redacts what the ledger says is forgotten. ``sets`` = ``{user_id: frozenset(active hashes)}`` (see ``memory_forgotten``)."""

    def __init__(self, sets: "dict[str, frozenset[str]]", *, near: bool = True):
        self.sets = {u: s for u, s in sets.items() if s}
        self.near = near
        self.spans = 0

    def _spans(self, user_id: str, text: str) -> "list[tuple[int, int]]":
        import memory_forgotten as mf
        exact, nr = mf.spans_in(user_id, text, self.sets[user_id], near=self.near)
        return exact + nr

    def text(self, text: str, user_id: Optional[str] = None) -> str:
        """``text`` with the forgotten spans of ``user_id`` (or, when the owner is unknown, of every user) replaced by the marker."""
        if not isinstance(text, str) or not text:
            return text
        spans: list[tuple[int, int]] = []
        for u in ([user_id] if user_id in self.sets else ([] if user_id else list(self.sets))):
            spans += self._spans(u, text)
        out, n = fm.redact(text, spans)
        self.spans += n
        return out

    def json(self, obj: Any, user_id: Optional[str] = None) -> Any:
        """``obj`` with every string value redacted (keys and structure untouched)."""
        if isinstance(obj, str):
            return self.text(obj, user_id)
        if isinstance(obj, list):
            return [self.json(x, user_id) for x in obj]
        if isinstance(obj, dict):
            return {k: self.json(v, user_id) for k, v in obj.items()}
        return obj


async def load_redactor(*, near: bool = True) -> LedgerRedactor:
    """The ledger of every user with an active entry, as a redactor (needs the database and ``ZOE_FORGET_LEDGER_SALT``)."""
    import memory_forgotten as mf
    if not mf.configured():
        raise RuntimeError("the forget ledger is not configured (ZOE_FORGET_LEDGER_SALT)")
    users = await mf.get_backend().users()          # type: ignore[attr-defined]
    return LedgerRedactor({u: await mf.active_hashes(u) for u in users}, near=near)


# ── the plaintext palace exports ─────────────────────────────────────────────

def _owner(meta: Any) -> Optional[str]:
    return (meta.get("user_id") or meta.get("wing") or None) if isinstance(meta, dict) else None


def redact_export(payload: dict, red: LedgerRedactor) -> int:
    """Redact an export payload IN PLACE and return the spans replaced. Both shapes: ``export_memory_store`` (``collections`` ->
    list of ``{id, document, metadata}``) and ``compact_drawers_index`` (parallel ``ids`` / ``documents`` / ``metadatas``).
    Records, ids and counts are never touched."""
    before = red.spans
    for recs in (payload.get("collections") or {}).values():
        for r in recs:
            u = _owner(r.get("metadata"))
            r["document"] = red.text(r.get("document"), u)
            r["metadata"] = red.json(r.get("metadata"), u)
    docs, metas = payload.get("documents"), payload.get("metadatas")
    if isinstance(docs, list):
        for i, d in enumerate(docs):
            u = _owner(metas[i]) if isinstance(metas, list) and i < len(metas) else None
            docs[i] = red.text(d, u)
            if isinstance(metas, list) and i < len(metas):
                metas[i] = red.json(metas[i], u)
    return red.spans - before


def verify_export(payload: dict) -> "list[str]":
    """What the backup-verify step checks of an export: the shape and the counts. [] = restorable."""
    bad = []
    cols = payload.get("collections")
    if isinstance(cols, dict):
        if payload.get("total_records") != sum(len(v) for v in cols.values()):
            bad.append("total_records does not match the records")
        for k, v in (payload.get("collection_counts") or {}).items():
            if len(cols.get(k, [])) != v:
                bad.append("collection_counts does not match a collection")
        if any(not isinstance(r, dict) or "id" not in r for v in cols.values() for r in v):
            bad.append("a record has no id")
    elif isinstance(payload.get("ids"), list):
        n = len(payload["ids"])
        for key in ("documents", "metadatas", "embeddings"):
            if key in payload and payload[key] is not None and len(payload[key]) != n:
                bad.append(f"{key} length differs from ids")
    else:
        bad.append("neither export shape")
    return bad
