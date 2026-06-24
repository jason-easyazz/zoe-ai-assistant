"""Samantha idle-consolidation — LIVE integration: isolation, owner-resolution, idle timing.

This suite runs the REAL idle-consolidation engine against the LIVE Postgres + the
live Gemma extractor (model server on :11434). It is the companion to the mocked
`tests/test_samantha_acceptance_loop.py`: that one proves the wiring with fakes and
runs in CI; THIS one proves the dangerous, security-critical properties end-to-end
on a real box — that one user's facts never leak into another, that the owner is
resolved from per-turn metadata (1b) and not the 'guest' session row, that a pure
guest conversation silently writes NOTHING to anyone's memory, and that the idle
window actually gates selection.

SAFETY (non-negotiable — this writes to the live memory store):
  * DEMO users only — "demo_isoA_<rand>" / "demo_isoB_<rand>". NEVER "jason" / a real
    identity is ever used as an owner or seeded into metadata.
  * Drives consolidation via `consolidate_session(conn, sid, owner, None)` DIRECTLY.
    NEVER calls `run_idle_consolidation_sweep()` — that would scan and touch REAL
    user conversations on the live box.
  * Teardown deletes BOTH demo users' memory (`svc.delete_user(..., actor=...)`) and
    every seeded chat row, then asserts both demo users have 0 facts. The fixture
    tears down even on failure.

GATING: the whole module SKIPS unless `ZOE_LIVE_TESTS=1` AND the model server health
endpoint (http://127.0.0.1:11434/health) answers ok. CI never sets the flag / has no
model server, so CI never runs this and never touches a database.

IDLE TIMING: we set `ZOE_IDLE_CONSOLIDATION_IDLE_S=30` *before importing the engine*
(its idle/lookback constants are read at import time) so the "is it idle yet?" test
is fast (default is 180s). Documented here so a future reader doesn't think 30s is
the production value.

    ZOE_LIVE_TESTS=1 python -m pytest \
        services/zoe-data/tests/samantha_live/test_live_isolation.py -v -s
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# Module-level gate: only run on a live box with the model server up.
# ---------------------------------------------------------------------------
_LIVE = os.environ.get("ZOE_LIVE_TESTS") == "1"


def _model_server_ok() -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:11434/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_LIVE and _model_server_ok()),
    reason="live-only: needs ZOE_LIVE_TESTS=1 and the model server on :11434",
)

# Fast idle window for the timing test — MUST be set before the engine imports its
# IDLE_SECONDS/LOOKBACK constants (read once at module import). 30s, not the prod 180s.
# Guarded by _LIVE: this module is still imported during CI *collection* (to read the
# skip mark), and an unguarded setdefault would leak ZOE_IDLE_CONSOLIDATION_IDLE_S=30
# into the process env, changing the engine's IDLE_SECONDS for any test collected after
# this one. Default to 30 here so the constant is defined even when skipped.
_IDLE_S = 30
if _LIVE:
    os.environ.setdefault("ZOE_IDLE_CONSOLIDATION_IDLE_S", "30")
    _IDLE_S = int(os.environ["ZOE_IDLE_CONSOLIDATION_IDLE_S"])

    # Load the live service env (POSTGRES_URL etc.). The worktree has no .env of its
    # own; this is a live-integration suite, so it reads the live tree's config.
    from dotenv import load_dotenv

    load_dotenv("/home/zoe/assistant/services/zoe-data/.env")


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------
async def _seed_session(db, *, session_id: str, owner: str | None, turns, age_s: int):
    """Seed a GUEST chat_sessions row + its chat_messages.

    chat_sessions.user_id is always 'guest' (mirrors prod: auth is per-turn). Each
    turn's metadata carries {"user_id": owner} when `owner` is a real user, or NULL
    when `owner is None` (pure-guest turn). All turns are backdated to `age_s` seconds
    ago so the engine sees the session as idle by exactly that amount.
    """
    now = datetime.now(timezone.utc)
    created = (now - timedelta(seconds=age_s)).isoformat()
    # chat_sessions.created_at/updated_at also backdated so nothing looks "fresh".
    await db.execute(
        "INSERT INTO chat_sessions (id, user_id, title, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
        (session_id, "guest", "demo-iso", created, created),
    )
    meta = json.dumps({"user_id": owner}) if owner else None
    for role, content in turns:
        await db.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (uuid.uuid4().hex, session_id, role, content, meta, created),
        )
    await db.commit()


async def _set_session_age(db, session_id: str, age_s: int):
    """Re-backdate every turn (and the session) of an already-seeded session."""
    created = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    await db.execute(
        "UPDATE chat_messages SET created_at = ? WHERE session_id = ?",
        (created, session_id),
    )
    await db.execute(
        "UPDATE chat_sessions SET created_at = ?, updated_at = ? WHERE id = ?",
        (created, created, session_id),
    )
    await db.commit()


async def _facts_text(svc, user_id: str) -> list[str]:
    refs = await svc.load_for_prompt(user_id, limit=50)
    return [(r.text or "").lower() for r in refs]


# ---------------------------------------------------------------------------
# Fixture: two demo users + full teardown (facts AND seeded chat rows).
# ---------------------------------------------------------------------------
@pytest.fixture
async def live_env():
    import db_pool

    await db_pool.init_pool()
    from db_pool import get_db_ctx
    from memory_service import get_memory_service

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
        "session_ids": session_ids,  # tests append every session they seed
    }
    try:
        yield env
    finally:
        # 1) wipe both demo users' memory; 2) delete every seeded chat row.
        for u in (user_a, user_b):
            try:
                await svc.delete_user(u, actor="samantha_live_isolation_test")
            except Exception as exc:  # pragma: no cover - teardown best-effort
                print(f"[teardown] delete_user({u}) failed: {exc}")
        try:
            async with get_db_ctx() as db:
                for sid in session_ids:
                    await db.execute("DELETE FROM chat_messages WHERE session_id = ?", (sid,))
                    await db.execute("DELETE FROM chat_sessions WHERE id = ?", (sid,))
                    await db.execute(
                        "DELETE FROM memory_consolidation_state WHERE session_id = ?", (sid,)
                    )
                await db.commit()
        except Exception as exc:  # pragma: no cover
            print(f"[teardown] chat-row cleanup failed: {exc}")
        # 3) verify both demo users are empty again — a non-empty user here means the
        #    test leaked memory it could not clean (loud failure surface).
        for u in (user_a, user_b):
            try:
                leftover = await _facts_text(svc, u)
                assert not leftover, f"teardown left {len(leftover)} facts for {u}: {leftover}"
            except AssertionError:
                raise
            except Exception as exc:  # pragma: no cover
                print(f"[teardown] verify-empty({u}) failed: {exc}")
        # 4) release the asyncpg pool so the suite doesn't leave live connections open.
        try:
            await db_pool.close_pool()
        except Exception as exc:  # pragma: no cover
            print(f"[teardown] close_pool failed: {exc}")


# ---------------------------------------------------------------------------
# Scenario 1 — owner resolution (1b): owner comes from per-turn metadata, not 'guest'.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_owner_resolved_from_per_turn_metadata(live_env):
    svc = live_env["svc"]
    get_db_ctx = live_env["get_db_ctx"]
    user_a = live_env["user_a"]
    from memory_idle_consolidation import consolidate_session, find_idle_sessions

    sid = f"sess_isoA_{uuid.uuid4().hex[:8]}"
    live_env["session_ids"].append(sid)
    turns = [
        ("user", "By the way, my favourite colour is teal."),
        ("assistant", "Teal is a lovely choice."),
        ("user", "Yes, I really do like teal a lot."),
    ]
    async with get_db_ctx() as db:
        # backdate > idle so it is selectable
        await _seed_session(db, session_id=sid, owner=user_a, turns=turns, age_s=_IDLE_S + 60)

    # find_idle_sessions is READ-ONLY — it must resolve owner=user_a (NOT 'guest').
    async with get_db_ctx() as db:
        idle = await find_idle_sessions(db)
    mine = [s for s in idle if s["session_id"] == sid]
    assert mine, f"seeded idle session {sid} not returned by find_idle_sessions"
    assert mine[0]["user_id"] == user_a, (
        f"owner mis-resolved: got {mine[0]['user_id']!r}, expected {user_a!r} "
        "(must come from per-turn metadata, never the guest session row)"
    )

    # Now consolidate and confirm the fact lands under user_a.
    async with get_db_ctx() as db:
        stored = await consolidate_session(db, sid, user_a, None)
    assert stored > 0, "expected at least one durable fact extracted from the exchange"

    facts_a = await _facts_text(svc, user_a)
    assert any("teal" in f for f in facts_a), f"user_a facts missing 'teal': {facts_a}"
    # And NOTHING was written under the literal 'guest' identity.
    guest_facts = await _facts_text(svc, "guest")
    assert not any("teal" in f for f in guest_facts), (
        "CRITICAL: a per-turn-authed fact leaked into the 'guest' bucket"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — cross-user isolation: A's facts never reach B and vice-versa.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cross_user_isolation_no_leak(live_env):
    svc = live_env["svc"]
    get_db_ctx = live_env["get_db_ctx"]
    user_a, user_b = live_env["user_a"], live_env["user_b"]
    from memory_idle_consolidation import consolidate_session

    sid_a = f"sess_isoA2_{uuid.uuid4().hex[:8]}"
    sid_b = f"sess_isoB2_{uuid.uuid4().hex[:8]}"
    live_env["session_ids"] += [sid_a, sid_b]

    async with get_db_ctx() as db:
        await _seed_session(
            db, session_id=sid_a, owner=user_a, age_s=_IDLE_S + 60,
            turns=[
                ("user", "My favourite colour is teal."),
                ("assistant", "Noted — teal."),
                ("user", "Teal, definitely."),
            ],
        )
        await _seed_session(
            db, session_id=sid_b, owner=user_b, age_s=_IDLE_S + 60,
            turns=[
                ("user", "My favourite colour is crimson."),
                ("assistant", "Got it — crimson."),
                ("user", "Crimson, for sure."),
            ],
        )

    async with get_db_ctx() as db:
        await consolidate_session(db, sid_a, user_a, None)
        await consolidate_session(db, sid_b, user_b, None)

    facts_a = await _facts_text(svc, user_a)
    facts_b = await _facts_text(svc, user_b)

    assert any("teal" in f for f in facts_a), f"user_a missing own fact 'teal': {facts_a}"
    assert not any("crimson" in f for f in facts_a), (
        f"CRITICAL LEAK: user_b's 'crimson' fact surfaced for user_a: {facts_a}"
    )
    assert any("crimson" in f for f in facts_b), f"user_b missing own fact 'crimson': {facts_b}"
    assert not any("teal" in f for f in facts_b), (
        f"CRITICAL LEAK: user_a's 'teal' fact surfaced for user_b: {facts_b}"
    )


# ---------------------------------------------------------------------------
# Scenario 3 — pure-guest session writes NOTHING (no resolvable owner).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_guest_only_session_is_skipped(live_env):
    get_db_ctx = live_env["get_db_ctx"]
    from memory_idle_consolidation import consolidate_session, find_idle_sessions

    sid = f"sess_guest_{uuid.uuid4().hex[:8]}"
    live_env["session_ids"].append(sid)
    async with get_db_ctx() as db:
        # owner=None → NO per-turn metadata; chat_sessions.user_id stays 'guest'.
        await _seed_session(
            db, session_id=sid, owner=None, age_s=_IDLE_S + 60,
            turns=[
                ("user", "My favourite colour is chartreuse."),
                ("assistant", "Chartreuse, noted."),
                ("user", "Yes chartreuse."),
            ],
        )

    # It must NOT appear in find_idle_sessions (no real owner to write to)...
    async with get_db_ctx() as db:
        idle = await find_idle_sessions(db)
    assert not [s for s in idle if s["session_id"] == sid], (
        "a pure-guest session (no per-turn user) must never be selected for consolidation"
    )

    # ...and even if forced, consolidating with the guest fallback stores nothing.
    async with get_db_ctx() as db:
        stored = await consolidate_session(db, sid, "guest", None)
    assert stored == 0, "guest conversations must not silently write memory for anyone"


# ---------------------------------------------------------------------------
# Scenario 4 — idle timing: fresh session not selected; backdated one is.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idle_timing_gate(live_env):
    get_db_ctx = live_env["get_db_ctx"]
    user_a = live_env["user_a"]
    from memory_idle_consolidation import find_idle_sessions

    sid = f"sess_timing_{uuid.uuid4().hex[:8]}"
    live_env["session_ids"].append(sid)

    # Seed with the last turn only ~10s old → NOT yet idle (idle window is _IDLE_S=30).
    async with get_db_ctx() as db:
        await _seed_session(
            db, session_id=sid, owner=user_a, age_s=10,
            turns=[
                ("user", "I drive a blue hatchback."),
                ("assistant", "Blue hatchback, noted."),
                ("user", "Yep, blue."),
            ],
        )
    async with get_db_ctx() as db:
        idle = await find_idle_sessions(db)
    assert not [s for s in idle if s["session_id"] == sid], (
        f"session idle for only ~10s must NOT be selected (idle window {_IDLE_S}s)"
    )

    # Backdate it past the idle window → now it should appear.
    async with get_db_ctx() as db:
        await _set_session_age(db, sid, _IDLE_S + 60)
    async with get_db_ctx() as db:
        idle = await find_idle_sessions(db)
    mine = [s for s in idle if s["session_id"] == sid]
    assert mine, f"session backdated > {_IDLE_S}s should now be selected as idle"
    assert mine[0]["user_id"] == user_a
