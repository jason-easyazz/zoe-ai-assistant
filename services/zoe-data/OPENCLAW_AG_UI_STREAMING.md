# OpenClaw agent path vs AG-UI tool streaming

## Current behavior

- **Intent fast path** (`zoe-data/routers/chat.py`): emits full AG-UI events (`TOOL_CALL_*`, `STEP_*`, etc.) for matched intents.
- **OpenClaw CLI agent** (`openclaw_cli` → `_stream_openclaw_assistant_ag`): waits for the **final** JSON response from `openclaw agent`, then streams **assistant text** and optional `zoe.ui_*` actions parsed from that response. **Live per-tool events from inside the OpenClaw run are not forwarded** to the Zoe web chat SSE stream today.

## Why

The integration uses a **blocking CLI invocation** with aggregated output, not a streaming subscription to the gateway’s tool/event channel (as the OpenClaw Control UI at `http://127.0.0.1:18789/` does).

## Path to parity with Control UI

To show tool-by-tool AG-UI in Zoe chat for OpenClaw turns, add a **streaming bridge** from the OpenClaw gateway (or an agent mode that emits structured events) into the same AG-UI event encoder used by `chat_stream_generator`. Until then, the web UI correctly renders any events the backend sends, but OpenClaw-heavy turns will look like **streaming prose + run metadata**, not a full tool debugger.

## Security note

Any WebSocket or HTTP bridge to the gateway must preserve **origin validation** and **token** handling; do not pass untrusted `gatewayUrl` from the browser (see `assistant/docs/governance/OPENCLAW_ECOSYSTEM_UPDATE.md`).
