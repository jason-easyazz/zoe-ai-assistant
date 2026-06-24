# services/zoe-data/tests/samantha_live/ — live-only memory integration tests

## Purpose

Integration tests that exercise the REAL Samantha idle-consolidation engine against
the LIVE Postgres and the live Gemma extractor (model server on :11434). They prove
the security-critical properties the mocked `tests/test_samantha_acceptance_loop.py`
cannot: cross-user isolation (no leakage), owner resolution from per-turn metadata
(increment 1b), guest-only sessions writing nothing, and the idle-timing gate.

## Ownership

Owned by the memory/Samantha workstream. These tests write to the live memory store,
so the safety contract below is load-bearing.

## Local Contracts

- The whole module SKIPS unless `ZOE_LIVE_TESTS=1` AND the model server health
  endpoint answers ok. CI never sets the flag and has no model server, so CI never
  runs these and never touches a database.
- DEMO users only — `demo_isoA_<rand>` / `demo_isoB_<rand>`, generated per run.
- Drive consolidation via `consolidate_session(conn, sid, owner, None)` directly.
- Every fixture tears down on success AND failure: `svc.delete_user(...)` for both
  demo users, delete all seeded chat rows + `memory_consolidation_state`, then assert
  both demo users have 0 facts.
- `ZOE_IDLE_CONSOLIDATION_IDLE_S` is forced to 30s (vs prod 180s) BEFORE the engine
  imports — keep that ordering if you add tests.

## Forbidden

- NEVER call `run_idle_consolidation_sweep()` or `start_idle_consolidation_loop()` —
  they scan ALL sessions and would consolidate real users' live conversations.
- NEVER use a real identity (e.g. `jason`) as an owner or in seeded turn metadata.
- NEVER semantic-search-then-archive real memory as a cleanup shortcut; delete only
  the demo users and the exact session rows this suite seeded.

## Verification

`ZOE_LIVE_TESTS=1 python -m pytest services/zoe-data/tests/samantha_live/test_live_isolation.py -v -s`
(run on the Jetson with the model server up; otherwise it skips).

## Child DOX Index

No child AGENTS.md files.
