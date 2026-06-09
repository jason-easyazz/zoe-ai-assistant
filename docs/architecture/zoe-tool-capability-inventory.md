# Zoe Tool And Capability Inventory

## Purpose

This inventory maps Zoe's active tool and capability surfaces before the self-evolution harness starts modifying, adding, or retiring abilities. It is a cleanup gate: a tool should not be removed, replaced, or made autonomous unless its owner, risk, evidence needs, and runtime surface are understood.

Source basis:

- Inspected branch/head: `origin/main` at `a22f3de580de5c4842f22c2edbbbd4a3a5561615`.
- Agent catalog: `services/zoe-data/zoe_agent.py`.
- MCP bridge: `services/zoe-data/mcp_server.py`.
- Multica/pipeline execution: `services/zoe-data/multica_*.py`, `services/zoe-data/pipeline_*.py`, `services/zoe-data/executors/kanban_adapter.py`.
- Current architecture inventory: `docs/architecture/zoe-harness-current-inventory.md`.

## Agent Tool Catalog

`services/zoe-data/zoe_agent.py` currently exposes these callable tools to the Gemma-backed Zoe agent loop.

| Tool | Status | Confidence | Capability | Primary Owner | Risk | Harness Evidence Needed |
| --- | --- | --- | --- | --- | --- | --- |
| `mempalace_search` | Active | High | Search user-scoped MemPalace facts. | `MemoryService` / MemPalace. | Medium: prompt influence and privacy scope. | user_id, query, result refs, latency, scope. |
| `mempalace_add` | Active | High | Store a user memory fact. | `MemoryService` / MemPalace. | High: durable memory write. | user_id, source turn, evidence/candidate status, review/admission path. |
| `memory_update` | Active | High | Update/review memory-like state. | Zoe agent + `MemoryService` paths. | High: durable memory mutation. | memory id, actor, decision, before/after, audit entry. |
| `ha_control` | Active | High | Home Assistant control. | HA bridge / data router. | High: physical-world action. | user_id, entity, action, risk approval where needed, HA result. |
| `calendar_today` | Active | High | Read today's calendar. | Calendar router/service. | Low/medium: personal data read. | user_id, source, read scope. |
| `calendar_list_events` | Active | High | Read calendar event range. | Calendar router/service. | Low/medium: personal data read. | user_id, date range, read scope. |
| `calendar_create_event` | Active | High | Create calendar event. | Calendar router/service. | Medium/high: external state write. | user_id, event payload, confirmation/approval, result id. |
| `reminder_create` | Active | High | Create reminder. | Reminders router/service. | Medium: durable task write. | user_id, reminder payload, due time, result id. |
| `reminder_list` | Active | High | Read reminders. | Reminders router/service. | Low/medium: personal data read. | user_id, query scope. |
| `list_add_item` | Active | High | Add list item. | Lists router/service. | Medium: durable list write. | user_id, list id/name, item, result. |
| `list_get_items` | Active | High | Read list items. | Lists router/service. | Low/medium: personal data read. | user_id, list id/name. |
| `weather_current` | Active | High | Current weather lookup. | Weather/router tool. | Low. | location source, result. |
| `weather_forecast` | Active | High | Forecast lookup. | Weather/router tool. | Low. | location source, result. |
| `open_touch_page` | Active | High | Open/navigate local touch UI page. | UI/router surface. | Medium: user-visible UI action. | target page, actor, result. |
| `bash` | Active | High | Shell execution. | Zoe agent execution lane. | Critical: arbitrary command execution. | approval/risk gate, command, working dir, stdout/stderr summary, rollback if mutating. |
| `web_search` | Active | High | Fast DuckDuckGo/CloakBrowser search. | Zoe agent research helpers. | Medium: external data and network. | query, sources, timestamp. |
| `deep_web_research` | Active | High | Multi-source browser research. | Zoe agent + CloakBrowser. | Medium/high: network/browser state. | query, visited sources, screenshots/evidence when available. |
| `escalate_to_openclaw` | Active | High | Escalate task to OpenClaw. | OpenClaw gateway/router. | High: external execution surface. | reason, task scope, user approval where needed, result. |
| `escalate_to_hermes` | Active | High | Escalate planning/code/reasoning task to Hermes. | Hermes operator skills. | High: implementation/execution surface. | proposal/task id, worktree, tests, PR/evidence for code. |
| `show_map` | Active | High | Render map panel. | AG-UI/panel tooling. | Low/medium: UI output. | location/source and panel result. |
| `show_chart` | Active | High | Render chart panel. | AG-UI/panel tooling. | Low/medium: UI output. | data source and panel result. |
| `show_action_menu` | Active | High | Render action menu. | AG-UI/panel tooling. | Medium: can prompt follow-up actions. | actions, actor, chosen result. |
| `list_openclaw_plugins` | Active | High | Inspect OpenClaw plugins. | OpenClaw. | Low read, medium if used for execution planning. | plugin list and timestamp. |
| `list_openclaw_skills` | Active | High | Inspect OpenClaw skills. | OpenClaw. | Low read, medium if used for execution planning. | skill list and timestamp. |
| `setup_telegram` | Active | High | Telegram integration setup helper. | Integration/setup surface. | High: external account/config. | explicit approval, config status, no secret leakage. |
| `proactive_schedule` | Active | High | Schedule proactive behavior. | Proactive router/scheduler. | Medium/high: future autonomous action. | user_id, schedule, scope, cancellation path. |
| `report_issue` | Active | High | Create/report an issue or operational finding. | Zoe issue/reporting lane. | Medium: task creation/noise. | source turn, issue scope, resulting task/id. |

