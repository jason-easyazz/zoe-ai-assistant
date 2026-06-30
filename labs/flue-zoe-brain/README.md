# flue-zoe-brain (LAB ONLY)

A Flue-hosted Pi `Agent` on Zoe's local Gemma brain — a spike toward replacing
the per-turn `pi --mode rpc` subprocess behind the `run_zoe_core` seam
(`docs/architecture/zoe-flue-integration.md` Phase 2). **Not wired into
production.**

## Tools

Each tool is a Flue `defineTool` in `src/tools/zoe-tools.ts`. With the exception
of `get_time` (answered locally) and `recall_memory` (GET
`/api/memories/for-prompt`), every tool calls zoe-data's internal
`POST /api/system/intent-dispatch` with an intent from that endpoint's
`_DISPATCHABLE_INTENTS` allowlist (`services/zoe-data/routers/system.py`). Slot
shapes mirror the prod abilities (`services/zoe-core/abilities/*.ts`).

**Security:** the acting `user_id` is bound in trusted code from
`ZOE_BRAIN_USER_ID` (env) — never from model args. Tools fail closed when no
real user is configured. The model only chooses *content*, never *whose* data.

**Writes:** gated behind `ZOE_BRAIN_ALLOW_WRITES` (default OFF → dry-run that
does NOT mutate real data and instructs the model not to claim success).

| Tool | Kind | Endpoint / intent | Slots |
| --- | --- | --- | --- |
| `get_time` | read | local (no network) | — |
| `recall_memory` | read | GET `/api/memories/for-prompt` | `query?` |
| `get_weather` | read | `weather` | `forecast?` |
| `list_reminders` | read | `reminder_list` | — |
| `show_calendar` | read | `calendar_show` | `qualifier?` (today/tomorrow/this week/this month) |
| `show_list` | read | `list_show` | `list_type?` (shopping/tasks/personal/work/bucket) |
| `shopping_list_add` | write | `list_add` | `item`, `list_type=shopping` |
| `set_timer` | write | `timer_create` | `minutes`, `label?` |
| `add_reminder` | write | `reminder_create` | `title`, `date?`, `time?` |
| `add_calendar_event` | write | `calendar_create` | `title`, `date?`, `time?`, `category?` |
| `create_note` | write | `note_create` | `content`, `title?` |

## Build / typecheck

```sh
npm install
npm run typecheck          # tsc --noEmit
npx flue build --target node
```

## Environment

| Var | Default | Purpose |
| --- | --- | --- |
| `ZOE_DATA_URL` | `http://127.0.0.1:8000` | zoe-data capability backend |
| `ZOE_INTERNAL_TOKEN` | `''` | sent as `X-Internal-Token` |
| `ZOE_BRAIN_USER_ID` | *(unset → fail closed)* | acting user, bound in trusted code |
| `ZOE_BRAIN_ALLOW_WRITES` | `false` | `true` enables real writes (otherwise dry-run) |
| `ZOE_BRAIN_TOOL_TIMEOUT_MS` | `8000` | per-call HTTP timeout |
| `ZOE_BRAIN_TOKEN` | *(unset → open)* | bearer token for the agent HTTP route |
