# Zoe Evolution Harness Implementation Status

## Purpose

This ledger keeps the Zoe evolution harness honest. The strategy in
`docs/strategy/zoe-evolution-harness-plan.md` is the north star; this file is
the implementation truth table for what is complete, what is partial, what is
blocked, and what should become the next small pull request.

Concrete modules, docs, metrics, and runtime concepts should use Zoe names.
External inspirations remain product vision and research inputs, not Zoe system
identifiers.

Implementation rule: close one contract loop end-to-end before starting the
next. A schema, card contract, memory contract, evidence contract, or handoff
contract is not considered complete until at least one intended consumer path is
tested, or the missing last mile is explicitly recorded as blocked.

## Status Snapshot

Date: 2026-06-10

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

- Graphify was last successfully refreshed through source commit `75f5345d`.
  A refresh attempt after `18b8bd5` scanned 599 code files and 257 docs but
  all 7 OpenAI semantic chunks failed with `insufficient_quota`; the tool still
  wrote partial graph output, so that output was discarded and the committed map
  must be treated as stale for changes after `75f5345d`. A local Ollama/Gemma
  semantic smoke test succeeded. A post-`015a529` full-repo local Gemma attempt
  reached AST extraction for 601 code files and 257 docs through Zoe's localhost
  llama.cpp server, but it was stopped after repeated context splits and three
  invalid JSON chunks; no partial graph output was committed. After `5c9ef0f`,
  the new local probe ran repo mode against current main and rejected the run
  after a 300 second timeout, 603 code files, 257 docs, completed AST extraction,
  11 context split warnings, three invalid JSON chunks, and one truncated chunk;
  temporary graph output existed but was not accepted or committed. The local
  backend is promising but not yet accepted for full Graphify refreshes.
- Zoe now has a local/offline Graphify probe that runs Graphify through the
  Ollama/OpenAI-compatible localhost llama.cpp path in a temporary fixture or
  snapshot, strips cloud API keys from the probe environment, parses context,
  invalid-JSON, truncation, quota, and graph-output evidence, records local
  model-file fit plus extract/cluster duration and child max RSS evidence, and
  refuses to accept partial or malformed graph output.
- Zoe now has a fail-closed local/offline Graphify refresh wrapper that reuses
  the probe, requires repo mode plus clustering, and syncs `graphify-out` back to
  the repo only when the probe is accepted and required graph/report files exist.
  Rejected local runs write the generated refresh marker and leave the committed
  graph untouched. Probe timeouts now terminate the whole Graphify process group
  so worker children do not survive failed local refresh attempts. The Graphify
  timer template now calls this local wrapper instead of the legacy OpenAI-backed
  refresh script.
- Tool/capability and memory read/write inventories now exist as cleanup gates; keep them updated as runtime paths change.
- MemPalace now has a repeatable local baseline harness and first measured Zoe-host run; Hindsight now has a fixed measured runner, read-only probes, Zoe-host availability baseline, and first live offline retain/recall bake-off: 4/4 synthetic events retained and recalled at score 1.0 using local Gemma and cached BGE embeddings, but p95 recall stayed above the 600 ms hot-path target at 649.00 ms live and 643.25 ms warm.
- Graphiti/FalkorDB/Neo4j have not yet been accepted for Zoe relational memory. Zoe-specific Graphiti fixtures, a read-only backend probe, and a read-only runtime readiness probe now define the first relationship eval set and availability preflight. A temporary FalkorDB sidecar was reachable, but Zoe's normal Python runtime lacks Graphiti backend packages and the current local Gemma endpoint failed Graphiti structured extraction in a smoke test.
- The plan now names Zoe's missing north-star layer: capability profiles, trust/autonomy classes, candidate scouting, outcome evals, and hardware-aware promotion.
- Zoe now has an executable capability profile contract and initial self-model for key chat, memory, graph, governance, escalation, Pi, and device-control surfaces.
- Zoe now has reviewable capability-trust update candidates built only from
  verified outcomes that were admitted and retained through Hindsight.
