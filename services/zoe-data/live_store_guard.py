"""Hard guard: tests, harnesses and scripts must never write the HOUSEHOLD palace.

Why this exists (docs/knowledge/memory-loss-audit-2026-10-05.md): between 2026-07-05 and
2026-09-27 the zoe-data test suite ran ~477 times on the box and every run wrote audit rows
for the owner's real id (``jason``) into the live palace — 7,078 ``ingest`` rows and 860
``archive`` rows for 17 + 2 rows that never existed there (the test replaced the drawers
collection with an in-memory fake but ``MemoryService._audit_collection`` opened a real
``chromadb.PersistentClient`` at the default ``~/.mempalace``). The audit then claimed
ingests the palace never held. ``tests/conftest.py`` pins the directory (#1773), but a pin is
configuration: ``pytest --noconftest``, a script, or a harness that imports the service
modules bypasses it silently.

This module is the backstop at the one door every writer uses (``memory_service._palace_client``
and the write methods): when the process is NOT the service — a pytest session, or a process
that declared itself a harness with ``ZOE_HARNESS=1`` — opening the live palace, or writing
under a non-synthetic user id to it, raises :class:`LiveStoreViolation` instead of succeeding.
Fail loudly; the caller fixes its configuration.

Stdlib-only (importable by ``memory_service`` without cycles). The service process is never a
"non-service context": it has neither pytest loaded nor ``ZOE_HARNESS`` set, so production
behaviour is unchanged.
"""

from __future__ import annotations

import os
import sys

from user_filters import GUEST_USERS, is_synthetic_user


class LiveStoreViolation(RuntimeError):
    """A test/harness/script tried to open or write the household palace."""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def live_palace_dirs() -> frozenset[str]:
    """Resolved paths that count as the household palace.

    ``~/.mempalace`` (the default every module falls back to) plus anything named by
    ``ZOE_LIVE_PALACE_DIR`` (comma-separated; for a relocated install). Resolved with
    ``realpath`` so a symlink or a ``..`` spelling cannot dodge the check.
    """
    home = os.path.expanduser("~")
    raw = [os.path.join(home, ".mempalace")]
    raw += [p.strip() for p in os.environ.get("ZOE_LIVE_PALACE_DIR", "").split(",") if p.strip()]
    return frozenset(os.path.realpath(os.path.abspath(os.path.expanduser(p))) for p in raw)


def is_live_palace(data_dir: str) -> bool:
    key = os.path.realpath(os.path.abspath(os.path.expanduser(str(data_dir))))
    return key in live_palace_dirs()


def non_service_context() -> str:
    """Why this process is not the zoe-data service ("" when it is the service).

    * ``pytest`` — a pytest session is loaded or running a test;
    * ``harness`` — ``ZOE_HARNESS`` is set (scripts that import service modules declare it,
      e.g. rehearsals and one-shot maintenance drivers that must not touch real users).
    """
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return "pytest"
    if _truthy(os.environ.get("ZOE_HARNESS")):
        return "harness"
    return ""


def assert_palace_open_allowed(data_dir: str) -> None:
    """Opening the live palace from a test session is always a bug (reads too: a test that
    reads it asserts against the household's data, and a test that opens it can write it)."""
    ctx = non_service_context()
    if ctx == "pytest" and is_live_palace(data_dir):
        raise LiveStoreViolation(
            f"refusing to open the live palace ({data_dir}) from a pytest session: tests must run "
            "against a throwaway MEMPALACE_DATA_DIR (services/zoe-data/tests/conftest.py pins one; "
            "do not run with --noconftest or override MEMPALACE_DATA_DIR)"
        )


def assert_write_allowed(data_dir: str, user_id: str, op: str) -> None:
    """A write by ``user_id`` into ``data_dir`` — refused for a non-service process that targets
    the live palace under a real (non-synthetic) id. Harnesses that legitimately exercise the
    live service use ``demo_*`` ids and go through the HTTP API, not this in-process door."""
    ctx = non_service_context()
    if not ctx or not is_live_palace(data_dir):
        return
    if ctx == "harness" and user_id not in GUEST_USERS and is_synthetic_user(user_id):
        return  # a declared harness may use its own throwaway ids (never the guest sentinel)
    raise LiveStoreViolation(
        f"refusing {op} for user_id={user_id!r} against the live palace ({data_dir}) from a "
        f"{ctx} context: only the zoe-data service writes the household's memory. Point "
        "MEMPALACE_DATA_DIR at a scratch directory (or use a demo_* id through the HTTP API)."
    )
