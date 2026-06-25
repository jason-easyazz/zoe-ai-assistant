"""Live-integration tests for the Samantha idle-consolidation memory engine.

Category: core loop + multi-fact + write-quality gate.

These run the REAL pipeline end-to-end against the live box — Gemma fact
extraction, the write-quality gate, Chroma vector store, and Postgres. They are
NOT mocked and are NOT for CI: the suite is skipped (by the shared conftest)
unless ``ZOE_LIVE_TESTS=1`` is set AND the local Gemma brain answers ``/health``.

Loop/pool discipline, the live gate, env loading, seeding, the consolidate
driver, recall helpers and demo-user teardown all live in
``conftest.py`` (one session loop + one asyncpg pool for the whole suite, so the
three live files run green *together*). This file only declares scenarios.

Safety: every test uses a throwaway DEMO user, never a real identity.
Consolidation is driven DIRECTLY via ``consolidate(...)`` on sessions we seed
ourselves — the sweep is never called. Teardown deletes the demo user's facts
and the seeded chat rows, then asserts the demo user has 0 facts left.

Run:
    ZOE_LIVE_TESTS=1 python -m pytest \
        services/zoe-data/tests/samantha_live/test_live_core.py -v -s
"""

from __future__ import annotations

import pytest

from conftest import (
    consolidate,
    joined,
    recall_texts,
    seed_session,
    texts,
)

# Run every test in this file on the suite's single session-scoped event loop so
# the shared asyncpg pool (created on that loop by conftest._live_pool) is never
# consumed from a foreign/closed loop. See conftest.py for the full rationale.
pytestmark = pytest.mark.asyncio(loop_scope="session")

# --------------------------------------------------------------------------- #
# Scenario 1 + 2 + 3: a realistic mixed conversation
#   - states ~4 distinct durable facts (job, city, pet name, allergy)
#   - also contains a question, chit-chat, and a half-finished sentence
# We assert: facts surface in recall (1), junk does NOT (2), search finds a
# fact (3) — all against the same seeded session so the gate is exercised on a
# real mixed transcript.
# --------------------------------------------------------------------------- #


async def test_multifact_extraction_and_junk_gate(demo_user_env):
    demo_user, register, svc = demo_user_env

    turns = [
        ("user", "Hey, what's the weather going to be like tomorrow?"),  # question -> junk
        ("assistant", "I can't see live weather right now, but I can help with other things."),
        ("user", "Fair enough. By the way, I work as a paramedic for the city ambulance service."),  # FACT: job
        ("assistant", "That's a demanding job — thanks for what you do."),
        ("user", "Yeah it keeps me busy. I live in Portland, Oregon."),  # FACT: city
        ("assistant", "Portland's a lovely city. Rainy though!"),
        ("user", "Haha true. Oh and I have a golden retriever named Biscuit."),  # FACT: pet name
        ("assistant", "Biscuit is a great name for a golden!"),
        ("user", "She's the best. Important: I'm severely allergic to penicillin."),  # FACT: allergy
        ("assistant", "Noted — that's good for me to remember."),
        ("user", "Anyway so the other day I was thinking that maybe we could"),  # half-finished -> junk
        ("assistant", "Go on — what were you thinking?"),
        ("user", "lol nvm. anyway how's it going?"),  # chit-chat -> junk
    ]

    sid = await seed_session(demo_user, turns, title="demo-core-live")
    register(sid)

    stored = await consolidate(sid, demo_user)
    print(f"\n[multifact] consolidate stored={stored} for session={sid}")

    recall = await svc.load_for_prompt(demo_user, limit=50)
    recall_t = texts(recall)
    blob = joined(recall)
    print(f"[multifact] recall ({len(recall)} facts):")
    for t in recall_t:
        print(f"    - {t}")

    # Exactly the 4 durable facts — anchoring to the real contract so a
    # partial-extraction regression (e.g. job/city/allergy silently dropped) is
    # caught, not masked by a loose "> 0" guard.
    assert stored == 4, f"expected exactly 4 durable facts, got {stored}: {recall_t}"
    assert recall, "expected durable facts to surface in recall"

    # --- Scenario 1: each of the ~4 durable facts is present and owner-clean.
    # Match on the salient token of each fact rather than exact phrasing
    # (Gemma rewrites into canonical fact form, e.g. "User works as a paramedic").
    assert "paramedic" in blob, f"job fact missing from recall: {recall_t}"
    assert "portland" in blob, f"city fact missing from recall: {recall_t}"
    assert "biscuit" in blob, f"pet-name fact missing from recall: {recall_t}"
    assert "penicillin" in blob, f"allergy fact missing from recall: {recall_t}"

    # Owner-attribution + status: load_for_prompt only returns this demo user's
    # approved facts (guests/other users are filtered server-side), so every
    # ref here belongs to demo_user by construction. Sanity-check none carry a
    # foreign owner in metadata if present.
    for r in recall:
        owner = r.metadata.get("user_id") if isinstance(r.metadata, dict) else None
        if owner is not None:
            assert owner == demo_user, f"fact owned by {owner}, not {demo_user}: {r.text}"

    # --- Scenario 2: junk gate. None of the question / chit-chat / fragment
    # should have become a stored fact.
    assert "weather" not in blob, f"weather question leaked into facts: {recall_t}"
    assert "how's it going" not in blob, f"chit-chat leaked into facts: {recall_t}"
    assert "nvm" not in blob, f"chit-chat leaked into facts: {recall_t}"
    # The half-finished sentence fragment should never appear verbatim.
    assert "maybe we could" not in blob, f"sentence fragment leaked: {recall_t}"

    # --- Scenario 3: semantic recall relevance. A natural query about one fact
    # should surface that fact in the top hits.
    hits = await svc.search("where does the user live", user_id=demo_user, limit=5)
    hit_blob = joined(hits)
    print(f"[multifact] search 'where does the user live' -> {texts(hits)}")
    assert hits, "expected semantic search to return hits"
    assert "portland" in hit_blob, (
        f"city fact not in top search hits: {texts(hits)}"
    )


# --------------------------------------------------------------------------- #
# Scenario 4: pure small-talk -> no facts stored.
# --------------------------------------------------------------------------- #


async def test_empty_smalltalk_stores_nothing(demo_user_env):
    demo_user, register, svc = demo_user_env

    turns = [
        ("user", "Hey there!"),
        ("assistant", "Hi! How can I help?"),
        ("user", "Just saying hello, nothing in particular."),
        ("assistant", "Always nice to hear from you."),
        ("user", "Haha yeah. What time is it, do you know?"),
        ("assistant", "I can check that for you if you'd like."),
        ("user", "Nah it's fine. Talk later!"),
        ("assistant", "Take care!"),
    ]

    sid = await seed_session(demo_user, turns, title="demo-core-live")
    register(sid)

    stored = await consolidate(sid, demo_user)
    print(f"\n[smalltalk] consolidate stored={stored} for session={sid}")

    recall = await recall_texts(demo_user)
    print(f"[smalltalk] recall ({len(recall)} facts): {recall}")

    assert stored == 0, (
        f"pure small-talk should store no facts, got {stored}: {recall}"
    )
    assert recall == [], (
        f"pure small-talk should leave recall empty, got: {recall}"
    )
