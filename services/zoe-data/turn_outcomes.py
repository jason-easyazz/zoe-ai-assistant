"""FAILURE CAPTURE: one outcome record per finished turn, so a real failed turn can become a CONFIRMED router example.

Why (deep dive 2, T-7, 2026-10-19): the router's retrain-and-adopt loop (struggle detector Phase 3) had no fuel - 4 struggle events in 30
days, no intent label on a miss, no tool-name logging, no per-turn record of how a turn was routed or whether it worked. This module is
the record and the owner's review of it. Contract: docs/architecture/struggle-detector.md -> *Failure capture*.

``ZOE_STRUGGLE_OUTCOMES`` = **off (default)** | on, read per call; needs ``ZOE_STRUGGLE_DETECT`` != off (the detector's worker writes it).
  * off - nothing: no handler / tool notes, no rows, no review items, the seam gets nothing from here.
  * on  - every observed turn gets a ``turn_outcomes`` row (alembic 0058) written by the detector's ONE background worker, never on the
          reply path: route + confidence, the handler / tool NAMES that ran, the outcome class, latency, and the traffic class.

The loop, and where a person stands in it ("approve-before-train": user-stated > confirmed > inferred):

    turn -> row (text-free) --(the turn failed: a can't-do / fall-through / error, or a later re-ask / correction / "that's wrong" /
            the thumbs-down button)--> failed row: the member's own scrubbed words + a PROPOSED route (the fast domain that answered the
            follow-up that worked = ``followup``; else the router's belief = ``router``)
         -> the owner REVIEWS the week's failed turns (admin API, CLI, one morning-brief line) and confirms, relabels or rejects each
         -> ONLY a confirmed row is handed to the router retrain seam (``struggle_proposals.queued_router_examples``)

Walls, each pinned by a negative control in ``tests/test_turn_outcomes.py``:
  * harness / probe traffic (``traffic_class``) is stored text-free and can never be marked failed, reviewed, confirmed or handed out;
  * a guest's row holds no words and cannot be confirmed (the words are nobody's to vouch for);
  * a member reviews and decides only their OWN rows (their own words); nobody else's words are shown;
  * an unconfirmed (proposed) or rejected label never reaches the seam; a forgotten turn is erased (``erase_entity``) and the seam
    re-checks the forget ledger anyway;
  * retention (``ZOE_STRUGGLE_RETENTION_DAYS``), forget and user delete reach the rows through ``struggle_detector`` and fail closed.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Iterable, Optional

import struggle_signals as sig

logger = logging.getLogger(__name__)

ENV = "ZOE_STRUGGLE_OUTCOMES"

DID, CANT_DO, FELL_THROUGH, BRAIN, ERROR, INTERRUPTED = "did", "cant_do", "fell_through", "brain", "error", "interrupted"
OUTCOMES = (DID, CANT_DO, FELL_THROUGH, BRAIN, ERROR, INTERRUPTED)
FAILED_OUTCOMES = frozenset({CANT_DO, FELL_THROUGH, ERROR})
PROPOSED, CONFIRMED, REJECTED = "proposed", "confirmed", "rejected"
SOURCE_FOLLOWUP, SOURCE_ROUTER, SOURCE_OWNER = "followup", "router", "owner"
#: what the owner may say a turn should have been: a fast domain, or ``chat`` (the brain was the right place)
LABELS = frozenset(sig.FAST_DOMAINS) | {"chat"}
#: a single-turn signal that is not about ROUTING keeps its row text-free unless something else failed (an over-long answer is not a
#: routing failure, and its words are not needed to fix it)
_NOT_ROUTING = frozenset({sig.VERBOSE})
SUMMARY_MAX = 160
STORE_TIMEOUT_S = 5.0
REVIEW_DAYS = 7.0

_COUNTERS: dict[str, int] = {}


def _count(name: str, n: int = 1) -> None:
    _COUNTERS[name] = _COUNTERS.get(name, 0) + n


def stats() -> dict[str, int]:
    return dict(_COUNTERS)


def enabled() -> bool:
    """The flag, read per call through ``typed_env`` (an unparseable value warns once and stays off). ``on`` / ``enforce`` are the
    flag's own spellings; ``enforce`` is the ladder word the detector's mode flag uses, so it is accepted here too."""
    from typed_env import env_bool, env_str

    if env_str(ENV).strip().lower() == "enforce":
        return True
    return env_bool(ENV, False)


# ── pure classification ──────────────────────────────────────────────────────

def route_of(turn: sig.Turn) -> tuple[str, Optional[float], str]:
    """(domain, confidence, source) of the router's decision for ``turn``. ``source`` = two_stage | two_stage_failed | similarity | ''."""
    if turn.two_stage_failed:
        return turn.predicted_domain or "", turn.predicted_score, "two_stage_failed"
    if turn.two_stage_active and turn.two_stage_domain:
        return turn.two_stage_domain, turn.two_stage_conf, "two_stage"
    if turn.predicted_domain:
        return turn.predicted_domain, turn.predicted_score, "similarity"
    return "", None, ""


