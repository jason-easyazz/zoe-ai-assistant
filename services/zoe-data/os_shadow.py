"""Zoe OS list-add SHADOW client (docs/knowledge/zoe-os-list-shadow.md).

Every list add the household really made is ALSO proposed to a Zoe OS kernel, which records the event and a receipt in its OWN
store; the two outcomes are compared and logged as one codes-only line. The existing add path stays the only thing that changes the
household's list. Flag ``ZOE_OS_LIST_SHADOW`` = ``off`` (default) | ``shadow``; there is no ``on``: the kernel is never
authoritative here.

Boundaries (each pinned by ``tests/test_os_shadow.py``):

* NO import from ``labs/``. The kernel is reached over its Unix socket with the wire protocol below, which is the kernel's
  documented v1 (``labs/zoe-os/zoe_os/wire.py``); ``tests/test_os_shadow_contract.py`` runs this client against the real lab kernel
  when the lab code is present, so the two cannot drift silently.
* The hook (``note_add``) runs AFTER the live add has committed, is O(1), never blocks and never raises: it puts one tuple on a
  bounded queue. A single worker THREAD does all socket work, so a slow or dead kernel can neither slow nor break a turn. The
  turn never starts that thread: it starts at import (start-up); a turn that finds it dead drops and counts (``worker_down``), and
  ``revive`` (start-up, a forget) brings it back.
* Erase-safe: a proposal for a member who is being erased (``user_erase_gate``) is dropped, and forget jobs go through the SAME
  FIFO queue, so a proposal queued before an erase is sent before the forget and removed by it.
* Forget reaches the kernel (``forget_user`` / ``erase_entity`` / ``erase_topic``): fail-closed while the flag is ``shadow``.
* Logs carry closed codes only: never an item, a list name or a person's name.

Stdlib only (plus ``typed_env`` / ``user_erase_gate``).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import hmac
import json
import logging
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
DIR_FLAG = "ZOE_OS_SHADOW_DIR"

# ---- the wire (kernel protocol v1; see the module docstring) ----------------------------------------------------------------------
WIRE_VERSION = 1
MAX_REPLY = 64 * 1024          # the client's own cap: a shadow reply is a receipt, never 16 MB
OP_TIMEOUT_S = 0.5             # per socket operation, and never past the add's own deadline
JOB_DEADLINE_S = 1.5           # ONE overall budget per add: connects, handshakes, publish and act all draw on it
CIRCUIT_S = 5.0                # after an unavailable kernel, skip connecting this long (counted, not retried per add)
FORGET_TIMEOUT_S = 8.0
QUEUE_MAX = 64

SKILL_VOICE, SKILL_BRAIN, SKILL_PANEL = "voice", "brain", "panel"
UTTERANCE_TEXT = "shadow list add"          # the cause event carries no household words: the item rides the act only


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
    """The timeout for the NEXT socket operation: ``OP_TIMEOUT_S``, cut to what is left of the add's budget (``until``, a
    ``time.monotonic()`` instant; None = no overall budget, e.g. a forget). A spent budget raises ``socket.timeout`` before the
    operation starts, so no connect, send or read can run past the add's deadline."""
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


def _read_frame(sock: socket.socket, until: Optional[float] = None) -> dict:
    (size,) = struct.unpack(">I", _recv_exact(sock, 4, until))
    if size > MAX_REPLY:
        raise KernelProtocol("reply too large")
    try:
        obj = json.loads(_recv_exact(sock, size, until))
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
#: EVERY code a list add can end in, and nothing else (``test_results_is_exactly_the_set_of_codes_emitted`` pins both directions).
#: Compared: ``match`` and the mismatch codes. No verdict: ``kernel_unavailable``, ``timeout``. Not compared: the rest, of which
#: ``no_input`` (no member or no item) and ``shadow_error`` (the shadow's OWN failure, ``reason=hook|worker|code``) are not the kernel's.
RESULTS = ("worker_down", "match", "kernel_refused_live_did", "kernel_unverified_live_did", "kernel_did_live_refused", "item_differs",
           "list_differs", "kernel_unavailable", "timeout", "kernel_malformed", "member_not_in_roster", "dropped_erased",
           "queue_full", "live_noop", "no_input", "shadow_error", "member_differs", "member_unproven")
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


def compare(live: str, kernel: Optional[dict], sent: dict, *, member: Optional[str] = None) -> tuple[str, str]:
    """``(result, kernel_state)``. ``live``: added | refused. ``kernel``: the act receipt (a dict), ``{"refused": reason}`` or None.
    ``sent``: ``{"item", "list"}`` as proposed; ``member``: who the add was for (``for_whom``: a receipt for anyone else is
    ``member_differs``, never a match)."""
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
    if not isinstance(proof, dict) or "item" not in proof:
        return "kernel_malformed", state
    if proof["item"] != sent["item"]:
        return "item_differs", state
    if "list" in proof and proof["list"] != sent["list"]:
        return "list_differs", state
    return "match", state


