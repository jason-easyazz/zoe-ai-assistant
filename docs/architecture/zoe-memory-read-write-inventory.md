# Zoe Memory Read Write Inventory

## Purpose

This inventory maps durable and prompt-time memory paths before Zoe adds Hindsight, Graphiti-style relational memory, or self-evolution memory admission. The goal is zero unscoped durable memory writes and no trusted relational or self-evolution memory without evidence.

Source basis:

- Inspected branch/head: `origin/main` at `a22f3de580de5c4842f22c2edbbbd4a3a5561615`.
- Memory facade: `services/zoe-data/memory_service.py`.
- Agent memory call sites: `services/zoe-data/zoe_agent.py`.
- API memory router: `services/zoe-data/routers/memories.py`.
- Derived memory paths: notes, journal, people, user profile, digest, extractor, portrait, MCP server.
- Hindsight candidate path: `services/zoe-data/hindsight_retain_candidates.py`.

## Memory Facade Contract

`MemoryService` is the active read/write facade for MemPalace-backed Zoe memory.

| Operation | Direction | Purpose | Required Evidence For Harness |
| --- | --- | --- | --- |
| `ingest` | Write | Add a memory row/fact. | user_id, source, memory_type, source id/turn, candidate/review status. |
| `load_for_prompt` | Read | Load approved/user-scoped facts for prompt context. | user_id, limit, returned refs, latency. |
| `search` | Read | Semantic search over user-scoped memory. | user_id, query, limit, returned refs, latency. |
| `review` | Mutate | Approve/reject/edit pending memory. | memory_id, actor, decision, before/after for edits. |
| `forget_last` | Mutate | Soft-delete most recent memory for correction. | user_id, actor/intent source, memory ref. |
| `delete_user` | Delete | Admin right-to-be-forgotten path. | admin actor, target user_id, count removed. |
| `archive_by_entity` | Mutate | Archive memories for deleted people/entities. | user_id, entity id, count/result. |
| `export_user` | Read | Export user memory. | admin/user actor and target scope. |

Harness rule: new memory backends should not bypass this facade unless the PR is explicitly changing the facade itself or adding a measured sidecar with no trusted direct writes.

## Durable Write Paths

| Path | File | Source | Current Write Shape | Gap Before Trusted Evolution Memory |
| --- | --- | --- | --- | --- |
| Agent direct add | `zoe_agent.py` `_mempalace_add` | Chat/tool call. | Calls `MemoryService.ingest`. | Needs Zoe memory event/scoped evidence packet before relational/self-evolution writes. |
| Agent background extraction | `zoe_agent.py` background save -> `memory_extractor.py` | Chat turns. | Extracts candidates and calls `MemoryService.ingest`. | Candidate/admission mode needed for high-risk facts and corrections. |
| Memory API proposal | `routers/memories.py` `/api/memories/proposals` | UI/API. | Calls `MemoryService.ingest` pending/review path. | Align review fields with Zoe memory contract. |
| Memory API review | `routers/memories.py` review endpoint. | UI/API. | Calls `MemoryService.review`. | Ensure actor and before/after evidence are retained. |
| Notes derived memory | `routers/notes.py` | Note create/update. | Calls `MemoryService.ingest`. | Add source note id and explicit evidence ref. |
| Journal derived memory | `routers/journal.py` | Journal entries. | Calls `MemoryService.ingest`. | Add source journal id and explicit evidence ref. |
| People derived memory | `routers/people.py` | Person records. | Calls `MemoryService.ingest`; archives by entity on delete. | Relationship facts should become Graphiti-style candidates with evidence. |
| User profile memory | `routers/user_profile.py` | Profile update path. | Calls `MemoryService.ingest`. | Add structured source/evidence metadata. |
| Person extractor | `person_extractor.py` | Extracted people/entities. | Calls `MemoryService.ingest`. | Needs candidate status for uncertain extraction. |
| Memory digest | `memory_digest.py` | Chat/music/behavior consolidation. | Calls `search`, `review`, and `ingest`. | Needs source inventory per digest job and contradiction/supersession traces. |
| MCP memory add | `mcp_server.py` `memory_add`. | MCP client/tool. | Calls `MemoryService.ingest`. | High-risk bridge; require explicit user/scope/evidence before self-evolution writes. |
| MCP memory review/forget | `mcp_server.py` `memory_review`, `memory_forget`. | MCP client/tool. | Calls `MemoryService.review`. | Require actor, decision, before/after, audit link. |
| Hindsight retain candidate | `hindsight_retain_candidates.py` | Reflective sidecar candidate. | Builds pending `MemoryService` payload. | Needs Multica/admission approval before trusted memory. |

