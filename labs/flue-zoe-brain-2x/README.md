# flue-zoe-brain-2x — the PARALLEL Flue 2.0.1 port (NOT DEPLOYED)

> ## ⚠ Read this before touching anything here
>
> **This directory is not live and must not become live by accident.** The
> deployed sidecar on `:3578` is the sibling `labs/flue-zoe-brain/`, still on
> `@flue/*@1.0.0-beta.6`. This is a parallel port to `@flue/*@2.0.1`, proven by
> tests against a mock model. It has **no CI hook**, and its systemd template
> (`flue-zoe-brain-2x.service`, `:3579`) **ships inert** — not enabled, not
> started, and unreachable from zoe-data until an operator sets
> `ZOE_FLUE_BRAIN_URL`. See "Cutover runbook" below for the deliberate flip.
>
> **Why it is a separate directory rather than a version bump in place** — two
> independent reasons, either one sufficient:
>
> 1. **`labs/flue-zoe-brain/` auto-deploys.** `.github/workflows/deploy.yml` gates
>    on `git diff -- labs/flue-zoe-brain/` and, on any diff, runs
>    `npm ci && npm run build && systemctl --user restart flue-zoe-brain.service`
>    on the Jetson. A merged one-line change there reaches the live voice brain
>    with no further decision.
> 2. **The persisted schema boundary is one-way.** Flue 2.x stores schema v8; the
>    beta stored v5, and pre-1.0 schemas are *reset-only* — the runtime rejects an
>    older database **before any application code runs**. A 2.x process pointed at
>    the live data dir refuses to start, and once it has written a v8 store,
>    reverting the unit to the beta does **not** restore service either. Rollback
>    needs a wipe in *both* directions.
>
> **And the wire contract is not backward-compatible.** 2.x actively rejects
> `?wait=result` and changed the POST body shape, so the live
> `services/zoe-data/zoe_flue_client.py` cannot talk to a 2.x sidecar at all.
> Cutover is therefore a coordinated change on both sides — see
> `parity/flue_wire.py` for the reference implementation of the new wire.
>
> **Cutover is a deliberate operator step:** start this directory's own unit on
> `:3579` and point zoe-data at it, having decided explicitly what happens to live
> session history. Never via the auto-deploy path. Leave `ZOE_BRAIN_DB` at its
> default — the two sidecars must not share a store, or neither direction of the
> rollback works. Full sequence: "Cutover runbook" below.

A Flue-hosted Pi `Agent` on Zoe's local Gemma brain — replaces the per-turn
`pi --mode rpc` subprocess behind the `run_zoe_core` seam
(`docs/architecture/zoe-flue-integration.md` Phase 2).

**Wiring status of the DEPLOYED sibling** (`labs/flue-zoe-brain/`, not this
directory): lab-hosted but production-reachable via zoe-data's
`ZOE_BRAIN_BACKEND=flue` seam (`services/zoe-data/brain_dispatch.py`, priority
flue > core > legacy). The **shipped repo default is OFF** (`core` =
`services/zoe-core`); **this deployment flipped it live on 2026-07-03** (host
`ZOE_BRAIN_BACKEND=flue`, sidecar on `:3578` under a systemd user unit).

## Running this port

```bash
npm ci
npm run typecheck        # shipped code (src/ + build config)
npm test                 # 20 test files, in-process mock model, no ports
npm run build            # vite build — runs the 'use agent' registration scan
./smoke-built.sh         # boots dist/server.mjs on a throwaway port + data dir
```

Everything runs offline against an in-process mock OpenAI-compatible model
(`test/helpers/mock-model.ts`); nothing contacts llama-server on `:11434` or the
live zoe-data on `:8000`.

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

## The output-budget clamp (why `contextWindow` is not the real window)

`zoeLocalModel()` declares
`contextWindow: contextWindowTokens() + 4096 + outputBudgetTokens()` — **deliberately
larger than llama-server's actual 8192-token slot.** That looks wrong and is not.

