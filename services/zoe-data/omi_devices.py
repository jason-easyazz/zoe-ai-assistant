"""Which household member owns which Omi pendant (table ``omi_devices``, migration 0047; flag ``ZOE_OMI_DEVICE_OWNERS``, default OFF).

THE RULE this module exists to hold: **a recording is attributed to the owner the REGISTRY names for the pendant that uploaded it -
never to whoever is signed in on the panel, never to a configured default, never to "some admin".** The Pi syncs whenever a pendant is
in range of it, whoever is (or is not) logged in; the device token authenticates the PANEL, never a person; and a pendant nobody owns is
refused (409 ``device_not_registered``) so the Pi keeps the audio until an admin assigns it.

  device_id    the 16-hex hash the Pi derives from the BLE address (``omi_sync.uploader.device_hash`` == ``panel_bt.model.device_id``;
               one identity, so the id Settings > Bluetooth shows is the id uploads carry). The address never reaches zoe-data.
  assign       admin-only (``routers/omi_recordings.py``), audited FAIL-CLOSED (``ZOE_OMI_AUDIT_PATH``): no audit line, no change.
  reassign     moves ONLY the registry row. ``omi_recordings`` rows keep the ``user_id`` they were stored under, so past recordings stay
               with whoever owned the pendant when they were uploaded. (Ownership is resolved at UPLOAD time: audio still on the pendant
               or in the Pi's spool when the owner changes goes to the new owner - hence the UI's "let it sync first" warning.)
  seed         ``ZOE_OMI_OWNER_USER_ID`` is only a ONE-TIME seed: if it is set and exactly one pendant has ever uploaded under that owner,
               that pendant is registered to them, once (an existing row of ANY state blocks a re-seed, so an unassign or an erase is
               never undone by a restart). With any other history, registration is explicit.
  erase        ``forget_user`` (called from ``omi_recordings.forget_user`` inside the member's erase gate, itself part of
               ``MemoryService.delete_user``): the member's registrations are UNASSIGNED (owner, label cleared) and their name is removed
               from ``registered_by``. The row stays as an unassigned device so the seed cannot resurrect them.

Every database access is fail-soft toward "nobody owns it": an unreadable registry refuses uploads (custody stays on the Pi); it never
guesses an owner.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import user_erase_gate as erase_gate
from typed_env import env_bool, env_str

logger = logging.getLogger(__name__)

#: the canonical device id (see the module docstring)
DEVICE_ID_RX = re.compile(r"^[0-9a-f]{16}$")
LABEL_MAX = 60
_GUEST_ROLES = ("guest",)

_seeded = False
_device_locks: dict[str, asyncio.Lock] = {}


def device_lock(device_id: str) -> asyncio.Lock:
    """The per-pendant lock shared by every writer that changes WHO owns a pendant (``assign`` / ``unassign``) and every writer that
    stores something under the owner it just resolved (``omi_health.record_report`` / ``sweep``). Without it a heartbeat checks the
    owner, a reassignment lands, and the heartbeat then saves the previous owner's id on the pendant's row. Lock order is always erase
    gate (the member) -> device lock; nothing takes the gate while holding this. Keys are validated 16-hex ids, so the map is bounded by
    the number of pendants."""
    return _device_locks.setdefault(device_id, asyncio.Lock())


class DeviceError(Exception):
    """A refused registry operation: ``status`` (HTTP), ``code`` (stable), a sentence for the UI."""

    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(code)
        self.status, self.code, self.message = status, code, message or code


def enabled() -> bool:
    """``ZOE_OMI_DEVICE_OWNERS`` - default OFF. Off = the legacy single owner (``ZOE_OMI_OWNER_USER_ID``) and owner-only views."""
    return env_bool("ZOE_OMI_DEVICE_OWNERS", default=False)


def seed_owner_user_id() -> str:
    """``ZOE_OMI_OWNER_USER_ID`` - with the registry on this is ONLY the one-time seed (see the module docstring)."""
    return env_str("ZOE_OMI_OWNER_USER_ID", "").strip()


def audit_path() -> Path:
    raw = env_str("ZOE_OMI_AUDIT_PATH", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".zoe" / "omi-audit.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def audit(event: dict) -> bool:
    """Append one JSON line (0600). False = it could not be written. No addresses, tokens, audio or free text beyond the bounded label."""
    rec = {"ts": _now(), **event}
    try:
        p = audit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        return True
    except OSError as exc:
        logger.error("omi audit log not writable: %s", type(exc).__name__)
        return False


async def _fetch(db, sql: str, params: tuple = ()) -> list:
    cur = await db.execute(sql, params)
    return list(await cur.fetchall())


def _public(row: Any) -> dict:
    return {"device_id": row["device_id"], "user_id": row["user_id"] or None, "label": row["label"], "active": bool(row["active"]),
            "registered_by": row["registered_by"] or None, "registered_at": row["registered_at"], "updated_at": row["updated_at"] or None}


def clean_label(label: Optional[str]) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(label or "")).strip()[:LABEL_MAX]


# --- reads -----------------------------------------------------------------------------------------------------------------------
async def owner_of(device_id: str) -> str:
    """The member who owns the pendant NOW, or '' (unknown device, unassigned, inactive, registry unreadable). Never a guess."""
    if not DEVICE_ID_RX.match(device_id or ""):
        return ""
    try:
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            rows = await _fetch(db, "SELECT user_id, active FROM omi_devices WHERE device_id = ?", (device_id,))
    except Exception as exc:  # noqa: BLE001
        logger.warning("omi_devices: registry unreadable, refusing the upload (%s)", type(exc).__name__)
        return ""
    return rows[0]["user_id"] if rows and rows[0]["active"] and rows[0]["user_id"] else ""


async def list_devices() -> list[dict]:
    """The registry, metadata only: NO recordings, counts or content - an admin manages who owns a pendant, not what it heard."""
    from db_compat import get_compat_db
    async with get_compat_db() as db:
        rows = await _fetch(db, "SELECT * FROM omi_devices ORDER BY registered_at, device_id")
    return [_public(r) for r in rows]


async def members() -> list[dict]:
    """Household members who may own a pendant: every user that exists and is not a guest."""
    from db_compat import get_compat_db
    async with get_compat_db() as db:
        rows = await _fetch(db, "SELECT id, name, role FROM users ORDER BY name, id")
    return [{"user_id": r["id"], "name": r["name"]} for r in rows if r["role"] not in _GUEST_ROLES and r["id"] != "guest"]


# --- the one-time seed -----------------------------------------------------------------------------------------------------------
async def ensure_seeded() -> bool:
    """Register the single pendant that has ever uploaded to the configured legacy owner, ONCE. Idempotent; returns True if a row was
    created by this call. Cheap after the first successful pass (a module flag). A failure leaves the flag unset so the next call retries."""
    global _seeded
    if _seeded:
        return False
    owner = seed_owner_user_id()
    if not owner:
        _seeded = True
        return False
    try:
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            seen = await _fetch(db, "SELECT DISTINCT device_id, user_id FROM omi_recordings")
            devices = {r["device_id"] for r in seen}
            owners = {r["user_id"] for r in seen}
            created = False
            if len(devices) == 1 and owners == {owner} and DEVICE_ID_RX.match(next(iter(devices))):
                (dev,) = devices
                before = await _fetch(db, "SELECT device_id FROM omi_devices WHERE device_id = ?", (dev,))
                if not before:
                    now = _now()
                    await db.execute("INSERT INTO omi_devices (device_id, user_id, label, registered_by, registered_at, updated_at, active) "
                                     "VALUES (?, ?, '', 'seed', ?, ?, 1) ON CONFLICT (device_id) DO NOTHING", (dev, owner, now, now))
                    await db.commit()
                    created = True
        _seeded = True
        if created:
            audit({"action": "seed", "device_id": dev, "to_user": owner, "actor": "seed"})
            logger.info("omi_devices: seeded the one known pendant to the configured owner (ZOE_OMI_OWNER_USER_ID is now inert)")
        return created
    except Exception as exc:  # noqa: BLE001
        logger.warning("omi_devices: seed not run (%s)", type(exc).__name__)
        return False


def reset_for_tests() -> None:
    global _seeded
    _seeded = False
    _device_locks.clear()


# --- writes (admin-only at the route; audited fail-closed here) ---------------------------------------------------------------------
async def assign(device_id: str, user_id: str, *, label: Optional[str], actor: dict) -> dict:
    """Assign (or reassign) a pendant to a household member. Moves NO recordings. Raises ``DeviceError``."""
    if not DEVICE_ID_RX.match(device_id or ""):
        raise DeviceError(400, "bad_device_id", "That is not a pendant id.")
    from db_compat import get_compat_db
    lbl = clean_label(label)
    async with erase_gate.writing(user_id, erase_gate.generation(user_id)) as live:
        if not live:
            raise DeviceError(409, "member_being_erased", "That member is being erased.")
        async with device_lock(device_id), get_compat_db() as db:
            urows = await _fetch(db, "SELECT id, role FROM users WHERE id = ?", (user_id,))
            if not urows or urows[0]["role"] in _GUEST_ROLES or user_id == "guest":
                raise DeviceError(400, "not_a_member", "Pick a household member (guests cannot own a pendant).")
            before = await _fetch(db, "SELECT * FROM omi_devices WHERE device_id = ?", (device_id,))
            prev = (before[0]["user_id"] if before and before[0]["active"] else "") or ""
            action = "assign" if not prev else ("reassign" if prev != user_id else "relabel")
            if not audit({"phase": "requested", "action": action, "device_id": device_id, "from_user": prev or None, "to_user": user_id,
                          "actor": actor.get("user_id"), "role": actor.get("role")}):
                raise DeviceError(503, "audit_unavailable", "The audit log is unavailable, so nothing was changed.")
            now = _now()
            if before:
                keep_reg = before[0]["registered_at"] if (prev == user_id) else now
                await db.execute("UPDATE omi_devices SET user_id = ?, label = ?, registered_by = ?, registered_at = ?, updated_at = ?, "
                                 "active = 1 WHERE device_id = ?", (user_id, lbl, str(actor.get("user_id") or ""), keep_reg, now, device_id))
            else:
                await db.execute("INSERT INTO omi_devices (device_id, user_id, label, registered_by, registered_at, updated_at, active) "
                                 "VALUES (?, ?, ?, ?, ?, ?, 1)", (device_id, user_id, lbl, str(actor.get("user_id") or ""), now, now))
            await db.commit()
            row = (await _fetch(db, "SELECT * FROM omi_devices WHERE device_id = ?", (device_id,)))[0]
    audit({"phase": "finished", "action": action, "device_id": device_id, "to_user": user_id, "actor": actor.get("user_id"), "ok": True})
    out = _public(row)
    out["previous_user_id"] = prev or None
    return out


async def unassign(device_id: str, *, actor: dict) -> bool:
    """Unassign: the pendant keeps its row (so the seed never re-runs) but nobody owns it; its next upload is refused and kept on the Pi."""
    if not DEVICE_ID_RX.match(device_id or ""):
        raise DeviceError(400, "bad_device_id", "That is not a pendant id.")
    from db_compat import get_compat_db
    async with device_lock(device_id), get_compat_db() as db:
        before = await _fetch(db, "SELECT * FROM omi_devices WHERE device_id = ?", (device_id,))
        if not before:
            return False
        prev = before[0]["user_id"] if before[0]["active"] else ""
        if not audit({"phase": "requested", "action": "unassign", "device_id": device_id, "from_user": prev or None,
                      "actor": actor.get("user_id"), "role": actor.get("role")}):
            raise DeviceError(503, "audit_unavailable", "The audit log is unavailable, so nothing was changed.")
        await db.execute("UPDATE omi_devices SET user_id = '', label = '', active = 0, updated_at = ? WHERE device_id = ?", (_now(), device_id))
        await db.commit()
    audit({"phase": "finished", "action": "unassign", "device_id": device_id, "actor": actor.get("user_id"), "ok": True})
    return True


async def forget_user(db, user_id: str) -> int:
    """Erase cascade, inside the member's erase gate and the caller's DB handle: unassign every pendant they own and drop their id from
    ``registered_by``. Returns the number of registrations unassigned. The caller commits."""
    rows = await _fetch(db, "SELECT device_id FROM omi_devices WHERE user_id = ?", (user_id,))
    await db.execute("UPDATE omi_devices SET user_id = '', label = '', active = 0, updated_at = ? WHERE user_id = ?", (_now(), user_id))
    await db.execute("UPDATE omi_devices SET registered_by = '' WHERE registered_by = ?", (user_id,))
    for r in rows:
        audit({"phase": "finished", "action": "unassign", "device_id": r["device_id"], "from_user": None, "actor": "erase", "ok": True})
    return len(rows)
