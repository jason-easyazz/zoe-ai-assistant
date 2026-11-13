"""Owner alert for a cold FIRST household voice turn (``PANEL_KV_MISS kind=first``).

A first reply that re-prefilled its whole prompt costs ~5 s instead of ~1.2 s and has regressed three times; a log line
nobody reads is not a guard. ``zoe_flue_client._panel_kv_alarm`` (household panel turns only, never probe/harness traffic)
calls ``note_first_miss`` and the owner gets ONE Telegram message a day through the shared owner-alert helper
(``scripts/maintenance/zoe_crash_loop_watch.deliver``: separate token path, survives zoe-data, bounded retries, and a
durable outbox that re-sends an undelivered alert later instead of losing it).
The message carries the day's miss count so far and yesterday's. State: ``~/.zoe/kv-miss-alert.json`` (counts only).
``ZOE_PANEL_KV_MISS_ALERT=0`` turns it off. Never raises, never blocks a turn (all work, state I/O included, runs on a
daemon thread). The day is marked alerted once the alert was delivered or is held in the outbox; only an alert that could not
even be stored is retried on later misses, at most ``MAX_ATTEMPTS_PER_DAY`` a day.
"""
from __future__ import annotations

import asyncio
import datetime
import importlib.util
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from typed_env import env_bool, env_str

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_lock = threading.Lock()
_last_failure = ""   # why the last send failed: a transport class from the ops helper's closed vocabulary, never free text
_alerted_day = ""   # in-process record: the 1/day limit holds even when the state file cannot be written


def _state_path() -> Path:
    return Path(env_str("ZOE_KV_MISS_ALERT_STATE") or (Path.home() / ".zoe" / "kv-miss-alert.json"))


def _enabled() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        # an unrelated test must never write the live state file: only an explicit override OUTSIDE ~/.zoe counts
        override = env_str("ZOE_KV_MISS_ALERT_STATE")
        live = (Path.home() / ".zoe").resolve()
        if not override or Path(override).resolve() == live or live in Path(override).resolve().parents:
            return False
    return env_bool("ZOE_PANEL_KV_MISS_ALERT", default=True)


def _today(now: "float | None" = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))


def _yesterday(now: "float | None" = None) -> str:
    t = time.localtime(time.time() if now is None else now)
    return (datetime.date(t.tm_year, t.tm_mon, t.tm_mday) - datetime.timedelta(days=1)).isoformat()


_ops_mod = None   # the ops sender module, loaded once per process (tests inject one pointed at temp paths)
ALERT_CODE = "kv_miss_first"