pi-ai 0.83.0 added `clampMaxTokensToContext` (`dist/api/simple-options.js`),
which every openai-completions request now passes through:

```
available = model.contextWindow − estimateContextTokens(context) − 4096
maxTokens = min(maxTokens, max(1, available))
```

The file does not exist in 0.79.10 (the 1.x lane), and the 1.x provider declared
no `contextWindow` at all — so nothing clamped there, and the port carried the
1.x assumption forward. Declaring the real 8192 meant a ~4090-token prompt left
`8192 − 4090 − 4096 ≈ 6` tokens of output. Measured on the failed flip: replies
truncated to 1-8 tokens with `stopReason: "length"`, and one length-stopped
**tool call** took a whole turn down with `ConversationRecordInvariantError`.
That was the flip's `CANT_DO`.

On 0.83 this field feeds *only* that clamp for this deployment (Flue's threshold
compaction is pinned off at the agent), so it is sized to protect the output
budget rather than to describe the server. The real prompt budget is enforced
independently and earlier by `src/context-window.ts` — which is what makes the
decoupling safe. The arithmetic: with `declared = W + 4096 + reserve`,
`available = W + reserve − prompt`, so **any prompt that llama-server would
accept at all (`prompt ≤ W`) leaves the full output budget.** The premise holds
because the two estimators agree closely on the real path (measured 2937 vs 2957
tokens on a live harness turn) and windowing keeps the prompt a further
`ZOE_BRAIN_REPLY_RESERVE` below `W`.

**`maxTokens` is the reply reserve, and that is the other half of the fix.**
pi-ai's clamp exists to stop a caller asking for more output than the context can
hold; defeating it means this deployment has to honour that constraint itself, or
it has removed a guard and put nothing in its place. llama-server runs
`--ctx-size 16384 --parallel 2` — an **8192-token slot per lane** — with context
shifting off on this build, so generation that reaches the end of the slot simply
stops with `finish_reason: "length"`. A flat 2048-token cap against a prompt at
the full 6656-token budget asks for 8704 tokens of slot and is silently cut at
1536: the same truncation, re-created from the other direction, and it would make
step 6's assertion unsound. Tying the cap to the reserve gives

```
prompt ≤ W − reserve   (windowing)      output ≤ reserve   (the cap)
⇒ prompt + output ≤ W  — the request always fits the slot
```

That bounds what the *clamp* and the *slot* can do to a reply; it does not make a
`"length"` stop impossible. A model that genuinely writes past the reserve still
stops on it — which on a brain whose replies run to tens of tokens is itself an
anomaly, which is why step 6 says *any* length stop is a bug rather than *cannot
happen*.

`ZOE_BRAIN_CONTEXT_WINDOW=0` (windowing off) declares `0`, which pi-ai reads as
*no clamp* — that removes the `prompt ≤ W` premise, so the honest guard becomes
llama-server's loud 400 rather than a silent truncation. The reply cap does *not*
collapse with it (`replyReserveTokens(0)` would be 0, and a falsy `maxTokens` is
dropped from the wire entirely), so it falls back to the reserve sized against the
default slot.

**Do not re-tie the declared window to the windowing budget** "so they can never
drift". That was the original rationale and it is precisely what caused the bug.
Tests: `test/output_budget_clamp.test.ts` — it imports the real clamp from
`node_modules` (so an upstream formula change turns it red), probes the 4096
constant through that function rather than trusting the source, pins
`prompt budget + output budget == slot` across several env configurations, and
carries the pre-fix declaration as an explicit negative control that reproduces
the recorded 8-token truncation (and `max_completion_tokens: 1` end-to-end on the
wire). Operationally the guard is step 6 of the flip runbook.

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
- `POST ... ?wait=result` is **GONE on 2.x** — the runtime rejects it with HTTP
  400 (*"Agent prompts are fire-and-forget"*), measured 2026-08-06. The beta's
  special case where that query outranked the Accept header went with it (see
  `src/streaming.ts`). The plain 202 admission is unchanged; to get a whole
  result, admit and then `GET` the same URL.

