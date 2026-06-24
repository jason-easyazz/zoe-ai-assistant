"""Live-integration tests for the Samantha idle-consolidation memory engine.

Category: core loop + multi-fact + write-quality gate.

These run the REAL pipeline end-to-end against the live box — Gemma fact
extraction, the write-quality gate, Chroma vector store, and Postgres. They are
NOT mocked and are NOT for CI: the module is skipped unless
``ZOE_LIVE_TESTS=1`` is set AND the local Ollama brain answers ``/health``.

Safety: every test uses a throwaway DEMO user (``demo_core_<rand>``), never a
real identity. Consolidation is driven DIRECTLY via
``consolidate_session(conn, sid, demo_user, None)`` on sessions we seed
ourselves — ``run_idle_consolidation_sweep()`` (which scans every real user's
sessions) is never called. Teardown deletes the demo user's facts and the
seeded chat rows, then asserts the demo user has 0 facts left.

Run:
    ZOE_LIVE_TESTS=1 python -m pytest \
        services/zoe-data/tests/samantha_live/test_live_core.py -v -s
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import uuid

import pytest

# --------------------------------------------------------------------------- #
# Module-level live guard — skip entirely unless explicitly enabled AND the
# local brain is reachable. This keeps the suite off GitHub-hosted CI.
# --------------------------------------------------------------------------- #


def _brain_healthy() -> bool:
    """True iff the local Ollama brain answers /health quickly."""
    try:
        out = subprocess.run(
            ["curl", "-s", "-m", "5", "http://127.0.0.1:11434/health"],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except Exception:
        return False
    return out.returncode == 0 and "ok" in (out.stdout or "").lower()


_LIVE_ENABLED = os.environ.get("ZOE_LIVE_TESTS") == "1"

if not _LIVE_ENABLED:
    pytest.skip(
        "Samantha live tests require ZOE_LIVE_TESTS=1", allow_module_level=True
    )
if not _brain_healthy():
    pytest.skip(
        "local brain (127.0.0.1:11434) not reachable — skipping live tests",
        allow_module_level=True,
    )

# Load the production env BEFORE importing service modules so db_pool / Chroma
# pick up the real connection settings.
from dotenv import load_dotenv  # noqa: E402

load_dotenv("/home/zoe/assistant/services/zoe-data/.env")

import db_pool  # noqa: E402
from db_pool import get_db_ctx  # noqa: E402
from memory_idle_consolidation import consolidate_session  # noqa: E402
from memory_service import get_memory_service  # noqa: E402


# --------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------- #


async def _seed_session(demo_user: str, turns: list[tuple[str, str]]) -> str:
    """Seed a guest chat session whose per-turn metadata owns it to demo_user.

    Turns are backdated ~200s so the session is past the idle window. Returns
    the new session id. ``turns`` is a list of ``(role, content)`` pairs.
    """
    sid = "sess_" + uuid.uuid4().hex
    base = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=200)
    meta = json.dumps({"user_id": demo_user})
    async with get_db_ctx() as conn:
        await conn.execute(
            "INSERT INTO chat_sessions (id, user_id, title) VALUES ($1, $2, $3) "
            "ON CONFLICT DO NOTHING",
            sid,
            "guest",
            "demo-core-live",
        )
        for i, (role, content) in enumerate(turns):
            # Each turn 1s apart, all still well past the idle window.
            at = (base + datetime.timedelta(seconds=i)).isoformat()
            await conn.execute(
                "INSERT INTO chat_messages (id, session_id, role, content, metadata, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                uuid.uuid4().hex,
                sid,
                role,
                content,
                meta,
                at,
            )
    return sid


async def _delete_seeded_rows(session_ids: list[str]) -> None:
    """Remove the seeded chat rows + sessions and the consolidation watermark."""
    async with get_db_ctx() as conn:
        for sid in session_ids:
            await conn.execute("DELETE FROM chat_messages WHERE session_id = $1", sid)
            await conn.execute(
                "DELETE FROM memory_consolidation_state WHERE session_id = $1", sid
            )
            await conn.execute("DELETE FROM chat_sessions WHERE id = $1", sid)


def _texts(refs) -> list[str]:
    return [(r.text or "").lower() for r in refs]


def _joined(refs) -> str:
    return " || ".join(_texts(refs))


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
async def _pool():
    """Ensure the shared asyncpg pool is initialised (idempotent per test)."""
    await db_pool.init_pool()
    yield


@pytest.fixture
async def demo_env():
    """A fresh demo user + a tracked session list, with guaranteed teardown.

    Yields ``(demo_user, svc, sessions)``. ``sessions`` is a mutable list the
    test appends seeded session ids to so teardown can clean them up.
    """
    demo_user = "demo_core_" + uuid.uuid4().hex[:8]
    svc = get_memory_service()
    sessions: list[str] = []
    try:
        yield demo_user, svc, sessions
    finally:
        # Forget every fact for the demo user, then drop the seeded chat rows.
        try:
            await svc.delete_user(demo_user, actor="demo-core-cleanup")
        finally:
            await _delete_seeded_rows(sessions)
        # Hard assert teardown left no residue for this demo identity.
        leftover = await svc.load_for_prompt(demo_user, limit=50)
        assert leftover == [], (
            f"teardown leak: demo user {demo_user} still has "
            f"{len(leftover)} facts: {_texts(leftover)}"
        )


# --------------------------------------------------------------------------- #
# Scenario 1 + 2 + 3: a realistic mixed conversation
#   - states ~4 distinct durable facts (job, city, pet name, allergy)
#   - also contains a question, chit-chat, and a half-finished sentence
# We assert: facts surface in recall (1), junk does NOT (2), search finds a
# fact (3) — all against the same seeded session so the gate is exercised on a
# real mixed transcript.
# --------------------------------------------------------------------------- #


async def test_multifact_extraction_and_junk_gate(demo_env):
    demo_user, svc, sessions = demo_env

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

    sid = await _seed_session(demo_user, turns)
    sessions.append(sid)

    async with get_db_ctx() as conn:
        stored = await consolidate_session(conn, sid, demo_user, None)

    print(f"\n[multifact] consolidate_session stored={stored} for session={sid}")

    recall = await svc.load_for_prompt(demo_user, limit=50)
    recall_texts = _texts(recall)
    blob = _joined(recall)
    print(f"[multifact] recall ({len(recall)} facts):")
    for t in recall_texts:
        print(f"    - {t}")

    assert stored > 0, "expected the engine to store at least the durable facts"
    assert recall, "expected durable facts to surface in recall"

    # --- Scenario 1: each of the ~4 durable facts is present and owner-clean.
    # Match on the salient token of each fact rather than exact phrasing
    # (Gemma rewrites into canonical fact form, e.g. "User works as a paramedic").
    assert "paramedic" in blob, f"job fact missing from recall: {recall_texts}"
    assert "portland" in blob, f"city fact missing from recall: {recall_texts}"
    assert "biscuit" in blob, f"pet-name fact missing from recall: {recall_texts}"
    assert "penicillin" in blob, f"allergy fact missing from recall: {recall_texts}"

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
    assert "weather" not in blob, f"weather question leaked into facts: {recall_texts}"
    assert "how's it going" not in blob, f"chit-chat leaked into facts: {recall_texts}"
    assert "nvm" not in blob, f"chit-chat leaked into facts: {recall_texts}"
    # The half-finished sentence fragment should never appear verbatim.
    assert "maybe we could" not in blob, f"sentence fragment leaked: {recall_texts}"

    # --- Scenario 3: semantic recall relevance. A natural query about one fact
    # should surface that fact in the top hits.
    hits = await svc.search("where does the user live", user_id=demo_user, limit=5)
    hit_blob = _joined(hits)
    print(f"[multifact] search 'where does the user live' -> {_texts(hits)}")
    assert hits, "expected semantic search to return hits"
    assert "portland" in hit_blob, (
        f"city fact not in top search hits: {_texts(hits)}"
    )


# --------------------------------------------------------------------------- #
# Scenario 4: pure small-talk -> no facts stored.
# --------------------------------------------------------------------------- #


async def test_empty_smalltalk_stores_nothing(demo_env):
    demo_user, svc, sessions = demo_env

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

    sid = await _seed_session(demo_user, turns)
    sessions.append(sid)

    async with get_db_ctx() as conn:
        stored = await consolidate_session(conn, sid, demo_user, None)

    print(f"\n[smalltalk] consolidate_session stored={stored} for session={sid}")

    recall = await svc.load_for_prompt(demo_user, limit=50)
    print(f"[smalltalk] recall ({len(recall)} facts): {_texts(recall)}")

    assert stored == 0, (
        f"pure small-talk should store no facts, got {stored}: {_texts(recall)}"
    )
    assert recall == [], (
        f"pure small-talk should leave recall empty, got: {_texts(recall)}"
    )