def outcome_of(turn: sig.Turn, events: Iterable[sig.Event] = ()) -> str:
    """The outcome class of a finished turn. Pure."""
    if turn.interrupted:
        return INTERRUPTED
    said = sig.cant_do(turn.reply)
    lane = (turn.lane_outcome or "").lower()
    if said == "error" or lane.startswith("fallback") or "error" in lane:
        return ERROR
    if said == "cant_do":
        return CANT_DO
    if any(e.signal == sig.FALLTHROUGH and e.about is turn for e in events):
        return FELL_THROUGH
    return BRAIN if turn.path == sig.PATH_BRAIN else DID


def proposed_label(event: sig.Event, follow_up: dict) -> tuple[str, str]:
    """(label, source) a failed turn is proposed to have needed. The fast domain that answered the follow-up that WORKED is evidence
    (``followup``); a router-training failure with no such follow-up proposes the router's own belief (``router``); else nothing."""
    nxt = str((follow_up or {}).get("next_domain") or "")
    if nxt in sig.FAST_DOMAINS:
        return nxt, SOURCE_FOLLOWUP
    dom = event.about.domain()
    if event.fix_type == sig.FIX_ROUTER_TRAINING and dom in sig.FAST_DOMAINS:
        return dom, SOURCE_ROUTER
    if event.signal == sig.FALLTHROUGH and str(event.numbers.get("domain") or "") in sig.FAST_DOMAINS:
        return str(event.numbers["domain"]), SOURCE_ROUTER
    return "", ""


def channel_of(turn: sig.Turn) -> str:
    """voice | chat | telegram (Telegram rides the chat lane under a ``telegram-`` session)."""
    if turn.channel == "chat" and (turn.session_id or "").startswith("telegram"):
        return "telegram"
    return turn.channel


def failing(signals: Iterable[str], outcome: str) -> bool:
    return outcome in FAILED_OUTCOMES or any(s not in _NOT_ROUTING for s in signals)


def new_row(turn: sig.Turn, *, traffic: str, outcome: str, handler: str = "", tools: Iterable[str] = (), signals: Iterable[str] = (),
            label: tuple[str, str] = ("", ""), summary: str = "", mode: str = "", message_id: str = "") -> dict[str, Any]:
    """The row for one turn. A harness row is text-free and never failed / labelled, whatever it is given."""
    dom, conf, src = route_of(turn)
    household = traffic == "household"
    sigs = sorted(set(signals)) if household else []
    failed = household and failing(sigs, outcome)
    lab, lsrc = label if failed else ("", "")
    return {
        "id": uuid.uuid4().hex, "ts": float(turn.ts), "user_id": "guest" if turn.is_guest else turn.user_id,
        "is_guest": 1 if turn.is_guest else 0, "traffic": traffic, "channel": channel_of(turn), "panel_id": (turn.panel_id or "")[:80],
        "session_id": "" if turn.is_guest else (turn.session_id or "")[:120], "turn_id": turn.turn_id,
        "message_id": "" if (turn.is_guest or not household) else (message_id or "")[:80],
        "route_domain": dom[:40], "route_conf": float(conf) if isinstance(conf, (int, float)) else None, "route_source": src,
        "head_top": (turn.head_top or "")[:40], "handler": (handler or "")[:80], "tools": ",".join(list(tools)[:12])[:200],
        "outcome": outcome, "path_served": turn.path + (f":{turn.lane}" if turn.lane else ""), "latency_ms": turn.latency_ms,
        "failed": 1 if failed else 0, "signals": ",".join(sigs), "proposed_label": lab, "label_source": lsrc,
        "label_status": PROPOSED if failed else "", "label": "", "decided_by": "", "decided_at": None,
        "summary": (summary or "")[:SUMMARY_MAX] if (failed and household and not turn.is_guest) else "", "mode": mode,
    }


