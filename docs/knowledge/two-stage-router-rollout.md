---
type: Runbook
title: Two-stage router rollout
description: Staged rollout of the two-stage router (SetFit shortlist head + FunctionGemma sidecar) behind ZOE_ROUTER_HEAD — stages, verification checklist, rollback, and where the numbers land.
tags: [router, rollout, functiongemma, setfit, operations]
timestamp: 2026-07-14T00:00:00Z
---

# Two-stage router rollout

Operator runbook for taking the two-stage router (SetFit **mlp** shortlist
head, chat gate **0.5**, + FunctionGemma **functok-r2** Q8 GGUF on the :11436
sidecar with the shortlist GBNF grammar — offline **90.1% / 100% canonical /
0% chat-FP / 424 ms p50**, see `labs/router-90-campaign/HANDOFF.md`) from
dark to live, behind the `ZOE_ROUTER_HEAD` flag in `services/zoe-data/.env`.

Driver: **`scripts/maintenance/router_rollout.sh`** — run it on the box from
the live checkout. It pre-flights, flips the flag, restarts `zoe-data.service`,
verifies, and **auto-restores the previous flag value on any failure** (the
flag is never left half-set). All names (flag, units, ports, log paths) are
env-overridable; defaults are documented in the script header.

## Stages

| Stage | Flag | What runs | Risk |
|---|---|---|---|
| off (default) | `ZOE_ROUTER_HEAD=off` | live Tier-0/1 router only | none |
| shadow (#1318) | `shadow` | stage-1 SetFit head logs would-be route | none (observe-only) |
| **shadow2** | `shadow2` | FULL two-stage pipeline off the hot path; logs would-be route + latency vs actual | none (observe-only) |
| **active** | `active` | two-stage router decides the route | live routing changes |

Never skip shadow2: it is the only place the sidecar's live latency and the
would-be fallback rate are measured against real traffic before cutover.

## Pre-flight (run automatically before every stage)

```
scripts/maintenance/router_rollout.sh --preflight
```

- live checkout (`/home/zoe/assistant`) on `main` and 0 commits behind origin
- sidecar user unit installed **and active**, `/props` on :11436 mentions the
  expected GGUF (`functiongemma-270m-zoe-functok-r2`)
- brain llama-server healthy on :11434
- zoe-data healthy on :8000

## Stage shadow2

```
scripts/maintenance/router_rollout.sh --stage shadow2
```

Sets the flag, restarts, verifies health, POSTs 3 synthetic utterances to
`/api/chat/?stream=false`, and confirms shadow2 log lines appear
(`services/zoe-data/data/router_head_shadow.jsonl`, or the zoe-data journal).

Let it soak on real traffic (a day of panel/chat/Telegram turns), then score:

```
python3 scripts/maintenance/router_shadow2_report.py
```

**Go/no-go for active** (mirrors the offline ship point):
- agreement vs actual route ≳ 90%
- shadow2 tool-call on actual-chat turns ~0% (chat-FP is the cardinal sin)
- would-be total latency p50 well under 1 s
- would-be fallback rate (actual tool, shadow2 abstains) low and explained

## Stage active

```
scripts/maintenance/router_rollout.sh --stage active
```

Post-deploy checks the script runs:
- canonical command probe (default "add rollout probe to my shopping list",
  override `ROLLOUT_CMD_UTTERANCE`) returns HTTP 200 **sub-second**
  (`ROLLOUT_ACTIVE_MAX_MS`, default 1000) and the router log shows a
  non-chat route
- 3 chat utterances return normally with **no tool route** in the log

Then, before calling the stage done (MANDATORY, root `AGENTS.md`): the router
is on the voice path, so **replay-gate** against `~/.zoe-voice-samples` —
`scripts/maintenance/voice_regression_probe.py` under
`flock /tmp/zoe-voice-harness.lock`. Said-vs-did and per-stage speed must not
regress.

## Rollback

```
scripts/maintenance/router_rollout.sh --rollback
```

Flag → `off`, restart, health-verified. Same instant-env-rollback pattern as
the flue cutover. Any failure mid-stage triggers the same restore
automatically via the script's trap. Roll back with the flag, never by
uninstalling the sidecar mid-incident (it is inert when the flag is off).

## Where the numbers land

- shadow2 JSONL: `services/zoe-data/data/router_head_shadow.jsonl`
  (summary: `router_shadow2_report.py`)
- stage-1 shadow JSONL (#1318): `services/zoe-data/data/router_head_shadow.jsonl`
  (summary: `router_shadow_report.py`)
- quick state: `router_rollout.sh --status`
- offline baselines: `labs/router-90-campaign/results/` (ship point
  `r2-gb-mlp-g0.5.json`)

## Contract caveat

This tooling was written against the contract in
`labs/router-90-campaign/HANDOFF.md` while the shadow2/active integration PR
was in flight. If that PR shipped different names (flag values, sidecar unit
name, log path or record fields), override via the `ROLLOUT_*` env vars — the
report script already tolerates several route/latency key spellings — and
update this runbook to the as-shipped names.
