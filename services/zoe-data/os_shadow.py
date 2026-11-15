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
  bounded queue. A single worker THREAD does all socket work, so a slow or dead kernel can neither slow nor break a turn.
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

#: shadow verb -> (kernel action, the proof key the kernel's read-back returns, the argument it must equal)
VERBS = {"list_add": ("list.add", "item", "item"), "list_remove": ("list.remove", "item", "item"),
         "reminder_create": ("remind.set", "note", "note"), "alarm_create": ("alarm.set", "alarm", "time")}
DEFAULT_VERBS = "list_add"
REMIND_MAX_MIN = 10080         # the kernel's ``remind.set`` takes 1..10080 whole minutes from now

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
RESULTS = ("match", "not_expressible", "kernel_refused_live_did", "kernel_unverified_live_did", "kernel_did_live_refused", "item_differs",
           "list_differs", "kernel_unavailable", "timeout", "kernel_malformed", "member_not_in_roster", "dropped_erased",
           "queue_full")
# "forgotten" is matched FIRST: the kernel's shield refusals mention arguments or causes too ("forgotten_action_arguments",
# "derives from a forgotten event"), and a forgotten refusal must never be read as one of the kernel's own limits.
_REASONS = (("forgotten", "forgotten"), ("no grant", "no_grant"), ("manifest", "manifest"), ("argument", "params"),
            ("cause", "cause"), ("cited", "cause"), ("cites", "cause"))


def reason_code(reason: str) -> str:
    """The kernel's fixed refusal text mapped to a closed code; never echoed."""
    low = (reason or "").lower()
    return next((code for needle, code in _REASONS if needle in low), "other")


def compare(live: str, kernel: Optional[dict], sent: dict, verb: str = "list_add") -> tuple[str, str]:
    """``(result, kernel_state)``. ``live``: added | refused (added = the live write landed, whatever the verb). ``kernel``: the act
    receipt (a dict), ``{"refused": reason}`` or None. ``sent``: the kernel args as proposed. ``item_differs`` means the kernel's
    read-back names a different value than was sent (the item, the reminder note, the alarm time). The member is the connection's own
    business: a receipt names the person it acted for, so a mismatch there is ``kernel_malformed``."""
    _action, pkey, akey = VERBS[verb]
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
    if not isinstance(proof, dict) or pkey not in proof:
        return "kernel_malformed", state
    if proof[pkey] != sent[akey]:
        return "item_differs", state
    if "list" in proof and proof["list"] != sent.get("list"):
        return "list_differs", state
    return "match", state


# ---- counters and the one line per shadowed write ------------------------------------------------------------------------------------
_counts: Counter = Counter()     # result -> n, all verbs (what the list-add tests read)
_vcounts: Counter = Counter()    # (verb, result) -> n
_hops: list = []                 # kernel hop ms of compared writes (bounded)
_lock = threading.Lock()
LIST_CLASSES = ("shopping", "todo", "personal")


def counters() -> dict:
    with _lock:
        return dict(_counts)


def counters_by_verb() -> dict:
    """``{verb: {result: n}}`` since start-up (includes the codes that never log a line, e.g. ``live_noop``, ``queue_full``)."""
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
                verb, source, cls, live, kernel_state, result, reason or "-", hop_ms, q_ms)


# ---- the worker ---------------------------------------------------------------------------------------------------------------------
_q: "queue.Queue" = queue.Queue(maxsize=QUEUE_MAX)
_worker: Optional[threading.Thread] = None
_worker_guard = threading.Lock()