- Zoe now has a governed in-memory capability-trust reviewer that can accept or
  reject retained-outcome trust candidates for existing profiles without
  writing profile files or mutating production runtime state.
- Zoe now has a deterministic capability-profile promotion manifest gate and
  pure patch writer. Clean trust reviews plus PR, rollback, and verification
  refs are required before Zoe can render applyable profile promotion records
  or deterministic source diffs.
- Zoe now has an inert Multica handoff packet builder, runtime-callable
  orchestration plan for governed capability profile promotions, a composed
  retained-outcome-to-profile-handoff path, an operator-approved ticket writer
  gate, and a gated Multica ticket writer: approved retained outcomes can be
  converted into trust candidates, reviewed in memory, rendered as promotion
  manifests and patch plans, packaged as inert handoff packets, validated
  against approval and hash evidence, and submitted as backlog tickets only
  through the profile ticket writer. Created profile tickets can now be
  validated against current source, embedded patch/manifest hashes, PR refs,
  rollback refs, verification refs, and Greptile evidence before a profile-edit
  PR is prepared; the operator CLI can emit a JSON PR-edit plan or render the
  reviewed patch only when the gate allows it, and blocked plans carry no patch
  text. Verified profile-edit outcomes can now be converted into admission-gated
  memory candidates and capability trust evidence without writing durable memory
  or mutating profile files.
- Zoe now has candidate scoring for comparing Pi, MCP, GitHub, skill, API, local-service, and existing-Zoe options before adoption.
- Zoe now has a runtime self-evolution intake helper that packages Notice/Search/Evaluate evidence into validated, review-only proposal rows with ranked candidate evidence and Multica payloads without writing DB rows, creating tickets, or executing anything.
- Zoe now has a self-evolution proposal contract that attaches signals, candidate scores, affected capabilities, approval gates, verification, rollback, and evidence before any execution.
- Zoe now has an observation/evaluation trace schema for recall, retain candidates, admission, contradiction, fallback, proposals, verification, outcome evals, and hardware budget evidence.
- Zoe now has an inert evolution outcome memory builder that converts
  verified, failed, or retired proposal outcomes plus trace evidence into
  pending self-evolution memory candidates.
- Zoe now has an inert evolution outcome admission bridge that evaluates those
  pending candidates through memory admission before any durable backend writer
  can promote them, plus an admitted Hindsight retain bridge for verified
  outcomes.
- Zoe now has an inert memory admission contract that keeps retain candidates pending until evidence, successful admission/verification traces, and approval refs allow durable/trusted memory writes.
- Hindsight retain candidates now close the MemoryService metadata loop for
  scope-bearing memory events: `scope`, legacy `visibility`, `event_id`,
  `evidence_refs`, and `relationships` reach the writer boundary as
  first-class metadata instead of only candidate-prefixed fields or tags.
- Zoe now has a read-only Pi runtime probe and policy contract; actual Pi execution is still blocked until Node/npm/Pi and local/offline model configuration are present and approved.
- The deterministic memory router now has a disabled-by-default runtime status gate, optional non-persistent observation trace packets, and a governed non-persistent trace collector; it is not yet used for prompt injection or backend recall in production chat.
- Retain-candidate admission has a tested contract, and Hindsight retain
  payloads can now be planned and executed only from approved Hindsight
  admission decisions; production chat still does not auto-write them.
- Multica/review records can now be converted into inert memory admission
  decisions: explicit `memory_admission_approved: true` metadata supplies
  approval evidence, blocked review metadata fails closed, and missing approval
  metadata leaves candidates pending.
- The full self-evolution loop is not yet implemented end to end; live proposal writers now store validated Zoe proposal contract snapshots in the legacy `target_patterns` field, Multica admission now refuses approved evolution-proposal tickets unless matching contract markers are present, and execute/promote proposal tickets must pass the execution approval gate before dispatch.
- Zoe now has a deterministic, side-effect-free self-evolution execution gate that fails closed unless execute/promote proposals carry matching approval evidence for each required approval class.
- Zoe main engine cleanup should not begin until the inventory, graph, and memory/evolution contracts have protective tests around the active surfaces.

