# Zoe Harness Current Inventory

## Purpose

This inventory is the first cleanup gate for the Zoe evolution harness. It maps
the current active surfaces, memory paths, execution paths, and known retired
areas before any broad main-engine cleanup begins.

Source basis:

- Branch/head inspected: Pi harness merge source commit `a3fe849c584190d6c2b6c5359920858fa0b72e2e`.
- API composition point: `services/zoe-data/main.py`.
- Main agent/tool surface: `services/zoe-data/zoe_agent.py`.
- Memory facade: `services/zoe-data/memory_service.py`.
- Harness status ledger: `docs/strategy/zoe-evolution-harness-status.md`.
- Graphify report status: `graphify-out/GRAPH_REPORT.md` has been regenerated from Pi harness merge source commit `a3fe849c`.

## Active Production Surfaces

| Surface | Files | Role | Inventory Status |
| --- | --- | --- | --- |
| Zoe Data API | `services/zoe-data/main.py` | FastAPI app and router composition. | Active. |
| Chat router | `services/zoe-data/routers/chat.py` | Production chat/session/task API; primary user conversation entry. | Active; cleanup-sensitive. |
| Zoe agent | `services/zoe-data/zoe_agent.py` | Gemma-backed agent loop, prompt/tool catalog, MemPalace wrappers, web/research tools, escalation tools. | Active; large and cleanup-sensitive. |
| UI | `services/zoe-ui/` | Browser/touch/user interface. | Active. |
| Auth | `services/zoe-auth/` and `services/zoe-data/auth.py` | Authentication and API user context. | Active; critical. |
| Voice | `services/zoe-data/routers/voice_tts.py`, `services/zoe-data/routers/voice_livekit.py`, `services/zoe-data/main.py` `/ws/voice/` | Voice/TTS/live voice routing plus the direct real-time voice WebSocket. | Active. |
| Memory API | `services/zoe-data/routers/memories.py` | Memory list, review, search, export, opt-out, and forget endpoints. | Active. |
| People/notes/journal | `services/zoe-data/routers/people.py`, `services/zoe-data/routers/notes.py`, `services/zoe-data/routers/journal.py` | Domain routers that can write derived memories through `MemoryService`. | Active. |
| Multica | `services/zoe-data/multica_*.py`, `services/zoe-data/pipeline_*.py` | Governed execution, phase lanes, evidence gates, and proposal workflow. | Active. |
| Greptile/Greploop | `services/zoe-data/greptile_client.py`, `services/zoe-data/greploop_guard.py` | PR review state and bounded repair loop support. | Active. |
| Hermes/OpenClaw escalation | `services/zoe-data/zoe_agent.py`, `services/zoe-data/routers/openclaw.py`, `services/zoe-data/openclaw_ws.py` | Escalation and execution surfaces. | Active; Hermes preferred by operating guide. |
| MCP server | `services/zoe-data/mcp_server.py` | Tool bridge including memory, Graphify, and operational tools. | Active; high surface area. |
| Graphify outputs | `graphify-out/` | Code/system graph intelligence. | Refreshed from Pi harness merge source commit `a3fe849c`; rerun after substantial code or architecture changes. |
| Retired core | `services/zoe-core/` | Historical/reference code. | Retired; do not extend for new Zoe features. |

## Mounted Zoe Data Routers

`services/zoe-data/main.py` mounts these routers directly or conditionally:

- `calendar_router`
- `lists_router`
- `people_router`
- `memories_router`
- `reminders_router`
- `notes_router`
- `journal_router`
- `transactions_router`
- `weather_router`
- `system_router`
- `_agent_card_router`
- `notifications_router`
- `chat_router`
- `ui_router`
- `openclaw_router`
- `voice_tts_router`
- `user_profile_router`
- `dashboard_router`
- `stubs_router`
- `push_router`
- `proactive_router`
- `panel_auth_router`
- `panel_provision_router`
- `capability_matrix_router`
- `music_router`
- `skybridge_router`
- `portrait_router`
- `ha_control_router`
- `auth_router`
- `voice_livekit_router` when the import succeeds

