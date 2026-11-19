"""Zoe OS SHADOW client (docs/knowledge/zoe-os-list-shadow.md, docs/knowledge/zoe-os-integration.md).

Every list add (and, when named in ``ZOE_OS_SHADOW_VERBS``, every reminder create, alarm create and spoken list-item remove) the
household really made is ALSO proposed to a Zoe OS kernel, which records the event and a receipt in its OWN store; the two outcomes
are compared and logged as one codes-only line. The existing write path stays the only thing that changes the household's data.
Flag ``ZOE_OS_LIST_SHADOW`` = ``off`` (default) | ``shadow`` is the ONE switch (it keeps its name: every verb rides it, and the
forget fail-closed rule hangs off it); ``ZOE_OS_SHADOW_VERBS`` (default ``list_add``) names which verbs are shadowed while it is
on, so flipping the switch alone still shadows list add only, exactly as before. There is no ``on``: the kernel is never
authoritative for a SHADOWED verb.

The LISTS DOOR is the other half (``ZOE_OS_LISTS=kernel``, default off; ``ask_list`` / ``report_list`` below, called only by
``list_service``): there the kernel DECIDES a list change before it is made and records it by ids; a refusal is final, an
unreachable or slow kernel lets the change proceed. While it is on, the watch-only hooks for list verbs are silent (``wants``).

Boundaries (each pinned by ``tests/test_os_shadow.py``):

* NO import from ``labs/``. The kernel is reached over its Unix socket with the wire protocol below, which is the kernel's
  documented v1 (``labs/zoe-os/zoe_os/wire.py``); ``tests/test_os_shadow_contract.py`` runs this client against the real lab kernel
  when the lab code is present, so the two cannot drift silently.
* The hook (``note_act``; ``note_add`` / ``note_reminder_create`` / ``note_alarm_create`` / ``note_list_remove`` are its thin
  per-verb argument builders) runs AFTER the live write has committed, is O(1), never blocks and never raises: it puts one tuple on a
  bounded queue. A single worker THREAD does all socket work, so a slow or dead kernel can neither slow nor break a turn. The
  turn never starts that thread: it starts at import (start-up); a turn that finds it dead drops and counts (``worker_down``), and
  ``revive`` (start-up, a forget) brings it back.
* Erase-safe: a proposal for a member who is being erased (``user_erase_gate``) is dropped, and forget jobs go through the SAME
  FIFO queue, so a proposal queued before an erase is sent before the forget and removed by it.
* Forget reaches the kernel (``forget_user`` / ``erase_entity`` / ``erase_topic``): fail-closed while the flag is ``shadow``.
* Logs carry closed codes only: never an item, a list name, a reminder note or a person's name.

Stdlib only (plus ``typed_env`` / ``user_erase_gate``).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import hmac
import json
import logging
import math
import os
import queue
import socket
import struct
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import user_erase_gate

logger = logging.getLogger(__name__)

FLAG = "ZOE_OS_LIST_SHADOW"
VERBS_FLAG = "ZOE_OS_SHADOW_VERBS"
DIR_FLAG = "ZOE_OS_SHADOW_DIR"

#: shadow verb -> (kernel action, the read-back CHECKS). Each check is (proof key, the sent arg it must equal, the code when it differs,
#: the code when the proof lacks it; None = optional). Together they are every field that says WHAT is done and WHEN it fires; FOR WHOM
#: is the receipt's ``on_behalf_of`` (``member_differs``), checked for every verb. A repeating alarm or reminder is never sent: the
#: kernel's verbs are one-shot (``kernel_args``: ``not_expressible reason=recurring``), so repeat never needs comparing.
VERBS = {"list_add": ("list.add", (("item", "item", "item_differs", "kernel_malformed"), ("list", "list", "list_differs", None))),
         "list_remove": ("list.remove", (("item", "item", "item_differs", "kernel_malformed"), ("list", "list", "list_differs", None))),
         "reminder_create": ("remind.set", (("note", "note", "item_differs", "kernel_malformed"),
                                            ("due_at", "due_at", "schedule_differs", "schedule_unproven"))),
         "alarm_create": ("alarm.set", (("alarm", "time", "schedule_differs", "kernel_malformed"),))}
DEFAULT_VERBS = "list_add"
REMIND_MAX_MIN = 10080         # the kernel's ``remind.set`` takes 1..10080 whole minutes from now
#: A reminder is sent and compared on its ABSOLUTE due instant (``due_at``: integer UTC epoch seconds), computed from the live row and
#: the commit time, never a relative "in N minutes" (a queue delay would shift that and still read as a match). The kernel's
#: ``remind.set`` takes exactly one of ``in_minutes`` / ``due_at`` and its read-back returns ``due_at``. An OLDER kernel refuses
#: ``due_at`` as an unknown argument: the act is retried ONCE with ``in_minutes`` recomputed from the instant at SEND time and the
#: outcome is marked ``reason=old_kernel`` (its read-back has no ``due_at``, so it reads ``schedule_unproven``: never a match).
DUE_TOLERANCE_S = 60.0         # the minute rounding of the old-kernel fallback; an exact due_at read-back is equal
DUE_MAX = 2 ** 40              # a due instant past this (year ~36812) is not a time: kernel_malformed
UNKNOWN_ARGUMENT = "unknown argument"
_wall = time.time              # the wall clock the send-time minutes are computed from (a test replaces it)

# ---- the wire (kernel protocol v1; see the module docstring) ----------------------------------------------------------------------
WIRE_VERSION = 1
MAX_REPLY = 64 * 1024          # the client's own cap: a shadow reply is a receipt, never 16 MB
OP_TIMEOUT_S = 0.5             # per socket operation, and never past the write's own deadline
JOB_DEADLINE_S = 1.5           # ONE overall budget per shadowed write: connects, handshakes, publish and act all draw on it
CIRCUIT_S = 5.0                # after an unavailable kernel, skip connecting this long (counted, not retried per add)
FORGET_TIMEOUT_S = 8.0
QUEUE_MAX = 64

SKILL_VOICE, SKILL_BRAIN, SKILL_PANEL = "voice", "brain", "panel"
UTTERANCE_TEXT = "shadow list add"          # the cause event carries no household words: the item rides the act only


def utterance_text(verb: str) -> str:
    """The cause event's fixed text for a verb (``shadow list add`` for list add, as before): never the household's words."""
    return UTTERANCE_TEXT if verb == "list_add" else "shadow " + verb.replace("_", " ")


def cause_text(verb: str) -> str:
    """The text the cause event is PUBLISHED with: the verb's fixed text plus a random tag, so no two cause events of a member
    carry the same string. The kernel shields, per member, every free-text string of what it forgets ("forgotten means forever");
    with one constant text for every write, a member's first forget shielded that constant and the kernel then refused every later
    shadowed write of theirs (``kernel_refused_live_did reason=forgotten``). Still no household word: the tag is random hex."""
    return f"{utterance_text(verb)} {os.urandom(8).hex()}"


class KernelRefused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class KernelUnavailable(Exception):
    """Cannot connect / the socket failed or timed out."""


class KernelProtocol(Exception):
    """The kernel answered something that is not the v1 shape."""


#: a reply whose ``v`` / ``id`` is not this request's: the add is ``kernel_malformed reason=crossed`` (never a match)
CROSSED = "crossed"


class ShadowForgetUnavailable(Exception):
    """A forget could not be confirmed by the kernel while the shadow flag is on: the caller must not say 'forgotten'.
    Deliberately NOT a RuntimeError: ``memory_forget_cascade._store_missing`` reads a RuntimeError in a process that never had a database
    pool as "nothing to reach" and would swallow this as a success (``test_the_entity_cascade_step...`` pins it)."""


def unconfirmed(exc: Optional[BaseException]) -> bool:
    """Is ``exc`` (or anything it was raised from) a forget the kernel did not confirm? The shared erasers wrap it
    (``MemoryService.erase_rows`` raises ``MemoryServiceError ... from`` it), and a caller that swallows an eraser's ordinary failure
    (the rows stay hidden) must still NOT say "forgotten" for this one. Pure; never raises."""
    seen = 0
    while exc is not None and seen < 8:
        if isinstance(exc, ShadowForgetUnavailable):
            return True
        exc, seen = exc.__cause__ or exc.__context__, seen + 1
    return False


# ---- settings -----------------------------------------------------------------------------------------------------------------------
def mode() -> str:
    """``off`` | ``shadow``. Anything unlisted is off (typed_env warns once); ``on`` is not built."""
    try:
        import typed_env

        return "shadow" if str(typed_env.flag(FLAG)).strip().lower() == "shadow" else "off"
    except Exception:  # noqa: BLE001 - a flag read must never break a turn
        return "off"


def verbs() -> frozenset:
    """The verbs shadowed while ``mode()`` is ``shadow``: ``ZOE_OS_SHADOW_VERBS`` (comma list), unknown names ignored; unset or
    unreadable = list add only (the behaviour before the verb list existed)."""
    try:
        import typed_env

        raw = typed_env.flag(VERBS_FLAG)
    except Exception:  # noqa: BLE001
        raw = DEFAULT_VERBS
    items = raw.split(",") if isinstance(raw, str) else raw
    return frozenset(str(v).strip().lower() for v in items if str(v).strip().lower() in VERBS)


def wants(verb: str) -> bool:
    """The switch is ``shadow`` AND the verb is listed: what a call site checks before any extra read it does only for the shadow.
    A LIST verb is never wanted while ``ZOE_OS_LISTS=kernel``: the lists door (``ask_list``) is then the kernel's one record of a list
    change, and the watch-only hook would record it a second time."""
    if mode() != "shadow" or verb not in verbs():
        return False
    return not (verb in LIST_SHADOW_VERBS and lists_on())


LISTS_FLAG = "ZOE_OS_LISTS"
LISTS_ASK_MS_FLAG = "ZOE_OS_LISTS_ASK_MS"
LIST_SHADOW_VERBS = frozenset({"list_add", "list_remove"})


def lists_on() -> bool:
    """``ZOE_OS_LISTS=kernel``: every list change ASKS the kernel first (``ask_list``). Anything else is off: no kernel call."""
    try:
        import typed_env

        return str(typed_env.flag(LISTS_FLAG)).strip().lower() == "kernel"
    except Exception:  # noqa: BLE001 - a flag read must never break a turn
        return False


def state_dir() -> Path:
    try:
        import typed_env

        raw = str(typed_env.flag(DIR_FLAG)).strip()
    except Exception:  # noqa: BLE001
        raw = ""
    return Path(raw) if raw else Path.home() / ".zoe" / "os-shadow"


def _creds_path() -> Path:
    return state_dir() / "creds.json"


def provisioned() -> bool:
    """The kernel's supervisor has written its credential file: a kernel was installed here at some point."""
    try:
        return _creds_path().is_file()
    except OSError:
        return False


