"""Zoe OS kernel as the AUTHORITY for one verb, list add (stage (c), docs/knowledge/zoe-os-integration.md).

Flag ``ZOE_OS_LIST_ADD_AUTHORITY`` = ``off`` (default) | ``kernel``. With ``kernel``, a spoken list add by a member the kernel knows is
PROPOSED to the kernel before production writes the row, inside the list's write transaction: the kernel's receipt decides.

* verified receipt -> production writes the row as today (``list_items`` stays the household's read model: the panel, the REST API
  and every reader are unchanged) and the shadow does NOT propose it again;
* a REAL policy refusal (``REAL_REFUSALS``: the member withdrew the brain's grant) -> nothing is written and the caller says so
  (``ListAddRefused``);
* anything else (kernel down, slow past ``TIMEOUT_S``, unhealthy, unverified, a refusal that is not policy, a member or guest the
  kernel does not know, a member being erased) -> the old path performs the add exactly as today and the event is a COUNTED
  fallback, one codes-only log line. The turn never fails or stalls because of the kernel.

ALL OR NONE (``all_or_none``): a spoken add of several items is one production transaction. When it rolls back after the kernel had
already verified some of its items (item 1 verified, item 3 refused), every granted item is taken back in the kernel with a
``list.remove`` act by the brain (the kernel has no batch verb, and its ``undo`` reverses only a person's LAST action and refuses a
second undo in a row, so it cannot take back two items). A take-back that is not verified is recorded durably (ids only,
``ROLLBACK_PENDING``) for the reconcile to report, and the add is never reported done.

Erase reach: ``erase_member`` (``MemoryService.delete_user``) connects as the kernel's ``eraser`` (``erase-creds.json``: the
member-delete path's own credential) and ``erase_item`` (one named member's item, by digest; no production verb calls it yet) goes
through the panel with a speaker assertion for that member (``os_speaker``); both fail closed while the flag is ``kernel``, and an
unprovisioned kernel fails them too. ``kernel_digests`` reads ``list_digest`` as the ``reconciler`` (keyed digests). The wire client is
``os_shadow``'s (kernel protocol v1); NO import from ``labs/``.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import hashlib
import json
import logging
import os
import socket
import threading
import time
from collections import Counter
from typing import NamedTuple, Optional

import os_shadow
import os_speaker
import user_erase_gate

logger = logging.getLogger(__name__)

FLAG = "ZOE_OS_LIST_ADD_AUTHORITY"
#: the propose budget (health when due + publish + act). The healthy hop is ~1.4 ms p50 (shadow record); 150 ms is ~100x that, and it
#: is the MOST a hanging kernel can add to one voice turn: the circuit then skips the kernel for CIRCUIT_S, so it is at most once per 5 s.
TIMEOUT_S = 0.15
CIRCUIT_S = 5.0
HEALTH_TTL_S = 5.0
ERASE_TIMEOUT_S = 8.0
ROLLBACK_TIMEOUT_S = 1.0           # per take-back (publish + act): the turn has already failed, so a little more than an add
ROLLBACK_PENDING = "authority-rollback-pending.json"     # beside creds.json: member ids, counts and times, NEVER an item or a list name
SCHEMA = 1
#: the kernel refusal codes (``os_shadow.reason_code``) that are a person's decision and are honoured. ``forgotten`` is one: the
#: kernel's shield says this text was forgotten, and falling back would let the old path write it straight back. Every other code
#: falls back: ``manifest`` / ``params`` / ``cause`` / ``other`` describe the kernel's own limits or a race, never a person's wish.
REAL_REFUSALS = frozenset({"no_grant", "forgotten"})
REFUSAL_REPLY = "I can't add to your lists: that permission is switched off for me."
FORGOTTEN_REPLY = "I was asked to forget that, so I haven't added it."


def refusal_reply(code: str) -> str:
    """The spoken line for an honoured refusal. No refused text is ever repeated."""
    return FORGOTTEN_REPLY if code == "forgotten" else REFUSAL_REPLY


class ListAddRefused(Exception):
    """The kernel refused the add for a person's reason (``REAL_REFUSALS``): nothing was written. Callers speak ``REFUSAL_REPLY``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AuthorityEraseFailed(Exception):
    """A kernel erase could not be confirmed while the flag is ``kernel``. NOT a RuntimeError (see ``os_shadow.ShadowForgetUnavailable``).
    ``refused`` True: the kernel ANSWERED no (``code`` ``refused_unproven``, ``refused_<reason>``), a real refusal, never "unavailable"."""

    def __init__(self, code: str, refused: bool = False) -> None:
        super().__init__(code)
        self.code, self.refused = code, refused