Auth is unchanged (the streaming path upgrades the response only after the
fail-closed route + admission succeed); identity binding and the write gate
are tool-level and unaffected. Events come from the runtime's in-process
`observe()` feed (the durable stream buffers deltas ~3 s — too slow for voice
TTFT). Contract + framing details and known limits: `src/streaming.ts`;
byte-pinned tests: `test/sentinel_stream.test.ts`. Kill switch:
`ZOE_BRAIN_STREAM=0` restores pre-streaming behaviour entirely.

## Build / typecheck / test

```sh
npm ci
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
| `ZOE_BRAIN_CONTEXT_WINDOW` | `8192` | model context budget for prompt-fit history windowing (`src/context-window.ts`); `0` disables windowing **and, with it, pi-ai's output clamp** — see "the output-budget clamp" below |
| `ZOE_BRAIN_REPLY_RESERVE` | `1536` | tokens held back from the window for the reply + estimator slack |
| `ZOE_BRAIN_PROGRESSIVE_TOOLS` | `true` | `false` disables progressive tool disclosure |
| `ZOE_BRAIN_STREAM` | `on` | `0`/`false` disables the NDJSON sentinel-stream mode |
| `ZOE_BRAIN_STREAM_TIMEOUT_S` | `180` | streamed-turn deadline (mirrors prod `ZOE_CORE_TIMEOUT_S`) |
| `ZOE_BRAIN_BASE_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible brain endpoint |
| `ZOE_BRAIN_API_KEY` | `local-no-key` | placeholder key for the completions client |
| `ZOE_BRAIN_DB` | `<package>/data/zoe-brain.db` | Flue durability sqlite path — **leave unset**, see the storage note under "Run as a service" |
| `PORT` | `3000` | HTTP port (`flue-zoe-brain-2x.service` sets **`3579`**, not 3578) |

The agent route **fails closed**: with neither `ZOE_BRAIN_TOKEN` nor
`ZOE_BRAIN_OPEN=1` set, every `POST /agents/zoe/:id` request is rejected with
401. `GET /health` is unauthenticated and never touches the model.

## Run as a service (systemd, operator opt-in)

A user-unit template ships at `scripts/setup/systemd/flue-zoe-brain-2x.service`
— **port 3579, deliberately NOT 3578.** It is designed to run *beside* the live
`flue-zoe-brain.service`, which stays up and warm: that is what makes the cutover
an env flip with an instant rollback rather than a rebuild. It **ships inert** —
installing it enables nothing, and zoe-data does not address `:3579` until an
operator sets `ZOE_FLUE_BRAIN_URL`.

```sh
# 1. Build the sidecar (the unit runs dist/server.mjs, it does not build)
cd ~/assistant/labs/flue-zoe-brain-2x
npm ci && npm run build

# 2. Configure env (secrets live here, never in the unit)
cp .env.example .env
${EDITOR:-nano} .env        # set ZOE_BRAIN_TOKEN + ZOE_BRAIN_USER_ID at minimum
                            # leave ZOE_BRAIN_DB unset — see "storage" below

# 3. Install + enable the unit
cp ~/assistant/scripts/setup/systemd/flue-zoe-brain-2x.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now flue-zoe-brain-2x

# 4. Verify
curl -f http://127.0.0.1:3579/health
journalctl --user -u flue-zoe-brain-2x -f
```

**Memory protections are identical to the live sidecar's** (`MemorySwapMax=0`,
`MemoryLow=512M`, `MemoryMax=2G`), because after cutover this unit inherits the
same latency contract. `tests/unit/test_systemd_memory_protection.py` pins them.
Apply changes as a drop-in in `~/.config/systemd/user/flue-zoe-brain-2x.service.d/`;
never copy the template over a running unit.

