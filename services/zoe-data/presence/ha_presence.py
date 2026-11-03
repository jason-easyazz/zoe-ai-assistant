"""Home Assistant presence reader: the OBSERVED half of environmental awareness (flag ``ZOE_HA_PRESENCE=off|shadow|on``).

Reads ``person`` and ``device_tracker`` states through the existing HA bridge (``ZOE_HA_BRIDGE_URL``; the bridge holds the one HA
credential, zoe-data adds none) once a minute and writes one OBSERVED row per change into ``presence_observations``. Rules:

* allow-list: only the ``person`` and ``device_tracker`` domains are read; any other entity in a payload is dropped, and only the
  ``state`` field is kept (attributes carry GPS and addresses, so they are never read into a row).
* the entity id is stored as ``sha256(entity_id)``; the state is ``home`` / ``away`` / ``unknown`` only.
* observed and inferred never merge: this module writes observed rows and nothing else; nothing here reaches a user.
* per-entity debounce (a flip is written at most once per ``DEBOUNCE_S``), a hard cap of ``MAX_ROWS_PER_MIN`` rows a minute with
  ONE ``storm`` row above it, and any bridge/DB failure means "write nothing this poll". The bridge answers an HA failure with HTTP
  200 + ``{"entities": [], "error": ...}``; that is a failure here, never "nobody is anywhere".
* the debounce state is in memory: after a restart every entity is written once again (the baseline), at most ``MAX_ROWS_PER_MIN``
  a minute, and the tracked keys are pruned to the entities the last good poll saw (bounded by HA's own entity count).
* retention: rows older than ``RETENTION_S`` are deleted (checked at most every ``PRUNE_EVERY_S``). The key is an UNSALTED hash of
  the entity id, so it is a pseudonym (guessable from the HA entity name), not anonymous - the bounded retention is what keeps this
  household metadata from accumulating (``docs/knowledge/forget-reach.md``, "Stores outside the per-member inventory").
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

ALLOWED_DOMAINS = ("person", "device_tracker")
SOURCE = "ha_bridge"
JOB_ID = "ha_presence_poll"
POLL_INTERVAL_S = 60
BRIDGE_TIMEOUT_S = 5.0
DEBOUNCE_S = 120.0
MAX_ROWS_PER_MIN = 20
TTL_S = 180
STORM_KEY = "storm"
RETENTION_S = 14 * 86400
PRUNE_EVERY_S = 3600.0

Fetch = Callable[[str], Awaitable[list]]

_last_state: dict[str, str] = {}   # entity_key -> last WRITTEN state
_last_write: dict[str, float] = {}  # entity_key -> monotonic time of that write
_window: dict[str, float] = {"start": 0.0, "rows": 0.0, "storm": 0.0, "pruned": float("-inf")}


def mode() -> str:
    from typed_env import flag

    return flag("ZOE_HA_PRESENCE")


def entity_key(entity_id: str) -> str:
    return hashlib.sha256(entity_id.encode("utf-8")).hexdigest()


def map_state(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    if s == "home":
        return "home"
    if s in ("", "unknown", "unavailable", "none"):
        return "unknown"
    return "away"  # not_home, or any named zone


def reset_state() -> None:
    _last_state.clear()
    _last_write.clear()
    _window.update(start=0.0, rows=0.0, storm=0.0, pruned=float("-inf"))


async def _bridge_fetch(domain: str) -> list:
    import httpx
    import os

    base = os.environ.get("ZOE_HA_BRIDGE_URL", "http://127.0.0.1:8007").rstrip("/")
    async with httpx.AsyncClient(timeout=BRIDGE_TIMEOUT_S) as client:
        r = await client.get(f"{base}/entities", params={"domain": domain})
        r.raise_for_status()
        data = r.json()
    return entities_of(data)


def entities_of(data: Any) -> list:
    """The bridge's ``GET /entities`` body (``services/homeassistant-mcp-bridge/main.py``): ``{"entities": [...], "count": n}``, or on an
    HA failure STILL HTTP 200 with ``{"entities": [], "error": ..., "status": n}``. An error body raises - an HA outage is "write nothing",
    never an empty house."""
    if not isinstance(data, dict):
        raise ValueError("bridge body is not an object")
    if data.get("error") or "entities" not in data:
        raise RuntimeError(f"bridge reported an error (status {data.get('status')})")
    ents = data["entities"]
    if not isinstance(ents, list):
        raise ValueError("bridge entities is not a list")
    return ents


async def _prune(db: Any, now_utc: datetime) -> None:
    await db.execute("DELETE FROM presence_observations WHERE observed_at < ?", (now_utc - timedelta(seconds=RETENTION_S),))


async def _insert(db: Any, key: str, state: str, now_utc: datetime) -> None:
    await db.execute(
        "INSERT INTO presence_observations (observed_at, source, entity_key, state, ttl_s) VALUES (?, ?, ?, ?, ?)",
        (now_utc, SOURCE, key, state, TTL_S),
    )


async def poll_once(db: Any = None, *, fetch: Optional[Fetch] = None, now: Optional[float] = None) -> int:
    """One poll. Returns the number of rows written; never raises."""
    if mode() == "off":
        return 0
    fetch = fetch or _bridge_fetch
    mono = time.monotonic() if now is None else now
    try:
        seen: dict[str, str] = {}
        for domain in ALLOWED_DOMAINS:
            for ent in await fetch(domain):
                eid = str((ent or {}).get("entity_id", ""))
                if eid.split(".", 1)[0] not in ALLOWED_DOMAINS or "." not in eid:
                    continue
                seen[entity_key(eid)] = map_state((ent or {}).get("state"))
    except Exception as exc:  # noqa: BLE001 - fail toward "do nothing"
        logger.info("ha_presence: poll skipped (%s)", type(exc).__name__)
        return 0
    logger.debug("ha_presence: poll saw %d entities", len(seen))
    for gone in [k for k in _last_state if k not in seen]:   # bound the debounce state to what HA has now
        _last_state.pop(gone, None)
        _last_write.pop(gone, None)

    if mono - _window["start"] >= 60.0:
        _window.update(start=mono, rows=0.0, storm=0.0)
    pending: list[tuple[str, str]] = []
    storm = False
    for key, state in seen.items():
        if _last_state.get(key) == state:
            continue
        if key in _last_write and mono - _last_write[key] < DEBOUNCE_S:
            continue  # flapping: retried next poll, last_state not advanced
        if _window["rows"] + len(pending) >= MAX_ROWS_PER_MIN:
            storm = True
            continue
        pending.append((key, state))
    if storm and not _window["storm"]:
        pending.append((STORM_KEY, "unknown"))
    prune = mono - _window["pruned"] >= PRUNE_EVERY_S
    if not pending and not prune:
        return 0

    now_utc = datetime.now(timezone.utc)

    async def _write(conn: Any) -> None:
        for key, state in pending:
            await _insert(conn, key, state, now_utc)
        if prune:
            await _prune(conn, now_utc)
        await conn.commit()

    try:
        if db is None:
            from db_pool import get_db_ctx

            async with get_db_ctx() as owned:
                await _write(owned)
        else:
            await _write(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ha_presence: write failed (%s); nothing recorded", type(exc).__name__)
        return 0
    if prune:
        _window["pruned"] = mono
    for key, state in pending:
        if key == STORM_KEY:
            _window["storm"] = 1.0
            logger.info("ha_presence: storm cap hit (>%d changes/min); further changes deferred", MAX_ROWS_PER_MIN)
            continue
        _last_state[key] = state
        _last_write[key] = mono
        _window["rows"] += 1
        logger.info("ha_presence: %s -> %s", key[:12], state)
    return len(pending)


def register(scheduler: Any) -> bool:
    """Register the poll job when the flag is shadow|on. ``off`` registers nothing."""
    if mode() == "off":
        return False
    scheduler.add_job(
        poll_once, trigger="interval", seconds=POLL_INTERVAL_S, id=JOB_ID,
        replace_existing=True, coalesce=True, max_instances=1,
    )
    return True


async def observed_view(db: Any, limit: int = 200) -> list[dict]:
    """Latest row per hashed key, hashed key + state + time only."""
    out: dict[str, dict] = {}
    async with db.execute(
        "SELECT entity_key, state, observed_at FROM presence_observations WHERE entity_key <> ? ORDER BY id DESC LIMIT ?",
        (STORM_KEY, int(limit)),
    ) as cur:
        for k, s, t in await cur.fetchall():
            out.setdefault(k, {"entity_key": k, "state": s, "observed_at": str(t)})
    return list(out.values())