Cleanup implication: broad router reshaping should wait until endpoint smoke tests
cover chat, memory, voice, system, auth, and Multica-facing routes. `main.py`
also owns direct WebSocket surfaces such as `/ws/voice/`, so cleanup cannot
assume every live endpoint is represented by a mounted router.

## Agent Tool Catalog

`services/zoe-data/zoe_agent.py` defines the primary tool catalog. Current source
inspection shows these concrete tool names in `_TOOLS`:

- `mempalace_search`
- `mempalace_add`
- `ha_control`
- `calendar_today`
- `calendar_list_events`
- `calendar_create_event`
- `reminder_create`
- `reminder_list`
- `list_add_item`
- `list_get_items`
- `weather_current`
- `weather_forecast`
- `open_touch_page`
- `bash`
- `memory_update`
- `web_search`
- `deep_web_research`
- `escalate_to_openclaw`
- `show_map`
- `show_chart`
- `show_action_menu`
- `list_openclaw_plugins`
- `list_openclaw_skills`
- `setup_telegram`
- `proactive_schedule`
- `report_issue`
- `escalate_to_hermes`

Important routing notes:

- Voice mode uses a smaller allowlist including memory, HA, calendar, reminders,
  lists, weather, page opening, web search, escalation, visual panel tools,
  OpenClaw skills, and issue reporting.
- Always-on tools include `web_search`, `deep_web_research`, and `report_issue`;
  Hermes escalation is appended when Hermes is enabled.
- `bash` is present in the agent catalog and should remain governed by risk,
  scope, and execution controls before any self-evolution automation uses it.
- `mempalace_search`, `mempalace_add`, and `memory_update` are the direct
  agent-facing memory tools and should be part of memory-write admission tests.

## Memory Read/Write Inventory

| Path | File | Operation | Current Gate | Harness Gap |
| --- | --- | --- | --- | --- |
| Memory facade | `services/zoe-data/memory_service.py` | `ingest`, `load_for_prompt`, `search`, review/edit/delete/export helpers. | PII scrub, per-user handling, idempotency/audit behavior in service. | Does not yet enforce the new Zoe memory event contract for every write. |
| Agent memory search | `services/zoe-data/zoe_agent.py` | `_mempalace_search` delegates to `MemoryService.search`. | User-scoped search and timeout handling. | Not yet routed through `zoe_memory_router.py` feature flag. |
| Agent memory add | `services/zoe-data/zoe_agent.py` | `_mempalace_add` delegates to `MemoryService.ingest`. | PII scrub/idempotency via `MemoryService`. | Direct durable writes need event/scope/evidence inventory before self-evolution memory. |
| Agent prompt facts | `services/zoe-data/zoe_agent.py` | `_load_user_facts_for_prompt` delegates to `MemoryService.load_for_prompt`. | User-scoped prompt load. | Prompt packets are not yet produced by the new memory router. |
| Background extraction | `services/zoe-data/memory_extractor.py` | Extracts candidates and ingests through `MemoryService`. | Lightweight extraction plus facade safety rails. | Needs retain-candidate/evidence mode for relational and self-evolution claims. |
| Digest jobs | `services/zoe-data/memory_digest.py` | Consolidates chat/music/other events into memories. | Uses `MemoryService` for writes. | Needs inventory of sources, scopes, and evidence for every digest write. |
| Memory API | `services/zoe-data/routers/memories.py` | Proposals, review, search, export, forget, opt-out. | FastAPI auth dependency plus `MemoryService`. | Review/admission should align with the Zoe memory contract. |
| Notes | `services/zoe-data/routers/notes.py` | Note-derived fact writes. | Uses `MemoryService`. | Needs explicit source/evidence mapping. |
| Journal | `services/zoe-data/routers/journal.py` | Journal-derived fact writes. | Uses `MemoryService`. | Needs explicit source/evidence mapping. |
| People | `services/zoe-data/routers/people.py` | Person-related fact writes and archive on delete. | Uses `MemoryService`. | Relationship facts should be candidates for Graphiti-style temporal modeling. |
| User profile | `services/zoe-data/routers/user_profile.py` | Reads profile/memory facts. | Uses `MemoryService`. | Needs router packet policy when memory router is enabled. |
| MCP memory tools | `services/zoe-data/mcp_server.py` | Store/search/review/export/delete memory operations. | Uses `MemoryService`. | High-risk bridge; must enforce user/scope/evidence before self-evolution writes. |
| OpenClaw context | `services/zoe-data/openclaw_ws.py` | Supplies MemPalace facts to OpenClaw sessions. | Gateway session per user. | Escalation packets should carry compact cited memory, not raw broad dumps. |
| Hindsight sidecar | `services/zoe-data/hindsight_memory.py` | Optional offline recall/retain sidecar client. | Disabled unless enabled; offline-only config guard. | Not measured live; not wired into production chat. |
| Hindsight retain candidates | `services/zoe-data/hindsight_retain_candidates.py` | Converts events into pending `MemoryService` rows. | Pending candidate path. | Needs Multica/admission approval before trusted memory. |

