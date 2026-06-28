# Zoe ⊕ Flue — gaining Flue's abilities with zero regression

> **North star.** Keep everything Zoe already is, and *gain* everything Flue
> offers, with **no regression** to the existing setup (above all, voice speed).
> This is the map every Flue-related piece (Telegram, the harness, future
> channels) slots into — so we stop building ad-hoc and start building to a plan.

Status: **proposed / living plan.** Supersedes the scope of
[`zoe-harness-on-flue.md`](zoe-harness-on-flue.md) (that decision is now *one phase*
of this, see §5). Honors the rocks in [`../CANONICAL.md`](../CANONICAL.md).

---

## 1. The principle: one system, two runtimes — integrate, don't rewrite

zoe-data is **Python**; Flue is **TypeScript/Node**. "Merge" therefore does **not**
mean one codebase or a rewrite — it means **one Zoe system composed of two
runtimes, tightly integrated over HTTP + MCP.** Zoe keeps her brain and her speed;
Flue adds the abilities she lacks. The integration is **additive** — Flue sits
*beside* the existing setup, never *in front of* the latency-critical path. That
additivity is precisely what makes zero-regression achievable.

We do **not** rewrite zoe-data onto Flue, and we do **not** route voice through
Flue. (A future, optional migration of the brain core itself onto Flue is §5
Phase 5 — deliberate and lab-proven, never assumed.)

---

## 2. Who owns what

| Stays in **zoe-data** (the brain & the rock) | Flue **adds** (the abilities) |
|---|---|
| Voice fast-path (STT → routers → brain → TTS) — **untouched** | First-class **channels** (Telegram, Slack, …) with verified ingress |
| `fast_tiers` **single channel-agnostic core** (Tier-0→1→1.5, `channel` tag → profile) | The autonomous **engineering harness** (scout→implement→verify→openPR) |
| `intent_router` / `semantic_router` / `expert_dispatch` | **Durable agentic workflows** + subagents |
| **MemPalace** memory | **MCP / tool host**, skills, scheduling |
| Gemma brain (llama-server `:11434`) | **Observability** (OpenTelemetry) |
| Existing web/touch UI + API | |

zoe-data remains **the brain and the single channel core**. Flue is the **channel
+ agent + harness layer** around it.

---

## 3. The seams — exact integration contracts

Four well-defined seams; everything crosses one of them.

### Seam A — Channels *in*, to the one core
A Flue channel (e.g. Telegram) handles only transport: verify/receive → hand the
turn to Zoe's **single core** → send the reply back out. The core is shared, so a
channel is **not a parallel brain** — it is another doorway into `fast_tiers`.

- Contract: the turn enters the core as `(text, user_id, session_id, channel="<name>")`.
  `fast_tiers.resolve(...)` runs the deterministic tiers (its `channel` tag selects
  the per-channel profile), and on `None` the turn falls through to the Gemma brain
  lane — identical to web/voice today.
- **Phase-1 task:** expose that channel-tagged entry to external channels (extend
  `/api/chat` to accept a `channel` field, or add a thin channel entry), so a Flue
  channel feeds the core *as the named channel* rather than generically.

### Seam B — Agentic work *out*, to Flue
zoe-data offloads long-running / agentic / engineering work to Flue and gets a
receipt/result back. Example: a PR task → the Flue harness; a durable multi-step
job → a Flue workflow (`invoke`/`dispatch`). zoe-data never blocks the user turn on
these.

### Seam C — The shared brain (Gemma `:11434`)
Both runtimes can use the local Gemma brain (OpenAI-compatible). **Voice always has
priority.** Flue agentic/text work either runs on a **cloud model** (as the harness
does on OpenRouter) or yields to voice — so the shared GPU is never a voice
roadblock. (Validated, not assumed — see §4.)

### Seam D — Shared tools & memory via MCP
Zoe's capabilities and memory are exposed as an **MCP server**; Flue agents
`connectMcpServer(...)` to gain them — so a Flue agent *is* Zoe-capable instead of a
generic model. The reverse (Flue tools available to zoe-data) uses the same bus.
One tool/memory layer, both runtimes.

---

## 4. Zero-regression guarantees (and how we prove them)

- **Voice fast-path is independent of Flue** — the rock. Flue is never inserted into
  the STT→router→brain→TTS path. *Flue down ⇒ channels/harness down, never "Zoe
  can't answer."*
- **Latency gate:** the [`#735` probe](../../scripts/maintenance/zoe_latency_probe.py)
  must show **no voice-latency regression** for any change touching a shared
  resource, before it goes live. (Harness already passed: **+1.5 ms** under load.)
- **Memory budget:** Flue is a Node service (~300 MB) on a tight 16 GB box — sized
  and monitored; it is the one real cost and stays bounded.
- **Brain-GPU policy:** voice priority; agentic/text work uses cloud or yields (Seam C).
- **Lab-prove before prod**, every phase (the Samantha bar). Per-phase operator
  sign-off for anything prod-facing or any retirement.

---

## 5. Migration order — phased, retire-by-removing

Each phase is independently shippable, additive, and gated by §4.

- **Phase 0 — Harness on Flue. ✅ DONE.** scout→implement→verify→openPR proven on a
  real issue; safety gate works; +1.5 ms voice. (PR #858.)
- **Phase 1 — Telegram as a real channel into the one core.** Flue Telegram
  transport (long-poll, allow-listed) feeds `fast_tiers` via Seam A with
  `channel="telegram"`. Retire Hermes's Telegram. *(In progress — re-slotting the
  current Flue Telegram app from a generic `/api/chat` bridge to a tagged channel.)*
- **Phase 2 — MCP seam (D).** Expose Zoe's tools + memory over MCP so Flue agents
  are Zoe-capable. Unlocks "Zoe, on any channel, with all her abilities."
- **Phase 3 — Engineering on Flue (B).** zoe-data triggers the Flue harness for real
  tasks; begin retiring the Hermes/Multica engineering loops.
- **Phase 4 — More channels + agentic workflows on Flue.** Retire OpenClaw.
- **Phase 5 — *(optional, later, deliberate)* migrate the `fast_tiers` core itself
  onto Flue.** Only if lab-proven **at least as fast** as today; all channels move
  together. Not required to gain Flue's abilities — most value lands in Phases 1–4.

---

## 6. What we explicitly do NOT do

- No big-bang rewrite of zoe-data.
- No routing voice through Flue until Phase 5 is lab-proven faster-or-equal.
- No parallel brain — channels feed the **one** core (`fast_tiers` + brain lane).
- No hoarding — retire superseded systems by **removing** them (git keeps history).

---

## 7. Status & next action

- **Now:** Phase 0 done (harness, #858). A Flue Telegram bot is running as a generic
  `/api/chat` bridge (proof it works) — to be re-slotted as the Phase-1 tagged channel.
- **Next action:** Phase 1 — add the `channel`-tagged entry (Seam A) and point the
  Telegram transport at it as `channel="telegram"`, so Telegram is genuinely "another
  channel into the single core," then retire Hermes Telegram.
