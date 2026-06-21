---
type: Reference
title: Platform Health Check — Loop Contract
description: Loop-Engineering contract for the Platform Health Check autopilot (Multica id 3f9bb428, assignee Hermes, daily).
tags: [autopilots, multica, health, hermes]
timestamp: 2026-06-18T00:00:00Z
---

# Platform Health Check — Loop Contract

Daily Multica autopilot. Id `3f9bb428`, assignee **Hermes**. See the [bundle index](index.md).

## Job
Verify that Zoe's platform is healthy once per day: docker services up, PostgreSQL reachable, and the zoe-data API, Hermes runtime, and OpenClaw runtime responding. Raise a single high-priority issue when a check fails.

## Inputs
- Docker service/container status (`docker compose ps` / health states).
- PostgreSQL connectivity and basic liveness.
- zoe-data HTTP health endpoint.
- Hermes and OpenClaw runtime health/readiness.
- Existing open Multica issues (to avoid duplicates).

## Allowed
- Run read-only health probes against the services above.
- Create one high-priority Multica issue summarising the failed check(s), with the failing component, observed symptom, and probe output.
- Update or comment on the existing health issue for the same component instead of opening a duplicate.

## Forbidden
- NEVER restart, stop, redeploy, scale, or otherwise mutate any docker service, container, or systemd unit.
- NEVER run write/DDL/DML against PostgreSQL or any database; probes are read-only.
- NEVER modify application code, config, secrets, or env files.
- NEVER touch the live checkout or push to `main`; this loop only reads and files issues.
- NEVER open more than one issue per failing component per run, and never escalate priority above high.
- NEVER attempt remediation itself — it reports, a human or a separate engineering task fixes.
- NEVER exceed its scope to "investigate" unrelated subsystems; on an all-green run it exits without creating anything.

## Output
- On all-green: no new issue; an idempotent run record (run completed, all checks passed).
- On failure: exactly one high-priority Multica issue per failing component, naming the component, symptom, and evidence, or an updated comment on the existing issue.

## Evaluation
- Runs to completion well within the 1800s budget.
- Zero false-positive issues on a healthy platform; an issue exists within one cycle of a real failure.
- No duplicate issues for the same ongoing failure.
- No side effects on services, data, or code.
