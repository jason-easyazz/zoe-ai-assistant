"""Pendant health: the latest numbers per pendant, and the four ways loss used to be silent (gap audit P0-2).

The panel Pi's ``omi-sync`` sends a small NUMBERS-ONLY report (``POST /api/omi/device-status``, device token, see
the omi router) after each visit and at least every ~15 minutes while the pendant is away. This module keeps the LATEST row per
pendant (``omi_device_health``, migration 0056), decides which warnings are active (``evaluate``, pure), and raises each one ONCE as a bell
notification when it becomes active. It stores no audio, transcript, address or free text.

WARNINGS (constants below; each fires AND clears from the data, nothing is acknowledged by hand):

  no_sync        the last clean sync is older than 36 h and the Pi is reporting (so the pendant is known to be away, or in range but not
                 syncing). The ring holds about 34 h at its ceiling, so past 36 h audio is probably already being overwritten.
  ring_fill      the ring is more than 75 % full after the last drain (it holds audio Zoe has not confirmed).
  overwrite      the pendant's overwrite counter rose in the last 72 h: audio was lost. Counted from the absolute counter, so a repeated or
                 lost report neither double counts nor misses an overwrite. The first report is a baseline (history before Zoe watched is not
                 news).
  battery_low    the battery level is below 20 % (from the standard BLE Battery Service).
  bridge_silent  the Pi has sent nothing for 3 h: the sync service or the panel is down, so "away" cannot be known. Suppresses no_sync.

NEVER SPEAKS. A warning is (1) a ``notifications`` row (idempotent by pendant + kind + episode) (the bell; the same path as the "N recordings synced" notice), (2) a field on
``GET /api/omi/health`` for the Memories/Pendant UI, and (3) at most ONE line in the member's first-turn-of-the-day brief
(``brief_first_turn`` via ``morning_items``), which Zoe mentions naturally if she speaks to them at all. Per
``project_w2_zoe_speaks_first``: no unprompted speech.

WHO MAY READ: the pendant's CURRENT owner only (the registry, or the configured owner in legacy mode), re-checked at read time. A device
token reports; it never reads. Kill switch ``ZOE_OMI_HEALTH=0`` stops the warnings (notifications, the brief line); the report endpoint and
the read stay (read-only numbers).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from typed_env import env_bool

logger = logging.getLogger(__name__)

NO_SYNC_H = 36.0              # no clean sync for this long while the Pi is reporting
RING_FILL_PCT = 75.0          # ring fill above this
BATTERY_LOW_PCT = 20          # battery below this
OVERWRITE_WINDOW_H = 72.0     # an overwrite keeps the warning up this long
BRIDGE_SILENT_H = 3.0         # the Pi has said nothing for this long
RENOTIFY_H = 6.0              # a warning that clears and re-arms inside this window does not produce a second bell
SECONDS_PER_RECORD = 0.11     # mirrors omi_sync.recordings.NOMINAL_RECORD_S (a 444-byte ring record is about this much audio)
BELL_TITLE = "Pendant"
SWEEP_S = 600.0               # the periodic re-evaluation (time-based warnings arise with no report at all)

KINDS = ("overwrite", "no_sync", "ring_fill", "battery_low", "bridge_silent")      # most to least important (the brief mentions the first)
_FIRMWARE_RX = re.compile(r"^[0-9A-Za-z._+-]{0,32}$")
DEVICE_ID_RX = re.compile(r"^[0-9a-f]{16}$")


def enabled() -> bool:
    """``ZOE_OMI_HEALTH`` - default ON (read-only numbers). Off = no warnings (no bell, no brief line); reports are still stored."""
    return env_bool("ZOE_OMI_HEALTH", default=True)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- time helpers ----------------------------------------------------------------------------------------------------------------------
def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hours(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds() / 3600.0


# --- the report ---------------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Report:
    """A validated heartbeat. Built by the router's pydantic model; every field is a number, a bool or a short token."""
    reachable: bool
    consecutive_misses: int = 0
    last_sync_age_s: Optional[int] = None
    battery_pct: Optional[int] = None
    firmware: Optional[str] = None
    ring_fill_pct: Optional[float] = None
    ring_fill_start_pct: Optional[float] = None
    ring_stored_bytes: Optional[int] = None
    ring_capacity_bytes: Optional[int] = None
    dropped_total: Optional[int] = None


