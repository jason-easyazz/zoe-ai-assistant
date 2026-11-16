"""Zoe OS SHADOW client (docs/knowledge/zoe-os-list-shadow.md, docs/knowledge/zoe-os-integration.md).

Every list add (and, when named in ``ZOE_OS_SHADOW_VERBS``, every reminder create, alarm create and spoken list-item remove) the
household really made is ALSO proposed to a Zoe OS kernel, which records the event and a receipt in its OWN store; the two outcomes
are compared and logged as one codes-only line. The existing write path stays the only thing that changes the household's data.
Flag ``ZOE_OS_LIST_SHADOW`` = ``off`` (default) | ``shadow`` is the ONE switch (it keeps its name: every verb rides it, and the
forget fail-closed rule hangs off it); ``ZOE_OS_SHADOW_VERBS`` (default ``list_add``) names which verbs are shadowed while it is
on, so flipping the switch alone still shadows list add only, exactly as before. There is no ``on``: the kernel is never
authoritative here.

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
    """The switch is ``shadow`` AND the verb is listed: what a call site checks before any extra read it does only for the shadow."""
    return mode() == "shadow" and verb in verbs()


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
STATE_NAMES = ("creds.json", "people.json", "known-people.json", "kernel", "pending-forgets.json")


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


def _load_creds() -> tuple[str, dict]:
    try:
        raw = json.loads(_creds_path().read_text("utf-8"))
        return str(raw["socket"]), {k: bytes.fromhex(v) for k, v in raw["creds"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise KernelUnavailable("not_provisioned") from exc


_roster_cache: tuple = (0.0, None, frozenset())      # (checked_at, mtime, ids)


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
            ids = frozenset(str(p) for p in json.loads(path.read_text("utf-8"))["people"])
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
SOURCES = ("spoken", "single", "skybridge", "rest", "suggestion", "service")      # every call site's ``source=``, on every branch
CLASSES = LIST_CLASSES + ("other", "once", "repeat", "-")       # list classes; reminder / alarm once|repeat; "-" (note_act)
LIVE = ("added", "refused", "noop")
KERNEL_STATES = ("verified", "unverified", "uncertain", "refused", "none")
REASONS = ("-", "no_grant", "manifest", "params", "cause", "forgotten", "other", "timeout", "protocol", CROSSED, "socket", "unavailable",
           "not_provisioned", "circuit", "no_credential", "closed", "auth", "handshake", "hook", "worker", "code", "pending_forget",
           "forgotten_before", "recurring", "no_time", "range", "time", "parse", "old_kernel")


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
            try:
                c = self.conns[skill] = _Conn(path, skill, creds[skill], until)
            except (KernelUnavailable, OSError):
                self.down_until = time.monotonic() + CIRCUIT_S
                raise
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
            self.drop(skill)
            if not (isinstance(exc, KernelUnavailable) and exc.args[:1] == ("circuit",)):
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
                _replay_pending(kernel, force=job[0] == "forget")
            except Exception as exc:  # noqa: BLE001 - kept pending, tried again before the next job
                logger.warning("OS_SHADOW replay error=%s", type(exc).__name__)
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
        ev = kernel.call(SKILL_VOICE, "publish", until, kind="utterance", subject=user_id, payload={"text": utterance_text(verb)})
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


def note_alarm_create(user_id: Optional[str], *, source: str, time_hhmm: Optional[str], repeat: Optional[str]) -> None:
    """An alarm row committed (``alarms.create_alarm``). The kernel's alarm is one time per person: the repeat rule rides ``cls``."""
    _note("alarm_create", user_id, source, True, lambda: ("once" if (repeat or "once") == "once" else "repeat",
                                                          {"time": str(time_hhmm or ""), "repeat": repeat or "once"}))


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

    def __enter__(self):
        import fcntl

        _pending_mutex.acquire()
        try:
            path = _pending_path()
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.fd = os.open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except BaseException:
            _pending_mutex.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            os.close(self.fd)             # closing drops the flock
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
    path = _pending_path()
    if not members:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(sorted(set(members)), fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _pending_add(member: str) -> None:
    """Park a forget for a later replay. RAISES if it cannot be made durable: the caller then fails the erase closed. Blocking (an
    ``flock`` wait and two fsyncs): never call it on the event loop (``_forget`` hands it to an executor)."""
    global _pending_members
    with _pending_locked():
        members = _pending_read() + [member]
        _pending_write(members)
        _pending_members = frozenset(members)


def _pending_done(members: set) -> None:
    """Drop the members a replay confirmed; re-read under the lock so a forget parked WHILE the replay ran is kept."""
    with _pending_locked():
        _pending_write([m for m in _pending_read() if m not in members])


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
    done = set()
    for member in todo:
        try:
            _kernel_forget(kernel, member, None, None)
            done.add(member)
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
    return int(out.get("count", 0)) if isinstance(out, dict) else 0


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
    the forget durably (the blocking park runs in an executor) instead of waiting. What the caller is told stays honest: a confirmed
    forget returns its count; with the flag ``shadow`` anything unconfirmed raises ``ShadowForgetUnavailable`` (parked or not); with
    it off a parked forget returns 0 (deferred: replayed before the next job), and one that could not be parked raises."""
    strict = mode() == "shadow"
    # the state check stats and lists a directory: in a thread, never on the event loop (a slow or hung disk would stall every turn)
    if not strict and not await asyncio.to_thread(shadow_state_exists):
        return 0                      # certain nothing was ever recorded here (no state at all) and the shadow is off: nothing to forget
    # anything else goes to the kernel, and is PARKED if it cannot be confirmed there (a missing creds.json included: the kernel's
    # store may still hold the member): "cannot reach" is never "done"
    fut: concurrent.futures.Future = concurrent.futures.Future()
    code = ""
    try:
        _ensure_worker()
        _q.put_nowait(("forget", user_id, entity, topic, fut))
    except Exception as exc:  # noqa: BLE001 - queue.Full above all: park, never wait on the loop
        code = "queue_full" if isinstance(exc, queue.Full) else type(exc).__name__
    if not code:
        try:
            return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=FORGET_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            code = type(exc).__name__
    try:                              # not confirmed, either flag: never lose it - parked, replayed before the next job
        _forgotten_at[user_id] = time.perf_counter()     # on the loop, before the park: every add stamped before it is now stale
        await asyncio.get_running_loop().run_in_executor(None, _pending_add, user_id)
    except Exception as park_exc:  # noqa: BLE001 - the retry could not be made durable: the erase must not say "done"
        logger.error("OS_SHADOW forget NOT confirmed and NOT parked code=%s", type(park_exc).__name__)
        raise ShadowForgetUnavailable(f"not parked ({type(park_exc).__name__})") from park_exc
    if strict:                        # the flag on: an unconfirmed forget is never "forgotten", parked or not
        logger.error("OS_SHADOW forget NOT confirmed, parked code=%s", code)
        raise ShadowForgetUnavailable(f"{code} (parked)")
    logger.warning("OS_SHADOW forget deferred (flag off) code=%s", code)
    return 0


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
    """Write ``people.json`` (the member ids) beside the kernel's credentials, 0600. The owner runs it once, and again when a member is
    added (the kernel reads it at start). Ids only; never printed."""
    clean = sorted({str(i).strip() for i in ids if str(i).strip()})
    path = dest or (state_dir() / "people.json")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"people": clean}, fh)
    return clean


def main(argv: Optional[list] = None) -> int:
    """``python3 os_shadow.py --write-roster ID [ID ...]``: the owner's one-off roster export. Prints the COUNT only."""
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--write-roster"] and len(args) > 1:
        print(f"roster: {len(write_roster(args[1:]))} members written")
        return 0
    print("usage: os_shadow.py --write-roster ID [ID ...]", file=sys.stderr)
    return 2


def _reset_for_tests() -> None:
    """Stop the worker (it drops its connections) and clear every counter, the roster cache and the queue."""
    global _roster_cache, _worker, _down_logged, _pending_members
    _down_logged = False
    _pending_members = None           # a restart: the next worker reads the park file before its first job
    _forgotten_at.clear()
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
