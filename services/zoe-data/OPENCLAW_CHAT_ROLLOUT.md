# OpenClaw Terminal Chat Rollout

This rollout enables terminal-style OpenClaw chat with guarded autonomy.

## Feature Flags

- `CHAT_TERMINAL_MODE=true`
  - Enables richer run metadata and terminal-like stream rendering.
- `OPENCLAW_GUARDED_AUTO=true`
  - Enables risk classification and approval gate before risky actions.
- `OPENCLAW_ALL_TOOLS_ENABLED=true`
  - Enables full OpenClaw routing (intent fast-path still available unless force mode enabled per message).
- `WHATSAPP_FLOW_ENABLED=true`
  - Enables natural-language WhatsApp connect orchestration.

## Chat Commands

- `/capabilities` — show current OpenClaw capability inventory.
- `/pending approvals` — list queued approvals.
- `/resume last operation` — preload and re-run latest request.
- `/cancel operation` — mark latest run cancelled.
- `/approve <approval_id>` — approve a gated request.

## Staged Enablement

1. Enable `CHAT_TERMINAL_MODE`.
2. Enable `OPENCLAW_GUARDED_AUTO`.
3. Enable `OPENCLAW_ALL_TOOLS_ENABLED`.
4. Enable `WHATSAPP_FLOW_ENABLED`.

## Smoke Checklist

1. Ask read-only command: `what can you do right now`.
2. Ask risky command: `connect to whatsapp`.
3. Confirm approval prompt appears.
4. Approve using button (or `/approve <id>`).
5. Verify run is visible via `GET /api/chat/runs/{session_id}/latest`.
