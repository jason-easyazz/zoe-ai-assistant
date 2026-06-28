# services/zoe-data/ — production web + chat API

## Purpose

THE production Zoe API: FastAPI app served by host uvicorn on port 8000. Owns chat, intent routing, memory, MCP tools, auth integration, background jobs, and the OpenClaw/Hermes agent bridges.

## Ownership

- App entry and core: `main.py`, `database.py` (+ `db_pool.py`, `db_compat.py`, `alembic/` migrations), `auth.py`.
- Routing/NLU: `intent_router.py`, `intent_classifier_llm.py`, `pi_intent_classifier.py`, `routers/` (see child doc).
- Agents and tools: `mcp_server.py`, `openclaw_ws.py`, `hermes_http.py`, `zoe_agent`-related modules, `executors/` (engineering-board executors, currently `kanban_adapter.py`).
- Agent security helpers: `agent_safety.py` — the single home for the bash-tool shell-injection guard (argv parsing, no shell) and the outbound-fetch SSRF guard (`assert_public_url`, `assert_panel_host`, `guarded_urlopen`). Stdlib-only so it loads in slim CI.
- Memory: `hindsight_memory.py`, `hindsight_retain_candidates.py`, `conversation_context.py`.
- Memory ops: Ingest (`memory_digest.py` dreaming), Query (`memory_service.py` search), and Lint (`memory_lint.py`) — the report-only scan for contradictions/stale/orphan/duplicate rows.
- Streaming/UI protocol: `ag_ui_stream.py`, `card_service.py`, `card_contract.py`.
- Local LLM endpoint: `gemma_endpoint.py` — `gemma_base()` resolves `GEMMA_SERVER_URL` to a `/v1`-free base. Modules that append `/v1/chat/completions` MUST build the URL via this helper (the live unit sets the value WITH `/v1`; appending again 404s). `zoe_agent`/`memory_digest` own their own convention.
- Engineering loop: `greptile_client.py` and `background_runner.py`. The Greploop guard script is NOT here — it lives at `scripts/maintenance/greploop_guard.py` (see `scripts/AGENTS.md`).

## Local Contracts

- CRITICAL FILES (never delete): `main.py`, `database.py`, `auth.py`, `openclaw_ws.py`, `openclaw_maintenance.py`, `intent_router.py`, `mcp_server.py`, `routers/chat.py`, `routers/system.py`.
- Runs as a systemd USER service: `systemctl --user restart zoe-data.service`; in scripts/CI prefix `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- Every memory write carries scope (`personal` / `shared` / `ambient`); no unscoped writes.
- Tools register through the allow-list mechanism; every world-changing action goes through a proposal path.
- The bash tool runs commands via argv (`create_subprocess_exec`), never a shell; outbound web-fetch / browser tools and panel-host proxies must validate targets through `agent_safety` (SSRF guard) — don't reintroduce raw `create_subprocess_shell` or unguarded `urlopen`/`page.goto` on user-supplied URLs/hosts.
- Schema changes go through Alembic; never DROP/DELETE without WHERE and a backup.
- Harness engineering rules apply (charter section 9): minimize structure, ablate module by module, prefer natural-language harness over brittle Python control flow.

## Forbidden

- Memory Lint (`memory_lint.py`) is REPORT-ONLY. It must never delete, merge, edit, archive, supersede, or otherwise mutate stored memory, and must never trigger such mutation. It reads through the `MemoryService` facade and returns flags/reports for human or curation review only.
- Lint must not run inside the nightly dreaming cycle by default. Emitting a lint report from dreaming is opt-in via `ZOE_MEMORY_LINT_IN_DREAMING` and stays report-only even when enabled.
- No DB schema migration, no destructive ops, and no cloud/LLM dependency from the lint detectors (they stay offline-safe heuristics).
- The local merge queue (`run_merge_queue` in `greploop_guard.py`) is DISABLED by default (requires `ZOE_MERGE_QUEUE_ENABLED=1` and no `merge_queue.disabled` kill file). It must NEVER use `--admin` or force-merge, never bypass branch protection, never merge a PR not assessed READY, never act on PRs lacking the `auto-merge` label, never resolve rebase conflicts automatically (abort + report), never touch the live checkout (rebases run in a disposable worktree), and act on at most one PR per cycle.

## Work Guidance

- Pi/Gemma intent governance is an ambiguous-miss fallback only; deterministic intents stay first, cloud providers are rejected when offline-only mode is active, and memory-write intents must not be created from ambiguous Pi classifications.
- Build the minimal working feature, then run a cleanup pass moving reusable mechanics into service-layer helpers; domain policy stays in routes/actions/intents.
- Use async/await for I/O; no blocking operations on the main thread.

## Verification

- `curl http://localhost:8000/health` and `/api/system/status` after changes plus a restart of the user service.
- Focused pytest in `tests/` (see child doc for runner constraints).

## Child DOX Index

- [routers/AGENTS.md](routers/AGENTS.md) — API routers; single-production-chat-router contract
- [tests/AGENTS.md](tests/AGENTS.md) — service test suite and runner constraints
