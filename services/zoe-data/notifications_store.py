"""Household notifications on the wall panel: the store, the orb count, the panel card, and the first creator.

Flag ``ZOE_NOTIFICATIONS`` = off | on (default ON, read per call). Off = every hook here returns before any I/O: the orb inbox has no
``notify`` key, no card is pushed, the card routes are absent and nothing is created. Spec + owner acceptance:
``docs/knowledge/notifications.md``. The spoken side (the end-of-turn tail, "read them") is ``notification_tail.py``.

The table is the existing ``notifications`` (migration 0001): ``delivered`` 0/1 is unread/read; sensitivity rides in ``data``
(``{"sensitive": true}``), so no migration. A creator calls ``create`` (deterministic id, ON CONFLICT DO NOTHING) and nothing else.

PRIVACY. Every read is the member's OWN rows. The orb count is for the request's own member session (a guest, a device token or a
degraded session reads 0). The card goes only to THAT panel's own push channel (``voice_session.send_to_panel``) with a card token
(``panel_review_cards.mint``); a tap is honoured only while that member's voice session is open there, or with their own session.
A wearer proposal's lines (what the pendant heard) are read only to build that card and are never stored in a row, never logged.
Logs carry counts and types, never a title or message.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any, Optional

import user_erase_gate as erase_gate
from typed_env import env_str

logger = logging.getLogger(__name__)

ENV = "ZOE_NOTIFICATIONS"
ENDPOINT = "/api/panel/notifications"
CARD_KIND = "notifications"
MAX_CARD = 8                       # rows on the card; the rest wait (do not overwhelm)
MAX_PROPOSALS = 3                  # wearer proposals shown at once
LINES_PER_PROPOSAL = 3
LINE_CHARS = 60
REFRESH_EVERY_S = 600.0            # creators that look at other stores run at most this often per member
WEARER_TYPE = "wearer_confirm"
WEARER_TITLE = "Please confirm this voice"
WEARER_MESSAGE = "I keep hearing the same voice on your pendant. Is it you?"


def enabled() -> bool:
    return env_str("ZOE_NOTIFICATIONS", "on").strip().lower() != "off"


def _db():
    from db_compat import get_compat_db
    return get_compat_db()


def _account(user_id: str) -> Optional[str]:
    """A real member account id, else None (guest sentinels, ``guest-<id>``, a panel id, empty)."""
    m = str(user_id or "").strip()
    if not m or m.startswith("panel"):
        return None
    try:
        import identity_facts
        from user_filters import is_unidentified

        if is_unidentified(m) or not identity_facts._is_account_id(m):
            return None
    except Exception:  # noqa: BLE001
        return None
    return m


async def _names_forgotten(user_id: str, text: str) -> bool:
    """Does the text name an entity the member has forgotten (the in-flight tombstone, then the durable ledger)? Fail closed."""
    if not (text or "").strip():
        return False
    try:
        import memory_forgotten
        import memory_tombstones

        return bool(memory_tombstones.matching_tombstone(user_id, text) or await memory_forgotten.matches(user_id, text))
    except Exception:  # noqa: BLE001 - cannot tell = not created
        return True


def _parse(raw: Any) -> dict:
    try:
        d = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


def is_sensitive(n: dict) -> bool:
    return bool((n.get("data") or {}).get("sensitive"))


# -- the store ---------------------------------------------------------------------------------------------------------------------
async def create(user_id: str, type: str, title: str, message: str, data: Optional[dict] = None, *, dedupe_id: Optional[str] = None,
                 sensitive: bool = False, scheduled_at: Optional[int] = None) -> Optional[str]:
    """One notification for ``user_id``; the id when a row was inserted, else None (off, not a member, a member being erased or erased
    since scheduled, a member with no household account, a forgotten name in the words, a duplicate ``dedupe_id``, a DB error).
    ``scheduled_at`` = the erase generation when the creator was scheduled (default now). The member rule is ``omi_devices.assign``'s:
    the erase gate first (an erase in progress or since scheduled refuses), then the account must still exist and not be a guest,
    read under that gate. Never raises."""
    if not enabled():
        return None
    uid = _account(user_id)
    if uid is None:
        return None
    gen = erase_gate.generation(uid) if scheduled_at is None else scheduled_at
    nid = f"{type}:{uid}:{dedupe_id}" if dedupe_id else f"{type}:{uuid.uuid4().hex}"
    payload = {**(data or {}), **({"sensitive": True} if sensitive else {})}
    try:
        async with erase_gate.writing(uid, gen) as live:
            if not live:
                logger.info("NOTIFY event=dropped reason=erased_since_scheduled type=%s", type)
                return None
            if await _names_forgotten(uid, f"{title} {message}"):
                logger.info("NOTIFY event=dropped reason=forgotten type=%s", type)
                return None
            async with _db() as db:
                ucur = await db.execute("SELECT role FROM users WHERE id = ?", (uid,))
                urow = await ucur.fetchone()
                if urow is None or str(urow[0] or "") in ("guest",):
                    logger.info("NOTIFY event=dropped reason=no_member type=%s", type)
                    return None
                cur = await db.execute("INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at) "
                                       "VALUES (?,?,?,?,?,?,0,NOW()) ON CONFLICT (id) DO NOTHING",
                                       (nid, uid, type, title, message, json.dumps(payload)))
                await db.commit()
                made = (getattr(cur, "rowcount", 1) or 0) > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("NOTIFY event=failed type=%s (%s)", type, exc.__class__.__name__)
        return None
    logger.info("NOTIFY event=%s type=%s sensitive=%d", "created" if made else "duplicate", type, int(sensitive))
    return nid if made else None


async def unread(user_id: str, limit: int = MAX_CARD) -> list[dict]:
    """The member's unread notifications, newest first; [] off, not a member, or unreadable."""
    uid = _account(user_id)
    if not enabled() or uid is None:
        return []
    try:
        async with _db() as db:
            cur = await db.execute("SELECT id, type, title, message, data, created_at FROM notifications WHERE user_id = ? AND delivered = 0 "
                                   "ORDER BY created_at DESC LIMIT ?", (uid, int(limit)))
            rows = await cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("notifications: unread failed (%s)", exc.__class__.__name__)
        return []
    return [{"id": r[0], "type": r[1], "title": r[2] or "", "message": r[3] or "", "data": _parse(r[4]), "created_at": r[5]} for r in rows]


