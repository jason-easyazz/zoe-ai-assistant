# services/zoe-data/tests/ — service test suite

## Purpose

Pytest suite for the production API: router contracts, agent harness behavior, chat persistence, card contracts, and background jobs. `fixtures/` holds shared test data.

## Local Contracts

- **Importing a local service module (`routers.*`, `zoe_agent`, `mcp_server`) does NOT by itself make a test Jetson-only.** What decides it is whether the *import chain* is green under the slim dep list — many routers lazy-import their heavy engines inside functions, so importing them needs only slim deps. Prove it (see the parent [tests/AGENTS.md](../../../tests/AGENTS.md): slim venv + no network + `TZ=UTC`) and mark `ci_safe`; if the chain drags in MemPalace/torch, a live service, or a live DB, leave it unmarked and it still runs on the Jetson.
- Starting the full FastAPI lifespan (`TestClient(app)`) IS still Jetson-only.
- CI-safe voice smoke (`test_voice_smoke_ci.py`) additionally MUST stay model/network/db free — fake STT (`_run_moonshine`) and every TTS provider, never load a model, never hit the network. Don't add `TestClient(app)` or heavy top-level imports to it.
- Rock guards (`test_canonical_invariants.py`): the live-default tests parse the COMMITTED env-resolution default (e.g. `ZOE_CORE_MODEL_ID`, `ZOE_MOONSHINE_ARCH`) from source — no imports/models — and fail if a non-canonical model id/arch ships. Swapping a rock means editing `docs/CANONICAL.md` AND these tests in the same reviewed PR.
- New tests for a router or module belong here, named `test_<area>.py`, matching existing patterns.
- Agent-security guards: `test_agent_safety.py` (shell-injection + SSRF policy) imports only the stdlib-only `agent_safety` module. Its companion `test_agent_safety_wiring.py` (behavioral `_bash` + router/SSRF call-site checks) `importorskip`s `zoe_agent`/`routers.system`; that chain proved slim-green, so it is `ci_safe` too. Both opt in by marker — there is no slim list in `validate.yml` to wire them into.
- Some suites here guard the **frontend**, not this service — the node-harness wrappers below, plus `test_ui_no_external_assets_scan.py`, a stdlib-only scan asserting that no page in the `zoe-ui` docroot loads a subresource from another origin (Zoe is local-first; third-party libs are vendored under `dist/lib/`). Its contract lives in [services/zoe-ui/AGENTS.md](../../zoe-ui/AGENTS.md); a tolerated external load needs an `ALLOWLIST` entry with a reason.
- Node-harness wrappers (`test_*_harness.py`) shell out to `services/zoe-ui/dist/test_*.js`. They may skip when `node` is absent locally, but a wrapper guarding a **security** invariant MUST `pytest.fail` instead of skipping when `CI` is set (`test_auth_no_demo_bypass_harness.py` is the reference) — a silently skipped auth guard reads green.
- Test error conditions and edge cases, not only happy paths.
- `samantha_live/` holds live-integration tests that exercise the real memory pipeline (Gemma extraction + quality gate + Chroma + Postgres). They skip at module level unless `ZOE_LIVE_TESTS=1` AND the local brain answers `/health`, so CI never runs them. Live memory tests MUST use a throwaway demo user (`demo_core_<rand>`), drive consolidation directly via `consolidate_session(...)` on self-seeded sessions (never the real-user `run_idle_consolidation_sweep()`), and delete the demo user + seeded rows in teardown.

## Work Guidance

(empty)

## Verification

Run focused: `pytest services/zoe-data/tests/test_<area>.py` from the repo root (pytest.ini applies).

## Child DOX Index

- [samantha_live/AGENTS.md](samantha_live/AGENTS.md) — LIVE-integration proofs for the Samantha memory engine (real Gemma + real store), gated off CI, demo-users only: core loop (`test_live_core.py`), dedup/durability (`test_live_dedup.py`), and cross-user isolation / owner resolution / idle timing (`test_live_isolation.py`).
