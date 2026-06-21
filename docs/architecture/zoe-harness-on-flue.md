# Decision: Build Zoe's Autonomous Harness on Flue (substrate) + `case` (blueprint)

- **Status:** **ACCEPTED — FIRM.** This is no longer "leaning"; it is the chosen path. Not up for re-litigation absent new evidence from the Phase 0 lab spike.
- **Date:** 2026-06-21
- **Owners:** Zoe core
- **Supersedes:** OpenClaw gateway, Hermes PR-harness (both to be retired onto this single substrate)
- **Type:** Architecture Decision Record (ADR)

> **Decision (one paragraph).** Build Zoe's autonomous, self-evolving harness — the
> **"Samantha"** capability — on **[Flue](https://github.com/withastro/flue)** (Apache-2.0,
> built on `@earendil-works/pi-*`, the *same Pi lineage* as Zoe's brain
> `@earendil-works/pi-coding-agent`), reproducing the **pipeline *pattern*** of
> **`workos/case`** (`scout → implementer → verifier → reviewer → closer → retrospective`,
> with evidence gates, fingerprint-based early-abort, and a retrospective → guardrail loop).
> Flue is adopted because it **ships the durable-state / sandbox / channel / observability
> infrastructure that OpenClaw and Hermes failed trying to hand-build** — and because, being
> Apache-2.0 and a thin framework layer over `pi-agent-core`, it is **Pi-with-batteries, not a
> foreign second runtime**, so adopting it *consolidates* Zoe onto one Pi-based framework
> rather than adding scaffolding. `case` is **unlicensed (all-rights-reserved)** and is used as
> a **study-only blueprint — its code is never copied.** The whole rollout is **phased and
> lab-gated**: nothing reaches the Jetson production path until it passes the Samantha tests.

---

## 1. Context & goal

Zoe has accumulated **two autonomous-build efforts that were built but never fully worked**:

- **OpenClaw** — the channel/gateway layer (notably Telegram) that was meant to be Zoe's
  external front door. It runs local Gemma, but the gateway scaffolding never converged.
- **Hermes** — the autonomous PR-processing harness in `services/zoe-data`. Its recurring
  failure mode is tickets stalling at roadblocks; the pipeline never reliably ran end to end.

Both efforts independently re-implement the same primitives — durable multi-step pipelines,
subagents, tool/skill hosting, channel ingress — on top of Pi. Maintaining two half-built
harnesses is the actual blocker.

**Goal:** converge OpenClaw + Hermes into **one Pi-based harness running on Zoe**, on a
substrate that already provides the durable-workflow / subagent / sandbox / channel plumbing,
so the team builds *Zoe's behavior* instead of re-building harness infrastructure. This
directly advances the **"Samantha" self-evolution goal**: a Zoe that can autonomously build,
test, and extend herself (author her own skills and tools) under hard lab-proof guardrails.

---

## 2. Decision

Adopt a **hybrid**:

> **Substrate = [Flue](https://github.com/withastro/flue)** (the runtime/harness we run on)
> **Blueprint = `workos/case`'s pipeline *pattern*** (the application design we reproduce — ideas only, never code)

We build Zoe's autonomous self-building harness **on Flue** and re-implement **`case`'s
pipeline shape** as Flue durable workflows + subagents. We then **retire OpenClaw and Hermes**
onto this single substrate.

### Why Flue as the substrate

- **License is clean.** Flue is **Apache-2.0**, published by the Astro team
  (`withastro`), ~**6.1k★**, active, currently **`v1.0.0-beta`**. We can build on it and
  depend on it without legal ambiguity.
- **Same Pi lineage as Zoe's brain.** Flue is built on
  **`@earendil-works/pi-agent-core` / `pi-ai`** — the *current* Pi scope, the same scope as
  Zoe's brain (`@earendil-works/pi-coding-agent`). Pi moved from `@mariozechner/pi-*` to the
  Earendil scope in **April 2026**; aligning the harness and the brain on one `@earendil-works/pi-*`
  runtime keeps a single Pi version surface.
- **It already provides everything the two dead harnesses were re-inventing:**
  durable workflows, subagents, a **`local()` sandbox (no Docker / no container daemon)**,
  **MCP + typed tools**, and **20+ channels including Telegram**.

### Why `case` as the *blueprint only* (and never as code)

- **`workos/case` is legally OFF-LIMITS for copying.** The repo has **no LICENSE file**,
  `package.json` shows **`"private": true`** and **no `license` field** (`license: null`), and
  the open-source **license request ([workos/case#19](https://github.com/workos/case/issues/19))**
  has been **ignored since May 2026**. Absent a license, the code is **all-rights-reserved**.
  → We may **study its public architecture for ideas, but MUST NOT copy its code.**
- **It is also a looser lineage fit.** `case` is still on the **old `@mariozechner/pi-*` scope**
  (≈`0.73.x`), from *before* Pi moved to Earendil. Flue, on the current Earendil scope, is the
  closer runtime match to Zoe's brain.

We take from `case` only the **pipeline *shape*** to reproduce as Flue workflows/subagents:

```
scout → implementer → verifier → reviewer → closer → retrospective
```

…with **evidence gates** between stages, **fingerprint-based early-abort** (hash the
issue/work signature so a stage bails fast on already-seen or no-op work instead of looping),
and a **retrospective → guardrail loop** (every failure produces a written playbook entry that
becomes a *standing guardrail* on later runs). Together these directly address Hermes's "stalls
at roadblocks" failure mode: the harness aborts cheap mistakes early and *learns* from the
expensive ones.

---

## 2a. Why buy-and-fork beats build (the rationale that won)

This is the crux of the decision. The two prior efforts **failed for the same reason**, and
Flue's existence removes that reason.

- **OpenClaw and Hermes both failed because they hand-built harness infrastructure** — durable
  state machines, retry/resume, sandboxing, channel ingress — *from scratch*, on top of bare
  Pi. That undifferentiated plumbing is exactly where they stalled (Hermes never ran a ticket
  end-to-end; OpenClaw's gateway never converged). **We will not repeat that mistake.** Hand-
  building harness infra is the trap, not the goal.
- **Flue ships that infrastructure as one converged Apache-2.0 monorepo:** durable workflows,
  subagents, a **`local()` sandbox (no Docker / no container daemon)**, **MCP + typed tools**,
  **first-party channels (Telegram and ~20 others)**, and **OpenTelemetry** instrumentation.
  Everything the dead harnesses re-invented, already built and maintained.
- **Flue is Pi-with-batteries, NOT a foreign / second runtime.** It is a *framework layer over
  `pi-agent-core`* — the very runtime Zoe's brain already executes on. Adopting Flue therefore
  **consolidates Zoe onto a single Pi-based framework**; it *is* the convergence goal, not a
  new pile of scaffolding to maintain. We are not adding a runtime; we are standardizing on the
  one we already have, with the harness primitives filled in.
- **Open source de-risks everything else.** Because Flue is **Apache-2.0**, the usual buy-vs-
  build worries lose their teeth: we can **pin a commit, fork it, vendor it, or modify it to
  fit**. Beta-churn, project abandonment, and vendor lock-in stop being existential — **worst
  case, we own the code.** That license freedom is what turns "depend on someone else's beta"
  from a risk into a managed one.

### Dissent considered and overruled

A credible alternative was raised: **build the harness directly on bare `pi-agent-core`**,
skipping Flue's framework layer. Technically this is *tighter* — fewer dependencies, no beta
surface, full control of every primitive. **It was considered and rejected.** Building directly
on bare Pi **re-introduces the exact hand-built-infra burden that sank Hermes** (we'd be writing
our own durable workflows, sandbox, and channel layer again). For a team that **needs to ship
Zoe's behavior, not harness plumbing**, the framework-over-Pi path wins. If Flue ever proves a
bad fit, the Apache-2.0 license means we can fall back to bare Pi — but we do not *start* there.

---

## 3. Non-negotiable guardrails

These are constraints on the decision, not open questions.

### 3.1 The real-time fast-path core stays independent of Flue

Zoe's sub-second answer path **must NOT depend on or route through Flue**:

| Tier | Component | Role |
|------|-----------|------|
| **Tier-0** | `intent_router.detect_intent` | regex fast path, <10ms, no LLM |
| **Tier-1** | `semantic_router` | embedding-based domain routing |
| **Tier-1.5** | `expert_dispatch` | direct domain-expert executors |

Flue is **only ever**:
- the **Tier-2 reasoning brain**,
- the **autonomous-harness substrate**, and
- the **channel / tool host**.

**A Flue outage must degrade to "channels/harness down," never "Zoe can't answer."** If Flue
is down, Tier-0 → Tier-1 → Tier-1.5 still serve the user. Flue is never on the critical path
for a real-time response.

### 3.2 Lab-proof before prod (the Samantha guardrail)

**Nothing migrates to the Jetson production path until it passes the "Samantha tests" in the
lab.** This is the standing hard guardrail — no prod migration on hope. Each retirement
milestone below is independently gated by lab proof.

### 3.3 Jetson resource reality

Production target is an **Orin NX (16GB)** already running **Gemma + Kokoro TTS**. The model
and TTS are the RAM pressure, **not Flue**. Therefore:

- Use Flue's **`local()` sandbox** — **no container daemon** on the Jetson.
- Treat the model + Kokoro (Kokoro's ~2.3GB included) as the binding resource budget; the Node
  harness is comparatively cheap but is **still a second runtime** (see Risks).

### 3.4 Version discipline

- **Pin Flue beta versions.** It is `v1.0.0-beta`; do not float.
- **Align Pi versions** so the harness and the brain share **one `@earendil-works/pi-*` runtime**
  (currently **~0.79**). The harness and brain must not drift onto different Pi versions.

### 3.5 Durability: persist self-authored skills straight to git

**Flue's `local()` sandbox files are not durable, and subagents restart fresh.** Anything Zoe
authors about herself — skills, tools, playbook/guardrail entries — must therefore be written
**straight to git**, not left in sandbox scratch space.

- When Zoe writes a new `SKILL.md` / MCP tool, the harness **commits it to the repo** as the
  durable record. The sandbox is a workspace, never the source of truth.
- **Keep subagent steps idempotent.** Because subagents restart-fresh and a workflow may resume,
  any step that writes self-authored artifacts must be safe to re-run (check-before-write, no
  duplicate commits, stable file paths). Self-evolution that only lives in a sandbox is lost on
  restart — it does not count.

---

## 4. Best practices to adopt regardless of substrate

These are settled patterns from the agent-systems literature that we adopt **independent of
Flue** — they constrain *how* we build, not *what* we build on. Cited briefly for traceability.

- **Tiered memory (Letta / MemGPT).** Split memory into **core** (always-in-context persona +
  key facts), **recall** (recent conversation), and **archival** (vector-searched long-term).
  This is the structure Zoe's layered-but-dormant memory should realign to.
- **Tested-skill library, check-before-write (Voyager / SAGE).** A self-authored skill enters
  the library **only after it passes a test**; before writing a "new" skill, **check whether one
  already exists** (dedup). No untested skill becomes permanent. Pairs with §3.5's
  persist-to-git + idempotency.
- **Append-only event log.** Every harness step (scout/implement/verify/…) appends an immutable
  event. State is reconstructed by replaying the log — the basis for durability, audit, and the
  retrospective loop.
- **A real sandbox for agent-authored code.** Flue's `local()` is fine for the harness, but code
  *Zoe writes and runs* should execute under a **true isolation boundary (bubblewrap / gVisor)**,
  not just process-level separation.
- **Proposal-only self-modification.** Zoe's self-edits are **proposals** (a PR / a diff a gate
  must pass), never direct mutation of running config or her own brain. The evidence gates and
  human/CI review stay in the loop.
- **OpenTelemetry from day one.** Instrument the harness with **OTel** at the start (Flue ships
  this), so stalls and regressions are observable rather than guessed at.

---

## 5. Integration & retirement plan

Concrete, **phased, and each phase independently lab-gated** (§3.2). Order matters: prove the
substrate on a dev box first, then channels, then pipeline, then retire the old harnesses, then
unlock self-authoring. **Nothing in any phase touches the Jetson production path until it passes
the Samantha tests.**

### Phase 0 — Lab spike on a DEV BOX (PR #737, `labs/flue-harness-spike/`)

Run the spike on a **development box, NOT the Jetson** — we are validating Flue's API surface,
not its Orin footprint yet.

- **Verify Flue's real API signatures** (durable workflows, subagents, `local()`, channels) at
  the pinned beta version against actual code, not docs.
- **Run a `scout → implement → verify → openPR` loop on a real issue** against a **local
  llama.cpp** model end-to-end.
- **Acceptance = the Samantha tests.** If the spike can't close the loop on a real issue, the
  decision returns for re-evaluation (the only condition under which this ADR reopens).

### Phase 1 — Telegram channel onto Flue

- Stand up the **Flue `@flue/telegram` channel** and **forward inbound messages to Zoe's
  channel-agnostic fast-path core** (Tier-0/1/1.5, untouched per §3.1).
- **Prerequisite (must land first):** that **channel-agnostic core does not yet exist as a
  reusable unit** — the fast path is currently **voice-only, living in `voice_tts.py`, not in
  `chat.py`**. It must be **extracted into a channel-agnostic core** before any Flue channel can
  call it. This extraction is a hard dependency of Phase 1, tracked separately.
- Prove Telegram-via-Flue reaches parity with the existing path in the lab.

### Phase 2 — Port a thin pipeline slice onto Flue durable workflows

- Port a **thin `scout → … → PR` slice** (the `case` pattern, minimal end-to-end) onto Flue
  durable workflows with evidence gates and fingerprint early-abort.
- **Benchmark the Orin footprint** here — this is where Node-harness-on-Jetson RAM headroom
  (alongside Gemma + Kokoro) gets measured for real.

### Phase 3 — Retire Hermes

- Once the Flue workflow covers Hermes's **PR-harness role** and passes the Samantha tests,
  **retire Hermes.** Its responsibilities move onto Flue.

### Phase 4 — Retire OpenClaw

- Once all live channels are served by Flue channels, **retire OpenClaw.** Its
  **Telegram / agent-gateway role moves onto Flue.**

### Phase 5 — Self-evolution: Zoe authors her own skills / MCP tools

- Zoe writes her own **`SKILL.md` skills** and exposes **MCP tools**, persisted straight to git
  (§3.5) and admitted only via the tested-skill-library discipline (§4). This closes the
  self-evolution loop — the actual **"Samantha" payoff.**

---

## 6. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| **Beta churn** — Flue is `v1.0.0-beta`; APIs may shift. | **Pin versions** (§3.4); Apache-2.0 means we can **fork / vendor / pin a commit** if upstream churns or stalls (§2a). Watch the changelog; upgrade deliberately behind lab proof. |
| **The fast-path trap** — drift where Zoe's real-time loop starts routing through Flue. | Hard architectural boundary (§3.1): Tier-0/1/1.5 **never import or call Flue**. Flue is Tier-2 + harness + channels only. A Flue outage = "channels/harness down," **never** "Zoe can't answer." |
| **`case` license discipline** — accidental code contamination. | Study `case`'s public architecture for **ideas only**; **never copy its code** (`case` is unlicensed / all-rights-reserved, issue #19 unanswered). Reproduce the *pattern* as original Flue code. |
| **Orin resource budget** — a second Node runtime on a 16GB Orin alongside Python services + Gemma + Kokoro. | The **model + Kokoro (~2.3GB) are the real RAM pressure, not Flue.** Use Flue's **`local()` sandbox (no container daemon)**; benchmark the footprint in **Phase 2**; keep the harness off the hot path so it can be killed without taking Zoe down. |
| **Pi version drift** between harness and brain. | Align both on one `@earendil-works/pi-*` version (§3.4). |
| **Durability loss** — self-authored skills vanish on sandbox/subagent restart. | **Persist straight to git** and keep write-steps **idempotent** (§3.5); admit skills only via the tested-skill-library discipline (§4). |

---

## 7. Open questions / next step

**Next step: the Phase 0 lab spike** (§5, **PR #737 / `labs/flue-harness-spike/`**) — on a **dev
box**, verify Flue's real API signatures and run a `scout → implement → verify → openPR` loop on a
real issue against local llama.cpp, judged by the **Samantha tests**. That spike resolves the
remaining open questions:

- Exact `@earendil-works/pi-*` version the Flue beta pins vs. Zoe's brain (~0.79) — confirm a
  single shared version, or document the bridge if they differ.
- Real Jetson RAM headroom for the Node harness alongside Gemma + Kokoro under load.
- The concrete shape of **evidence gates** and the **failure → playbook retrospective** loop on
  Flue (the part most directly aimed at Hermes's "stalls at roadblocks" pain).
- Whether Flue's channel abstraction cleanly fronts Zoe's channel-agnostic fast-path core
  without leaking into the real-time path (§3.1).

Nothing past the lab spike reaches the Jetson production path until it passes the Samantha
tests.

---

## Sources

- **Flue** — GitHub: <https://github.com/withastro/flue> (**Apache-2.0**, see
  [`LICENSE`](https://github.com/withastro/flue/blob/main/LICENSE); `withastro`, ~6.1k★,
  `v1.0.0-beta`; durable workflows, subagents, `local()` sandbox, MCP + typed tools, 20+
  channels incl. Telegram, OpenTelemetry). Site: <https://flueframework.com>
- **Pi (current scope)** — `@earendil-works/pi-agent-core` / `pi-ai` (Flue's base) and
  `@earendil-works/pi-coding-agent` (Zoe's brain). Pi moved from `@mariozechner/pi-*` to
  `@earendil-works/*` in April 2026.
- **`workos/case`** — public architecture studied for ideas only; **unlicensed**
  (`private: true`, no `license` field → all-rights-reserved), on the old `@mariozechner/pi-*`
  scope (~0.73.x). License request **[workos/case#19](https://github.com/workos/case/issues/19)**
  open and unanswered since May 2026.