async def unread_count(user_id: str) -> int:
    uid = _account(user_id)
    if not enabled() or uid is None:
        return 0
    try:
        async with _db() as db:
            cur = await db.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND delivered = 0", (uid,))
            row = await cur.fetchone()
        return int(row[0] or 0) if row else 0
    except Exception:  # noqa: BLE001
        return 0


async def mark_read(user_id: str, nid: str, action: str = "done") -> bool:
    try:
        async with _db() as db:
            cur = await db.execute("UPDATE notifications SET delivered = 1, action_taken = ? WHERE id = ? AND user_id = ? AND delivered = 0",
                                   (action, nid, user_id))
            await db.commit()
        return (getattr(cur, "rowcount", 0) or 0) > 0
    except Exception:  # noqa: BLE001
        return False


# -- creators ----------------------------------------------------------------------------------------------------------------------
_REFRESHED: dict[str, float] = {}


async def refresh(user_id: str) -> None:
    """Run the creators that read other stores, at most once per ``REFRESH_EVERY_S`` per member. Never raises."""
    uid = _account(user_id)
    if not enabled() or uid is None:
        return
    now = time.monotonic()
    if now - _REFRESHED.get(uid, -REFRESH_EVERY_S) < REFRESH_EVERY_S:
        return
    _REFRESHED[uid] = now
    await notify_wearer_proposals(uid)


async def _wearer_status(uid: str) -> Optional[dict]:
    from typed_env import env_bool

    if not env_bool("ZOE_OMI_SYNC_ENABLED", False):
        return None
    try:
        import omi_repass

        async with _db() as db:
            return await omi_repass.wearer_status(db, uid)
    except Exception as exc:  # noqa: BLE001 - no pendant tables on this box: nothing to ask
        logger.debug("notifications: wearer status unavailable (%s)", exc.__class__.__name__)
        return None


