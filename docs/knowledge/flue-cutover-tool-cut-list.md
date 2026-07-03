---
type: decision-record
title: Flue Cutover — Tool Surface Cut List (signed off 2026-07-03)
description: Decision of record for the Flue brain cutover — the explicit 18-item cut list for the "~56 tools" parity target, signed off by Jason 2026-07-03. Establishes that the sidecar moves from 12 → 20 tools via Waves 1–3 and that the remaining gap is deliberate scope, not a parity deficit. Cross-linked from docs/PLANS.md and docs/architecture/zoe-flue-integration.md §10.
tags: [decision-record, flue, cutover, tools, parity, governance]
timestamp: 2026-07-03T00:00:00Z
---

# Flue Cutover — Tool Surface Cut List (decision of record)

**Status:** APPROVED by Jason 2026-07-03 (blanket approval, all 18 items with the
recommended dispositions). This is the governance record referenced from
[`docs/PLANS.md`](../PLANS.md) and
[`docs/architecture/zoe-flue-integration.md` §10](../architecture/zoe-flue-integration.md).
It converts the "~56 tools" parity goal from a moving design estimate into a
signed-off scope boundary.

Related: [Zoe tool stack](zoe-tool-stack.md) (what each legacy agent is and how
they converge), [Multica autopilot loop contracts](autopilots/index.md) (the
loop-engineering governance pattern this record follows).

## 1. Pinned reality — what the extension brain actually exposes today

The "~56" number that appears throughout
[`zoe-flue-integration.md`](../architecture/zoe-flue-integration.md) and
[`PLANS.md`](../PLANS.md) is the **projected** full-parity target, not the
current registration. The extension brain (zoe-core, the production Pi-CLI
brain — `docs/CANONICAL.md` "Canonical live systems") currently registers
**18 tools** in total:

| Layer | Count | Tools | Evidence |
|---|---|---|---|
| **zoe-core abilities** (registered as Pi tools via the auto-discovery loop) | **11** | `info`, `calendar`, `lists`, `reminders`, `timers`, `media`, `home`, `notes`, `journal`, `people`, `research` | `services/zoe-core/extensions/abilities.ts:78-110` (auto-discovers `abilities/*.ts`); 11 `CapabilityEntry` objects across the 7 files in `services/zoe-core/abilities/` |
| **Pi built-in tools** (always registered on the agent) | **7** | `read`, `bash`, `edit`, `write`, `grep`, `find`, `ls` | `node_modules/@earendil-works/pi-coding-agent/dist/core/tools/index.js:13` — `allToolNames = new Set([...])`; the prod `rpc_command` in `services/zoe-data/zoe_core_client.py:74-91` does NOT pass `--no-builtin-tools`, so all 7 are registered |
| **TOTAL REGISTERED** | **18** | | |

Per-turn, progressive disclosure (`services/zoe-core/extensions/abilities.ts:121-146`,
`setActiveTools()`) reduces the active set to 11 abilities + 4 default-active
built-ins (`read`/`bash`/`edit`/`write` per
`node_modules/@earendil-works/pi-coding-agent/dist/core/sdk.js:131`).

The "56" appears in five places, all marked with `~` (approximate) and
referring to a projected target, not the current surface:

| Source | Quote |
|---|---|
| `services/zoe-core/extensions/abilities.ts:5` | *"so a ~2B local model isn't drowned in 56 tools"* |
| [`docs/PLANS.md`](../PLANS.md) (Flue convergence bullet) | *"12 vs ~56 tools"* |
| [`docs/architecture/zoe-flue-integration.md`](../architecture/zoe-flue-integration.md) §10 | *"Tool coverage: 12 vs ~56. The sidecar serves 12 tools; the extension brain exposes ~56."* |
| `docs/architecture/zoe-flue-integration.md:165` | *"Full parity with the extension brain's ~56 tools is still open (Phase-4 blocker, §10)"* |
| `docs/strategy/zoe-samantha-harness-plan.md:45` | *"a ~2B model isn't drowned in 56 tools"* |

The 56 includes the 27 legacy `zoe_agent.py` tools
(`services/zoe-data/zoe_agent.py:402-840` — 26 names in `_TOOLS` + `_HERMES_TOOL`
`escalate_to_hermes`), the 11 zoe-core abilities, the 7 Pi built-ins, and
projected additions for Multica board ops (per `zoe-flue-integration.md` §8.1),
Hermes engineering ops (§8.2), OpenClaw browser (§8.3), touch-panel, proactive
scheduling, `report_issue`, and AG-UI card components (`show_map` /
`show_chart` / `show_action_menu`). No one source enumerates exactly which 56
tool names make up the target — it is a rough design estimate.