def _ensure_worker() -> None:
    """Lock-free when the worker is up (the normal case: it is started at import while the flag is ``shadow``, off every turn).
    Otherwise ONE caller starts it; a caller that finds another one starting it never waits on the guard: its job is queued and
    drained by the worker that other caller is starting."""
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
    replayed = False
    while True:
        job = _q.get()
        if job[0] == "stop":
            for skill in list(kernel.conns):
                kernel.drop(skill)
            return
        try:
            if not replayed:
                replayed = _replay_pending(kernel)
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
    started = time.perf_counter()
    q_ms = (started - queued_at) * 1000
    if user_id not in roster():
        _tally("member_not_in_roster", verb=verb)
        return
    if user_erase_gate.generation(user_id) != gen or user_erase_gate.in_progress(user_id):
        _tally("dropped_erased", verb=verb)
        return
    try:
        sent, why = kernel_args(verb, raw, wall_at)
    except Exception:  # noqa: BLE001 - a field the client could not read is counted against the kernel, never silently lost
        sent, why = None, "parse"
    if sent is None:              # the live write is something the kernel's verb cannot say: a disagreement, never sent
        _tally("not_expressible", verb=verb)
        _log(verb, source, cls, live, "none", "not_expressible", why, 0.0, q_ms)
        return
    action = VERBS[verb][0]
    result_kernel: Optional[dict] = None
    reason, result = "", ""
    try:
        ev = kernel.call(SKILL_VOICE, "publish", kind="utterance", subject=user_id, payload={"text": utterance_text(verb)})
        seq = ev.get("seq") if isinstance(ev, dict) else None
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise KernelProtocol("no seq")
        if time.perf_counter() - started > JOB_DEADLINE_S:
            raise socket.timeout()
        rec = kernel.call(SKILL_BRAIN, "act", action=action, on_behalf_of=user_id, args={**sent, "asked": seq}, caused_by=[seq])
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
        result, kstate = compare(live, result_kernel, sent, verb)
    _tally(result, hop_ms if kstate != "none" else None, verb=verb)
    _log(verb, source, cls, live, kstate, result, reason, hop_ms, q_ms)


def kernel_args(verb: str, raw: dict, wall_at: float) -> tuple[Optional[dict], str]:
    """The live write's fields as the kernel verb's args, or ``(None, reason_code)`` when the verb cannot express it. Runs on the
    WORKER (any parsing or import happens off the turn). ``wall_at``: the wall clock when the live write committed."""
    if verb in ("list_add", "list_remove"):
        return {"item": raw["item"], "list": raw["list"]}, ""
    if verb == "alarm_create":
        hhmm = str(raw.get("time") or "")
        if len(hhmm) != 5 or hhmm[2] != ":" or not (hhmm[:2] + hhmm[3:]).isdigit() or int(hhmm[:2]) > 23 or int(hhmm[3:]) > 59:
            return None, "time"
        return {"time": hhmm}, ""
    # reminder_create: the kernel's remind.set is ONE-SHOT, in whole minutes from now (1..10080)
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
    minutes = int(round((run_at.timestamp() - wall_at) / 60.0))
    if not 1 <= minutes <= REMIND_MAX_MIN:
        return None, "range"
    return {"note": raw["note"], "in_minutes": minutes}, ""


# ---- the hook -----------------------------------------------------------------------------------------------------------------------
def list_class(list_type: Optional[str]) -> str:
    lt = (list_type or "").strip().lower()
    return lt if lt in LIST_CLASSES else "other"


def note_act(verb: str, user_id: Optional[str], *, source: str, raw: dict, cls: str = "-", landed: bool = True) -> None:
    """THE hook: a live write of ``verb`` committed (call AFTER the commit). ``raw``: the fields ``kernel_args`` turns into the
    kernel's args (nothing is parsed here). ``landed`` False = nothing was written: counted, never proposed. Never raises, never
    blocks, O(1); with the switch off, or the verb not in ``ZOE_OS_SHADOW_VERBS``, it returns before anything is touched."""
    try:
        if not wants(verb):
            return
        if not landed:
            _tally("live_noop", verb=verb)
            return
        uid = (user_id or "").strip()
        if not uid:
            return
        if user_erase_gate.in_progress(uid):
            _tally("dropped_erased", verb=verb)
            return
        _ensure_worker()
        _q.put_nowait(("act", verb, uid, source, cls, "added", raw, user_erase_gate.generation(uid), time.perf_counter(), time.time()))
    except queue.Full:
        _tally("queue_full", verb=verb)
    except Exception:  # noqa: BLE001 - shadow must never break the turn
        return


