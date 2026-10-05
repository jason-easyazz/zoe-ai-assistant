"""Hard guard: tests, harnesses and scripts must never write the HOUSEHOLD palace.

Why this exists (docs/knowledge/memory-loss-audit-2026-10-05.md): between 2026-07-05 and
2026-09-27 the zoe-data test suite ran ~477 times on the box and every run wrote audit rows for the
owner's real id (``jason``) into the live palace — 7,078 ``ingest`` rows and 860 ``archive`` rows for
rows that never existed there (the test replaced the drawers collection with an in-memory fake but
``MemoryService._audit_collection`` opened a real ``chromadb.PersistentClient`` at the default
``~/.mempalace``). The audit then claimed ingests the palace never held. ``tests/conftest.py`` pins the
directory (#1773), but a pin is configuration: ``pytest --noconftest``, a script, or a harness that
imports the service modules bypasses it silently.

This module is the backstop at the doors every writer uses: ``memory_service._palace_client`` (open),
``MemoryService.ingest`` / ``_write_row`` / ``_append_audit_sync`` / ``delete_user`` (user-aware writes)
and ``get_drawers_collection`` (a write-checking proxy for every direct drawer writer — ``col.upsert`` /
``update`` / ``add`` / ``delete`` in the digest passes, ``tick_access``, supersede, ``zoe_agent``). When the
process is NOT the service — a pytest session, or a process that declared itself a harness with
``ZOE_HARNESS=1`` — opening the live palace from pytest, or writing it under a non-synthetic user id,
raises :class:`LiveStoreViolation`.

Trips are *loud*: ``LiveStoreViolation`` derives from ``BaseException`` so the broad ``except Exception``
handlers all over the write paths (best-effort audit, extractors, consolidation) cannot swallow it, and
every trip increments :func:`trip_count` so a test can also assert "nothing tripped".

What counts as "live": the real user's ``~/.mempalace`` resolved ONCE at import from the password
database (a test that monkeypatches ``$HOME`` cannot move it), ``MEMPALACE_DATA_DIR`` as it was at import
when it is not inside the system temp dir (conftest pins always are), and ``ZOE_LIVE_PALACE_DIR``
(comma-separated, additive, read per call — for a relocated install and for tests).

Stdlib-only (importable by ``memory_service`` without cycles). The service process is never a
"non-service context": it has neither pytest loaded nor ``ZOE_HARNESS`` set.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading

from user_filters import GUEST_USERS, is_synthetic_user


class LiveStoreViolation(BaseException):
    """A test/harness/script tried to open or write the household palace.

    ``BaseException`` on purpose: ``except Exception`` handlers (there are dozens on the memory write
    paths, all "best effort") must not turn a guard trip into a logged warning and a green test."""


_TRIPS = 0
_TRIPS_LOCK = threading.Lock()


def trip_count() -> int:
    """How many violations have been raised in this process (assert it is 0 in a test)."""
    return _TRIPS


def _trip(message: str) -> LiveStoreViolation:
    global _TRIPS
    with _TRIPS_LOCK:
        _TRIPS += 1
    return LiveStoreViolation(message)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _real(path: str) -> str:
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))


def _real_home() -> str:
    """The account's home from the password database — never ``$HOME`` (tests patch that)."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir
    except Exception:  # noqa: BLE001 — non-POSIX / no passwd entry
        return os.path.expanduser("~")


def _under_tempdir(path: str) -> bool:
    tmp = _real(tempfile.gettempdir())
    return path == tmp or path.startswith(tmp + os.sep)


def _import_time_live_dirs() -> frozenset[str]:
    dirs = {_real(os.path.join(_real_home(), ".mempalace"))}
    env = os.environ.get("MEMPALACE_DATA_DIR")
    if env:
        r = _real(env)
        if not _under_tempdir(r):          # conftest pins are always throwaway dirs under the temp dir
            dirs.add(r)
    return frozenset(dirs)


