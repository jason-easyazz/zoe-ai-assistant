"""A once-a-day Telegram digest of a member's PENDING pendant day-review candidates (owner-facing delivery).

Why: every candidate since 2026-10-15 sat ``pending`` - the first-turn mention (``day_review_raise``) was never switched on and the
phone page had no way to reach the owner. This puts the day's proposals in the member's own Telegram chat, where they already are.

Flag-dark, TWO keys: ``ZOE_OMI_REVIEW_TELEGRAM`` (default OFF, read per call) AND ``ZOE_OMI_DAY_REVIEW=on`` (``shadow`` is "no mention"
by the day review's contract). Either off = ``run_once`` returns before any I/O.

APPROVE-BEFORE-WRITE is untouched. This module never writes a list, reminder, event or memory: ``act`` is the only mutation it
offers and it goes through ``omi_review_apply.approve`` (the ONLY writer, with all its floors) or ``omi_day_review.dismiss``
(skip: marks the candidate, writes nothing else). Both are keyed on the member, so another member's candidate is "not found".
The bot's loopback route takes only text + one link button, so the message lists the items and links the Pendant page where the
buttons live; ``act`` is the seam for per-item Telegram buttons (bot side, a follow-up).

Rules: only a linked, real account (never a guest / panel / synthetic id); one message per member per household day, from
``ZOE_OMI_REVIEW_TELEGRAM_HOUR`` (default 8, local); only when there is something NEW since the last digest; after
``MAX_UNANSWERED`` digests in a row with no decision it waits for a decision. The text is the member's own words, sent to their
own chat only; nothing is logged but counts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENV = "ZOE_OMI_REVIEW_TELEGRAM"
STATE_KEY = "omi_review_telegram"
MAX_ITEMS = 5
MAX_UNANSWERED = 3
POLL_S = 600
_TS = "%Y-%m-%dT%H:%M:%SZ"
VIA = "phone"


def enabled() -> bool:
    import omi_day_review as dr
    from typed_env import env_str

    return env_str(ENV).strip().lower() in ("1", "true", "yes", "on") and dr.mode() == "on"


def send_hour() -> int:
    from typed_env import env_int

    return max(0, min(21, env_int("ZOE_OMI_REVIEW_TELEGRAM_HOUR", 8)))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local(now: datetime) -> datetime:
    from time_utils import zoe_timezone

    return now.astimezone(zoe_timezone())


def _s(v: Any, n: int = 90) -> str:
    return " ".join(str(v or "").split())[:n]


def describe(c: dict) -> str:
    """One plain-language line for a candidate (its own payload only)."""
    p, kind = c.get("payload") or {}, c.get("kind")
    when = " ".join(x for x in (_s(p.get("date"), 30), _s(p.get("time"), 12)) if x) or _s(p.get("when_said"), 30)
    if kind == "calendar_event":
        return f"Calendar: {_s(p.get('title'))}" + (f" ({when})" if when else "")
    if kind == "list_item":
        return f"Add to your {_s(p.get('list'), 20) or 'shopping'} list: {_s(p.get('item'))}"
    if kind == "reminder":
        return f"Remind you: {_s(p.get('title'))}" + (f" ({when})" if when else "")
    if kind == "follow_up":
        return f"Come back to: {_s(p.get('title'))}"
    if kind == "person":
        return f"Person: {_s(p.get('name'))}" + (f" ({_s(p.get('relation'), 30)})" if p.get("relation") else "")
    if kind == "owner_fact":
        return f"About you: {_s(p.get('fact'))}"
    if kind == "person_fact":
        return f"About someone you know: {_s(p.get('fact'))}"
    return "A relationship to confirm"


def compose(cands: list[dict], total: int) -> str:
    shown = cands[:MAX_ITEMS]
    lines = [f"Zoe went through your pendant day. {total} thing{'s' if total != 1 else ''} to look at - nothing is saved until you say so:"]
    lines += [f"{i}. {describe(c)}" for i, c in enumerate(shown, 1)]
    if total > len(shown):
        lines.append(f"...and {total - len(shown)} more.")
    lines.append("Approve or skip each one on the Pendant page (Review your day).")
    return "\n".join(lines)


async def act(member: str, action: str, cid: str) -> str:
    """``approve`` | ``skip`` one of THIS member's candidates. Returns ``approved`` | ``skipped`` | ``not_found`` | ``already_decided`` |
    ``refused:<code>``. Approve is ``omi_review_apply.approve`` exactly once; skip writes nothing but the candidate's own status."""
    import omi_day_review as dr
    import omi_review_apply as apply

    if action not in ("approve", "skip") or not enabled():
        return "refused:bad_request"
    if await dr.get_candidate(member, cid) is None:
        return "not_found"
    if action == "skip":
        return "skipped" if await dr.dismiss(member, cid, via=VIA) else "already_decided"
    try:
        await apply.approve(member, cid, via=VIA)
    except apply.ApplyError as exc:
        return "refused:" + str(exc)
    return "approved"


