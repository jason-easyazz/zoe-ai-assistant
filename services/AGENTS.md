# services/ — Zoe runtime services

## Purpose

Production runtime services for the Zoe assistant: the web/chat API, static UI, auth, protocol bridges, and realtime media.

## Ownership

- `zoe-data/` — THE production web + chat API (host uvicorn, port 8000). Single source of truth for chat, intents, memory, MCP tools, and background jobs.
- `zoe-ui/` — static frontend served by nginx (`dist/` is the docroot) plus `nginx.conf`.
- `zoe-auth/` — authentication service.
- `homeassistant-mcp-bridge/` — MCP protocol bridge to Home Assistant. (The n8n bridge was retired in March 2026 along with n8n itself — OpenClaw cron/skills/exec covers automation. The n8n SSO endpoints under `zoe-auth/` stay until that wiring is also removed.)
- `livekit/` — realtime audio/video infrastructure config.
- `zoe-core/` — Zoe's reasoning/orchestration core: the Pi agent (Gemma 4) that binds the other services together and calls abilities. This is the center of Zoe, not a monolith — it orchestrates and delegates; it does not absorb the other services' code. NOTE: a *different*, older `zoe-core` (the retired Docker monolith, replaced by `zoe-data`) is archived at `docs/archive/retired-services/zoe-core/` — that legacy tree must never be revived or extended. This service is the new Pi core, not that archive.

## Local Contracts

- Exactly ONE production chat router: `zoe-data/routers/chat.py`. Never create `chat_v2.py`, `chat_new.py`, or parallel routers.
- No hardcoded NLU if/else in the production chat router; natural-language routing belongs in `intent_router.py`, Zoe Agent, Hermes, or OpenClaw.
- Code must work on BOTH Jetson Orin NX (GPU) and Raspberry Pi 5 (CPU); gate hardware-specific paths on `HARDWARE_PLATFORM`.
- Memory writes always carry scope (`personal` / `shared` / `ambient`); credentials are per user and per scope, never in global env vars.
- No `home` / `family` / `household` concepts in kernel code (router, memory schema, auth, tool signatures) — they belong in skills and scopes.
- `zoe-data` runs as a systemd USER service: `systemctl --user restart zoe-data.service` (never `sudo systemctl`).

## Work Guidance

- Prefer harness improvements (routing, prompts, memory boundaries, tool contracts) before model upgrades; ablate changes module by module.
- Keep domain policy in routes/actions/intents; move reusable mechanics into service-layer helpers.

## Verification

- `curl http://localhost:8000/health` and `/api/system/status` after service changes.
- Focused pytest under `zoe-data/tests/`; full FastAPI-lifespan tests run only on the self-hosted Jetson runner, not GitHub-hosted runners.
- `python3 tools/audit/validate_critical_files.py` before commit.

## Child DOX Index

- [zoe-data/AGENTS.md](zoe-data/AGENTS.md) — production web/chat API internals, critical files, memory scope rules
- [zoe-ui/AGENTS.md](zoe-ui/AGENTS.md) — frontend docroot, SW_VERSION contract, nginx.conf
- [zoe-auth/AGENTS.md](zoe-auth/AGENTS.md) — authentication, OIDC/SSO, touch-panel pairing
- [homeassistant-mcp-bridge/AGENTS.md](homeassistant-mcp-bridge/AGENTS.md) — Home Assistant MCP bridge

`livekit/` is a single config file (`config.yaml`) with no local editing rules; it stays owned by this doc.
