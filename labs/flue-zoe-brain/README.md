# flue-zoe-brain (LAB-HOSTED, PRODUCTION-REACHABLE)

A Flue-hosted Pi `Agent` on Zoe's local Gemma brain — replaces the per-turn
`pi --mode rpc` subprocess behind the `run_zoe_core` seam
(`docs/architecture/zoe-flue-integration.md` Phase 2).

**Wiring status:** lab-hosted but production-reachable via zoe-data's
`ZOE_BRAIN_BACKEND=flue` seam (`services/zoe-data/brain_dispatch.py`, priority
flue > core > legacy). The **shipped repo default is OFF** (`core` =
`services/zoe-core`); **this deployment flipped it live on 2026-07-03** (host
`ZOE_BRAIN_BACKEND=flue`, sidecar on `:3578` under a systemd user unit).

## Tools

Each tool is a Flue `defineTool` in `src/tools/zoe-tools.ts`. With the exception
of `get_time` (answered locally) and `recall_memory` (GET
`/api/memories/for-prompt`), every tool calls zoe-data's internal
`POST /api/system/intent-dispatch` with an intent from that endpoint's
`_DISPATCHABLE_INTENTS` allowlist (`services/zoe-data/routers/system.py`). Slot
shapes mirror the prod abilities (`services/zoe-core/abilities/*.ts`).

**Security & per-request identity:** the acting `user_id` is bound in trusted
code, **never** from model args. It is resolved from two trusted server-side
sources, in order:

1. **Per-request identity** — the `route` handler (`src/agents/zoe.ts`) reads the
   trusted `user_id` the zoe-data seam forwards in the request body
   (`services/zoe-data/zoe_flue_client.py`), then runs the whole turn inside a
   `runWithUserId(...)` context (`src/request-identity.ts`, an
   `AsyncLocalStorage`). Every tool call of that turn acts as **that** user, so
   each family member's turn touches their own memories/lists. The id is trusted
   because zoe-data resolved it from auth in trusted code and the route already
   fails closed on the bearer token — an unauthorized caller can't reach it.
2. **`ZOE_BRAIN_USER_ID` (env) fallback** — used only when no per-request identity
   is present (non-HTTP / test paths).

Tools fail closed (refuse) when neither yields a real user, and on guest-style
ids. The model only ever chooses *content*, never *whose* data.

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
| `note_search` | read | `note_search` | `query` |
| `add_to_list` | write | `list_add` | `item`, `list_type?` (shopping/tasks/personal/work/bucket) |
| `list_remove` | write | `list_remove` | `item`, `list_type?` |
| `journal` | read+write | `journal_create` / `journal_prompt` / `journal_streak` | `action` (create/prompt/streak), `content?`, `mood?` — create is write-gated |
| `people` | read+write | `people_create` / `people_search` | `action` (create/search), `name?`, `relationship?`, `query?`, `notes?` — create is write-gated |
| `media` | write | `music_play` / `music_control` / `music_volume` / `set_volume` / `music_setup` | `action` (play/control/set_music_volume/system_volume/setup), `query?`, `command?`, `level?`, `direction?` — system_volume = Zoe's TTS volume, not the player |
| `home` | write | `smart_home` (validated; entity_id built server-side) | `action` (on/off/dim/brighten), `room?` — lights only |
| `remember_fact` | write | `memory_store` (→ MemoryService.ingest) | `fact` |
| `remember_emotional_moment` | write | `memory_store` with `memory_type=emotional_moment` (→ MemoryService.ingest; valence/intensity ride ingest metadata) | `moment`, `valence?` (pos/neg/mixed), `intensity?` (0–1) |
| `activate_abilities` | local | none (progressive disclosure, see below) | `group` |

## Progressive tool disclosure

