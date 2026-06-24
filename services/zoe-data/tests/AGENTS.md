# services/zoe-data/tests/ — service test suite

## Purpose

Pytest suite for the production API: router contracts, agent harness behavior, chat persistence, card contracts, and background jobs. `fixtures/` holds shared test data.

## Local Contracts

- Tests importing local service modules (`routers`, `zoe_agent`, `mcp_server`) or starting the full FastAPI lifespan (`TestClient(app)`) run ONLY on the self-hosted Jetson runner; they cannot run on GitHub-hosted runners.
- New tests for a router or module belong here, named `test_<area>.py`, matching existing patterns.
- Test error conditions and edge cases, not only happy paths.

## Work Guidance

(empty)

## Verification

Run focused: `pytest services/zoe-data/tests/test_<area>.py` from the repo root (pytest.ini applies).

## Child DOX Index

- [samantha_live/AGENTS.md](samantha_live/AGENTS.md) — LIVE-integration durability proofs for the memory engine (real Gemma + real store); gated off CI, demo-user only.
