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
- Tool/capability and memory read/write inventories now exist as cleanup gates; keep them updated as runtime paths change.
- MemPalace now has a repeatable local baseline harness and first measured Zoe-host run; Hindsight now has a fixed measured runner and Zoe-host availability baseline, but no live sidecar bake-off yet because no Hindsight process/container is running.
- Graphiti/FalkorDB/Neo4j have not yet been measured for Zoe relational memory, but Zoe-specific Graphiti fixtures now define the first relationship eval set.
- The plan now names Zoe's missing north-star layer: capability profiles, trust/autonomy classes, candidate scouting, outcome evals, and hardware-aware promotion.
- Zoe now has an executable capability profile contract and initial self-model for key chat, memory, graph, governance, escalation, Pi, and device-control surfaces.
- Zoe now has candidate scoring for comparing Pi, MCP, GitHub, skill, API, local-service, and existing-Zoe options before adoption.
- Zoe now has a self-evolution proposal contract that attaches signals, candidate scores, affected capabilities, approval gates, verification, rollback, and evidence before any execution.
- Zoe now has an observation/evaluation trace schema for recall, retain candidates, admission, contradiction, fallback, proposals, verification, outcome evals, and hardware budget evidence.
- The deterministic memory router is not yet wired into production chat behind a feature flag.
- Retain-candidate admission is not yet fully governed through Multica approval.
- The full self-evolution loop is not yet implemented end to end; proposal records are now defined, but existing live proposal writers still need to emit them and Multica still needs to enforce admission.
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
| MemPalace baseline | Complete foundation | `services/zoe-data/mempalace_baseline.py`, `scripts/maintenance/mempalace_baseline.py`, and `docs/architecture/zoe-mempalace-baseline.md` record a repeatable local benchmark; first run scored 1.0 avg with p95 200.90 ms and cleaned up 4 synthetic rows. | Expand with relational/supersession cases before comparing Graphiti-style backends. |
| Hindsight bake-off | Partial | Offline sidecar client, retain-candidate helpers, synthetic fixtures, measured runner, availability doc, and tests exist; current Zoe-host check found no running Hindsight sidecar. | Start a local/offline sidecar, run retain/recall with synthetic events, and record p50/p95 latency, failures, evidence quality, and CPU/RAM use. |
| Graphiti bake-off | Partial | ADR plus `services/zoe-data/graphiti_bakeoff.py`, `services/zoe-data/tests/test_graphiti_bakeoff.py`, and `docs/architecture/zoe-graphiti-fixtures.md` define the first relationship fixture set. | Run the fixtures against FalkorDB first, then Neo4j if feasible, and record latency, evidence quality, supersession behavior, and CPU/RAM use. |
| Graphify current map | Complete foundation | `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` were regenerated from `origin/main` at `ad52f61d`; `docs/architecture/zoe-harness-current-inventory.md` records the source-backed inventory. | Rerun Graphify after substantial code or architecture changes. |
| Tool/capability inventory | Complete foundation | `docs/architecture/zoe-tool-capability-inventory.md` maps agent, MCP, Multica, Hermes, OpenClaw, and governance surfaces. | Keep updated when tool catalogs or execution lanes change. |
| Memory read/write inventory | Complete foundation | `docs/architecture/zoe-memory-read-write-inventory.md` maps MemoryService operations, durable writes, prompt reads, MCP paths, Hindsight candidates, and metadata gaps. | Keep updated when memory write/read paths change. |
| Zoe north-star layer | Complete foundation | `docs/strategy/zoe-evolution-harness-plan.md` and `docs/adr/ADR-zoe-north-star-layer.md` define capability profiles, trust/autonomy classes, discovery scoring, outcome evals, and hardware budgets. | Build capability profile inventory and candidate-scoring records. |
| Capability profiles | Complete foundation | `services/zoe-data/zoe_capability_profile.py`, `services/zoe-data/tests/test_zoe_capability_profile.py`, and `docs/architecture/zoe-capability-profiles.md` define the first executable Zoe capability self-model. | Wire profiles into candidate scoring, self-evolution proposals, and cleanup gates. |
| Candidate scoring | Complete foundation | `services/zoe-data/zoe_candidate_scoring.py`, `services/zoe-data/tests/test_zoe_candidate_scoring.py`, and `docs/architecture/zoe-candidate-scoring.md` define candidate scoring and adoption gates. | Attach candidate scores to self-evolution proposal records before installs or replacements. |
| Observation/evaluation traces | Complete foundation | `services/zoe-data/zoe_observation_trace.py`, `services/zoe-data/tests/test_zoe_observation_trace.py`, and `docs/architecture/zoe-observation-trace-schema.md` define trace records for recall, retain candidate, admission, contradiction, fallback, proposal, verification, outcome eval, and hardware-budget events. | Wire traces around memory router decisions, retain candidates, sidecar bake-offs, Multica admission, and capability promotion/retirement. |
| Multica evidence gates | Partial | Implement completion now requires PR evidence for code/default profiles. | Add memory admission gates and explicit self-evolution proposal gates. |
| Self-evolution proposal records | Complete foundation | `services/zoe-data/zoe_evolution_proposal.py`, `services/zoe-data/tests/test_zoe_evolution_proposal.py`, and `docs/architecture/zoe-evolution-proposal-contract.md` define structured Notice -> Explain -> Search -> Evaluate -> Propose records. | Adapt existing live proposal writers to emit this payload and require it before installs/replacements. |
| Self-evolution loop | Partial | Multica, pipeline evidence, Greploop, Greptile, Hermes, worktree bootstrap, candidate scoring, and proposal contract pieces exist. | Wire live proposal writers, Multica admission, execution approval, verification, retained outcomes, and retirement evidence. |
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