def note_add(user_id: Optional[str], *, source: str, list_type: Optional[str], list_name: Optional[str], item: Optional[str],
             landed: bool = True) -> None:
    """A list add finished (call AFTER the live commit). ``landed`` False = nothing was written (already on the list, replayed)."""
    text = (item or "").strip()
    if landed and not text:
        return
    note_act("list_add", user_id, source=source, cls=list_class(list_type), landed=landed,
             raw={"item": text, "list": (list_name or list_type or "shopping").strip() or "shopping"})


def note_list_remove(user_id: Optional[str], *, source: str, list_type: Optional[str], list_name: Optional[str],
                     item: Optional[str]) -> None:
    """A list item was removed (call AFTER the commit). The kernel's post-condition is "that item is not on the list"."""
    text = (item or "").strip()
    if text:
        note_act("list_remove", user_id, source=source, cls=list_class(list_type),
                 raw={"item": text, "list": (list_name or list_type or "shopping").strip() or "shopping"})


def note_reminder_create(user_id: Optional[str], *, source: str, note: Optional[str], due_date: Optional[str],
                         due_time: Optional[str], recurring: bool) -> None:
    """A reminder row committed (``reminder_service.create_reminder_record``): its stored fields, parsed on the worker."""
    text = (note or "").strip()
    if text:
        note_act("reminder_create", user_id, source=source, cls="repeat" if recurring else "once",
                 raw={"note": text, "due_date": due_date, "due_time": due_time, "recurring": bool(recurring)})


def note_alarm_create(user_id: Optional[str], *, source: str, time_hhmm: Optional[str], repeat: Optional[str]) -> None:
    """An alarm row committed (``alarms.create_alarm``). The kernel's alarm is one time per person: the repeat rule rides ``cls``."""
    note_act("alarm_create", user_id, source=source, cls="once" if (repeat or "once") == "once" else "repeat",
             raw={"time": str(time_hhmm or "")})


# ---- forget -------------------------------------------------------------------------------------------------------------------------
def _pending_path() -> Path:
    return state_dir() / "pending-forgets.json"


def _pending_read() -> list:
    try:
        raw = json.loads(_pending_path().read_text("utf-8"))
        return [str(x) for x in raw] if isinstance(raw, list) else []
    except (OSError, ValueError):
        return []


def _pending_write(members: list) -> None:
    path = _pending_path()
    if not members:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(sorted(set(members)), fh)
    os.replace(tmp, path)


def _replay_pending(kernel: _Kernel) -> bool:
    """Forgets that could not reach the kernel while the flag was off are re-sent (whole member: the safe over-forget) before the
    first job after a restart. True once nothing is pending."""
    left = []
    kernel.down_until = 0.0           # a forget never waits out the circuit an earlier failed add opened
    for member in _pending_read():
        try:
            _kernel_forget(kernel, member, None, None)
        except Exception:  # noqa: BLE001
            left.append(member)
    _pending_write(left)
    return not left


def _kernel_forget(kernel: _Kernel, member: str, entity: Optional[str], topic: Optional[str]) -> int:
    if member not in roster():
        return 0                      # never proposed for them: nothing recorded, nothing to forget
    scoped: dict = {"entity": entity} if entity is not None else ({"topic": topic} if topic is not None else {})
    try:
        out = kernel.call(SKILL_PANEL, "forget", subject=member, actor=member, **scoped)
    except KernelRefused:
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
    if not provisioned():
        return 0                      # no kernel was ever installed here: nothing recorded, no socket touched
    strict = mode() == "shadow"
    fut: concurrent.futures.Future = concurrent.futures.Future()
    try:
        _ensure_worker()
        _q.put(("forget", user_id, entity, topic, fut), timeout=2.0)
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=FORGET_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        if strict:
            logger.error("OS_SHADOW forget NOT confirmed code=%s", type(exc).__name__)
            raise ShadowForgetUnavailable(type(exc).__name__) from exc
        try:                          # flag off: do not block the erase, but never lose it - replayed before the next shadow job
            _pending_write(_pending_read() + [user_id])
        except OSError:
            pass
        logger.warning("OS_SHADOW forget deferred (flag off) code=%s", type(exc).__name__)
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
    global _roster_cache, _worker
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