#: every file or directory the shadow kernel or this client ever leaves in the state directory (``labs/zoe-os/shadow_daemon.py``):
#: the credentials, the roster, the ids the kernel can still forget, the kernel's own store, and the parked forgets
STATE_NAMES = ("creds.json", "people.json", "known-people.json", "kernel", "pending-forgets.json", "pending-erases.json")   # = DATA_NAMES (pinned)


def shadow_state_exists() -> bool:
    """True unless it is CERTAIN nothing was ever recorded here: the state directory does not exist, or exists and holds none of
    ``STATE_NAMES``. A missing credential is not that proof (the kernel's database can outlive it), and a directory that cannot be
    read, or a path that is not a directory, counts as state (fail closed)."""
    d = state_dir()
    try:
        if not d.exists():
            return False
        if not d.is_dir():
            return True
        names = {p.name for p in d.iterdir()}
    except OSError:
        return True
    return bool(names & set(STATE_NAMES))


# ---- the state directory settles before it holds anything; "no shadow state" is then answered from memory ----------------------------
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



class ShadowStateUnsettled(Unsettled):
    """zoe-data's name for Unsettled (a park that could not be made because the directory is not fit to hold data)."""


# A forget with the flag off and no shadow state is a no-op and costs a spoken forget NOTHING: no executor hop, no stat, no disk
# at all. "No state" here means "no state DIRECTORY", and the answer is the cached one alone - sound because of THE RULE above:
#   * established OFF the turn (at import, and by any probe), then kept fresh by a daemon thread that looks every POLL_S;
#   * the time recorded with an answer is taken BEFORE the look at the disk, so an answer is never younger than it claims;
#   * trusted only for this directory and for TRUST_S: a dead or stalled poller, a changed directory setting, or nothing
#     established yet means the forget probes the disk itself, in a thread;
#   * this process making the directory flips the answer at once (_state_installed); another process making it is seen by
#     the poll, and until then there is provably no data in it (SETTLE_S > TRUST_S);
#   * a process that starts after the directory exists sees it at import, during the settle window included.
# Once the directory exists every forget probes (in a thread): data means the kernel path, fail closed; none means nothing to forget.
_mono = time.monotonic                               # the cache's clock (tests inject one)
_state_lock = threading.Lock()
_state_known: tuple = ("", True, 0.0)                # (state directory, the directory exists, _mono() BEFORE that look)
_state_seq = 0                                       # bumped when this process makes the directory: an older "absent" reading is void
_state_poller: Optional[threading.Thread] = None
_state_stop = threading.Event()


def _no_state() -> bool:
    """True only when it is certain, WITHOUT touching the disk, that there is no state directory. Loop-safe: memory only."""
    d, exists, at = _state_known
    return not exists and _mono() - at < TRUST_S and d == str(state_dir())


def _dir_exists() -> bool:
    try:
        os.stat(state_dir())
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _state_installed() -> None:
    """This process is about to make (or write into) the state directory: no forget of this process skips the disk from here on."""
    global _state_known, _state_seq
    with _state_lock:
        _state_seq += 1
        _state_known = (str(state_dir()), True, _mono())


def _look(d: str, seq: int, at: float, exists: bool) -> None:
    global _state_known
    with _state_lock:
        if exists or seq == _state_seq:              # an "absent" look that raced this process making the directory is void
            _state_known = (d, exists, at)


def _state_probe() -> bool:
    """Look at the disk: is there DATA to forget (shadow_state_exists)? Records whether the directory exists, and starts the
    poller while it does not. BLOCKING: a thread or start-up, never the loop."""
    global _state_poller
    d, seq, at = str(state_dir()), _state_seq, _mono()
    exists = _dir_exists()
    _look(d, seq, at, exists)
    with _state_lock:
        if not exists and not (_state_poller is not None and _state_poller.is_alive() and _state_poller.name == d):
            _state_stop.clear()
            _state_poller = threading.Thread(target=_state_poll, args=(d,), name=d, daemon=True)
            _state_poller.start()
    return exists and shadow_state_exists()


def _state_poll(d: str) -> None:
    """Keep "no directory" fresh for d; ends when the directory appears, the setting changes, or a reset asks."""
    me = threading.current_thread()
    while not _state_stop.wait(POLL_S):
        try:
            if _state_poller is not me or str(state_dir()) != d:
                return
            seq, at = _state_seq, _mono()
            exists = _dir_exists()
            if _state_poller is not me:
                return
            _look(d, seq, at, exists)
            if exists:
                return
        except Exception:  # noqa: BLE001 - the answer goes stale and forgets probe the disk themselves (the safe side)
            return


def ensure_state_dir() -> Path:
    """The ONE way zoe-data makes the state directory or first writes into it (a park, the roster, the owner's --init / --restore):
    the directory is made EMPTY with its settle marker and this WAITS the settle time before returning, so no data file appears in
    a directory some process has not seen (settle). A directory that already holds data needs no wait (every process either
    saw it or probes); whether that data may be SERVED is the kernel's gate. BLOCKING for up to SETTLE_S when the directory
    is new: a worker / executor thread or the CLI, and it REFUSES to run on an event loop."""
    _state_installed()
    d = state_dir()
    if has_data(str(d)):
        return d
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("ensure_state_dir may wait the settle time: never on the event loop")
    try:
        settle(str(d), clock, _sleep, SETTLE_S)
    except Unsettled as exc:
        raise ShadowStateUnsettled(str(exc)) from exc
    return d


_sleep = time.sleep                                  # the settle wait (tests inject one)