**Storage stays separate, and must.** `ZOE_BRAIN_DB` defaults to
`labs/flue-zoe-brain-2x/data/zoe-brain.db`, already a different file from the
live sidecar's. Flue 2.x writes schema v8 and the beta writes v5; the runtime
rejects an older store before any application code runs, so a shared file makes
the rollback half of the runbook impossible.

For hand runs (no unit): `PORT=3579 npm start` with the same env exported — but
once the unit is installed, `:3579` is taken, so pick another scratch port or
stop the unit first. Both `LANDING.md` recipes still say `3579`.

**Stopping a hand-started sidecar — kill by port ONLY:**

```sh
lsof -ti tcp:3579 | xargs -r kill
```

Never `pkill -f` (it can take out unrelated node processes), and never restart
zoe-data (`:8000`) or llama-server (`:11434`) as part of a lab run.

## Cutover runbook (operator)

The operator lifted the merge hold on 2026-08-06. Merging this directory changes
nothing on the box; the flip below is the separate, deliberate step.

### Prerequisite — the Phase-2 client change (✅ LANDED, this PR's sibling)

**The wire selector exists**: `ZOE_FLUE_WIRE` in
`services/zoe-data/zoe_flue_client.py` (PR #1637, replay-gated in its own
right). Default `1` posts the wire-1 body byte-identically; `2` speaks the 2.x
`{kind: "user", body}` shape with fire-and-forget admission + NDJSON stream
read (modeled on `parity/flue_wire.py`). The historical measurement that
motivated it, against the built 2.x server on 2026-08-06 (throwaway port,
throwaway store):

| POST body | 2.x response |
|---|---|
| `{"message":"hi"}` — the wire-1 shape | **HTTP 400** `invalid_request` — *"Delivered messages must be `{ kind: "user", body: string, … }`"* |
| `{"kind":"user","body":"hi"}` | **HTTP 202** admitted |

2.x also rejects `?wait=result`. Both misconfig directions are diagnosed in the
client's logs: a wire-2 reply arriving on wire 1 names the flag, and a 1.x
sidecar 400-ing the wire-2 body names it too. The flip below is therefore
env-only: `ZOE_FLUE_WIRE=2` + `ZOE_FLUE_BRAIN_URL` together, never one alone.

### The flip (once the client change has landed)

`ZOE_BRAIN_BACKEND=flue` already lives in `services/zoe-data/.env`, and that is
the file the **live** `zoe-data.service` actually loads (`systemctl --user cat
zoe-data` — the installed unit reads `services/zoe-data/.env` and `~/.hermes/.env`;
it does *not* read the repo-root `.env` the tracked template lists). Both new
vars go in the same file. One copy-paste block:

```sh
# ── FLIP: zoe-data 1.x sidecar (:3578) -> Flue 2.x sidecar (:3579) ───────────
set -euo pipefail
ENVF=/home/zoe/assistant/services/zoe-data/.env
BAK="${ENVF}.pre-flue2x"

# 1. Start the 2.x sidecar (built + configured per "Run as a service" above).
systemctl --user start flue-zoe-brain-2x
curl -fsS http://127.0.0.1:3579/health   # must print {"ok":true,...}

# 2. Snapshot the env file FIRST. Rollback restores this exact file, so it
#    survives a pre-existing ZOE_FLUE_BRAIN_URL that a blind delete would lose.
#    Refuse to overwrite an existing snapshot — that means a flip is already in
#    progress and the pre-flip state is the one worth keeping.
[ -e "$BAK" ] && { echo "FAIL: $BAK exists — already flipped? roll back first."; exit 1; }
cp -p "$ENVF" "$BAK"

# 3. Point zoe-data at the 2.x sidecar. The 1.x sidecar stays UP and warm —
#    DO NOT stop it; it is the rollback target. Replace-or-append, so a rerun
#    does not accumulate duplicate keys (last value would win, silently).
for kv in "ZOE_FLUE_WIRE=2" "ZOE_FLUE_BRAIN_URL=http://127.0.0.1:3579"; do
  k=${kv%%=*}
  if grep -q "^${k}=" "$ENVF"; then
    sed -i "s|^${k}=.*|${kv}|" "$ENVF"
  else
    printf '%s\n' "$kv" >> "$ENVF"
  fi
done
grep -E '^(ZOE_BRAIN_BACKEND|ZOE_FLUE_WIRE|ZOE_FLUE_BRAIN_URL)=' "$ENVF"   # eyeball it

# 4. Restart zoe-data and wait for it to actually SERVE (is-active lies).
systemctl --user restart zoe-data
for _ in $(seq 1 60); do curl -fsS http://127.0.0.1:8000/health >/dev/null && break; sleep 2; done
curl -fsS http://127.0.0.1:8000/health

# 5. THE GATE. Must pass on the FLIPPED config, not before it.
cd /home/zoe/assistant
flock /tmp/zoe-voice-harness.lock \
  python3 scripts/maintenance/voice_regression_probe.py --samples 20 --stt remote
# said-vs-did must not regress and per-stage medians must not regress.
# Non-zero exit, WARN, or a SKIP (low memory — a skip is NOT a pass) => ROLL BACK.

# 6. THE LENGTH-STOP ASSERTION — MANDATORY, and step 5 passing does NOT imply it.
#    Run it against the 2.x store AFTER the replay run. Any hit => ROLL BACK.
python3 labs/flue-zoe-brain-2x/parity/count_length_stops.py
```

```sh
# ── ROLLBACK: back to the 1.x sidecar (:3578) ────────────────────────────────
set -euo pipefail
ENVF=/home/zoe/assistant/services/zoe-data/.env
BAK="${ENVF}.pre-flue2x"

# Restore the exact pre-flip file rather than deleting keys, so anything that
# was already set (including a prior ZOE_FLUE_BRAIN_URL) comes back as it was.
[ -e "$BAK" ] || { echo "FAIL: no $BAK — do not guess; inspect $ENVF by hand."; exit 1; }
cp -p "$BAK" "$ENVF" && rm -f "$BAK"

systemctl --user restart zoe-data
for _ in $(seq 1 60); do curl -fsS http://127.0.0.1:8000/health >/dev/null && break; sleep 2; done
curl -fsS http://127.0.0.1:8000/health

systemctl --user stop flue-zoe-brain-2x   # optional; leaving it running is harmless
```

Rollback works because the 1.x sidecar was never stopped and its v5 store was
never touched. That is why step 3 says *do not stop it* — and why the two units
must never share `ZOE_BRAIN_DB`.

Once the flip is accepted and you no longer want the escape hatch, delete the
snapshot (`rm services/zoe-data/.env.pre-flue2x`) — it is a copy of a secret-
bearing file and should not linger.

### Guards

- **Do not run the gate on a busy box.** The probe self-skips under low memory,
  and a skip is not a pass. Give it a quiet window with real headroom.
- **`systemctl is-active` lies** — poll `/health`, which is why the block does.
- **Do not run two Kokoro loads at once**; always take the flock, as above.
- **Step 6 is not optional, and step 5 does not imply it** — see below.

### Step 6: the length-stop assertion, and why verdict counts are not enough

**A replay verdict of `OK` does not mean the reply was complete.** The 2026-08-06
attempt scored `OK=18 CANT_DO=1 EMPTY=1` and was written up as a near-pass. It
was not. pi-ai 0.83's output clamp had cut the reply budget to single digits, so
the lane was truncating replies **mid-sentence** on turns the harness scored `OK`:

| sample | 1.x | 2.x |
|---|---|---|
| `065921_799` "A. Zoe," | `"I'm Zoe."` | `"I"` |
| `065122_389` "Turn on the light" | `"Which room needs the light turned on?"` | `"Which room are"` |

Both non-empty; neither matches `_CANT_DO_RE`; both scored `OK`. The verdict
counts are *structurally blind* to truncation, and the single real `CANT_DO` was
the same defect in its fatal form (a length-stopped **tool call** leaves the
canonical conversation stream without a committed result batch, and the next
reduce throws `ConversationRecordInvariantError`, killing the turn).

The store is not blind. `assistant_message_completed` records carry `stopReason`,
so count them:

```sh
python3 labs/flue-zoe-brain-2x/parity/count_length_stops.py
# PASS: 0 length-stopped replies in .../data/zoe-brain.db     -> proceed
# FAIL: N length-stopped replies ...                          -> ROLL BACK
```

Read-only, stdlib Python (the box has no `sqlite3` CLI), exits 1 on any hit, and
reassembles Flue's spilled >1MB batches so a truncation cannot hide in one.
`--db` points it at another store, `--json` for machine consumption. Both
directions are pinned by `tests/unit/test_flue2x_length_stop_gate.py`; the FAIL
path was verified against the real 2026-08-06 store, where it finds all 8.

**Any `"length"` stop on this deployment is a bug, not a tuning knob.**
llama-server has an 8192-token slot and `src/context-window.ts` windows every
prompt to fit inside it *with* a reply reserve, so a reply that runs out of room
means the budget arithmetic is wrong. See "the output-budget clamp" below.

### Two instrument corrections (from the 2026-08-06 post-mortem)

Both of these made the failed flip look cleaner than it was. They cost a whole
flip attempt; do not re-learn them.

- **Grep the STREAMING error prefix, not `flue wire-2`.** The flip report said
  "zero transport errors". The failure *was* logged — under a different prefix.
  `ZOE_BRAIN_STREAM` is on, so turns take the streaming path, which logs
  `flue stream reported error: …` (`services/zoe-data/zoe_flue_client.py`). The
  `flue wire-2 …` diagnostics only cover `_run_turn_aggregated_wire2`, which runs
  **only when streaming is disabled**. Grepping for `flue wire-2` could never have
  found it. Grep for **both**:

  ```sh
  grep -E 'flue stream reported error|flue wire-2' ~/.zoe-logs/*.log
  ```

  (zoe-data logs to `~/.zoe-logs/`, **not** journald.)
- **A speed ratio is meaningless while replies truncate.** The report's
  "0.76–0.77× vs baseline" was reproduced exactly — and is substantially an
  artifact: 2.x was generating far fewer tokens. Treat any speed comparison as
  void until step 6 is green, then re-measure.

### `deploy.yml` cannot auto-restart this unit — verified, not assumed

`.github/workflows/deploy.yml` gates its sidecar rebuild on
`git diff --quiet "$OLD_SHA" HEAD -- labs/flue-zoe-brain/`, a **path prefix that
matches the directory `labs/flue-zoe-brain/` only** — `labs/flue-zoe-brain-2x/`
is a sibling, not a child, so it never matches. The job also short-circuits on
`systemctl --user list-unit-files flue-zoe-brain.service`, naming the 1.x unit
explicitly. Nothing in the workflow references `flue-zoe-brain-2x`. The sibling
directory was chosen for exactly this property; re-check it if the workflow's
path filter is ever loosened to a glob.

## Operator measurement (pending)

The on-box measurement checklist for the #965 activator-fallback hardening —
sidecar on a scratch port, ~10 trigger-free prompts, score `tool_start` events,
re-run `parity/recall_reliability.py` — lives in [`LANDING.md`](LANDING.md).
Acceptance: ≥50% activator fire on the trigger-free set, zero fabricated tool
claims, recall ≥90%. It has not been run yet.
