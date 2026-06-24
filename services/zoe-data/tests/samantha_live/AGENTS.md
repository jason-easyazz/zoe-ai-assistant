# services/zoe-data/tests/samantha_live/ — live-integration memory proofs

## Purpose

LIVE end-to-end proofs for the Samantha idle-consolidation memory engine: seed real
`chat_messages` → drive `memory_idle_consolidation.consolidate_session` → real Gemma
E4B extraction + write-quality gate + MemoryService store → assert on real recall.
Unlike the mocked unit tests one level up, these exercise the WHOLE stack. One file
per category:

- `test_live_core.py` — core loop + multi-fact + write-quality gate.
- `test_live_dedup.py` — memory STAYS clean over time (dedup/skip, correction-supersede,
  no near-dup accumulation, conservative on distinct facts).
- `test_live_isolation.py` — security-critical properties: cross-user isolation (no
  leakage), owner resolution from per-turn metadata (increment 1b), guest-only
  sessions writing nothing, idle-timing gate.

## Ownership

Owned by the memory/Samantha workstream. These tests write to the live memory store,
so the safety contract below is load-bearing.

## Local Contracts

- Module-level skip unless `ZOE_LIVE_TESTS=1` AND Gemma at `http://127.0.0.1:11434/health`
  is OK. CI never sets the flag / has no model server, so CI never runs these and never
  touches a database.
- Every test uses isolated DEMO users (`demo_core_<rand>`, `demo_dedup_<rand>`,
  `demo_isoA_<rand>` / `demo_isoB_<rand>`); never a real identity.
- Sessions are seeded as `chat_sessions.user_id = 'guest'` with the real owner only in
  per-turn metadata `{"user_id": demo_user}` — mirrors the real chat path that
  `_resolve_owner` reads.
- Drive the engine via `consolidate_session(conn, sid, demo_user, None)` directly.
- Each fixture tears down on success AND failure: `MemoryService.delete_user` for every
  demo user, delete seeded chat rows (+ `memory_consolidation_state`), assert 0 facts
  remain, and release the asyncpg pool.
- Scenarios documenting a current engine gap are marked `xfail(strict=True)` with a
  root-cause reason; when the engine is fixed they XPASS (fail loudly) → remove the marker.
- The isolation suite forces `ZOE_IDLE_CONSOLIDATION_IDLE_S=30` (vs prod 180s) BEFORE the
  engine imports, guarded by `_LIVE` so the side effect can't leak into CI collection —
  keep that ordering/guard if you add timing tests.

## Forbidden

- NEVER use `jason` or any real user_id; demo users only.
- NEVER call `run_idle_consolidation_sweep()` or `start_idle_consolidation_loop()` —
  they scan/write for ALL idle real users; only `consolidate_session` on seeded demo
  sessions.
- NEVER skip teardown, and never semantic-search-then-archive real users' facts for cleanup.
- Do not enable `ZOE_IDLE_CONSOLIDATION_ENABLED` from these tests.

## Work Guidance

(empty)

## Verification

`ZOE_LIVE_TESTS=1 python -m pytest services/zoe-data/tests/samantha_live/ -v -s` on the
Jetson host with Gemma live; otherwise every test skips.

## Child DOX Index

No child AGENTS.md files.
