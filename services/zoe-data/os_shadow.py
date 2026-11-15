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
  bounded queue. A single worker THREAD does all socket work, so a slow or dead kernel can neither slow nor break a turn.
* Erase-safe: a proposal for a member who is being erased (``user_erase_gate``) is dropped, and forget jobs go through the SAME
  FIFO queue, so a proposal queued before an erase is sent before the forget and removed by it.
* Forget reaches the kernel (``forget_user`` / ``erase_entity`` / ``erase_topic``): fail-closed while the flag is ``shadow``.
* Logs carry closed codes only: never an item, a list name or a person's name.

Stdlib only (plus ``typed_env`` / ``user_erase_gate``).
"""
from __future__ import annotations

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
OP_TIMEOUT_S = 0.5             # per socket operation
JOB_DEADLINE_S = 1.5           # publish + act together
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
    raw = os.environ.get(DIR_FLAG, "").strip()
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
def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise KernelUnavailable("closed")
        buf += chunk
    return bytes(buf)


def _read_frame(sock: socket.socket) -> dict:
    (size,) = struct.unpack(">I", _recv_exact(sock, 4))
    if size > MAX_REPLY:
        raise KernelProtocol("reply too large")
    try:
        obj = json.loads(_recv_exact(sock, size))
    except ValueError as exc:
        raise KernelProtocol("not json") from exc
    if not isinstance(obj, dict):
        raise KernelProtocol("not an object")
    return obj


def _write_frame(sock: socket.socket, obj: dict) -> None:
    body = json.dumps(obj, separators=(",", ":")).encode()
    sock.sendall(struct.pack(">I", len(body)) + body)


class _Conn:
    """One authenticated connection (one kernel identity). Any failure closes it: a half-read reply must never be mistaken for the
    answer to the next request."""

    def __init__(self, path: str, skill: str, credential: bytes) -> None:
        self._id = 0
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.settimeout(OP_TIMEOUT_S)
            self.sock.connect(path)
            hello = _read_frame(self.sock)
            nonce = bytes.fromhex(str(hello["nonce"]))
            _write_frame(self.sock, {"v": WIRE_VERSION, "skill": skill,
                                     "proof": hmac.new(credential, nonce, hashlib.sha256).hexdigest()})
            if _read_frame(self.sock).get("ok") is not True:
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

    def call(self, op: str, **args: Any) -> Any:
        self._id += 1
        try:
            _write_frame(self.sock, {"v": WIRE_VERSION, "id": self._id, "op": op, "args": args})
            reply = _read_frame(self.sock)
        except (OSError, KernelUnavailable, KernelProtocol):
            self.close()
            raise
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
RESULTS = ("match", "kernel_refused_live_did", "kernel_unverified_live_did", "kernel_did_live_refused", "item_differs",
           "list_differs", "kernel_unavailable", "timeout", "kernel_malformed", "member_not_in_roster", "dropped_erased",
           "queue_full")
_REASONS = (("no grant", "no_grant"), ("manifest", "manifest"), ("argument", "params"), ("cause", "cause"),
            ("cited", "cause"), ("forgotten", "forgotten"))


def reason_code(reason: str) -> str:
    """The kernel's fixed refusal text mapped to a closed code; never echoed."""
    low = (reason or "").lower()
    return next((code for needle, code in _REASONS if needle in low), "other")


def compare(live: str, kernel: Optional[dict], sent: dict) -> tuple[str, str]:
    """``(result, kernel_state)``. ``live``: added | refused. ``kernel``: the act receipt (a dict), ``{"refused": reason}`` or None.
    ``sent``: ``{"item", "list"}`` as proposed. The member is the connection's own business: a receipt names the person it acted for,
    so a mismatch there is ``kernel_malformed``."""
    if kernel is None:
        return "kernel_unavailable", "none"
    if "refused" in kernel:
        return ("match" if live == "refused" else "kernel_refused_live_did"), "refused"
    state = "verified" if kernel.get("verified") is True and kernel.get("outcome") == "verified" else (
        "uncertain" if kernel.get("outcome") == "uncertain" else "unverified")
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
                source, cls, live, kernel_state, result, reason or "-", hop_ms, q_ms)


