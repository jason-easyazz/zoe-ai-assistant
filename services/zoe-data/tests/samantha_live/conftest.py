"""Shared harness for the Samantha LIVE-integration memory suite.

This conftest owns the ONE piece of discipline that lets the three live files
(`test_live_core.py`, `test_live_dedup.py`, `test_live_isolation.py`) run green
*together* — not just one at a time. The bug it fixes:

  The asyncpg pool (`db_pool._pool`) is process-global and bound to whatever
  event loop created it. When each file managed its own loop differently
  (pytest-asyncio per-test loop here, a hand-rolled persistent loop there) the
  pool ended up created on one loop and consumed on another/closed one →
  ``RuntimeError: Event loop is closed`` as soon as the suite ran as a whole.

The fix is a single loop + single pool for the entire session:

  * Each live test file sets ``pytestmark = pytest.mark.asyncio(loop_scope="session")``
    so every async test runs on ONE pytest-asyncio session-scoped loop (no per-file
    loop juggling).
  * The session-scoped autouse pool fixture initialises ``db_pool`` once on that
    session loop and closes it once at session end. Individual tests/fixtures
    must NOT create, null, or close the pool themselves.

It also provides the helpers the three files used to duplicate (seed a guest
session with per-turn owner metadata, drive ``consolidate_session`` directly,
read recall/approved facts, and a demo-user teardown that wipes facts + seeded
rows and asserts 0 facts remain) so the files stay DRY and share one safety
contract.

SAFETY (mirrors samantha_live/AGENTS.md — load-bearing):
  * Module/suite skips entirely unless ``ZOE_LIVE_TESTS=1`` AND Gemma at
    http://127.0.0.1:11434/health answers ok, so CI never runs these and never
    touches a database.
  * DEMO users only. Drive via ``consolidate_session(...)`` directly; NEVER the
    sweep/loop. Teardown deletes the demo user + seeded rows; never
    semantic-search-then-archive. The consolidation feature flag is never set.
"""

from __future__ import annotations

import json
import os
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

# --------------------------------------------------------------------------- #
# Live gate — skip the whole directory unless explicitly enabled AND the local
# Gemma brain is reachable. Kept here so all three files share one gate and CI
# (no flag / no model server) collects them as a clean skip.
# --------------------------------------------------------------------------- #

_LIVE_ENABLED = os.environ.get("ZOE_LIVE_TESTS") == "1"


def _brain_healthy() -> bool:
    """True iff the local Gemma brain answers /health ok, quickly."""
    if not _LIVE_ENABLED:
        return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/health", timeout=5) as r:
            body = r.read().decode("utf-8", "replace")
        return r.status == 200 and "ok" in body.lower()
    except Exception:
        return False


_BRAIN_OK = _brain_healthy()  # one blocking /health probe at import; reused below
_LIVE = _LIVE_ENABLED and _BRAIN_OK

# How we keep CI from running (or even collecting) these. A conftest is NOT a
# test module, so `pytest.skip(allow_module_level=True)` here is a hard import
# error, not a clean skip. Instead, when the suite is not live we (a) skip the
# heavy live imports below and (b) tell pytest to ignore every file in this
# directory via `pytest_ignore_collect`, so the test modules are never imported
# and CI sees a clean "no tests ran here".
_SKIP_REASON: str | None = None
if not _LIVE_ENABLED:
    _SKIP_REASON = "Samantha live tests require ZOE_LIVE_TESTS=1"
elif not _BRAIN_OK:
    _SKIP_REASON = "local Gemma brain (127.0.0.1:11434) not reachable"


def pytest_ignore_collect(collection_path, config):
    """Ignore this whole directory's test files unless the suite is live."""
    if _SKIP_REASON is not None and Path(str(collection_path)).is_file():
        return True
    return None


def pytest_report_collectionfinish(config):
    if _SKIP_REASON is not None:
        return f"samantha_live: skipped (live-only) — {_SKIP_REASON}"
    return None


# --------------------------------------------------------------------------- #
# Env + engine import-time wiring — ONLY on the live path. On CI (_LIVE False)
# we skip this entirely: the test modules are ignored (pytest_ignore_collect),
# the fixtures/helpers below are never invoked, and these names stay unbound,
# which is fine because nothing calls them. The conftest itself must import
# cleanly with no .env and no DB.
#
# pytest loads conftest BEFORE collecting any test module, so doing this here
# (when live) means the engine binds its idle window from our value regardless
# of which test file imports it first.
# --------------------------------------------------------------------------- #

# Names that the live helpers/fixtures below resolve at call time. Declared so
# the module always has them; populated only on the live path.
db_pool = None  # type: ignore[assignment]
get_db_ctx = None  # type: ignore[assignment]
consolidate_session = None  # type: ignore[assignment]
get_memory_service = None  # type: ignore[assignment]

