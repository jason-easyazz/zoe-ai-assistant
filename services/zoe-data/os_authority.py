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

Erase reach: ``erase_member`` (``MemoryService.delete_user``) and ``erase_item`` (by digest; no production verb calls it yet), both
fail closed while the flag is ``kernel``. The wire client is ``os_shadow``'s (kernel protocol v1); NO import from ``labs/``.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import socket
import threading
import time
from collections import Counter
from typing import NamedTuple, Optional

import os_shadow
import user_erase_gate

logger = logging.getLogger(__name__)

FLAG = "ZOE_OS_LIST_ADD_AUTHORITY"
#: the propose budget (health when due + publish + act). The healthy hop is ~1.4 ms p50 (shadow record); 150 ms is ~100x that, and it
#: is the MOST a hanging kernel can add to one voice turn: the circuit then skips the kernel for CIRCUIT_S, so it is at most once per 5 s.
TIMEOUT_S = 0.15
CIRCUIT_S = 5.0
HEALTH_TTL_S = 5.0
ERASE_TIMEOUT_S = 8.0
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
    """A kernel erase could not be confirmed while the flag is ``kernel``. NOT a RuntimeError (see ``os_shadow.ShadowForgetUnavailable``)."""


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
    """The kernel's ``authority.digest``: what a reconcile or an erase sends instead of the words."""
    return hashlib.sha256(item.strip().lower().encode()).hexdigest()[:16]


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
    k = _kernel()
    k.down_until = 0.0                     # this module keeps its own circuit
    conn = k.conn(skill)
    conn.sock.settimeout(timeout)
    return k.call(skill, op, **args)


def _healthy() -> str:
    """'' when the kernel answered health inside the TTL window, else the fallback code."""
    if time.monotonic() - _state["health_at"] < HEALTH_TTL_S:
        return ""
    h = _call(os_shadow.SKILL_BRAIN, "health", TIMEOUT_S)
    if not isinstance(h, dict) or h.get("alive") is not True or h.get("schema") != SCHEMA:
        return "unhealthy"
    if h.get("last_write_ok") is False:
        return "write_failed"
    _state["health_at"] = time.monotonic()
    return ""


def _propose(user_id: str, item: str, list_name: str) -> Decision:
    try:
        bad = _healthy()
        if bad:
            return Decision("fallback", bad)
        ev = _call(os_shadow.SKILL_VOICE, "publish", TIMEOUT_S, kind="utterance", subject=user_id,
                   payload={"text": "authority list add"})
        seq = ev.get("seq") if isinstance(ev, dict) else None
        if not isinstance(seq, int) or isinstance(seq, bool):
            return Decision("fallback", "protocol")
        rec = _call(os_shadow.SKILL_BRAIN, "act", TIMEOUT_S, action="list.add", on_behalf_of=user_id,
                    args={"item": item, "list": list_name, "asked": seq}, caused_by=[seq])
        proof = rec.get("proof") if isinstance(rec, dict) else None
        if rec.get("verified") is True and rec.get("outcome") == "verified" and isinstance(proof, dict) and proof.get("item") == item:
            return Decision("kernel", "verified")
        return Decision("fallback", "unverified")
    except os_shadow.KernelRefused as exc:
        code = os_shadow.reason_code(exc.reason)
        return Decision("refused", code) if code in REAL_REFUSALS else Decision("fallback", "refused_" + code)
    except (socket.timeout, TimeoutError):
        return Decision("fallback", "timeout")
    except (os_shadow.KernelUnavailable, FileNotFoundError, ConnectionError):     # not provisioned, no socket, refused, closed
        return Decision("fallback", "unavailable")
    except os_shadow.KernelProtocol:
        return Decision("fallback", "protocol")
    except OSError:
        return Decision("fallback", "socket")
    except Exception:  # noqa: BLE001 - nothing the kernel does may break the add
        return Decision("fallback", "error")


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
        if uid not in os_shadow.roster():           # a guest, the household, or a member added after the roster was written
            return _note(Decision("fallback", "not_in_roster"), 0.0)
        if user_erase_gate.in_progress(uid):
            return _note(Decision("fallback", "erasing"), 0.0)
        if time.monotonic() < _state["down_until"]:
            return _note(Decision("fallback", "circuit"), 0.0)
        name = (list_name or list_type or "shopping").strip() or "shopping"
        fut = asyncio.get_running_loop().run_in_executor(_pool, _propose, uid, text, name)
        try:
            d = await asyncio.wait_for(fut, timeout=TIMEOUT_S)
        except asyncio.TimeoutError:
            d = Decision("fallback", "timeout")
        if d.code in ("timeout", "unavailable", "socket", "protocol", "unhealthy", "write_failed"):
            _state["down_until"] = time.monotonic() + CIRCUIT_S
            _state["health_at"] = -1e9
        return _note(d, (time.perf_counter() - started) * 1000)
    except Exception:  # noqa: BLE001
        return _note(Decision("fallback", "error"), (time.perf_counter() - started) * 1000)


# ---- erase reach ----------------------------------------------------------------------------------------------------------------
async def _erase(op: str, **args) -> dict:
    loop = asyncio.get_running_loop()
    out = await asyncio.wait_for(loop.run_in_executor(_pool, lambda: _call(os_shadow.SKILL_PANEL, op, ERASE_TIMEOUT_S, **args)),
                                 timeout=ERASE_TIMEOUT_S + 1)
    if not isinstance(out, dict) or out.get("verified") is not True:
        raise AuthorityEraseFailed("not verified")
    return out


async def erase_member(user_id: str) -> int:
    """Everything of the member in the kernel's record and list store. With the flag ``kernel``: raises ``AuthorityEraseFailed``
    unless the kernel's read-back found nothing left (so the delete fails and is retried). With it off: a no-op (the shadow's
    ``forget_user`` still forgets the member, and the kernel rebuilds its rows from what the forget left)."""
    if mode() != "kernel" or not os_shadow.provisioned():
        return 0
    try:
        return int((await _erase("erase_member", subject=user_id)).get("erased", 0))
    except AuthorityEraseFailed:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("OS_AUTHORITY erase NOT confirmed op=erase_member code=%s", type(exc).__name__)
        raise AuthorityEraseFailed(type(exc).__name__) from exc


async def erase_item(item: str) -> int:
    """"Forget that" on one list item, household-wide, by digest (the words never cross the wire). Same fail-closed rule."""
    if mode() != "kernel" or not os_shadow.provisioned():
        return 0
    try:
        return int((await _erase("erase_item", digest=digest(item))).get("erased", 0))
    except AuthorityEraseFailed:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("OS_AUTHORITY erase NOT confirmed op=erase_item code=%s", type(exc).__name__)
        raise AuthorityEraseFailed(type(exc).__name__) from exc


def kernel_digests(timeout: float = 5.0) -> list:
    """The kernel's list rows as digests (the reconcile script). Raises when the kernel cannot answer."""
    return list(_pool.submit(lambda: _call(os_shadow.SKILL_PANEL, "list_digest", timeout)).result(timeout + 1)["digests"])


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