def _load_creds() -> tuple[str, dict]:
    try:
        raw = json.loads(_creds_path().read_text("utf-8"))
        return str(raw["socket"]), {k: bytes.fromhex(v) for k, v in raw["creds"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise KernelUnavailable("not_provisioned") from exc


_roster_cache: tuple = (0.0, None, frozenset())      # (checked_at, mtime, ids)


def _roster_ids(entries) -> frozenset:
    """Read plain ids and role-bearing entries; malformed entries confer no membership."""
    ids = (entry.get("id") if isinstance(entry, dict) else entry for entry in entries)
    return frozenset(value.strip() for value in ids if isinstance(value, str) and value.strip())


def roster() -> frozenset:
    """Member ids the kernel was started with (``people.json`` beside the credentials). A member not in it is never proposed: the
    kernel could not forget what it recorded about them, and it would refuse them anyway."""
    global _roster_cache
    now = time.monotonic()
    checked, mtime, ids = _roster_cache
    if now - checked < 30.0 and mtime is not None:
        return ids
    path = state_dir() / "people.json"
    try:
        st = path.stat().st_mtime
        if st != mtime:
            ids = _roster_ids(json.loads(path.read_text("utf-8"))["people"])
        _roster_cache = (now, st, ids)
    except (OSError, ValueError, KeyError, TypeError):
        _roster_cache = (now, None, frozenset())
        ids = frozenset()
    return ids


# ---- the socket protocol ------------------------------------------------------------------------------------------------------------
def _op_timeout(until: Optional[float]) -> float:
    """The timeout for the NEXT socket operation: ``OP_TIMEOUT_S``, cut to what is left of the write's budget (``until``, a
    ``time.monotonic()`` instant; None = no overall budget, e.g. a forget). A spent budget raises ``socket.timeout`` before the
    operation starts, so no connect, send or read can run past the write's deadline."""
    if until is None:
        return OP_TIMEOUT_S
    left = until - time.monotonic()
    if left <= 0:
        raise socket.timeout("deadline")
    return min(OP_TIMEOUT_S, left)


def _recv_exact(sock: socket.socket, n: int, until: Optional[float] = None) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        sock.settimeout(_op_timeout(until))           # per chunk: a reply dripped byte by byte cannot outlast the budget
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise KernelUnavailable("closed")
        buf += chunk
    return bytes(buf)


def _no_constant(name: str):
    """``NaN`` / ``Infinity`` / ``-Infinity`` are not JSON: Python's decoder takes them unless told not to. A kernel reply carrying one
    is not v1 (``kernel_malformed``), decided HERE, the one place replies are decoded, so no caller can forget."""
    raise ValueError("non-standard constant")


def _finite_float(text: str) -> float:
    """A JSON number too large for a float (``1e400``) decodes to inf: rejected the same way."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite number")
    return value


def _read_frame(sock: socket.socket, until: Optional[float] = None) -> dict:
    (size,) = struct.unpack(">I", _recv_exact(sock, 4, until))
    if size > MAX_REPLY:
        raise KernelProtocol("reply too large")
    try:
        obj = json.loads(_recv_exact(sock, size, until), parse_constant=_no_constant, parse_float=_finite_float)
    except ValueError as exc:
        raise KernelProtocol("not json") from exc
    if not isinstance(obj, dict):
        raise KernelProtocol("not an object")
    return obj


def _write_frame(sock: socket.socket, obj: dict, until: Optional[float] = None) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode()
    sock.settimeout(_op_timeout(until))
    sock.sendall(struct.pack(">I", len(body)) + body)


class _Conn:
    """One authenticated connection (one kernel identity). Any failure closes it: a half-read reply must never be mistaken for the
    answer to the next request."""

    def __init__(self, path: str, skill: str, credential: bytes, until: Optional[float] = None) -> None:
        self._id = 0
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.settimeout(_op_timeout(until))
            self.sock.connect(path)
            hello = _read_frame(self.sock, until)
            if hello.get("v") != WIRE_VERSION:
                raise KernelProtocol("version")
            nonce = bytes.fromhex(str(hello["nonce"]))
            _write_frame(self.sock, {"v": WIRE_VERSION, "skill": skill,
                                     "proof": hmac.new(credential, nonce, hashlib.sha256).hexdigest()}, until)
            welcome = _read_frame(self.sock, until)
            if welcome.get("v") != WIRE_VERSION:
                raise KernelProtocol("version")
            if welcome.get("ok") is not True:
                raise KernelUnavailable("auth")
        except (KeyError, ValueError, KernelProtocol) as exc:
            self.close()
            raise KernelUnavailable("handshake") from exc
        except (OSError, KernelUnavailable):          # a timeout stays a timeout (an OSError); the caller classifies it
            self.close()
            raise

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def call(self, op: str, until: Optional[float] = None, **args: Any) -> Any:
        self._id += 1
        try:
            _write_frame(self.sock, {"v": WIRE_VERSION, "id": self._id, "op": op, "args": args}, until)
            reply = _read_frame(self.sock, until)
        except (OSError, KernelUnavailable, KernelProtocol):
            self.close()
            raise
        if reply.get("v") != WIRE_VERSION or type(reply.get("id")) is not int or reply["id"] != self._id:
            # a reply for ANOTHER request (a stale one left on the socket, a crossed id), no id, or another protocol version: never
            # this request's answer, refusal included. Closed, so nothing later on this connection is read against it.
            self.close()
            raise KernelProtocol(CROSSED)
        if reply.get("ok") is True and "result" in reply:
            return reply["result"]
        err = reply.get("error")
        if reply.get("ok") is False and isinstance(err, dict) and isinstance(err.get("code"), str):
            if err["code"] == "refused":
                raise KernelRefused(str(err.get("reason", "")))
            self.close()
            raise KernelProtocol(err["code"][:40])
        self.close()
        raise KernelProtocol("bad reply")


# ---- comparison ---------------------------------------------------------------------------------------------------------------------
#: EVERY code a shadowed write can end in, any verb, and nothing else (``test_results_is_exactly_the_set_of_codes_emitted`` pins both
#: directions). Compared: ``match`` and the mismatch codes (``not_expressible`` included). No verdict: ``kernel_unavailable``, ``timeout``.
#: Not compared: the rest, of which ``no_input`` (no member or no value) and ``shadow_error`` (the shadow's OWN failure,
#: ``reason=hook|worker|code``) are not the kernel's.
RESULTS = ("worker_down", "match", "not_expressible", "kernel_refused_live_did", "kernel_unverified_live_did", "kernel_did_live_refused",
           "item_differs", "list_differs", "schedule_differs", "schedule_unproven", "member_differs", "member_unproven",
           "kernel_unavailable", "timeout", "kernel_malformed", "member_not_in_roster", "dropped_erased", "queue_full", "live_noop",
           "no_input", "shadow_error")
#: FIRST match wins, so "forgotten" comes first: the kernel's "forgotten_action_arguments" (an act citing words a forget shielded) also
#: contains "argument", and a forgotten refusal must never be read as a fixable argument problem.
_REASONS = (("forgotten", "forgotten"), ("no grant", "no_grant"), ("manifest", "manifest"), ("argument", "params"), ("cause", "cause"),
            ("cited", "cause"))


def reason_code(reason: str) -> str:
    """The kernel's fixed refusal text mapped to a closed code; never echoed."""
    low = (reason or "").lower()
    return next((code for needle, code in _REASONS if needle in low), "other")


def for_whom(kernel: dict, member: Optional[str]) -> Optional[str]:
    """None when the receipt is for ``member``; else the result code: ``member_differs`` (it names someone else) or
    ``member_unproven`` (it names no one: a disagreement, never a match). The ONE member check, for every verb. Checked before anything else in a receipt: a match for the wrong person is the worst
    disagreement there is. ``member`` None = the caller did not say (the comparator's own table tests)."""
    if member is None:
        return None
    who = kernel.get("on_behalf_of")
    if not isinstance(who, str):
        return "member_unproven"
    return None if who == member else "member_differs"


def _a_time(value) -> bool:
    """A number the kernel may report as a due instant: an int or a finite float, not a bool, in ``0..DUE_MAX``. The decoder already
    refuses NaN / Infinity on the wire (``_no_constant``); this holds for any caller of ``compare``."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= DUE_MAX)


def compare(live: str, kernel: Optional[dict], sent: dict, verb: str = "list_add", *, member: Optional[str] = None) -> tuple[str, str]:
    """``(result, kernel_state)``. ``live``: added | refused (added = the live write landed, whatever the verb). ``kernel``: the act
    receipt (a dict), ``{"refused": reason}`` or None. ``sent``: the kernel args as proposed; ``member``: who it was for. A ``match`` needs
    EVERY check of the verb (``VERBS``): the receipt is for ``member`` (``for_whom``: ``member_differs`` / ``member_unproven``), the
    read-back names the same WHAT (``item_differs``: the item, the reminder note) and the same WHEN (``schedule_differs``: the
    reminder's absolute due time, the alarm time; ``schedule_unproven`` when the kernel's read-back does not say when, so a reminder at
    the wrong time can never read as a match)."""
    checks = VERBS[verb][1]
    if kernel is None:
        return "kernel_unavailable", "none"
    if "refused" in kernel:
        return ("match" if live == "refused" else "kernel_refused_live_did"), "refused"
    state = "verified" if kernel.get("verified") is True and kernel.get("outcome") == "verified" else (
        "uncertain" if kernel.get("outcome") == "uncertain" else "unverified")
    wrong = for_whom(kernel, member)
    if wrong:
        return wrong, state
    if live == "refused":
        return ("kernel_did_live_refused" if state == "verified" else "match"), state
    if state != "verified":
        return "kernel_unverified_live_did", state
    proof = kernel.get("proof")
    if not isinstance(proof, dict):
        return "kernel_malformed", state
    for pkey, akey, differs, missing in checks:
        if pkey not in proof:
            if missing is not None:
                return missing, state
            continue
        got, want = proof[pkey], sent.get(akey)
        if pkey == "due_at":          # absolute instants: equal within the minute the old-kernel fallback rounds to
            if not _a_time(got):
                return "kernel_malformed", state        # NaN, inf, a string, a bool, an absurd number: not an answer
            if abs(got - want) > DUE_TOLERANCE_S:
                return differs, state
        elif got != want:
            return differs, state
    return "match", state


# ---- counters and the one line per shadowed write ------------------------------------------------------------------------------------
_counts: Counter = Counter()     # result -> n, all verbs (what the list-add tests read)
_vcounts: Counter = Counter()    # (verb, result) -> n
_hops: list = []                 # kernel hop ms of compared writes (bounded)
_lock = threading.Lock()
LIST_CLASSES = ("shopping", "todo", "personal")
#: EVERY value each field of the one line can take (``_finish`` maps anything else to ``other`` / ``none``): the line and the counter
#: keys can never carry a caller's text (``test_marker_text_from_any_caller_argument_never_reaches_a_log_line_or_a_counter``).
SOURCES = ("spoken", "single", "skybridge", "rest", "suggestion", "service", "mcp")      # every call site's ``source=``, on every branch
CLASSES = LIST_CLASSES + ("other", "once", "repeat", "-")       # list classes; reminder / alarm once|repeat; "-" (note_act)
LIVE = ("added", "refused", "noop")
KERNEL_STATES = ("verified", "unverified", "uncertain", "refused", "none")
REASONS = ("-", "no_grant", "manifest", "params", "cause", "forgotten", "other", "timeout", "protocol", CROSSED, "socket", "unavailable",
           "not_provisioned", "circuit", "no_credential", "closed", "auth", "handshake", "hook", "worker", "code", "pending_forget",
           "forgotten_before", "recurring", "no_time", "range", "time", "parse", "old_kernel", "dated")


def counters() -> dict:
    with _lock:
        return dict(_counts)


def counters_by_verb() -> dict:
    """``{verb: {result: n}}`` since start-up: the same outcomes the log lines carry, one per shadowed write."""
    with _lock:
        out: dict = {}
        for (verb, result), n in _vcounts.items():
            out.setdefault(verb, {})[result] = n
        return out


def _tally(result: str, hop_ms: Optional[float] = None, verb: str = "list_add") -> None:
    with _lock:
        _counts[result] += 1
        _vcounts[(verb, result)] += 1
        if hop_ms is not None:
            _hops.append(hop_ms)
            del _hops[:-500]


def _log(verb: str, source: str, cls: str, live: str, kernel_state: str, result: str, reason: str, hop_ms: float, q_ms: float) -> None:
    """ONE line per shadowed write; codes and numbers only (tests/test_os_shadow*.py plant words and check none reach it)."""
    logger.info("OS_SHADOW_ACT verb=%s src=%s cls=%s live=%s kernel=%s result=%s reason=%s hop_ms=%.1f q_ms=%.1f",
                verb, source, cls, live, kernel_state, result, reason, hop_ms, q_ms)


def _finish(verb: str, result: str, *, source: str, cls: str, live: str, kernel_state: str = "none", reason: str = "",
            hop_ms: float = 0.0, q_ms: float = 0.0) -> None:
    """THE one exit of every shadowed write (switch on, verb listed): it writes the write's one line AND counts it, so an outcome
    cannot be counted without a line or logged without a count. Only ``_note`` (a write that ends at the door) and ``_do_act`` (a
    write the worker took) call it, each exactly once, from a ``finally`` (``test_every_outcome_of_every_verb_writes_exactly_one_line``
    and the structure pins). A code outside ``RESULTS`` is itself a bug: written as ``shadow_error reason=code``, never the stray code."""
    if result not in RESULTS:
        result, reason, kernel_state = "shadow_error", "code", "none"
    verb = verb if verb in VERBS else "unknown"                 # closed sets only: a caller's text can never reach the line
    source = source if source in SOURCES else "other"
    cls = cls if cls in CLASSES else "other"
    live = live if live in LIVE else "added"
    kernel_state = kernel_state if kernel_state in KERNEL_STATES else "none"
    reason = (reason or "-") if (reason or "-") in REASONS else "other"
    hop_ms, q_ms = float(hop_ms), float(q_ms)
    _log(verb, source, cls, live, kernel_state, result, reason, hop_ms, q_ms)   # the line first: a counted write has always been written
    _tally(result, hop_ms if kernel_state != "none" else None, verb=verb)


# ---- the worker ---------------------------------------------------------------------------------------------------------------------
_q: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
_worker: Optional[threading.Thread] = None
_worker_guard = threading.Lock()


def _worker_alive() -> bool:
    w = _worker
    return w is not None and w.is_alive()


def _ensure_worker() -> None:
    """Start the worker if it is not running. NEVER called on a turn: only at import (start-up), by ``revive`` and by the forget path
    (an erase, not a turn). One caller starts it; a caller that finds another one starting it never waits on the guard."""
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    if not _worker_guard.acquire(blocking=False):
        return
    try:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="os-shadow", daemon=True)
            _worker.start()
    finally:
        _worker_guard.release()


def revive() -> bool:
    """Bring a dead or never-started worker back, OFF the turn (start-up, a forget, an operator). True if a worker is running after."""
    try:
        _ensure_worker()
    except Exception:  # noqa: BLE001
        pass
    return _worker_alive()


_down_logged = False


def _worker_down() -> None:
    """A turn found no live worker: the proposal is dropped (the hook's one line says ``worker_down``); the turn never starts a thread.
    The warning is said ONCE per process, so a dead worker stands out without a warning per write."""
    global _down_logged
    if not _down_logged:
        _down_logged = True
        logger.warning("OS_SHADOW worker down: proposals dropped (worker_down) until a start-up or a forget revives it")


def _start_at_import() -> None:
    """The worker starts when the service imports this module (start-up, not a turn) if the flag is ``shadow``; the flag is process
    env, so a flip means a restart and the import sees it. Never raises: a failure here only defers the start to the first add."""
    try:
        if mode() == "shadow":
            _ensure_worker()
    except Exception:  # noqa: BLE001
        pass
    try:
        _state_probe()                # start-up, never a turn: "no shadow state" is then answered from memory (see _no_state)
    except Exception:  # noqa: BLE001 - nothing established: the first forget probes
        pass


class _Kernel:
    """The worker's three lazily opened connections and the circuit that keeps a dead kernel from costing a connect per add."""

    def __init__(self) -> None:
        self.conns: dict[str, _Conn] = {}
        self.down_until = 0.0

    def conn(self, skill: str, until: Optional[float] = None) -> _Conn:
        if time.monotonic() < self.down_until:
            raise KernelUnavailable("circuit")
        c = self.conns.get(skill)
        if c is None:
            path, creds = _load_creds()
            if skill not in creds:
                raise KernelUnavailable("no_credential")
            c = self.conns[skill] = _Conn(path, skill, creds[skill], until)
        return c

    def drop(self, skill: str) -> None:
        c = self.conns.pop(skill, None)
        if c is not None:
            c.close()

    def call(self, skill: str, op: str, until: Optional[float] = None, **args: Any) -> Any:
        """``until``: the write's overall deadline (``time.monotonic()``), passed into the connect, the handshake and the call alike.
        ANY unavailability opens the circuit: a failed connect, unreadable credentials, a missing credential, AND a connection the
        kernel accepted then closed or timed out mid-request (else such a kernel costs a reconnect on every write). A protocol fault (a
        reply that is not v1) drops the connection but is not unavailability; the circuit's own refusal never extends it."""
        try:
            return self.conn(skill, until).call(op, until, **args)
        except KernelProtocol:
            self.drop(skill)
            raise
        except (KernelUnavailable, OSError) as exc:
            if not (isinstance(exc, KernelUnavailable) and exc.args[:1] == ("circuit",)):
                for connected in list(self.conns):
                    self.drop(connected)
                self.down_until = time.monotonic() + CIRCUIT_S
            raise


def _run() -> None:
    kernel = _Kernel()
    while True:
        job = _q.get()
        if job[0] == "stop":
            for skill in list(kernel.conns):
                kernel.drop(skill)
            return
        if _pending_members is None or _pending_members:
            # BEFORE EVERY job while anything is parked (or this process has not read the park file yet: the first job after a start
            # reads it, so a park from before a restart is replayed first). Known-empty costs one global read per job, no file I/O.
            # Its own try: a replay that fails must never cost the job behind it (an add keeps its one line).
            try:
                _replay_pending(kernel, force=job[0] in ("forget", "replay"))
            except Exception as exc:  # noqa: BLE001 - kept pending, tried again before the next job
                logger.warning("OS_SHADOW replay error=%s", type(exc).__name__)
        if _erase_members is None or _erase_members:
            _kick_erases()            # the same replay finishes what production still holds for a member the kernel has confirmed
        if job[0] == "replay":
            continue                  # a nudge: the replay above was the whole job
        try:
            if job[0] == "act":
                _do_act(kernel, *job[1:])
            else:
                _do_forget(kernel, job[1], job[2], job[3], job[4])
        except Exception as exc:  # noqa: BLE001 - the worker never dies and never raises into a turn
            logger.warning("OS_SHADOW worker error=%s", type(exc).__name__)
            if job[0] == "forget" and not job[4].done():
                job[4].set_exception(exc)


def _do_act(kernel: _Kernel, verb: str, user_id: str, source: str, cls: str, live: str, raw: dict, gen: int,
            queued_at: float, wall_at: float) -> None:
    """One queued write, end to end: ONE line, whatever happens (an unexpected error is ``shadow_error reason=worker``)."""
    started = time.perf_counter()
    out: dict = {"result": "shadow_error", "reason": "worker"}
    try:
        out = _act_outcome(kernel, verb, user_id, live, raw, gen, started, wall_at, queued_at)
    except Exception as exc:  # noqa: BLE001 - counted and written below; the worker never dies
        logger.warning("OS_SHADOW worker error=%s", type(exc).__name__)
    finally:
        _finish(verb, out.get("result", "shadow_error"), source=source, cls=cls, live=live,
                kernel_state=out.get("kernel_state", "none"), reason=out.get("reason", ""), hop_ms=out.get("hop_ms", 0.0),
                q_ms=(started - queued_at) * 1000)


def _act_outcome(kernel: _Kernel, verb: str, user_id: str, live: str, raw: dict, gen: int, started: float, wall_at: float,
                 queued_at: float = 0.0) -> dict:
    """What the kernel made of one write, as ``{"result", "kernel_state", "reason", "hop_ms"}``; every return names a result. The
    whole write runs on ONE budget (``JOB_DEADLINE_S`` from when the kernel is first called) that every socket operation draws on: a
    stall anywhere ends it as ``timeout`` within the budget."""
    if user_id not in roster():
        return {"result": "member_not_in_roster"}
    if user_erase_gate.generation(user_id) != gen or user_erase_gate.in_progress(user_id):
        return {"result": "dropped_erased"}
    if _pending_members is None or user_id in _pending_members:
        # a forget for this member is parked and has not reached the kernel (the replay just before this job failed): an add must
        # never reach the kernel ahead of the member's forget. None = the park file could not be read: fail closed for everyone.
        return {"result": "dropped_erased", "reason": "pending_forget"}
    if queued_at < _forgotten_at.get(user_id, float("-inf")):
        # stamped at the door BEFORE a forget of this member that skipped the queue (parked: the queue was full) and so reached the
        # kernel AHEAD of it: sending it now would re-create what the forget removed. A forget that went through the queue needs no
        # such check: one FIFO worker sends every add stamped before it first, and the forget then removes them.
        return {"result": "dropped_erased", "reason": "forgotten_before"}
    try:
        sent, why = kernel_args(verb, raw, wall_at)
    except Exception:  # noqa: BLE001 - a field the client could not read is counted against the kernel, never silently lost
        sent, why = None, "parse"
    if sent is None:              # the live write is something the kernel's verb cannot say: a disagreement, never sent
        return {"result": "not_expressible", "reason": why}
    until = time.monotonic() + JOB_DEADLINE_S
    action = VERBS[verb][0]
    result_kernel: Optional[dict] = None
    reason, result = "", ""
    old_kernel = False
    try:
        ev = kernel.call(SKILL_VOICE, "publish", until, kind="utterance", subject=user_id, payload={"text": cause_text(verb)})
        seq = ev.get("seq") if isinstance(ev, dict) else None
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise KernelProtocol("no seq")
        try:
            rec = kernel.call(SKILL_BRAIN, "act", until, action=action, on_behalf_of=user_id, args={**sent, "asked": seq}, caused_by=[seq])
        except KernelRefused as exc:
            if not ("due_at" in sent and UNKNOWN_ARGUMENT in (exc.reason or "").lower()):
                raise
            old_kernel = True             # an older kernel: ONE retry with the minutes counted from now (the send), never from the commit
            minutes = int(round((sent["due_at"] - _wall()) / 60.0))
            if not 1 <= minutes <= REMIND_MAX_MIN:
                return {"result": "not_expressible", "reason": "range"}
            rec = kernel.call(SKILL_BRAIN, "act", until, action=action, on_behalf_of=user_id,
                              args={"note": sent["note"], "in_minutes": minutes, "asked": seq}, caused_by=[seq])
        if not isinstance(rec, dict) or not isinstance(rec.get("verified"), bool) or not isinstance(rec.get("outcome"), str):
            raise KernelProtocol("bad receipt")
        result_kernel = rec
    except KernelRefused as exc:
        result_kernel, reason = {"refused": exc.reason}, reason_code(exc.reason)
    except (socket.timeout, TimeoutError):
        result, reason = "timeout", "timeout"
    except KernelUnavailable as exc:
        result, reason = "kernel_unavailable", str(exc)[:24].replace(" ", "_") or "unavailable"
    except KernelProtocol as exc:
        result, reason = "kernel_malformed", (CROSSED if exc.args[:1] == (CROSSED,) else "protocol")
    except OSError as exc:
        result, reason = ("timeout", "timeout") if isinstance(exc, socket.timeout) else ("kernel_unavailable", "socket")
    hop_ms = (time.perf_counter() - started) * 1000
    kstate = "none"
    if not result:
        result, kstate = compare(live, result_kernel, sent, verb, member=user_id)
    if old_kernel and not reason:
        reason = "old_kernel"             # the report counts which kernels are old
    return {"result": result, "kernel_state": kstate, "reason": reason, "hop_ms": hop_ms}


def kernel_args(verb: str, raw: dict, wall_at: float) -> tuple[Optional[dict], str]:
    """The live write's fields as the kernel verb's args, or ``(None, reason_code)`` when the verb cannot express it. Runs on the
    WORKER (any parsing or import happens off the turn). ``wall_at``: the wall clock when the live write committed."""
    if verb in ("list_add", "list_remove"):
        return {"item": raw["item"], "list": raw["list"]}, ""
    if verb == "alarm_create":
        if (raw.get("repeat") or "once") != "once":
            return None, "recurring"         # alarm.set is one time, once: a repeating alarm is something the kernel cannot say
        if raw.get("dated"):
            return None, "dated"             # alarm.set says a time of day only (its next occurrence): a one-off for a LATER date is not that
        hhmm = str(raw.get("time") or "")
        if len(hhmm) != 5 or hhmm[2] != ":" or not (hhmm[:2] + hhmm[3:]).isdigit() or int(hhmm[:2]) > 23 or int(hhmm[3:]) > 59:
            return None, "time"
        return {"time": hhmm}, ""
    # reminder_create: the kernel's remind.set is ONE-SHOT. The due instant is absolute (from the row and the COMMIT time) and is what
    # is sent (``due_at``), so a job that waited in the queue still asks for that instant.
    if raw.get("recurring"):
        return None, "recurring"
    if not raw.get("due_time"):
        return None, "no_time"           # production fires a date-only reminder at the household default time; the verb has no "default"
    from datetime import datetime, timezone

    from proactive.triggers.reminder_scan import _parse_due_time, build_run_at     # production's own due instant, never a copy

    hm = _parse_due_time(str(raw["due_time"]))
    run_at = build_run_at(raw.get("due_date"), hm[0], hm[1], datetime.fromtimestamp(wall_at, timezone.utc)) if hm else None
    if run_at is None:
        return None, "time"
    return {"note": raw["note"], "due_at": int(round(run_at.timestamp()))}, ""


# ---- the hook -----------------------------------------------------------------------------------------------------------------------
def list_class(list_type: Optional[str]) -> str:
    lt = (list_type or "").strip().lower()
    return lt if lt in LIST_CLASSES else "other"


_OFF, _QUEUED = "off", "queued"      # hook outcomes that are NOT results: off (nothing touched) / the worker writes the line


def _note(verb: str, user_id: Optional[str], source: str, landed: bool, build) -> None:
    """THE hook, for every verb: a live write of ``verb`` committed (call AFTER the commit). ``build()`` returns ``(cls, raw)``, or
    ``(cls, None)`` when the write carries no value to propose; it runs inside the guard, so a bad argument is counted, never raised.
    Never raises, never blocks, O(1); with the switch off, or the verb not in ``ZOE_OS_SHADOW_VERBS``, nothing is touched.

    Otherwise EVERY call ends in exactly one ``OS_SHADOW_ACT`` line: written here (``_finish``, in the ``finally``) when the write ends
    at the door, or by the worker when it was queued. An unexpected error is ``shadow_error reason=hook``."""
    outcome, cls = "shadow_error", "-"
    try:
        outcome, cls = _door(verb, user_id, source, landed, build)
    except Exception:  # noqa: BLE001 - shadow must never break the turn; counted and written below
        outcome = "shadow_error"
    finally:
        if outcome not in (_OFF, _QUEUED):
            try:
                _finish(verb if verb in VERBS else "list_add", outcome, source=str(source), cls=cls,
                        live="noop" if outcome == "live_noop" else "added", reason="hook" if outcome == "shadow_error" else "")
            except Exception:  # noqa: BLE001
                pass


def _door(verb: str, user_id: Optional[str], source: str, landed: bool, build) -> tuple:
    """The hook's decision: ``(_OFF | _QUEUED | the result code the write ends in here, cls)``. Every return names one."""
    if not wants(verb):
        return _OFF, "-"
    _note_loop()
    cls, raw = build()
    if not landed:
        return "live_noop", cls
    uid = (user_id or "").strip()
    if not uid or raw is None:
        return "no_input", cls
    if user_erase_gate.in_progress(uid):
        return "dropped_erased", cls
    if not _worker_alive():           # never Thread.start() on a turn: drop, count, revive off the turn
        _worker_down()
        return "worker_down", cls
    try:
        _q.put_nowait(("act", verb, uid, source, cls, "added", raw, user_erase_gate.generation(uid), time.perf_counter(), time.time()))
    except queue.Full:
        return "queue_full", cls
    return _QUEUED, cls


def note_act(verb: str, user_id: Optional[str], *, source: str, raw: dict, cls: str = "-", landed: bool = True) -> None:
    """A live write of ``verb`` committed, with its fields already in ``raw`` (``kernel_args`` turns them into the kernel's args)."""
    _note(verb, user_id, source, landed, lambda: (cls, raw))


def _text(value: Optional[str]) -> str:
    return (value or "").strip()


def _list_raw(list_type: Optional[str], list_name: Optional[str], item: Optional[str]) -> tuple:
    text = _text(item)
    return list_class(list_type), ({"item": text, "list": (list_name or list_type or "shopping").strip() or "shopping"} if text else None)


def note_add(user_id: Optional[str], *, source: str, list_type: Optional[str], list_name: Optional[str], item: Optional[str],
             landed: bool = True) -> None:
    """A list add finished (call AFTER the live commit). ``landed`` False = nothing was written (already on the list, replayed)."""
    _note("list_add", user_id, source, landed, lambda: _list_raw(list_type, list_name, item))


def note_list_remove(user_id: Optional[str], *, source: str, list_type: Optional[str], list_name: Optional[str],
                     item: Optional[str]) -> None:
    """A list item was removed (call AFTER the commit). The kernel's post-condition is "that item is not on the list"."""
    _note("list_remove", user_id, source, True, lambda: _list_raw(list_type, list_name, item))


def note_reminder_create(user_id: Optional[str], *, source: str, note: Optional[str], due_date: Optional[str],
                         due_time: Optional[str], recurring: bool) -> None:
    """A reminder row committed (``reminder_service.create_reminder_record``): its stored fields, parsed on the worker."""
    _note("reminder_create", user_id, source, True, lambda: (
        "repeat" if recurring else "once",
        {"note": _text(note), "due_date": due_date, "due_time": due_time, "recurring": bool(recurring)} if _text(note) else None))


def note_alarm_create(user_id: Optional[str], *, source: str, time_hhmm: Optional[str], repeat: Optional[str],
                      dated: Any = False) -> None:
    """An alarm row committed (``alarms.create_alarm``). The kernel's alarm is one time per person: the repeat rule rides ``cls``.
    ``dated`` may be a zero-argument callable: it is evaluated ONLY when the shadow is on (inside the lazy payload), so with the flag
    off the caller pays no date / timezone work after its commit.
    ``dated``: a one-off set for a date that is NOT the time's next occurrence - the kernel's verb carries no date, so proposing the
    time alone could "match" a kernel alarm on another day; it is counted ``not_expressible reason=dated`` and never sent."""
    _note("alarm_create", user_id, source, True, lambda: ("once" if (repeat or "once") == "once" else "repeat",
                                                          {"time": str(time_hhmm or ""), "repeat": repeat or "once",
                                                           "dated": bool(dated() if callable(dated) else dated)}))


# ---- the lists door: the kernel DECIDES a list change (ZOE_OS_LISTS=kernel) ----------------------------------------------------------
#: The wire verbs of ``labs/zoe-os/zoe_os/lists.py``. Ids only cross the socket: never an item's text or a list's name.
DOOR_VERBS = ("list.create", "list.rename", "list.delete", "list.share", "item.add", "item.remove", "item.tick", "item.edit")
ASK_DEADLINE_S = 0.150            # the whole ask, queue wait included; past it the change proceeds ``not_checked`` (ZOE_OS_LISTS_ASK_MS)
DOOR_QUEUE_MAX = 256
ALLOW, REFUSE, NOT_CHECKED = "allow", "refuse", "not_checked"
#: every reason an ask can end in (closed: the log line carries nothing else). The kernel's own refusal codes first.
DOOR_REFUSALS = ("unknown_person", "not_owner", "adults_only", "not_in_manifest", "forgotten")
DOOR_REASONS = ("-", *DOOR_REFUSALS, "other", "timeout", "unavailable", "circuit", "not_provisioned", "no_credential", "protocol",
                "erasing", "queue_full", "worker", "no_actor", "no_list", "lookup", "bad_verb")
_door_q: "queue.Queue" = queue.Queue(maxsize=DOOR_QUEUE_MAX)
_door_worker: Optional[threading.Thread] = None
_door_guard = threading.Lock()


def ask_deadline_s() -> float:
    try:
        import typed_env

        ms = int(typed_env.flag(LISTS_ASK_MS_FLAG))
        return ms / 1000.0 if 1 <= ms <= 5000 else ASK_DEADLINE_S
    except Exception:  # noqa: BLE001
        return ASK_DEADLINE_S


def _door_log(verb: str, decision: str, reason: str, ask_ms: float) -> None:
    """ONE line per decided list change; closed codes and a number only."""
    logger.info("ZOE_OS_ACT area=lists verb=%s decision=%s reason=%s ask_ms=%.1f", verb if verb in DOOR_VERBS else "unknown",
                decision if decision in (ALLOW, REFUSE, NOT_CHECKED) else NOT_CHECKED,
                (reason or "-") if (reason or "-") in DOOR_REASONS else "other", float(ask_ms))
    with _lock:
        _door_counts[decision] += 1


_door_counts: Counter = Counter()


def door_counters() -> dict:
    with _lock:
        return dict(_door_counts)


def door_unchecked(verb: str, reason: str) -> tuple:
    """A list change the door could not put to the kernel at all (no actor, the list row could not be read): one line, and it proceeds."""
    _door_log(verb, NOT_CHECKED, reason, 0.0)
    return NOT_CHECKED, reason, None


def _refusal_code(reason: str) -> str:
    text = (reason or "").strip()
    if text in DOOR_REFUSALS:
        return text
    return "forgotten" if "forgotten" in text.lower() else "other"


def _door_parked(user_id: str) -> bool:
    """Is a forget of this member parked (not yet confirmed by the kernel)? Then nothing new may be recorded about them. An
    unreadable park file is "yes" for everyone (the change still proceeds; it is only not recorded)."""
    members = _pending_members
    if members is None:
        try:
            if not _pending_path().parent.is_dir():
                return False
            members = _pending_read_locked()
        except Exception:  # noqa: BLE001
            return True
    return user_id in members


def _door_ask(kernel: "_Kernel", verb: str, user_id: str, args: dict, until: float) -> tuple:
    """On the door thread: ``(decision, reason, ask id)``. Only the kernel's own refusal is a refusal; every fault is not_checked."""
    try:
        if user_erase_gate.in_progress(user_id) or _door_parked(user_id):
            return NOT_CHECKED, "erasing", None
        out = kernel.call(SKILL_BRAIN, "list_ask", until, verb=verb, person=user_id, **args)
        ask = out.get("ask") if isinstance(out, dict) else None
        if type(ask) is not int:
            raise KernelProtocol("no ask id")
        return ALLOW, "", ask
    except KernelRefused as exc:
        return REFUSE, _refusal_code(exc.reason), None
    except (socket.timeout, TimeoutError):
        return NOT_CHECKED, "timeout", None
    except KernelUnavailable as exc:
        reason = str(exc)[:24].replace(" ", "_")
        return NOT_CHECKED, reason if reason in DOOR_REASONS else "unavailable", None
    except KernelProtocol:
        return NOT_CHECKED, "protocol", None
    except OSError as exc:
        return NOT_CHECKED, "timeout" if isinstance(exc, socket.timeout) else "unavailable", None
    except Exception:  # noqa: BLE001 - the door thread never dies and never raises into a turn
        return NOT_CHECKED, "worker", None


def _door_run() -> None:
    """The door's own thread and its own connection (a ``_Conn`` is one caller's): asks and reports, in order."""
    kernel = _Kernel()
    while True:
        job = _door_q.get()
        if job[0] == "stop":
            for skill in list(kernel.conns):
                kernel.drop(skill)
            return
        try:
            if job[0] == "ask":
                _, verb, user_id, args, until, fut = job
                out = _door_ask(kernel, verb, user_id, args, until)
                if not fut.done():
                    fut.set_result(out)
            else:                     # a report: best effort, bounded; an ask left open is "uncertain" at the kernel's next start
                kernel.call(SKILL_BRAIN, "list_report", time.monotonic() + OP_TIMEOUT_S, ask=job[1], outcome=job[2])
        except Exception as exc:  # noqa: BLE001
            logger.debug("ZOE_OS door error=%s", type(exc).__name__)


def _ensure_door() -> bool:
    global _door_worker
    with _door_guard:
        if _door_worker is None or not _door_worker.is_alive():
            _door_worker = threading.Thread(target=_door_run, name="os-lists-door", daemon=True)
            _door_worker.start()
    return True


async def ask_list(verb: str, user_id: str, *, panel: bool = False, list_id: Optional[str] = None, item_ids=(),
                   owner: Optional[str] = None, household: bool = False, turn_id: Optional[str] = None) -> tuple:
    """Ask the kernel whether ``user_id`` may do ``verb`` to a list: ``(allow | refuse | not_checked, reason code, ask id | None)``.

    The caller awaits this BEFORE the change, so it has a hard deadline (``ask_deadline_s``, queue wait included) and all socket
    work is on the door thread, never the event loop. A refusal is the kernel's own and is final. Anything else that goes wrong
    (kernel down, circuit open, slow, a reply that is not v1) is ``not_checked``: the caller makes the change as before. Never
    raises. Writes the change's one ``ZOE_OS_ACT`` line. Call only when ``lists_on()``."""
    started = time.perf_counter()
    out: tuple = (NOT_CHECKED, "worker", None)
    try:
        uid = (user_id or "").strip()
        if verb not in DOOR_VERBS:
            out = (NOT_CHECKED, "bad_verb", None)
        elif not uid and not panel:
            out = (NOT_CHECKED, "no_actor", None)
        else:
            args: dict = {"visibility": "household" if household else "private"}
            if panel:
                args["via"] = "panel"
            for key, value in (("list", list_id), ("owner", owner), ("turn", turn_id)):
                if value:
                    args[key] = str(value)
            items = [str(i) for i in item_ids if i][:16]
            if items:
                args["items"] = items
            if _door_worker is None or not _door_worker.is_alive():
                await asyncio.to_thread(_ensure_door)             # a thread start is never on the loop
            deadline = ask_deadline_s()
            fut: "concurrent.futures.Future" = concurrent.futures.Future()
            try:
                _door_q.put_nowait(("ask", verb, uid or "panel", args, time.monotonic() + deadline, fut))
                out = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=deadline)
            except queue.Full:
                out = (NOT_CHECKED, "queue_full", None)
            except (asyncio.TimeoutError, TimeoutError):
                out = (NOT_CHECKED, "timeout", None)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - never an exception to the list change
        out = (NOT_CHECKED, "worker", None)
    _door_log(verb, out[0], out[1], (time.perf_counter() - started) * 1000)
    return out


def report_list(ask: Optional[int], outcome: str) -> None:
    """Tell the kernel how an allowed change ended (``done`` | ``failed``). After the commit; never blocks, never raises."""
    try:
        if type(ask) is int and outcome in ("done", "failed"):
            _door_q.put_nowait(("report", ask, outcome))
    except Exception:  # noqa: BLE001 - dropped: the kernel closes the ask as "uncertain" at its next start
        pass


# ---- forget -------------------------------------------------------------------------------------------------------------------------
def _pending_path() -> Path:
    return state_dir() / "pending-forgets.json"


class PendingUnreadable(OSError):
    """``pending-forgets.json`` exists but is not a list of ids: never overwritten (that would drop the forgets it holds)."""


_pending_mutex = threading.Lock()
#: the parked members as this process last saw them in the park file, kept in step with it under the same lock (``_pending_add``,
#: ``_replay_pending``): None = not read yet in this process / unreadable, so the worker reads the file before its next job. A park
#: written by ANOTHER process is seen at this worker's next start (zoe-data is the only writer in production).
_pending_members: Optional[frozenset] = None
#: member -> ``time.perf_counter()`` when a forget of theirs was PARKED instead of queued: every add stamped (``queued_at``) before it is
#: dropped (``forgotten_before``), because it may still sit in the queue ahead of the replay. In memory only: a restart empties the queue.
_forgotten_at: dict = {}


class _pending_locked:
    """The parked-forget file is read-modify-written only under BOTH a thread lock and an ``flock`` on a sibling lock file, so two
    forgets (two threads, or zoe-data and a second process) never overwrite each other. Held only for one read or one write."""

    def __init__(self, create: bool = False) -> None:
        self.create, self.fd = create, None       # create: a WRITER of new data (a park); it makes the directory, and settles it

    def __enter__(self):
        import fcntl

        _pending_mutex.acquire()
        try:
            path = _pending_path()
            if self.create:
                ensure_state_dir()        # empty directory + marker + the settle wait, before any data file (never on the loop)
            elif not path.parent.is_dir():
                return self               # a reader with no directory: nothing is parked, and reading never makes the directory
            self.fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except BaseException:
            _pending_mutex.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                os.close(self.fd)         # closing drops the flock
        finally:
            _pending_mutex.release()
        return False


def _pending_read() -> list:
    """The parked members. Missing file = none; a file that is there but unreadable RAISES ``PendingUnreadable`` (fail closed)."""
    path = _pending_path()
    try:
        text = path.read_text("utf-8")
    except FileNotFoundError:
        return []
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise PendingUnreadable("pending-forgets.json is not json") from exc
    if not isinstance(raw, list):
        raise PendingUnreadable("pending-forgets.json is not a list")
    return [str(x) for x in raw]


def _pending_write(members: list) -> None:
    """Atomic and durable: a unique temp file, fsync, rename, fsync of the directory. Raises on any failure (never swallowed)."""
    _durable_write(_pending_path(), sorted(set(members)))


def _durable_write(path: Path, payload) -> None:
    """The park's ONE writer (``pending-forgets.json`` and ``pending-erases.json``): an empty payload removes the file."""
    members = payload
    if not members:
        try:
            path.unlink()
        except FileNotFoundError:
            return                    # nothing was parked: nothing to make durable
        _fsync_dir(path.parent)       # the unlink is a directory change: durable too, or a crash could bring the file back
        return
    _state_installed()                # this IS shadow state: no forget may skip the kernel from here on
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    dfd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


#: member -> ``time.perf_counter()`` of its LATEST park in this process (a newer request for the same member): ``_pending_done`` keeps a
#: member whose park is newer than the replay's send. A park by another process is older by definition (seen at this worker's start).
_parked_at: dict = {}


def _pending_add(member: str) -> None:
    """Park a forget for a later replay. RAISES if it cannot be made durable: the caller then fails the erase closed. Blocking (an
    ``flock`` wait and two fsyncs): never call it on the event loop (``_forget`` hands it to an executor)."""
    global _pending_members
    with _pending_locked(create=True):
        members = _pending_read() + [member]
        _pending_write(members)
        _pending_members = frozenset(members)
        _parked_at[member] = time.perf_counter()


def _pending_done(members) -> None:
    """Drop the members a replay confirmed; re-read under the lock so a forget parked WHILE the replay ran is kept. ``members``: a
    set, or a ``{member: time the replay SENT its forget}`` map, so a member parked AGAIN after that send (a newer unconfirmed forget
    of the same member) stays parked: only the request the kernel confirmed is cleared."""
    sent = members if isinstance(members, dict) else {m: float("inf") for m in members}
    with _pending_locked():
        _pending_write([m for m in _pending_read() if m not in sent or _parked_at.get(m, float("-inf")) > sent[m]])


def _replay_pending(kernel: _Kernel, force: bool = True) -> bool:
    """Parked forgets (the flag was off, the kernel was down, the queue was full) are re-sent (whole member: the safe over-forget)
    before the next job, every job, until none is left. True once nothing is pending; ``_pending_members`` follows the file.
    ``force`` False (the replay ahead of an add): an open circuit is respected, so a dead kernel costs no connect per add; the adds of
    a parked member are dropped meanwhile (``_add_outcome``), never sent ahead of the forget."""
    global _pending_members
    try:
        with _pending_locked():
            todo = _pending_read()
            _pending_members = frozenset(todo)
    except OSError:
        _pending_members = None       # unreadable: kept as it is, read again before the next job (adds fail closed meanwhile)
        return False
    if not todo:
        return True
    if not force and time.monotonic() < kernel.down_until:
        return False
    kernel.down_until = 0.0           # a forget never waits out the circuit an earlier failed add opened
    done: dict = {}
    for member in todo:
        sent = time.perf_counter()
        try:
            _kernel_forget(kernel, member, None, None)
            done[member] = sent
        except Exception:  # noqa: BLE001
            pass
    try:
        if done:
            _pending_done(done)
        with _pending_locked():
            left = _pending_read()
            _pending_members = frozenset(left)
        return not left
    except OSError:
        _pending_members = None
        return False


# ---- parked PRODUCTION erases: what a forget could not erase because the kernel did not confirm ------------------------------------------
# ``pending-erases.json`` beside the parked forgets, under the SAME lock and writer: ``{member: {"rows": [...], "turns": [...],
# "messages": [...], "observations": [...], "threads": [...], "notes": [...], "values": [...], "offers": [...]}}``.
# IDS ONLY, never a word. It is written at
# the erasers' own shadow step (``park_unconfirmed`` in ``MemoryService.erase_rows`` and ``exact_words._shadow_first``), so
# no caller can forget to park. The worker's replay that confirms a member's parked kernel forget then has the service finish these
# (``turn_forget.finish_parked``) with no further request from the member; a finish that fails leaves the entry for the next job; a
# member delete drops the member's entry.
ERASE_KINDS = ("rows", "turns", "messages", "observations", "threads", "notes", "values", "offers")
#: members with a parked erase as this process last saw the file (None = not read yet: the worker reads it before its next job)
_erase_members: Optional[frozenset] = None
_loop: Optional[asyncio.AbstractEventLoop] = None    # the service's loop, noted by the hook and the forget: where a finish is run
_finishing = False


def _note_loop() -> None:
    global _loop
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        pass


def _erases_path() -> Path:
    return state_dir() / "pending-erases.json"


def _erases_read() -> dict:
    """Missing file = none; a file that is there but not the documented shape RAISES ``PendingUnreadable`` (never overwritten)."""
    try:
        text = _erases_path().read_text("utf-8")
    except FileNotFoundError:
        return {}
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise PendingUnreadable("pending-erases.json is not json") from exc
    if not isinstance(raw, dict) or not all(isinstance(v, dict) for v in raw.values()):
        raise PendingUnreadable("pending-erases.json is not a map")
    return {str(m): {k: [str(i) for i in (e.get(k) or []) if i] for k in ERASE_KINDS} for m, e in raw.items()}


def _erases_store(todo: dict) -> None:
    global _erase_members
    todo = {m: {k: e[k] for k in ERASE_KINDS if e.get(k)} for m, e in todo.items() if any(e.get(k) for k in ERASE_KINDS)}
    _durable_write(_erases_path(), todo)
    _erase_members = frozenset(todo)


def park_erase(member: str, *, rows=(), turns=(), messages=(), observations=(), threads=(), notes=(), values=(), offers=()) -> None:
    """Record, durably, what is left to erase for ``member`` (merged into what is already parked). BLOCKING and it RAISES when it
    cannot be made durable: a thread, never the loop."""
    add = dict(rows=rows, turns=turns, messages=messages, observations=observations, threads=threads, notes=notes, values=values, offers=offers)
    with _pending_locked(create=True):
        todo = _erases_read()
        entry = todo.setdefault(str(member), {k: [] for k in ERASE_KINDS})
        for k in ERASE_KINDS:
            entry[k] = list(dict.fromkeys([*entry[k], *(str(i) for i in (add[k] or ()) if i)]))
        _erases_store(todo)


def parked_erases() -> dict:
    """What is parked, by member. BLOCKING (a thread)."""
    global _erase_members
    with _pending_locked():
        todo = _erases_read()
        _erase_members = frozenset(todo)
        return todo


def erases_done(member: str, entry: Optional[dict] = None) -> None:
    """Drop what was finished: the ids in ``entry`` (anything parked for the member SINCE stays), or the member's whole entry when
    ``entry`` is None (a member delete, which erased everything they had). BLOCKING (a thread); raises when it cannot be written."""
    with _pending_locked():
        todo = _erases_read()
        have = todo.pop(str(member), None) or {}
        if entry is not None:
            todo[str(member)] = {k: [i for i in have.get(k, []) if i not in set(str(x) for x in (entry.get(k) or ()))] for k in ERASE_KINDS}
        _erases_store(todo)


# ---- PARK FIRST: the whole plan of a multi-step forget is durable before its first destructive step --------------------------------------
# A forget is several erasers in a row (hide the rows, erase them, the index turn, the indexed words, the transcript flag ...), each
# with its own shadow check. An eraser that is refused can only park what IT was about to erase: when an earlier step has already
# destroyed the evidence a later step is found by (the index row the transcript turn is looked up from), or the process dies between
# two steps, nothing durable would remember the rest. So every multi-step forget calls ``plan_erase`` BEFORE its first destructive
# step with every id the whole sequence will touch, and ``Plan.ok`` only when every step has confirmed. Anything else (a refusal, an
# exception, a crash) leaves the plan parked and the shadow's replay finishes it (``turn_forget.finish_parked``, idempotent).
PLAN_HOLD_S = 60.0
#: member -> ``_mono()`` until which a forget of THIS process is running that member's plan itself: the replay's finish leaves the
#: member alone meanwhile (it would only do the same erases concurrently). It expires by itself: a forget that returned without
#: ``ok`` (refused, raised) is then finished by the replay.
_planning: dict = {}
#: member -> how many parked ids the replay's last finish could NOT erase (they stay parked): ``parked_unfinished`` is their total
_left: dict = {}


def parked_unfinished() -> int:
    """How many parked ids the replay tried and could not erase (still parked). 0 = nothing is stuck."""
    return sum(_left.values())


class Plan:
    def __init__(self, user_id: str, entry: Optional[dict]) -> None:
        self.user_id, self.entry = user_id, entry
        self.has_failed = False

    def failed(self) -> None:
        """A step failed: keep every planned id for replay, even if the caller later calls ok. ONLY a real plan (something was
        parked: the shadow is in play) can fail: with the shadow off and no state there is no plan, ``has_failed`` stays False and
        every caller behaves exactly as it did before the shadow existed (the reply included)."""
        if self.entry is not None:
            self.has_failed = True

    def note_erase(self, *, result) -> None:
        """Keep the plan parked when physical erasure did not confirm."""
        if isinstance(result, dict) and isinstance(result.get("physical"), dict) and result["physical"].get("ok") is False:
            self.failed()

    async def ok(self) -> None:
        """Every step confirmed: the plan's ids leave the park. Never raises (an id left behind names nothing that still exists,
        and the next replay drops it)."""
        if self.has_failed:
            return
        entry, self.entry = self.entry, None
        if entry is None:
            return
        try:
            await asyncio.to_thread(erases_done, self.user_id, entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OS_SHADOW finished plan not cleared code=%s", type(exc).__name__)
        _planning.pop(self.user_id, None)

    drop = ok        # the forget stopped BEFORE its first step (the store refused to hide the row): nothing began, nothing to finish


def in_play() -> bool:
    """Is the shadow part of this forget at all? The switch is on, or shadow state exists (a memory read: the same cached answer
    ``plan_erase`` starts from; no disk, no thread). False = behave exactly as before the shadow existed."""
    return mode() == "shadow" or not _no_state()


async def plan_erase(user_id: str, *, rows=(), words=None) -> Plan:
    """Park the whole plan of a multi-step forget (see above) and return its handle. ``rows``: memory row ids. ``words``: an async
    callable returning ids by ``ERASE_KINDS`` (``exact_words.words_plan`` plus the caller's other stores);
    ``rows`` may be an async callable too. Both are CALLED ONLY when there is shadow state
    to park beside: with no state directory this is a memory read and nothing else (no thread, no disk, no lookup). RAISES
    ``ShadowForgetUnavailable`` when the plan cannot be made durable: the forget must not start (fail closed, like every park)."""
    if mode() != "shadow":
        if _no_state() or not await asyncio.to_thread(_state_probe):
            return Plan(user_id, None)
    try:
        if callable(rows):            # an async lookup of more row ids (a curated row's earlier revisions): only when parking
            rows = await rows()
        found = {**dict.fromkeys(ERASE_KINDS, ()), **((await words()) if words is not None else {})}   # every kind present: no lookups below
        rows = list(rows)
        if rows:
            import memory_value_registry
            found["values"] = [*(found["values"] or ()), *(await memory_value_registry.erase_for_rows(user_id, rows, plan_only=True) or ())]
        entry = {k: list(dict.fromkeys(str(i) for i in (rows if k == "rows" else found[k] or ()) if i)) for k in ERASE_KINDS}
        if not any(entry.values()):
            return Plan(user_id, None)
        _planning[user_id] = _mono() + PLAN_HOLD_S
        await asyncio.to_thread(lambda: park_erase(user_id, **entry))
    except Exception as exc:  # noqa: BLE001
        _planning.pop(user_id, None)
        raise ShadowForgetUnavailable(f"plan not parked ({type(exc).__name__})") from exc
    return Plan(user_id, entry)


async def park_unconfirmed(user_id: str, *, rows=(), turns=(), messages=()) -> None:
    """An eraser's shadow step was not confirmed: park what that eraser was about to erase (off the loop). Never raises: the eraser
    raises its own failure either way, and an erase that could not be parked is still never reported as done."""
    try:
        if rows or turns or messages:
            await asyncio.to_thread(lambda: park_erase(user_id, rows=rows, turns=turns, messages=messages))
    except Exception as exc:  # noqa: BLE001
        logger.error("OS_SHADOW unconfirmed erase NOT parked code=%s", type(exc).__name__)


async def nudge() -> None:
    """Ask the worker to replay now (the parked kernel forgets, then the parked erases) without waiting for the next write: called by
    the forget retry pass (``memory_forget_cascade.retry_pending``: every night per member, and on a topic or complete entity
    forget). Costs nothing - no thread, no disk - when there is certainly no shadow state, or this process knows nothing is parked.
    Never raises, never waits for the replay."""
    try:
        if _no_state() or (_pending_members is not None and not _pending_members and _erase_members is not None and not _erase_members):
            return
        _note_loop()
        if not await asyncio.to_thread(_state_probe):
            return
        await asyncio.to_thread(_ensure_worker)
        _q.put_nowait(("replay",))
    except Exception:  # noqa: BLE001 - a full queue has jobs in it, and each of those replays first anyway
        pass


async def drop_parked_erases(user_id: str) -> None:
    """A member delete finished: nothing of theirs is left to erase, so their parked entry (ids) goes too. No disk and no thread
    thread when there is certainly no state directory (the forget's own rule). Raises when the entry could not be removed."""
    if _no_state():
        return
    if not await asyncio.to_thread(_state_probe):
        return
    await asyncio.to_thread(erases_done, user_id, None)


def _kick_erases() -> None:
    """From the worker, after a replay: have the service's loop finish what is parked. Never waits (the finish itself forgets in the
    kernel, through this worker), never raises."""
    loop = _loop
    if _finishing or loop is None or loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(_finish_erases(), loop)
    except Exception:  # noqa: BLE001 - kept parked, tried again after the next job
        pass


async def _finish_erases() -> int:
    """Finish the parked erases of every member whose kernel forget is NOT still parked (the kernel confirmed it). One at a time; a
    member whose finish fails stays parked (never dropped) and is tried again after the next job. Returns members finished."""
    global _finishing
    if _finishing:
        return 0
    _finishing = True
    done = 0
    try:
        global _erase_members
        if not await asyncio.to_thread(os.path.exists, _erases_path()):
            _erase_members = frozenset()
            return 0                  # nothing is parked, and reading the park must never create the state directory
        todo = await asyncio.to_thread(parked_erases)
        still = await asyncio.to_thread(_pending_read_locked)
        for member, entry in todo.items():
            if member in still:
                continue              # its kernel forget is not confirmed yet: nothing in production is erased before that
            if member in _planning and _planning[member] > _mono():
                continue              # a forget of this process is running this member's plan right now: left to it (PLAN_HOLD_S)
            try:
                import turn_forget

                left = await turn_forget.finish_parked(member, entry)
                kept = {k: set(left[k]) if k in left else set() for k in ERASE_KINDS}
                await asyncio.to_thread(erases_done, member, {k: [i for i in entry[k] if i not in kept[k]] for k in ERASE_KINDS})
                _left[member] = sum(len(kept[k]) for k in ERASE_KINDS)
                if _left[member]:     # an id the replay could not erase is NEVER cleared as done: it stays parked, and is counted
                    logger.error("OS_SHADOW parked erase NOT finished, kept code=left")
                    continue
                del _left[member]
                done += 1
                logger.info("OS_SHADOW parked erase finished")
            except Exception as exc:  # noqa: BLE001
                _left[member] = sum(len(entry[k]) for k in ERASE_KINDS)      # nothing was cleared: every id of the entry is still parked
                logger.error("OS_SHADOW parked erase NOT finished, kept code=%s", type(exc).__name__)
    except Exception as exc:  # noqa: BLE001
        logger.error("OS_SHADOW parked erases unreadable code=%s", type(exc).__name__)
    finally:
        _finishing = False
    return done


def _pending_read_locked() -> frozenset:
    with _pending_locked():
        return frozenset(_pending_read())


#: the kernel's own refusal when it has never known the subject (``capability.may_forget``). The shadow daemon never forgets a member
#: it was started with (it keeps every id it ever had: ``shadow_daemon.py``), so this refusal means nothing was ever recorded for them.
NEVER_KNOWN = "only a person can ask to forget"


def _kernel_forget(kernel: _Kernel, member: str, entity: Optional[str], topic: Optional[str]) -> int:
    """Forget by the member's id, ALWAYS: the current roster says who may be PROPOSED now, not who has records (a member taken off
    ``people.json`` may still have events from before). Only the kernel's own "never knew them" is read as nothing to forget."""
    scoped: dict = {"entity": entity} if entity is not None else ({"topic": topic} if topic is not None else {})
    try:
        out = kernel.call(SKILL_PANEL, "forget", subject=member, actor=member, **scoped)
    except KernelRefused as exc:
        if NEVER_KNOWN in (exc.reason or "").lower():
            return 0
        if not scoped:
            raise
        out = kernel.call(SKILL_PANEL, "forget", subject=member, actor=member)    # unmatchable scope: over-forget, the safe error
    return forget_ack(out)


#: the kernel's documented forget reply (``labs/zoe-os/zoe_os/server.py`` ``_op_forget`` -> ``spine.ForgetResult``), the same for a
#: whole, entity, topic or seqs forget: exactly these keys, each a non-negative int (never a bool)
FORGET_REPLY_KEYS = frozenset({"count", "upto"})


def forget_ack(out: Any) -> int:
    """The ONE place a kernel reply is read as "forgotten": ``count`` when ``out`` is exactly the documented reply, else
    ``KernelProtocol`` (so the delete is NOT confirmed and a parked forget STAYS parked). ``_kernel_forget`` is the only caller, and
    every path that confirms a forget or drops its durable marker goes through it: ``_do_forget`` (a delete, an entity or topic
    erase, the cascade's step and its whole-member retry) and ``_replay_pending``."""
    if not isinstance(out, dict) or set(out) != FORGET_REPLY_KEYS:
        raise KernelProtocol("forget reply")
    for key in FORGET_REPLY_KEYS:
        v = out[key]
        if type(v) is not int or v < 0:
            raise KernelProtocol("forget reply")
    return out["count"]


def _do_forget(kernel: _Kernel, member: str, entity: Optional[str], topic: Optional[str], fut: "concurrent.futures.Future") -> None:
    kernel.down_until = 0.0           # a forget never waits out the circuit an earlier failed add opened
    try:
        fut.set_result(_kernel_forget(kernel, member, entity, topic))
    except KernelRefused as exc:
        fut.set_exception(ShadowForgetUnavailable(f"kernel refused ({reason_code(exc.reason)})"))
    except Exception as exc:  # noqa: BLE001
        fut.set_exception(ShadowForgetUnavailable(type(exc).__name__))


async def _forget(user_id: str, entity: Optional[str], topic: Optional[str]) -> int:
    """Never blocks the event loop: the job goes on the queue with ``put_nowait``; a full queue (or a worker that cannot start) PARKS
    the forget durably (the blocking park runs in an executor) instead of waiting.

    What the caller is told is the truth about the KERNEL'S RECORD, whatever the flag says now: a confirmed forget returns its count;
    ANY forget the kernel did not confirm raises ``ShadowForgetUnavailable`` (parked as well, so it is replayed before the next job),
    with the flag on OR off. A durable retry marker is not an erase: a shadow turned off with its kernel stopped still holds what it
    recorded, so the member delete / entity / topic forget fails closed and the caller retries until the kernel confirms. The ONE
    no-op is the flag off with no shadow state at all (nothing was ever recorded here). There is deliberately no offline removal:
    zoe-data cannot prove the kernel process is stopped (deleting its store under a live writer is not a provable erase), so retiring
    the shadow for good is an owner step (stop the unit, then remove the shadow directory: docs/knowledge/zoe-os-list-shadow.md)."""
    strict = mode() == "shadow"
    _note_loop()
    if not strict:
        if _no_state():               # no state directory, answered from memory ALONE: no thread, no stat (see THE RULE)
            return 0
        # not certain: the state check stats and lists a directory, in a thread, never on the event loop
        if not await asyncio.to_thread(_state_probe):
            return 0                  # certain nothing was ever recorded here (no state at all) and the shadow is off: nothing to forget
    # anything else goes to the kernel, and is PARKED if it cannot be confirmed there (a missing creds.json included: the kernel's
    # store may still hold the member): "cannot reach" is never "done"
    fut: concurrent.futures.Future = concurrent.futures.Future()
    code = ""
    try:
        # Thread.start() waits for the new thread to come up: in a thread, never on the turn's loop (a forget is awaited by the turn)
        await asyncio.to_thread(_ensure_worker)
        _q.put_nowait(("forget", user_id, entity, topic, fut))
    except Exception as exc:  # noqa: BLE001 - queue.Full above all: park, never wait on the loop
        code = "queue_full" if isinstance(exc, queue.Full) else type(exc).__name__
    if not code:
        try:
            # shielded: a timeout or a cancelled caller never cancels the worker's own future (it still sets its result)
            return await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(fut)), timeout=FORGET_TIMEOUT_S)
        except asyncio.CancelledError:
            # the CALLER was cancelled (a request gone, a shutdown) before the kernel confirmed: park durably all the same, off the
            # loop and shielded so a second cancel cannot stop it, then let the cancellation go on. An unconfirmed forget is never lost.
            _forgotten_at[user_id] = time.perf_counter()
            park = asyncio.get_running_loop().run_in_executor(None, _pending_add, user_id)
            try:
                await asyncio.shield(park)
            except BaseException:  # noqa: BLE001 - the park carries on in its thread; the cancellation is what the caller gets
                pass
            raise
        except Exception as exc:  # noqa: BLE001
            code = type(exc).__name__
    try:                              # not confirmed, either flag: never lose it - parked, replayed before the next job
        _forgotten_at[user_id] = time.perf_counter()     # on the loop, before the park: every add stamped before it is now stale
        await asyncio.get_running_loop().run_in_executor(None, _pending_add, user_id)
    except Exception as park_exc:  # noqa: BLE001 - the retry could not be made durable: the erase must not say "done"
        logger.error("OS_SHADOW forget NOT confirmed and NOT parked code=%s", type(park_exc).__name__)
        raise ShadowForgetUnavailable(f"not parked ({type(park_exc).__name__})") from park_exc
    # never "forgotten" without the kernel's confirmation, flag on or off: the record may still be there
    logger.error("OS_SHADOW forget NOT confirmed, parked code=%s", code)
    raise ShadowForgetUnavailable(f"{code} (parked)")


async def forget_for_erase(user_id: str) -> int:
    """THE shadow step of every row- and turn-scoped erase (``MemoryService.erase_rows`` and ``exact_words``' erasers: "forget that",
    "forget it", the memories-screen row forget, a guardian forget, curation, the day-review unwrite...), same contract as
    ``forget_user``: confirmed or it RAISES (``ShadowForgetUnavailable``, parked), whatever the flag; a no-op only with no shadow
    state at all.

    What the kernel's ``forget`` can target today (``labs/zoe-os/zoe_os/server.py`` ``_op_forget`` -> ``spine.forget``): a whole
    member (``subject``), event sequence numbers (``seqs``), an ``entity`` name (also shielded), a ``topic``'s words. NOT a request,
    turn or production row: zoe-data keeps no map from a turn to the kernel events its writes produced (the cause event carries fixed
    text, never a turn id). So a row- or turn-scoped forget cannot name the turn's shadow records; the nearest CORRECT op is the
    member's whole shadow record (the documented safe over-forget: a shadow copy is disposable). Narrowing it needs the shadow to
    record its seqs per turn first (an open item).

    When this raises, the erasers that called it park what they were about to erase (``park_unconfirmed``: the row eraser its row
    ids, the word erasers their index turn ids), so the replay finishes it: the erasers are the one place every row and words
    forget goes through, and a caller that had already hidden rows cannot leave them behind."""
    return await _forget(user_id, None, None)


async def forget_user(user_id: str) -> int:
    """Whole-member forget in the kernel (``MemoryService.delete_user``). Raises ``ShadowForgetUnavailable`` while shadow is on."""
    return await _forget(user_id, None, None)


async def erase_entity(user_id: str, name: str) -> int:
    """'Forget Dana': the kernel erases the member's events naming the entity (and shields the name)."""
    return await _forget(user_id, name, None)


async def erase_topic(user_id: str, topic: str) -> int:
    return await _forget(user_id, None, topic)


# ---- the roster, for the owner's one-off export ----------------------------------------------------------------------------------------
def write_roster(ids, dest: Optional[Path] = None) -> list:
    """Write people.json (the member ids) beside the kernel's credentials, 0600. The owner runs it once, and again when a member is
    added (the kernel reads it at start). Ids only; never printed. With no dest it goes into the state directory, which is made
    and SETTLED first (ensure_state_dir: blocks up to the settle time when the directory is new)."""
    clean = sorted(_roster_ids(ids))
    if dest is None:
        path = ensure_state_dir() / "people.json"
    else:
        path = dest
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"people": clean}, fh)
    return clean