class Decision(NamedTuple):
    verdict: str        # off | kernel | refused | fallback
    code: str = ""


OFF = Decision("off")


def mode() -> str:
    try:
        import typed_env

        return "kernel" if str(typed_env.flag(FLAG)).strip().lower() == "kernel" else "off"
    except Exception:  # noqa: BLE001 - a flag read must never break a turn
        return "off"


DB_FLAG = "ZOE_OS_KERNEL_DB"


def kernel_db_path():
    """Where the kernel keeps its OWN database (owner decision 2026-11-15: its own file, beside Zoe's other stores, in the nightly
    backup). ONE setting, the same value the kernel unit passes as ``--db``; zoe-data only NAMES it (forget reach, the reconcile, the
    owner's steps) and never opens it: the socket is the only seam."""
    from pathlib import Path
    try:
        import typed_env

        raw = str(typed_env.flag(DB_FLAG)).strip()
    except Exception:  # noqa: BLE001
        raw = ""
    return Path(raw).expanduser() if raw else Path.home() / ".zoe" / "zoe-os" / "zoe-os.db"


def digest(item: str) -> str:
    """The kernel's ``authority.digest``: what ``erase_item`` sends instead of the words. NOT what ``list_digest`` returns (that is
    keyed: ``KernelRows.digest``)."""
    return hashlib.sha256(item.strip().lower().encode()).hexdigest()[:16]


def kernel_list_name(list_type: Optional[str], list_name: Optional[str]) -> str:
    """The list name the kernel is told (the act's ``list`` argument) and so the one its keyed digest covers: ONE mapping, used by
    ``propose_add`` and by the reconcile."""
    return (list_name or list_type or "shopping").strip() or "shopping"


# ---- counters and the one line per decision ---------------------------------------------------------------------------------------
_counts: Counter = Counter()
_lock = threading.Lock()


def counters() -> dict:
    with _lock:
        return dict(_counts)


def _note(d: Decision, ms: float) -> Decision:
    with _lock:
        _counts[f"{d.verdict}:{d.code}"] += 1
    logger.info("OS_AUTHORITY verb=list_add result=%s code=%s ms=%.1f", d.verdict, d.code or "-", ms)
    return d


# ---- the kernel side, on ONE thread (it owns the connections) ---------------------------------------------------------------------
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="os-authority")
_state = {"kernel": None, "down_until": 0.0, "health_at": -1e9}


def _kernel() -> "os_shadow._Kernel":
    if _state["kernel"] is None:
        _state["kernel"] = os_shadow._Kernel()
    return _state["kernel"]


def _call(skill: str, op: str, timeout: float, **args):
    return _call_until(skill, op, time.monotonic() + timeout, **args)


def _call_until(skill: str, op: str, until: float, **args):
    """One kernel call that may not run past ``until`` (``time.monotonic()``): connect, handshake, request and reply all draw on it."""
    k = _kernel()
    k.down_until = 0.0                     # this module keeps its own circuit
    return k.call(skill, op, until, **args)


def _healthy(until: float) -> str:
    """'' when the kernel answered health inside the TTL window, else the fallback code."""
    if time.monotonic() - _state["health_at"] < HEALTH_TTL_S:
        return ""
    h = _call_until(os_shadow.SKILL_BRAIN, "health", until)
    if not isinstance(h, dict) or h.get("alive") is not True or h.get("schema") != SCHEMA:
        return "unhealthy"
    if h.get("last_write_ok") is False:
        return "write_failed"
    _state["health_at"] = time.monotonic()
    return ""