The **sidecar** (`labs/flue-zoe-brain/src/tools/zoe-tools.ts`, auto-discovered
by `agents/zoe.ts`) currently serves **12 tools** (11 capability + 1
`activate_abilities` activator for progressive disclosure) — all targeting
zoe-data's `_DISPATCHABLE_INTENTS` allowlist at
`services/zoe-data/routers/system.py:2384-2395` (27 intents).

## 2. The 18-item cut list (signed off 2026-07-03)

Each item: **what** is being cut, **disposition** (cut / must-not-port /
defer), **justification** (one paragraph), **what replaces it**, and
**retire-gate** (where applicable).

| # | Item | Disposition | Justification | Replaced by | Retire-gate |
|---|---|---|---|---|---|
| 1 | `mempalace_search` (legacy `zoe_agent.py`) | **Cut** | The new brain uses the `memory.ts` extension that injects a memory packet into the system prompt every turn (`services/zoe-core/extensions/memory.ts:31-43`); the model no longer "calls" memory — it reads it. The sidecar's `recall_memory` is the explicit-per-turn equivalent. | `memory.ts` packet-injection + `recall_memory` (sidecar) | Implicit — no port needed; verify on voice-corpus replay that the packet covers the use-cases `mempalace_search` previously served. |
| 2 | `mempalace_add` (legacy) | **Cut** | Replaced by `_ingest_or_supersede` in `services/zoe-data/expert_dispatch.py:280-329`; voice memory-store goes through the expert-dispatch path, not a model-callable tool. The new model never needs to *decide* to add a memory — the dispatch path auto-captures the conversational turn. | `expert_dispatch._ingest_or_supersede` | Verify on voice-corpus that end-of-turn memory writes still fire for the corpus's "remember that …" turns. |
| 3 | `memory_update` (legacy) | **Cut** | Same as 2 — superseded by the expert-dispatch write path. | `expert_dispatch._ingest_or_supersede` | Same as 2. |
| 4 | `proactive_schedule` (legacy) | **Cut** | Push-notification scheduling is a UI/notification concern, not a per-turn voice/chat capability. Voice answers once and is done; the notification surface (touch / Telegram / chat) is the place for "schedule a push" affordances. Not in `_DISPATCHABLE_INTENTS` today (`routers/system.py:2384-2395`). | UI notification settings (touch/chat surface) | n/a — out of voice/chat-brain scope. |
| 5 | `report_issue` (legacy) | **Cut** | Bug-report tool — never high-value for voice; chat-only. Not in `_DISPATCHABLE_INTENTS` today. The chat surface has a "report issue" button that opens the issue tracker directly. | Chat "report issue" button → issue tracker | n/a — out of voice/chat-brain scope. |
| 6 | `show_map` / `show_chart` / `show_action_menu` (legacy) | **Cut** | These are AG-UI sentinel components (touch/chat surface), not `defineTool`-style LLM-callable tools. They belong on the chat wire-protocol surface, not the brain's per-turn toolbox. The voice filler path can't render them anyway (`docs/PLANS.md` blocker #3 — `voice filler #844 would go dark`). | Chat / touch UI card components | n/a — out of brain-tool scope. |
| 7 | `escalate_to_openclaw` (legacy) | **Must-NOT-port** | OpenClaw is being retired by Flue convergence ([`zoe-flue-integration.md`](../architecture/zoe-flue-integration.md) §8.3, `docs/PLANS.md` Flue convergence bullet). A sidecar that exposes it is **building on a dying system**. OpenClaw is manual-fallback-only per root `AGENTS.md` §"Hermes-First Delegation". | Flue convergence — OpenClaw is being deleted (§8.3 retire-gate: *"Flue runs the same background job classes; operator sign-off"*) | OpenClaw unit removed only after §8.3 gate passes; no sidecar tool to remove in the interim. |
| 8 | `escalate_to_hermes` (legacy) | **Must-NOT-port** | Hermes is being retired by Flue convergence ([`zoe-flue-integration.md`](../architecture/zoe-flue-integration.md) §8.2, `docs/PLANS.md`). The sidecar's `research` ability covers the only delegation seam that is still needed: the `delegate-sync` HTTP endpoint that itself is being re-pointed to Flue. | `research` ability (`services/zoe-core/abilities/delegate.ts`) → `/api/system/delegate-sync` → (re-pointed) Flue agent | Hermes unit removed only after §8.2 gate passes; the `delegate-sync` endpoint is the single seam. |
| 9 | `list_openclaw_plugins` / `list_openclaw_skills` / `setup_telegram` (legacy) | **Cut** | OpenClaw-bound UI affordances, not capabilities. Belong on the OpenClaw app/UI surface, which is being retired (item 7). | Removed with OpenClaw (§8.3 gate) | n/a. |
| 10 | `web_search` / `deep_web_research` as direct tools (legacy) | **Cut** | Replaced by the `research` ability (`services/zoe-core/abilities/delegate.ts`) which routes through `/api/system/delegate-sync` to Hermes (and eventually to Flue per item 8). Adding separate `web_search` tools is double-bookkeeping and bypasses the validation layer in `delegate-sync`. | `research` ability | Verify that the legacy "do a deep search" voice command still works through `research`. |
| 11 | `open_touch_page` (legacy) | **Cut** | Replaced by `panel_*` intents in `services/zoe-data/intent_router.py:635-643`, which are dispatched from the conversational turn without a model-callable tool. The intent router is the correct seam (no need for the model to *call* a tool to open a panel). | `panel_*` intents (`intent_router.py:635-643`) | n/a. |
| 12 | `setup_telegram` (legacy) | **Cut** | Single-use OAuth flow; belongs on a settings page, not in the brain's per-turn toolbox. (Not in `_DISPATCHABLE_INTENTS`.) | Settings page (touch/chat UI) | n/a. |
| 13 | `ha_control` (legacy raw `entity_id`) | **Must-NOT-port-as-is** | The legacy `zoe_agent.py:436-450` exposes a single `ha_control` with raw `entity_id` taken from model args. This is a security regression — model-chosen `entity_id` is not validated against the user's authorised entity list. The new `home` ability (`services/zoe-core/abilities/media.ts:91-129`) routes through the `smart_home` intent (`intent_router.py:1080-1086`) which is **validated** against the user's authorised home. Porting `ha_control` 1:1 is forbidden; the new ability is the replacement (Wave 2). | `home` ability → `smart_home` intent (validated) | n/a — the new path supersedes it before any port. |
| 14 | Any future Multica-board / kanban / ticket tools | **Must-NOT-port** | Multica is being retired by recreation on Flue first ([`zoe-flue-integration.md`](../architecture/zoe-flue-integration.md) §8.1, `docs/PLANS.md`). Its tools belong on Flue's autonomous engineering agent, **NOT** on the user-facing conversational brain. Porting them here violates seam A (the conversational brain stays a per-turn reducer; engineering orchestration is a Flue workflow). | Flue durable ticket workflow (§8.1) | §8.1 gate: *"Flue processes ≥5 real tickets end-to-end (branch → PR → merged) with no stalls; operator sign-off"* — no port until that gate passes. |
| 15 | Any direct Hermes-skill invocation (bypassing `delegate-sync`) | **Must-NOT-port** | Same as 8. The only allowed seam is the `research` ability via `/api/system/delegate-sync` (which itself is re-pointed to Flue). Direct Hermes skill invocation is forbidden by the Flue convergence direction; no new code path may add it. | `research` ability → `delegate-sync` | §8.2 gate (per-skill: Flue agent completes the same task class the skill handled). |
| 16 | Browser / CloakBrowser tools in the conversational brain | **Must-NOT-port** | The conversational pathway can't hold a multi-step browser session. Browser is a Hermes / OpenClaw / Flue-engineering surface ([`zoe-flue-integration.md`](../architecture/zoe-flue-integration.md) §8.2 *"Browser work: browser_broker.py + zoe-cloakbrowser skill — CloakBrowser tools exposed to Flue via MCP (Seam B)"*). Adding them to the conversational brain would couple a per-turn reducer to a multi-step surface. | Flue agent + CloakBrowser MCP tools (Seam B) | §8.2 gate: *"Flue agent completes a real browser task through the broker"*. |
| 17 | Voice TTS / LiveKit direct-control tools | **Must-NOT-port** | Voice is a rocks-protected path ([`docs/CANONICAL.md`](../CANONICAL.md) — the locked-in truth). A tool that streams audio or drives the TTS pipeline is out-of-scope for the brain's per-turn toolbox. The TTS path is the **voice** system, not a tool the model calls. | n/a (out of scope) | Rocks; canonical invariants test in `services/zoe-data/tests/test_canonical_invariants.py` enforces it. |
| 18 | `transactions` (transaction_create / transaction_summary) | **Defer** | Intent exists at `services/zoe-data/intent_router.py:1017-1056`; module is in `get_enabled_modules` (`routers/system.py:78`); but no legacy tool exposed it and no zoe-core ability implements it. Nice-to-have, not daily-driver. Defer to post-cutover; the intent catalog is the right place for it in the meantime. | Defer; revisit after cutover lands | n/a — not blocking cutover. |

**Summary of dispositions:** 7 **Cut**, 6 **Must-NOT-port**, 1 **Must-NOT-port-as-is**, 1 **Defer**. The 7 "Cut" items are deliberately excluded from the parity target; the 6 "Must-NOT-port" + 1 "Must-NOT-port-as-is" + 1 "Defer" items fall outside the conversational brain's scope by design.

## 3. Revised parity target — 12 → 20 via Waves 1–3

With items 1–18 cut from the parity target, the sidecar's target is **20
tools** (not 56). The path is three porting waves, each one PR:

### Wave 1 — "Daily-driver" (1 PR, 5 new tools)
Close the highest-value voice-command gaps:
`list_remove` • `note_search` • `journal_create` + `journal_prompt` +
`journal_streak` • `people_create` + `people_relate` + `people_search` •
extend `shopping_list_add` → `add_to_list` with `list_type` arg.
**100% thin HTTP wrapper over existing `_DISPATCHABLE_INTENTS`. Zero new
zoe-data surface.** Reuses the `dispatchIntent` helper at
`labs/flue-zoe-brain/src/tools/zoe-tools.ts:101-141`. Each new tool gets
its group in `tool-groups.ts` for progressive disclosure.

### Wave 2 — "Music & Home" (1 PR, 2 tools)
One `media` tool (action=play|control|set_music_volume|system_volume|setup)
and one `home` tool (action=on|off|dim|brighten, optional room). Action
dispatch matches the legacy `media.ts` ability pattern exactly.
**Zero new zoe-data surface** (music + `smart_home` intents already exist).
**Note for `music_play`:** sidecar is read-ONLY on the music player;
`set_volume` for system TTS must NOT touch the music player (mirrors
`services/zoe-core/abilities/media.ts:74-89` discipline).

### Wave 3 — "Memory write path" (1 PR, 1 new tool, the ONE zoe-data touch)
Add `remember_fact` to the sidecar — a model-callable memory-write tool
covering the gap the legacy `mempalace_add` / `memory_update` left.
**This is the one B-wave item that needs zoe-data surface work:** add a
new intent `memory_store` to the `_DISPATCHABLE_INTENTS` allowlist
(`routers/system.py:2384-2395`) — 3-line change, the fulfillment path via
`MemoryService.ingest` already exists in `expert_dispatch.py`.

### Revised count

- **Sidecar current (Wave 0, shipped #952):** 12 tools
  (11 capability + 1 `activate_abilities` activator)
- **After Wave 1:** 17 tools (16 capability + 1 activator)
- **After Wave 2:** 19 tools (18 capability + 1 activator)
- **After Wave 3:** **20 tools** (19 capability + 1 activator)
- **Items 1–18 in this record:** the 36-tool difference between 20 and 56
  is now **deliberate scope**, not a parity deficit.

### Verification gate before cutover

`docs/PLANS.md` blocker #1 (voice-parity gate) is the gate that flips
`_USE_ZOE_CORE=true` for real users. The verification for Waves 1–3 is the
existing voice-corpus replay (`scripts/maintenance/voice_regression_probe.py`),
the tool-call reliability harness (`labs/flue-zoe-brain/parity/tool_reliability.py`),
and the recall reliability check (`parity/recall_reliability.py`, current 97%,
target ≥90%). All three are mandatory per root `AGENTS.md` voice-change rules.

## 4. Cross-references

- [`docs/PLANS.md`](../PLANS.md) — the Flue convergence plan; the Flue
  convergence bullet and the blocker-call-site have been re-pointed to this
  record (see change log below).
- [`docs/architecture/zoe-flue-integration.md`](../architecture/zoe-flue-integration.md) —
  the brain-campaign detail doc; §10 blocker #2 ("Tool coverage: 12 vs ~56")
  has been re-pointed to this record.
- [`docs/CANONICAL.md`](../CANONICAL.md) — the locked-in truth. **Not
  modified by this record.** Flue remains a lab sidecar, not canonical.
  The cut list is governance over a design scope, not over the rocks.
- [`docs/knowledge/zoe-tool-stack.md`](zoe-tool-stack.md) — the Zoe tool
  stack reference (what Multica, Hermes, OpenClaw, Pi, MemPalace, etc. are
  and how they converge).
- [`docs/knowledge/autopilots/index.md`](autopilots/index.md) — the OKF
  governance pattern this record follows (Loop-Engineering contract shape:
  Job / Allowed / Forbidden / Output / Evaluation).
- [`services/zoe-data/expert_dispatch.py`](../../services/zoe-data/expert_dispatch.py) —
  the conversational memory-write path (items 1–3 replacement).
- [`services/zoe-core/extensions/abilities.ts`](../../services/zoe-core/extensions/abilities.ts) —
  the extension-brain ability registry (the 11 + 7 = 18 tools pinned in §1).

## 5. Change log

- **2026-07-03** — created. Initial decision of record; signed off by Jason
  2026-07-03 (blanket approval, 18 items with recommended dispositions);
  supersedes the open parity question in `zoe-flue-integration.md` §10 and
  `docs/PLANS.md` Flue convergence bullet.