def _ops_module():
    """``scripts/maintenance/zoe_crash_loop_watch.py``: the shared owner-alert helper (``deliver``: retries, a durable
    outbox, a marker per outcome) on the ops Telegram path (separate token file, survives zoe-data)."""
    global _ops_mod
    if _ops_mod is None:
        spec = importlib.util.spec_from_file_location(
            "_zoe_clw", _REPO_ROOT / "scripts" / "maintenance" / "zoe_crash_loop_watch.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        _ops_mod = mod
    return _ops_mod


def _marker_log(line: str) -> None:
    quiet = any(f"event={e} " in line for e in ("raised", "delivered", "uncertain", "healed"))
    (logger.info if quiet else logger.warning)("%s", line)


def _ops_send(text: str) -> bool:
    """Hand the alert to the shared helper. True once it was delivered OR is held in the durable outbox (which retries
    it); False only when it could not even be stored and the direct send failed. Under pytest it never reaches the
    network or ``~/.zoe`` unless a test injected the ops module."""
    global _last_failure
    if os.environ.get("PYTEST_CURRENT_TEST") and _ops_mod is None:
        return False
    outcome, reason = _ops_module().deliver(ALERT_CODE, text, log=_marker_log)
    _last_failure = "" if outcome == "delivered" else str(reason)
    if reason in ("auth", "config"):
        _notice_owner_now()
    if outcome in ("queued", "gave_up"):
        logger.warning("kv_miss_alert: alert not delivered now, held in the outbox for retry: %s", _last_failure)
    return outcome in ("delivered", "uncertain", "queued")


def alerting_spells() -> list:
    """The ops helper's recent broken spells [{since, reason, healed}], [] when none or unreadable. Read-only; under
    pytest only when a test injected the ops module."""
    if os.environ.get("PYTEST_CURRENT_TEST") and _ops_mod is None:
        return []
    try:
        return list(_ops_module().alerting_spells())
    except Exception:  # noqa: BLE001
        return []


_loop = None   # zoe-data's event loop, captured by note_first_miss (called on it): the owner notice is created there


def _notice_owner_now() -> None:
    """zoe-data itself just saw alerting break: create the owner's panel notification NOW on its event loop, rather
    than waiting for the 5-minute tick. Never raises; nothing when there is no loop (tests, sync mode)."""
    loop = _loop
    if loop is None or loop.is_closed():
        return
    try:
        import notifications_store

        asyncio.run_coroutine_threadsafe(notifications_store.sync_alerting_broken(), loop)
    except Exception:  # noqa: BLE001
        logger.debug("kv_miss_alert: owner notice not scheduled", exc_info=True)


def alerting_broken() -> "dict | None":
    """The ops helper's open "owner alerting is broken" record (credential refused / no setting), else None. Read-only;
    under pytest only when a test injected the ops module."""
    if os.environ.get("PYTEST_CURRENT_TEST") and _ops_mod is None:
        return None
    try:
        return _ops_module().alerting_broken()
    except Exception:  # noqa: BLE001
        return None


MAX_ATTEMPTS_PER_DAY = 3   # a failed send (Telegram down) is retried on later misses that day, never more than this


def note_first_miss(first_prompt: int, first_cache: int, *, now: "float | None" = None,
                    send: "Optional[Callable[[str], bool]]" = None, sync: bool = False) -> "str | None":
    """Count one cold first turn and send the day's single alert. ALL work (state file I/O included) runs on a daemon
    thread, so the voice turn that hit the miss never waits on storage; ``sync=True`` (tests) runs it inline and returns
    the text sent, or None."""
    global _loop
    try:
        if not _enabled():
            return None
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if sync:
            return _record_and_send(first_prompt, first_cache, now, send)
        threading.Thread(target=_record_and_send, args=(first_prompt, first_cache, now, send),
                         name="kv-miss-alert", daemon=True).start()
        return None
    except Exception:  # noqa: BLE001 - instrumentation must never break a turn
        return None


_attempts: dict = {}     # day -> send attempts this process made (the bound holds when the state file cannot be written)
_inflight: set = set()   # days with a send in progress: a concurrent miss that day must not start a second one


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save(path: Path, st: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        logger.warning("kv_miss_alert: state not saved", exc_info=True)


def _record_and_send(first_prompt: int, first_cache: int, now: "float | None",
                     send: "Optional[Callable[[str], bool]]") -> "str | None":
    global _alerted_day
    try:
        day = _today(now)
        path = _state_path()
        with _lock:
            st = _load(path)
            if st.get("day") != day:
                # "yesterday" is the immediately preceding calendar day only: after a gap it is 0, not the last busy day
                st = {"day": day, "count": 0, "alerted": False, "attempts": 0,
                      "prev_count": int(st.get("count") or 0) if st.get("day") == _yesterday(now) else 0}
            st["count"] = int(st.get("count") or 0) + 1
            tries = max(int(st.get("attempts") or 0), int(_attempts.get(day, 0)))
            fire = (not st.get("alerted") and _alerted_day != day and day not in _inflight
                    and tries < MAX_ATTEMPTS_PER_DAY)
            if fire:
                tries += 1
                st["attempts"] = tries
                _attempts[day] = tries
                _inflight.add(day)
            _save(path, st)
        if not fire:
            return None
        text = (f"Zoe: a first voice reply after idle missed the brain cache (prompt {int(first_prompt)} tokens, "
                f"{int(first_cache)} cached) - expect ~5 s instead of ~1.2 s. Cold first turns today: {st['count']}, "
                f"yesterday: {st.get('prev_count', 0)}. At most one alert a day. Compare FLUE_KV_WARM key= with "
                f"FLUE_PREFIX_KEY key= in the zoe-data log.")
        ok = False
        try:
            ok = bool((send or _ops_send)(text))
            if not ok:
                logger.warning("kv_miss_alert: alert not delivered (attempt %d/%d): %s", tries, MAX_ATTEMPTS_PER_DAY,
                               _last_failure or "unknown")
        except Exception as exc:  # noqa: BLE001
            logger.warning("kv_miss_alert: send failed: %s (attempt %d/%d)", type(exc).__name__, tries,
                           MAX_ATTEMPTS_PER_DAY)
        with _lock:
            _inflight.discard(day)   # only this worker's day: a send that crossed midnight never frees the next day's claim
            if ok:   # delivered: only now is the day done (a failed send stays eligible for a bounded retry)
                _alerted_day = day
                st2 = _load(path)
                if st2.get("day") == day:
                    st2["alerted"] = True
                    _save(path, st2)
        return text
    except Exception:  # noqa: BLE001 - instrumentation must never break anything
        return None