# ---- counters and the one line per add ----------------------------------------------------------------------------------------------
_counts: Counter = Counter()
_hops: list = []                 # kernel hop ms of compared adds (bounded)
_lock = threading.Lock()
LIST_CLASSES = ("shopping", "todo", "personal")
#: EVERY value each field of the one line can take (``_finish`` maps anything else to ``other`` / ``none``): the line and the counter
#: keys can never carry a caller's text (``test_marker_text_from_any_caller_argument_never_reaches_a_log_line_or_a_counter``).
SOURCES = ("spoken", "single", "skybridge", "rest", "suggestion", "service")      # every call site's ``source=``, on every branch
CLASSES = LIST_CLASSES + ("other",)
LIVE = ("added", "refused", "noop")
KERNEL_STATES = ("verified", "unverified", "uncertain", "refused", "none")
REASONS = ("-", "no_grant", "manifest", "params", "cause", "forgotten", "other", "timeout", "protocol", CROSSED, "socket", "unavailable",
           "not_provisioned", "circuit", "no_credential", "closed", "auth", "handshake", "hook", "worker", "code", "pending_forget",
           "forgotten_before")


def counters() -> dict:
    with _lock:
        return dict(_counts)


def _tally(result: str, hop_ms: Optional[float] = None) -> None:
    with _lock:
        _counts[result] += 1
        if hop_ms is not None:
            _hops.append(hop_ms)
            del _hops[:-500]


def _log(source: str, cls: str, live: str, kernel_state: str, result: str, reason: str, hop_ms: float, q_ms: float) -> None:
    """ONE line per list add; codes and numbers only (tests/test_os_shadow.py plants words and checks none reach it)."""
    logger.info("OS_SHADOW_LIST_ADD src=%s cls=%s live=%s kernel=%s result=%s reason=%s hop_ms=%.1f q_ms=%.1f",
                source, cls, live, kernel_state, result, reason, hop_ms, q_ms)


def _finish(result: str, *, source: str, cls: str, live: str, kernel_state: str = "none", reason: str = "", hop_ms: float = 0.0,
            q_ms: float = 0.0) -> None:
    """THE one exit of every list add the shadow saw (flag on): it counts the add AND writes its one line, so an outcome cannot be
    counted without a line or logged without a count. Only ``note_add`` (an add that ends at the door) and ``_do_add`` (an add the
    worker took) call it, each exactly once, from a ``finally`` (``test_every_note_add_outcome_writes_exactly_one_line`` and the
    structure pins). A code outside ``RESULTS`` is itself a bug: written as ``shadow_error reason=code``, never as the stray code."""
    if result not in RESULTS:
        result, reason, kernel_state = "shadow_error", "code", "none"
    source = source if source in SOURCES else "other"          # closed sets only: a caller's text can never reach the line
    cls = cls if cls in CLASSES else "other"
    live = live if live in LIVE else "added"
    kernel_state = kernel_state if kernel_state in KERNEL_STATES else "none"
    reason = (reason or "-") if (reason or "-") in REASONS else "other"
    hop_ms, q_ms = float(hop_ms), float(q_ms)
    _log(source, cls, live, kernel_state, result, reason, hop_ms, q_ms)       # the line first: a counted add has always been written
    _tally(result, hop_ms if kernel_state != "none" else None)


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
    The warning is said ONCE per process, so a dead worker stands out without a warning per add."""
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
        """``until``: the add's overall deadline (``time.monotonic()``), passed into the connect, the handshake and the call alike."""
        try:
            return self.conn(skill, until).call(op, until, **args)
        except (KernelUnavailable, KernelProtocol, OSError):
            self.drop(skill)
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
            if job[0] == "add":
                _do_add(kernel, *job[1:])
            else:
                _do_forget(kernel, job[1], job[2], job[3], job[4])
        except Exception as exc:  # noqa: BLE001 - the worker never dies and never raises into a turn
            logger.warning("OS_SHADOW worker error=%s", type(exc).__name__)
            if job[0] == "forget" and not job[4].done():
                job[4].set_exception(exc)


def _do_add(kernel: _Kernel, user_id: str, source: str, cls: str, live: str, item: str, list_name: str, gen: int,
            queued_at: float) -> None:
    """One queued add, end to end: ONE line, whatever happens (an unexpected error is ``shadow_error reason=worker``)."""
    started = time.perf_counter()
    out: dict = {"result": "shadow_error", "reason": "worker"}
    try:
        out = _add_outcome(kernel, user_id, live, item, list_name, gen, started, queued_at)
    except Exception as exc:  # noqa: BLE001 - counted and written below; the worker never dies
        logger.warning("OS_SHADOW worker error=%s", type(exc).__name__)
    finally:
        _finish(out.get("result", "shadow_error"), source=source, cls=cls, live=live, kernel_state=out.get("kernel_state", "none"),
                reason=out.get("reason", ""), hop_ms=out.get("hop_ms", 0.0), q_ms=(started - queued_at) * 1000)