# ---- the worker ---------------------------------------------------------------------------------------------------------------------
_q: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
_worker: Optional[threading.Thread] = None
_worker_guard = threading.Lock()


def _ensure_worker() -> None:
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _worker_guard:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="os-shadow", daemon=True)
            _worker.start()


class _Kernel:
    """The worker's three lazily opened connections and the circuit that keeps a dead kernel from costing a connect per add."""

    def __init__(self) -> None:
        self.conns: dict[str, _Conn] = {}
        self.down_until = 0.0

    def conn(self, skill: str) -> _Conn:
        if time.monotonic() < self.down_until:
            raise KernelUnavailable("circuit")
        c = self.conns.get(skill)
        if c is None:
            path, creds = _load_creds()
            if skill not in creds:
                raise KernelUnavailable("no_credential")
            try:
                c = self.conns[skill] = _Conn(path, skill, creds[skill])
            except (KernelUnavailable, OSError):
                self.down_until = time.monotonic() + CIRCUIT_S
                raise
        return c

    def drop(self, skill: str) -> None:
        c = self.conns.pop(skill, None)
        if c is not None:
            c.close()

    def call(self, skill: str, op: str, **args: Any) -> Any:
        try:
            return self.conn(skill).call(op, **args)
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
        try:
            _do_add(kernel, *job[1:])
        except Exception as exc:  # noqa: BLE001 - the worker never dies and never raises into a turn
            logger.warning("OS_SHADOW worker error=%s", type(exc).__name__)


def _do_add(kernel: _Kernel, user_id: str, source: str, cls: str, live: str, item: str, list_name: str, gen: int,
            queued_at: float) -> None:
    started = time.perf_counter()
    q_ms = (started - queued_at) * 1000
    if user_id not in roster():
        _tally("member_not_in_roster")
        return
    if user_erase_gate.generation(user_id) != gen or user_erase_gate.in_progress(user_id):
        _tally("dropped_erased")
        return
    sent = {"item": item, "list": list_name}
    result_kernel: Optional[dict] = None
    reason, result = "", ""
    try:
        ev = kernel.call(SKILL_VOICE, "publish", kind="utterance", subject=user_id, payload={"text": UTTERANCE_TEXT})
        seq = ev.get("seq") if isinstance(ev, dict) else None
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise KernelProtocol("no seq")
        if time.perf_counter() - started > JOB_DEADLINE_S:
            raise socket.timeout()
        rec = kernel.call(SKILL_BRAIN, "act", action="list.add", on_behalf_of=user_id,
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
    except KernelProtocol:
        result, reason = "kernel_malformed", "protocol"
    except OSError as exc:
        result, reason = ("timeout", "timeout") if isinstance(exc, socket.timeout) else ("kernel_unavailable", "socket")
    hop_ms = (time.perf_counter() - started) * 1000
    kstate = "none"
    if not result:
        result, kstate = compare(live, result_kernel, sent)
    _tally(result, hop_ms if kstate != "none" else None)
    _log(source, cls, live, kstate, result, reason, hop_ms, q_ms)


# ---- the hook -----------------------------------------------------------------------------------------------------------------------
def list_class(list_type: Optional[str]) -> str:
    lt = (list_type or "").strip().lower()
    return lt if lt in LIST_CLASSES else "other"


def note_add(user_id: Optional[str], *, source: str, list_type: Optional[str], list_name: Optional[str], item: Optional[str],
             landed: bool = True) -> None:
    """A list add finished (call AFTER the live commit). ``landed`` False = nothing was written (already on the list, replayed):
    counted, never proposed. Never raises, never blocks, O(1); a flag of ``off`` returns before anything is touched."""
    try:
        if mode() != "shadow":
            return
        if not landed:
            _tally("live_noop")
            return
        uid, text = (user_id or "").strip(), (item or "").strip()
        if not uid or not text:
            return
        if user_erase_gate.in_progress(uid):
            _tally("dropped_erased")
            return
        _ensure_worker()
        _q.put_nowait(("add", uid, source, list_class(list_type), "added", text, (list_name or list_type or "shopping").strip() or "shopping",
                       user_erase_gate.generation(uid), time.perf_counter()))
    except queue.Full:
        _tally("queue_full")
    except Exception:  # noqa: BLE001 - shadow must never break the turn
        return


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
    global _roster_cache, _worker
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
