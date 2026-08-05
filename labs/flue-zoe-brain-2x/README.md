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
> **Cutover is a deliberate operator step:** point the unit and `ZOE_BRAIN_DB` at
> this directory, having decided explicitly what happens to live session history.
> Never via the auto-deploy path.

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

### Prerequisite — the Phase-2 client change (NOT YET LANDED)

**The flip is not env-only today.** `services/zoe-data/zoe_flue_client.py` posts
the beta body shape, which 2.x rejects. Measured against the built 2.x server on
2026-08-06 (throwaway port, throwaway store):

| POST body | 2.x response |
|---|---|
| `{"message":"hi"}` — what `zoe_flue_client.py` sends today | **HTTP 400** `invalid_request` — *"Delivered messages must be `{ kind: "user", body: string, … }`"* |
| `{"kind":"user","body":"hi"}` | **HTTP 202** admitted |

2.x also rejects `?wait=result`. So the client needs a wire selector — the
`ZOE_FLUE_WIRE` flag tracked on the ideas board, **which does not exist in the
code yet** (`grep ZOE_FLUE_WIRE services/` returns only `docs/IDEAS.md`).
`parity/flue_wire.py` is the reference implementation of the 2.x wire
(fire-and-forget admission + NDJSON stream read) and the model for that change.
Until it lands, flipping `ZOE_FLUE_BRAIN_URL` alone breaks **every** brain turn.

That client change touches a voice-path file, so it is replay-gated in its own
right and belongs in its own PR.

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

# 1. Start the 2.x sidecar (built + configured per "Run as a service" above).
systemctl --user start flue-zoe-brain-2x
curl -fsS http://127.0.0.1:3579/health   # must print {"ok":true,...}

# 2. Point zoe-data at it. The 1.x sidecar stays UP and warm — do not stop it.
printf '\n# Flue 2.x cutover %s\nZOE_FLUE_WIRE=2\nZOE_FLUE_BRAIN_URL=http://127.0.0.1:3579\n' \
  "$(date -Is)" >> "$ENVF"

# 3. Restart zoe-data and wait for it to actually serve (is-active lies).
systemctl --user restart zoe-data
for _ in $(seq 1 60); do curl -fsS http://127.0.0.1:8000/health >/dev/null && break; sleep 2; done
curl -fsS http://127.0.0.1:8000/health

# 4. THE GATE. Must pass on the FLIPPED config, not before it.
cd /home/zoe/assistant
flock /tmp/zoe-voice-harness.lock \
  python3 scripts/maintenance/voice_regression_probe.py --samples 20 --stt remote
# said-vs-did must not regress and per-stage medians must not regress.
# Non-zero exit or WARN => ROLL BACK.
```

```sh
# ── ROLLBACK: back to the 1.x sidecar (:3578) ────────────────────────────────
set -euo pipefail
ENVF=/home/zoe/assistant/services/zoe-data/.env

# Drop the two cutover lines and the comment that introduced them.
sed -i '/^# Flue 2.x cutover /d;/^ZOE_FLUE_WIRE=/d;/^ZOE_FLUE_BRAIN_URL=/d' "$ENVF"

systemctl --user restart zoe-data
for _ in $(seq 1 60); do curl -fsS http://127.0.0.1:8000/health >/dev/null && break; sleep 2; done
curl -fsS http://127.0.0.1:8000/health

systemctl --user stop flue-zoe-brain-2x   # optional; leaving it running is harmless
```

Rollback works because the 1.x sidecar was never stopped and its v5 store was
never touched. That is the reason step 2 says *do not stop it*.

### Guards

- **Do not run the gate on a busy box.** The probe self-skips under low memory,
  and a skip is not a pass. Give it a quiet window with real headroom.
- **`systemctl is-active` lies** — poll `/health`, which is why the block does.
- **Do not run two Kokoro loads at once**; always take the flock, as above.

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