def _add_outcome(kernel: _Kernel, user_id: str, live: str, item: str, list_name: str, gen: int, started: float,
                 queued_at: float = 0.0) -> dict:
    """What the kernel made of one add, as ``{"result", "kernel_state", "reason", "hop_ms"}``; every return names a result. The
    whole add runs on ONE budget (``JOB_DEADLINE_S`` from now) that every socket operation draws on: a stall anywhere ends it as
    ``timeout`` within the budget."""
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
    until = time.monotonic() + JOB_DEADLINE_S
    sent = {"item": item, "list": list_name}
    result_kernel: Optional[dict] = None
    reason, result = "", ""
    try:
        ev = kernel.call(SKILL_VOICE, "publish", until, kind="utterance", subject=user_id, payload={"text": UTTERANCE_TEXT})
        seq = ev.get("seq") if isinstance(ev, dict) else None
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise KernelProtocol("no seq")
        rec = kernel.call(SKILL_BRAIN, "act", until, action="list.add", on_behalf_of=user_id,
                          args={"item": item, "list": list_name, "asked": seq}, caused_by=[seq])
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
        result, kstate = compare(live, result_kernel, sent, member=user_id)
    return {"result": result, "kernel_state": kstate, "reason": reason, "hop_ms": hop_ms}


# ---- the hook -----------------------------------------------------------------------------------------------------------------------
def list_class(list_type: Optional[str]) -> str:
    lt = (list_type or "").strip().lower()
    return lt if lt in LIST_CLASSES else "other"


_OFF, _QUEUED = "off", "queued"      # hook outcomes that are NOT results: flag off (nothing touched) / the worker writes the line


def note_add(user_id: Optional[str], *, source: str, list_type: Optional[str], list_name: Optional[str], item: Optional[str],
             landed: bool = True) -> None:
    """A list add finished (call AFTER the live commit). ``landed`` False = nothing was written (already on the list, replayed):
    counted, never proposed. Never raises, never blocks, O(1); a flag of ``off`` returns before anything is touched.

    With the flag on, EVERY call ends in exactly one ``OS_SHADOW_LIST_ADD`` line: written here (``_finish``, in the ``finally``)
    when the add ends at the door, or by the worker when it was queued. An unexpected error is ``shadow_error reason=hook``."""
    outcome = "shadow_error"
    try:
        outcome = _door(user_id, source, list_type, list_name, item, landed)
    except Exception:  # noqa: BLE001 - shadow must never break the turn; counted and written below
        outcome = "shadow_error"
    finally:
        if outcome not in (_OFF, _QUEUED):
            try:
                _finish(outcome, source=str(source), cls=list_class(list_type) if isinstance(list_type, str) else "other",
                        live="noop" if outcome == "live_noop" else "added", reason="hook" if outcome == "shadow_error" else "")
            except Exception:  # noqa: BLE001
                pass


def _door(user_id: Optional[str], source: str, list_type: Optional[str], list_name: Optional[str], item: Optional[str],
          landed: bool) -> str:
    """The hook's decision: ``_OFF``, ``_QUEUED`` or the result code the add ends in here. Every return names one."""
    if mode() != "shadow":
        return _OFF
    if not landed:
        return "live_noop"
    uid, text = (user_id or "").strip(), (item or "").strip()
    if not uid or not text:
        return "no_input"
    if user_erase_gate.in_progress(uid):
        return "dropped_erased"
    if not _worker_alive():           # never Thread.start() on a turn: drop, count, revive off the turn
        _worker_down()
        return "worker_down"
    try:
        _q.put_nowait(("add", uid, source, list_class(list_type), "added", text, (list_name or list_type or "shopping").strip() or "shopping",
                       user_erase_gate.generation(uid), time.perf_counter()))
    except queue.Full:
        return "queue_full"
    return _QUEUED


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
    if not provisioned():
        return 0                      # no kernel was ever installed here: nothing recorded, no socket touched
    strict = mode() == "shadow"
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
            if strict:
                logger.error("OS_SHADOW forget NOT confirmed code=%s", code)
                raise ShadowForgetUnavailable(code) from exc
    try:                              # not confirmed: never lose it - replayed before the next job
        _forgotten_at[user_id] = time.perf_counter()     # on the loop, before the park: every add stamped before it is now stale
        await asyncio.get_running_loop().run_in_executor(None, _pending_add, user_id)
    except Exception as park_exc:  # noqa: BLE001 - the retry could not be made durable: the erase must not say "done"
        logger.error("OS_SHADOW forget NOT confirmed and NOT parked code=%s", type(park_exc).__name__)
        raise ShadowForgetUnavailable(f"not parked ({type(park_exc).__name__})") from park_exc
    if strict:                        # only a forget that never reached the queue gets here with the flag on
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
