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
- **Now:** 1a (idle-consolidation engine, #771) ✅ + 1b (per-turn user, #775) ✅ **merged** behind the `ZOE_IDLE_CONSOLIDATION_ENABLED` flag → **NEXT: 1c** — lab-prove the live→idle→store→recall loop against the Samantha acceptance bar, then enable the flag in prod (🔨 in progress).

### Agent + tooling readiness (for Jason to work on Zoe remotely)
- **Goal:** Omnigent + Claude Code + Codex + Cursor all set up correctly and **actually using** the repo's tools (Serena, codebase-memory, opensrc, Greptile/greploop, Dox), with a shared command center (this) so ideas/plans don't scatter.
- **Now:** audit ✅ (#774, `docs/agent-setup-audit.md`); opensrc populated ✅ (#773); **Claude Code** wired ✅ (#787 — `CLAUDE.md`+committed `.mcp.json`), **Codex** ✅ (#788 — `.codex/config.toml`), **Cursor** ✅ (#786 — fixed `.cursor/mcp.json`); **Omnigent container code-intel** ✅ (#792 committed the mounts; **applied + verified live 2026-06-26** — recreated the container, serena+codebase-memory+opensrc all resolve in-container, harnesses launch with `--dangerously-skip-permissions` so the project MCP auto-loads); **shared knowledge bundle populated** ✅ (#855 — runtime-topology/voice-pipeline/merge-and-deploy promoted from private memory into `docs/knowledge/` so the whole fleet reads the same ground-truth). **Remaining (🔨):** tool-use enforcement verification across agents (AGENTS.md start-of-task checklist landed; confirm each harness honours it).

### Flue convergence — recreate the harness on Flue, then retire Multica/Hermes/OpenClaw
- **Goal:** one engine (Pi) hosted by Flue owns the brain lane + the autonomous engineering harness; Multica/Hermes/OpenClaw are retired **by recreation** — each capability is rebuilt on Flue, gate-proven, then deleted (never deleted first).
- **Detail / executable plan:** [`docs/architecture/zoe-flue-integration.md`](architecture/zoe-flue-integration.md) — seams (§3), zero-regression gates (§4), phases (§6), and the per-capability **retirement inventory** (§8: what Multica/Hermes/OpenClaw each do today → the Flue recreation → the retire-gate).
- **Now:** Phase 0 (harness on Flue) ✅ #858 (+1.5 ms voice). **Brain lane (phases 1–2 of the brain campaign = detail-doc Phases 2–3) ✅ delivered:** #947 (event-loop fork-deadlock fix — 3.9-day `/api/memories/for-prompt` outage; now 18–66 ms), #915/#939 (recall reliability 97% + turn cap), #944 (non-mutating dry-run preflight), #960 (`reminder_list` direct executor), #904 (`ZOE_BRAIN_BACKEND=flue` seam, default OFF), #952 (progressive tool disclosure 11→3 schemas + systemd unit template + runbook), #965 (activator fallback hardening). E2E-verified through the seam; ~2× faster than the prod core on pure chat. **Still a lab sidecar, NOT canonical** — cutover blocked (detail doc §10: voice-parity gate unrun, tool coverage 12 → 20 via Waves 1–3 (remainder deliberately cut per [`docs/knowledge/flue-cutover-tool-cut-list.md`](knowledge/flue-cutover-tool-cut-list.md), signed off 2026-07-03), no streaming/sentinels so voice filler #844 would go dark, write path unexercised). Phase 1 (Telegram front door) 🔨 — bot built (#870), re-slot through `/api/chat` with a `channel` tag still open. **NEXT:** operator runs the on-box activator measurement (`labs/flue-zoe-brain/LANDING.md`), then the cutover blockers in order; §8.1 Multica recreation (durable ticket workflow) after that, gates before any deletion.

### Touch / Chat UI — "window into Zoe" (foundation-first)
- **Goal:** the touch panel + chat page become a real window into Zoe — type/speak anything, get working interactive components (not prose). DeerFlow-grade, dynamic from basic (calendar) to advanced (Pi builds something new).
- **Order (foundation before features — the panel froze invisible on a hung turn; don't bolt features on a brittle shell):** **1. Resilience → 2. Consolidation → 3. Brain-serves-experience.**
- **Now:** **1. Resilience ✅** (PR #770 merged+deployed+verified-on-panel: turn-stall watchdog, guaranteed UI recovery, + root-cause orb fix — `sky-ambient-clock` was overloaded as a body-flag AND element class, fading the whole UI). Foundations also merged: #766 (Skybridge calendar/list voice+touch loop), #767 (chat tool-activity + interactive-component renderer), #769 (deploy health-check fix). **NEXT: 2. Consolidation** — retire the legacy `dashboard.html`/dual-stack ([`skybridge-cutover-plan.md`](architecture/skybridge-cutover-plan.md), partly done), converge the **4** card producers onto the one validated component contract, tame the z-index/CSS sprawl. Then **3.** Wave A (brain tool calls → `zoe.component` cards). Component contract + `zoe.component` AG-UI event already shipped.

---

## 🗂️ Backlog — pinned & sequenced (from Jason, 2026-06-23)
*Agent/tooling readiness for remote work. Each is a tracked item; do them in order, don't bounce.*

- [x] **1. Audit Omnigent** ✅ — done in #774. Fix ✅ — #792 committed the read-only mounts + container-relative `.mcp.json`; **applied + verified live 2026-06-26** (container recreated; serena+codebase-memory+opensrc resolve in-container).
- [x] **2. Audit + wire Claude Code** ✅ — #774 audit; #787 added `CLAUDE.md`→`@AGENTS.md` and committed the previously-ignored `.mcp.json` (Serena + codebase-memory).
- [x] **3. Audit + wire Codex** ✅ — #774 audit; #788 added repo-local `.codex/config.toml` registering both MCP servers (Codex reads `AGENTS.md` natively).
- [ ] **4. Enforce tool use across ALL agents** 🔨 in progress — AGENTS.md "start-of-task checklist" making code-intel/opensrc/Greptile/DOX non-optional + verifying each entrypoint references the hub.
- [x] **5. Populate opensrc with our reference repos** ✅ — #773 (llama.cpp, Serena, codebase-memory, OKF; confirmed ag-ui, MemPalace; mapped internal sources).
- [ ] **6. Verify the Dox `AGENTS.md` system is referenced by each agent** — folded into #4 (Cursor pointer ✅ #786; Claude ✅ #787; Codex native ✅; Omnigent pending the container fix).
- [x] **7. Stand up this command center** ✅ — `docs/IDEAS.md` + `docs/PLANS.md` + wired into `AGENTS.md` (landed via the VISION train).
- [ ] **8. Research a richer ideas-board surface** (see IDEAS.md) — 📝 not started.

---

## ✅ Done
- **Security & reliability hardening wave (2026-06-29 → 2026-07-02, ~30 PRs #909–#944):** WebSocket Origin validation (CSWSH, #933) + push-WS lifecycle/fanout hardening (#917/#922), panel authz holes closed (#921/#873), SSRF + argv-exec guards (#909), bounded outbound fetch bodies (#931), auth session invalidation after password rotation (#938) + query-string PII log leak (#926), skill-name path-traversal sandbox (#930), nginx proxy headers/CSP (#925), memory edit data-loss/recall re-embed/unbounded growth fixes (#934), Alembic-managed consolidation state (#929), timezone-correct dates across MCP/frontend (#914/#919/#923), exact integer-cents money (#924), deploy readiness gating (#940), Multica outage observability (#932), vacuous-test strengthening (#913).
- **Gated zoe-data deploy helper (2026-06-29):** added a host-run one-liner wrapper that checks the live tree, memory headroom, explicit restart confirmation, and post-deploy health while delegating pull/restart/rollback to the blessed deploy script.
- **WebSocket lifecycle hardening (2026-06-29):** fixed streaming push WS cleanup leaks from malformed `catchup:` and half-open idle sockets; all `main.py` push endpoints now share guarded catchup parsing, idle receive deadlines, and `finally` broadcaster cleanup.
- **Config/deploy health hardening (2026-06-28):** kept `/health` as backward-compatible liveness and added `/readyz` for real Gemma 4 E4B, Moonshine, and TTS readiness; removed faster-whisper/whisper warm-resurrection paths; moved dependency health out of deploy liveness, tightened auth DB health dependency, and raised nginx API upload size.
- **Repo cleanup + lock-in (2026-06-24):** purged `docs/archive` (84MB→1.9MB) + a 69MB untracked backup; added `docs/CANONICAL.md` (single live/dead authority) + `test_canonical_invariants.py` (CI fails if a rock is swapped) + wired CANONICAL into AGENTS.md READ-FIRST; fixed manifest E2B→E4B drift. (#777)
- **Module retirement (2026-06-24):** retired `orbit`/`agent-zero`/`jag-board`/`questionable-decisions` (code + nginx/UI/compose wiring); `jag-board` + `questionable-decisions` preserved (private repos + local archive); `zoe-music` kept, pinned for Music Assistant migration. (#779)
- **Agent-readiness wave (2026-06-24):** opensrc repos (#773), agent audit (#774), Claude Code (#787) / Codex (#788) / Cursor (#786) all wired to load the rules + code-intel MCP.
- **Samantha memory 1a + 1b (2026-06-24):** idle-consolidation engine (#771) + per-turn user (#775), behind a flag.
- **Voice + memory hardening (2026-06-23):** capture/first-audio/recall fixes, write-quality gate, junk cleanup, identity-from-auth. (See PR history; memory `project-mempalace-deep-dive`.)
- **Command center stood up** (this doc + IDEAS.md + VISION.md).
