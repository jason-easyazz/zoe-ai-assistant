# tests/ — test suites

## Purpose

Repository-level test suites: unit, integration, performance, e2e, intent-system, voice, and manual test assets.

## Ownership

- `unit/`, `integration/`, `performance/`, `e2e/` — primary suites by level.
- `intent_system/`, `voice/`, `production/`, `manual/` — domain suites and manual procedures.
- Service-local tests live with their service (e.g. `services/zoe-data/tests/`), not here.

## Local Contracts

- New tests go in the matching subfolder here or in the owning service's `tests/` — never `test_*.py` in the repository root.
- Tests that import local service modules (`routers`, `zoe_agent`, `mcp_server`) or start the full FastAPI lifespan (`TestClient(app)`) run ONLY on the self-hosted Jetson runner, never GitHub-hosted runners.
- CI on GitHub runners must not `pip install -r requirements.txt` (Jetson-only packages pull ~3 GB of PyTorch); use a slim explicit install list.

## Work Guidance

- No loose `test_*.py` files in the `tests/` root — the pre-suite diary scripts were removed; add new tests to the matching subfolder.
- `results/` is gitignored: local run artifacts (model bake-offs, reports) live there untracked and are never committed.

## Verification

`pytest` with `pytest.ini` at the repo root; run focused suites rather than the full tree on constrained hosts.

## Child DOX Index

No child AGENTS.md files yet.
