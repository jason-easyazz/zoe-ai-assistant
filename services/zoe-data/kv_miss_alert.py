"""Owner alert for a cold FIRST household voice turn (``PANEL_KV_MISS kind=first``).

A first reply that re-prefilled its whole prompt costs ~5 s instead of ~1.2 s and has regressed three times; a log line
nobody reads is not a guard. ``zoe_flue_client._panel_kv_alarm`` (household panel turns only, never probe/harness traffic)
calls ``note_first_miss`` and the owner gets ONE Telegram message a day through the ops sender the crash-loop watcher and
the backup drill use (``scripts/maintenance/zoe_crash_loop_watch._telegram``: separate token path, survives zoe-data).
The message carries the day's miss count so far and yesterday's. State: ``~/.zoe/kv-miss-alert.json`` (counts only).
``ZOE_PANEL_KV_MISS_ALERT=0`` turns it off. Never raises, never blocks a turn (send runs on a daemon thread).
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_lock = threading.Lock()


def _state_path() -> Path:
    return Path(os.environ.get("ZOE_KV_MISS_ALERT_STATE") or (Path.home() / ".zoe" / "kv-miss-alert.json"))


def _enabled() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("ZOE_KV_MISS_ALERT_STATE"):
        return False   # an unrelated test must never write the live state file
    return os.environ.get("ZOE_PANEL_KV_MISS_ALERT", "1").strip().lower() not in ("0", "off", "false", "no")


def _today(now: "float | None" = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))


def _ops_send(text: str) -> bool:
    """The repo's ops Telegram path. Under pytest it never reaches the network."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("ZOE_KV_MISS_ALERT_LIVE_TEST"):
        return False
    spec = importlib.util.spec_from_file_location(
        "_zoe_clw", _REPO_ROOT / "scripts" / "maintenance" / "zoe_crash_loop_watch.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return bool(mod._telegram(text))


def note_first_miss(first_prompt: int, first_cache: int, *, now: "float | None" = None,
                    send: "Optional[Callable[[str], bool]]" = None, sync: bool = False) -> "str | None":
    """Count one cold first turn; send the day's single alert if none went out today. Returns the text sent (or
    queued) or None. ``send``/``sync`` are for tests."""
    try:
        if not _enabled():
            return None
        day = _today(now)
        with _lock:
            path = _state_path()
            try:
                st = json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                st = {}
            if st.get("day") != day:
                st = {"day": day, "count": 0, "alerted": False,
                      "prev_count": int(st.get("count") or 0) if st.get("day") else 0}
            st["count"] = int(st.get("count") or 0) + 1
            fire = not st.get("alerted")
            if fire:
                st["alerted"] = True
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(st))
                os.replace(tmp, path)
            except Exception:  # noqa: BLE001
                logger.warning("kv_miss_alert: state not saved", exc_info=True)
        if not fire:
            return None
        text = (f"Zoe: a first voice reply after idle missed the brain cache (prompt {int(first_prompt)} tokens, "
                f"{int(first_cache)} cached) - expect ~5 s instead of ~1.2 s. Cold first turns today: {st['count']}, "
                f"yesterday: {st.get('prev_count', 0)}. At most one alert a day. Compare FLUE_KV_WARM key= with "
                f"FLUE_PREFIX_KEY key= in the zoe-data log.")
        sender = send or _ops_send

        def _go() -> None:
            try:
                if not sender(text):
                    logger.warning("kv_miss_alert: alert not delivered")
            except Exception as exc:  # noqa: BLE001
                logger.warning("kv_miss_alert: send failed: %s", type(exc).__name__)

        if sync:
            _go()
        else:
            threading.Thread(target=_go, name="kv-miss-alert", daemon=True).start()
        return text
    except Exception:  # noqa: BLE001 - instrumentation must never break a turn
        return None
