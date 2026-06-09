# Zoe Evolution Harness Implementation Status

## Purpose

This ledger keeps the Zoe evolution harness honest. The strategy in
`docs/strategy/zoe-evolution-harness-plan.md` is the north star; this file is
the implementation truth table for what is complete, what is partial, what is
blocked, and what should become the next small pull request.

Concrete modules, docs, metrics, and runtime concepts should use Zoe names.
External inspirations remain product vision and research inputs, not Zoe system
identifiers.

## Status Snapshot

Date: 2026-06-09

Current merged foundation:

- Strategy and ADRs exist for the layered Zoe memory/evolution direction.
- `zoe_memory_contract.py` defines the shared memory event and relationship contract.
- `zoe_memory_layers.py` documents deterministic layer responsibilities in code.
- `zoe_memory_router.py` provides deterministic routing decisions and prompt packet policy.
- `hindsight_memory.py` implements an offline-only Hindsight sidecar client.
- `hindsight_bakeoff.py` provides synthetic evaluation fixtures and scoring helpers.
- `hindsight_retain_candidates.py` creates pending retain candidates instead of trusted blind writes.
- Pipeline evidence gates require code-producing implement phases to carry tool and PR evidence before completion.
- Greploop/Greptile handling recognizes active review states and avoids treating an in-progress review as a no-progress loop.

Important non-complete truth:

- Graphify has been refreshed from `origin/main` at `ad52f61d`; rerun it after subsequent code or architecture changes.
- Zoe does not yet have a complete active-vs-retired tool inventory.
- Zoe does not yet have a complete memory read/write inventory.
- Hindsight has not yet been run as a measured live sidecar bake-off.
- Graphiti/FalkorDB/Neo4j have not yet been measured for Zoe relational memory.
- The deterministic memory router is not yet wired into production chat behind a feature flag.
- Retain-candidate admission is not yet fully governed through Multica approval.
- The full self-evolution loop is not yet implemented end to end.
- Zoe main engine cleanup should not begin until the inventory, graph, and memory/evolution contracts have protective tests around the active surfaces.

## Phase Ledger

| Phase | Status | Evidence | Next PR Slice |
| --- | --- | --- | --- |
| Strategy and ADRs | Complete | `docs/strategy/zoe-evolution-harness-plan.md`, `docs/adr/ADR-zoe-memory-layer.md`, `docs/adr/ADR-hindsight-bakeoff.md`, `docs/adr/ADR-graphiti-bakeoff.md` | Keep updated only when decisions change. |
| Zoe naming discipline | Complete for new harness docs/code | Strategy and ADRs use Zoe system names and keep external inspirations out of concrete identifiers. | Sweep future PRs for accidental non-Zoe module names. |
| Offline-only memory rule | Partial | `HindsightConfig` rejects public/cloud memory model configuration by default. | Add a repository-wide memory/provider audit that flags cloud memory paths and exposed secrets. |
| Memory contract | Complete foundation | `services/zoe-data/zoe_memory_contract.py` and `services/zoe-data/tests/test_zoe_memory_contract.py`. | Extend only when a measured backend requires a new field or relationship. |
| Memory layer map | Complete foundation | `services/zoe-data/zoe_memory_layers.py` and `services/zoe-data/tests/test_zoe_memory_layers.py`. | Link layer decisions to runtime config and status endpoints. |
| Memory router | Partial | `services/zoe-data/zoe_memory_router.py` and `services/zoe-data/tests/test_zoe_memory_router.py`. | Wire into chat/agent recall behind a disabled-by-default feature flag with latency guards. |
| MemPalace baseline | Partial | `MemoryService` remains the live memory facade; MemPalace integration tests exist. | Add a current MemPalace read/write inventory and latency baseline. |
| Hindsight bake-off | Partial | Offline sidecar client, retain-candidate helpers, synthetic fixtures, and tests exist. | Run a measured sidecar bake-off with local models only and record p50/p95 latency, failures, and evidence quality. |
| Graphiti bake-off | Not started | ADR exists only. | Build Graphiti evaluation fixtures, then test FalkorDB first and Neo4j second if feasible. |
| Graphify current map | Complete foundation | `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` were regenerated from `origin/main` at `ad52f61d`; `docs/architecture/zoe-harness-current-inventory.md` records the source-backed inventory. | Rerun Graphify after substantial code or architecture changes. |
| Tool/capability inventory | Not started | Current plan names surfaces but does not provide a full inventory. | Generate a tool/capability map with owner, runtime surface, evidence requirements, and retirement status. |
| Memory read/write inventory | Not started | Existing code uses `MemoryService`, MemPalace, digest, MCP, chat, notes, people, and journal paths, but no single inventory exists. | Produce an inventory of every durable memory read/write and whether it has user, scope, evidence, and review state. |
| Observation/evaluation traces | Not started | Memory metrics exist for MemPalace but not full retrieval helpfulness/contradiction traces. | Add a trace schema for recall, retain candidate, admission, contradiction, and fallback events. |
| Multica evidence gates | Partial | Implement completion now requires PR evidence for code/default profiles. | Add memory admission gates and explicit self-evolution proposal gates. |
| Self-evolution loop | Partial | Multica, pipeline evidence, Greploop, Greptile, Hermes, and worktree bootstrap pieces exist. | Implement structured Notice -> Explain -> Search -> Evaluate -> Propose records before any automatic execution. |
| Main engine cleanup | Deferred | `zoe_agent.py` remains large and active. | Start only after inventories and chat/memory/tool dispatch tests are strong enough to prevent regressions. |

## Missing Work By Capability

### Current-State Inventory

Zoe needs one current, generated-ish inventory before major cleanup:

- active services, routers, agents, schedulers, and sidecars;
- tools and capability owners;
- memory read/write paths;
- escalation paths through Hermes, OpenClaw, Multica, Greploop, and Greptile;
- feature flags and runtime dependencies;
- retired/reference-only surfaces, especially `services/zoe-core`.

Acceptance evidence:

- Architecture inventory is generated from current `main`.
- Graphify report freshness is recorded with the exact commit hash.
- Every active/retired surface has an owner and confidence level.
- No cleanup PR deletes or rewires a surface that has not been inventoried.

### Memory And Relationship Truth

The plan still needs the relational truth layer to be proven, not assumed.

Required slices:

- benchmark MemPalace in Zoe with representative local queries;
- run Hindsight as an offline-only sidecar and measure recall/retain behavior;
- build Graphiti/FalkorDB and Graphiti/Neo4j fixtures for people, tools, failures, fixes, approvals, recurring tasks, and supersession;
- compare speed, memory footprint, setup burden, and failure modes on Zoe hardware or an explicitly marked remote sidecar;
- decide which graph functionality belongs in Postgres, which belongs in MemPalace, and which requires Graphiti-style temporal graph memory.

Acceptance evidence:

- p50/p95 latency for recall, retain, and graph query paths.
- memory/CPU footprint under idle and test load.
- evidence pointers on every returned durable fact or relationship.
- cross-user/scope isolation tests pass.
- wrong, disputed, and superseded memories can be corrected without destructive overwrite.

### Governed Self-Evolution

The full Zoe evolution loop is still future work. The immediate goal is not automatic self-modification; it is reliable proposal formation with evidence.

Required slices:

- `Notice`: capture repeated failures, user requests, stale tools, missing capabilities, and manual operator observations.
- `Explain`: convert signals into a structured problem statement with evidence.
- `Search`: look across existing Zoe tools, Pi, MCP servers, GitHub projects, local skills, and APIs.
- `Evaluate`: score candidates by fit, activity, stars, license, tests, local viability, runtime cost, security, and maintenance burden.
- `Propose`: create a Multica-backed evolution proposal with scope, risk, expected benefit, verification, and rollback.
- `Approve`: require user/admin approval before privileged changes.
- `Execute`: route work through the appropriate lane with bounded worktrees.
- `Verify`: attach tests, health checks, screenshots for UI, or runtime metrics.
- `Learn`: retain outcome, failures, fixes, and capability trust as pending or approved memory according to the memory contract.
- `Retire`: remove stale tools only after measured non-use, replacement, and cleanup safety review.

Acceptance evidence:

- Every self-evolution proposal has source evidence and a rollback plan.
- Code-producing completion has PR evidence.
- Privileged execution cannot proceed without approval.
- Failed execution produces a pending memory candidate, not a trusted fact.
- Successful execution updates capability trust only after verification.

## Recommended PR Order

Keep each pull request small enough for Greptile and Zoe verification.

1. Harness status ledger.
   - Add this file.
   - Verify structure.
   - Use Greptile as the first review loop.
2. Fresh Graphify and architecture inventory.
   - Status: complete foundation as of `ad52f61d`.
   - Keep Graphify refreshed after substantial code or architecture changes.
   - Reconcile future generated reports against `docs/architecture/zoe-harness-current-inventory.md`.
3. Tool and memory inventory.
   - Add `docs/architecture/zoe-tool-capability-inventory.md`.
   - Add `docs/architecture/zoe-memory-read-write-inventory.md`.
   - Include active, retired, owner, scope, evidence, and risk columns.
4. MemPalace baseline evaluation.
   - Add repeatable local benchmark fixtures.
   - Measure current p50/p95 and memory footprint.
   - Keep MemPalace as baseline unless another offline option wins.
5. Hindsight sidecar bake-off.
   - Run local/offline-only Hindsight.
   - Produce measured results and gaps.
   - Keep it out of production chat until feature-flagged and measured.
6. Graphiti relational bake-off.
   - Add fixtures first.
   - Test FalkorDB, then Neo4j if feasible.
   - Decide sidecar/remote/hot-path eligibility from evidence.
7. Runtime memory router feature flag.
   - Wire deterministic routing into chat/agent recall in fallback-safe mode.
   - Add latency timeout and compact cited memory packets.
8. Memory admission governance.
   - Route retain candidates through Multica/review.
   - Add evidence and scope gates for relational and self-evolution memory.
9. Self-evolution proposal records.
   - Implement Notice -> Explain -> Search -> Evaluate -> Propose records.
   - Keep execution approval-gated.
10. Main engine cleanup.
    - Split only protected, well-understood areas in `zoe_agent.py`.
    - Remove duplicate or retired memory paths only after inventory and tests.

## Stop Conditions

Do not start major Zoe main engine cleanup if any of these are true:

- Graphify is stale and no source-verified architecture map exists.
- Active-vs-retired surfaces are not labeled.
- Memory writes cannot be traced to user, scope, evidence, and review state.
- Chat, memory gating, tool dispatch, and Multica evidence tests are missing.
- A proposed change would bypass PR review, Greptile, or protected-branch policy.
- A memory backend requires cloud models for Zoe memory.

## Definition Of Done For The Full Harness

The Zoe evolution harness is done only when:

- normal chat latency regression stays under 10%;
- memory packets are compact, cited, scoped, and correction-aware;
- MemPalace remains a measured offline baseline;
- Hindsight is either accepted with measurements or rejected with evidence;
- Graphiti-style relational memory is either accepted with measurements or deferred with evidence;
- no durable memory write is unscoped;
- no trusted relational/self-evolution memory lacks evidence;
- every code-producing self-evolution change has proposal, approval when privileged, PR evidence, verification, and retained outcome;
- stale tools and memory paths can be retired through measured, reviewable cleanup rather than intuition.