if _LIVE:
    # Shrink the idle window so the isolation timing tests run fast. The engine
    # binds IDLE_SECONDS ONCE at its own import time; setting it here (before any
    # test module imports the engine) makes that binding deterministic. Tests
    # still read the engine's *actual* runtime value, so this is purely speed.
    os.environ.setdefault("ZOE_IDLE_CONSOLIDATION_IDLE_S", "30")

    # Load the production env BEFORE importing service modules so db_pool / Chroma
    # pick up the real connection settings. Prefer the .env relative to this file
    # (tests/samantha_live/ -> zoe-data/.env); when running from a git worktree
    # the .env may live only in the canonical checkout, so fall back to that.
    from dotenv import load_dotenv

    _ENV_CANDIDATES = [
        Path(__file__).resolve().parents[2] / ".env",
        Path("/home/zoe/assistant/services/zoe-data/.env"),
    ]
    _ENV_PATH = next((p for p in _ENV_CANDIDATES if p.is_file()), None)
    if _ENV_PATH is None:
        # No live config → demote to a clean skip rather than importing against
        # ambient env vars and giving misleading results.
        _SKIP_REASON = (
            "zoe-data .env not found in "
            f"{[str(p) for p in _ENV_CANDIDATES]}"
        )
        _LIVE = False
    else:
        load_dotenv(_ENV_PATH)
        import db_pool  # noqa: F811
        from db_pool import get_db_ctx  # noqa: F811
        from memory_idle_consolidation import consolidate_session  # noqa: F811
        from memory_service import get_memory_service  # noqa: F811


def idle_seconds() -> int:
    """The engine's effective idle window at runtime (whatever it bound at import)."""
    import memory_idle_consolidation as mic

    return int(mic.IDLE_SECONDS)


# --------------------------------------------------------------------------- #
# One loop for the whole suite. Each test file carries
# ``pytestmark = pytest.mark.asyncio(loop_scope="session")`` so pytest-asyncio
# runs every test on a single session-scoped loop; the session-scoped pool
# fixture below then creates + consumes + closes the asyncpg pool on that one
# loop. No file manages its own loop anymore.
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture(scope="session", autouse=True, loop_scope="session")
async def _live_pool():
    """Initialise the asyncpg pool ONCE on the session loop and close it once.

    This is the whole fix: the pool is created and consumed on the same loop for
    the entire suite, so cross-file runs never hit "Event loop is closed". Tests
    and per-test fixtures must not touch db_pool._pool directly.
    """
    await db_pool.init_pool()
    try:
        yield db_pool.get_pool()
    finally:
        await db_pool.close_pool()


# --------------------------------------------------------------------------- #
# Shared seeding / driving / recall helpers (previously duplicated per file).
# All use get_db_ctx() (AsyncpgCompat), which supports both the ?-placeholder
# aiosqlite-style API and native asyncpg $N via __getattr__.
# --------------------------------------------------------------------------- #


async def seed_session(
    demo_user: str | None,
    turns: list[tuple[str, str]],
    *,
    session_id: str | None = None,
    age_s: int = 200,
    title: str = "samantha-live",
) -> str:
    """Seed a GUEST chat session whose per-turn metadata owns it to ``demo_user``.

    Mirrors the real chat path: ``chat_sessions.user_id`` stays ``'guest'`` (FK to
    users) and the real owner lives ONLY in per-turn ``metadata {"user_id": ...}``,
    which is what ``_resolve_owner`` reads. When ``demo_user`` is None the turns
    carry no owner metadata (a pure-guest session). All turns (and the session
    row) are backdated ``age_s`` seconds so the engine sees the session as idle by
    exactly that amount. Returns the session id.
    """
    sid = session_id or ("demo_sess_" + uuid.uuid4().hex[:12])
    created = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    meta = json.dumps({"user_id": demo_user}) if demo_user else None
    async with get_db_ctx() as db:
        await db.execute(
            "INSERT INTO chat_sessions (id, user_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            (sid, "guest", title, created, created),
        )
        for role, content in turns:
            # Mirror the real chat path: per-turn owner metadata is stamped on USER
            # turns only; assistant turns carry NULL metadata (what _resolve_owner sees).
            turn_meta = meta if role == "user" else None
            await db.execute(
                "INSERT INTO chat_messages (id, session_id, role, content, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (uuid.uuid4().hex, sid, role, content, turn_meta, created),
            )
        await db.commit()
    return sid


