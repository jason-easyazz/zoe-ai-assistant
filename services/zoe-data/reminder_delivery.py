"""reminder_delivery - a due reminder reaches its owner's PHONE through channels that already exist (gap audit 2026-11-13).

Record: ``docs/knowledge/reminder-phone-delivery.md``. Flag ``ZOE_REMINDER_PHONE_DELIVERY`` = off (default) | shadow | on, read per call.
  off    - ``proactive.engine.fire_notification`` never calls this module; reminders behave exactly as before.
  shadow - the rules run and one ``REMINDER_PHONE`` line per channel says what WOULD happen; nothing is sent or stored.
  on     - reminder web push is sent HERE (instead of the engine's direct push) plus Telegram, per the member's opt-in.

The panel side (spoken announce, the in-app notification, the bell) stays in the engine, unchanged.

Rules (each one tested):
  * OWNER ONLY: the member's own ``push_subscriptions`` rows and their own ``user_prefs.telegram_id``; never anyone else's.
  * OPT-IN: ``user_prefs`` key ``reminder_delivery`` = ``{"channels": ["telegram", "web_push"], "full_text": false}``; absent = none.
  * ADULTS ONLY: ``enrollment_state.audience_of`` must say ``adult`` (a child, guest, unknown or failed lookup gets nothing).
  * QUIET HOURS: the member's ``communication.quiet_hours`` (comm_prefs), else the household's ``ZOE_QUIET_START/END_HOUR``.
  * SENSITIVE (health category or words, private visibility, or unknown) = the neutral line unless ``full_text`` is true.
  * AT MOST ONCE per (dedupe_id, channel): a claim row in ``app_settings`` (``reminder_phone:<uid>:<hash>:<channel>``) is written
    before the send, inside ``user_erase_gate.writing``; a ``retry`` outcome (definitely not sent) releases it. Forget reach:
    ``forget_user`` (called by ``MemoryService.delete_user``).
  * NEVER RAISES, each channel bounded by ``CHANNEL_TIMEOUT_S``; logs carry classes and a hash, never a title, body or id.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import user_erase_gate as erase_gate
from typed_env import env_str

logger = logging.getLogger(__name__)

ENV = "ZOE_REMINDER_PHONE_DELIVERY"
PREF_KEY = "reminder_delivery"
CHANNELS = ("web_push", "telegram")
LEDGER_PREFIX = "reminder_phone:"
CHANNEL_TIMEOUT_S = 20.0
NEUTRAL_TEXT = "You have a reminder."
SENSITIVE_CATEGORIES = frozenset({"health", "medical", "private"})
# Raised before any transport call (a wrong argument, a missing module or key): definitely not sent, so the claim is released.
# Anything else raised by a sender (a transport error mid-send) is "uncertain" and keeps the claim. Both senders already turn
# their transport outcomes into return values (send_push_to_user counts, telegram_notify.send classifies), so this is the rest.
PRE_SEND_ERRORS = (TypeError, ImportError, KeyError, AttributeError, NameError)


def mode() -> str:
    m = env_str("ZOE_REMINDER_PHONE_DELIVERY", "off").strip().lower()
    return m if m in ("off", "shadow", "on") else "off"


def channel_prefs(raw: Any) -> tuple[list[str], bool]:
    """(opted-in channels, full_text) from the stored document; anything malformed reads as nothing."""
    if not isinstance(raw, dict):
        return [], False
    ch = raw.get("channels")
    chans = [c for c in dict.fromkeys(ch) if c in CHANNELS] if isinstance(ch, list) else []
    return chans, raw.get("full_text") is True


@dataclass
class Result:
    outcomes: dict = field(default_factory=dict)   # channel -> "delivered" | "skipped:<class>" | "failed:<class>" | "would_send"
    web_push_sent: int = 0


# -- the I/O seams (each small, each faked in tests) --------------------------------------------------------------------------
def _db():
    from db_compat import get_compat_db
    return get_compat_db()


async def _audience(user_id: str) -> str:
    try:
        from enrollment_state import audience_of
        return await audience_of(user_id)
    except Exception:  # noqa: BLE001 - not knowing who someone is means no phone delivery
        return "unknown"


async def _member_prefs(user_id: str) -> dict:
    from user_prefs import read_prefs

    async with _db() as db:
        return await read_prefs(db, user_id)


async def _subscription_count(user_id: str) -> int:
    async with _db() as db:
        cur = await db.execute("SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
    return int(row[0] or 0) if row else 0


async def reminder_sensitive(item_id: str) -> bool:
    """Is the reminder behind ``item_id`` sensitive? Health category / private visibility / health words in the title. Fail closed."""
    if not item_id:
        return True
    try:
        async with _db() as db:
            cur = await db.execute("SELECT title, category, visibility FROM reminders WHERE id = ?", (item_id,))
            row = await cur.fetchone()
        if row is None:
            return True
        if str(row["category"] or "").lower() in SENSITIVE_CATEGORIES or str(row["visibility"] or "").lower() == "private":
            return True
        from intent_router import _infer_event_category
        return _infer_event_category(str(row["title"] or "")) == "health"
    except Exception:  # noqa: BLE001
        return True


async def _claim(key: str) -> bool:
    async with _db() as db:
        cur = await db.execute("INSERT INTO app_settings (key, value, updated_at) VALUES (?, 'claimed', ?) ON CONFLICT (key) DO NOTHING",
                               (key, datetime.now(timezone.utc).isoformat()))
        await db.commit()
    return (getattr(cur, "rowcount", 1) or 0) > 0


async def _settle(key: str, outcome: str) -> None:
    async with _db() as db:
        if outcome == "retry":   # definitely not sent: release so a later fire of the same reminder may send
            await db.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        else:                    # UPDATE, never an upsert: a row an erase removed meanwhile is not recreated
            await db.execute("UPDATE app_settings SET value = ? WHERE key = ?", (outcome, key))
        await db.commit()


async def _send_web_push(user_id: str, title: str, body: str, url: str) -> int:
    from routers.push import send_push_to_user
    return int(await send_push_to_user(user_id=user_id, title=title, body=body, url=url) or 0)


async def _send_telegram(telegram_id: str, text: str) -> str:
    import telegram_notify
    return await telegram_notify.send(telegram_id, text)


# -- the seam -----------------------------------------------------------------------------------------------------------------
def ledger_key(user_id: str, dedupe_id: str, channel: str) -> str:
    return f"{LEDGER_PREFIX}{user_id}:{hashlib.sha256(dedupe_id.encode()).hexdigest()[:24]}:{channel}"


def _log(m: str, kind: str, channel: str, outcome: str, dedupe_id: str) -> None:
    logger.info("REMINDER_PHONE mode=%s kind=%s channel=%s outcome=%s ref=%s", m, kind, channel, outcome,
                hashlib.sha256(dedupe_id.encode()).hexdigest()[:8])


async def deliver(user_id: str, kind: str, title: str, body: str, *, dedupe_id: str, sensitive: bool = True,
                  url: str = "/", now: Optional[datetime] = None, dry_run: Optional[bool] = None) -> Result:
    """Fan one due reminder out to the member's ENABLED phone channels. Never raises. ``dry_run`` defaults to the flag (shadow)."""
    res = Result()
    m = mode()
    dry = (m != "on") if dry_run is None else dry_run
    try:
        uid = str(user_id or "").strip()
        gen = erase_gate.generation(uid)                      # before any awaited read: an erase after this point wins
        audience = await _audience(uid)
        prefs = await _member_prefs(uid) if audience == "adult" else {}
        chans, full_text = channel_prefs(prefs.get(PREF_KEY))
        quiet = False
        if chans:
            import comm_prefs
            from proactive.triggers.reminder_scan import zoe_now

            local = now or zoe_now()
            comm = prefs.get(comm_prefs.PREF_KEY)
            qh = comm.get("quiet_hours") if isinstance(comm, dict) else None
            if not (isinstance(qh, dict) and comm_prefs._hhmm(qh.get("start")) and comm_prefs._hhmm(qh.get("end"))):
                qh = None                                     # unset or malformed = the household's window, as comm_prefs reads it
            quiet = comm_prefs.in_quiet_hours({"quiet_hours": qh}, local.hour * 60 + local.minute)
        text = f"{title}: {body}" if body and body != title else (title or body)
        if sensitive and not full_text:
            text = NEUTRAL_TEXT
        tasks = {}
        for ch in CHANNELS:
            if audience != "adult":
                res.outcomes[ch] = "skipped:not_adult"
            elif ch not in chans:
                res.outcomes[ch] = "skipped:not_opted_in"
            elif quiet:
                res.outcomes[ch] = "skipped:quiet_hours"
            else:
                tasks[ch] = _channel(ch, uid, gen, text, url, dedupe_id, dry, prefs)
        if tasks:
            done = await asyncio.gather(*(asyncio.wait_for(t, CHANNEL_TIMEOUT_S) for t in tasks.values()), return_exceptions=True)
            for ch, out in zip(tasks, done):
                if isinstance(out, asyncio.TimeoutError):
                    res.outcomes[ch] = "failed:timeout"
                elif isinstance(out, BaseException):
                    res.outcomes[ch] = f"failed:{type(out).__name__}"
                else:
                    res.outcomes[ch], sent = out
                    if ch == "web_push":
                        res.web_push_sent = sent
    except Exception as exc:  # noqa: BLE001 - never into the scheduler
        res.outcomes.setdefault("seam", f"failed:{type(exc).__name__}")
    for ch, out in res.outcomes.items():
        _log("shadow" if dry else m, kind, ch, out, dedupe_id or "")
    return res


