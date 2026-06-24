"""Samantha idle-consolidation — LIVE integration: isolation, owner-resolution, idle timing.

This suite runs the REAL idle-consolidation engine against the LIVE Postgres + the
live Gemma extractor (model server on :11434). It is the companion to the mocked
`tests/test_samantha_acceptance_loop.py`: that one proves the wiring with fakes and
runs in CI; THIS one proves the dangerous, security-critical properties end-to-end
on a real box — that one user's facts never leak into another, that the owner is
resolved from per-turn metadata (1b) and not the 'guest' session row, that a pure
guest conversation silently writes NOTHING to anyone's memory, and that the idle
window actually gates selection.

Loop/pool discipline, the live gate, env loading, seeding, the consolidate driver
and two-user teardown all live in ``conftest.py`` (one session loop + one asyncpg
pool for the whole suite, so the three live files run green *together* — that is
the fix for the cross-file "Event loop is closed"). This file only declares
scenarios.

SAFETY (non-negotiable — this writes to the live memory store):
  * DEMO users only — "demo_isoA_<rand>" / "demo_isoB_<rand>". NEVER "jason" / a real
    identity is ever used as an owner or seeded into metadata.
  * Drives consolidation via `consolidate(sid, owner)` DIRECTLY. NEVER calls
    `run_idle_consolidation_sweep()` — that would scan and touch REAL user
    conversations on the live box.
  * Teardown (conftest) deletes BOTH demo users' memory and every seeded chat row,
    then asserts both demo users have 0 facts. It runs even on failure.

IDLE TIMING: the engine binds its idle window (`IDLE_SECONDS`) ONCE at its own
import time. The conftest sets `ZOE_IDLE_CONSOLIDATION_IDLE_S=30` before any test
module imports the engine, so the window is small and deterministic. Every
backdating decision still reads the engine's *actual* runtime `IDLE_SECONDS` via
`idle_seconds()`, so the tests are correct regardless of the bound value.

    ZOE_LIVE_TESTS=1 python -m pytest \
        services/zoe-data/tests/samantha_live/test_live_isolation.py -v -s
"""
from __future__ import annotations

import uuid

import pytest

from conftest import (
    consolidate,
    idle_seconds,
    seed_session,
    set_session_age,
    texts,
)

# Run every test in this file on the suite's single session-scoped event loop so
# the shared asyncpg pool (created on that loop by conftest._live_pool) is never
# consumed from a foreign/closed loop. See conftest.py for the full rationale.
pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _facts_text(svc, user_id: str) -> list[str]:
    return texts(await svc.load_for_prompt(user_id, limit=50))


# ---------------------------------------------------------------------------
# Scenario 1 — owner resolution (1b): owner comes from per-turn metadata, not 'guest'.
# ---------------------------------------------------------------------------
async def test_owner_resolved_from_per_turn_metadata(two_user_env):
    svc = two_user_env["svc"]
    get_db_ctx = two_user_env["get_db_ctx"]
    user_a = two_user_env["user_a"]
    from memory_idle_consolidation import find_idle_sessions

    sid = f"sess_isoA_{uuid.uuid4().hex[:8]}"
    two_user_env["session_ids"].append(sid)
    turns = [
        ("user", "By the way, my favourite colour is teal."),
        ("assistant", "Teal is a lovely choice."),
        ("user", "Yes, I really do like teal a lot."),
    ]
    # backdate > the engine's actual idle window so it is selectable
    await seed_session(user_a, turns, session_id=sid, age_s=idle_seconds() + 60,
                       title="demo-iso")

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
    stored = await consolidate(sid, user_a)
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
async def test_cross_user_isolation_no_leak(two_user_env):
    svc = two_user_env["svc"]
    user_a, user_b = two_user_env["user_a"], two_user_env["user_b"]

    sid_a = f"sess_isoA2_{uuid.uuid4().hex[:8]}"
    sid_b = f"sess_isoB2_{uuid.uuid4().hex[:8]}"
    two_user_env["session_ids"] += [sid_a, sid_b]

    await seed_session(
        user_a, session_id=sid_a, age_s=idle_seconds() + 60, title="demo-iso",
        turns=[
            ("user", "My favourite colour is teal."),
            ("assistant", "Noted — teal."),
            ("user", "Teal, definitely."),
        ],
    )
    await seed_session(
        user_b, session_id=sid_b, age_s=idle_seconds() + 60, title="demo-iso",
        turns=[
            ("user", "My favourite colour is crimson."),
            ("assistant", "Got it — crimson."),
            ("user", "Crimson, for sure."),
        ],
    )

    await consolidate(sid_a, user_a)
    await consolidate(sid_b, user_b)

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
async def test_guest_only_session_is_skipped(two_user_env):
    get_db_ctx = two_user_env["get_db_ctx"]
    from memory_idle_consolidation import find_idle_sessions

    sid = f"sess_guest_{uuid.uuid4().hex[:8]}"
    two_user_env["session_ids"].append(sid)
    # owner=None → NO per-turn metadata; chat_sessions.user_id stays 'guest'.
    await seed_session(
        None, session_id=sid, age_s=idle_seconds() + 60, title="demo-iso",
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
    stored = await consolidate(sid, "guest")
    assert stored == 0, "guest conversations must not silently write memory for anyone"


# ---------------------------------------------------------------------------
# Scenario 4 — idle timing: fresh session not selected; backdated one is.
# ---------------------------------------------------------------------------
async def test_idle_timing_gate(two_user_env):
    get_db_ctx = two_user_env["get_db_ctx"]
    user_a = two_user_env["user_a"]
    from memory_idle_consolidation import find_idle_sessions

    sid = f"sess_timing_{uuid.uuid4().hex[:8]}"
    two_user_env["session_ids"].append(sid)
    idle_s = idle_seconds()
    # "fresh" = comfortably inside the idle window (a third of it, capped at 10s) so the
    # not-yet-idle assertion holds whatever the engine's window is.
    fresh_s = max(1, min(10, idle_s // 3))

    # Seed with the last turn only `fresh_s`s old → NOT yet idle.
    await seed_session(
        user_a, session_id=sid, age_s=fresh_s, title="demo-iso",
        turns=[
            ("user", "I drive a blue hatchback."),
            ("assistant", "Blue hatchback, noted."),
            ("user", "Yep, blue."),
        ],
    )
    async with get_db_ctx() as db:
        idle = await find_idle_sessions(db)
    assert not [s for s in idle if s["session_id"] == sid], (
        f"session idle for only ~{fresh_s}s must NOT be selected (idle window {idle_s}s)"
    )

    # Backdate it past the idle window → now it should appear.
    await set_session_age(sid, idle_s + 60)
    async with get_db_ctx() as db:
        idle = await find_idle_sessions(db)
    mine = [s for s in idle if s["session_id"] == sid]
    assert mine, f"session backdated > {idle_s}s should now be selected as idle"
    assert mine[0]["user_id"] == user_a
