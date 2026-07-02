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
| `activate_abilities` | local | none (progressive disclosure, see below) | `group` |

## Progressive tool disclosure

The model does **not** see all 11 tool schemas every call (on the 4B brain that
bloats the prompt, slows prefill in the 8k context, and hurts tool choice). The
sidecar ports prod's pattern (`services/zoe-core/extensions/abilities.ts`:
always-on core + relevance-matched tools) onto its own wire seam:

- **Where:** Flue `1.0.0-beta.6` has no per-turn tool switching
  (`AgentRuntimeConfig.tools` is static; pi-agent-core's
  `AgentHarness.setActiveTools()` is not surfaced), so disclosure happens in
  the registered `zoe-capped-completions` wire handler
  (`src/providers/capped-completions.ts`), which filters `context.tools` on
  every model call. All tools stay **registered** on the agent, so anything
  the model calls still executes with unchanged identity fail-closed and
  write-gate semantics — disclosure shrinks what the model *sees*, it is not
  a security boundary.
- **Active set** (`src/tools/tool-groups.ts`, derived statelessly from the
  request's own message window): the always-on core (`get_time`,
  `recall_memory`, `activate_abilities`) + groups keyword-matched against the
  last user message + groups the model unlocked via `activate_abilities` +
  groups whose tools were already used this session (sticky).
- **Groups:** weather, lists, timers, reminders, calendar, notes.
- **Trade-off:** the per-session set grows monotonically — a long session that
  touches every domain converges back to all schemas. Sessions are
  per-conversation, so a typical turn carries 3 schemas instead of 11. An
  `activate_abilities` round costs one extra tool iteration when keywords miss
  (counted against `ZOE_BRAIN_MAX_TOOL_ITERS`).
- **Kill switch:** `ZOE_BRAIN_PROGRESSIVE_TOOLS=false` restores
  all-schemas-every-call (A/B comparison).

## Build / typecheck / test

```sh
npm install
npm run typecheck          # tsc --noEmit
npm run build              # flue build --target node → dist/server.mjs
npm test                   # offline unit tests (node --test, type-stripping)
```

## Environment

| Var | Default | Purpose |
| --- | --- | --- |
| `ZOE_DATA_URL` | `http://127.0.0.1:8000` | zoe-data capability backend |
| `ZOE_INTERNAL_TOKEN` | `''` | sent as `X-Internal-Token` |
| `ZOE_BRAIN_USER_ID` | *(unset → fail closed)* | acting user, bound in trusted code |
| `ZOE_BRAIN_ALLOW_WRITES` | `false` | `true` enables real writes (otherwise dry-run) |
| `ZOE_BRAIN_TOOL_TIMEOUT_MS` | `8000` | per-call HTTP timeout against zoe-data |
| `ZOE_BRAIN_TOKEN` | *(unset)* | bearer token for the agent HTTP route |
| `ZOE_BRAIN_OPEN` | *(unset)* | `1` opts into an open route (local smoke runs only) |
| `ZOE_BRAIN_MAX_TOOL_ITERS` | `8` | hard per-turn tool-iteration ceiling |
| `ZOE_BRAIN_PROGRESSIVE_TOOLS` | `true` | `false` disables progressive tool disclosure |
| `ZOE_BRAIN_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible brain endpoint |
| `ZOE_BRAIN_API_KEY` | `local-no-key` | placeholder key for the completions client |
| `ZOE_BRAIN_DB` | `<package>/data/zoe-brain.db` | Flue durability sqlite path |
| `PORT` | `3000` | HTTP port (the systemd unit sets `3578`) |

The agent route **fails closed**: with neither `ZOE_BRAIN_TOKEN` nor
`ZOE_BRAIN_OPEN=1` set, every `POST /agents/zoe/:id` request is rejected with
401. `GET /health` is unauthenticated and never touches the model.

## Run as a service (systemd, operator opt-in)

A user-unit template ships at `scripts/setup/systemd/flue-zoe-brain.service`.
It is **optional and not enabled by default** — production only reaches the
sidecar through zoe-data's default-OFF `ZOE_BRAIN_BACKEND=flue` seam.

```sh
# 1. Build the sidecar (the unit runs dist/server.mjs, it does not build)
cd ~/assistant/labs/flue-zoe-brain
npm install && npm run build

# 2. Configure env (secrets live here, never in the unit)
cp .env.example .env
${EDITOR:-nano} .env        # set ZOE_BRAIN_TOKEN + ZOE_BRAIN_USER_ID at minimum

# 3. Install + enable the unit
cp ~/assistant/scripts/setup/systemd/flue-zoe-brain.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now flue-zoe-brain

# 4. Verify
curl -f http://127.0.0.1:3578/health
journalctl --user -u flue-zoe-brain -f
```

For hand runs (no unit): `PORT=3578 npm start` with the same env exported.
