"""Proactivity selector — flag-dark (``ZOE_PROACTIVE_SELECTOR``, default OFF, read per call).

Precompute, don't improvise (research gap #5, docs/research/samantha-context-engineering-
2026-09-29.md §4.5). Nightly, inside the dreaming cycle, each real member gets at most
``CAP`` ranked "things worth raising" in ``proactive_candidates``. At runtime a brain
turn may carry ONE of them as a ``[RAISE …]`` block — on an open/greeting turn, or when
a candidate's cue word is in the user's words — at most once per conversation. The
model only phrases; ranking, triggers and cooldown are decided here in code.

Salience = importance × recency × relevance (Generative Agents' three factors, as a
product so a zero in any one — stale, trivial, not due — sinks the item):
  importance  open loop: emotional_weight / 5 (1–5 → 0.2–1.0) · emotional moment:
              ``candidate_intensity`` clamped to [0.3, 1], default 0.6 · event: 0.6
  recency     0.5 ** (age_h / 72) — half-life 72 h, the continuity window
              (routers.memories._CONTINUITY_RECENT_WINDOW_S) · events: 1.0
  relevance   open loop: follow-up due within 24 h (or overdue) 1.0, within 48 h 0.6,
              no date 0.7, later → not a candidate · moment: 0.8 · event: starts
              within 24 h 1.0, within 48 h 0.7
Items below ``MIN_SALIENCE`` are dropped. Full contract:
docs/knowledge/synthetic-users-and-proactive-recipients.md ("Proactivity selector").
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Shared with the day brief: naive DB timestamps are UTC; stored text is flattened,
# bracket-free (cannot forge a delimiter) and capped.
from brief_first_turn import _as_utc, _clean

logger = logging.getLogger(__name__)

CAP = 5
MIN_SALIENCE = 0.1
HALF_LIFE_H = 72.0
COOLDOWN = timedelta(days=3)
MAX_SURFACED = 2          # raised twice without resolution → never again (no nagging)
EXPIRES = timedelta(hours=36)  # one missed nightly run must not strand stale rows
EVENT_HORIZON = timedelta(hours=48)
_PREPARE_TIMEOUT_S = 2.0
_TRUTHY = {"1", "true", "yes", "on"}

RAISE_OPEN = "[RAISE — once, naturally, only if it fits; otherwise ignore]"
RAISE_CLOSE = "[END RAISE]"
_ASK = {
    "open_loop": "briefly and warmly ask how that is going",
    "emotional": "briefly and warmly ask how that is going now",
    "event": "mention it only if it is useful for their day",
}


def selector_enabled() -> bool:
    return (os.environ.get("ZOE_PROACTIVE_SELECTOR", "") or "").strip().lower() in _TRUTHY


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Candidate:
    kind: str           # open_loop | emotional | event
    source_ref: str
    text: str
    hint: str
    salience: float
    cues: str           # space-separated anchor words
    expires_at: datetime


def salience(importance: float, age_h: float | None, relevance: float) -> float:
    recency = 1.0 if age_h is None else 0.5 ** (max(0.0, age_h) / HALF_LIFE_H)
    return round(max(0.0, min(1.0, importance)) * recency * relevance, 4)


def loop_relevance(due: datetime | None, now: datetime) -> float | None:
    if due is None:
        return 0.7
    if due <= now + timedelta(hours=24):
        return 1.0
    return 0.6 if due <= now + EVENT_HORIZON else None


def score_all(loops: list[dict], moments: list[dict], events: list[dict],
              now: datetime) -> list[Candidate]:
    """Pure: every eligible item from the three sources, scored.

    loops: {id, text, hint, weight, created, due} · moments: {id, text, intensity,
    added} · events: {id, title, start (aware), tz}. Loops and moments must name
    something (open_loop_quality.loop_is_concrete), so junk rows never surface."""
    from memory_digest import _content_tokens
    from open_loop_quality import loop_anchors, loop_is_concrete

    out: list[Candidate] = []
    for lp in loops:
        text = _clean(lp.get("text"))
        rel = loop_relevance(_as_utc(lp.get("due")), now)
        if rel is None or not text or not loop_is_concrete(text):
            continue
        created = _as_utc(lp.get("created")) or now
        weight = max(1, min(5, int(lp.get("weight") or 1)))
        out.append(Candidate("open_loop", f"open_loops:{lp['id']}", text,
                             _clean(lp.get("hint")) or _ASK["open_loop"],
                             salience(weight / 5, (now - created).total_seconds() / 3600, rel),
                             " ".join(sorted(loop_anchors(text))), now + EXPIRES))
    for mo in moments:
        text = _clean(mo.get("text"))
        if not text or not loop_is_concrete(text):
            continue
        try:
            intensity = max(0.3, min(1.0, float(mo.get("intensity"))))
        except (TypeError, ValueError):
            intensity = 0.6
        added = _as_utc(mo.get("added")) or now
        out.append(Candidate("emotional", f"memory:{mo['id']}", text, _ASK["emotional"],
                             salience(intensity, (now - added).total_seconds() / 3600, 0.8),
                             " ".join(sorted(loop_anchors(text))), now + EXPIRES))
    for ev in events:
        start, title = ev.get("start"), _clean(ev.get("title"))
        if not title or start is None or not now < start <= now + EVENT_HORIZON:
            continue
        when = start.astimezone(ev.get("tz") or timezone.utc).strftime("%a %H:%M")
        out.append(Candidate("event", f"events:{ev['id']}", f"{title} ({when})", _ASK["event"],
                             salience(0.6, None, 1.0 if start <= now + timedelta(hours=24) else 0.7),
                             " ".join(sorted(_content_tokens(title))), start))
    return out


def rank(scored: list[Candidate]) -> list[Candidate]:
    """At most CAP candidates, highest salience first, one per topic."""
    from memory_digest import _content_tokens, _loop_is_dup

    kept: list[Candidate] = []
    seen: list[set[str]] = []
    for c in sorted(scored, key=lambda c: c.salience, reverse=True):
        tokens = _content_tokens(c.text)
        if c.salience < MIN_SALIENCE or _loop_is_dup(tokens, seen):
            continue
        kept.append(c)
        seen.append(tokens)
        if len(kept) >= CAP:
            break
    return kept


# ── Nightly precompute ───────────────────────────────────────────────────────
async def _gather(db, user_id: str, now: datetime) -> tuple[list, list, list]:
    """The three sources: unresolved open loops, the continuity recency read's
    emotional rows (72 h, the S4 source) minus moments the emotional_followup push
    already spoke, and the member's own events in the next 48 h."""
    from memory_digest import fact_has_topic
    from memory_service import get_memory_service, is_emotional_memory
    from time_utils import zoe_timezone

    async with db.execute(
        "SELECT id, loop_text, follow_up_hint, emotional_weight, created_at, follow_up_after "
        "FROM open_loops WHERE user_id = ? AND resolved IS NOT TRUE", (user_id,),
    ) as cur:
        loops = [{"id": r[0], "text": r[1], "hint": r[2], "weight": r[3], "created": r[4],
                  "due": r[5]} for r in await cur.fetchall()]
    moments = []
    for ref in await get_memory_service().load_recent_for_prompt(
            user_id, window_s=HALF_LIFE_H * 3600, limit=50, emotional_first=True):
        if not (is_emotional_memory(ref) and fact_has_topic(ref.text or "")):
            continue
        async with db.execute(
            "SELECT 1 FROM proactive_pending WHERE trigger_type = 'emotional_followup' "
            "AND item_id = ? LIMIT 1", (ref.id,),
        ) as cur:
            if await cur.fetchone():
                continue
        meta = ref.metadata or {}
        moments.append({"id": ref.id, "text": ref.text, "added": meta.get("added_at"),
                        "intensity": meta.get("candidate_intensity")})
    tz = zoe_timezone()
    local = now.astimezone(tz)
    days = [(local + timedelta(days=i)).date().isoformat() for i in range(3)]
    async with db.execute(
        "SELECT id, title, start_date, start_time FROM events WHERE user_id = ? AND deleted = 0 "
        "AND start_date IN (?, ?, ?)", (user_id, *days),
    ) as cur:
        rows = await cur.fetchall()
    events = []
    for r in rows:
        m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(r[3] or ""))
        try:
            day = datetime.fromisoformat(str(r[2])[:10]).replace(tzinfo=tz)
        except ValueError:
            continue
        start = day.replace(hour=int(m.group(1)), minute=int(m.group(2))) if m and int(m.group(1)) < 24 else day
        events.append({"id": r[0], "title": r[1], "start": start, "tz": tz})
    return loops, moments, events