async def _channel(ch: str, uid: str, gen: int, text: str, url: str, dedupe_id: str, dry: bool, prefs: dict) -> tuple[str, int]:
    tid = prefs.get("telegram_id")
    if ch == "telegram":
        import telegram_notify
        if not (isinstance(tid, str) and tid.isdigit()):
            return "skipped:not_linked", 0
        if not telegram_notify.available():
            return "skipped:unavailable", 0
    elif await _subscription_count(uid) == 0:
        return "skipped:no_subscription", 0
    if dry:
        return "would_send", 0
    key = ledger_key(uid, dedupe_id, ch)
    async with erase_gate.writing(uid, gen) as live:
        if not live:
            return "skipped:erased", 0
        if not await _claim(key):
            return "skipped:duplicate", 0
    if erase_gate.generation(uid) != gen:      # erased between the claim and the send: send nothing (the erase took the claim)
        return "skipped:erased", 0
    try:
        if ch == "telegram":
            outcome = await _send_telegram(tid, text)
            sent = 1 if outcome == "sent" else 0
        else:
            sent = await _send_web_push(uid, "Zoe", text, url)
            outcome = "sent" if sent else "retry"
        result = ("delivered" if sent else f"failed:{outcome}"), sent
    except PRE_SEND_ERRORS as exc:   # a bad call / missing module / bad key: nothing can have left the box -> release, retry later
        outcome, result = "retry", (f"failed:{type(exc).__name__}", 0)
    except Exception as exc:  # noqa: BLE001 - a transport failure mid-send may have reached the phone: keep the claim (at most once)
        outcome, result = "uncertain", (f"failed:{type(exc).__name__}", 0)
    async with erase_gate.writing(uid, gen) as live:
        if live:
            await _settle(key, outcome)
    return result


async def forget_user(user_id: str) -> int:
    """Right to be forgotten: every ledger row of the member (ids and outcomes only, no words). Raises on a DB error."""
    uid = str(user_id or "").strip()
    if not uid:
        return 0
    pattern = (LEDGER_PREFIX + uid).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ":%"
    try:
        async with erase_gate.erasing(uid):
            async with _db() as db:
                cur = await db.execute("DELETE FROM app_settings WHERE key LIKE ? ESCAPE '\\'", (pattern,))
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        from conversation_notes import _no_store       # the one "nothing stored to erase" rule (a lost pool still raises)

        if _no_store(exc):
            return 0
        logger.error("REMINDER_PHONE forget failed (%s): rows NOT erased", type(exc).__name__)
        raise
    return int(getattr(cur, "rowcount", 0) or 0)


def validate(raw: Any) -> dict:
    """The stored document for a PUT: only known channels, a strict boolean."""
    chans, full_text = channel_prefs(raw)
    return {"channels": chans, "full_text": full_text}