_LIVE_DIRS_AT_IMPORT = _import_time_live_dirs()


def live_palace_dirs() -> frozenset[str]:
    """Resolved paths that count as the household palace (see the module docstring)."""
    extra = [p.strip() for p in os.environ.get("ZOE_LIVE_PALACE_DIR", "").split(",") if p.strip()]
    return _LIVE_DIRS_AT_IMPORT | frozenset(_real(p) for p in extra)


def is_live_palace(data_dir: str) -> bool:
    return _real(data_dir) in live_palace_dirs()


def non_service_context() -> str:
    """Why this process is not the zoe-data service ("" when it is the service).

    * ``pytest`` — a pytest session is loaded or running a test;
    * ``harness`` — ``ZOE_HARNESS`` is set (scripts that import service modules declare it)."""
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return "pytest"
    if _truthy(os.environ.get("ZOE_HARNESS")):
        return "harness"
    return ""


def assert_palace_open_allowed(data_dir: str) -> None:
    """Opening the live palace from a test session is always a bug (reads too: a test that reads it
    asserts against the household's data, and a test that opens it can write it)."""
    if non_service_context() == "pytest" and is_live_palace(data_dir):
        raise _trip(
            f"refusing to open the live palace ({data_dir}) from a pytest session: tests must run "
            "against a throwaway MEMPALACE_DATA_DIR (services/zoe-data/tests/conftest.py pins one; "
            "do not run with --noconftest or override MEMPALACE_DATA_DIR)"
        )


def assert_write_allowed(data_dir: str, user_id: str, op: str) -> None:
    """A write by ``user_id`` into ``data_dir`` — refused for a non-service process that targets the
    live palace under a real (non-synthetic, non-guest) id. Harnesses that legitimately exercise the
    live service use ``demo_*`` ids and go through the HTTP API, not this in-process door."""
    ctx = non_service_context()
    if not ctx or not is_live_palace(data_dir):
        return
    if ctx == "harness" and user_id not in GUEST_USERS and is_synthetic_user(user_id):
        return  # a declared harness may use its own throwaway ids (never the guest sentinel)
    raise _trip(
        f"refusing {op} for user_id={user_id!r} against the live palace ({data_dir}) from a "
        f"{ctx} context: only the zoe-data service writes the household's memory. Point "
        "MEMPALACE_DATA_DIR at a scratch directory (or use a demo_* id through the HTTP API)."
    )


class GuardedCollection:
    """Write-checking proxy around a chroma collection, handed out ONLY to a non-service process that
    opened the live palace (a harness; pytest cannot open it at all). Reads pass straight through. A
    mutation is allowed only when every row it names belongs to a synthetic id; ``delete(ids=...)`` and
    an ids-only ``update`` carry no user, so they are refused (they cannot be proven synthetic)."""

    _MUTATORS = ("add", "upsert", "update", "delete")

    def __init__(self, inner, data_dir: str):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_data_dir", data_dir)

    def __getattr__(self, name):
        attr = getattr(object.__getattribute__(self, "_inner"), name)
        if name not in self._MUTATORS:
            return attr
        data_dir = object.__getattribute__(self, "_data_dir")

        def guarded(*args, **kwargs):
            metas = kwargs.get("metadatas")
            if metas is None and name in ("add", "upsert", "update") and len(args) >= 3:
                metas = args[2]
            users = {str((m or {}).get("user_id") or (m or {}).get("wing") or "") for m in (metas or [])}
            for uid in users or {""}:
                assert_write_allowed(data_dir, uid, f"collection.{name}")
            return attr(*args, **kwargs)

        return guarded


def guard_collection(col, data_dir: str):
    """The collection to hand a caller: the raw one for the service, a :class:`GuardedCollection` for a
    non-service process on the live palace."""
    if non_service_context() and is_live_palace(data_dir):
        return GuardedCollection(col, data_dir)
    return col
