# AG-UI chat protocol for Zoe (zoe-data + `chat.html`)

## Decision (Option 1)

**Canonical AG-UI events** are emitted from **`zoe-data`** over **SSE** (`POST /api/chat/` with `stream=true`). The browser still authenticates through **zoe-auth** and nginx; the UI does not talk to the OpenClaw gateway WebSocket directly.

**Non-goals for this phase**

- Replacing nginx or zoe-auth with OpenClaw’s WebChat session model.
- Bundling third-party CopilotKit UI; we keep a vanilla `chat.html` subscriber.
- Full AG-UI branching/compaction UI (serialization is stored for future use).

**Migration path for `chat.html`**

- The stream now uses official `type` strings (`RUN_STARTED`, `TEXT_MESSAGE_CHUNK`, …).
- Legacy `session_start` / `message_delta` / `session_end` handlers remain in the client for older backends during rollout.

---

## Spike: `clawg-ui` (@contextableai/clawg-ui)

**What it is:** Community OpenClaw plugin that exposes an **AG-UI-shaped HTTP/SSE** surface for clients such as `@ag-ui/client` `HttpAgent`.

**Verdict: defer / do not adopt for Zoe production UI yet**

| Criterion | Fit |
|-----------|-----|
| **Auth** | Plugin expects OpenClaw/device token semantics; Zoe uses **cookie + zoe-auth** and per-user `chat_sessions` in SQLite. Extra bridge would be required. |
| **Network** | Browser must reach whatever host runs the plugin; today Zoe standardizes on **`zoe-data` on the web boundary**. |
| **Multi-user** | OpenClaw sessions are keyed by operator/device; Zoe maps **many family users** to `user_id` + `chat_sessions`. Keeping encoding in `zoe-data` preserves that mapping. |
| **Generative UI** | Zoe’s `:::zoe-ui` blocks are emitted as **`CUSTOM`** events (`zoe.ui_component`, `zoe.ui_command`). A generic plugin may not know these without extension. |

**Recommendation:** Revisit `clawg-ui` if Zoe ever exposes the OpenClaw gateway directly to browsers or standardizes on CopilotKit as the primary shell. Until then, **encode AG-UI in `zoe-data`** (current approach).

---

## Wire format: legacy Zoe vs canonical AG-UI

| Legacy SSE (`data.type`) | Canonical AG-UI | Notes |
|-------------------------|-------------------|--------|
| `session_start` | `RUN_STARTED` + `CUSTOM` `zoe.session` | `threadId` = `session_id`; `zoe.session` carries `messageId` for feedback/saves. |
| `session_end` | `RUN_FINISHED` | Terminal success event. |
| `message_delta` | `TEXT_MESSAGE_START` / `TEXT_MESSAGE_CHUNK` / `TEXT_MESSAGE_END` | Client may rely on `TEXT_MESSAGE_CHUNK` only for streaming text. |
| `skill_matched` | `STEP_STARTED` / `STEP_FINISHED` | Step name = intent label. |
| `action` / `action_result` | `TOOL_CALL_*` / `TOOL_CALL_RESULT` | Intent path uses a synthetic tool name `zoe-data.<intent>`. |
| `agent_state_delta` | `STATE_SNAPSHOT` | `snapshot` object replaces `state`. |
| `error` | `RUN_ERROR` | User-facing `message`; optional `code`. |
| `ui_component` / `ui_command` | `CUSTOM` `zoe.ui_*` | Generative UI extensions. |

**Source of truth**

- AG-UI concepts: [Events](https://docs.ag-ui.com/concepts/events), [OpenAPI](https://docs.ag-ui.com/api-reference/openapi.json)
- Python package: `ag-ui-protocol` (`ag_ui.encoder.EventEncoder`, `ag_ui.core.*`)

---

## Persistence (serialization groundwork)

Table **`chat_ag_ui_runs`** (see `zoe-data/database.py`) stores the **JSON list of events** for each completed generator run (`session_id`, `run_id`, `events`). This supports future **resume / inspection**; the UI does not yet reload from this table.

---

## Contract checklist (manual / tests)

- Every successful run ends with **`RUN_FINISHED`** (after at least `RUN_STARTED`).
- Failed runs emit **`RUN_ERROR`** and do not emit **`RUN_FINISHED`**.
- Assistant text is bracketed by **`TEXT_MESSAGE_START`** / **`TEXT_MESSAGE_END`** with stable **`messageId`**.
- Intent path emits a complete **tool call** sequence with stable **`toolCallId`**.

See `zoe-data/tests/test_ag_ui_chat_contract.py` for lightweight ordering checks.