## Phase Ledger

| Phase | Status | Evidence | Next PR Slice |
| --- | --- | --- | --- |
| Strategy and ADRs | Complete | `docs/strategy/zoe-evolution-harness-plan.md`, `docs/adr/ADR-zoe-memory-layer.md`, `docs/adr/ADR-hindsight-bakeoff.md`, `docs/adr/ADR-graphiti-bakeoff.md` | Keep updated only when decisions change. |
| Zoe naming discipline | Complete for new harness docs/code | Strategy and ADRs use Zoe system names and keep external inspirations out of concrete identifiers. | Sweep future PRs for accidental non-Zoe module names. |
| Offline-only memory rule | Partial | `HindsightConfig` rejects public/cloud memory LLM and embedding configuration by default; the Hindsight embedding probe verifies local model/service readiness without downloads or writes; the first live Hindsight bake-off used Zoe's local Gemma endpoint plus cached local `BAAI/bge-small-en-v1.5` embeddings. | Keep Hindsight disabled by default, and require the embedding/sidecar probes before every live bake-off or rollout. |
| Memory contract | Complete foundation | `services/zoe-data/zoe_memory_contract.py` and `services/zoe-data/tests/test_zoe_memory_contract.py`. | Extend only when a measured backend requires a new field or relationship. |
| Memory layer map | Complete foundation | `services/zoe-data/zoe_memory_layers.py` and `services/zoe-data/tests/test_zoe_memory_layers.py`. | Link layer decisions to runtime config and status endpoints. |
| Memory router | Partial with cached packet measurement | `services/zoe-data/zoe_memory_router.py`, `services/zoe-data/zoe_memory_router_runtime.py`, `services/zoe-data/zoe_memory_prompt_packet_measure.py`, `services/zoe-data/tests/test_zoe_memory_router.py`, `services/zoe-data/tests/test_zoe_memory_router_runtime.py`, `services/zoe-data/tests/test_zoe_memory_prompt_packet_measure.py`, and `/api/system/memory-router/status` expose disabled-by-default observe-only route decisions with optional non-persistent trace packets routed through the governed collector. `compile_cached_memory_prompt_packet()` is separately gated by `ZOE_MEMORY_PROMPT_PACKET_PREVIEW_ENABLED`, compiles compact cited lines only from caller-supplied cached rows, rejects uncited/cross-user rows, and still cannot inject prompts or write memory. Live synthetic `MemoryService.load_for_prompt` + packet compile measurement scored all packets cited/safe with p95 compile 2.71 ms and p95 total 22.53 ms, then cleaned up 7 synthetic rows after the real-user seed guard rejected non-synthetic IDs. | Add a separate chat-integration proposal only after explicit timeout, fallback, and user-correction guards; keep live Hindsight sidecar recall async/cached-only until p95 clears budget or routing budget changes. |
| MemPalace baseline | Complete expanded foundation | `services/zoe-data/mempalace_baseline.py`, `scripts/maintenance/mempalace_baseline.py`, and `docs/architecture/zoe-mempalace-baseline.md` record a repeatable local benchmark; the expanded relational/supersession run scored 1.0 avg/min across 7 cases with p95 179.41 ms and cleaned up 7 synthetic rows. | Use this expanded case set as the comparison floor for Hindsight and Graphiti-style backends; add harder multi-hop/adversarial cases only after backend parity is measurable. |
| Hindsight bake-off | Partial with budget-matrix evidence | Offline sidecar client, retain-candidate helpers, synthetic fixtures, measured runner, read-only probes, availability doc, admission bridge, admitted retain-plan gate, admitted retain executor, and tests exist. First live offline sidecar retained 4/4 synthetic events with 0 extraction errors and recalled 4/4 cases at score 1.0 using local Gemma plus cached local BGE embeddings. Live p95 recall was 649.00 ms and warm p95 was 643.25 ms. A later low/mid budget matrix retained 4/4 synthetic events, recalled 8/8 cases at score 1.0, and measured aggregate p95 657.52 ms, low p95 647.14 ms, and mid p95 637.34 ms; the sidecar sampled about 936.9 MiB at cleanup. | Treat Hindsight as validated for offline experience recall but not accepted for hot-path prompt recall; next measure cached prompt packets or async routing instead of direct prompt-time sidecar recall. |
| Graphiti bake-off | Partial with blocker evidence | ADR plus `services/zoe-data/graphiti_bakeoff.py`, `services/zoe-data/graphiti_sidecar_probe.py`, `services/zoe-data/graphiti_runtime_probe.py`, tests, and `docs/architecture/zoe-graphiti-fixtures.md` define the first relationship fixture set, backend availability preflight, and runtime readiness gate. FalkorDB was reachable in a temporary local sidecar, but Zoe's normal Python runtime lacks Graphiti backend packages and the current local Gemma endpoint failed Graphiti structured extraction in a smoke test. | Create an approved optional-dependency/runtime proposal, then solve the local structured-output extraction path before any accepted fixture ingest/query measurements. |
| Graphify current map | Stale after quota-blocked and local-backend refresh attempts | `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json` were last successfully regenerated through source commit `75f5345d`. A post-`18b8bd5` OpenAI refresh attempt scanned 599 code files and 257 docs but all 7 semantic chunks failed with `insufficient_quota`; the partial output was discarded. A post-`015a529` local Gemma attempt reached AST extraction for 601 code files and 257 docs through Zoe's localhost llama.cpp server, then was stopped after repeated context splits and three invalid JSON chunks; no partial graph output was committed. `scripts/maintenance/graphify_local_probe.py` now provides an offline/local probe that strips cloud keys, runs in a temporary fixture or snapshot, and fails closed on invalid JSON, truncation, missing graph output, or quota signals. `scripts/maintenance/graphify_local_refresh.py` is the sync-capable local wrapper: it requires an accepted clustered repo probe and required graph/report files before `rsync`; otherwise it writes the refresh marker and leaves the committed graph untouched. The Graphify timer template now calls the local wrapper instead of the legacy OpenAI-backed refresh script. A post-`5c9ef0f` repo-mode probe scanned 603 code files and 257 docs, completed AST extraction, then rejected the run after a 300 second timeout, 11 context split warnings, three invalid JSON chunks, and one truncated chunk; temporary graph output was not accepted or committed. A later local model matrix accepted Gemma 4 E2B, E4B, and 12B for scoped `services/zoe-data` extraction with 290 code files, 10 docs, no invalid JSON/truncation/context splits, and E4B fastest at 47.087 s. A full-repo E4B local refresh after `3dd3218` still rejected after the 1800 second timeout with AST extraction complete over 617 code files and 257 docs, temporary graph output present, 58 context splits, 26 invalid JSON chunks, and 6 truncated chunks; the wrapper did not sync partial `graphify-out` output. The first shard matrix smoke run accepted `scripts/maintenance` with E4B in 148.736 s over 45 code files and 1 doc, with no invalid JSON/truncation/context splits. | Use the probe, model matrix wrapper, shard matrix wrapper, and refresh wrapper as the acceptance gate while building a sharded local/offline Graphify refresh lane with bounded chunks, JSON repair/validation, and partial-output discard, then rerun Graphify before major cleanup. |
| Tool/capability inventory | Complete foundation | `docs/architecture/zoe-tool-capability-inventory.md` maps agent, MCP, Multica, Hermes, OpenClaw, and governance surfaces. | Keep updated when tool catalogs or execution lanes change. |
| Memory read/write inventory | Complete foundation | `docs/architecture/zoe-memory-read-write-inventory.md` maps MemoryService operations, durable writes, prompt reads, MCP paths, Hindsight candidates, and metadata gaps. | Keep updated when memory write/read paths change. |
| Zoe north-star layer | Complete foundation | `docs/strategy/zoe-evolution-harness-plan.md` and `docs/adr/ADR-zoe-north-star-layer.md` define capability profiles, trust/autonomy classes, discovery scoring, outcome evals, and hardware budgets. | Build capability profile inventory and candidate-scoring records. |
| Capability profiles | Complete first operator outcome loop | `services/zoe-data/zoe_capability_profile.py`, `services/zoe-data/zoe_capability_profile_patch_writer.py`, `services/zoe-data/zoe_capability_profile_promotion.py`, `services/zoe-data/zoe_capability_profile_promotion_handoff.py`, `services/zoe-data/zoe_capability_outcome_profile_handoff.py`, `services/zoe-data/zoe_capability_profile_multica_handoff.py`, `services/zoe-data/zoe_capability_profile_ticket_gate.py`, `services/zoe-data/zoe_capability_profile_ticket_writer.py`, `services/zoe-data/zoe_capability_profile_pr_edit_gate.py`, `services/zoe-data/zoe_capability_profile_edit_outcome.py`, `scripts/maintenance/capability_profile_pr_edit_workflow.py`, `services/zoe-data/zoe_capability_trust_update.py`, `services/zoe-data/zoe_capability_trust_review.py`, tests, and `docs/architecture/zoe-capability-profiles.md` define the first executable Zoe capability self-model, retained-outcome trust-update candidates, governed in-memory trust reviews, promotion manifests with PR/rollback/verification evidence, pure source diff rendering, inert profile handoff orchestration, retained-outcome-to-handoff composition, inert Multica handoff payloads, an operator-approved ticket writer gate, a gated ticket writer, a PR-edit preparation gate, a side-effect-free operator CLI, and an admission-gated profile-edit outcome bridge that validates PR, rollback, verification, Greptile, and promotion-manifest evidence before memory/trust evidence can be considered. | Keep every actual profile edit landing through PR review; next wire a real runtime caller only after choosing the owning operator surface. |
| Candidate scoring | Complete foundation | `services/zoe-data/zoe_candidate_scoring.py`, `services/zoe-data/tests/test_zoe_candidate_scoring.py`, and `docs/architecture/zoe-candidate-scoring.md` define candidate scoring and adoption gates. | Attach candidate scores to self-evolution proposal records before installs or replacements. |
| Pi runtime harness | Partial | `services/zoe-data/pi_runtime_probe.py`, `scripts/maintenance/pi_runtime_probe.py`, and `docs/architecture/zoe-pi-runtime-harness.md` detect Pi readiness without installing or executing Pi. Current Zoe host lacks Node/npm/Pi. | Create an approved install/runtime proposal with local model evidence before delegated Pi execution. |
| Observation/evaluation traces | Complete foundation | `services/zoe-data/zoe_observation_trace.py`, `services/zoe-data/zoe_observation_trace_collector.py`, `services/zoe-data/zoe_evolution_outcome_memory.py`, tests, `docs/architecture/zoe-observation-trace-schema.md`, and `docs/architecture/zoe-observation-trace-collector.md` define trace records, non-persistent collection gates, and pending outcome-memory candidates for memory route decisions, recall, retain candidate, admission, contradiction, fallback, proposal, verification, outcome eval, and hardware-budget events. | Wire runtime callers through the collector, then pass outcome memory admission decisions into durable writer gates. |
| Memory admission governance | Complete foundation | `services/zoe-data/zoe_memory_admission.py`, `services/zoe-data/hindsight_retain_candidates.py`, `services/zoe-data/hindsight_retain_executor.py`, `services/zoe-data/zoe_multica_memory_admission.py`, `services/zoe-data/zoe_evolution_outcome_admission.py`, `services/zoe-data/zoe_evolution_outcome_retain.py`, `services/zoe-data/zoe_capability_profile_edit_outcome.py`, tests, and `docs/architecture/zoe-memory-admission-gates.md` define evidence, trace, approval, graph, Multica review metadata, outcome memory admission, Hindsight admitted retain planning/execution, admitted outcome retain execution, profile-edit outcome admission, and self-evolution proposal gates before durable memory writes; Hindsight retain candidates, Multica review records, terminal evolution outcomes, and verified profile-edit outcomes can now build/evaluate admission requests before promotion, Hindsight candidate metadata reaches MemoryService writer metadata with first-class scope/evidence/relationship fields, and `admit_hindsight_retain_candidate(event_id)` gives future runtime code an inert no-write pending-review entrypoint instead of direct sidecar retain calls. | Route future MemPalace, Graphiti, and non-Hindsight outcome-memory durable writers through admission decisions before any backend write. |
| Multica evidence gates | Partial | Implement completion now requires PR evidence for code/default profiles, memory admission has a tested decision contract, Multica admission requires matching Zoe proposal contract markers before dispatching evolution-proposal tickets, execute/promote proposal tickets must pass the execution approval gate before dispatch, Multica memory review metadata can be evaluated as an inert admission decision, retained capability-trust candidates can be reviewed in memory with explicit approval refs, profile promotion manifests/patch plans require PR/rollback/verification refs, governed profile promotions can build inert Multica handoff packets, approved retained outcomes can be composed into inert profile handoff plans, ticket payloads require an operator-approved profile ticket gate, profile handoff ticket creation is routed through the gated writer, and profile-file patch preparation requires ticket metadata, current-source hash, PR, rollback, verification, and Greptile refs. | Keep actual profile edits PR-backed and wire patch application only through the PR edit gate. |
| Self-evolution proposal records | Runtime intake foundation wired | `services/zoe-data/zoe_evolution_proposal.py`, `services/zoe-data/zoe_evolution_proposal_adapter.py`, `services/zoe-data/zoe_evolution_runtime_intake.py`, tests, and `docs/architecture/zoe-evolution-proposal-contract.md` define structured Notice -> Explain -> Search -> Evaluate -> Propose records. MCP proposal creation, nightly NOTICE proposals, user-frustration proposals, explicit user issue reports, and side-effect-free runtime intake payloads now store or produce validated contract snapshots while preserving the legacy row shape and MEASURE target patterns; Multica tickets receive compact contract markers for admission. | Adopt the runtime intake helper in one live caller at a time, then require approval evidence before installs/replacements and connect verified outcomes back to memory/capability trust. |
| Self-evolution loop | Partial | Multica, pipeline evidence, Greploop, Greptile, Hermes, worktree bootstrap, candidate scoring, proposal contract, side-effect-free runtime intake payloads, contract-aware Multica admission, live execution approval gating for execute/promote proposal tickets, inert outcome-memory candidate construction, outcome admission decisions, admitted Hindsight outcome retain, retained-outcome capability trust candidates, governed in-memory trust reviews, promotion manifests, profile patch plans, runtime-callable profile handoff orchestration, composed retained-outcome profile handoff planning, inert profile Multica handoffs, an operator-approved profile ticket gate, gated profile ticket creation, a side-effect-free operator PR-edit workflow, and an admission-gated profile-edit outcome bridge exist. | Wire a real operator/runtime caller for profile-edit outcomes only after its owning surface and approval evidence are explicit. |
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
   - Status: stale after source commit `75f5345d`; later OpenAI refresh attempts are blocked by `insufficient_quota`.
   - Latest blocked OpenAI attempt after `18b8bd5` scanned 599 code files and 257 docs; all 7 semantic chunks failed, and the partial graph output was discarded.
   - Local Ollama/Gemma completed a semantic smoke test. A post-`015a529` full-repo local attempt scanned 601 code files and 257 docs, reached AST extraction, then was stopped after repeated context splitting and three invalid JSON chunks.
   - A post-`5c9ef0f` repo-mode local probe scanned 603 code files and 257 docs, completed AST extraction, and rejected the run after a 300 second timeout, 11 context split warnings, three invalid JSON chunks, and one truncated chunk.
   - Use the E4B full-repo rejection evidence and shard matrix wrapper to measure bounded local/offline Graphify slices before treating Graphify as current for major cleanup.
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
   - Status: runner, read-only probes, availability baseline, and first live offline retain/recall bake-off complete.
   - Result: 4/4 synthetic events retained and recalled at score 1.0 using local Gemma and cached BGE embeddings.
   - Gap: live p95 649.00 ms and warm p95 643.25 ms are above the 600 ms hot-path target; container memory was about 1 GiB.
   - Next: test lower budgets, caching, or async routing and record whether p95 can clear 600 ms.
   - Keep it out of production chat until feature-flagged and measured.
