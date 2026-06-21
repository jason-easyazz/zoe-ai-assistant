# Decision: Build Zoe's Autonomous Harness on Flue (substrate) + `case` (blueprint)

- **Status:** Accepted (decision record — not up for re-litigation)
- **Date:** 2026-06-21
- **Owners:** Zoe core
- **Supersedes:** OpenClaw gateway, Hermes PR-harness (both to be retired onto this single substrate)
- **Type:** Architecture Decision Record (ADR)

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

…with **evidence gates** between stages and a **failure → playbook retrospective** loop so the
harness *learns* from stalls (directly addressing Hermes's "stalls at roadblocks" failure mode).

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

---

## 4. Retirement milestones

Incremental and **each gated by lab proof** (§3.2). Order matters — channels first, then
pipeline, then retire the old harnesses, then unlock self-authoring.

- **(a) Telegram off OpenClaw → onto a Flue channel.** Stand up a Flue channel that calls
  **Zoe's channel-agnostic fast-path core** (Tier-0/1/1.5 untouched, per §3.1). Prove parity in
  the lab.
- **(b) Port a thin PR-harness slice onto Flue durable workflows.** Just
  `scout → implement → verify → PR` — the minimum end-to-end slice, with evidence gates. This
  is the spike that proves the `case` pattern runs on Flue.
- **(c) Retire Hermes** once the Flue workflow covers its responsibilities and passes the
  Samantha tests.
- **(d) Retire OpenClaw** once all live channels are served by Flue channels.
- **(e) Zoe authors her own skills / tools.** Zoe writes her own `SKILL.md` skills and exposes
  tools via **MCP**, closing the self-evolution loop — the actual "Samantha" payoff.

---

## 5. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| **Beta churn** — Flue is `v1.0.0-beta`; APIs may shift. | **Pin versions** (§3.4) and **watch the changelog**; upgrade deliberately behind lab proof. |
| **Second Node runtime** on the Jetson alongside Python services + Gemma + Kokoro. | Use **`local()` sandbox (no daemon)**; budget RAM against model+TTS (§3.3); keep the harness off the hot path so it can be killed without taking Zoe down. |
| **"Flue owns the real-time loop" trap** — drift where the fast-path starts routing through Flue. | Hard architectural boundary (§3.1): Tier-0/1/1.5 never import or call Flue. Flue is Tier-2+harness+channels only. A Flue outage = "channels/harness down," not "Zoe is mute." |
| **License contamination from `case`.** | **License discipline:** study `case`'s public architecture for *ideas only*; **never copy its code** (`case` is unlicensed / all-rights-reserved, issue #19 unanswered). Reproduce the *pattern* as original Flue code. |
| **Pi version drift** between harness and brain. | Align both on one `@earendil-works/pi-*` version (§3.4). |

---

## 6. Open questions / next step

**Next step: a lab spike** — milestone **(b)**: port the thin
`scout → implement → verify → PR` slice onto Flue durable workflows in the lab, and run it
against the **Samantha tests**. That spike resolves the remaining open questions:

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

- **Flue** — GitHub: <https://github.com/withastro/flue> (Apache-2.0, `withastro`, ~6.1k★,
  `v1.0.0-beta`; durable workflows, subagents, `local()` sandbox, MCP + typed tools, 20+
  channels incl. Telegram). Site: <https://flueframework.com>
- **Pi (current scope)** — `@earendil-works/pi-agent-core` / `pi-ai` (Flue's base) and
  `@earendil-works/pi-coding-agent` (Zoe's brain). Pi moved from `@mariozechner/pi-*` to
  `@earendil-works/*` in April 2026.
- **`workos/case`** — public architecture studied for ideas only; **unlicensed**
  (`private: true`, no `license` field → all-rights-reserved), on the old `@mariozechner/pi-*`
  scope (~0.73.x). License request **[workos/case#19](https://github.com/workos/case/issues/19)**
  open and unanswered since May 2026.
