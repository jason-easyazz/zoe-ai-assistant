"""Delivery-ledger LINES (register BH2) — what was raised, what was held back and why, and
whether it was welcome. ``ZOE_DELIVERY_LEDGER`` = ``shadow`` (default) | ``on`` | ``off``.

``proactive/ledger.py`` (``ZOE_PROACTIVE_LEDGER``) follows a block that WENT OUT: was it voiced,
did the person take it up. It cannot see the other half of the story: an item the selector
considered and a gate held (spacing, the daily cap, the brief owning the turn), nor what the
person thought of a raise. This module writes one line per such event to
``proactive_ledger_lines`` (migration 0040), so the 0-of-5 voiced failure and the nagging
failure both become RATES instead of silences (docs/research/best-ideas-register-2026-10-09.md
BH2; person-likeness 4.6 "the household tier").

Modes (read per call):
  * ``shadow`` (default) - every raised / withheld / pulled item and every welcome tap is
    LOGGED; nothing the person hears or sees changes. A back-off that WOULD have held a raise is
    logged as ``would_withhold``, so the effect of ``on`` is measurable before it is enabled.
  * ``on`` - as shadow, and the taps tune RAISING: one "not now" in a week doubles the spacing for
    that item CLASS (open_loop / emotional / event) for that member; two switch the class off for
    the week. Tone, warmth and wording are never touched, and a pull is never held back (the
    person asked). ``welcome`` / ``neutral`` taps are labels for the later acceptance head
    (>= 200 labelled rows, >= 40 positives, register BH2) and change nothing - a signal that can
    only ever raise the rate of raising is the engagement trap the 981-person RCT warns about.
  * ``off`` - no line is written and no tap is accepted (nothing to read, no DB access).

Every entry point NEVER raises (a ledger must not break a turn) and logs counts and kinds only:
no item text, utterance or reply text is stored or logged.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

MODES = ("off", "shadow", "on")
SIGNALS = ("welcome", "neutral", "not_now")
# The panel / the state endpoint may name this much of an item's shape - never its content.
_KLASS = {"event": "notify", "open_loop": "question", "emotional": "question"}
BACKOFF_WINDOW = timedelta(days=7)
CLASS_OFF_AT = 2           # two "not now" taps inside the window switch the class off
TAP_WINDOW = timedelta(minutes=10)  # a tap is about the raise / pull this recent
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_SENS_TTL_S = 300.0
_sens_cache: tuple[float, bool] | None = None


def delivery_ledger_mode() -> str:
    """``ZOE_DELIVERY_LEDGER``: off | shadow (default) | on. Unknown values read as shadow (the
    safe middle: it records and changes nothing)."""
    raw = (os.environ.get("ZOE_DELIVERY_LEDGER", "") or "").strip().lower()
    if raw in {"0", "false", "no", "none"}:
        return "off"
    if raw in {"1", "true", "yes", "active"}:
        return "on"
    return raw if raw in MODES else "shadow"


def lines_enabled() -> bool:
    return delivery_ledger_mode() != "off"


def backoff_active() -> bool:
    return delivery_ledger_mode() == "on"


def klass_of(kind: str) -> str:
    return _KLASS.get(kind, "question")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS_FMT)


def _day(now: datetime) -> str:
    from time_utils import zoe_timezone

    return now.astimezone(zoe_timezone()).date().isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── the optional restraint tier (read-only, by name; degrades to "") ───────────────────────
async def _has_sensitivity_column() -> bool:
    """True when ``proactive_candidates`` carries a ``sensitivity`` column (the restraint tier,
    person-likeness SAL3). Probed on its OWN connection - a missing column must never poison the
    caller's - and cached for a few minutes."""
    global _sens_cache
    if _sens_cache and time.monotonic() - _sens_cache[0] < _SENS_TTL_S:
        return _sens_cache[1]
    ok = False
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            async with db.execute("SELECT sensitivity FROM proactive_candidates LIMIT 1") as cur:
                await cur.fetchall()
        ok = True
    except Exception:  # noqa: BLE001 - absent column / table: degrade gracefully
        ok = False
    _sens_cache = (time.monotonic(), ok)
    return ok


async def sensitivity_of(db, candidate_ids: list[str]) -> dict[str, str]:
    """``{candidate id: sensitivity class}`` for the ids that have one. ``{}`` when the
    restraint tier has not landed (no column) or the read fails."""
    if not candidate_ids or not await _has_sensitivity_column():
        return {}
    try:
        marks = ", ".join("?" for _ in candidate_ids)
        async with db.execute(
            f"SELECT id, sensitivity FROM proactive_candidates WHERE id IN ({marks})",
            tuple(candidate_ids),
        ) as cur:
            return {str(r[0]): str(r[1]) for r in await cur.fetchall() if r[1]}
    except Exception:  # noqa: BLE001
        return {}


