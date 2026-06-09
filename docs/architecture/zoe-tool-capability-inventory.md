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

| Tool | Capability | Primary Owner | Risk | Harness Evidence Needed |
| --- | --- | --- | --- | --- |
| `mempalace_search` | Search user-scoped MemPalace facts. | `MemoryService` / MemPalace. | Medium: prompt influence and privacy scope. | user_id, query, result refs, latency, scope. |
| `mempalace_add` | Store a user memory fact. | `MemoryService` / MemPalace. | High: durable memory write. | user_id, source turn, evidence/candidate status, review/admission path. |
| `memory_update` | Update/review memory-like state. | Zoe agent + `MemoryService` paths. | High: durable memory mutation. | memory id, actor, decision, before/after, audit entry. |
| `ha_control` | Home Assistant control. | HA bridge / data router. | High: physical-world action. | user_id, entity, action, risk approval where needed, HA result. |
| `calendar_today` | Read today's calendar. | Calendar router/service. | Low/medium: personal data read. | user_id, source, read scope. |
| `calendar_list_events` | Read calendar event range. | Calendar router/service. | Low/medium: personal data read. | user_id, date range, read scope. |
| `calendar_create_event` | Create calendar event. | Calendar router/service. | Medium/high: external state write. | user_id, event payload, confirmation/approval, result id. |
| `reminder_create` | Create reminder. | Reminders router/service. | Medium: durable task write. | user_id, reminder payload, due time, result id. |
| `reminder_list` | Read reminders. | Reminders router/service. | Low/medium: personal data read. | user_id, query scope. |
| `list_add_item` | Add list item. | Lists router/service. | Medium: durable list write. | user_id, list id/name, item, result. |
| `list_get_items` | Read list items. | Lists router/service. | Low/medium: personal data read. | user_id, list id/name. |
| `weather_current` | Current weather lookup. | Weather/router tool. | Low. | location source, result. |
| `weather_forecast` | Forecast lookup. | Weather/router tool. | Low. | location source, result. |
| `open_touch_page` | Open/navigate local touch UI page. | UI/router surface. | Medium: user-visible UI action. | target page, actor, result. |
| `bash` | Shell execution. | Zoe agent execution lane. | Critical: arbitrary command execution. | approval/risk gate, command, working dir, stdout/stderr summary, rollback if mutating. |
| `web_search` | Fast DuckDuckGo/CloakBrowser search. | Zoe agent research helpers. | Medium: external data and network. | query, sources, timestamp. |
| `deep_web_research` | Multi-source browser research. | Zoe agent + CloakBrowser. | Medium/high: network/browser state. | query, visited sources, screenshots/evidence when available. |
| `escalate_to_openclaw` | Escalate task to OpenClaw. | OpenClaw gateway/router. | High: external execution surface. | reason, task scope, user approval where needed, result. |
| `escalate_to_hermes` | Escalate planning/code/reasoning task to Hermes. | Hermes operator skills. | High: implementation/execution surface. | proposal/task id, worktree, tests, PR/evidence for code. |
| `show_map` | Render map panel. | AG-UI/panel tooling. | Low/medium: UI output. | location/source and panel result. |
| `show_chart` | Render chart panel. | AG-UI/panel tooling. | Low/medium: UI output. | data source and panel result. |
| `show_action_menu` | Render action menu. | AG-UI/panel tooling. | Medium: can prompt follow-up actions. | actions, actor, chosen result. |
| `list_openclaw_plugins` | Inspect OpenClaw plugins. | OpenClaw. | Low read, medium if used for execution planning. | plugin list and timestamp. |
| `list_openclaw_skills` | Inspect OpenClaw skills. | OpenClaw. | Low read, medium if used for execution planning. | skill list and timestamp. |
| `setup_telegram` | Telegram integration setup helper. | Integration/setup surface. | High: external account/config. | explicit approval, config status, no secret leakage. |
| `proactive_schedule` | Schedule proactive behavior. | Proactive router/scheduler. | Medium/high: future autonomous action. | user_id, schedule, scope, cancellation path. |
| `report_issue` | Create/report an issue or operational finding. | Zoe issue/reporting lane. | Medium: task creation/noise. | source turn, issue scope, resulting task/id. |

## Voice Tool Allowlist

Voice mode uses a smaller set of tools for latency and safety. Current voice allowlist includes memory search/add, HA, calendar, reminders, lists, weather, page opening, web search, Hermes/OpenClaw escalation, map/chart display, OpenClaw skill listing, and issue reporting. Voice always keeps Hermes escalation available for complex tasks.

Harness implication: voice tools need stricter latency and interruption behavior than chat tools. Any self-evolution change that expands the voice allowlist must include voice-specific tests or a manual voice smoke check.

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

## Retirement Rules

A tool can be retired only when:

- it has a named replacement or measured non-use;
- active call sites are removed or redirected;
- user-facing chat/voice affordances still work;
- memory, external state, or physical-world side effects are covered by tests or manual verification;
- the retirement is reviewed in a small PR with Greptile and local validation.