class _Handoff:
    """Who owns a verified grant: the waiting turn or nobody. Set under ``lock`` on both sides, so a grant is either DELIVERED to a
    turn that is still waiting (it joins that turn's ledger) or seen by the worker as ABANDONED (the turn already fell back), never
    both and never neither."""

    __slots__ = ("lock", "gone", "delivered")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.gone = False
        self.delivered = False


#: the least budget an ``act`` is started with: an act begun closer to the deadline would mostly finish after the turn gave up
ACT_MIN_S = 0.02


def _propose(user_id: str, item: str, list_name: str, until: float, hand: _Handoff) -> Decision:
    """The whole proposal on the worker thread, against ONE deadline (``until``): the roster read (a file), health, publish and act
    together, never 150 ms each. No act is started without ``ACT_MIN_S`` of the budget left. A grant the kernel verified after the
    turn gave up is taken back here (``list.remove``) and, if that does not verify, recorded ids-only; an act whose answer never came
    (the deadline passed mid-act: the kernel may have committed it) is recorded the same way, for the reconcile."""
    acting = False
    try:
        if user_id not in os_shadow.roster():   # a guest, the household, or a member added after the roster was written
            return Decision("fallback", "not_in_roster")
        bad = _healthy(until)
        if bad:
            return Decision("fallback", bad)
        ev = _call_until(os_shadow.SKILL_VOICE, "publish", until, kind="utterance", subject=user_id,
                         payload={"text": "authority list add"})
        seq = ev.get("seq") if isinstance(ev, dict) else None
        if not isinstance(seq, int) or isinstance(seq, bool):
            return Decision("fallback", "protocol")
        if until - time.monotonic() < ACT_MIN_S:
            return Decision("fallback", "timeout")         # never act past (or at the edge of) the turn's deadline
        acting = True
        rec = _call_until(os_shadow.SKILL_BRAIN, "act", until, action="list.add", on_behalf_of=user_id,
                          args={"item": item, "list": list_name, "asked": seq}, caused_by=[seq])
        acting = False
        proof = rec.get("proof") if isinstance(rec, dict) else None
        if not (rec.get("verified") is True and rec.get("outcome") == "verified" and isinstance(proof, dict)
                and proof.get("item") == item):
            return Decision("fallback", "unverified")
        with hand.lock:
            if not hand.gone:
                hand.delivered = True
                return Decision("kernel", "verified")
        _abandoned(user_id, list_name, item)               # the turn fell back: production writes it the old way
        return Decision("fallback", "late")
    except os_shadow.KernelRefused as exc:
        code = os_shadow.reason_code(exc.reason)
        return Decision("refused", code) if code in REAL_REFUSALS else Decision("fallback", "refused_" + code)
    except Exception as exc:  # noqa: BLE001 - nothing the kernel does may break the add
        if acting:
            _record_late(user_id, "uncertain")             # sent, no answer: the kernel may hold it
        if isinstance(exc, (socket.timeout, TimeoutError)):
            return Decision("fallback", "timeout")
        if isinstance(exc, (os_shadow.KernelUnavailable, FileNotFoundError, ConnectionError)):   # not provisioned, no socket, closed
            return Decision("fallback", "unavailable")
        if isinstance(exc, os_shadow.KernelProtocol):
            return Decision("fallback", "protocol")
        if isinstance(exc, OSError):
            return Decision("fallback", "socket")
        return Decision("fallback", "error")


def _abandoned(member: str, list_name: str, item: str) -> None:
    """A verified grant nobody is waiting for: take it back now; if that does not verify, record it."""
    try:
        ok = _take_back_one(member, list_name, item)
    except Exception:  # noqa: BLE001
        ok = False
    with _lock:
        _counts["late:" + ("taken_back" if ok else "recorded")] += 1
    if not ok:
        _record_late(member, "late")


def _record_late(member: str, why: str) -> None:
    with _lock:
        _counts["late:" + why] += 1
    try:
        _pending_add([member], why)
    except Exception as exc:  # noqa: BLE001
        logger.error("OS_AUTHORITY late grant NOT recorded code=%s", type(exc).__name__)


