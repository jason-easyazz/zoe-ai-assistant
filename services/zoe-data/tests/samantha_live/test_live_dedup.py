"""Samantha LIVE-integration durability proof for idle-consolidation memory.

This is the *durability* test (the whole point of #794): after the idle
consolidation engine runs over many real conversations, the user's memory must
stay CLEAN — same-attribute near-duplicates collapse (skip), corrections
supersede the old value, and the approved-fact count stays BOUNDED instead of
piling up linearly with the number of mentions.

Unlike `test_memory_idle_consolidation.py` (which mocks Gemma + the store), this
suite drives the REAL stack end to end:
    seed chat_messages  →  consolidate_session(conn, sid, demo_user, None)
                        →  real Gemma E4B extraction
                        →  real write-quality gate + classify_against_existing
                        →  real MemoryService store (Chroma)
and then asserts on what `load_for_prompt` / `search` / `list_by_status` return.

SAFETY:
  * DEMO user only — `demo_dedup_<rand>`; never "jason" or a real identity.
  * Drives `consolidate_session(...)` directly; NEVER `run_idle_consolidation_sweep()`.
  * Teardown deletes every seeded fact (`svc.delete_user`) and chat row, and
    asserts 0 facts remain.

GATING (CI never runs this): module-skips unless
    ZOE_LIVE_TESTS=1   AND   http://127.0.0.1:11434/health is OK.

Run:
    ZOE_LIVE_TESTS=1 python -m pytest \
        services/zoe-data/tests/samantha_live/test_live_dedup.py -v -s
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest

# ── module-level live gate (CI never runs past here) ───────────────────────────
_LIVE = os.environ.get("ZOE_LIVE_TESTS") == "1"


def _gemma_ok() -> bool:
    if not _LIVE:
        return False
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:11434/health", timeout=5) as r:
            body = r.read().decode("utf-8", "replace")
        return r.status == 200 and "ok" in body.lower()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _gemma_ok(),
    reason="live test: needs ZOE_LIVE_TESTS=1 and a healthy Gemma at :11434",
)

# Load the live env + service modules only when the gate is open, so importing
# this file on a CI runner (no .env / no DB) is a clean skip, not an error.
if _gemma_ok():
    from dotenv import load_dotenv

    load_dotenv("/home/zoe/assistant/services/zoe-data/.env")
    import db_pool  # noqa: E402
    from db_pool import get_db_ctx  # noqa: E402
    from memory_service import get_memory_service  # noqa: E402
    from memory_idle_consolidation import consolidate_session  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────────

def _demo_user() -> str:
    return "demo_dedup_" + secrets.token_hex(4)


async def _ensure_pool() -> None:
    try:
        db_pool.get_pool()
    except Exception:
        await db_pool.init_pool()


async def _seed_session(demo_user: str, turns: list[tuple[str, str]],
                        *, backdate_s: int = 200) -> str:
    """Create a guest chat session whose per-turn metadata carries `demo_user`,
    backdated so the exchange looks idle. Returns the session_id.

    chat_sessions.user_id stays 'guest' (FK to users) exactly like the real
    chat path; the real owner lives ONLY in per-turn metadata {"user_id": ...},
    which is what `_resolve_owner` reads.
    """
    sid = "demo_sess_" + uuid.uuid4().hex[:12]
    base = datetime.now(timezone.utc) - timedelta(seconds=backdate_s)
    meta = json.dumps({"user_id": demo_user})
    async with get_db_ctx() as db:
        await db.execute(
            "INSERT INTO chat_sessions (id, user_id, title) VALUES (?, ?, ?) "
            "ON CONFLICT DO NOTHING",
            (sid, "guest", "demo dedup"),
        )
        for i, (role, content) in enumerate(turns):
            created = (base + timedelta(seconds=i)).isoformat()
            await db.execute(
                "INSERT INTO chat_messages (id, session_id, role, content, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (uuid.uuid4().hex, sid, role, content,
                 meta if role == "user" else None, created),
            )
        await db.commit()
    return sid


async def _consolidate(sid: str, demo_user: str) -> int:
    """Drive the engine directly (never the sweep) with since=None."""
    async with get_db_ctx() as db:
        # `db` is an AsyncpgCompat wrapper; its __getattr__ delegates .fetch()/
        # .execute() straight to the native asyncpg conn, which is exactly what
        # consolidate_session needs for its positional $N queries.
        return await consolidate_session(db, sid, demo_user, None)


async def _approved(demo_user: str) -> list:
    svc = get_memory_service()
    return await svc.list_by_status(user_id=demo_user, status="approved", limit=10000)


async def _texts(demo_user: str) -> list[str]:
    return [(getattr(r, "text", "") or "").lower() for r in await _approved(demo_user)]


async def _recall_text(demo_user: str, limit: int = 30) -> str:
    svc = get_memory_service()
    rows = await svc.load_for_prompt(demo_user, limit=limit)
    return " || ".join((getattr(r, "text", "") or "") for r in rows).lower()


def _count_containing(texts: list[str], *needles: str) -> int:
    """Count facts mentioning ALL needles (case-insensitive)."""
    n = [x.lower() for x in needles]
    return sum(1 for t in texts if all(x in t for x in n))


async def _full_teardown(demo_user: str, session_ids: list[str]) -> int:
    svc = get_memory_service()
    removed = await svc.delete_user(demo_user, actor="demo-dedup-cleanup")
    async with get_db_ctx() as db:
        for sid in session_ids:
            await db.execute("DELETE FROM chat_messages WHERE session_id = ?", (sid,))
            await db.execute("DELETE FROM chat_sessions WHERE id = ?", (sid,))
        await db.commit()
    return removed


# ── fixture: isolated demo user with guaranteed teardown ───────────────────────

_EVENT_LOOP = None


def _loop():
    """One explicit event loop reused across setup/teardown so the asyncpg pool is
    created and consumed on the same loop. `_loop()` is deprecated
    from sync contexts (3.10+) and raises on 3.12+, so don't rely on it."""
    global _EVENT_LOOP
    if _EVENT_LOOP is None or _EVENT_LOOP.is_closed():
        _EVENT_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(_EVENT_LOOP)
    return _EVENT_LOOP


