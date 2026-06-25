"""Samantha LIVE-integration durability proof for idle-consolidation memory.

This is the *durability* test (the whole point of #794): after the idle
consolidation engine runs over many real conversations, the user's memory must
stay CLEAN — same-attribute near-duplicates collapse (skip), corrections
supersede the old value, and the approved-fact count stays BOUNDED instead of
piling up linearly with the number of mentions.

Unlike `test_memory_idle_consolidation.py` (which mocks Gemma + the store), this
suite drives the REAL stack end to end:
    seed chat_messages  →  consolidate(sid, demo_user)
                        →  real Gemma E4B extraction
                        →  real write-quality gate + classify_against_existing
                        →  real MemoryService store (Chroma)
and then asserts on what `load_for_prompt` / `list_by_status` return.

Loop/pool discipline, the live gate, env loading, seeding, the consolidate
driver, and demo-user teardown all live in ``conftest.py`` (one session loop +
one asyncpg pool for the whole suite, so the three live files run green
*together*). This file only declares scenarios.

SAFETY:
  * DEMO user only — never "jason" or a real identity.
  * Drives `consolidate(...)` directly; NEVER `run_idle_consolidation_sweep()`.
  * Teardown deletes every seeded fact (`svc.delete_user`) and chat row, and
    asserts 0 facts remain.

GATING (CI never runs this): the conftest skips the whole directory unless
    ZOE_LIVE_TESTS=1   AND   http://127.0.0.1:11434/health is OK.

Run:
    ZOE_LIVE_TESTS=1 python -m pytest \
        services/zoe-data/tests/samantha_live/test_live_dedup.py -v -s
"""
from __future__ import annotations

import pytest

from conftest import (
    approved_texts,
    consolidate,
    count_containing,
    recall_blob,
    seed_session,
)

# Run every test in this file on the suite's single session-scoped event loop so
# the shared asyncpg pool (created on that loop by conftest._live_pool) is never
# consumed from a foreign/closed loop. See conftest.py for the full rationale.
pytestmark = pytest.mark.asyncio(loop_scope="session")


# ── Scenario 1: skip duplicate (richer kept, not two rows) ─────────────────────

async def test_skip_duplicate_father_name(demo_user_env):
    demo_user, register, _svc = demo_user_env

    # Session A — the RICH statement (spelled out).
    sa = await seed_session(demo_user, [
        ("user", "My dad's name is Neil, spelled N-E-I-L."),
        ("assistant", "Got it — Neil, N-E-I-L."),
        ("user", "Yes, that's right."),
    ], title="demo dedup")
    register(sa)
    await consolidate(sa, demo_user)

    # Session B (later) — the SPARSE restatement.
    sb = await seed_session(demo_user, [
        ("user", "Just so you remember, my dad's name is Neil."),
        ("assistant", "Of course, Neil."),
        ("user", "Thanks."),
    ], title="demo dedup")
    register(sb)
    await consolidate(sb, demo_user)

    texts = await approved_texts(demo_user)
    father = count_containing(texts, "neil")
    print(f"\n[scenario1] father-name facts={father} all={texts}")
    assert father >= 1, "the father's name must be stored at least once"
    assert father == 1, (
        f"expected exactly ONE father-name fact (skip/merge), got {father}: {texts}"
    )


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
async def test_correction_supersedes_old_employer(demo_user_env):
    demo_user, register, _svc = demo_user_env

    sc = await seed_session(demo_user, [
        ("user", "I work at Acme Corporation."),
        ("assistant", "Noted — you work at Acme."),
        ("user", "Correct."),
    ], title="demo dedup")
    register(sc)
    await consolidate(sc, demo_user)

    sd = await seed_session(demo_user, [
        ("user", "Actually, I changed jobs — I work at Globex now, not Acme."),
        ("assistant", "Thanks for the update — Globex it is."),
        ("user", "Right."),
    ], title="demo dedup")
    register(sd)
    await consolidate(sd, demo_user)

    recall = await recall_blob(demo_user)
    texts = await approved_texts(demo_user)
    print(f"\n[scenario2] recall={recall!r} approved={texts}")
    assert "globex" in recall, f"corrected employer (Globex) must be recalled: {recall!r}"
    # Acme must NOT survive as an active approved fact (superseded/archived).
    acme_live = count_containing(texts, "acme")
    assert acme_live == 0, (
        f"stale employer (Acme) must be superseded, still active: {texts}"
    )


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
async def test_volume_no_accumulation(demo_user_env):
    demo_user, register, _svc = demo_user_env

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

    for i in range(n_sessions):
        sid = await seed_session(demo_user, [
            ("user", dad_phrasings[i]),
            ("assistant", "Got it."),
            ("user", job_phrasings[i]),
            ("assistant", "Noted."),
            ("user", pet_phrasings[i]),
            ("assistant", "Nice."),
            ("user", novel[i]),
            ("assistant", "Understood."),
        ], title="demo dedup")
        register(sid)
        await consolidate(sid, demo_user)

    texts = await approved_texts(demo_user)
    total = len(texts)
    dad = count_containing(texts, "neil")
    job = count_containing(texts, "globex")
    pet = count_containing(texts, "rex")

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


# ── Scenario 4: conservative — do NOT merge two distinct same-topic facts ──────

async def test_conservative_keeps_distinct_siblings(demo_user_env):
    demo_user, register, _svc = demo_user_env

    sk = await seed_session(demo_user, [
        ("user", "My sister Karen lives in Perth."),
        ("assistant", "Karen in Perth, got it."),
        ("user", "Yes."),
    ], title="demo dedup")
    register(sk)
    await consolidate(sk, demo_user)

    sj = await seed_session(demo_user, [
        ("user", "My sister Julie lives in Sydney."),
        ("assistant", "Julie in Sydney, noted."),
        ("user", "Correct."),
    ], title="demo dedup")
    register(sj)
    await consolidate(sj, demo_user)

    texts = await approved_texts(demo_user)
    recall = await recall_blob(demo_user)
    karen = count_containing(texts, "karen")
    julie = count_containing(texts, "julie")
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