async def propose_add(user_id: Optional[str], *, list_type: Optional[str], list_name: Optional[str], item: str) -> Decision:
    """THE decision. Called inside production's list write transaction, before the row is inserted. Never raises; returns within
    ``TIMEOUT_S`` (plus scheduling) whatever the kernel does."""
    if mode() != "kernel":
        return OFF
    started = time.perf_counter()
    uid = (user_id or "").strip()
    text = (item or "").strip()
    try:
        if not uid or not text:
            return _note(Decision("fallback", "empty"), 0.0)
        if user_erase_gate.in_progress(uid):
            return _note(Decision("fallback", "erasing"), 0.0)
        if _ledger.get() is None:                   # outside an all_or_none unit a grant could not be taken back: never asked
            return _note(Decision("fallback", "no_unit"), 0.0)
        if time.monotonic() < _state["down_until"]:
            return _note(Decision("fallback", "circuit"), 0.0)
        name = kernel_list_name(list_type, list_name)
        until = time.monotonic() + TIMEOUT_S          # ONE deadline: the turn's wait and the worker's whole proposal share it
        hand = _Handoff()
        fut = asyncio.get_running_loop().run_in_executor(_pool, _propose, uid, text, name, until, hand)
        try:
            d = await asyncio.wait_for(fut, timeout=TIMEOUT_S)
        except asyncio.TimeoutError:
            with hand.lock:                           # in memory, a few instructions: the worker holds it as briefly
                late = hand.delivered                 # verified in the instant the wait ran out: still this turn's
                hand.gone = not late
            d = Decision("kernel", "verified") if late else Decision("fallback", "timeout")
        if d.code in ("timeout", "unavailable", "socket", "protocol", "unhealthy", "write_failed", "late"):
            _state["down_until"] = time.monotonic() + CIRCUIT_S
            _state["health_at"] = -1e9
        if d.verdict == "kernel":
            ledger = _ledger.get()
            if ledger is not None:
                ledger.append((uid, name, text))
        return _note(d, (time.perf_counter() - started) * 1000)
    except Exception:  # noqa: BLE001
        return _note(Decision("fallback", "error"), (time.perf_counter() - started) * 1000)


# ---- all or none: a production rollback takes back what the kernel granted --------------------------------------------------------
_ledger: contextvars.ContextVar = contextvars.ContextVar("os_authority_ledger", default=None)
_pending_lock = threading.Lock()


class _Rollback:
    """``async with all_or_none():`` around ONE production write transaction. Every item the kernel verifies inside it is noted; if
    the block raises (the transaction rolled back), each is taken back in the kernel before the exception goes on. Nested use joins
    the outer ledger, so a spoken add of several items is one all-or-none unit."""

    async def __aenter__(self):
        self._token = _ledger.set([]) if _ledger.get() is None else None
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._token is None:
            return False
        granted = _ledger.get()
        _ledger.reset(self._token)
        if exc_type is not None and granted:
            await take_back(granted)
        return False


def all_or_none() -> _Rollback:
    return _Rollback()


def _take_back_one(member: str, list_name: str, item: str) -> bool:
    until = time.monotonic() + ROLLBACK_TIMEOUT_S
    k = _kernel()
    k.down_until = 0.0
    ev = k.call(os_shadow.SKILL_VOICE, "publish", until, kind="utterance", subject=member, payload={"text": "authority list add rollback"})
    seq = ev.get("seq") if isinstance(ev, dict) else None
    if not isinstance(seq, int) or isinstance(seq, bool):
        return False
    rec = k.call(os_shadow.SKILL_BRAIN, "act", until, action="list.remove", on_behalf_of=member,
                 args={"item": item, "list": list_name, "asked": seq}, caused_by=[seq])
    return isinstance(rec, dict) and rec.get("verified") is True and rec.get("outcome") == "verified"


def _take_back_all(granted: list) -> list:
    left = []
    for member, list_name, item in reversed(granted):
        try:
            ok = _take_back_one(member, list_name, item)
        except Exception:  # noqa: BLE001 - a failed take-back is recorded, never raised over the add's own error
            ok = False
        if not ok:
            left.append(member)
    return left


