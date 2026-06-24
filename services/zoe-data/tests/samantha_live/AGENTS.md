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

- **`conftest.py` owns the suite-wide loop + pool discipline.** All three files run
  green *together* (not just one at a time) because the conftest gives the whole
  directory ONE event loop + ONE asyncpg pool: every test file carries
  `pytestmark = pytest.mark.asyncio(loop_scope="session")`, and a session-scoped autouse
  fixture (`_live_pool`) `init_pool()`s once on that loop and `close_pool()`s once at
  session end. Tests/fixtures must NEVER create, null (`db_pool._pool = None`), or close
  the pool themselves, nor spin up their own `asyncio.new_event_loop()` — doing so
  reintroduces the `RuntimeError: Event loop is closed` cross-file failure.
- The conftest also owns the gate, env load, engine idle-window wiring, and the shared
  helpers the files use instead of duplicating: `seed_session`, `set_session_age`,
  `consolidate`, `recall_texts`/`recall_blob`/`approved_texts`/`texts`/`joined`/
  `count_containing`, and the `demo_user_env` / `two_user_env` fixtures (each yields a
  `register(sid)` callback and tears down on success AND failure).
- Skip is enforced from the conftest via `pytest_ignore_collect` (a conftest is not a
  test module, so `pytest.skip(allow_module_level=True)` there is an import error, not a
  skip): the whole directory is ignored unless `ZOE_LIVE_TESTS=1` AND Gemma at
  `http://127.0.0.1:11434/health` is OK (and the live `.env` is found). CI never sets the
  flag / has no model server, so CI collects 0 items here and never touches a database.
- Every test uses isolated DEMO users (`demo_<rand>` for single-user scenarios,
  `demo_isoA_<rand>` / `demo_isoB_<rand>` for isolation); never a real identity.
- Sessions are seeded as `chat_sessions.user_id = 'guest'` with the real owner only in
  per-turn metadata `{"user_id": demo_user}` — mirrors the real chat path that
  `_resolve_owner` reads.
- Drive the engine via the shared `consolidate(sid, owner)` helper (→
  `consolidate_session(conn, sid, owner, None)`) directly.
- Teardown (shared `teardown_demo_users`): `MemoryService.delete_user` for every demo
  user, delete seeded chat rows (+ `memory_consolidation_state`), assert 0 facts remain.
  Pool release is the conftest's job, not the per-test fixture's.
- Scenarios documenting a current engine gap are marked `xfail(strict=True)` with a
  root-cause reason; when the engine is fixed they XPASS (fail loudly) → remove the marker.
- The conftest sets `ZOE_IDLE_CONSOLIDATION_IDLE_S=30` (vs prod 180s) at conftest import
  (before any test module imports the engine), so the timing tests run fast and the
  binding is deterministic. Timing tests still read the engine's *actual* runtime
  `IDLE_SECONDS` via `idle_seconds()`, so they stay correct whatever the bound value is.

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
Jetson host with Gemma live — the WHOLE directory must pass as one suite (shared loop +
pool); a green run is `8 passed, 2 xfailed` (the two xfails are the documented engine
gaps). Without `ZOE_LIVE_TESTS=1` (or with no model server) the directory collects 0
items and skips cleanly, so CI never touches a database.

## Child DOX Index

No child AGENTS.md files.
