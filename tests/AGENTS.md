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
- **`services/zoe-data/tests/` CI selection is marker-based, not enumerated.** GitHub's `validate.yml` runs `pytest services/zoe-data/tests -m ci_safe`; a slim-dep-green test opts into that fast lane by adding a co-located `pytestmark = pytest.mark.ci_safe` (marker registered in root `pytest.ini`). Do NOT hand-list files in the YAML — that list silently dropped new tests (flagged twice in review). Every `services/zoe-data/tests/*.py` also runs UNCONDITIONALLY (marked or not) in the full-directory catch-all step of `self-hosted-tests.yml` on the Jetson, so a new test always runs somewhere with zero enumeration. Do NOT mark a test `ci_safe` if it needs MemPalace / torch / the live host — it fails on the slim runner; leave it unmarked and it still runs on the Jetson.

## Work Guidance

- No loose `test_*.py` files in the `tests/` root — the pre-suite diary scripts were removed; add new tests to the matching subfolder.
- `results/` is gitignored: local run artifacts (model bake-offs, reports) live there untracked and are never committed.

## Verification

`pytest` with `pytest.ini` at the repo root; run focused suites rather than the full tree on constrained hosts.

## Child DOX Index

No child AGENTS.md files yet.