# ── the store ────────────────────────────────────────────────────────────────

COLUMNS = ("id", "ts", "user_id", "is_guest", "traffic", "channel", "panel_id", "session_id", "turn_id", "message_id", "route_domain", "route_conf",
           "route_source", "head_top", "handler", "tools", "outcome", "path_served", "latency_ms", "failed", "signals", "proposed_label",
           "label_source", "label_status", "label", "decided_by", "decided_at", "summary", "mode")


def _merge_failed(row: dict, signals: Iterable[str], label: tuple[str, str], summary: str) -> Optional[dict]:
    """The fields to update when a later turn reveals ``row`` failed, or None when nothing may change (a harness row; a decided row)."""
    if row.get("traffic") != "household":
        return None
    sigs = sorted(set(filter(None, str(row.get("signals") or "").split(","))) | set(signals))
    upd: dict[str, Any] = {"failed": 1, "signals": ",".join(sigs)}
    if row.get("label_status") in ("", PROPOSED):
        upd["label_status"] = PROPOSED
        lab, src = label
        # a follow-up proof beats the router's belief; nothing replaces a follow-up proof
        if lab and (row.get("label_source") != SOURCE_FOLLOWUP or src == SOURCE_FOLLOWUP):
            upd["proposed_label"], upd["label_source"] = lab, src
    if summary and not row.get("summary") and not int(row.get("is_guest") or 0):
        upd["summary"] = summary[:SUMMARY_MAX]
    return upd


class SqlBackend:
    """``turn_outcomes`` (alembic 0058) through ``db_pool.get_db_ctx`` (``?`` placeholders)."""

    @staticmethod
    def _ctx():
        from db_pool import get_db_ctx  # type: ignore[import]

        return get_db_ctx()

    async def add(self, row: dict) -> None:
        async with self._ctx() as db:
            await db.execute(f"INSERT INTO turn_outcomes ({', '.join(COLUMNS)}) VALUES ({', '.join('?' for _ in COLUMNS)})",
                             tuple(row.get(c) for c in COLUMNS))
            await db.commit()

    async def _select(self, where: str, params: tuple, limit: int = 500) -> list[dict]:
        async with self._ctx() as db:
            cur = await db.execute(f"SELECT {', '.join(COLUMNS)} FROM turn_outcomes WHERE {where} ORDER BY ts DESC LIMIT ?",
                                   (*params, int(limit)))
            return [dict(zip(COLUMNS, tuple(r))) for r in await cur.fetchall()]

    async def update(self, row_id: str, fields: dict) -> None:
        keys = [k for k in fields if k in COLUMNS and k != "id"]
        if not keys:
            return
        async with self._ctx() as db:
            await db.execute(f"UPDATE turn_outcomes SET {', '.join(f'{k} = ?' for k in keys)} WHERE id = ?",
                             (*[fields[k] for k in keys], row_id))
            await db.commit()

    async def by_turn(self, user_id: str, turn_id: str) -> list[dict]:
        return await self._select("user_id = ? AND turn_id = ?", (user_id, turn_id), 5)

    async def by_id(self, row_id: str) -> list[dict]:
        return await self._select("id = ?", (row_id,), 1)

    async def by_message(self, user_id: str, message_id: str) -> list[dict]:
        return await self._select("user_id = ? AND message_id = ? AND traffic = 'household'", (user_id, message_id), 1)


    async def rows(self, since: float, user_id: Optional[str] = None, failed_only: bool = False, limit: int = 500) -> list[dict]:
        where, params = "ts >= ?", [float(since)]
        if user_id is not None:
            where += " AND user_id = ?"
            params.append(user_id)
        if failed_only:
            where += " AND failed = 1 AND traffic = 'household'"
        return await self._select(where, tuple(params), limit)

    async def confirmed(self, limit: int) -> list[dict]:
        return await self._select("label_status = 'confirmed' AND traffic = 'household' AND is_guest = 0 AND failed = 1 "
                                  "AND summary <> ''", (), limit)

    async def matching(self, user_id: str, needle: str) -> list[tuple[str, str]]:
        async with self._ctx() as db:
            cur = await db.execute("SELECT id, summary FROM turn_outcomes WHERE user_id = ? AND LOWER(summary) LIKE ?",
                                   (user_id, f"%{(needle or '').lower()}%"))
            return [(str(r[0]), str(r[1])) for r in await cur.fetchall()]

    async def delete_ids(self, user_id: str, ids: list[str]) -> int:
        if not ids:
            return 0
        async with self._ctx() as db:
            cur = await db.execute(f"DELETE FROM turn_outcomes WHERE user_id = ? AND id IN ({', '.join('?' for _ in ids)})",
                                   (user_id, *ids))
            await db.commit()
            return int(getattr(cur, "rowcount", 0) or 0)

    async def delete_user(self, user_id: str) -> int:
        async with self._ctx() as db:
            cur = await db.execute("DELETE FROM turn_outcomes WHERE user_id = ?", (user_id,))
            await db.commit()
            return int(getattr(cur, "rowcount", 0) or 0)

    async def purge_before(self, cutoff: float) -> int:
        async with self._ctx() as db:
            cur = await db.execute("DELETE FROM turn_outcomes WHERE ts < ?", (float(cutoff),))
            await db.commit()
            return int(getattr(cur, "rowcount", 0) or 0)