def restore(src: Path) -> dict:
    """Zoe's restore of a shadow state directory from a backup copy src: the ONLY supported way (never copy the directory by
    hand: the kernel refuses a directory that was not made here, and a hand copy can appear before a process has seen it).

    The directory is made and settled first (ensure_state_dir). Then ONLY what is safe to bring back is merged in: the parked
    forgets and parked erases (ids; replaying a forget twice is harmless) and, when there is none here, the roster. The kernel's
    RECORD (kernel/) is deliberately NOT restored: it is a disposable copy, and a backup of it can hold what a member has had
    forgotten since. Credentials are re-issued by the kernel at start. Returns counts only."""
    src = Path(src)
    d = ensure_state_dir()
    out = {"forgets": 0, "erases": 0, "roster": 0}
    try:
        members = [str(x) for x in json.loads((src / "pending-forgets.json").read_text("utf-8"))]
    except FileNotFoundError:
        members = []
    for member in members:
        _pending_add(member)
        out["forgets"] += 1
    try:
        erases = json.loads((src / "pending-erases.json").read_text("utf-8"))
    except FileNotFoundError:
        erases = {}
    for member, entry in erases.items():
        park_erase(str(member), **{k: entry.get(k) or () for k in ERASE_KINDS})
        out["erases"] += 1
    if not (d / "people.json").exists() and (src / "people.json").exists():
        out["roster"] = len(write_roster(json.loads((src / "people.json").read_text("utf-8"))["people"]))
    return out


