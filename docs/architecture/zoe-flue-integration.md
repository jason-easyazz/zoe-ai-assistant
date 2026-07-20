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
- **Phase 1 — Telegram as a front-door channel. ✅ DONE (live-hardened 2026-07).**
  Transport (long-poll) → zoe-data `/api/chat` with `channel="telegram"` → `fast_tiers`;
  identity via signed `/start` deep-link account linking (verified Telegram sender id →
  Zoe user, no shared guest), `/new` session reset (#1246), health watchdog for the
  silent poll-loop death mode (#1233).
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

**Usage gate — apply BEFORE the recreation gate (added 2026-07-20).** The rule
above exists to avoid *losing* capability, but you cannot lose what was never
invoked. Applied literally to the 101 skills across both discovery directories it
becomes a quarter of porting work for dead weight. Ask "was it used?" first, and
recreate only what fired.

**The measurement has now been done — both answers are zero-ish:**

| | Ever invoked? | Evidence |
|---|---|---|
| **OpenClaw**, 31 ours | **Never.** | 18,433 tool calls, 0 skill reads; loader reproduces a 14-skill catalog with no `zoe-*` (§8.3) |
| **Hermes**, 10 ours | **Yes — then stopped.** | `~/.hermes/skills/.usage.json`: zoe-engineering 206, zoe-board 167, github-greptile-loop 79. All last fired **2026-05-27 → 2026-06-18**. |

Note the Hermes ledger *does* exist — an earlier revision of this section claimed
no telemetry existed anywhere, which was true of the database and `~/.zoe-logs/`
but wrong about `~/.hermes/`. Its flatline is real signal, not a dead writer:
six other skills recorded through 2026-07-17 (negative control). The stop has a
cause — the Multica kanban feed died 2026-06-18 09:29, and skill loads are
bumped through `skill_bundles.py`, so no dispatch means no bump. Input stopped,
not the instrument.

**Zoe's own application logging was blacked out** for all of this (root logger
had no handler — fixed separately), which is why these answers required database
and session-corpus forensics rather than a log query.

**Operator sign-off, 2026-07-20 (Jason) — NARROWED, read the scope line.**

> **SCOPE: this sign-off authorises retiring the SKILLS ONLY.** It does **not**
> authorise removing `hermes-agent.service`, the Hermes gateway, or
> `~/.hermes/kanban.db`. Those are the queue and executor of a working
> autonomous harness (§8.1, §8.2) and are gated separately on a proven
> replacement. An earlier revision of this block read as blanket authorisation
> for "full retirement of Hermes and OpenClaw"; that wording predates the
> discovery that the board and workers live inside Hermes, and it is withdrawn.

Authorised without the recreation gates being green: deletion of the 101
Hermes/OpenClaw **skill files** and the dead discovery machinery. Recorded here
because §8 otherwise forbids acting without green gates. Both skill sets are
backed up under
[`docs/knowledge/operator-skills/`](../knowledge/operator-skills/index.md), so
the deletion is git-recoverable.

**The risk originally attached to this sign-off has been measured away** for the
skills: there is no capability to regress, because none of them was ever
executing (§8.3). It has **not** been measured away for the runtime — the live
Hermes couplings in `background_runner`, `kanban_adapter`, `routers/system`, the
Multica skill pins, and above all the gateway/board pair, remain in scope of the
normal gate.

### 8.1 Multica — board-driven engineering orchestration

> ## ⛔ STOP — this section describes retiring a system that WORKS
>
> **Corrected 2026-07-20.** Multica **achieved true 100% hands-off
> idea→merged-PR autonomy** on 2026-06-17 (e2e8 / ZOE-5834 → PR **#682**
> actually merged, `merge_sha 2d3edaa9`, zero operator action on the pipeline).
> Twelve harness PRs are merged and verified present in `main`:
> `#592 #597` dispatch keystone · `#601` PR_URL handoff · `#607` verify mandate ·
> `#632` deterministic verify · `#637` board/journal reconcile · `#672 #677`
> deterministic review · `#679 #681` authoritative closeout · `#685` zombie
> reaper · `#694` no-op converge.
>
> **It is PAUSED, not broken.** Kill switch `~/.zoe/multica_dispatch_paused` is
> present (2026-06-18 20:29). The board reads 132 done / 128 blocked /
> **0 dispatchable**.
>
> **Do not read the board's statistics as a health metric.** The success rate
> declines over time (100% on 05-31 → 6% on 06-11 → ~45% by 06-18) and not one
> of the 128 blocked tickets records a `blocker_reason`. That looks like decay
> and is the opposite: those were **deliberate test tickets run to find failure
> modes**, and each mode found became one of the twelve PRs above. The curve
> measures the system being BUILT. A session on 2026-07-20 misread exactly this
> and spent hours arguing to delete a working harness.
>
> **The design worth preserving:** every flaky-agent dependency was taken off
> the critical path. The agent only *implements*; the **harness** runs the PR's
> focused tests, approves review from objective CI state, and performs the merge
> itself. `#681` goes further — closeout ignores an agent's *claimed* success and
> requires a real `merge_sha`. Reuse this shape for any future autonomy work:
> **agents propose, the harness verifies.**

> **Migration scope (2026-07-20):**
> [`multica-executor-migration.md`](multica-executor-migration.md) — decision of
> record that Multica is KEPT (it is a third-party product running on Zoe with
> its own DB, not a Hermes component), and that what moves is *execution*, not
> the board. Phases, non-negotiables, and the open decisions live there.
>
> Executable packet: [`multica-retirement-gates.md`](multica-retirement-gates.md)
> — code-verified inventory, per-capability proof gates, rollback, sequencing,
> Forbidden list. The table below is the summary; the packet is normative.

| Capability today (code) | Flue recreation | Retire-gate |
|---|---|---|
| Ticket/board orchestration: `executors/kanban_adapter.py`, `multica_ticket_contract.py`, `multica_autopilot_sync.py`, `pipeline_handoff.py` | A **durable Flue workflow** per ticket: bound agent delegates scout → implement → verify → openPR to subagents (the Phase-0 harness pattern, proven in #858). Ticket state lives in Flue run durability; zoe-data stays SoR for anything user-visible. | Flue processes ≥5 real tickets end-to-end (branch → PR → merged) with no stalls; operator sign-off. |
| Worktree lifecycle: `worktree_bootstrap.py` (create/remove/prune, squash-merge detection) | Reused as-is first (it's harness-agnostic Python callable via a tool), ported into the Flue workflow's exec helpers later. | Flue workflow owns worktree create/cleanup for its own tickets. |
| Greptile PR loop: `greploop_guard.py`, `greptile_client.py` (packet-only fix loops, thread resolution) | A Flue **workflow step/subagent** wrapping the same packet-generation logic — port the module, don't rewrite the mechanics. | A Flue-driven greploop takes ≥3 PRs from open → threads-resolved → merged. |

### 8.2 Hermes — engineering/browser delegation

> ## ⛔ RETIRING HERMES REMOVES THE AUTONOMOUS HARNESS'S QUEUE AND EXECUTOR
>
> **Added 2026-07-20 — this section previously missed it entirely.** The skills
> inventory below is accurate and low-stakes (none of them was ever invoked; see
> §8.3). The load-bearing dependency is elsewhere, and it is not a skill:
>
> | Piece | Where it lives | Survives Hermes retirement? |
> |---|---|---|
> | The **board** | `~/.hermes/kanban.db` — Hermes-owned SQLite, no remote, unbacked | **NO** |
> | The **workers** | Hermes gateway `kanban_watchers.py`, `dispatch_in_gateway: true` | **NO** |
> | The harness logic | `kanban_adapter.py`, `pipeline_store.py`, deterministic verify/review/closeout — all in `zoe-data` | yes |
>
> An earlier operator note is explicit that `dispatch_in_gateway` must stay
> `true` — *"NEVER disable it; it's the executor."* Zoe's `kanban_adapter` only
> **creates** tasks via the kanban CLI; it never spawns a worker.
>
> **So retiring Hermes leaves the intelligence with no queue and nothing to run
> it.** §8.1's harness (which achieved 100% hands-off autonomy — see the stop
> block there) would be intact but inert.
>
> **Sequencing requirement:** build and prove a replacement board + executor
> **before** retiring Hermes, not after. Zoe's Postgres is the natural home for
> the board (it is already the system of record and is not being retired); Flue's
> own docs say its sqlite holds run durability only, never business data. The
> agent roles are already spiked in `labs/flue-harness-spike/` (`scout`,
> `verifier`, `sandbox: local()`); the durable state layer is what's missing.
>
> **Whatever replaces the board must record WHY on every transition.** Multica's
> board had a `blocker_reason` field and populated it **zero times across 128
> blocked tickets**, which is why its failure modes had to be found by hand one
> at a time. Zoe's application logging was also dead for this entire period
> (fixed 2026-07-20, PR #1468) — so the June debugging was conducted blind.

| Capability today | Flue recreation | Retire-gate |
|---|---|---|
| Engineering delegation: `hermes_http.py`, `~/.hermes/skills` — **68 skills, not the 4 this row used to name.** Only **10 carry `author: Zoe`** (`zoe-engineering`, `github-greptile-loop`, `source-code-context`, `code-structure-cleanup`, `agentic-engineering-workflow`, `grep-loop-review-workflow`, `zoe-board`, `zoe-cloakbrowser`, `zoe-status-refresh`, `zoe-graphify`); the other 58 are the stock `NousResearch/Hermes-Agent` pack and are re-pullable, not recreation targets. | Flue **agents + subagents** with `defineTool`/MCP over the same zoe-data endpoints. Recreate only skills that are actually invoked — see the usage-gate note below. | Per-skill: the Flue agent completes the same task class the skill handled. |
| Browser work: `browser_broker.py` + `zoe-cloakbrowser` skill | CloakBrowser tools exposed to Flue via MCP (Seam B). | Flue agent completes a real browser task through the broker. |
| Knowledge refresh: `zoe-status-refresh` skill | A scheduled Flue workflow writing OKF records under `docs/knowledge/` (records only — never AGENTS.md contracts). | One full refresh cycle produced by Flue and lint-clean. |
| `hermes-agent.service` — **ACTIVE, not paused.** This row claimed "PAUSED since 2026-06-21: enabled but inactive"; verified 2026-07-20 as `active` and running (~156 MB). A month of nobody noticing it running is itself evidence of low engagement, but do not plan against the stale claim. | Superseded by the Flue runtime unit. | **Gate corrected 2026-07-20 — 8.1's PR-loop gate is NOT sufficient.** That gate proves Flue can process tickets; it says nothing about where the queue and workers live. Removing this unit ALSO removes `~/.hermes/kanban.db` (the board) and the gateway `kanban_watchers` (the executor). Required, in addition to the PR-loop gate: (a) a durable board exists outside Hermes and holds real ticket state, (b) an executor runs phase workers without the Hermes gateway, (c) both proven on ≥3 real tickets end-to-end, (d) operator sign-off naming this row specifically. |

### 8.3 OpenClaw — fallback agent execution

| Capability today | Flue recreation | Retire-gate |
|---|---|---|
| Fallback agent runtime: `routers/openclaw.py`, `background_runner.py`, `executor_registry.py`, skills sandbox | Flue `local()` sandbox + subagent execution. | Flue runs the same background job classes; operator sign-off. |
| **Skills — ours, but never loaded. Verified twice, 2026-07-20.** Of 34 entries in `~/.openclaw/workspace/skills/`, **31 are ours** (20 absent from stock `openclaw@2026.5.12`, 11 stock-but-modified, 3 untouched) — `briefing`, `family-data`, `grocery-meal`, `ha-patterns`, `home-assistant`, `journal`, `memory-consolidation`, `proactive`, `touch-panel`, `transactions`, `weather`, `zoe-ui`, `dynamic-widgets`. Compare Hermes: 10 of 68. **But none has ever been invoked.** Session-corpus forensics: 18,433 tool calls, 17,042 `read`s, **17,040 of them `HEARTBEAT.md`, zero of any skill path**. Independently confirmed by running OpenClaw's shipped loader against the live config — it builds a 14-skill catalog with **zero `zoe-*`** entries. Cause is a **config bug, not a design gap**: the loader *does* scan `workspaceDir/skills` (merged last, highest precedence), but `workspaceDir` resolves from `agents.list[0].workspace` = `~/.openclaw/agents/main`, whose `skills/` does not exist. Point it at the right dir and 12 load immediately. Three builders additionally fail `symlink-escape` (no `skills.load` key configured); `zoe-verify`'s target is missing; `memory-consolidation` has lowercase `skill.md`. **Also: 12 workspace skills silently shadow bundled ones by name — the agent loads the STOCK version, so the catalog looks correct while the customization is inert.** Corroborated behaviourally: `journal_entries`, `transactions`, `open_loops`, `background_tasks`, `dashboard_layouts` all have **0 rows**. | Nothing to preserve — these are unproven intent, not live capability. Rebuild on Flue (which supports the same `SKILL.md` format natively) rather than repairing a platform being retired. | None. Delete freely; the ideas are worth revisiting, the wiring is not. |

### 8.4 Cross-cutting seams (re-pointed, not retired)

`agent_sync.py`, `zoe_agent_registry.py`, capability profiles / evolution gates
(`zoe_capability_profile.py`, `zoe_evolution_execution_gate.py`) register *which
agents exist and what they may do* — they get a Flue-agent registration alongside
the existing ones, and legacy rows drop out as each system above retires.

### 8.5 What deletion looks like (when gates pass)

Per CANONICAL: **retire by removing** — each passed gate produces a deletion PR
(module + its tests + its AGENTS.md/docs mentions), not an archive copy. None of
it moves until its row above is green.

**Corrected 2026-07-20 — this list previously led with the wrong targets.** It
named `kanban_adapter.py` (~2.3k lines) + `test_kanban_adapter.py` (~6k, the
largest file in the repo) as "the big wins". Those are **the working autonomous
harness** (§8.1), and line count is not a reason to delete something that works.
Likewise `greploop_guard.py`: it is Hermes-free (verified — `ESCALATE_HERMES` in
it is a state label with **0 occurrences across 293 state files**; it routes
through `ZOE_CHEAP_PR_AGENT_CMD`), so it is not part of a Hermes retirement at
all, and it is actively used.

Deletion candidates that are genuinely safe, in order of confidence:

| Target | Basis |
|---|---|
| `skill_discovery.py`, `skills_watcher.py` | **DONE** (PR #1471) — zero consumers, no behaviour change |
| The 58 stock Hermes skills + 3 stock OpenClaw skills | third-party, re-pullable upstream |
| `hermes_http.py` | **NOT yet deletable.** PR #1473 cut it from 13 importers to **6** by moving `zoe_repo_root` out. All six are live runtime files. **Five are module-level** — `routers/voice_tts.py:24`, `routers/system.py:23`, `background_runner.py:21`, `executors/kanban_adapter.py:32`, `proactive/triggers/openclaw_trigger.py:21` — so deleting the helper first breaks them at **import time** on the next deploy. The sixth, `main.py:938`, is a lazy in-function import, so it fails later, at lifespan startup, which is worse to diagnose. Order: rewire all six callers, then the helper falls out on its own. (Counted against the tree 2026-07-20; an earlier revision of this row said "4", repeating a figure instead of checking.) |
| `routers/openclaw.py`, `openclaw_manager.py`, ACP path | **NOT dead** — 2,338 `openclaw_run_state` rows through 2026-07-16. Needs a decision, not a deletion PR. |
| `kanban_adapter.py`, `pipeline_store.py`, harness | **DO NOT DELETE** — the working harness. See §8.1. |

Measure twice here: three separate claims in this document have already been
found backwards (skills "live" when never invoked; `hermes-agent` "paused" when
active; Multica "failing" when it had succeeded and been parked). Verify
execution, not presence, before deleting anything.

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

## 10. Status — brain lane delivered; CUT OVER to production 2026-07-03 (fix-after in progress)

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

### Voice-parity gate — RUN 2026-07-03: **latency clear win; quality ~parity (see confound)**

44-prompt corpus from `tests/voice/comprehensive_conversation_test.py` (chat /
social / info / memory-recall biased), each brain scored on reply sanity, tool
correctness, and latency.

- **Latency (clean, unconfounded): decisive Flue win** — median **2.5 s vs prod
  5.3 s**, flue faster at every percentile but the LLM-bound max. This is the
  headline result and it does not depend on identity.
- **Quality (confounded — read carefully): roughly at parity, edge to prod.**
  The raw aggregate (flue 41/44 = 93% vs prod 38/44 = 86%) OVERSTATES flue,
  because the two brains ran under different identities: prod ran as `guest`
  (the only client-mintable identity — `parity-gate-user` needs a zoe-auth admin
  write, which was denied), so prod's memory was contaminated by residual guest
  facts and it "failed" 4 fresh-user recall prompts by asserting stale data,
  while flue (env-bound to `parity-gate-user`, empty store) "passed" them by
  honestly saying it knew nothing. Those 4 points are an artifact of the setup,
  not flue being better. Removing the 4 identity-decided prompts: **flue 37/40 =
  92.5% vs prod 38/40 = 95%** — a fair comparison puts prod marginally ahead on
  quality. Flue emitted zero fabricated tool claims either way. No data mutated
  (writes dry-run flue-side; the 3 prod guest writes failed at the service).

**Honest read:** the gate does NOT show flue beating prod on answer quality — it
shows flue *competitive* on quality (within a couple of points, prod slightly
ahead once the confound is removed) and *clearly faster*. A clean quality
re-run needs a real authenticated test user on both sides — now unblocked:
`scripts/maintenance/provision_parity_test_user.py` (operator-run) mints
`parity-gate-user` through AuthService with demo-user guardrails; log in via
`POST :8002/api/auth/login` and pass the session as `X-Session-ID` on the prod
side, same user through the Seam-A identity envelope on the flue side. Full
record: `labs/flue-zoe-brain/parity/` scratch.

### Cutover — DONE 2026-07-03 (operator-authorized), fix-after in progress

`ZOE_BRAIN_BACKEND=flue` is **live in production**: zoe-data routes brain turns
to the sidecar, which now runs as a supervised systemd user service
(`flue-zoe-brain.service`, token auth, virtual sandbox — matches Flue's
`ecosystem/deploy/node` guidance). Routing proven via a marker session id in the
sidecar store. Rollback is one env removal + a zoe-data restart (~15 s).

**Blockers cleared before/at cutover:**
- ~~Output token corruption~~ — root-caused to MTP draft-acceptance
  (`--spec-type draft-mtp` on the shared llama-server) exposed by streaming;
  **fixed by matching prod's sampling temperature 0.5** (#991): 0/60 vs ~3.5% at
  0.7. (Non-streaming was tried and honestly shelved — it did not eliminate it.)
- ~~In-session context recall regression~~ — the recall doctrine was too absolute;
  **#988** adds in-session precedence (3/3 scenarios recovered, recall held 97%).
- ~~voice-parity gate unrun~~ (RUN — latency win, quality ~parity above);
  ~~no streaming/sentinels~~ (#971, verified on-box); ~~coding tools leaking into
  the voice brain~~ (#989 strips pi/Flue built-ins).

**Fix-after (post-cutover):**
1. ~~**Write intents with no direct executor** returned `ok:false` through the
   seam~~ ✅ **CLOSED 2026-07-06 — real root cause one level deeper.** The
   `_run_mcporter`-None failures were a **stale rotated `POSTGRES_URL` baked
   into `~/.mcporter/mcporter.json`** pre-empting `bootstrap_runtime_env()`'s
   canonical `.env` load (a pre-set env key wins by design): every spawned
   `mcp_server.py` failed DB auth, limped on, and crashed mid-call. Fixes:
   baked credential **removed** from mcporter.json (the subprocess now
   self-loads the current URL — rotation-proof); `run_stdio_server` **exits 1
   loudly** on pool-init failure; `_run_mcporter` migrated to
   `async_subprocess.run_to_completion` (off-loop fork, the #947 outage class).
   Residual fallback intents (`journal_prompt`/`journal_streak`/`note_search`/
   `people_search`/`transaction_*`) verified working. Direct executors added
   along the way (#993 lists; calendar/note/reminder/journal/people) remain the
   first-choice paths.
2. ~~**Multi-user identity.**~~ ✅ **DONE (#998/#1000/#1001, live).** The shipped
   mechanism is NOT the `AsyncLocalStorage` fix this doc originally proposed —
   ALS was proven broken through the `?wait=result` path (the agent fiber does
   not inherit the route's ALS store). The working design keys identity by the
   turn's `AbortSignal` in a `WeakMap`, carried by a trusted ` zoe-uid:` message
   envelope from the seam; see `labs/flue-zoe-brain/src/request-identity.ts`.
3. **Quality is marginally below the old core** (92.5% vs 95%, confound-corrected)
   — the deliberate trade for the ~2× latency win; watch it in daily use. A clean
   re-run still needs an authenticated test user (zoe-auth provisioning).
4. **Tool coverage: 12 → 20 via Waves 1–3; remainder deliberately cut per [`docs/knowledge/flue-cutover-tool-cut-list.md`](../knowledge/flue-cutover-tool-cut-list.md) (signed off 2026-07-03).** The parity corpus barely probed this gap — it stands separately.
5. ~~**Unbounded session history wedged long-lived sessions permanently** (found
   2026-07-07)~~ ✅ **FIXED (#1138).** Durable sessions grew without bound and
   nothing between the store and llama-server ever shrank the assembled prompt;
   once system prompt + tool schemas + history crossed the 8192-token context,
   EVERY subsequent turn on that session 500'd forever (`400 request (8288
   tokens) exceeds the available context size (8192 tokens)` — observed live on
   the harness replay session at 198 stored entries; any long-lived Telegram or
   voice session hits the same wall). Fix: **prompt-fit history windowing** at
   the capped-provider wire seam (`labs/flue-zoe-brain/src/context-window.ts`,
   applied in `applyPolicies`): drop the OLDEST whole user-turn blocks until the
   estimated prompt (~4 chars/token, Flue's own heuristic) fits
   `ZOE_BRAIN_CONTEXT_WINDOW` (default 8192, the llama-server rock's
   `--ctx-size`) minus `ZOE_BRAIN_REPLY_RESERVE` (default 1536). Guarantees:
   soul + doctrines are NEVER touched (separate Context field); the newest turn
   and its ` zoe-uid:` identity envelope always survive intact; blocks drop
   whole, so toolCall/toolResult pairs never split; the durable store keeps
   full history — only the wire prompt is windowed. Windowed-out facts stay
   recoverable via `recall_memory` (the per-turn extractor stored them), which
   is the design reason windowing beats summarization here. Flue's native
   compaction (`@flue/runtime` `defineAgent({compaction})` +
   `registerProvider({contextWindow})`) was evaluated first and deliberately
   NOT enabled: its summarizer runs through the same 8k-window model, so an
   already-oversized session overflows the summarization call itself (the
   exact wedge), and it adds nondeterministic multi-second Gemma summarization
   stalls to the latency-gated voice path. It remains available via config if
   that trade ever flips.

### Next action

Fix-after #1 and #2 are closed. Phase 1 (Telegram front door) is ✅ done and
live-hardened (§6). Next: §8.1 Multica recreation (gates before any deletion).
