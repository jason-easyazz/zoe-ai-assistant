# Zoe ⊕ Flue — the merge, on a shared Pi core

> **North star: one well-organised machine, not a frankenstein.** This plan is
> backed by a four-thread deep dive — Pi internals, Zoe's *real* current
> architecture, Flue's intended integration shape, and industry patterns — which
> all converge on the same design. Honors the rocks in [`../CANONICAL.md`](../CANONICAL.md).
> Supersedes [`zoe-harness-on-flue.md`](zoe-harness-on-flue.md) (now one phase).

---

## 0. The key discovery: it's already one engine

The merge is far cleaner than "glue two foreign runtimes," because Zoe's brain and
Flue are **the same engine consumed at two layers**:

- **Pi** (`@earendil-works/pi-*`, MIT, ~v0.80) is a three-layer stack: `pi-ai`
  (providers) → `pi-agent-core` (the reusable `Agent` loop) → `pi-coding-agent`
  (CLI + extensions + tools).
- **Zoe's brain is Pi.** `services/zoe-core` is `@earendil-works/pi-coding-agent` —
  the **default production brain** (`brain_dispatch.py`, `ZOE_USE_CORE_BRAIN=true`;
  the "lab-only" note in `zoe-core/README.md` is stale). Python `zoe-data` runs it
  **out-of-process**, spawning `pi --mode rpc` per `(user, session)` via
  `zoe_core_client.py`, with `soul` / `memory` / `abilities` / `local-gemma`
  extensions.
- **Flue is Pi too.** `packages/runtime/src/session.ts` imports the same `Agent`
  from `pi-agent-core` and drives it **in-process**.
- Both float `^0.79` → resolve to the **same 0.80.x**. Same family, same version,
  identical event/message vocabulary — **not divergent forks.**

So "merge" = **collapse a process boundary onto the shared Pi core**: move Zoe's
brain from *"Python spawns the `pi` CLI every turn"* to *"Flue hosts the durable Pi
`Agent`,"* keeping the model and behaviour. That's convergence, not a rewrite.

---

## 1. Principles (the anti-frankenstein rules)

From the patterns research (Anthropic *Building Effective Agents*, HumanLayer
*12-Factor Agents*, Vellum):

- **One source of truth per concern** — routing/session → Python core; durable
  workflow/run state → Flue; model weights → llama-server. No concern owned twice.
- **Two thin, explicit, language-neutral seams** — not one fused app, not many
  chatty cross-language calls.
- **The agent is a stateless reducer** (`f(context) → next action`); it owns
  workflow state only and never duplicates session state (12-factor Factor 5).
- **Keep the fast path in front; escalation is an explicit branch**, not the default.
- **Graceful degradation** — if Flue is down, the deterministic path still answers.
  *Flue down ⇒ "agentic/channels down," never "Zoe can't answer."*
- **Integrate, don't rewrite.** Python and Node stay separate processes that can
  ship independently (avoid the distributed monolith).

---

## 2. Who owns what

| **zoe-data** (Python) — front door + fast path + system-of-record | **Flue** (Node sidecar) — the agentic layer, on Pi | **llama-server** — shared model |
|---|---|---|
| All user channels (voice, touch, web, LiveKit); Moonshine STT; Kokoro TTS | Hosts the **Pi `Agent`** = the conversational brain (durable, in-process) | Gemma E4B-QAT (+MTP draft) on `:11434`, OpenAI-compatible |
| Tier-0/1/1.5 deterministic core (`intent_router` → `semantic_router` → `expert_dispatch` → `fast_tiers`) | The autonomous **engineering harness** + durable agentic workflows | The "rock" — behind **one swappable URL seam** |
| Session state, MemPalace memory, auth, DB — **the SoR** | MCP/tool host; observability; the live "what Zoe is doing" event stream | Shared sidecar; both paths point at it by URL |

zoe-data's fast path is **untouched** — that's how zero-regression is guaranteed.

---

## 3. The seams (concrete, from the code — not invented)

**Seam A — brain escalation (the one coarse handoff).**
`fast_tiers.resolve()` returns a finished answer **or `None`**; on `None` the turn
escalates to the brain via `run_zoe_core(message, session_id, user_id) → stream of
text deltas + __TOOL__/__THINKING__ sentinels` (`chat.py`, env-gated by
`_USE_ZOE_CORE`). That contract already has **two implementations** (Pi subprocess +
legacy). **The Flue-hosted brain is a third implementation in the same seam** —
`zoe-data` calls `POST /agents/zoe/:session?wait=result` (or streams) and maps the
result through its existing AG-UI sentinel handlers. Voice/fast-path never route
through Flue → graceful degradation to the local brain lane if Flue is down.