async def select_for_user(user_id: str, *, now: datetime | None = None) -> dict | None:
    """Rank and persist one member's candidates. None when the flag is off (no
    I/O). Surfaced state (cooldown, count, session) survives the upsert; rows no
    longer selected are expired, and deleted once out of cooldown."""
    if not selector_enabled():
        return None
    from db_compat import get_compat_db

    now = now or datetime.now(timezone.utc)
    stamp = _iso(now)
    async with get_compat_db() as db:
        scored = score_all(*await _gather(db, user_id, now), now)
        kept = rank(scored)
        for c in kept:
            await db.execute(
                """INSERT INTO proactive_candidates (id, user_id, kind, source_ref, text, hint,
                       salience, on_open, cue_words, expires_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (user_id, kind, source_ref) DO UPDATE SET text = excluded.text,
                       hint = excluded.hint, salience = excluded.salience, on_open = excluded.on_open,
                       cue_words = excluded.cue_words, expires_at = excluded.expires_at,
                       updated_at = excluded.updated_at""",
                (uuid.uuid4().hex, user_id, c.kind, c.source_ref, c.text, c.hint, c.salience,
                 1, c.cues, _iso(c.expires_at), stamp, stamp),
            )
        await db.execute(
            "UPDATE proactive_candidates SET expires_at = ? WHERE user_id = ? AND updated_at < ? "
            "AND expires_at > ?", (stamp, user_id, stamp, stamp),
        )
        await db.execute(
            "DELETE FROM proactive_candidates WHERE user_id = ? AND expires_at <= ? "
            "AND (cooldown_until IS NULL OR cooldown_until <= ?)", (user_id, stamp, stamp),
        )
    logger.info("PROACTIVE_SELECT user=%s candidates=%d kept=%d", user_id, len(scored), len(kept))
    return {"kept": len(kept), "kinds": [c.kind for c in kept]}