The model does **not** see all 19 tool schemas every call (on the 4B brain that
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
- **Coding built-ins are always stripped (safety floor):** Flue's harness
  injects its framework coding tools — `read`, `write`, `edit`, `bash`, `grep`,
  `glob`, `task` — into `context.tools` on **every** turn regardless of the
  agent's declared tool list (verified in `@flue/runtime`'s `createTools`;
  `defineAgent` exposes no option to suppress them). A family **voice** brain
  must never be handed `bash`/`write`/`edit`/`task`, and the extra schemas bloat
  the 4B context. `src/tools/tool-groups.ts` carries an explicit denylist
  (`CODING_BUILTIN_TOOL_NAMES`) and strips these **unconditionally** —
  `stripCodingBuiltins` runs in `applyPolicies` even when
  `ZOE_BRAIN_PROGRESSIVE_TOOLS=false`, so the disclosure kill switch can never
  re-expose them. Real Zoe tools (including ungrouped future ones) are never
  affected.
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
- **Activator fallback hardening** (E2E found indirect, keyword-free prompts
  never reached `activate_abilities`, and one reply fabricated a forecast):
  the agent instructions now carry the group catalogue (`GROUP_SUMMARY`) plus
  an imperative activate-first / never-fabricate doctrine
  (`src/agents/zoe.ts`); the activator's wire schema is pinned to a
  dead-simple single-enum object (`test/activator_fallback.test.ts`); and the
  keyword triggers cover high-value indirect phrasings
  (washing/laundry/outside → weather, "anything on \<day\>" / "am I free" →
  calendar). On-box measurement checklist: `LANDING.md`.