class MemoryBackend:
    """In-process store (tests). Same contract; ``data`` is the stored rows."""

    def __init__(self) -> None:
        self.data: list[dict] = []

    async def add(self, row: dict) -> None:
        self.data.append(dict(row))

    def _pick(self, pred, limit: int = 500) -> list[dict]:
        return [dict(r) for r in sorted(self.data, key=lambda r: -r["ts"]) if pred(r)][:limit]

    async def update(self, row_id: str, fields: dict) -> None:
        for r in self.data:
            if r["id"] == row_id:
                r.update({k: v for k, v in fields.items() if k in COLUMNS and k != "id"})

    async def by_turn(self, user_id: str, turn_id: str) -> list[dict]:
        return self._pick(lambda r: r["user_id"] == user_id and r["turn_id"] == turn_id, 5)

    async def by_id(self, row_id: str) -> list[dict]:
        return self._pick(lambda r: r["id"] == row_id, 1)

    async def by_message(self, user_id: str, message_id: str) -> list[dict]:
        return self._pick(lambda r: r["user_id"] == user_id and r["message_id"] == message_id and r["traffic"] == "household", 1)


    async def rows(self, since: float, user_id: Optional[str] = None, failed_only: bool = False, limit: int = 500) -> list[dict]:
        return self._pick(lambda r: r["ts"] >= since and (user_id is None or r["user_id"] == user_id)
                          and (not failed_only or (r["failed"] == 1 and r["traffic"] == "household")), limit)

    async def confirmed(self, limit: int) -> list[dict]:
        return self._pick(lambda r: r["label_status"] == CONFIRMED and r["traffic"] == "household" and not r["is_guest"]
                          and r["failed"] == 1 and r["summary"], limit)

    async def matching(self, user_id: str, needle: str) -> list[tuple[str, str]]:
        n = (needle or "").lower()
        return [(r["id"], r["summary"]) for r in self.data if r["user_id"] == user_id and n in (r["summary"] or "").lower()]

    async def delete_ids(self, user_id: str, ids: list[str]) -> int:
        before, drop = len(self.data), set(ids)
        self.data = [r for r in self.data if not (r["user_id"] == user_id and r["id"] in drop)]
        return before - len(self.data)

    async def delete_user(self, user_id: str) -> int:
        before = len(self.data)
        self.data = [r for r in self.data if r["user_id"] != user_id]
        return before - len(self.data)

    async def purge_before(self, cutoff: float) -> int:
        before = len(self.data)
        self.data = [r for r in self.data if r["ts"] >= cutoff]
        return before - len(self.data)


_backend: Optional[Any] = None


def get_backend():
    global _backend
    if _backend is None:
        _backend = SqlBackend()
    return _backend


def set_backend(backend: Optional[Any]) -> None:
    """Swap the store (tests); ``None`` restores the SQL default."""
    global _backend
    _backend = backend


