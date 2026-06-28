# services/zoe-data/tests/ — service test suite

## Purpose

Pytest suite for the production API: router contracts, agent harness behavior, chat persistence, card contracts, and background jobs. `fixtures/` holds shared test data.

## Local Contracts

- Tests importing local service modules (`routers`, `zoe_agent`, `mcp_server`) or starting the full FastAPI lifespan (`TestClient(app)`) run ONLY on the self-hosted Jetson runner; they cannot run on GitHub-hosted runners.
- EXCEPTION — CI-safe voice smoke (`test_voice_smoke_ci.py`): may import `routers.voice_tts` on GitHub-hosted runners *because* that router lazy-imports every heavy engine (moonshine_voice, kokoro_onnx, edge_tts) inside functions, so module import needs only slim deps. It MUST stay model/network/db free — fake STT (`_run_moonshine`) and every TTS provider, never load a model, never hit the network — and is wired into the slim list in `.github/workflows/validate.yml`. Don't add `TestClient(app)` or heavy top-level imports to it.
- Rock guards (`test_canonical_invariants.py`): the live-default tests parse the COMMITTED env-resolution default (e.g. `ZOE_CORE_MODEL_ID`, `ZOE_MOONSHINE_ARCH`) from source — no imports/models — and fail if a non-canonical model id/arch ships. Swapping a rock means editing `docs/CANONICAL.md` AND these tests in the same reviewed PR.
- New tests for a router or module belong here, named `test_<area>.py`, matching existing patterns.
- Test error conditions and edge cases, not only happy paths.
- `samantha_live/` holds live-integration tests that exercise the real memory pipeline (Gemma extraction + quality gate + Chroma + Postgres). They skip at module level unless `ZOE_LIVE_TESTS=1` AND the local brain answers `/health`, so CI never runs them. Live memory tests MUST use a throwaway demo user (`demo_core_<rand>`), drive consolidation directly via `consolidate_session(...)` on self-seeded sessions (never the real-user `run_idle_consolidation_sweep()`), and delete the demo user + seeded rows in teardown.

## Work Guidance

(empty)

## Verification

Run focused: `pytest services/zoe-data/tests/test_<area>.py` from the repo root (pytest.ini applies).

## Child DOX Index

- [samantha_live/AGENTS.md](samantha_live/AGENTS.md) — LIVE-integration proofs for the Samantha memory engine (real Gemma + real store), gated off CI, demo-users only: core loop (`test_live_core.py`), dedup/durability (`test_live_dedup.py`), and cross-user isolation / owner resolution / idle timing (`test_live_isolation.py`).