@pytest.fixture
def demo_env():
    """Yields (demo_user, register_session) and tears EVERYTHING down after,
    asserting the demo user has 0 facts left."""
    _loop().run_until_complete(_ensure_pool())
    demo_user = _demo_user()
    sessions: list[str] = []

    def register(sid: str) -> None:
        sessions.append(sid)

    try:
        yield demo_user, register
    finally:
        loop = _loop()
        removed = loop.run_until_complete(_full_teardown(demo_user, sessions))
        left = loop.run_until_complete(_approved(demo_user))
        print(f"\n[teardown] user={demo_user} removed={removed} remaining={len(left)}")
        assert len(left) == 0, f"teardown leaked facts for {demo_user}: {len(left)}"


def _run(coro):
    return _loop().run_until_complete(coro)


# ── Scenario 1: skip duplicate (richer kept, not two rows) ─────────────────────

def test_skip_duplicate_father_name(demo_env):
    demo_user, register = demo_env

    async def go():
        # Session A — the RICH statement (spelled out).
        sa = await _seed_session(demo_user, [
            ("user", "My dad's name is Neil, spelled N-E-I-L."),
            ("assistant", "Got it — Neil, N-E-I-L."),
            ("user", "Yes, that's right."),
        ])
        register(sa)
        await _consolidate(sa, demo_user)

        # Session B (later) — the SPARSE restatement.
        sb = await _seed_session(demo_user, [
            ("user", "Just so you remember, my dad's name is Neil."),
            ("assistant", "Of course, Neil."),
            ("user", "Thanks."),
        ])
        register(sb)
        await _consolidate(sb, demo_user)

        texts = await _texts(demo_user)
        father = _count_containing(texts, "neil")
        print(f"\n[scenario1] father-name facts={father} all={texts}")
        assert father >= 1, "the father's name must be stored at least once"
        assert father == 1, (
            f"expected exactly ONE father-name fact (skip/merge), got {father}: {texts}"
        )

    _run(go())