def _reset_state() -> None:
    """Tests: forget the cached column probe."""
    global _sens_cache
    _sens_cache = None


# ── writers ───────────────────────────────────────────────────────────────────────────────
async def record(db, *, user_id: str, line: str, kind: str, source_ref: str = "",
                 reason: str = "", score: float | None = None, shape: str = "",
                 channel: str = "", session_id: str = "", sensitivity: str = "",
                 signal: str = "", target_id: str = "", now: datetime | None = None,
                 idem: str | None = None) -> str | None:
    """Insert one line (idempotent). Returns the line id when a NEW row was written, else
    None. A no-op with the mode ``off``. Never raises."""
    if not lines_enabled() or not user_id:
        return None
    now = now or _now()
    try:
        lid = uuid.uuid4().hex
        key = idem or "|".join((user_id, line, kind, source_ref, reason, _day(now)))
        cur = await db.execute(
            """INSERT INTO proactive_ledger_lines (id, idem_key, user_id, line, kind, klass,
                   source_ref, reason, score, shape, channel, session_id, sensitivity, signal,
                   target_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (idem_key) DO NOTHING""",
            (lid, key, user_id, line, kind, klass_of(kind), source_ref, reason, score, shape,
             channel, session_id, sensitivity, signal, target_id, _iso(now)),
        )
        new = (getattr(cur, "rowcount", 1) or 0) > 0
    except Exception as exc:  # noqa: BLE001 - the ledger must never break a turn
        logger.warning("delivery-lines: record failed user=%s line=%s: %r", user_id, line, exc)
        return None
    if new:
        logger.info("DELIVERY_LINE user=%s line=%s kind=%s reason=%s shape=%s", user_id, line,
                    kind, reason or "-", shape or "-")
    return lid if new else None