# ── writes (the detector's worker; bounded, counted, never raised into a turn) ─

async def record(row: dict) -> bool:
    try:
        await asyncio.wait_for(get_backend().add(row), STORE_TIMEOUT_S)
        _count("rows")
        _count(f"rows_{row.get('traffic')}")
        return True
    except Exception as exc:  # noqa: BLE001 - the turn already happened
        _count("store_errors")
        logger.warning("turn_outcomes: store failed (%s) traffic=%s channel=%s outcome=%s", type(exc).__name__, row.get("traffic"),
                       row.get("channel"), row.get("outcome"))
        return False


async def mark_failed(user_id: str, turn_id: str, signals: Iterable[str], label: tuple[str, str] = ("", ""), summary: str = "") -> bool:
    """A later turn revealed ``turn_id`` failed (a re-ask, a correction, "that was wrong"): flag its row, keep the words (already vetted by
    the caller) and propose a label. A harness row or an already-decided label is left as it is. Never raises."""
    try:
        sigs = list(signals)

        async def _run() -> bool:
            backend = get_backend()
            for row in await backend.by_turn(user_id, turn_id):
                upd = _merge_failed(row, sigs, label, summary)
                if upd:
                    await backend.update(row["id"], upd)
                    return True
            return False

        done = await asyncio.wait_for(_run(), STORE_TIMEOUT_S)
        _count("marked_failed" if done else "mark_missed")
        return done
    except Exception as exc:  # noqa: BLE001
        _count("store_errors")
        logger.warning("turn_outcomes: mark failed write failed (%s) turn=%s", type(exc).__name__, turn_id)
        return False


async def note_feedback(user_id: str, interaction_id: str, kind: str) -> bool:
    """The thumbs-down / correction BUTTON (``routers/chat.submit_feedback``): the rated reply's OWN outcome row (matched exactly on the
    assistant message id stored with it) is marked failed (NEGATIVE_FEEDBACK). A reply with no row of its own - an older one, or one whose
    row was dropped - is never guessed at by falling back to a neighbouring turn. Never raises."""
    try:
        if not enabled() or kind not in ("thumbs_down", "correction") or not str(interaction_id or "").strip():
            return False
        rows = await get_backend().by_message(user_id, str(interaction_id))
        if not rows:
            _count("feedback_no_row")
            return False
        import struggle_detector

        words = await struggle_detector.words_for_turn(user_id, rows[0]["turn_id"])   # '' once the turn left the in-process history
        return await mark_failed(user_id, rows[0]["turn_id"], [sig.NEGATIVE_FEEDBACK], summary=words)
    except Exception as exc:  # noqa: BLE001
        _count("store_errors")
        logger.warning("turn_outcomes: feedback mark failed (%s) message=%s", type(exc).__name__, interaction_id)
        return False


# ── the owner's review ───────────────────────────────────────────────────────

class ReviewError(Exception):
    pass


def public(row: dict, *, with_words: bool) -> dict:
    keep = ("id", "ts", "channel", "route_domain", "route_conf", "route_source", "head_top", "handler", "tools", "outcome", "path_served",
            "latency_ms", "signals", "proposed_label", "label_source", "label_status", "label", "decided_at")
    out = {k: row.get(k) for k in keep}
    if with_words:
        out["words"] = row.get("summary") or ""
    return out


async def review(viewer_id: str, *, days: float = REVIEW_DAYS, now: Optional[float] = None, limit: int = 100) -> dict:
    """The viewer's OWN failed household turns of the last ``days`` (their own words), newest first, plus counts."""
    t = time.time() if now is None else now
    rows = await get_backend().rows(t - days * 86400.0, user_id=viewer_id, failed_only=True, limit=limit)
    items = [public(r, with_words=True) for r in rows if not int(r.get("is_guest") or 0)]
    pending = sum(1 for r in items if r["label_status"] == PROPOSED)
    return {"days": days, "enabled": enabled(), "pending": pending, "items": items}