async def take_back(granted: list) -> int:
    """Take back every ``(member, list, item)`` the kernel verified for a write production then rolled back. Returns how many could
    NOT be taken back; those are recorded in ``ROLLBACK_PENDING`` (member id, count, time: never the words) for the reconcile. Never
    raises: the caller's own exception is what the add reports (nothing was added)."""
    left: list = list(m for m, _l, _i in granted)
    try:
        fut = asyncio.get_running_loop().run_in_executor(_pool, _take_back_all, list(granted))
        left = await asyncio.wait_for(fut, timeout=ROLLBACK_TIMEOUT_S * len(granted) + 1)
    except Exception:  # noqa: BLE001
        pass
    with _lock:
        _counts["rollback:" + ("incomplete" if left else "done")] += 1
    logger.warning("OS_AUTHORITY verb=list_add result=rollback code=%s n=%d left=%d", "incomplete" if left else "done", len(granted),
                   len(left))
    if left:
        try:
            await asyncio.get_running_loop().run_in_executor(None, _pending_add, left)
        except Exception as exc:  # noqa: BLE001
            logger.error("OS_AUTHORITY rollback NOT recorded code=%s", type(exc).__name__)
    return len(left)


def _pending_path():
    return os_shadow.state_dir() / ROLLBACK_PENDING


def pending_rollbacks() -> list:
    """The recorded take-backs that did not verify: ``[{"member", "n", "at"}]``. Raises ``OSError`` / ``ValueError`` when unreadable."""
    path = _pending_path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text("utf-8"))
    if not isinstance(raw, list):
        raise ValueError("shape")
    return raw


def _pending_write(rows: list) -> None:
    path = _pending_path()
    if not rows:
        path.unlink(missing_ok=True)
        return
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


#: why a row is in ``ROLLBACK_PENDING`` (closed codes): a take-back after a rollback that did not verify, a grant verified after the
#: turn gave up that could not be taken back, an act whose answer never came
PENDING_WHY = ("rollback", "late", "uncertain")


def _pending_add(members: list, why: str = "rollback") -> None:
    with _pending_lock:
        rows = pending_rollbacks()
        for member, n in Counter(members).items():
            rows.append({"member": member, "n": n, "at": int(time.time()), "why": why})
        _pending_write(rows)


def _pending_drop(member: str) -> None:
    """After a verified whole-member erase the member's recorded take-backs are moot (the kernel holds nothing of them)."""
    with _pending_lock:
        rows = pending_rollbacks()
        keep = [r for r in rows if r.get("member") != member]
        if len(keep) != len(rows):
            _pending_write(keep)


# ---- erase reach ----------------------------------------------------------------------------------------------------------------
def _erase_sync(skill: str, op: str, audience: Optional[str], args: dict) -> dict:
    """One erase op. Through the panel it carries a speaker assertion for ``args["subject"]`` (minted fresh per request); a
    ``speaker unproven`` answer is retried ONCE with the secret and generation read again (a restart, or a generation rolled mid-run).
    Runs on the worker: every file it reads (creds, the identity secret) is read off the event loop."""
    if not os_shadow.provisioned():
        raise AuthorityEraseFailed("not_provisioned")          # kernel mode, no kernel credentials: the record cannot be reached
    for attempt in (0, 1):
        extra = os_speaker.speaker_arg(args["subject"], audience) if audience else {}
        try:
            return _call(skill, op, ERASE_TIMEOUT_S, **args, **extra)
        except os_shadow.KernelRefused as exc:
            if attempt or not audience or not os_speaker.is_unproven(exc.reason):
                raise
    raise AssertionError("unreachable")


async def _erase(skill: str, op: str, audience: Optional[str], **args) -> dict:
    loop = asyncio.get_running_loop()
    try:
        out = await asyncio.wait_for(loop.run_in_executor(_pool, _erase_sync, skill, op, audience, args), timeout=ERASE_TIMEOUT_S + 1)
    except os_shadow.KernelRefused as exc:
        code = "refused_unproven" if os_speaker.is_unproven(exc.reason) else "refused_" + os_shadow.reason_code(exc.reason)
        raise AuthorityEraseFailed(code, refused=True) from exc
    if not isinstance(out, dict) or out.get("verified") is not True:
        raise AuthorityEraseFailed("not_verified")
    return out


