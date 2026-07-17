# tests/ — test suites

## Purpose

Repository-level test suites: unit, integration, performance, e2e, intent-system, voice, and manual test assets.

## Ownership

- `unit/`, `integration/`, `performance/`, `e2e/` — primary suites by level.
- `intent_system/`, `voice/`, `production/`, `manual/` — domain suites and manual procedures.
- Service-local tests live with their service (e.g. `services/zoe-data/tests/`), not here.

## Local Contracts

- New tests go in the matching subfolder here or in the owning service's `tests/` — never `test_*.py` in the repository root.
- Tests that need Jetson-only packages (whisper/torch/MemPalace), the live services on localhost, or the full FastAPI lifespan (`TestClient(app)`) run ONLY on the self-hosted Jetson runner, never GitHub-hosted runners. Importing a local service module is fine on GitHub IF the import chain is green under the slim dep list — prove it in a slim venv before marking `ci_safe`.
- **Prove `ci_safe` under CI's conditions, not just this box's.** A dev-box pass is not evidence: the Jetson runs the live Zoe stack and sits in Zoe's timezone, GitHub runners have neither. Before marking, re-run the candidate with (a) **all network blocked** (`unshare -rn --map-root-user bash -c 'ip link set lo up; …'`) — a test that quietly hits `localhost:8000`/`:11434` passes here and fails there; and (b) **`TZ=UTC`** — GitHub runners are UTC, so a test comparing `date.today()` against a value resolved via `today_for_zoe_tz()` passes locally and fails on CI. Both traps have been hit for real. A test needing postgres is also a leave-it-unmarked case even though `validate.yml` has a pg service.
- CI on GitHub runners must not `pip install -r requirements.txt` (Jetson-only packages pull ~3 GB of PyTorch); use a slim explicit install list.
- **`services/zoe-data/tests/` AND repo-root `tests/unit/` CI selection is marker-based, not enumerated.** GitHub's `validate.yml` runs `pytest services/zoe-data/tests -m ci_safe` and `pytest tests/unit -m ci_safe`; a slim-dep-green test opts into that fast lane by adding a co-located `pytestmark = pytest.mark.ci_safe` (marker registered in root `pytest.ini`). Do NOT hand-list files in the YAML — that list silently dropped new tests (flagged twice in review; the old two-file `tests/unit` enumeration left the other ~20 files out of CI entirely). Both directories also run UNCONDITIONALLY (marked or not) in the full-directory catch-all steps of `self-hosted-tests.yml` on the Jetson, so a new test always runs somewhere with zero enumeration. Do NOT mark a test `ci_safe` if it needs MemPalace / torch / the live host — it fails on the slim runner; leave it unmarked and it still runs on the Jetson. `services/zoe-auth/tests` is also enumeration-free: `validate.yml` runs the whole directory (no marker needed — small, slim-dep-green suite), so new zoe-auth test files run automatically.
- **No live I/O at import time — anywhere under `tests/`.** Module-level code runs during *collection*, so `pytest --collect-only`, an IDE, or any lane that merely imports the file fires it. Requests at import land real writes in the household production DB, attributed to the shared kiosk account (this was the confirmed source of the operator's recurring calendar-spam — hundreds of junk events, one batch per CI run; the four scripts responsible were removed, see git history). Every network/DB call belongs inside a test function or fixture. A file that POSTs at import is an ad-hoc script, not a test: it does not belong in `tests/` — put throwaway probes in `scripts/` or `results/` (gitignored).
- **Every `tests/unit` file must at least COLLECT under the slim dep list** — `-m` deselection happens after import, so one module-level import of a non-slim package (e.g. `import jwt`) reddens the whole GitHub lane. Use `pytest.importorskip` for non-slim imports in unmarked files.
- **Panel/browser tests are a node+playwright harness behind a pytest wrapper** — `e2e/music/coverflow_motion.mjs` + `test_coverflow_motion.py` is the reference shape. What keeps them honest:
  - The wrapper `skipif`s on node / `playwright-core` / the chromium binary and is never `ci_safe` (the slim GitHub lane has none of them); it runs in the Jetson's full-directory lane.
  - **Serve the docroot resolved RELATIVE to the test file**, never an absolute `/home/zoe/assistant/...` path — the absolute one silently tests whatever is deployed on `main` instead of the branch under test. This has already happened once.
  - **Assert the mock's shape against the live endpoint in the same file**, and capture fixtures from a real response instead of hand-writing them. Mocks that invented `queue_item_id`/`queue_index` are how a carousel PR passed 50 assertions on a feature that was dead on the panel.
  - Prefer **black-box** assertions (synthetic pointer events + computed DOM transforms) over exporting page internals for tests — `touch/home.html` runs inside an IIFE and should stay that way.
  - A motion assertion must **fail on the pre-change code**; verify that before trusting it. Asserting only rest states passes on a slideshow.
  - Frame timing needs `--disable-gpu-vsync --disable-frame-rate-limit`, or headless pins rAF to ~30fps and every result reads 33.3ms regardless of the code.
  - Screenshot artifacts (`e2e/music/*.png`) are gitignored — look at them, don't commit them.

## Work Guidance

- No loose `test_*.py` files in the `tests/` root — the pre-suite diary scripts were removed; add new tests to the matching subfolder.
- `results/` is gitignored: local run artifacts (model bake-offs, reports) live there untracked and are never committed.

## Verification

`pytest` with `pytest.ini` at the repo root; run focused suites rather than the full tree on constrained hosts.

## Child DOX Index

No child AGENTS.md files yet.