### Zoe Product Intelligence

The missing plan layer is not another database. It is a product and agency model that keeps Zoe pointed at the right kind of intelligence: local-first continuity, useful initiative, safe capability growth, and measured improvement.

Required slices:

- define capability profiles for current Zoe tools, routes, skills, sidecars, and execution lanes;
- classify each capability by trust level, approval requirement, offline viability, model/device dependency, tests, rollback, and hardware budget;
- create candidate-scoring records for Pi, MCP, GitHub, local skills, APIs, and existing Zoe tools before adopting or replacing anything;
- add trust/autonomy classes so Zoe can observe, recall, suggest, prepare, execute, and promote under different gates;
- add outcome eval traces for task completion, correction handling, continuity, friction, latency, trust, cleanup quality, and hardware fit;
- treat user-visible coherence and relationship continuity as first-class success metrics alongside latency and test pass rate.

Acceptance evidence:

- every trusted capability has a profile with tests, owner surface, budget, and rollback;
- candidate adoption decisions record activity, stars, license, offline viability, security, footprint, and fit;
- privileged execution cannot occur merely because a capability exists;
- outcome evals can create Notice records when Zoe repeatedly fails or regresses;
- cleanup proposals include non-use, replacement, failure-rate, or maintenance-cost evidence.

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
   - Status: complete foundation.
   - Keep `docs/architecture/zoe-tool-capability-inventory.md` and `docs/architecture/zoe-memory-read-write-inventory.md` updated as runtime paths change.
   - Use the inventories as cleanup and self-evolution admission gates.
4. MemPalace baseline evaluation.
   - Status: complete foundation.
   - Repeatable benchmark and first Zoe-host p50/p95 run are documented in `docs/architecture/zoe-mempalace-baseline.md`.
   - Expand with memory footprint, relational, and supersession cases before backend replacement decisions.
5. Hindsight sidecar bake-off.
   - Status: runner and availability baseline complete.
   - Next: start local/offline-only Hindsight and run synthetic retain/recall.
   - Produce measured p50/p95, evidence quality, CPU/RAM, and gaps.
   - Keep it out of production chat until feature-flagged and measured.
6. Graphiti relational bake-off.
   - Status: fixture foundation complete.
   - Next: test FalkorDB, then Neo4j if feasible.
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
10. Zoe capability profile inventory.
    - Status: complete foundation for key active and candidate surfaces.
    - Expand profiles for voice, MCP, calendars, reminders, lists, search, maps/charts, proactive scheduling, and setup flows.
    - Use trust level, approval class, tests, budget, dependencies, rollback, and known failures as proposal/cleanup gates.
11. Candidate discovery scoring.
    - Status: complete foundation.
    - Candidate records now compare Pi, MCP, GitHub, local skills, APIs, local services, and existing Zoe capabilities.
    - Next: attach scores to self-evolution proposals and require them before installs/replacements.
12. Outcome eval trace schema.
    - Track task completion, correction handling, continuity, friction, latency, trust, cleanup quality, and hardware fit.
    - Convert recurring failures into Notice records.
13. Main engine cleanup.
    - Split only protected, well-understood areas in `zoe_agent.py`.
    - Remove duplicate or retired memory paths only after inventory and tests.

## Stop Conditions

Do not start major Zoe main engine cleanup if any of these are true:

- Graphify is stale and no source-verified architecture map exists.
- Active-vs-retired surfaces are not labeled.
- Memory writes cannot be traced to user, scope, evidence, and review state.
- Chat, memory gating, tool dispatch, and Multica evidence tests are missing.
- Capability profiles and outcome evals are missing for the surface being cleaned or replaced.
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
- every trusted Zoe capability has a profile, budget, tests, approval class, known failure list, and rollback path;
- outcome evals can show Zoe is improving task completion, continuity, correction handling, trust, latency, and hardware fit;
- stale tools and memory paths can be retired through measured, reviewable cleanup rather than intuition.