6. Graphiti relational bake-off.
   - Status: fixture foundation, read-only backend probe, and read-only runtime readiness probe complete.
   - Evidence: FalkorDB can be reachable as a local sidecar, but Zoe's normal Python runtime lacks Graphiti packages and current local Gemma structured extraction failed during smoke testing.
   - Next: approve optional local dependencies and structured-output compatibility work, then run FalkorDB fixture ingest/query measurements; test Neo4j only if feasible.
   - Decide sidecar/remote/hot-path eligibility from evidence.
7. Runtime memory router feature flag.
   - Status: observe-only runtime status foundation complete.
   - `zoe_memory_router_runtime.py` and `/api/system/memory-router/status` expose disabled-by-default route decisions without prompt injection or writes.
   - Next: wire prompt-time recall in fallback-safe mode with latency timeout and compact cited memory packets.
8. Memory admission governance.
   - Status: complete foundation with Multica review and outcome memory bridges.
   - `zoe_memory_admission.py` now gates durable memory writes on approval refs, successful admission/verification traces, graph edge requirements, user/scope matching, and proposal context for self-evolution memories.
   - Hindsight retain candidates can now build and evaluate admission requests before any durable promotion.
   - Hindsight sidecar retain payloads can now be planned and executed only
     from matching admission decisions that allow durable Hindsight writes.
   - Multica memory review metadata can now be converted into the same inert decision: missing approval stays pending, explicit approval creates approval evidence, and blocked reviews fail closed.
   - Terminal proposal outcomes can now be converted into pending memory
     candidates, evaluated through admission, and retained to Hindsight only
     when the outcome admission decision approves that backend.
   - Next: route future MemPalace, Graphiti, and non-Hindsight outcome-memory durable writers through admission decisions before backend writes.