## Execution And Self-Evolution Surfaces

| Surface | Files | Current Role | Harness Gap |
| --- | --- | --- | --- |
| Multica client/operator | `services/zoe-data/multica_client.py`, `services/zoe-data/multica_operator.py` | Ticket creation, operator intents, and task control. | Needs explicit evolution proposal records and memory admission gates. |
| Pipeline evidence | `services/zoe-data/pipeline_evidence.py`, `services/zoe-data/pipeline_store.py` | Phase transitions and required evidence profiles. | Evidence gate exists for code implement completion; self-evolution/memory gates still partial. |
| Worktree bootstrap | `services/zoe-data/worktree_bootstrap.py` | Branch/worktree setup for PR work. | Should remain the default execution setup for code-producing evolution proposals. |
| Kanban adapter | `services/zoe-data/executors/kanban_adapter.py` | Hermes/Graphify/scout/implement/review lane prompting. | Needs inventory-backed cleanup boundaries before main-engine refactors. |
| Greptile client | `services/zoe-data/greptile_client.py` | PR review status/comments. | Active and required for review loop. |
| Greploop guard | `services/zoe-data/greploop_guard.py` | Bounded repair-loop state. | Active and required for small PR repair loops. |
| Hermes escalation | `services/zoe-data/zoe_agent.py` | Preferred planning/code/reasoning escalation path. | Needs structured proposal evidence before autonomous execution. |
| OpenClaw escalation | `services/zoe-data/zoe_agent.py`, `services/zoe-data/routers/openclaw.py`, `services/zoe-data/openclaw_ws.py` | Browser/manual/fallback execution surface. | Should stay explicit and measurable, not hidden inside memory or chat cleanup. |

## Graphify Status

`graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` were regenerated
from Pi harness merge source commit `a3fe849c` on 2026-06-09. The refreshed graph reports
7,756 nodes, 13,253 edges, and 614 communities after `cluster-only --no-viz`.

Graphify remains a generated architecture aid, not the only source of truth.
The embedded report commit is the pre-merge code base used for extraction; this
PR changes only generated Graphify outputs and harness docs, so a post-merge
HEAD mismatch by itself does not indicate code graph staleness. This inventory
is source-verified from mounted routers, agent tool catalog, memory files, and
pipeline files. After substantial code or architecture changes, rerun Graphify
and reconcile the generated report against this inventory before broad cleanup.

## Cleanup Readiness

Major `zoe_agent.py` or memory cleanup is not ready until these are true:

- Graphify is fresh for current `main` or the stale graph is explicitly ignored
  with source-verified replacements.
- Memory writes are mapped to user, scope, source, evidence, and review state.
- Chat behavior, memory gating, tool dispatch, and Multica evidence tests protect
  the surfaces being changed.
- Hindsight and Graphiti remain sidecar/bake-off paths, not hot-path dependencies.
- PR review uses the Greptile/Grep loop process and does not bypass branch protection.
