# ADR: Zoe North Star Layer

## Status

Accepted.

## Context

The Zoe evolution harness already defines memory contracts, routing, bake-offs, evidence gates, and a self-evolution pipeline. That is the machinery. It does not fully define the product compass Zoe needs to become a coherent local-first assistant with continuity, useful initiative, and safe self-improvement.

Without a north-star layer, Zoe can accumulate tools and memories without knowing which capabilities are trusted, which are expensive, which are duplicated, which improve user outcomes, and which should be retired.

## Decision

Add a Zoe north-star layer to the architecture. This is not a new runtime service yet. It is a required design layer that future PRs must make executable through small artifacts:

- capability profiles for tools, routes, skills, agents, sidecars, devices, and execution lanes;
- trust/autonomy classes for observe, recall, suggest, prepare, execute, and promote;
- candidate discovery scoring for Pi, MCP, GitHub, local skills, APIs, and existing Zoe tools;
- outcome evals for task completion, correction handling, continuity, friction, latency, trust, cleanup quality, and hardware fit;
- hardware/runtime budgets for Jetson CPU, RAM, GPU, latency, model use, and local/offline viability;
- promotion and retirement rules based on evidence.

Concrete system identifiers must use Zoe names. External inspirations remain product vision and research inputs, not module or layer names.

## Consequences

Zoe's future cleanup work should not remove or replace active tools based only on intuition. It should use capability profiles, candidate comparisons, usage/failure evidence, and rollback plans.

Zoe's self-evolution loop should prefer discovering and evaluating existing abilities before building new ones. Pi, MCP servers, GitHub projects, local skills, Hermes capabilities, OpenClaw fallback paths, and APIs should be scored with the same candidate record.

Zoe may observe and suggest aggressively, but execution, durable trusted memory, privileged capability promotion, device control, secret use, installs, and cleanup remain approval-gated.

## Acceptance Criteria

- Every trusted capability has a profile with owner surface, trust level, approval requirement, offline mode, dependencies, tests, budget, evidence, known failures, and rollback.
- Candidate adoption records include fit, project activity, stars, license, offline viability, security, footprint, test quality, maintenance burden, and overlap with existing Zoe abilities.
- Outcome evals can show whether Zoe improves task completion, continuity, correction handling, trust, latency, cleanup quality, and hardware fit.
- Failed outcome evals can become Notice records for the self-evolution loop.
- Cleanup proposals include measured non-use, replacement, failure-rate, or maintenance-cost evidence.
- No cloud model dependency is introduced for Zoe memory.

## Initial PR Slices

1. Add a generated or maintained capability profile inventory for active Zoe surfaces.
2. Add a candidate-scoring template for Pi/MCP/GitHub/skill/API adoption.
3. Add outcome eval trace schema.
4. Wire recurring outcome failures into Notice records.
5. Use capability profiles as a gate before main engine cleanup.