async def notify_wearer_proposals(user_id: str) -> Optional[str]:
    """The pendant keeps hearing a regular voice and the member has no confirmed wearer print: ONE notification per batch of open
    proposals ("Please confirm this voice"). The row holds proposal ids only, never what was said. Never raises."""
    uid = _account(user_id)
    if not enabled() or uid is None:
        return None
    gen = erase_gate.generation(uid)       # BEFORE the read: an erase that completes during it stales this create
    st = await _wearer_status(uid)
    if not st or int(st.get("confirmed") or 0) >= int(st.get("needed") or 0) or not st.get("proposals"):
        return None
    ids = sorted(str(p["id"]) for p in st["proposals"])
    # one open ask per member: a batch that shrank (one proposal decided or expired) or grew is the SAME ask, so the unread row is
    # updated in place rather than a second "confirm this voice" appearing beside it
    for n in await unread(uid, limit=50):
        if n.get("type") != WEARER_TYPE:
            continue
        if sorted(str(x) for x in (n.get("data") or {}).get("proposals") or []) != ids:
            try:
                async with erase_gate.writing(uid, gen) as live:
                    if live:
                        async with _db() as db:
                            await db.execute("UPDATE notifications SET data = ? WHERE id = ? AND user_id = ?",
                                             (json.dumps({**(n.get("data") or {}), "proposals": ids}), n["id"], uid))
                            await db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("NOTIFY event=failed type=%s (%s)", WEARER_TYPE, exc.__class__.__name__)
        return None
    batch = hashlib.sha256("|".join(ids).encode()).hexdigest()[:16]
    return await create(uid, WEARER_TYPE, WEARER_TITLE, WEARER_MESSAGE, {"proposals": ids}, dedupe_id=batch, scheduled_at=gen)


# -- the orb -----------------------------------------------------------------------------------------------------------------------
async def orb_count(caller: Optional[dict]) -> Optional[int]:
    """The inbox's ``notify`` field: the request's OWN member's unread count. None = the key is left out: the flag is off, or the caller is
    not a member session (a guest, a device token, a degraded session - they get only ``orb_any``'s boolean)."""
    if not enabled() or not _member_session(caller):
        return None
    uid = str(_account(str((caller or {}).get("user_id") or "")))
    try:
        from proactive.pull import _member

        if not _member(uid):
            return 0
    except Exception:  # noqa: BLE001
        return 0
    await refresh(uid)
    return await unread_count(uid)


_HOUSEHOLD_REFRESHED = [-REFRESH_EVERY_S]


async def _refresh_household() -> None:
    """The creators for every member the pendant has proposals for, at most once per ``REFRESH_EVERY_S`` (the kiosk poll has no member
    to refresh for). Never raises."""
    from typed_env import env_bool

    now = time.monotonic()
    if now - _HOUSEHOLD_REFRESHED[0] < REFRESH_EVERY_S or not env_bool("ZOE_OMI_SYNC_ENABLED", False):
        return
    _HOUSEHOLD_REFRESHED[0] = now
    try:
        async with _db() as db:
            cur = await db.execute("SELECT DISTINCT user_id FROM omi_wearer_samples WHERE status = 'proposed'")
            uids = [str(r[0]) for r in await cur.fetchall()]
    except Exception:  # noqa: BLE001 - no pendant tables: nothing to create
        return
    for uid in uids:
        await refresh(uid)


def _member_session(caller: Optional[dict]) -> bool:
    c = caller or {}
    return _account(str(c.get("user_id") or "")) is not None and "device_token_role" not in c and not c.get("auth_degraded")


async def orb_any(caller: Optional[dict]) -> Optional[bool]:
    """The inbox's ``notify_any`` for a caller that is NOT a member session (the kiosk guest panel): True when ANY household member
    (never a guest) has an unread NON-sensitive notification. A bare boolean - never a count, never whose. None = the key is left out:
    the flag is off, or the caller is a member (they get their own ``notify`` count instead)."""
    if not enabled() or _member_session(caller):
        return None
    await _refresh_household()
    try:
        from proactive.pull import _member

        async with _db() as db:
            cur = await db.execute("SELECT user_id, data FROM notifications WHERE delivered = 0 ORDER BY created_at DESC LIMIT 500")
            rows = await cur.fetchall()
        return any(_account(str(r[0])) and _member(str(r[0])) and not _parse(r[1]).get("sensitive") for r in rows)
    except Exception:  # noqa: BLE001
        return False