async def set_session_age(session_id: str, age_s: int) -> None:
    """Re-backdate every turn (and the session row) of an already-seeded session."""
    created = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    async with get_db_ctx() as db:
        await db.execute(
            "UPDATE chat_messages SET created_at = ? WHERE session_id = ?",
            (created, session_id),
        )
        await db.execute(
            "UPDATE chat_sessions SET created_at = ?, updated_at = ? WHERE id = ?",
            (created, created, session_id),
        )
        await db.commit()


async def consolidate(session_id: str, owner: str) -> int:
    """Drive the engine directly (never the sweep) with since=None.

    ``db`` is an AsyncpgCompat wrapper; its __getattr__ delegates .fetch()/
    .execute() straight to the native asyncpg conn, which is what
    consolidate_session needs for its positional $N queries.
    """
    async with get_db_ctx() as db:
        return await consolidate_session(db, session_id, owner, None)


async def delete_seeded_rows(session_ids: list[str]) -> None:
    """Remove seeded chat rows + sessions + the consolidation watermark."""
    async with get_db_ctx() as db:
        for sid in session_ids:
            await db.execute("DELETE FROM chat_messages WHERE session_id = ?", (sid,))
            await db.execute(
                "DELETE FROM memory_consolidation_state WHERE session_id = ?", (sid,)
            )
            await db.execute("DELETE FROM chat_sessions WHERE id = ?", (sid,))
        await db.commit()


def texts(refs) -> list[str]:
    """Lowercased text of each MemoryRef (handles missing .text)."""
    return [(getattr(r, "text", "") or "").lower() for r in refs]


def joined(refs) -> str:
    return " || ".join(texts(refs))


def count_containing(items: list[str], *needles: str) -> int:
    """Count strings mentioning ALL needles (case-insensitive)."""
    n = [x.lower() for x in needles]
    return sum(1 for t in items if all(x in t for x in n))


async def recall_texts(demo_user: str, limit: int = 50) -> list[str]:
    """load_for_prompt facts as lowercased strings."""
    svc = get_memory_service()
    return texts(await svc.load_for_prompt(demo_user, limit=limit))


async def recall_blob(demo_user: str, limit: int = 50) -> str:
    return " || ".join(await recall_texts(demo_user, limit=limit))


async def approved_texts(demo_user: str) -> list[str]:
    """Approved facts (list_by_status) as lowercased strings."""
    svc = get_memory_service()
    rows = await svc.list_by_status(user_id=demo_user, status="approved", limit=10000)
    return texts(rows)


async def teardown_demo_users(users: list[str], session_ids: list[str]) -> None:
    """Wipe every demo user's facts, delete seeded chat rows, assert 0 facts left.

    NEVER semantic-search-then-archive — uses MemoryService.delete_user (full,
    targeted by demo user_id) plus exact seeded-row deletes by session id.
    """
    svc = get_memory_service()
    for u in users:
        try:
            await svc.delete_user(u, actor="samantha_live_cleanup")
        except Exception as exc:  # pragma: no cover - teardown best-effort
            print(f"[teardown] delete_user({u}) failed: {exc}")
    try:
        await delete_seeded_rows(session_ids)
    except Exception as exc:  # pragma: no cover
        print(f"[teardown] seeded-row cleanup failed: {exc}")
    for u in users:
        leftover = await svc.load_for_prompt(u, limit=50)
        assert leftover == [], (
            f"teardown leak: demo user {u} still has {len(leftover)} facts: "
            f"{texts(leftover)}"
        )


# --------------------------------------------------------------------------- #
# Fixtures: isolated demo identities with guaranteed teardown (success OR fail).
# Each yields the demo identity/identities plus a `register(sid)` callback the
# test uses to track every session it seeds so teardown can clean it up.
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture(loop_scope="session")
async def demo_user_env():
    """One demo user + a session registrar. Yields (demo_user, register)."""
    demo_user = "demo_" + uuid.uuid4().hex[:10]
    svc = get_memory_service()
    sessions: list[str] = []

    def register(sid: str) -> None:
        sessions.append(sid)

    try:
        yield demo_user, register, svc
    finally:
        await teardown_demo_users([demo_user], sessions)


@pytest_asyncio.fixture(loop_scope="session")
async def two_user_env():
    """Two isolated demo users + a session registrar (cross-user isolation tests).

    Yields a dict with svc, get_db_ctx, user_a, user_b, session_ids list.
    """
    svc = get_memory_service()
    rand = uuid.uuid4().hex[:8]
    user_a = f"demo_isoA_{rand}"
    user_b = f"demo_isoB_{rand}"
    session_ids: list[str] = []
    env = {
        "svc": svc,
        "get_db_ctx": get_db_ctx,
        "user_a": user_a,
        "user_b": user_b,
        "session_ids": session_ids,
    }
    try:
        yield env
    finally:
        await teardown_demo_users([user_a, user_b], session_ids)