async def decide(row_id: str, viewer_id: str, action: str, label: Optional[str] = None, *, now: Optional[float] = None) -> dict:
    """``confirm`` (optionally with a different ``label``) or ``reject`` one of the viewer's OWN failed turns. Raises ``ReviewError``."""
    if action not in ("confirm", "reject"):
        raise ReviewError("unknown action")
    rows = await get_backend().by_id(row_id)
    row = rows[0] if rows else None
    if not row or row.get("user_id") != viewer_id:
        raise ReviewError("no such turn")          # another member's row is indistinguishable from a missing one
    if row.get("traffic") != "household" or int(row.get("is_guest") or 0) or not int(row.get("failed") or 0):
        raise ReviewError("this turn cannot be labelled")
    upd: dict[str, Any] = {"decided_by": f"owner:{viewer_id}", "decided_at": float(time.time() if now is None else now)}
    if action == "reject":
        upd["label_status"] = REJECTED
    else:
        lab = (label or row.get("proposed_label") or "").strip()
        if lab not in LABELS:
            raise ReviewError("choose a route: " + ", ".join(sorted(LABELS)))
        if not row.get("summary"):
            raise ReviewError("this turn kept no words, so it cannot become an example")
        upd.update(label_status=CONFIRMED, label=lab)
        if lab != row.get("proposed_label"):
            upd["label_source"] = SOURCE_OWNER
    await get_backend().update(row_id, upd)
    _count(f"decided_{upd['label_status']}")
    return public({**row, **upd}, with_words=True)


async def brief_line(user_id: str, *, days: float = REVIEW_DAYS) -> Optional[str]:
    """One morning-brief line for the owner when some of their failed turns wait for a label check. None otherwise. Never raises."""
    try:
        if not enabled():
            return None
        rep = await asyncio.wait_for(review(user_id, days=days), STORE_TIMEOUT_S)
        n = int(rep.get("pending") or 0)
        if n <= 0:
            return None
        return (f"{n} of your requests this week didn't go the way you wanted; I have a suggested fix for "
                f"{'each' if n > 1 else 'it'} that needs your OK in the struggles report before I learn from {'them' if n > 1 else 'it'}.")
    except Exception:  # noqa: BLE001
        return None


# ── the router seam (``struggle_proposals.queued_router_examples`` appends these) ─

async def confirmed_examples(limit: int = 500) -> list[dict]:
    """Owner-CONFIRMED failed turns as router training pairs, in the seam's shape. Only fast-domain labels (a ``chat`` confirmation says
    the brain was right: nothing to train the router towards). ``label_source`` keeps the seam's two-valued vocabulary: ``followup`` when
    the owner confirmed a follow-up-proven label, else ``router`` with a PERSON as ``decided_by`` (the retrain driver treats a
    person-accepted label as strong); ``label_origin`` says where the label came from. The caller re-checks the forget ledger."""
    if not enabled():       # capture off = nothing from here reaches training, whatever was confirmed while it was on
        return []
    try:
        rows = await asyncio.wait_for(get_backend().confirmed(max(1, min(2000, int(limit)))), STORE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - a missing table / DB blip: no examples from here
        logger.debug("turn_outcomes: confirmed read failed (%s)", type(exc).__name__)
        return []
    import struggle_proposals as sp

    out = []
    for r in rows:
        lab, text = str(r.get("label") or ""), str(r.get("summary") or "").strip()
        if lab not in sig.FAST_DOMAINS or not text or r.get("traffic") != "household" or int(r.get("is_guest") or 0):
            continue
        origin = str(r.get("label_source") or "")
        source = SOURCE_FOLLOWUP if origin == SOURCE_FOLLOWUP and lab == r.get("proposed_label") else SOURCE_ROUTER
        rid = f"turn:{r['id']}"
        out.append({"proposal_id": rid, "status": "accepted", "decided_by": str(r.get("decided_by") or ""), "event_id": rid,
                    "user_id": str(r["user_id"]), "label": lab, "label_source": source, "label_origin": origin or SOURCE_OWNER,
                    "split": sp.split_of(text), "ts": round(float(r["ts"]), 3), "text": text,
                    "audio_ref": sp.audio_ref(r["ts"]) if r.get("channel") == "voice" else None})
    return out


# ── forgetting (called by struggle_detector; RAISES on a store failure) ──────

async def matching_ids(user_id: str, pattern, needle: str) -> list[str]:
    """``user_id``'s rows whose words match ``pattern`` (the forget's whole-word name pattern). RAISES on a failure."""
    hits = await get_backend().matching(user_id, needle)
    return [rid for rid, words in hits if pattern.search(words or "")]