# -- the card ----------------------------------------------------------------------------------------------------------------------
def _short(text: str, n: int = LINE_CHARS) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "..."


async def _open_proposals(uid: str, n: dict) -> list[dict]:
    st = await _wearer_status(uid)
    wanted = set((n.get("data") or {}).get("proposals") or [])
    return [p for p in (st or {}).get("proposals") or [] if str(p.get("id")) in wanted]


async def _proposal_lines(uid: str, pid: str) -> list[str]:
    """Owner-only output: what was said in a proposal's windows. Goes into that member's card and nowhere else."""
    try:
        import omi_repass

        async with _db() as db:
            rows = await omi_repass.proposal_lines(db, uid, pid, limit=LINES_PER_PROPOSAL)
        return [_short(r.get("text") or "") for r in rows if r.get("text")]
    except Exception:  # noqa: BLE001
        return []


async def card(panel_id: str, member: str) -> Optional[dict]:
    """The member's notifications card for ``panel_id`` (a ``voice_card`` frame), or None when there is nothing to show."""
    uid = _account(member)
    if not enabled() or uid is None or not panel_id:
        return None
    reg: dict = {}
    items: list = []
    for n in await unread(uid, limit=50):
        if n["type"] == WEARER_TYPE:
            props = await _open_proposals(uid, n)
            if not props:                              # decided elsewhere (the CLI) or expired: nothing left to ask
                await mark_read(uid, n["id"], "stale")
                continue
            for p in props[:MAX_PROPOSALS]:
                key = f"w{len(reg) + 1}"
                reg[key] = {"type": "wearer", "ref": str(p["id"]), "nid": n["id"], "batch": list((n.get("data") or {}).get("proposals") or [])}
                said = await _proposal_lines(uid, str(p["id"]))
                meta = " / ".join(f'"{s}"' for s in said) if said else f"heard in {p.get('recordings', 0)} recordings"
                items.append({"id": key, "text": n["title"], "meta": meta,
                              "actions": [{"key": "me", "label": "That's me"}, {"key": "not_me", "label": "Not me"}]})
        else:
            key = f"n{len(reg) + 1}"
            reg[key] = {"type": "note", "ref": n["id"]}
            items.append({"id": key, "text": _short(n["title"], 80), "meta": _short(n["message"], 120),
                          "actions": [{"key": "done", "label": "Done"}]})
    if not items:
        return None
    more = len(items) - MAX_CARD                       # the cap holds AFTER a wearer notification expands into its proposals
    items = items[:MAX_CARD]
    reg = {it["id"]: reg[it["id"]] for it in items}    # a row not on the card cannot be acted on
    import panel_review_cards as prc

    tok = prc.mint(panel_id, uid, CARD_KIND, reg)
    return {"panel_id": panel_id, "member_id": uid, "kind": CARD_KIND, "private": True, "title": "Notifications", "subtitle": "",
            "items": items, "more": max(0, more), "more_label": f"And {more} more." if more > 0 else "", "empty": "No new notifications.",
            "card_token": tok, "endpoint": ENDPOINT}


async def push_card(panel_id: str, member: str) -> int:
    """Send the card to THAT panel's own channel only. 0 = nothing sent. Never raises."""
    try:
        frame = await card(panel_id, member)
        if not frame:
            return 0
        import voice_session

        return int(await voice_session.send_to_panel(panel_id, "voice_card", frame) or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("notifications: card not pushed (%s)", exc.__class__.__name__)
        return 0


_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}
ANNOUNCED: dict[str, set] = {}     # member -> notification ids already told (the orb press, the spoken tail): never told twice


def tail_key(member: str) -> str:
    """The ``app_settings`` row that persists the member's spoken-tail caps (last time, the day's count). Erased with the member."""
    return f"notification_tail:{member}"


def count_words(n: int) -> str:
    return "a notification" if n == 1 else f"{_WORDS.get(n, str(n))} notifications"