## Prompt And Recall Read Paths

| Path | File | Purpose | Current Gate | Gap |
| --- | --- | --- | --- | --- |
| Agent prompt facts | `zoe_agent.py` `_load_user_facts_for_prompt`. | Prompt-time approved facts. | `MemoryService.load_for_prompt`. | Needs compact cited packet from `zoe_memory_router.py`. |
| Agent semantic recall | `zoe_agent.py` `_mempalace_search`. | On-demand memory search. | `MemoryService.search` with timeout. | Route through memory router feature flag. |
| Memory API search/list/export | `routers/memories.py`. | User/admin memory UI. | Auth dependency and `MemoryService`. | Add structured trace for query/result/helpfulness. |
| Proactive morning check-in | `proactive/triggers/morning_checkin.py`. | Reads recent memory for proactive context. | `load_for_prompt`. | Must remain scoped and cancellation-aware. |
| User portrait | `user_portrait.py`, `routers/portrait.py`. | Synthesizes profile context from memories. | `load_for_prompt`. | Needs trace and stale/superseded handling. |
| Startup health probe | `main.py`. | Checks MemoryService readiness. | `load_for_prompt` and `search` against `family-admin`. | Keep read-only; do not write synthetic rows. |
| Memory metrics | `memory_metrics.py`. | Counts/latency gauges. | `MemoryService` read helpers. | Extend to recall packet and admission metrics. |
| OpenClaw context | `openclaw_ws.py`. | Supplies user facts to OpenClaw session. | User-scoped gateway session. | Use compact cited packet instead of broad raw memory dump. |
| MCP memory search/list | `mcp_server.py`. | MCP memory read. | `MemoryService.search` and list/export helpers. | Trace actor/scope and result refs. |

## Hindsight And Graphiti Readiness

Hindsight is currently safe as an optional sidecar because:

- `hindsight_memory.py` defaults to disabled, offline-only, localhost, and no auto-retain;
- `tools/audit/validate_offline_memory.py` is enforced in CI;
- `hindsight_retain_candidates.py` writes pending candidates instead of trusted memories.

Graphiti-style relational memory is not yet implemented. Relationship facts from people, tools, failures, fixes, approvals, recurring tasks, and self-evolution outcomes should first become evidence-backed memory events or pending candidates, then be admitted to a graph backend only after the Graphiti bake-off proves latency, footprint, supersession, and source isolation.

## Required Admission Metadata

Every new durable memory write introduced by the Zoe harness should include or be derivable from:

- `user_id`;
- scope: personal, shared, ambient, system, or project;
- source: chat, tool, test, trace, proposal, code, or external;
- event type: fact, preference, experience, failure, fix, capability, recurring_task, or approval;
- evidence refs: source turn, tool run, test, PR, proposal, or raw object id;
- confidence and review status;
- supersedes/disputed/archived state when correcting old memory.

## Cleanup Rules

Do not remove or merge memory paths until:

- the path is mapped in this inventory;
- focused tests cover user/scope isolation and write status;
- high-risk writes go through pending/admission flow;
- existing chat, voice, memories UI, MCP, and digest behavior still works;
- MemPalace remains the measured offline baseline unless a replacement wins Zoe benchmarks.