# ── Scenario 2: correction → supersede (Globex, not Acme) ──────────────────────
#
# KNOWN ENGINE GAP (xfail, strict): under real Gemma the correction does NOT
# supersede the stale employer. Gemma distils "I work at Acme" → "user works at
# acme corporation" and "I work at Globex now, not Acme" → "user works at
# globex". memory_quality._attribute_key returns None for the "works at X"
# framing (it only keys possessive/"X is Y" assertions), so classify_against_
# existing falls back to pure text similarity — which is below _SUPERSEDE_RATIO
# between those two strings — and chooses "add". Result: BOTH employers persist
# as active approved facts. Globex IS recalled, but stale Acme lingers.
# When _attribute_key learns the "works at / employed by" framing this should
# flip to XPASS — remove the marker then.
@pytest.mark.xfail(strict=True, reason="engine gap: 'works at X' correction not "
                                       "superseded under real Gemma (attribute_key=None)")
def test_correction_supersedes_old_employer(demo_env):
    demo_user, register = demo_env

    async def go():
        sc = await _seed_session(demo_user, [
            ("user", "I work at Acme Corporation."),
            ("assistant", "Noted — you work at Acme."),
            ("user", "Correct."),
        ])
        register(sc)
        await _consolidate(sc, demo_user)

        sd = await _seed_session(demo_user, [
            ("user", "Actually, I changed jobs — I work at Globex now, not Acme."),
            ("assistant", "Thanks for the update — Globex it is."),
            ("user", "Right."),
        ])
        register(sd)
        await _consolidate(sd, demo_user)

        recall = await _recall_text(demo_user)
        texts = await _texts(demo_user)
        print(f"\n[scenario2] recall={recall!r} approved={texts}")
        assert "globex" in recall, f"corrected employer (Globex) must be recalled: {recall!r}"
        # Acme must NOT survive as an active approved fact (superseded/archived).
        acme_live = _count_containing(texts, "acme")
        assert acme_live == 0, (
            f"stale employer (Acme) must be superseded, still active: {texts}"
        )

    _run(go())


# ── Scenario 3: volume / no-accumulation-over-time (the big one) ───────────────
#
# KNOWN ENGINE GAP (xfail, strict): the strict "father-name collapses to exactly
# ONE fact" bar FAILS under real Gemma. Gemma emits the SAME fact in different
# distilled framings across sessions — "user's dad's name is neil" (attr=
# 'father name'), "user's father is called neil" (attr='father'), "user has a
# father named neil" (attr=None) — and because _attribute_key produces
# different/None keys for those paraphrases, classify_against_existing can't see
# them as the same attribute and stores each as a NEW row. So near-duplicates DO
# still accumulate across varied phrasings (observed: father=3, globex=4 rows for
# 8 mentions each). This is the headline durability finding. The fix is to make
# _attribute_key / the same-fact detector robust to Gemma's paraphrase space
# (semantic equivalence, not text/regex similarity). Flip to XPASS when fixed.
#
# The test still RUNS the full 8-session volume load and PRINTS the real
# mentions-vs-distinct counts (see -s output) — that report is the deliverable.
@pytest.mark.xfail(strict=True, reason="engine gap: paraphrased same-attribute "
                                       "facts still accumulate under real Gemma")