# ── Runtime raise (both brain lanes: prepare before the turn, settle in its finally) ──
_holds: dict[str, tuple[str, float]] = {}  # session -> (token, t): injected, not settled
_HOLD_S = 120.0  # brief_first_turn._BRIEFING_HOLD_S: a lost settle cannot hold forever
_raised_sessions: set[str] = set()  # settled with text (backstop for a failed DB write)
_raise_marks: set[str] = set()     # users whose turn deferred a contact offer for a raise
_settling: set = set()
_LEAD = {"event": "On their calendar"}


@dataclass
class Raise:
    user_id: str
    session_id: str
    candidate_id: str
    kind: str
    shape: str      # greeting | cue
    text: str
    hint: str
    token: str

    @property
    def body(self) -> str:
        lead = _LEAD.get(self.kind, "Earlier they told you")
        return (f"{lead}: {self.text}. If it fits, {self.hint} — once, in your own words, "
                "never quoting them and never as a list or a reminder.")

    @property
    def block(self) -> str:
        """The Flue seam's block, appended after the user's words."""
        return f"{RAISE_OPEN}\n{self.body}\n{RAISE_CLOSE}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _eligible(uid: str) -> bool:
    """Real members only (user_filters: guest sentinels and test/demo/probe ids out)."""
    from user_filters import is_synthetic_user

    return not is_synthetic_user(uid)


def _is_command(message: str) -> bool:
    """A deterministic intent hit is a command (or ack/meta): never a raise turn."""
    try:
        from intent_router import detect_intent

        return detect_intent(message, log_miss=False) is not None
    except Exception:  # noqa: BLE001 — unknown shape: fail closed, no raise
        return True


def _words(message: str) -> set[str]:
    low = (message or "").lower()
    toks = set(re.findall(r"[a-z0-9:'-]+", low)) | set(re.findall(r"[a-z0-9]+", low))
    return toks | {t.rstrip("s") for t in toks}


def _held(sid: str) -> bool:
    hold = _holds.get(sid)
    if hold and time.monotonic() - hold[1] >= _HOLD_S:
        _holds.pop(sid, None)
        hold = None
    return hold is not None


async def _load(user_id: str) -> list[tuple]:
    from db_compat import get_compat_db

    async with get_compat_db() as db:
        async with db.execute(
            "SELECT id, kind, text, hint, salience, on_open, cue_words, expires_at, "
            "cooldown_until, surfaced_count, last_surfaced_session "
            "FROM proactive_candidates WHERE user_id = ?", (user_id,),
        ) as cur:
            return [tuple(r) for r in await cur.fetchall()]