def overwritten_delta(previous_total: Optional[int], total: Optional[int]) -> int:
    """Records the pendant overwrote between two readings of its CUMULATIVE counter. No reading, or the first one (no baseline) = 0. A
    counter that went DOWN means the pendant restarted its count, so the new value is what it has overwritten since."""
    if total is None or previous_total is None:
        return 0
    return total - previous_total if total >= previous_total else total


def merge(prev: Optional[dict], rep: Report, now: datetime) -> dict:
    """The row after applying a report to the previous row (or None). Pure. An unreachable report only updates the attempt facts: what we
    last KNEW about the battery and the ring stays, with its own timestamp."""
    row = dict(prev or {})
    stamp = _iso(now)
    row.setdefault("first_reported_at", stamp)
    row["reported_at"] = stamp
    row["reachable"] = 1 if rep.reachable else 0
    row["consecutive_misses"] = max(0, int(rep.consecutive_misses))
    if rep.last_sync_age_s is not None:
        row["last_sync_at"] = _iso(now - timedelta(seconds=max(0, int(rep.last_sync_age_s))))
    if rep.reachable:
        row["last_seen_at"] = stamp
        if rep.battery_pct is not None:
            row["battery_pct"], row["battery_at"] = int(rep.battery_pct), stamp
        if rep.firmware:
            row["firmware"] = rep.firmware
        if rep.ring_fill_pct is not None:
            row["ring_fill_pct"] = float(rep.ring_fill_pct)
            row["ring_fill_start_pct"] = None if rep.ring_fill_start_pct is None else float(rep.ring_fill_start_pct)
            row["ring_stored_bytes"], row["ring_capacity_bytes"] = rep.ring_stored_bytes, rep.ring_capacity_bytes
            row["ring_at"] = stamp
        if rep.dropped_total is not None:
            delta = overwritten_delta(row.get("dropped_total"), rep.dropped_total)
            row["dropped_total"] = int(rep.dropped_total)
            if delta > 0:
                row["overwritten_records_total"] = int(row.get("overwritten_records_total") or 0) + delta
                row["last_overwritten_records"] = delta
                row["last_overwrite_at"] = stamp
    row.setdefault("overwritten_records_total", 0)
    row.setdefault("last_overwritten_records", 0)
    return row


# --- warnings --------------------------------------------------------------------------------------------------------------------------------
def _span(hours: float) -> str:
    return f"{int(hours)} hours" if hours < 48 else f"{int(hours // 24)} days"


def evaluate(row: dict, now: datetime) -> list[dict]:
    """The warnings active for one stored row at ``now``, most important first. Pure. Each is ``{"kind", "text"}``; the text is counts and
    durations only."""
    out: dict[str, str] = {}
    reported = _parse(row.get("reported_at"))
    silent = reported is None or _hours(now, reported) > BRIDGE_SILENT_H
    last_overwrite = _parse(row.get("last_overwrite_at"))
    if last_overwrite is not None and _hours(now, last_overwrite) < OVERWRITE_WINDOW_H:
        minutes = max(1, round(int(row.get("last_overwritten_records") or 0) * SECONDS_PER_RECORD / 60.0))
        out["overwrite"] = f"Your pendant ran out of room and overwrote about {minutes} minutes of audio."
    if silent:
        gap = "a while" if reported is None else _span(_hours(now, reported))
        out["bridge_silent"] = (f"Zoe has not heard from the panel's pendant sync for {gap}, so she cannot tell whether your pendant "
                                "is being saved.")
    else:
        ref = _parse(row.get("last_sync_at")) or _parse(row.get("first_reported_at"))
        if ref is not None and _hours(now, ref) > NO_SYNC_H:
            away = not row.get("reachable")
            out["no_sync"] = (f"Your pendant has not synced for {_span(_hours(now, ref))}"
                              + (" and is out of range" if away else " although it is in range")
                              + "; it overwrites its oldest audio after about a day and a half.")
    fill = row.get("ring_fill_pct")
    if fill is not None and float(fill) > RING_FILL_PCT:
        out["ring_fill"] = f"Your pendant's storage is {int(round(float(fill)))}% full of audio Zoe has not saved yet."
    batt = row.get("battery_pct")
    if batt is not None and int(batt) < BATTERY_LOW_PCT:
        out["battery_low"] = f"Your pendant's battery is at {int(batt)}%."
    return [{"kind": k, "text": out[k]} for k in KINDS if k in out]


