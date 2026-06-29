# services/zoe-data/ — production web + chat API

## Purpose

THE production Zoe API: FastAPI app served by host uvicorn on port 8000. Owns chat, intent routing, memory, MCP tools, auth integration, background jobs, and the OpenClaw/Hermes agent bridges.

## Ownership

- App entry and core: `main.py`, `database.py` (+ `db_pool.py`, `db_compat.py`, `alembic/` migrations), `auth.py`.
- Routing/NLU: `intent_router.py`, `intent_classifier_llm.py`, `pi_intent_classifier.py`, `routers/` (see child doc).
- Agents and tools: `mcp_server.py`, `openclaw_ws.py`, `hermes_http.py`, `zoe_agent`-related modules, `executors/` (engineering-board executors, currently `kanban_adapter.py`).
- Agent security helpers: `agent_safety.py` — the single home for the bash-tool shell-injection guard (argv parsing, no shell) and the SSRF guards: `assert_public_url` (web fetch), `assert_panel_host` (panels = private-LAN only; loopback/link-local/metadata/public always rejected, allowlist narrows but never bypasses), `guarded_urlopen` (validates + **pins the connection to the validated IP**, defeating DNS-rebinding), and `guard_browser_page` (Playwright `page.route` that validates every request/redirect hop pre-connect). Stdlib-only so it loads in slim CI.
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
- The bash tool runs commands via argv (`create_subprocess_exec`), never a shell. Outbound fetch/browser tools and panel proxies must route through `agent_safety`: HTTP fetches use `guarded_urlopen` (IP-pinned, env proxies ignored), Playwright pages must `await guard_browser_page(page)` before any `goto` (per-hop pre-connect validation), panel hosts go through `assert_panel_host`, and panel-display navigation (e.g. `panel_browser_screenshot`) goes through `assert_panel_url` (LAN-panel-only). Don't reintroduce raw `create_subprocess_shell`, unguarded `urlopen`, or a `page.goto` on a user URL without the route guard. Validating `page.url` only AFTER `goto` is insufficient — the request was already sent.
- Every WebSocket endpoint in `main.py` (`/ws/voice/`, `/ws/push`, and the `/api/*/ws*` channels) must call `_enforce_ws_origin(websocket)` before accept/auth to block cross-site WebSocket hijacking. Allowed origins come from `_allowed_browser_origins()` — the base `ALLOWED_ORIGINS` list plus `ZOE_ALLOWED_WS_ORIGINS` (comma-separated extra panel/kiosk hosts) — which is the single source feeding BOTH this WS guard and the HTTP `CORSMiddleware`, so an operator-added origin can't be allowed for one and rejected by the other. Policy: a present-but-unlisted Origin is rejected (close 1008 / HTTP 403); a **missing** Origin is allowed (non-browser native kiosk/voice client — browsers always send Origin on a WS handshake). Don't add a new WS endpoint without this guard.
- Known SSRF residuals (documented, accepted): (1) `guard_browser_page` validates each request URL in Python but cannot IP-pin Chromium's own resolver, so redirect-to-private is blocked pre-connect but exotic same-host DNS-rebinding (public at guard time, private at connect time) for the browser path is not closed — full pinning is only available for `guarded_urlopen`. (2) `panel_browser_screenshot` constrains the initial `navigate_to` to a LAN panel host via `assert_panel_url`, but the navigation runs in the out-of-process Hermes broker, so an allowed LAN page that redirects to loopback/metadata inside the broker is only closeable broker-side. Closing either needs CDP `Fetch.requestPaused` resolved-IP inspection or a pinning proxy in the browser/broker.
- The stdio MCP server must derive the acting user from transport/session context, not tool arguments; non-admin tools may only target the acting user unless the tool is explicitly admin-authorized for cross-user action.
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