9. Self-evolution proposal records.
    - Status: foundation complete and live proposal writers now emit validated contract snapshots into `target_patterns`; side-effect-free runtime intake can package Notice/Search/Evaluate signals plus ranked candidate evidence into the same review-only row shape; Multica admission requires matching contract markers before dispatching evolution-proposal tickets.
    - Next: adopt the runtime intake helper in one live caller at a time, then require approval evidence before installs/replacements and connect verified outcomes back to memory/capability trust.
    - Keep execution approval-gated.
10. Zoe capability profile inventory.
    - Status: complete foundation for key active and candidate surfaces, with governed promotion manifests, pure patch plans, and inert Multica handoff packets.
    - Expand profiles for voice, MCP, calendars, reminders, lists, search, maps/charts, proactive scheduling, and setup flows.
    - Use trust level, approval class, tests, budget, dependencies, rollback, known failures, and handoff evidence as proposal/cleanup gates.
11. Candidate discovery scoring.
    - Status: complete foundation.
    - Candidate records now compare Pi, MCP, GitHub, local skills, APIs, local services, and existing Zoe capabilities.
    - Next: attach scores to self-evolution proposals and require them before installs/replacements.
12. Pi runtime harness.
    - Status: read-only probe and policy contract complete.
    - Next: approved install/runtime proposal for Node/npm/Pi plus local/offline model config.
    - Keep Pi execution disabled until the probe reports available runtime and Multica approves use.
13. Outcome eval trace schema.
    - Track task completion, correction handling, continuity, friction, latency, trust, cleanup quality, and hardware fit.
    - Convert recurring failures into Notice records.
    - Convert terminal proposal outcomes into pending self-evolution memory
      candidates, not durable trusted facts.
    - Evaluate terminal outcome candidates through memory admission before any
      backend writer can promote them.
14. Main engine cleanup.
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