def main(argv: Optional[list] = None) -> int:
    """The owner's tool (counts only are printed; each step makes and SETTLES the state directory first):

        os_shadow.py --init                         make the state directory (empty, marker, the settle wait)
        os_shadow.py --write-roster ID [ID ...]     the roster export
        os_shadow.py --restore BACKUP_DIR           bring back parked forgets / erases and the roster from a backup copy"""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--init"]:
        ensure_state_dir()
        print("state directory ready")
        return 0
    if args[:1] == ["--write-roster"] and len(args) > 1:
        print(f"roster: {len(write_roster(args[1:]))} members written")
        return 0
    if args[:1] == ["--restore"] and len(args) == 2:
        got = restore(Path(args[1]))
        print(f"restored: {got['forgets']} parked forgets, {got['erases']} parked erases, roster {got['roster']} members")
        return 0
    print("usage: os_shadow.py --init | --write-roster ID [ID ...] | --restore BACKUP_DIR", file=sys.stderr)
    return 2


def _reset_for_tests() -> None:
    """Stop the worker (it drops its connections) and clear every counter, the roster cache and the queue."""
    global _roster_cache, _worker, _down_logged, _pending_members, _erase_members, _loop, _finishing, _state_known, _state_poller
    global _door_worker
    if _door_worker is not None and _door_worker.is_alive():
        _door_q.put(("stop",))
        _door_worker.join(timeout=5)
    _door_worker = None
    while True:
        try:
            _door_q.get_nowait()
        except queue.Empty:
            break
    with _lock:
        _door_counts.clear()
    _down_logged = False
    _pending_members = None           # a restart: the next worker reads the park file before its first job
    _erase_members, _loop, _finishing = None, None, False
    _planning.clear()
    _left.clear()
    _state_stop.set()                 # the poller ends; nothing is established, so the next forget probes
    with _state_lock:
        _state_known, _state_poller = ("", True, 0.0), None
    _forgotten_at.clear()
    _parked_at.clear()
    if _worker is not None and _worker.is_alive():
        _q.put(("stop",))
        _worker.join(timeout=5)
    _worker = None
    _roster_cache = (0.0, None, frozenset())
    with _lock:
        _counts.clear()
        _vcounts.clear()
        del _hops[:]
    while True:
        try:
            _q.get_nowait()
        except queue.Empty:
            break


if __name__ == "__main__":
    raise SystemExit(main())
else:
    _start_at_import()        # service start-up, never a turn (see _ensure_worker)