async def _candidate(db, candidate_id: str) -> tuple[str, str, float | None] | None:
    async with db.execute(
        "SELECT kind, source_ref, salience FROM proactive_candidates WHERE id = ?",
        (candidate_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    try:
        score = float(row[2])
    except (TypeError, ValueError):
        score = None
    return str(row[0]), str(row[1]), score


async def log_for_candidate(db, *, line: str, candidate_id: str, user_id: str, reason: str = "",
                            shape: str = "", channel: str = "", session_id: str = "",
                            now: datetime | None = None) -> str | None:
    """A raise settled (``line='raised'``) or a gate held a candidate (``'withheld'`` /
    ``'would_withhold'``): resolve the item's identity, score and sensitivity from its candidate
    row and write the line. Never raises."""
    if not lines_enabled():
        return None
    try:
        cand = await _candidate(db, candidate_id)
        if cand is None:
            return None
        kind, ref, score = cand
        sens = (await sensitivity_of(db, [candidate_id])).get(candidate_id, "")
        return await record(db, user_id=user_id, line=line, kind=kind, source_ref=ref,
                            reason=reason, score=score, shape=shape, channel=channel,
                            session_id=session_id, sensitivity=sens, now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("delivery-lines: log failed user=%s line=%s: %r", user_id, line, exc)
        return None


async def log_held(*, candidate_id: str, user_id: str, reason: str, shape: str, session_id: str,
                   shadow: bool = False, now: datetime | None = None) -> None:
    """The selector held this turn's candidate back, with the gate's name as the reason. Opens
    its own connection (the selector's ``_prepare`` has none) and never raises."""
    if not lines_enabled():
        return
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            await log_for_candidate(db, line="would_withhold" if shadow else "withheld",
                                    candidate_id=candidate_id, user_id=user_id, reason=reason,
                                    shape=shape, session_id=session_id, now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("delivery-lines: held log failed user=%s: %r", user_id, exc)


# ── per-class tuning of RAISING (mode ``on`` only; never tone) ────────────────────────────
async def class_hold(db, user_id: str, kind: str, last_stamp: str | None, now: datetime,
                     gap_s: int) -> str:
    """Why a raise of this item CLASS must wait on the person's own "not now" taps
    (``class_off`` | ``class_backoff``), or ``""``. ``last_stamp`` is the newest
    ``last_surfaced_at`` among the member's candidates of this kind; ``gap_s`` the base raise gap
    (0 = spacing disabled, so there is nothing to double). Never raises."""
    try:
        since = _iso(now - BACKOFF_WINDOW)
        async with db.execute(
            "SELECT COUNT(*) FROM proactive_ledger_lines WHERE user_id = ? AND line = 'welcome' "
            "AND signal = 'not_now' AND kind = ? AND created_at >= ?",
            (user_id, kind, since),
        ) as cur:
            row = await cur.fetchone()
        taps = int(row[0] or 0) if row else 0
    except Exception as exc:  # noqa: BLE001 - unreadable: no hold (fail open for a nicety)
        logger.debug("delivery-lines: class_hold read failed: %r", exc)
        return ""
    if taps >= CLASS_OFF_AT:
        return "class_off"
    if taps == 1 and gap_s and last_stamp and str(last_stamp) > _iso(now - timedelta(seconds=gap_s * 2)):
        return "class_backoff"
    return ""


# ── the welcome tap ───────────────────────────────────────────────────────────────────────
async def recent_deliveries(db, user_id: str, now: datetime) -> list[tuple]:
    """The member's raised / pulled lines inside ``TAP_WINDOW``, newest first:
    ``(id, kind, source_ref, session_id)``. A line that already has a tap stays here - a second
    tap REPLACES the first (the last tap wins)."""
    since = _iso(now - TAP_WINDOW)
    async with db.execute(
        "SELECT id, kind, source_ref, session_id FROM proactive_ledger_lines "
        "WHERE user_id = ? AND line IN ('raised', 'pulled') AND created_at >= ? "
        "ORDER BY created_at DESC LIMIT 10",
        (user_id, since),
    ) as cur:
        return [tuple(r) for r in await cur.fetchall()]


async def record_tap(user_id: str, signal: str, *, target_id: str = "", channel: str = "",
                     now: datetime | None = None) -> int:
    """A one-tap signal about the most recent raise / pull (or the one named by ``target_id``).
    Writes one ``welcome`` line per delivery it covers and returns how many. 0 = nothing recent to
    tap about, the mode is ``off``, or the signal is unknown. A repeat tap on the same delivery
    replaces the earlier one. Never raises."""
    if signal not in SIGNALS or not user_id or not lines_enabled():
        return 0
    now = now or _now()
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            if target_id:
                async with db.execute(
                    "SELECT id, kind, source_ref, session_id FROM proactive_ledger_lines "
                    "WHERE id = ? AND user_id = ? AND line IN ('raised', 'pulled')",
                    (target_id, user_id),
                ) as cur:
                    targets = [tuple(r) for r in await cur.fetchall()]
            else:
                targets = await recent_deliveries(db, user_id, now)
            wrote = 0
            for tid, kind, ref, sid in targets:
                # The last tap wins: replace, never stack (one signal per delivered item).
                await db.execute(
                    "DELETE FROM proactive_ledger_lines WHERE target_id = ? AND line = 'welcome' "
                    "AND user_id = ?", (str(tid), user_id))
                if await record(db, user_id=user_id, line="welcome", kind=str(kind),
                                source_ref=str(ref), signal=signal, target_id=str(tid),
                                channel=channel, session_id=str(sid or ""), now=now,
                                idem=f"{user_id}|welcome|{tid}"):
                    wrote += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("delivery-lines: tap failed user=%s: %r", user_id, exc)
        return 0
    if wrote:
        logger.info("DELIVERY_TAP user=%s signal=%s covered=%d", user_id, signal, wrote)
    return wrote


# ── reading the ledger (the measurement the register asks for) ────────────────────────────
async def summary(db, user_id: str | None = None, *, days: int = 7,
                  now: datetime | None = None) -> dict:
    """Counts only: lines by ``line`` and ``reason``, taps by signal, and the intrusive rate per
    item class (``not_now`` taps over raised + pulled lines of that class). One member, or the
    household when ``user_id`` is None."""
    now = now or _now()
    since = _iso(now - timedelta(days=days))
    where, args = "created_at >= ?", [since]
    if user_id:
        where, args = "user_id = ? AND created_at >= ?", [user_id, since]
    async with db.execute(
        f"SELECT line, reason, kind, signal, COUNT(*) FROM proactive_ledger_lines WHERE {where} "
        "GROUP BY line, reason, kind, signal", tuple(args),
    ) as cur:
        rows = [tuple(r) for r in await cur.fetchall()]
    by_line: dict[str, int] = {}
    held: dict[str, int] = {}
    taps: dict[str, int] = {}
    delivered: dict[str, int] = {}
    not_now: dict[str, int] = {}
    for line, reason, kind, signal, n in rows:
        n = int(n)
        by_line[line] = by_line.get(line, 0) + n
        if line in ("withheld", "would_withhold"):
            held[reason] = held.get(reason, 0) + n
        if line in ("raised", "pulled"):
            delivered[kind] = delivered.get(kind, 0) + n
        if line == "welcome":
            taps[signal] = taps.get(signal, 0) + n
            if signal == "not_now":
                not_now[kind] = not_now.get(kind, 0) + n
    intrusive = {k: round(not_now.get(k, 0) / v, 3) for k, v in delivered.items() if v}
    return {"lines": by_line, "held_by_reason": held, "taps": taps, "delivered": delivered,
            "intrusive_rate": intrusive}