**Seam M — the model (provider).**
Flue's Pi `Agent` reaches Gemma the blessed first-party way (the shipped
`hello-world` example does this for ollama/lmstudio):
`registerProvider('zoe', { api: 'openai-completions', baseUrl: 'http://127.0.0.1:11434/v1' })`,
then `model: 'zoe/gemma-…'`. **The Gemma rock is untouched** — same server both Zoe's
current Pi extension and Flue point at.

**Seam B — tools & memory (MCP / typed tools).**
Zoe's capabilities (memory recall, calendar, lists, abilities) are exposed to the
Flue brain as **tools**: start with `defineTool` HTTP wrappers over zoe-data's
existing `/api/*` (lowest friction; this replaces today's Pi-extension HTTP
callbacks), and promote to **MCP** (`connectMcpServer`, local **stdio**) as the
surface matures. **MemPalace stays the system-of-record** — Flue's `db.ts`
(`sqlite('./data/flue.db')`) holds *only* conversation/run durability, never
business data (per Flue's own docs).

**Seam C — channels (into the front door, not a parallel brain).**
User channels — including **Telegram** — enter **zoe-data's front door** (`/api/chat`
with a `channel` tag → `fast_tiers` selects the channel profile), go through the
fast path, and escalate to the Flue brain only when needed — exactly like voice and
touch. A channel is *another doorway into the one core*, never a separate brain.

---

## 4. Zero-regression guarantees (proven, not assumed)

- **Voice fast-path is independent of Flue** — the rock. Never in the
  STT→router→brain→TTS latency path.
- **Latency gate:** the [`#735` probe](../../scripts/maintenance/zoe_latency_probe.py)
  must show no voice regression for any shared-resource change (harness already
  passed at **+1.5 ms**).
- **Graceful degradation:** Flue/brain-sidecar down ⇒ fall back to the deterministic
  path (and the legacy local brain lane) — Zoe still answers.
- **Lab-prove before prod (Samantha bar):** the Flue brain must reach parity with the
  current Pi-CLI brain on the voice-sample corpus before it serves real users;
  demo-users-only until then.
- **Pin Pi in lockstep:** both sides float `^0.79`; choose one **exact**
  `pi-agent-core`/`pi-ai` version for Zoe *and* Flue so the shared types can't drift.
- **Memory budget:** the Flue Node sidecar (~300 MB) is sized + monitored on the
  16 GB Orin.

---

## 5. What "move the brain to Flue" actually means (the port)

The `Agent` loop is the *same Pi*; what changes is the **extension surface**. Zoe's
four `pi-coding-agent` extensions are re-expressed against Flue's `Agent`/tool model:

| zoe-core extension (`pi-coding-agent`) | Becomes in Flue |
|---|---|
| `soul.ts` (persona) | Agent `instructions` |
| `provider-local-gemma.ts` | `registerProvider('zoe', …:11434)` (Seam M) |
| `memory.ts` (`/api/memories/for-prompt`) | a tool / MCP call (Seam B) |
| `abilities.ts` (zoe-data `/api/*`) | typed tools (Seam B) |

This is a **real port, not a config flip** — but because both already speak the same
Pi vocabulary at the same version, it's convergence on one engine. Bonus: it removes
the per-turn subprocess spawn, the LRU worker pool, the RPC-stream parsing, and the
per-process multi-user workaround in `zoe_core_client.py`.

---

## 6. Migration order (phased, additive, each gated by §4)

- **Phase 0 — Harness on Flue. ✅ DONE.** scout→implement→verify→openPR proven;
  +1.5 ms voice (PR #858).
- **Phase 1 — Telegram as a front-door channel.** Transport (long-poll) → zoe-data
  `/api/chat` with `channel="telegram"` → `fast_tiers`.
  *(The running Flue Telegram bot is re-slotted to this — feed the front door with
  the channel tag, not a separate brain.)*
  **Identity is mandatory, not optional:** the bridge must map the *verified*
  Telegram sender id → a Zoe user through Zoe's own auth boundary (a device/session
  token or an explicit allow-list→user mapping owned by zoe-data) — never trust an
  identity value supplied by the bridge, and never let multiple senders collapse
  into a shared `guest`. Retiring Hermes Telegram happens only **after** this
  channel is live and verified (gates below).