## Voice Tool Allowlist

Voice mode uses a smaller set of tools for latency and safety. Current source
inspection shows this allowlist baseline:

| Tool | Voice Allowed | Voice Note |
| --- | --- | --- |
| `mempalace_search` | Yes | Memory recall is allowed in voice. |
| `mempalace_add` | Yes | Memory add is allowed, but durable writes still need admission rules for high-risk facts. |
| `memory_update` | No | Keep review/update flows out of low-latency voice until explicitly designed. |
| `ha_control` | Yes | Physical-world action; must preserve HA risk/confirmation behavior. |
| `calendar_today` | Yes | Read-only calendar helper. |
| `calendar_list_events` | Yes | Read-only calendar helper. |
| `calendar_create_event` | Yes | Durable external write; keep confirmation behavior. |
| `reminder_create` | Yes | Durable reminder write. |
| `reminder_list` | Yes | Read-only reminder helper. |
| `list_add_item` | Yes | Durable list write. |
| `list_get_items` | Yes | Read-only list helper. |
| `weather_current` | Yes | Low-risk weather read. |
| `weather_forecast` | Yes | Low-risk weather read. |
| `open_touch_page` | Yes | User-visible UI action. |
| `bash` | No | Shell execution must not be voice-fast-path. |
| `web_search` | Yes | Fast search allowed. |
| `deep_web_research` | No | Long-running research should escalate/background rather than block voice. |
| `escalate_to_openclaw` | Yes | Explicit escalation allowed. |
| `escalate_to_hermes` | Yes | Always available for complex voice tasks. |
| `show_map` | Yes | Panel display allowed. |
| `show_chart` | Yes | Panel display allowed. |
| `show_action_menu` | No | Interactive action menus need separate voice design. |
| `list_openclaw_plugins` | No | Plugin inventory is not in the voice allowlist. |
| `list_openclaw_skills` | Yes | Skill listing is allowed. |
| `setup_telegram` | No | Account setup should not be voice-fast-path. |
| `proactive_schedule` | No | Future autonomous scheduling needs explicit approval flow. |
| `report_issue` | Yes | Issue reporting is allowed. |

Harness implication: any self-evolution change that expands the voice allowlist
must update this table and include voice-specific tests or a manual voice smoke
check.

## MCP Tool Surface

`services/zoe-data/mcp_server.py` exposes broader tool access for clients and execution surfaces.

| MCP Area | Representative Tools / Paths | Risk | Harness Evidence Needed |
| --- | --- | --- | --- |
| Memory | `memory_add`, `memory_search`, `memory_list`, `memory_review`, `memory_forget`. | High for writes/review/delete. | user_id, actor, memory id/ref, decision, source evidence. |
| People/notes/journal bridges | Calls `_store_person_memory`, `_store_note_memory`, `_store_journal_memory`. | High for derived memory writes. | source object id, user_id, MemoryService ref. |
| Graphify | Graphify search/query helpers. | Low/medium read; high if used to justify cleanup. | graph commit/report date, query, cited nodes. |
| Operational tools | System, UI, browser, integration, and maintenance tools. | Varies; many are privileged. | command/action log, approval where mutating. |

## Execution And Governance Lanes

| Lane | Files | Purpose | Required Before Autonomy |
| --- | --- | --- | --- |
| Multica proposal/control | `multica_client.py`, `multica_operator.py`, `multica_admission.py`. | Governed task/proposal control plane. | Structured proposal, risk, expected benefit, approval state. |
| Pipeline evidence | `pipeline_evidence.py`, `pipeline_store.py`, `pipeline_validators.py`. | Phase transition gates and evidence profiles. | Evidence requirements for memory admission and self-evolution proposals. |
| Greptile/Grep loop | `greptile_client.py`, `greploop_guard.py`. | Review and bounded repair loop. | Review state, confidence/gates, no unresolved actionable comments. |
| Worktree bootstrap | `worktree_bootstrap.py`. | Isolated code-producing work. | Branch/worktree id, PR evidence, rollback path. |
| Hermes | `executors/kanban_adapter.py` plus operator skills. | Preferred planning/code/reasoning lane. | Task packet, scoped context, tests, PR/Greptile evidence for code. |
| OpenClaw | `routers/openclaw.py`, `openclaw_ws.py`. | Manual/fallback browser/execution lane. | Explicit routing reason and result evidence. |

## Retired And Reference Surfaces

| Surface | Status | Confidence | Owner / Notes | Cleanup Rule |
| --- | --- | --- | --- | --- |
| `services/zoe-core/` | Retired reference | High | Historical Zoe core code; project rules say do not extend it for new features. | Do not use as a source for new runtime behavior; archive/delete only through cleanup safety. |
| `docs/archive/retired-services/` | Retired reference | High | Archived service documentation and code snapshots. | Read for history only; do not wire into production paths. |
| `services/zoe-ui/dist/js/_archive/` | Archived UI reference | Medium | UI archive files retained in manifest. | Do not revive without a dedicated PR and current UI tests. |

## Retirement Rules

A tool can be retired only when:

- it has a named replacement or measured non-use;
- active call sites are removed or redirected;
- user-facing chat/voice affordances still work;
- memory, external state, or physical-world side effects are covered by tests or manual verification;
- the retirement is reviewed in a small PR with Greptile and local validation.
