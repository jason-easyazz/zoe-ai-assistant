"""The settle rule of the Zoe OS shadow's state directory, kernel side (``shadow_daemon.py`` calls ``require_settled`` before it
reads or writes anything there). Stdlib only; a lab process imports nothing of zoe-data.

    python3 labs/zoe-os/zoe_os/shadow_state.py DIR     # make (or check) a state directory and wait until it may hold data

Read ``docs/knowledge/zoe-os-list-shadow.md`` ("The state directory settles before it holds anything").
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time

# >>> settle: SHARED TEXT. The same lines live in services/zoe-data/os_shadow.py and labs/zoe-os/zoe_os/shadow_state.py (neither
# imports the other); services/zoe-data/tests/test_os_shadow_settle.py fails when the two copies differ by one character.
#
# THE RULE. Shadow state cannot exist until every process has seen its directory. Whoever makes the state directory makes it EMPTY,
# writes the settle marker, and waits SETTLE_S before any data file appears in it. A zoe-data process answers "no shadow state"
# from memory only while its last look at the disk said "no directory" less than TRUST_S ago; SETTLE_S is longer than TRUST_S, so
# by the time there is anything to forget, every process has either seen the directory or stopped trusting its answer.
#
# The marker binds to THIS directory (device and inode: a copy of the directory is a different one) and to the moment it was made:
# on the same boot the age is read on the monotonic clock (CLOCK_MONOTONIC is one clock for every process on the box and does not
# step); after a reboot every process has started since and its start-up look saw the directory, so the marker is settled; with no
# boot id to compare, the wall clock is used and a marker from the future is not trusted. Anything unreadable is not trusted.
MARKER = "settled.json"
POLL_S = 1.0                         # how often a zoe-data process looks for the directory
TRUST_S = 3 * POLL_S                 # how long its "no directory" answer is trusted
SETTLE_S = 4 * POLL_S                # how long a new directory stays empty of data: longer than TRUST_S
DATA_NAMES = ("creds.json", "people.json", "known-people.json", "kernel", "pending-forgets.json", "pending-erases.json")


class Unsettled(OSError):
    """The state directory holds data it must not hold yet, or data with no trustworthy settle marker (a copy made by hand)."""


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def clock():
    """``(wall, monotonic, boot id)`` now."""
    return time.time(), time.monotonic(), boot_id()


def has_data(d):
    """Does the directory hold any data file? Unreadable counts as yes (fail closed)."""
    try:
        names = set(os.listdir(d))
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return bool(names & set(DATA_NAMES))


def read_marker(d):
    """The settle marker of state directory ``d``, or None when it is missing, unreadable, malformed or another directory's."""
    try:
        with open(os.path.join(d, MARKER), encoding="utf-8") as fh:
            m = json.load(fh)
        st = os.stat(d)
        ok = (isinstance(m, dict) and m.get("v") == 1 and isinstance(m.get("boot"), str)
              and all(type(m.get(k)) in (int, float) and math.isfinite(m[k]) for k in ("wall", "mono", "settle_s"))
              and m["settle_s"] >= 0 and m.get("dev") == st.st_dev and m.get("ino") == st.st_ino)
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return m if ok else None


def marker_left(m, wall, mono, boot, settle_s):
    """Seconds until the directory may hold data (0.0 = settled), or None when the marker cannot be trusted (fail closed)."""
    if m is None:
        return None
    need = max(float(m["settle_s"]), float(settle_s))
    if boot and m["boot"] == boot:
        age = mono - m["mono"]           # the same boot: the monotonic clock, which no clock step moves
    elif boot and m["boot"]:
        return 0.0                       # a reboot since: every process started after the directory existed
    else:
        age = wall - m["wall"]           # no boot id to compare: the wall clock, and never a marker from the future
    if not age >= 0:
        return None
    return need - age if need > age else 0.0


def write_marker(d, wall, mono, boot, settle_s):
    """Write the marker unless one is already there (two creators: the first one's time stands). No personal data."""
    st = os.stat(d)
    tmp = os.path.join(d, f".{MARKER}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as fh:
            json.dump({"v": 1, "wall": wall, "mono": mono, "boot": boot, "settle_s": settle_s, "dev": st.st_dev, "ino": st.st_ino}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp, os.path.join(d, MARKER))
    except FileExistsError:
        pass
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def settle(d, now=clock, sleep=time.sleep, settle_s=None):
    """Make state directory ``d`` ready to hold data, and return only when it may. BLOCKING for up to the settle time (a new
    directory): a start-up, a tool or a worker thread, never an event loop.

    * no directory: it is made EMPTY (0700) with its marker, then this waits the settle time;
    * an empty directory with no marker of its own (made by hand, or left by a marker copied from elsewhere): adopted the same way;
    * a marker still young: waits what is left, UNLESS data is already there (written too early): ``Unsettled``;
    * data and no trustworthy marker (a copy made by hand, a restore that did not go through the tool): ``Unsettled``.
    Returns the seconds it waited."""
    settle_s = SETTLE_S if settle_s is None else settle_s
    try:
        os.makedirs(d, mode=0o700)
    except FileExistsError:
        pass
    m = read_marker(d)
    if m is None:
        if has_data(d):
            raise Unsettled("data with no settle marker of this directory: not made by the install / restore tool")
        try:
            os.unlink(os.path.join(d, MARKER))       # a marker that is not this directory's (copied in): replaced
        except FileNotFoundError:
            pass
        write_marker(d, *now(), settle_s)
        m = read_marker(d)
    waited, left = 0.0, marker_left(m, *now(), settle_s)
    while left is not None and left > 0:
        if has_data(d):
            raise Unsettled("data inside the settle window: written before every process could have seen the directory")
        step = min(left, 0.25)
        sleep(step)
        waited += step
        left = marker_left(m, *now(), settle_s)
    if left is None:
        raise Unsettled("the settle marker cannot be trusted (unreadable, or from the future)")
    return waited
# <<< settle


def require_settled(state, now=clock, sleep=time.sleep) -> float:
    """The kernel's gate: ``settle`` the state directory, or refuse to start and SAY WHY. A directory that holds data with no
    trustworthy marker was copied in by hand (or restored outside the tool): the kernel does not serve it, and zoe-data's forgets
    fail closed against it (the data is there, nothing answers) until the owner removes the directory or restores through the tool."""
    try:
        return settle(os.fspath(state), now, sleep)
    except Unsettled as exc:
        raise SystemExit(f"zoe-os shadow kernel: refusing to start: {exc}. Remove the state directory, or restore it with "
                         "services/zoe-data/os_shadow.py --restore (never copy the directory by hand).") from exc


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: shadow_state.py STATE_DIR")
    print(f"settled (waited {require_settled(sys.argv[1]):.1f}s)")
