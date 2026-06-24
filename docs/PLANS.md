---
type: plans-index
audience: human-first (Jason) + all agents
---
# Zoe Plans 📋

**What we're actually building, in plain English, with status.** One line of goal +
a link to the detail + where it's up to. Mark ✅ when done so nothing lingers half-finished.

**Status key:** 📝 planned · 🔨 active · ✅ done · 🗄️ parked

> **For agents:** this is the human-readable index. Detailed/executable plans live in
> `docs/architecture/*.md` (and agent memory). When you finish a step, update the
> status here AND in the detailed plan. New ideas start in [IDEAS.md](IDEAS.md).

---

## 🔨 Active

### Samantha-grade memory
- **Goal:** local, fast, **no-nightly** companion memory — tell it in the morning, reference it in the afternoon; recalls the emotional thread; feels like Samantha.
- **Detail / executable plan:** [`docs/architecture/zoe-memory-samantha-buildplan.md`](architecture/zoe-memory-samantha-buildplan.md) (has the NEXT ACTION pointer + checklist).
- **Now:** Increment 1a (idle-consolidation engine) merged behind a flag → **NEXT: 1b** (persist the per-turn user so consolidation knows whose memory to write).

### Agent + tooling readiness (for Jason to work on Zoe remotely)
- **Goal:** Omnigent + Claude Code + Codex all set up correctly and **actually using** the repo's tools (Serena, codebase-memory, opensrc, Greptile/greploop, Dox), with a shared command center (this) so ideas/plans don't scatter.
- **Detail:** see Backlog below. The tools mostly *exist*; the gap is **adoption + verification**, not setup-from-scratch.

### Touch / Chat UI — "window into Zoe" (foundation-first)
- **Goal:** the touch panel + chat page become a real window into Zoe — type/speak anything, get working interactive components (not prose). DeerFlow-grade, dynamic from basic (calendar) to advanced (Pi builds something new).
- **Order (foundation before features — the panel froze invisible on a hung turn; don't bolt features on a brittle shell):** **1. Resilience → 2. Consolidation → 3. Brain-serves-experience.**
- **Now:** **1. Resilience ✅** (PR #770 merged+deployed+verified-on-panel: turn-stall watchdog, guaranteed UI recovery, + root-cause orb fix — `sky-ambient-clock` was overloaded as a body-flag AND element class, fading the whole UI). Foundations also merged: #766 (Skybridge calendar/list voice+touch loop), #767 (chat tool-activity + interactive-component renderer), #769 (deploy health-check fix). **NEXT: 2. Consolidation** — retire the legacy `dashboard.html`/dual-stack ([`skybridge-cutover-plan.md`](architecture/skybridge-cutover-plan.md), partly done), converge the **4** card producers onto the one validated component contract, tame the z-index/CSS sprawl. Then **3.** Wave A (brain tool calls → `zoe.component` cards). Component contract + `zoe.component` AG-UI event already shipped.

---

## 🗂️ Backlog — pinned & sequenced (from Jason, 2026-06-23)
*Agent/tooling readiness for remote work. Each is a tracked item; do them in order, don't bounce.*

- [ ] **1. Audit Omnigent** — is it set up correctly for remote coding-on-Zoe? (`zoe-omnigent` container is up.)
- [ ] **2. Audit Claude Code** — does it load repo rules? (CLAUDE.md → AGENTS.md; `.mcp.json` wires Serena + codebase-memory — confirm they load + are used.)
- [ ] **3. Audit Codex** — does it read `AGENTS.md` (its native convention) + get the tools?
- [ ] **4. Enforce tool use across ALL agents** — Serena, codebase-memory, opensrc, Greptile/greploop, Dox keep getting **skipped** even though configured. Make AGENTS.md rules salient/enforced + verify each agent entrypoint references them. (Even Claude in the app skips them — noted in memory.)
- [ ] **5. Populate opensrc with our reference repos** — has ag-ui-protocol, chroma, fastapi, livekit, browser-use…; **missing**: Skybridge, llama.cpp, Serena, codebase-memory, Dox, Pi agent, hermes, openclaw, Open Knowledge Framework. Add them so agents read real source, not guesses.
- [ ] **6. Verify the Dox `AGENTS.md` system is actually referenced by each agent** (Q7) + complete the repo rules so they're consistent and loaded.
- [x] **7. Stand up this command center** — `docs/IDEAS.md` (ideas board) + `docs/PLANS.md` (this) + wire into `AGENTS.md`. ✅
- [ ] **8. Research a richer ideas-board surface** (see IDEAS.md) — maybe an existing project fits.

---

## ✅ Done
- **Voice + memory hardening (2026-06-23):** capture/first-audio/recall fixes, write-quality gate, junk cleanup, identity-from-auth. (See PR history; memory `project-mempalace-deep-dive`.)
- **Command center stood up** (this doc + IDEAS.md).
