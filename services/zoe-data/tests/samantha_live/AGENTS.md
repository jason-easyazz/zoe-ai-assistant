# services/zoe-data/tests/samantha_live/ — live-integration memory proofs

## Purpose

LIVE end-to-end durability proofs for the Samantha idle-consolidation memory:
seed real `chat_messages` → drive `memory_idle_consolidation.consolidate_session`
→ real Gemma E4B extraction + write-quality gate + MemoryService store → assert
on real recall. Unlike the mocked unit tests one level up, these exercise the
WHOLE stack to prove memory STAYS clean over time (dedup/skip, correction-
supersede, no near-dup accumulation, conservative on distinct facts).

## Local Contracts

- Module-level skip unless `ZOE_LIVE_TESTS=1` AND Gemma at `http://127.0.0.1:11434/health` is OK. CI never runs these.
- Every test uses an isolated DEMO user (`demo_dedup_<rand>`) and the `demo_env` fixture, which on teardown calls `MemoryService.delete_user` + deletes seeded chat rows and asserts 0 facts remain.
- Sessions are seeded as `chat_sessions.user_id = 'guest'` (FK to users) with the real owner only in per-turn metadata `{"user_id": demo_user}` — mirrors the real chat path that `_resolve_owner` reads.
- Drive the engine via `consolidate_session(conn, sid, demo_user, None)` directly.
- Scenarios documenting a current engine gap are marked `xfail(strict=True)` with a root-cause reason; when the engine is fixed they XPASS (fail loudly) → remove the marker.

## Forbidden

- NEVER use `jason` or any real user_id; demo users only.
- NEVER call `run_idle_consolidation_sweep()` (it scans/writes for ALL idle real users) — only `consolidate_session` on the seeded demo session.
- NEVER skip teardown, and never semantic-search-then-archive real users' facts for cleanup.
- Do not enable `ZOE_IDLE_CONSOLIDATION_ENABLED` from these tests.

## Work Guidance

(empty)

## Verification

`ZOE_LIVE_TESTS=1 python -m pytest services/zoe-data/tests/samantha_live/test_live_dedup.py -v -s` on the Jetson host with Gemma live.

## Child DOX Index

No child AGENTS.md files.
