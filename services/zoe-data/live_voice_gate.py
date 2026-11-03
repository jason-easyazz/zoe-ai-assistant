"""One answer to "is something more important than background STT happening right now?" - the yield gate for the Omi pendant
transcriber, which shares the panel's in-process Moonshine model and so competes with live voice for it.

``busy_reason()`` returns None when background STT may run, else a short stable code. It only READS (no import of the voice router,
no lock taken, no file written), so asking is never itself a source of contention:

* ``live_stt``  - the shared Moonshine inference lock is held, or an under-speech STT stream is open: a live turn is being decoded.
* ``harness``   - ``/tmp/zoe-voice-harness.lock`` is flock-ed (the nightly voice probe / replay harness / any ``flock`` wrapper). Read
                  from ``/proc/locks`` by inode, NOT by trying to take the lock: a try-lock would itself briefly fail a harness's ``flock -n``.
* ``night_unit``- ``zoe-night-window.service`` is active (the 12B owns the box).
* ``cooloff``   - any of the above was seen within ``ZOE_OMI_YIELD_COOLOFF_S`` (default 15): a turn is a burst of STT calls with gaps,
                  and a harness hands the lock over between runs.

Panel-session recency (a turn in the last 45 s) stays in the pendant module's ``voice_busy``. Nothing here is a decision about WHETHER to
transcribe, only about WHEN; a deferred recording stays pending (see the pendant module's ``GateClosed``).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Optional

from typed_env import env_bool, env_int, env_str

HARNESS_LOCK_DEFAULT = "/tmp/zoe-voice-harness.lock"
NIGHT_UNIT = "zoe-night-window.service"
UNIT_TTL_S = 10.0

_last_busy_at: Optional[float] = None
_unit_cache: dict = {"at": -1e9, "active": False}


def cooloff_s() -> float:
    """``ZOE_OMI_YIELD_COOLOFF_S`` - how long background STT stays out of the way after the last busy signal."""
    return float(max(0, env_int("ZOE_OMI_YIELD_COOLOFF_S", 15)))


def enabled() -> bool:
    """``ZOE_OMI_YIELD_TO_VOICE`` (default on) - off restores the old behaviour (panel-session recency only)."""
    return env_bool("ZOE_OMI_YIELD_TO_VOICE", True)


def harness_lock_path() -> str:
    return env_str("ZOE_VOICE_HARNESS_LOCK", HARNESS_LOCK_DEFAULT) or HARNESS_LOCK_DEFAULT


def harness_lock_held(path: Optional[str] = None) -> bool:
    """True while some process holds a flock on the harness lock file. Read-only: scans ``/proc/locks`` for the file's inode."""
    try:
        st = os.stat(path or harness_lock_path())
        want = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
        with open("/proc/locks", encoding="ascii", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 6 and parts[1] == "FLOCK" and parts[5] == want:
                    return True
    except (OSError, ValueError):
        pass                                 # no lock file / no /proc/locks (off-Linux): nothing is holding it
    return False


def _finished(sess: object) -> bool:
    done = getattr(sess, "_done", None)
    try:
        return bool(done is not None and done.is_set())
    except Exception:  # noqa: BLE001
        return False


def live_stt_in_flight() -> bool:
    """The panel's Moonshine inference lock is held, or an under-speech STT stream is open. Reads module state only, and only if the
    voice router is already loaded (``sys.modules``) - this never imports it."""
    mod = sys.modules.get("routers.voice_tts")
    lock = getattr(mod, "_moonshine_infer_lock", None) if mod else None
    try:
        if lock is not None and lock.locked():
            return True
    except Exception:  # noqa: BLE001
        pass
    ss = sys.modules.get("voice_stt_stream")
    try:
        if not ss:
            return False
        if getattr(ss, "_pending", None):
            return True
        # A session whose worker has finished (error / rejected chunk) can sit in the registry until the next reap: not inference in flight.
        return any(not _finished(s) for s in list((getattr(ss, "_sessions", None) or {}).values()))
    except Exception:  # noqa: BLE001
        return False


def _probe_unit() -> bool:
    try:
        env = dict(os.environ)
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        r = subprocess.run(["systemctl", "--user", "is-active", "--quiet", NIGHT_UNIT], env=env, timeout=1.5,
                           capture_output=True, check=False)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_refresh_lock = threading.Lock()
_refresh_thread: Optional[threading.Thread] = None


def _refresh_unit(t: float) -> None:
    active = _probe_unit()
    _unit_cache["at"], _unit_cache["active"] = t, active


def night_unit_active(now_mono: Optional[float] = None) -> bool:
    """``zoe-night-window.service`` is active. NEVER blocks the caller (this runs on the API event loop): the answer is cached for
    ``UNIT_TTL_S`` and a stale cache is refreshed by one worker thread while the last known value is returned. Before the first probe
    completes it reads as not-active (the harness flock, live STT and cool-off signals still apply); systemctl trouble reads as inactive."""
    global _refresh_thread
    t = time.monotonic() if now_mono is None else now_mono
    if t - _unit_cache["at"] >= UNIT_TTL_S:
        with _refresh_lock:
            if _refresh_thread is None or not _refresh_thread.is_alive():
                _refresh_thread = threading.Thread(target=_refresh_unit, args=(t,), name="live-voice-gate-unit", daemon=True)
                _refresh_thread.start()
    return bool(_unit_cache["active"])


def busy_reason(now_mono: Optional[float] = None) -> Optional[str]:
    """None when background STT may run; else ``live_stt`` | ``harness`` | ``night_unit`` | ``cooloff``."""
    global _last_busy_at
    if not enabled():
        return None
    t = time.monotonic() if now_mono is None else now_mono
    code = ("live_stt" if live_stt_in_flight() else "harness" if harness_lock_held()
            else "night_unit" if night_unit_active(t) else None)
    if code:
        _last_busy_at = t
        return code
    if _last_busy_at is not None and t - _last_busy_at < cooloff_s():
        return "cooloff"
    return None


def reset() -> None:
    """Test seam: forget the cool-off and the cached unit state."""
    global _last_busy_at
    _last_busy_at = None
    th = _refresh_thread
    if th is not None:
        th.join(5)
    _unit_cache["at"], _unit_cache["active"] = -1e9, False