- **In-session context doctrine** (`IN_SESSION_CONTEXT_DOCTRINE` in
  `src/agents/zoe.ts`): the parity gate found the imperative recall doctrine
  ("you do NOT know anything about the person from your own head; ALWAYS call
  `recall_memory` first") was, taken absolutely, making the model distrust the
  live transcript — with an empty fresh-user recall store it forgot facts the
  user stated 1–3 turns earlier THIS session ("My name is Alex" → "What's my
  name?" → "I don't have anything stored about your name"). The appended
  doctrine rebalances precedence: facts stated during the conversation are used
  immediately from context, and an empty recall result means "nothing stored
  from before", not "never told this session". It does NOT weaken
  anti-fabrication or the past-conversation `recall_memory` rule — recall still
  fires ≥90% on standalone recall prompts.
- **Voice-delivery doctrine** (`VOICE_DELIVERY_DOCTRINE` in `src/agents/zoe.ts`),
  ported from prod's battle-tested spoken-mode soul (`_ZOE_SOUL_VOICE` in
  `services/zoe-data/zoe_agent.py`): since this family sidecar IS the voice brain,
  it now carries the same tight spoken discipline — reply in 1–3 short complete
  sentences, no markdown/lists/code, lead with the answer, brief but never clipped.
  The activator doctrine also gained prod's tool-first directives ("act
  proactively, don't ask a clarifying question first"; "don't claim you can't
  until a tool has actually tried and failed"). These sharpen *delivery* only —
  recall/activation/anti-fabrication are unchanged, and the on-box recall gate
  stays ≥90% (measured 19/20 = 95% at landing).
- **Kill switch:** `ZOE_BRAIN_PROGRESSIVE_TOOLS=false` restores
  all-schemas-every-call (A/B comparison).

## Prompt-fit history windowing

Durable sessions grow without bound; before #1138 nothing shrank the assembled
prompt, so once system prompt + tool schemas + history crossed the 8192-token
model context every turn on that session failed permanently (`400 request …
exceeds the available context size`). `src/context-window.ts` (applied first in
`applyPolicies`) drops the OLDEST whole user-turn blocks until the estimated
prompt (~4 chars/token + overheads) fits `ZOE_BRAIN_CONTEXT_WINDOW` minus
`ZOE_BRAIN_REPLY_RESERVE`. The system prompt (soul + doctrines) is never
touched; the newest turn and its ` zoe-uid:` envelope always survive whole; the
durable store keeps full history — only the wire prompt is windowed, and
windowed-out facts stay recoverable via `recall_memory`. Flue's native
compaction was evaluated and deliberately not enabled (its summarizer runs
through the same 8k model and stalls the voice path) — rationale + budget
failure mode in the module header. Kill switch: `ZOE_BRAIN_CONTEXT_WINDOW=0`.
Tests: `test/context_window.test.ts` (incl. an end-to-end fake llama-server).

## Seam-A sentinel streaming

The prod brain seam (`run_zoe_core_streaming`, docs/architecture/
zoe-flue-integration.md §3 Seam A) is a stream of text deltas plus
`__TOOL__:`/`__THINKING__:` sentinel chunks — the voice tool filler (#844)
keys off the `__TOOL__` phase=start sentinel arriving MID-turn. The sidecar
emits that exact contract via **content negotiation** on the existing route:

- `POST /agents/zoe/:sid` with `Accept: application/x-ndjson` (and **no**
  `?wait=result`) → a live NDJSON stream. Each line is a JSON string holding
  exactly one Seam-A chunk (text delta or sentinel, byte-identical to what
  `services/zoe-data/zoe_core_client.py` yields — Python `json.dumps` default
  separators, `ensure_ascii`), terminated by `{"done": true}` on success or
  `{"error": "..."}` on failure.
- `POST ... ?wait=result` (today's whole-result mode) and the plain 202
  admission are **untouched** — `?wait=result` wins even if the Accept header
  is also present.

Auth is unchanged (the streaming path upgrades the response only after the
fail-closed route + admission succeed); identity binding and the write gate
are tool-level and unaffected. Events come from the runtime's in-process
`observe()` feed (the durable stream buffers deltas ~3 s — too slow for voice
TTFT). Contract + framing details and known limits: `src/streaming.ts`;
byte-pinned tests: `test/sentinel_stream.test.ts`. Kill switch:
`ZOE_BRAIN_STREAM=0` restores pre-streaming behaviour entirely.

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
| `ZOE_BRAIN_USER_ID` | *(unset → fail closed)* | **fallback** acting user; used only when the request forwards no `user_id` (per-request identity from the seam wins — see Security above) |
| `ZOE_BRAIN_ALLOW_WRITES` | `false` | `true` enables real writes (otherwise dry-run) |
| `ZOE_BRAIN_TOOL_TIMEOUT_MS` | `8000` | per-call HTTP timeout against zoe-data |
| `ZOE_BRAIN_TOKEN` | *(unset)* | bearer token for the agent HTTP route |
| `ZOE_BRAIN_OPEN` | *(unset)* | `1` opts into an open route (local smoke runs only) |
| `ZOE_BRAIN_MAX_TOOL_ITERS` | `8` | hard per-turn tool-iteration ceiling |
| `ZOE_BRAIN_CONTEXT_WINDOW` | `8192` | model context budget for prompt-fit history windowing (`src/context-window.ts`); `0` disables windowing |
| `ZOE_BRAIN_REPLY_RESERVE` | `1536` | tokens held back from the window for the reply + estimator slack |
| `ZOE_BRAIN_PROGRESSIVE_TOOLS` | `true` | `false` disables progressive tool disclosure |
| `ZOE_BRAIN_STREAM` | `on` | `0`/`false` disables the NDJSON sentinel-stream mode |
| `ZOE_BRAIN_STREAM_TIMEOUT_S` | `180` | streamed-turn deadline (mirrors prod `ZOE_CORE_TIMEOUT_S`) |
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

**Stopping a hand-started sidecar — kill by port ONLY:**

```sh
lsof -ti tcp:3578 | xargs -r kill
```

Never `pkill -f` (it can take out unrelated node processes), and never restart
zoe-data (`:8000`) or llama-server (`:11434`) as part of a lab run.

## Operator measurement (pending)

The on-box measurement checklist for the #965 activator-fallback hardening —
sidecar on a scratch port, ~10 trigger-free prompts, score `tool_start` events,
re-run `parity/recall_reliability.py` — lives in [`LANDING.md`](LANDING.md).
Acceptance: ≥50% activator fire on the trigger-free set, zero fabricated tool
claims, recall ≥90%. It has not been run yet.
