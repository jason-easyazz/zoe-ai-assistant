"""first_boot_probe - read ``first_boot.Facts`` from the tables and settings that already hold each answer (unwired).

Every item in ``first_boot.ITEMS`` names its ``source``; this module reads exactly that and nothing else, so first boot can never
disagree with the system it describes. Each read is on its own: a failed read leaves THAT field ``None`` (unknown), the rest intact
(the ``enrollment_state.get_state`` rule). Counts and booleans only: no name, no secret, no value of a setting is ever returned.

Writes: one, ``defer`` - the owner said "later" to an item. It REUSES ``enrollment_offers`` (migration 0046: one row per member and
``modality``, with ``declined_at`` / ``declined_until``) under ``modality = 'setup:<item id>'``: no new table, no migration. Every
existing reader of that table filters on ``modality = 'voice'``, so a setup row is invisible to the enrolment offer policy.

Nothing calls this module yet (wiring notes: docs/architecture/first-boot.md).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import first_boot as fb

logger = logging.getLogger(__name__)

SETUP_PREFIX = "setup:"
DEFAULT_DEFER_DAYS = 7
_UNSET_PASSWORDS = (None, "", "SETUP_REQUIRED")
_TS = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> datetime:
    return datetime.now(timezone.utc)


@asynccontextmanager
async def _conn(db=None):
    if db is not None:
        yield db
        return
    from db_compat import get_compat_db

    async with get_compat_db() as conn:
        yield conn


async def _rows(db, sql: str, params: tuple = ()) -> list:
    async with db.execute(sql, params) as cur:
        return list(await cur.fetchall())


async def _count(db, sql: str, params: tuple = ()) -> int:
    rows = await _rows(db, sql, params)
    return int(rows[0][0] or 0) if rows else 0


async def _safe(name: str, coro):
    """Run one read; ``None`` (unknown) when it fails, never ``False``."""
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 - unknown, not "no"
        logger.warning("first_boot_probe: %s read failed: %s", name, type(exc).__name__)
        return None


# ── reads (one per item) ──────────────────────────────────────────────────────────────────────────────────────────────────

async def _owner(db) -> tuple:
    """(owner id or None, password set?) - the first-created active admin is the owner."""
    from auth import is_admin_role

    rows = await _rows(db, "SELECT user_id, role, password_hash FROM auth_users WHERE COALESCE(is_active, 1) = 1 "
                           "ORDER BY created_at, user_id")
    for row in rows:
        if is_admin_role(str(row[1] or "").strip().lower()):
            return str(row[0]), row[2] not in _UNSET_PASSWORDS
    return None, False


async def _panel(db, panel_id: Optional[str], now: datetime) -> tuple:
    """(paired?, named and placed?) for ``panel_id``, or for every paired panel when none is named."""
    from routers.panel_auth import parse_token_expiry

    sql = ("SELECT p.panel_id, p.name, p.room_id, t.expires_at FROM panels p "
           "JOIN device_tokens t ON t.panel_id = p.panel_id "
           "WHERE COALESCE(p.is_active, 1) = 1 AND COALESCE(t.revoked, 0) = 0")
    params: tuple = ()
    if panel_id:
        sql += " AND p.panel_id = ?"
        params = (panel_id,)
    rows = []
    for row in await _rows(db, sql, params):
        try:
            if row[3] and parse_token_expiry(row[3]) <= now:
                continue
        except (TypeError, ValueError):
            continue                                      # auth refuses an unreadable expiry too
        rows.append(row)
    if not rows:
        return False, False
    for pid, name, room_id in ((str(r[0]), str(r[1] or "").strip(), r[2]) for r in rows):
        default = not name or name == pid or name.lower().startswith("panel-")
        if default or not room_id or not await _count(db, "SELECT COUNT(*) FROM rooms WHERE id = ?", (str(room_id),)):
            return True, False
    return True, True


async def _time_and_place() -> bool:
    from zoneinfo import ZoneInfo

    import household_locale

    zone = (os.environ.get("ZOE_TIMEZONE") or "").strip()
    if not zone:
        return False                                      # the code default zone is an assumption, not a household's answer
    ZoneInfo(zone)                                        # an invalid zone raises: unknown, never "set"
    return bool(household_locale.country())


async def _phone(db, owner: str) -> Optional[bool]:
    """True when ANY channel is linked; None only when every read failed."""
    import user_prefs

    async def push():
        return await _count(db, "SELECT COUNT(*) FROM push_subscriptions WHERE user_id = ?", (owner,)) > 0

    async def telegram():
        return bool((await user_prefs.read_prefs(db, owner)).get("telegram_id"))

    async def app():
        return await _count(db, "SELECT COUNT(*) FROM phone_devices WHERE user_id = ? AND COALESCE(revoked, 0) = 0",
                            (owner,)) > 0

    seen = [await _safe("phone." + n, f()) for n, f in (("push", push), ("telegram", telegram), ("app", app))]
    if any(seen):
        return True
    return None if all(s is None for s in seen) else False


async def _first_win(db, owner: str) -> bool:
    n = await _count(db, "SELECT COUNT(*) FROM reminders WHERE user_id = ? AND COALESCE(is_active, 1) = 1", (owner,))
    return n > 0 or await _count(db, "SELECT COUNT(*) FROM lists WHERE user_id = ? AND COALESCE(deleted, 0) = 0", (owner,)) > 0


async def _members(db, owner: str) -> tuple:
    """(another real member exists?, child_marked: True | False | "n/a")."""
    import manner_block
    from user_filters import GUEST_USERS, is_synthetic_user

    rows = await _rows(db, "SELECT a.user_id, a.role, m.minor FROM auth_users a LEFT JOIN member_modes m ON m.user_id = a.user_id "
                           "WHERE COALESCE(a.is_active, 1) = 1")
    real = [r for r in rows if str(r[0]) not in GUEST_USERS and not str(r[0]).startswith("guest-") and not is_synthetic_user(str(r[0]))]
    others = any(str(r[0]) != owner for r in real)
    children = [r for r in real if str(r[1] or "").strip().lower() in manner_block.CHILD_ROLES or int(r[2] or 0) == 1]
    if not children:
        return others, fb.NA
    return others, all(int(r[2] or 0) == 1 for r in children)


async def _account(db, owner: str, kind: str) -> bool:
    return await _count(db, "SELECT COUNT(*) FROM connected_accounts WHERE user_id = ? AND kind = ? AND status = 'active'",
                        (owner, kind)) > 0


async def _deferred(db, owner: str, now: datetime) -> frozenset:
    import enrollment_state

    rows = await _rows(db, "SELECT modality, declined_until FROM enrollment_offers WHERE user_id = ? AND modality LIKE ?",
                       (owner, SETUP_PREFIX + "%"))
    out = set()
    for modality, until in rows:
        when = enrollment_state.parse(until)
        if when is not None and when > now:
            out.add(str(modality)[len(SETUP_PREFIX):])
    return frozenset(out)


def _backup_configured(config_dir: Optional[str] = None) -> bool:
    base = config_dir or os.environ.get("ZOE_OFFBOX_CONFIG_DIR") or str(Path.home() / ".config" / "zoe-backup")
    return (Path(base) / "offbox.env").is_file()


# ── the snapshot ──────────────────────────────────────────────────────────────────────────────────────────────────────────

async def _owner_pin(db, owner: str) -> bool:
    """An active passcode zoe-auth would ACCEPT: not past its ``expires_at``, judged exactly as ``core/passcode.py::verify_passcode``
    does (naive local ``datetime.now()`` against ``fromisoformat``). An unreadable expiry is not a usable PIN."""
    for (expires_at,) in await _rows(db, "SELECT expires_at FROM passcodes WHERE user_id = ? AND is_active = 1", (owner,)):
        if not expires_at:
            return True
        try:
            if datetime.now() <= datetime.fromisoformat(str(expires_at)):
                return True
        except ValueError:
            continue
    return False


async def gather(*, panel_id: Optional[str] = None, db=None, now: Optional[datetime] = None,
                 backup_dir: Optional[str] = None) -> fb.Facts:
    """The unit's ``Facts``. Items that need an owner are ``False`` (and so ``waiting``) while there is none."""
    import memory_forgotten

    now = now or _now()
    try:
        async with _conn(db) as conn:
            got = await _safe("owner", _owner(conn))
            owner, has_pw = got if got is not None else (None, None)
            paired, named = (await _safe("panel", _panel(conn, panel_id, now))) or (None, None)
            members = child = pin = voice = phone = win = cal = music = None
            deferred: frozenset = frozenset()
            if owner:
                pin = await _safe("pin", _owner_pin(conn, owner))
                voice = await _safe("voice", _voice(owner, conn))
                phone = await _phone(conn, owner)
                win = await _safe("first_win", _first_win(conn, owner))
                cal = await _safe("calendar", _account(conn, owner, "calendar"))
                music = await _safe("music", _account(conn, owner, "music"))
                members, child = (await _safe("members", _members(conn, owner))) or (None, None)
                deferred = (await _safe("deferred", _deferred(conn, owner, now))) or frozenset()
            elif got is not None:
                pin = voice = phone = win = cal = music = members = False
                child = fb.NA
    except Exception as exc:  # noqa: BLE001 - no connection at all: everything that needs the database is unknown
        logger.warning("first_boot_probe: no connection: %s", type(exc).__name__)
        owner = has_pw = paired = named = members = child = pin = voice = phone = win = cal = music = None
        deferred = frozenset()
    return fb.Facts(
        owner_account=has_pw, forget_secret=memory_forgotten.configured(), panel_paired=paired, panel_named=named,
        time_and_place=await _safe("time_and_place", _time_and_place()), owner_pin=pin, owner_voice=voice,
        phone_linked=phone, backup_target=_backup_configured(backup_dir), first_win=win, more_members=members,
        child_marked=child, calendar=cal, music=music, deferred=deferred)


async def _voice(owner: str, db):
    import enrollment_state

    return (await enrollment_state.get_state(owner, db=db)).voice_enrolled


async def defer(owner: str, item_id: str, *, days: int = DEFAULT_DEFER_DAYS, now: Optional[datetime] = None, db=None) -> bool:
    """The owner said "later" to ``item_id``: remember it for ``days``. Upsert into ``enrollment_offers``; never raises."""
    if item_id not in fb.BY_ID or not owner:
        return False
    now = now or _now()
    stamp, until = now.strftime(_TS), (now + timedelta(days=max(1, days))).strftime(_TS)
    try:
        async with _conn(db) as conn:
            await conn.execute(
                "INSERT INTO enrollment_offers (user_id, modality, declined_at, declined_until, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (user_id, modality) DO UPDATE SET declined_at = excluded.declined_at, "
                "declined_until = excluded.declined_until, updated_at = excluded.updated_at",
                (owner, SETUP_PREFIX + item_id, stamp, until, stamp))
            await conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("first_boot_probe: defer failed: %s", type(exc).__name__)
        return False
