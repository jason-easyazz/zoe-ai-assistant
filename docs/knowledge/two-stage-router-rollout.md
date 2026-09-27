---
type: Runbook
title: Two-stage router rollout
description: Staged rollout of the two-stage router (SetFit shortlist head + FunctionGemma sidecar) behind ZOE_ROUTER_HEAD — stages, verification checklist, rollback, and where the numbers land.
tags: [router, rollout, functiongemma, setfit, operations]
timestamp: 2026-09-27T21:00:00Z
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

## Stage-1 heads are numpy (since 2026-09-27)

zoe-data does not import scikit-learn, scipy or joblib. Both stage-1 heads
(`router_head_logreg` 13×384 multinomial logreg; `router_head_mlp`
384→256 relu→13 softmax) are served from their numpy export by
`services/zoe-data/router_heads_numpy.py`:

| file (`services/zoe-data/models/`) | role |
|---|---|
| `router_head_*.joblib` | TRAINING artefact (sklearn 1.7.2, `labs/setfit-router`); source of the export; loaded only by the fallback |
| `router_head_*.npz` | the served weights — original dtypes, no pickle, byte-deterministic |
| `router_head_*.json` | architecture, `classes`, source joblib sha256, npz sha256 (checked at load — a tampered or stale npz is refused and the head disables, non-fatal) |

- Flag `ZOE_ROUTER_HEADS_BACKEND` = `numpy` (default) | `joblib` (the
  pre-2026-09-27 path, kept for **one release** as the escape hatch; needs the
  sklearn/joblib training pins, which zoe-data's manifests still carry because
  librosa declares them). Unknown values fall back to `numpy`.
- Parity when exported: max-abs **0.0** vs sklearn `predict_proba` on 1,291
  embedded corpus utterances (needle 81 + SetFit train set + `ROUTES`) and 2,000
  random vectors. Measured head-load cost: +72.7 MB / 1.23 s → +1.7 MB / 0.012 s.
- Pinned by `services/zoe-data/tests/test_router_heads_numpy.py` (`ci_safe`,
  sklearn-free): parity ≤ 1e-6 on a committed 50-vector fixture, a perturbed-weight
  negative control, the tampered-npz refusal, the joblib-sha drift check, and a
  fresh interpreter proving the live loaders never import sklearn/scipy/joblib.

**Regenerating after a retrain** (every stage-1 retrain — the drift check fails
CI until you do). Run where the training pins are installed (the SetFit lab
venv, or the zoe-data venv while it still carries sklearn 1.7.2 + fastembed):

```
cp labs/setfit-router/artifacts/head_mlp.joblib    services/zoe-data/models/router_head_mlp.joblib
cp labs/setfit-router/artifacts/head_logreg.joblib services/zoe-data/models/router_head_logreg.joblib
python3 scripts/maintenance/export_router_heads.py --corpus \
    --fixture services/zoe-data/tests/fixtures/router_heads_parity.npz
# verify-only (writes nothing): ... export_router_heads.py --check --corpus
```

It refuses to write if any parity value exceeds `--tol` (1e-6) or if its own
negative control cannot go red, and it refuses head types it cannot reproduce
bit-for-bit (a `Pipeline`/scaler, OvR/binary logreg, a `logistic` MLP
activation). Commit the four model files and the fixture together; `models/*`
is voice-path, so the PR needs the replay.

## Sidecar flags (`functiongemma-router.service`, 2026-09-27)

The sidecar stays on llama.cpp **b9733** (`~/llama.cpp/build-jetson-new`, also the
brain's rollback build). Two sizing flags were added to the template and installed
on the box; everything else is unchanged:

| flag | was | now | why |
|---|---|---|---|
| `--cache-ram` | unset = **8192 MiB** default | `64` | The unit has `MemoryMax=1G`. An 8 GiB prompt-cache cap inside a 1 GiB cgroup means the cache never evicts: the cgroup OOM-kills the sidecar first. The corpus alone fills 18 entries / 32 MiB, so the cache does grow with traffic. At 64 MiB it evicts oldest-first. |
| `--ctx-size` | 4096 | `1024` | The longest live routing request was 87 tokens (608 requests, p99 72). 1024 is about 12× headroom. |

`--no-repack` is deliberately NOT used. Without repack the weights are served from
reclaimable file pages, which is the paging failure the unit's `MemorySwapMax=0`
exists to prevent. `tests/unit/test_llama_server_unit_flags.py` pins
`--cache-ram` as a positive cap under a quarter of `MemoryMax`.

Measurement: a fresh restart for each arm, then the 81-case prod-path corpus
(`labs/router-90-campaign/prod_path_eval.py`) run against the live sidecar:

| arm | overall | chat-FP | p50 / p90 ms | per-case decisions | VmRSS idle → after corpus (MiB) | RssAnon after (MiB) | cgroup after (MiB) | prompt cache after corpus |
|---|---|---|---|---|---|---|---|---|
| control (4096, no cap) | 91.4 % | 0 % | 369.7 / 494.4 | — | 599 → 680 | 337 | 268 | 18 prompts, 32.0 MiB (limit 8192) |
| new (1024, cap 64) | 91.4 % | 0 % | 381.4 / 509.5 | **identical** (all 81, args included) | 503 → 651 | 321 | 250 | 18 prompts, 32.0 MiB (limit 64) |

The RSS saving is small, about 16-19 MiB anonymous or cgroup. The latency
difference is inside run-to-run noise: the long-lived pre-restart instance
measured 377.6 ms p50 on the same corpus. The point of the change is the bound,
not the bytes.

Rollback: `~/.cache/zoe/functiongemma-router.service.pre-cache-ram` (the unit as
it was on the box), then `daemon-reload` and `restart functiongemma-router`.

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