async def pull_addendum(user_id: str, channel: str, extra_ctx: Optional[dict], *, alone: bool, dry: bool = False) -> str:
    """The sentence a "what's up?" pull (the orb press) adds when the turn's member has unread notifications, after the card has gone
    to that panel. Voice on a panel only, the turn's own member only (``panel_review_cards._member``). "" otherwise. Never raises."""
    try:
        ctx = extra_ctx or {}
        panel_id = str(ctx.get("panel_id") or "")
        if not enabled() or dry or channel != "voice" or not panel_id:
            return ""                              # a dry run (a replayed pull) says nothing about notifications at all
        import panel_review_cards as prc

        member = prc._member(user_id, channel, ctx)
        if not member:
            return ""
        await refresh(member)
        rows = await unread(member, limit=50)
        if not rows or not await push_card(panel_id, member):
            return ""
        ANNOUNCED.setdefault(member, set()).update(r["id"] for r in rows)
        return f"You have {count_words(len(rows))} on the screen." if alone else f"And {count_words(len(rows))} on the screen."
    except Exception as exc:  # noqa: BLE001
        logger.debug("notifications: pull addendum skipped (%s)", exc.__class__.__name__)
        return ""


class ActError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


async def act(member: str, item: dict, action: str) -> dict:
    """A tap on one row of the member's card. Raises ``ActError`` (400 unknown action, 409 already decided)."""
    if item.get("type") == "note":
        if action != "done":
            raise ActError(400, "unknown action")
        if not await mark_read(member, item["ref"], "done"):
            raise ActError(409, "already decided")
        return {"ok": True}
    if item.get("type") == "wearer":
        if action not in ("me", "not_me"):
            raise ActError(400, "unknown action")
        import omi_repass

        async with _db() as db:
            if action == "me":                         # confirm_wearer answers a dict that is always truthy: read its status
                res = await omi_repass.confirm_wearer(db, member, item["ref"])
                status = str((res or {}).get("status") or "")
                if status == "not_eligible":
                    raise ActError(403, "not available")
                changed = status == "confirmed"
            else:
                changed = await omi_repass.reject_wearer(db, member, item["ref"])
        if not changed:
            raise ActError(409, "already decided")
        if not await _open_proposals(member, {"data": {"proposals": item.get("batch") or []}}):
            await mark_read(member, item["nid"], "confirmed" if action == "me" else "rejected")
        logger.info("NOTIFY act=%s type=%s", action, WEARER_TYPE)
        return {"ok": True, "note": "Thanks, I'll remember your voice." if action == "me" else "Okay, that wasn't you."}
    raise ActError(404, "not on this card")


# -- forget ---------------------------------------------------------------------------------------------------------------------------
async def forget_user(user_id: str) -> int:
    """Right to be forgotten (``MemoryService.delete_user``): EVERY notification row of the member goes, whoever wrote it, whatever the flag
    says. Takes the member's erase gate, so a create already in flight lands before and is removed, and one scheduled before is dropped.
    Raises on a DB error (the caller fails closed)."""
    uid = str(user_id or "").strip()
    if not uid:
        return 0
    try:
        async with erase_gate.erasing(uid):
            async with _db() as db:
                cur = await db.execute("DELETE FROM notifications WHERE user_id = ?", (uid,))
                n = int(getattr(cur, "rowcount", 0) or 0)
                await db.commit()
                try:
                    await db.execute("DELETE FROM app_settings WHERE key = ?", (tail_key(uid),))
                    await db.commit()
                except Exception as exc:  # noqa: BLE001 - the tail-state row fails closed too; only "no such table / no pool" is nothing
                    from conversation_notes import _no_store

                    if not _no_store(exc):
                        raise
    except Exception as exc:  # noqa: BLE001
        from conversation_notes import _no_store       # the one "nothing stored to erase" rule (a lost pool still raises)

        if _no_store(exc):
            return 0
        logger.error("NOTIFY forget failed (%s): rows NOT erased", exc.__class__.__name__)
        raise
    ANNOUNCED.pop(uid, None)
    _REFRESHED.pop(uid, None)
    logger.info("NOTIFY forget rows=%d", n)
    return n


def reset() -> None:
    _REFRESHED.clear()
    _HOUSEHOLD_REFRESHED[0] = -REFRESH_EVERY_S
    ANNOUNCED.clear()