def public_view(row: dict, now: datetime, *, label: str = "") -> dict:
    """What ``GET /api/omi/health`` returns for one pendant: numbers, timestamps and the live warnings. No ids of people, no content.
    With the kill switch (``ZOE_OMI_HEALTH=0``) there are NO warnings and ``status`` is ``warnings_off`` - not ``ok``, which would read
    as "checked and healthy" while nothing is being checked. The numbers stay."""
    warnings = evaluate(row, now) if enabled() else []
    state = _state(row)
    for w in warnings:
        w["since"] = (state.get(w["kind"]) or {}).get("since")
    return {
        "device_id": row["device_id"], "label": label or None,
        "status": ("attention" if warnings else "ok") if enabled() else "warnings_off",
        "reachable": bool(row.get("reachable")), "reported_at": row.get("reported_at"), "last_seen_at": row.get("last_seen_at"),
        "last_sync_at": row.get("last_sync_at"), "consecutive_misses": int(row.get("consecutive_misses") or 0),
        "battery": {"pct": row.get("battery_pct"), "at": row.get("battery_at")},
        "ring": {"fill_pct": row.get("ring_fill_pct"), "fill_at_connect_pct": row.get("ring_fill_start_pct"),
                 "stored_bytes": row.get("ring_stored_bytes"), "capacity_bytes": row.get("ring_capacity_bytes"), "at": row.get("ring_at")},
        "overwritten": {"records_total": int(row.get("overwritten_records_total") or 0),
                        "last_minutes": round(int(row.get("last_overwritten_records") or 0) * SECONDS_PER_RECORD / 60.0, 1),
                        "last_at": row.get("last_overwrite_at")},
        "firmware": row.get("firmware"), "warnings": warnings,
    }