async def _confirmed(op: str, call) -> dict:
    try:
        return await call
    except AuthorityEraseFailed as exc:
        logger.error("OS_AUTHORITY erase NOT confirmed op=%s code=%s", op, exc.code)
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("OS_AUTHORITY erase NOT confirmed op=%s code=%s", op, type(exc).__name__)
        raise AuthorityEraseFailed(type(exc).__name__) from exc


async def erase_member(user_id: str) -> int:
    """Everything of the member in the kernel's record and list store, as the kernel's ``eraser`` (the member-delete path's own
    credential, ``erase-creds.json``). With the flag ``kernel``: raises ``AuthorityEraseFailed`` unless the kernel's read-back found
    nothing left, so the delete fails and is retried: an unprovisioned kernel, a missing eraser credential and a refusal all fail it.
    With the flag off: a no-op (the shadow's ``forget_user`` still forgets the member)."""
    if mode() != "kernel":
        return 0
    out = await _confirmed("erase_member", _erase(os_speaker.ERASER, "erase_member", None, subject=user_id))
    try:
        await asyncio.get_running_loop().run_in_executor(None, _pending_drop, user_id)
    except Exception as exc:  # noqa: BLE001 - the ids-only record is erased by the delete's retry; never fail a verified erase on it
        logger.warning("OS_AUTHORITY rollback record not pruned code=%s", type(exc).__name__)
    return int(out.get("erased", 0))


async def erase_item(user_id: str, item: str) -> int:
    """"Forget that" on one list item of ONE member (``user_id``, whom the CALLER has verified is the one speaking), by digest (the
    words never cross the wire), through the panel with that member's ``forget.scoped`` speaker assertion. Same fail-closed rule;
    a kernel refusal (``speaker unproven`` included) raises ``AuthorityEraseFailed`` with ``refused`` True."""
    if mode() != "kernel":
        return 0
    if not (user_id or "").strip():
        raise AuthorityEraseFailed("no_member")
    out = await _confirmed("erase_item", _erase(os_shadow.SKILL_PANEL, "erase_item", "forget.scoped", subject=user_id.strip(),
                                                digest=digest(item)))
    return int(out.get("erased", 0))


class KernelRows:
    """The kernel's list rows as KEYED digests, plus the means to digest production's rows the same way. The key is the kernel's
    per-start digest key: held only here, never printed, logged or stored (``repr`` shows a count)."""

    def __init__(self, digests: list, key: bytes) -> None:
        self.digests = list(digests)
        self._key = key

    def digest(self, member: str, list_name: str, item: str) -> str:
        return os_speaker.keyed_digest(self._key, member, list_name, item)

    def __repr__(self) -> str:
        return f"KernelRows({len(self.digests)} rows)"


def kernel_digests(timeout: float = 5.0) -> KernelRows:
    """``list_digest`` as the ``reconciler`` (``reconcile-creds.json``, read now: its key is fresh at every kernel start). Raises when
    the kernel cannot answer."""
    path, cred, key = os_speaker.reconciler_creds()
    conn = os_shadow._Conn(path, os_speaker.RECONCILER, cred, time.monotonic() + timeout)
    try:
        out = conn.call("list_digest", time.monotonic() + timeout)
    finally:
        conn.close()
    if not isinstance(out, dict) or not isinstance(out.get("digests"), list):
        raise os_shadow.KernelProtocol("bad reply")
    return KernelRows(out["digests"], key)


def _reset_for_tests() -> None:
    def _drop():
        k = _state["kernel"]
        if k is not None:
            for skill in list(k.conns):
                k.drop(skill)
    _pool.submit(_drop).result(5)
    _state.update(kernel=None, down_until=0.0, health_at=-1e9)
    with _lock:
        _counts.clear()