# --- state (the member's own user_prefs; dates and counts only) ----------------------------------------------------------
async def _load_state(uid: str) -> dict:
    from user_prefs import get_pref

    st = await get_pref(uid, STATE_KEY)
    return dict(st) if isinstance(st, dict) else {}


async def _save(uid: str, st: dict) -> None:
    from user_prefs import set_pref

    await set_pref(uid, STATE_KEY, st)


async def _telegram_id(member: str) -> Optional[str]:
    import day_review_raise as drr

    return await drr._telegram_id(member)


def _account(uid: str) -> bool:
    import day_review_raise as drr

    return drr._account(uid)


async def _members() -> list[str]:
    import omi_day_review as dr

    async with dr._db() as db:
        rows = await dr._fetch(db, "SELECT DISTINCT user_id FROM omi_day_candidates WHERE status = 'pending'")
    return sorted(str(r["user_id"]) for r in rows)


async def _decided_since(uid: str, since: str) -> int:
    import omi_day_review as dr

    async with dr._db() as db:
        rows = await dr._fetch(db, "SELECT COUNT(*) AS n FROM omi_day_candidates WHERE user_id = ? AND decided_at > ?", (uid, since or ""))
    return int(rows[0]["n"]) if rows else 0


async def _newest_pending(uid: str) -> str:
    import omi_day_review as dr

    async with dr._db() as db:
        rows = await dr._fetch(db, "SELECT MAX(created_at) AS t FROM omi_day_candidates WHERE user_id = ? AND status = 'pending'", (uid,))
    return str(rows[0]["t"] or "") if rows else ""


async def send_for(member: str, now: datetime) -> str:
    """``sent`` | ``uncertain`` | ``skipped:<why>`` for one member. The day is claimed only when the message may have left
    (``sent`` / ``uncertain``); a retry-safe failure leaves it open for the next poll."""
    import omi_day_review as dr
    import telegram_notify
    from day_review_raise import _phone_url

    if not _account(member):
        return "skipped:not_an_account"
    tid = await _telegram_id(member)
    if not tid or not telegram_notify.available():
        return "skipped:no_telegram"
    st = await _load_state(member)
    today = _local(now).date().isoformat()
    if st.get("day") == today:
        return "skipped:already_today"
    newest = await _newest_pending(member)
    if not newest or newest <= str(st.get("sent_at") or ""):
        return "skipped:nothing_new"
    unanswered = int(st.get("unanswered") or 0)
    if await _decided_since(member, str(st.get("sent_at") or "")):
        unanswered = 0
    if unanswered >= MAX_UNANSWERED:
        return "skipped:waiting_for_a_decision"
    cands = await dr.list_candidates(member, status="pending", limit=50)
    if not cands:
        return "skipped:nothing_pending"
    total = sum((await dr.pending_counts(member)).values()) or len(cands)
    url = _phone_url()
    # Claim the day BEFORE the network call (at-most-once, like change_notices._send): a failed claim write sends nothing; a crash or
    # failed final write after the message left leaves the claim, so the next poll cannot repeat it.
    await _save(member, {**st, "day": today, "unanswered": unanswered, "claim": "sending"})
    outcome = await telegram_notify.send(tid, compose(cands, total), url=url, label="Review your day" if url else None)
    if outcome in ("sent", "uncertain"):
        await _save(member, {"day": today, "sent_at": now.strftime(_TS), "unanswered": unanswered + 1})
    elif str(outcome).startswith("refused"):
        # a PERMANENT rejection (e.g. HTTP 400 for a bad link): keep today's claim so the 10-minute poll does not repeat it all day;
        # sent_at / unanswered are untouched, so tomorrow's digest still goes out once the cause is fixed
        await _save(member, {**st, "day": today, "unanswered": unanswered, "refused": str(outcome)})
    else:  # a retry-safe failure: definitely not delivered, release the claim so the next poll retries
        try:
            await _save(member, st)
        except Exception as exc:  # noqa: BLE001 - the day stays claimed (safe side)
            logger.warning("omi_review_digest: claim release failed (%s)", type(exc).__name__)
    logger.info("OMI_REVIEW_TELEGRAM user=%s outcome=%s pending=%d", member, outcome, total)
    return outcome if outcome in ("sent", "uncertain") else "skipped:" + outcome


async def run_once(now: Optional[datetime] = None) -> dict:
    """One poll: flag off / before the hour / the quiet evening = nothing. Returns counts by outcome."""
    if not enabled():
        return {}
    now = now or _now()
    if not (send_hour() <= _local(now).hour < 22):
        return {}
    out: dict[str, int] = {}
    for member in await _members():
        try:
            r = await send_for(member, now)
        except Exception as exc:  # noqa: BLE001 - one member's failure never stops the others
            logger.warning("omi_review_digest: %s", type(exc).__name__)
            r = "error"
        out[r] = out.get(r, 0) + 1
    return out


async def run_loop() -> None:
    """Started from ``main.py`` only when the flag is on. Sleeps first (a restart never fires a burst)."""
    while True:
        await asyncio.sleep(POLL_S)
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("omi_review_digest: pass failed (%s)", type(exc).__name__)
