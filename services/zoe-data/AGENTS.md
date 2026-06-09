# services/zoe-data/ — production web + chat API

## Purpose

THE production Zoe API: FastAPI app served by host uvicorn on port 8000. Owns chat, intent routing, memory, MCP tools, auth integration, background jobs, and the OpenClaw/Hermes agent bridges.

## Ownership

- App entry and core: `main.py`, `database.py` (+ `db_pool.py`, `db_compat.py`, `alembic/` migrations), `auth.py`.
- Routing/NLU: `intent_router.py`, `intent_classifier_llm.py`, `routers/` (see child doc).
- Agents and tools: `mcp_server.py`, `openclaw_ws.py`, `hermes_http.py`, `zoe_agent`-related modules, `executors/` (engineering-board executors, currently `kanban_adapter.py`).
- Memory: `hindsight_memory.py`, `hindsight_retain_candidates.py`, `conversation_context.py`.
- Streaming/UI protocol: `ag_ui_stream.py`, `card_service.py`, `card_contract.py`.
- Engineering loop: `greploop_guard.py` lives in `scripts/maintenance/`; `greptile_client.py` and `background_runner.py` live here.

## Local Contracts

- CRITICAL FILES (never delete): `main.py`, `database.py`, `auth.py`, `openclaw_ws.py`, `openclaw_maintenance.py`, `intent_router.py`, `mcp_server.py`, `routers/chat.py`, `routers/system.py`.
- Runs as a systemd USER service: `systemctl --user restart zoe-data.service`; in scripts/CI prefix `XDG_RUNTIME_DIR=/run/user/$(id -u)`.
- Every memory write carries scope (`personal` / `shared` / `ambient`); no unscoped writes.
- Tools register through the allow-list mechanism; every world-changing action goes through a proposal path.
- Schema changes go through Alembic; never DROP/DELETE without WHERE and a backup.
- Harness engineering rules apply (charter section 9): minimize structure, ablate module by module, prefer natural-language harness over brittle Python control flow.

## Work Guidance

- Build the minimal working feature, then run a cleanup pass moving reusable mechanics into service-layer helpers; domain policy stays in routes/actions/intents.
- Use async/await for I/O; no blocking operations on the main thread.

## Verification

- `curl http://localhost:8000/health` and `/api/system/status` after changes plus a restart of the user service.
- Focused pytest in `tests/` (see child doc for runner constraints).

## Child DOX Index

- [routers/AGENTS.md](routers/AGENTS.md) — API routers; single-production-chat-router contract
- [tests/AGENTS.md](tests/AGENTS.md) — service test suite and runner constraints
