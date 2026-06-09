# services/zoe-data/routers/ — API routers

## Purpose

FastAPI routers for every Zoe API domain: chat, calendar, lists, memories, reminders, notifications, push, voice, weather, skybridge, system, and more.

## Ownership

- `chat.py` — THE ONLY production chat router (intent fast path + OpenClaw/Hermes agent path via `intent_router`, `openclaw_ws`, `ag_ui_stream`). CRITICAL FILE.
- `system.py` — system/status endpoints. CRITICAL FILE.
- One router module per domain (`calendar.py`, `lists.py`, `memories.py`, `journal.py`, `music.py`, `push.py`, `skybridge.py`, ...).

## Local Contracts

- NEVER create `chat_v2.py`, `chat_new.py`, `chat_optimized.py`, or any parallel chat router. Use git branches, not file duplication.
- No hardcoded NLU command detection (if "add" in message and "shopping" in message...) in `chat.py` or any router; natural-language understanding goes through `intent_router.py` patterns, Zoe Agent, Hermes, or OpenClaw.
- Validate user input at the API boundary; parameterized queries only.
- Routers hold domain policy; reusable mechanics (provider calls, parsing, payload transforms) belong in service-layer helpers, not duplicated across routers.

## Work Guidance

Match the existing router style: APIRouter per module, explicit auth dependencies, structured error responses.

## Verification

Focused pytest in `../tests/` for the touched router plus a live `/health` check after restart.

## Child DOX Index

No child AGENTS.md files.