def test_volume_no_accumulation(demo_env):
    demo_user, register = demo_env

    # Stable, overlapping facts restated with VARIED phrasing across sessions,
    # PLUS one genuinely-new fact per session. A correctly-behaving engine keeps
    # ONE row per distinct fact regardless of how many times it's mentioned.
    dad_phrasings = [
        "My dad's name is Neil.",
        "Just reminding you, my father is called Neil.",
        "Dad — that's Neil — is coming over.",
        "Neil, my dad, says hi.",
        "I mentioned my father Neil before.",
        "My dad Neil loves fishing.",
        "Did I tell you my dad's name is Neil?",
        "My father's name? Neil.",
    ]
    job_phrasings = [
        "I work at Globex.",
        "My job is at Globex Corp.",
        "I'm employed by Globex.",
        "Work's been busy at Globex.",
        "I've been at Globex for years.",
        "Globex is where I work.",
        "My employer is Globex.",
        "At Globex, we shipped a release.",
    ]
    pet_phrasings = [
        "I have a dog named Rex.",
        "Rex, my dog, needs a walk.",
        "My dog is called Rex.",
        "Rex the dog is a good boy.",
        "My dog Rex barked all night.",
        "I took Rex (my dog) to the vet.",
        "Rex is my dog's name.",
        "My dog? His name is Rex.",
    ]
    # Genuinely-new, distinct facts — one per session.
    novel = [
        "I live in Brisbane.",
        "My favourite colour is teal.",
        "I play the cello.",
        "I'm allergic to peanuts.",
        "I drive a blue Subaru.",
        "My birthday is in March.",
        "I'm learning Spanish.",
        "I support the Broncos.",
    ]

    n_sessions = len(dad_phrasings)  # 8 sessions

    async def go():
        for i in range(n_sessions):
            sid = await _seed_session(demo_user, [
                ("user", dad_phrasings[i]),
                ("assistant", "Got it."),
                ("user", job_phrasings[i]),
                ("assistant", "Noted."),
                ("user", pet_phrasings[i]),
                ("assistant", "Nice."),
                ("user", novel[i]),
                ("assistant", "Understood."),
            ])
            register(sid)
            await _consolidate(sid, demo_user)

        texts = await _texts(demo_user)
        total = len(texts)
        dad = _count_containing(texts, "neil")
        job = _count_containing(texts, "globex")
        pet = _count_containing(texts, "rex")

        # mentions vs distinct: 3 stable facts mentioned n_sessions times each
        # (= 24 overlapping mentions) + 8 genuinely-new facts.
        stable_mentions = 3 * n_sessions
        distinct_facts = 3 + len(novel)  # 11 distinct facts ideally

        print(
            f"\n[scenario3 VOLUME] sessions={n_sessions} "
            f"stable_mentions={stable_mentions} novel={len(novel)} "
            f"distinct_ideal={distinct_facts}\n"
            f"  approved_total={total}\n"
            f"  father(neil)={dad}  job(globex)={job}  pet(rex)={pet}\n"
            f"  all_facts={texts}"
        )

        # The core durability assertions.
        assert dad == 1, f"father-name must collapse to ONE fact, got {dad}: {texts}"
        assert job <= 2, f"employer should stay ~1 fact, got {job}: {texts}"
        assert pet <= 2, f"pet should stay ~1 fact, got {pet}: {texts}"
        # Bounded total: must be far below the raw mention count, and within a
        # reasonable band of the distinct-fact ideal (allow Gemma phrasing splits
        # but reject a linear pile-up). 24 stable mentions + 8 novel = 32 raw.
        assert total <= distinct_facts * 2, (
            f"approved facts ({total}) piling up vs distinct ideal "
            f"({distinct_facts}); raw stable mentions were {stable_mentions}: {texts}"
        )
        # Sanity: the novel facts should mostly land (we don't require all 8 since
        # Gemma extraction is probabilistic, but a healthy run keeps several).
        assert total >= 4, f"expected several distinct facts to persist, got {total}: {texts}"

    _run(go())


# ── Scenario 4: conservative — do NOT merge two distinct same-topic facts ──────

def test_conservative_keeps_distinct_siblings(demo_env):
    demo_user, register = demo_env

    async def go():
        sk = await _seed_session(demo_user, [
            ("user", "My sister Karen lives in Perth."),
            ("assistant", "Karen in Perth, got it."),
            ("user", "Yes."),
        ])
        register(sk)
        await _consolidate(sk, demo_user)

        sj = await _seed_session(demo_user, [
            ("user", "My sister Julie lives in Sydney."),
            ("assistant", "Julie in Sydney, noted."),
            ("user", "Correct."),
        ])
        register(sj)
        await _consolidate(sj, demo_user)

        texts = await _texts(demo_user)
        recall = await _recall_text(demo_user)
        karen = _count_containing(texts, "karen")
        julie = _count_containing(texts, "julie")
        print(
            f"\n[scenario4] karen={karen} julie={julie}\n"
            f"  recall={recall!r}\n  approved={texts}"
        )
        # Both distinct sisters must survive — the dedup must NOT collapse two
        # genuinely-different facts about the same topic into one.
        assert karen >= 1, f"Karen/Perth fact lost (wrongly merged?): {texts}"
        assert julie >= 1, f"Julie/Sydney fact lost (wrongly merged?): {texts}"
        assert "karen" in recall and "julie" in recall, (
            f"both sisters must be recallable: {recall!r}"
        )

    _run(go())