- **Phase 2 — Flue brain behind the `run_zoe_core` seam. ✅ DELIVERED** (PR trail
  in §10). The sidecar lives at `labs/flue-zoe-brain/` (`registerProvider` Gemma,
  `soul` → instructions); zoe-data reaches it through the **default-OFF**
  `ZOE_BRAIN_BACKEND=flue` seam (#904 — default path byte-identical). E2E-verified
  through the seam. It is a **lab sidecar, not canonical** — the voice-corpus
  parity gate (§4) has not been run, and the sentinel contract is not yet emitted
  (both are Phase-4 blockers, §10).
- **Phase 3 — Tools & memory (Seam B). ✅ DELIVERED for the 12-tool lab surface**
  — `defineTool` HTTP wrappers over zoe-data intents (#915 reliability, #952
  progressive disclosure, #965 activator hardening). Full parity with the
  extension brain's ~56 tools is still open (Phase-4 blocker, §10).
- **Phase 4 — Cut over + retire (gated, reversible).** Flip `_USE_ZOE_CORE` → Flue
  for real users **only after** §4's gates pass (voice-corpus parity + #735 +
  operator sign-off), and keep the flip reversible (the Pi-CLI brain stays as the
  fallback until Flue has soaked). **Retire a system only once Flue demonstrably
  owns its job and the fallback is preserved:** retire the per-turn `pi --mode rpc`
  brain path only after the Flue brain is the proven default; retire each
  Hermes/Multica/OpenClaw loop only after Flue has replaced that specific function
  (engineering harness, fallback agent execution, PR automation) and an operator has
  signed off. **Never remove a live bridge before its Flue replacement is proven** —
  removing fallback paths ahead of their replacement is the one move that breaks
  prod. Retirement is per-function and per-operator-approval, not a sweep.
- **Phase 5 — Thin & enrich.** zoe-data settles into front-door + fast-path + data/IO;
  more channels + durable workflows on Flue; wire the live "what Zoe is doing" UI
  stream (`@flue/sdk`/`@flue/react`) into touch/chat.

---

## 7. What we explicitly do NOT do

- Don't embed Flue in Python — it's a **Node sidecar** (own systemd unit), peer over
  HTTP/SDK.
- Don't route voice through Flue; don't make the brain a hard dependency of a turn.
- Don't build a parallel brain — channels feed the **one** front door/core.
- Don't duplicate state — Flue is a stateless reducer; **zoe-data is the SoR**.
- Don't big-bang; **retire by removing** (git keeps history); **pin Pi**.

---

## 8. Retirement inventory — what each legacy system does, and what recreates it

> **The rule (from §6 Phase 4): a capability is retired only after its Flue
> recreation demonstrably owns the job, with operator sign-off. Nothing on this
> list is deleted because it's old — it's deleted because Flue now does it.**
> This is the concrete checklist behind "retire Multica/Hermes/OpenClaw."

### 8.1 Multica — board-driven engineering orchestration

| Capability today (code) | Flue recreation | Retire-gate |
|---|---|---|
| Ticket/board orchestration: `executors/kanban_adapter.py`, `multica_ticket_contract.py`, `multica_autopilot_sync.py`, `pipeline_handoff.py` | A **durable Flue workflow** per ticket: bound agent delegates scout → implement → verify → openPR to subagents (the Phase-0 harness pattern, proven in #858). Ticket state lives in Flue run durability; zoe-data stays SoR for anything user-visible. | Flue processes ≥5 real tickets end-to-end (branch → PR → merged) with no stalls; operator sign-off. |
| Worktree lifecycle: `worktree_bootstrap.py` (create/remove/prune, squash-merge detection) | Reused as-is first (it's harness-agnostic Python callable via a tool), ported into the Flue workflow's exec helpers later. | Flue workflow owns worktree create/cleanup for its own tickets. |
| Greptile PR loop: `greploop_guard.py`, `greptile_client.py` (packet-only fix loops, thread resolution) | A Flue **workflow step/subagent** wrapping the same packet-generation logic — port the module, don't rewrite the mechanics. | A Flue-driven greploop takes ≥3 PRs from open → threads-resolved → merged. |

### 8.2 Hermes — engineering/browser delegation

| Capability today | Flue recreation | Retire-gate |
|---|---|---|
| Engineering delegation: `hermes_http.py`, `~/.hermes/skills` (`zoe-engineering`, `github-greptile-loop`, `source-code-context`, `code-structure-cleanup`) | Flue **agents + subagents** with `defineTool`/MCP over the same zoe-data endpoints; each Hermes skill becomes a Flue agent definition (prompts are largely reusable). | Per-skill: the Flue agent completes the same task class the skill handled. |
| Browser work: `browser_broker.py` + `zoe-cloakbrowser` skill | CloakBrowser tools exposed to Flue via MCP (Seam B). | Flue agent completes a real browser task through the broker. |
| Knowledge refresh: `zoe-status-refresh` skill | A scheduled Flue workflow writing OKF records under `docs/knowledge/` (records only — never AGENTS.md contracts). | One full refresh cycle produced by Flue and lint-clean. |
| `hermes-agent.service` (PAUSED since 2026-06-21: enabled but inactive) | Superseded by the Flue runtime unit. | Disable + remove the unit only when 8.1's PR loop gate passes. |

### 8.3 OpenClaw — fallback agent execution

| Capability today | Flue recreation | Retire-gate |
|---|---|---|
| Fallback agent runtime: `routers/openclaw.py`, `background_runner.py`, `executor_registry.py`, skills sandbox | Flue `local()` sandbox + subagent execution; OpenClaw is already manual-fallback-only (AGENTS.md), so this is last and lowest-risk. | Flue runs the same background job classes; operator sign-off. |

### 8.4 Cross-cutting seams (re-pointed, not retired)

`agent_sync.py`, `zoe_agent_registry.py`, capability profiles / evolution gates
(`zoe_capability_profile.py`, `zoe_evolution_execution_gate.py`) register *which
agents exist and what they may do* — they get a Flue-agent registration alongside
the existing ones, and legacy rows drop out as each system above retires.

### 8.5 What deletion looks like (when gates pass)

Per CANONICAL: **retire by removing** — each passed gate produces a deletion PR
(module + its tests + its AGENTS.md/docs mentions), not an archive copy. The big
wins land here: `kanban_adapter.py` (~2.3k lines) + `test_kanban_adapter.py`
(~6k lines, the largest file in the repo), `greploop_guard.py` + its ~3.2k-line
test, `hermes_http.py`, `routers/openclaw.py`. None of it moves until its row
above is green.

---

## 9. Research basis

Four parallel deep-dive threads (2026-06-28), each evidence-backed:
1. **Pi** — confirmed Pi is the shared substrate (Zoe = `pi-coding-agent` CLI/RPC;
   Flue = `pi-agent-core` in-process; same 0.80.x). Recommends converging on Pi.
2. **Zoe's real architecture** — the fast path is Python in-process; the only
   out-of-process hop is the brain lane (`run_zoe_core` → `pi` CLI → llama-server);
   identified the env-gated brain seam.
3. **Flue integration** — Flue is designed to be *added alongside* (Node sidecar,
   never embedded), brain via `registerProvider`, tools via `defineTool`/MCP, `db.ts`
   = runtime state only; SoR stays external.
4. **Industry patterns** — front-door gateway + one HTTP escalation + MCP tool bus +
   shared model sidecar; one-owner-per-concern, stateless-reducer agent, graceful
   degradation. Sources: Anthropic *Building Effective Agents*, HumanLayer
   *12-Factor Agents*, Vellum, the MCP spec, llama.cpp server docs.

---

## 10. Status — brain lane delivered (Phases 2–3); cutover (Phase 4) blocked

### Delivered — the merged PR trail

- **#947** — event-loop fork-deadlock fix in zoe-data: forking the hermes CLI on
  the event loop wedged the loop thread (child stuck in `futex_wait` for
  **3.9 days**, every endpoint timing out); CLI spawns now run off-loop with real
  timeouts, and `/api/memories/for-prompt` answers in **18–66 ms**.
- **#915 / #939** — reliable `recall_memory` + all tools (recall fires **97%**),
  hard per-turn tool-iteration cap, real per-tool timeouts.
- **#944** — the parity harness's dry-run preflight made non-mutating.
- **#960** — `reminder_list` direct executor (an empty list no longer surfaces as
  a dispatch failure).
- **#904** — the prod seam: `ZOE_BRAIN_BACKEND=flue` in zoe-data — **default OFF,
  default path byte-identical**.
- **#952** — progressive tool disclosure: **11 → 3 schemas** on a typical turn
  (always-on core `get_time` / `recall_memory` / `activate_abilities`; keyword
  groups in `labs/flue-zoe-brain/src/tools/tool-groups.ts`; wire filter in
  `src/providers/capped-completions.ts`; kill switch
  `ZOE_BRAIN_PROGRESSIVE_TOOLS=false`). Also the operator opt-in systemd unit
  template `scripts/setup/systemd/flue-zoe-brain.service` and the lab runbook
  (`labs/flue-zoe-brain/README.md`).
- **#965** — activator fallback hardening: imperative activate-first /
  never-fabricate doctrine + the `GROUP_SUMMARY` group catalogue in the agent
  instructions; the activator's wire schema pinned to a bare enum by test;
  widened weather/calendar keyword triggers.
- **#971** — Seam-A streaming: the sidecar now emits the prod text-delta +
  `__TOOL__`/`__THINKING__` sentinel stream as NDJSON (content-negotiated), so
  the voice filler (#844) keeps working. Byte contract pinned offline; live
  stream verified on-box.

**E2E-verified through the seam:** real memories returned; `ZOE_BRAIN_BACKEND=flue`
works end-to-end; on pure chat the sidecar is **~2× faster** than the prod
Pi-CLI core.

**On-box verification run 2026-07-03 (LANDING.md checklists, live Gemma):**
- **Sentinel stream (#971): PASS** — `__THINKING__` → `__TOOL__` start/args
  (before result) → result → token-level text deltas → `{"done":true}`, real data.
- **Activator (#965): PASS** — 7/10 trigger-free prompts reached their tool
  (bar ≥50%); all three prior E2E-failing phrasings now fire; **zero fabricated
  tool claims**.
- **Recall regression: PASS** — `recall_memory` fires **31/32 = 96.9%** (bar ≥90%).

### Voice-parity gate — RUN 2026-07-03: **PASS (same-or-better)**

44-prompt corpus from `tests/voice/comprehensive_conversation_test.py` (chat /
social / info / memory-recall biased), each brain scored on reply sanity, tool
correctness, and latency. **Flue 41/44 (93%) vs prod 38/44 (86%); flue median
2.5 s vs prod 5.3 s** (flue faster at every percentile but the LLM-bound max).
Flue won on fresh-user recall honesty (4/4 vs prod's stale-fact assertions) and
emitted zero fabricated tool claims. Identity caveat: prod ran as `guest` (only
client-mintable identity — `parity-gate-user` needs a zoe-auth admin write,
denied), so prod's recall was contaminated by residual guest memories; flue ran
env-bound to `parity-gate-user`. No data mutated (writes dry-run flue-side; the
3 prod guest writes failed at the service). Full record:
`labs/flue-zoe-brain/parity/` scratch + this section.

### Cutover blockers — Phase 4 stays closed until each is cleared

1. **Output token corruption (NEW, from the parity gate).** Reply-start glitches
   — "I don'm not sure", "I don've stored" (×4 across ~88 calls) — surfaced only
   on the Flue side. Both brains share the same llama-server/Gemma, so this is in
   the Flue wire path (pi-ai `openai-completions` streaming detokenization,
   possibly interacting with the canonical E4B **MTP/speculative** decoding), not
   the model. **Voice-audible → a hard blocker.** Not yet root-caused.
2. **In-session context recall regresses vs prod (NEW).** 3 of flue's 4 parity
   failures are one behaviour: it over-defers to the (empty, fresh-user) memory
   store and forgets facts stated 1–3 turns earlier ("What's my name?" after
   "My name is Alex"; "Who am I meeting?" after naming Sarah). Prod, carrying the
   turn history, answered these. The sidecar must weight in-session conversation
   over an empty recall packet.
3. **Tool coverage: 12 → 20 via Waves 1–3; remainder deliberately cut per [`docs/knowledge/flue-cutover-tool-cut-list.md`](../knowledge/flue-cutover-tool-cut-list.md) (signed off 2026-07-03).** The sidecar serves 12 tools; the extension brain registers 18 (11 abilities + 7 Pi built-ins — see the cut-list record for the pinned file:line evidence). The historical "~56" was a projected full-parity target, not the current surface; the 18-item cut list converts the 36-tool difference into deliberate scope (7 Cut, 6 Must-NOT-port, 1 Must-NOT-port-as-is, 1 Defer). Waves 1–3 grow the sidecar 12 → 20 (see the cut-list record §3). The parity corpus barely probed this gap — it stands separately.
4. **Write path unexercised.** Writes have only ever run dry
   (`ZOE_BRAIN_ALLOW_WRITES` defaults OFF); no real write has been verified
   end-to-end. The parity gate could not close this (dry-run flue-side; prod's
   guest writes failed), so it remains open.

**Cleared since last revision:** ~~voice-parity gate unrun~~ (RUN, PASS above);
~~no streaming/sentinels~~ (#971, verified on-box); ~~operator hasn't run the
#965 LANDING checklist~~ (run, PASS above).

### Next action

The gate PASSED same-or-better, so the flip decision is now the operator's — but
blockers 1 (voice-audible token corruption) and 2 (in-session context recall)
are the two that would degrade the live voice experience and should be fixed
before `ZOE_BRAIN_BACKEND=flue` is flipped, even though the aggregate score
favours Flue. Phase 1 (Telegram as a front-door channel) remains in flight
independently — the bot is built (#870); the re-slot through `/api/chat` with a
`channel` tag is still open.