def _state(row: dict) -> dict:
    try:
        doc = json.loads(row.get("warn_state") or "{}")
    except (TypeError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def transition(row: dict, active: list[dict], now: datetime) -> tuple[dict, list[dict]]:
    """``(new warn_state, warnings to notify now)``. A kind that becomes active gets ``since``; it is notified unless it was notified inside
    the re-notify window (a battery hovering at 19/21 % does not ring every cycle). A kind that is no longer active loses its ``since``
    but keeps ``notified_at`` for that window. Each notified warning carries an ``episode`` number (the reserved ``_episodes`` counter,
    never dropped): the bell's notification id is derived from (pendant, kind, episode), so ringing the same episode twice - a retry
    after a crash between the bell and the saved state - is the same row, never a second bell. Pure."""
    old = _state(row)
    stamp = _iso(now)
    episodes = {k: int(v) for k, v in (old.get("_episodes") or {}).items() if isinstance(v, (int, float))}
    live = {w["kind"]: w for w in active}
    new: dict[str, Any] = {}
    notify: list[dict] = []
    for kind, w in live.items():
        prev = old.get(kind) or {}
        since = prev.get("since") or stamp
        notified = prev.get("notified_at")
        was_active = bool(prev.get("since"))
        last = _parse(notified)
        if not was_active and (last is None or _hours(now, last) >= RENOTIFY_H):
            episodes[kind] = episodes.get(kind, 0) + 1
            notify.append(dict(w, episode=episodes[kind]))
            notified = stamp
        new[kind] = {"since": since, "notified_at": notified}
    for kind, prev in old.items():
        if kind.startswith("_") or kind in live or not isinstance(prev, dict):
            continue
        last = _parse(prev.get("notified_at"))
        if last is not None and _hours(now, last) < RENOTIFY_H:
            new[kind] = {"since": None, "notified_at": prev.get("notified_at")}
    if episodes:
        new["_episodes"] = episodes
    return new, notify


# --- storage ------------------------------------------------------------------------------------------------------------------------------------
_COLUMNS = ("user_id", "reported_at", "first_reported_at", "reachable", "last_seen_at", "last_sync_at", "battery_pct", "battery_at",
            "ring_fill_pct", "ring_fill_start_pct", "ring_stored_bytes", "ring_capacity_bytes", "ring_at", "dropped_total",
            "overwritten_records_total", "last_overwritten_records", "last_overwrite_at", "firmware", "consecutive_misses", "warn_state")


async def _get_row(db, device_id: str) -> Optional[dict]:
    cur = await db.execute("SELECT * FROM omi_device_health WHERE device_id = ?", (device_id,))
    rows = list(await cur.fetchall())
    return dict(rows[0]) if rows else None


async def _save(db, device_id: str, row: dict) -> None:
    cols = ("device_id",) + _COLUMNS
    values = (device_id,) + tuple(row.get(c) for c in _COLUMNS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS)
    await db.execute(f"INSERT INTO omi_device_health ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
                     f"ON CONFLICT (device_id) DO UPDATE SET {updates}", values)


def _rebaselined(prev: dict) -> dict:
    """The stored row of a pendant whose OWNER changed, with the previous owner's overwrite history and counters dropped. The pendant's
    cumulative ``dropped_total`` becomes a fresh baseline (the new owner's first report is a baseline, exactly like a first-ever one), so
    audio lost on the previous owner's watch is neither charged to the new owner nor counted as a delta against the old baseline, and
    the old "overwritten ... minutes" warning does not follow the pendant. Facts about the pendant itself (battery, ring, last sync,
    firmware) stay: they describe the hardware, not a person. Pure."""
    row = dict(prev)
    row.update(dropped_total=None, overwritten_records_total=0, last_overwritten_records=0, last_overwrite_at=None)
    return row


async def current_owner(device_id: str) -> str:
    """Who owns the pendant NOW: the registry in device-owners mode, else the configured legacy owner. '' = nobody (never a guess)."""
    import omi_devices

    if omi_devices.enabled():
        return await omi_devices.owner_of(device_id)
    return omi_devices.seed_owner_user_id()          # the legacy single owner: the same ZOE_OMI_OWNER_USER_ID the upload route uses


async def _label(device_id: str) -> str:
    try:
        import omi_devices

        if not omi_devices.enabled():
            return ""
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            cur = await db.execute("SELECT label FROM omi_devices WHERE device_id = ?", (device_id,))
            rows = list(await cur.fetchall())
        return str(rows[0]["label"] or "") if rows else ""
    except Exception:  # noqa: BLE001 - a label is decoration
        return ""


class NotTheOwner(Exception):
    """The report cannot be stored: the member was erased since the request was scheduled, or the pendant is no longer theirs."""


async def record_report(owner: str, device_id: str, rep: Report, *, now: Optional[datetime] = None,
                        scheduled_at: Optional[int] = None) -> list[dict]:
    """Apply one heartbeat for ``device_id`` (whose owner the caller resolved from the registry). Returns the active warnings.

    * ERASE GATE. The write takes the member's ``user_erase_gate`` lock before any database connection, like every background writer:
      an erase of the member queues behind it (and removes what it wrote) or runs first, in which case ``scheduled_at`` (the erase
      generation snapshotted when the request arrived) is stale and nothing is written - a racing heartbeat can never recreate a health
      row for a forgotten member. Ownership is re-checked inside the lock, so a reassignment/unassign that landed meanwhile also drops it
      (``NotTheOwner``; the route answers 409 ``device_not_registered``).
    * DEVICE LOCK. The owner check and the save run under ``omi_devices.device_lock(device_id)``, the lock ``assign`` / ``unassign``
      hold while they change the owner: a reassignment cannot slip between the check and the save. A report that reaches a pendant
      whose owner CHANGED since its row was written starts a fresh overwrite baseline (``_rebaselined``).
    * ONE TRANSITION AT A TIME. The same lock serialises this with the sweep and with other heartbeats for the member, so two tasks can
      never both read the old ``warn_state`` and both ring.
    * NO LOST BELL. The bell rows are inserted BEFORE the new ``warn_state`` is saved, with an id derived from (pendant, kind, episode)
      and ``ON CONFLICT DO NOTHING``: a crash between the two leaves the old state, the retry rings the same episode, and the row that
      already exists is simply kept. A bell is lost only if its insert fails, and then nothing is saved and the report fails (503), so
      the Pi sends it again.
    Raises on a storage failure; the route turns that into a 503."""
    import omi_devices
    import user_erase_gate as erase_gate
    from db_compat import get_compat_db

    when = now or _utcnow()
    gen = erase_gate.generation(owner) if scheduled_at is None else scheduled_at
    pushes: list[tuple[str, dict]] = []
    async with erase_gate.writing(owner, gen) as live:
        if not live:
            raise NotTheOwner("erased")
        # The DEVICE lock spans the owner check and the save: a reassignment / unassignment (``omi_devices.assign`` / ``unassign`` take
        # the same lock) can never land between them and leave the previous owner's id on the row (or a bell for the wrong member).
        async with omi_devices.device_lock(device_id):
            if await current_owner(device_id) != owner:
                raise NotTheOwner("not_the_owner")
            async with get_compat_db() as db:
                prev = await _get_row(db, device_id)
                reassigned = prev is not None and prev.get("user_id") != owner
                if reassigned:
                    prev = _rebaselined(prev)
                row = merge(prev, rep, when)
                row["user_id"] = owner
                old_state = (prev or {}).get("warn_state") or "{}"
                if reassigned:
                    # reassigned: the new owner inherits none of the previous owner's "already told" / cooldown state - but the episode
                    # counter stays (it makes the bell ids unique per pendant, so the new owner's first bell cannot collide with the old one's)
                    kept = _state(prev).get("_episodes")
                    old_state = json.dumps({"_episodes": kept} if kept else {})
                if enabled():
                    active = evaluate(row, when)
                    state, notify = transition({"warn_state": old_state}, active, when)
                    row["warn_state"] = json.dumps(state, sort_keys=True)
                else:
                    active, notify, row["warn_state"] = [], [], old_state
                pushes = await _write_bells(db, owner, device_id, notify)
                await _save(db, device_id, row)
                await db.commit()
    await _push(owner, pushes)
    return active


def bell_id(device_id: str, kind: str, episode: int) -> str:
    """The notification id of one warning episode of one pendant: stable across retries, different for the next episode."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"zoe:omi_health:{device_id}:{kind}:{int(episode)}"))


async def _write_bells(db, user_id: str, device_id: str, notify: list[dict]) -> list[tuple[str, dict]]:
    """Insert the bell rows (idempotent by ``bell_id``) on the caller's connection. Returns the ones that are NEW (to push live). A
    failed insert RAISES: the caller has saved nothing yet, so the whole report is retried and the bell cannot be lost."""
    fresh: list[tuple[str, dict]] = []
    for w in notify:
        nid = bell_id(device_id, w["kind"], w["episode"])
        cur = await db.execute("INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at) "
                               "VALUES (?, ?, 'omi_health', ?, ?, ?, 0, NOW()) ON CONFLICT (id) DO NOTHING",
                               (nid, user_id, BELL_TITLE, w["text"], json.dumps({"kind": w["kind"]})))
        if int(getattr(cur, "rowcount", 1) or 0) > 0:
            fresh.append((nid, w))
    return fresh


async def _push(user_id: str, fresh: list[tuple[str, dict]]) -> int:
    """The live push of bells that are already committed. Best effort: a failure is logged and never retried (the row exists)."""
    sent = 0
    for nid, w in fresh:
        try:
            from push import broadcaster

            await broadcaster.broadcast("all", "notification_created", {"id": nid, "type": "omi_health", "title": BELL_TITLE,
                                                                         "message": w["text"], "delivered": False}, user_id=user_id)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("omi_health: live push of a committed notification failed (%s)", type(exc).__name__)
    return sent


async def list_for(user_id: str, *, now: Optional[datetime] = None) -> list[dict]:
    """The caller's pendants' health, live-evaluated. A row whose pendant is NOT the caller's any more (reassigned or unassigned since the
    last report) is dropped at once: the previous owner stops seeing it without waiting for the next report."""
    from db_compat import get_compat_db

    when = now or _utcnow()
    async with get_compat_db() as db:
        cur = await db.execute("SELECT * FROM omi_device_health WHERE user_id = ? ORDER BY device_id", (user_id,))
        rows = [dict(r) for r in await cur.fetchall()]
    out = []
    for row in rows:
        if await current_owner(row["device_id"]) != user_id:
            continue
        out.append(public_view(row, when, label=await _label(row["device_id"])))
    return out


async def forget_user(user_id: str) -> int:
    """Erase cascade: the member's health rows go with them (called from the member's Omi erase). Only a MISSING table (migration 0056 not
    applied: nothing was ever stored) counts as nothing to erase and returns 0; every other failure RAISES so the erase cannot read as
    complete while the rows (user id, timestamps, pendant metadata) remain."""
    from db_compat import get_compat_db

    try:
        async with get_compat_db() as db:
            cur = await db.execute("DELETE FROM omi_device_health WHERE user_id = ?", (user_id,))
            n = getattr(cur, "rowcount", 0) or 0
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        if _table_missing(exc):
            return 0
        raise
    return max(0, n)


def _table_missing(exc: BaseException) -> bool:
    return type(exc).__name__ == "UndefinedTableError" or "no such table" in str(exc).lower()


# --- notifications + the first-turn line -----------------------------------------------------------------------------------------------------------
async def morning_items(user_id: str, *, now: Optional[datetime] = None) -> list[dict]:
    """At most ONE ``{"text", "kind"}`` for the member's first-turn-of-the-day brief (the most important active warning across their
    pendants), or []. ``ZOE_OMI_HEALTH`` off, no rows, or an unmigrated table = []. Never raises."""
    if not enabled():
        return []
    try:
        views = await list_for(user_id, now=now)
    except Exception as exc:  # noqa: BLE001
        logger.debug("omi_health: morning items unavailable (%s)", type(exc).__name__)
        return []
    best: Optional[dict] = None
    for view in views:
        for w in view["warnings"]:
            if best is None or KINDS.index(w["kind"]) < KINDS.index(best["kind"]):
                best = w
    return [{"text": best["text"], "kind": best["kind"]}] if best else []


# --- the periodic sweep ---------------------------------------------------------------------------------------------------------------------------
async def sweep(*, now: Optional[datetime] = None) -> int:
    """Re-evaluate every row (time-based warnings arise with no report at all: a silent Pi, a pendant away for 36 h) and notify new
    ones. Returns how many bells were created. Never raises; one row's failure does not stop the others.

    Each row is handled inside the owner's erase-gate lock (the same one a heartbeat takes), re-read FRESH inside it: a heartbeat and
    the sweep can never both decide to ring the same warning, and a member erased mid-sweep is skipped."""
    if not enabled():
        return 0
    import omi_devices
    import user_erase_gate as erase_gate
    from db_compat import get_compat_db

    when = now or _utcnow()
    gens = erase_gate.snapshot()
    raised = 0
    try:
        async with get_compat_db() as db:
            cur = await db.execute("SELECT device_id, user_id FROM omi_device_health")
            targets = [(r["device_id"], r["user_id"]) for r in await cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("omi_health: sweep failed (%s)", type(exc).__name__)
        return 0
    for device_id, user_id in targets:
        try:
            pushes: list[tuple[str, dict]] = []
            async with erase_gate.writing(user_id, gens.get(user_id, 0)) as live:
                if not live:
                    continue
                async with omi_devices.device_lock(device_id):           # the owner check and the write are one step (see ``record_report``)
                    if await current_owner(device_id) != user_id:
                        continue              # erased, reassigned or unassigned since its last report: not this member's business
                    async with get_compat_db() as db:
                        row = await _get_row(db, device_id)
                        if row is None or row["user_id"] != user_id:
                            continue
                        state, notify = transition(row, evaluate(row, when), when)
                        new_state = json.dumps(state, sort_keys=True)
                        if new_state == (row.get("warn_state") or "{}"):
                            continue
                        pushes = await _write_bells(db, user_id, device_id, notify)       # first: a failure here saves nothing
                        await db.execute("UPDATE omi_device_health SET warn_state = ? WHERE device_id = ?", (new_state, device_id))
                        await db.commit()
            await _push(user_id, pushes)
            raised += len(pushes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("omi_health: sweep of one pendant failed (%s)", type(exc).__name__)
    return raised


async def run_loop(interval_s: float = SWEEP_S) -> None:
    """Started from ``main.py`` when the Omi feature is on. Sleeps first: a restart never fires a burst of stale warnings at once."""
    while True:
        await asyncio.sleep(interval_s)
        await sweep()