def _log(uid: str, kind: str, shape: str, injected: bool, settled: bool, reason: str = "") -> None:
    logger.info("PROACTIVE_RAISE user=%s kind=%s shape=%s injected=%d settled=%d%s", uid, kind,
                shape, int(injected), int(settled), f" reason={reason}" if reason else "")


async def _prepare(message: str, uid: str, sid: str, brief_active: bool) -> Raise | None:
    from brief_first_turn import turn_shape
    from zoe_flue_client import is_continuity_turn

    shape = turn_shape(message)
    if shape != "greeting":
        if _is_command(message):
            return None
        shape = "cue"
    # The continuity check-in is that turn's one job (the offer defers there too).
    if is_continuity_turn(message, uid) or _held(sid) or sid in _raised_sessions:
        return None
    rows = await _load(uid)
    if any(r[10] == sid for r in rows):
        return None  # already raised in this conversation (durable across restarts)
    now, words = _iso(_now()), _words(message)
    for r in sorted(rows, key=lambda r: float(r[4] or 0), reverse=True):
        if str(r[7]) <= now or (r[8] and str(r[8]) > now) or int(r[9] or 0) >= MAX_SURFACED:
            continue
        if (shape == "greeting" and int(r[5] or 0)) or (shape == "cue" and words & set(str(r[6]).split())):
            if brief_active:  # the [Today] brief owns this turn; the candidate waits
                _log(uid, r[1], shape, False, False, "brief")
                return None
            token = uuid.uuid4().hex
            _holds[sid] = (token, time.monotonic())
            _raise_marks.add(uid)
            return Raise(uid, sid, str(r[0]), str(r[1]), shape, _clean(r[2]),
                         _clean(r[3]) or _ASK.get(str(r[1]), _ASK["open_loop"]), token)
    return None


async def prepare(message: str, user_id: str, session_id: str, *,
                  brief_active: bool = False) -> Raise | None:
    """The raise for this turn, or None. NEVER raises; time-boxed. No I/O when off."""
    uid, sid = (user_id or "").strip(), (session_id or "").strip()
    if not selector_enabled() or not uid or not sid or not _eligible(uid):
        return None
    try:
        return await asyncio.wait_for(_prepare(message or "", uid, sid, brief_active),
                                      timeout=_PREPARE_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 — a raise must never break a turn
        logger.debug("proactive-selector: prepare skipped (non-fatal): %r", exc)
        return None


async def settle(raised: Raise | None, *, produced: bool) -> bool:
    """Mark the candidate surfaced once the turn EMITTED reply text (cooldown,
    count, this session). Called from each lane's stream ``finally`` — the
    brief_first_turn.settle pattern: shielded, so a barge-in still records a raise
    that was heard; ``produced=False`` records nothing. NEVER raises."""
    if raised is None:
        return False
    task = asyncio.ensure_future(_settle(raised, produced))
    _settling.add(task)
    task.add_done_callback(_settling.discard)
    return await asyncio.shield(task)


async def _settle(raised: Raise, produced: bool) -> bool:
    settled = False
    try:
        if produced:
            if len(_raised_sessions) > 4096:
                _raised_sessions.clear()
            _raised_sessions.add(raised.session_id)
            from db_compat import get_compat_db

            now = _now()
            async with get_compat_db() as db:
                await db.execute(
                    "UPDATE proactive_candidates SET surfaced_count = surfaced_count + 1, "
                    "cooldown_until = ?, last_surfaced_session = ?, last_surfaced_at = ? WHERE id = ?",
                    (_iso(now + COOLDOWN), raised.session_id, _iso(now), raised.candidate_id),
                )
            settled = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("proactive-selector: settle failed user=%s: %r", raised.user_id, exc)
    finally:
        if (_holds.get(raised.session_id) or ("",))[0] == raised.token:
            _holds.pop(raised.session_id, None)
    _log(raised.user_id, raised.kind, raised.shape, True, settled)
    return settled


def consume_raise_mark(user_id: str) -> bool:
    """True when a raise deferred this user's contact offer since the last
    offer-aging tick (latent_intent_detector); clears the mark."""
    if user_id in _raise_marks:
        _raise_marks.discard(user_id)
        return True
    return False


def _reset_state() -> None:
    """Clear in-process state (tests; simulates a restart)."""
    _holds.clear()
    _raised_sessions.clear()
    _raise_marks.clear()
