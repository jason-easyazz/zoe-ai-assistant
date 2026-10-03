---
type: Runbook
title: Two-stage router rollout
description: Staged rollout of the two-stage router (SetFit shortlist head + FunctionGemma sidecar) behind ZOE_ROUTER_HEAD — stages, verification checklist, rollback, and where the numbers land.
tags: [router, rollout, functiongemma, setfit, operations]
timestamp: 2026-09-28T20:30:00Z
---

# Two-stage router rollout

Operator runbook for taking the two-stage router (SetFit **mlp** shortlist
head, chat gate **0.5**, + FunctionGemma **functok-r2** Q8 GGUF on the :11436
sidecar with the shortlist GBNF grammar — offline **90.1% / 100% canonical /
0% chat-FP / 424 ms p50**, see `labs/router-90-campaign/HANDOFF.md`) from
dark to live, behind the `ZOE_ROUTER_HEAD` flag in `services/zoe-data/.env`.
Since 2026-09-28 a **low-confidence floor** (`ZOE_ROUTER_HEAD_MIN_CONF`, 0.70)
sits on top of the 0.5 chat gate — see *Low-confidence floor* below; with it
the offline 81-case number is **84.0%** (same 0% chat-FP).

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

## Low-confidence floor (`ZOE_ROUTER_HEAD_MIN_CONF`)

**Why.** Samantha bar S1 (2026-09-28 16:29, reproduced 19:37) asked *"Who is
flying in on Thursday, and where from?"*. The head's top class was `people` at
**0.5371**, with shortlist people/calendar/reminders. That passed the 0.5 chat
gate, and the decoder picked `show_calendar`. The result was a deterministic
calendar reply in 488 ms. The brain and the recall packet never saw a
personal-recall question. The 0.5 gate only asks "is this chat?". Nothing asked
"is the head sure enough to give this turn to a deterministic tool?".

**Rule** (`router_two_stage.gate_reason`). If the head's top class is not chat
and `gate <= head_conf < ZOE_ROUTER_HEAD_MIN_CONF`, the router abstains
**before** the sidecar call. The decision is `tool=None, domain=chat,
gated=true, reason=low_conf`, and the turn goes to the chat lane (brain +
recall packet).

- Every gated decision now carries `reason` (`chat_top` | `below_gate` |
  `low_conf`). It appears in the decision dict and in the `router_two_stage` log
  line and shadow JSONL.
- Default **0.70**. **`0` restores the pre-2026-09-28 behaviour exactly.** An
  unparseable, non-finite or out-of-range value (outside 0.0–1.0 — `1.70` would
  abstain every tool decision) logs a WARNING and keeps 0.70.
- The voice Skybridge router gate (`skybridge_service.skybridge_router_gate`)
  treats a `low_conf` abstain as a chat verdict and **vetoes** the fast path;
  only `below_gate` (the 0.5 gate) keeps its `router_unsure` allow. Otherwise
  an S1-shaped ask with a calendar cue would get the deterministic calendar
  reply back from Skybridge.
- No per-domain overrides. No domain had enough low-confidence decisions (at
  most 14 each) to justify its own number.
- The downstream expert per-domain thresholds
  (`expert_dispatch._DEFAULT_THRESHOLDS`, based on similarity score) are
  unchanged.

**Measurement** (2026-09-28, offline). This measures the ACTIVE decision: the
head gate plus the live r2 sidecar decode, using the production grammar, prompt
and parser. Nothing was retrained. Every input is held out:

- the frozen 81-case needle corpus, scored by the SHIPPED head;
- the 1,121-row SetFit train set, scored as **5-fold out-of-fold** predictions.
  This uses the same MLP recipe as `labs/setfit-router/train.py` with
  `StratifiedKFold(5, random_state=0)`, so each utterance is scored by a head
  that never saw it;
- the synthetic Samantha-bar asks.

That gives 1,037 labelled tool decisions and 0 sidecar errors. The rig
reproduces the campaign numbers exactly: 90.1% at floor 0.5 and 84.0% at 0.7.

In the table below, "right" means the decoded tool's domain equals the label.
People and memory count as one domain, because the stage-1 and stage-2 label
sets disagree on person facts.

| head_conf band | n | right | precision |
|---|---|---|---|
| 0.50–0.55 | 19 | 11 | 57.9% |
| 0.55–0.60 | 15 | 9 | 60.0% |
| 0.60–0.65 | 14 | 10 | 71.4% |
| 0.65–0.70 | 15 | 9 | 60.0% |
| 0.70–0.75 | 18 | 14 | 77.8% |
| 0.75–0.80 | 21 | 19 | 90.5% |
| 0.80–0.90 | 66 | 40 | 60.6% |
| 0.90–0.95 | 61 | 47 | 77.0% |
| 0.95–1.00 | 808 | 715 | 88.5% |

Stage 1 on its own (head top == label, n = 1,089):

- below 0.75: **47.8%** pooled;
- 0.75–0.95: about 75–88%;
- **0.95 and above: 96.7%**. This is the only band that clears 95%.

The out-of-fold dip at 0.80–0.95 comes from the two stages' label sets
disagreeing (e.g. timers "how long left" → `get_time`), not from confidence.

Operating points below use the 81-case frozen corpus, which is the ratchet's
ship metric. A gated tool case scores as a miss even though the brain still
answers it.

| floor | 81-case overall | chat-FP | tool decisions below the floor (pooled precision) | correct fast-tier calls handed to the brain |
|---|---|---|---|---|
| 0.50 (= off) | 90.1% | 0% | — | 0 |
| 0.60 | 88.9% | 0% | 34 (58.8%) | 20 |
| 0.65 | 87.7% | 0% | 48 (62.5%) | 30 |
| **0.70 (default)** | **84.0%** | **0%** | **63 (61.9%)** | 39 |
| 0.75 | 80.2% | 0% | 81 (65.4%) | 53 |
| 0.95 (strict 95%) | 67.9% | 0% | — | — |

**Why 0.70.** It is the knee in the table. Every band below it is right only
58–71% of the time, so about 2 in 5 of those deterministic replies are wrong.
The bands just above it jump to 78–90%. The cost of the floor is latency, not
correctness: those turns reach the brain instead of the fast tier.

**No band below 0.95 meets 95%.** A strict 95% floor would be 0.95, and it
would cost 22 points of fast-tier coverage. That is an operator decision (set
the env var), not this default.

**Samantha-bar asks through the shipped head.** The embeddings are in the
fixture `services/zoe-data/tests/fixtures/router_samantha_probe_vectors.json`.

| ask | head top @ conf | old decision | floor 0.70 |
|---|---|---|---|
| S1 "Who is flying in on Thursday, and where from?" | people @ 0.537 | `show_calendar` ✗ | chat ✓ |
| S2 "Which city do I live in these days?" | weather @ 0.593 | decoder chat escape | chat |
| S6 "Who is flying in on Thursday, and which city do I live in?" | calendar @ 0.728 | `show_calendar` ✗ | **still `show_calendar`** (above the floor) |
| S8 "Remind me, who did I say is flying in on Thursday?" | reminders @ 0.9996 | `recall_memory` (the decoder rescues it) | unchanged |

**The floor binds the keyword lanes too (INTENT_GATE, 2026-09-28).** A `low_conf`
abstain only helps if nothing downstream answers deterministically anyway. After
#1763 went live, S1 was still answered by the chat.py keyword lane ("who is <X>" →
contacts lookup). `fast_tiers.intent_gate` now asks this head's stage-1 verdict
(`router_two_stage.head_verdict`, numpy only) before any keyword intent executes;
`chat_top`/`low_conf` or a confident different domain vetoes it. The contract is in
`services/zoe-data/AGENTS.md`.

The floor does not fix a CONFIDENT stage-1 miss: S6 is at 0.73 and S8 at
0.9996, and no threshold can catch those. They go into the training data
instead (below).

**Feeding misses to training.** Real misroutes are committed to dedicated files.
The dataset builders (`build_dataset.py`, `build_sibling_dataset.py`) regenerate
the main training files, so they cannot overwrite these:

- stage 1: `labs/setfit-router/data/misses.jsonl` (`{text, label}`), read by
  `labs/setfit-router/train.py` alongside `data/train.jsonl`;
- stage 2: `labs/functiongemma-finetune/data/train_misses.jsonl`
  (`{text, tool, args}`), read by `router_selftrain.build_training_set`.

Both files hold the three Samantha-bar asks above, labelled `memory` /
`recall_memory`. That is where the label set already puts "what did I tell you"
recall.

**Nothing is retrained here.**

- The stage-2 retrain is the multi-hour self-train job.
- A stage-1 retrain is `train.py` plus the numpy re-export below. It is
  replay-gated, because `models/*` is on the voice path.
- Caveat: these rows use the Samantha bar's own wording. After a retrain,
  S1/S6/S8 partly measure memorisation, so also score the router on
  paraphrases.

The self-train ratchet still compares like with like: incumbent and candidate
are both measured through `route_two_stage` under the same floor. Its absolute
81-case numbers read about 6 points lower than the pre-floor baselines quoted
in [router-selftrain-loop.md](router-selftrain-loop.md).

**Regenerating the table** after a head retrain:

1. Embed the texts with fastembed `BAAI/bge-small-en-v1.5`.
2. Score the needle corpus with the shipped head, and the SetFit train set with
   5-fold out-of-fold MLPs.
3. Decode every non-gated case through `router_two_stage` against a sidecar
   (`build_grammar` → `_post_sidecar` → `parse_call` → `validate_call`).
4. Bucket the results by `head_conf`.
5. Re-derive the floor from the new table, not from the old default.
6. Regenerate the probe fixture. Its parity test fails until you do.

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
- A custom `ZOE_ROUTER_HEAD_PATH` / `ZOE_ROUTER_HEAD_MLP_PATH` `.joblib`
  **outside** `services/zoe-data/models/` with **no** `.npz`+`.json` beside it is
  loaded via joblib for that head only, with a WARNING (so a working custom head
  is not silently disabled). A **shipped** head with a missing export is logged
  as an ERROR and disabled — a partial deploy never pulls sklearn back in. A
  present but stale/tampered export is always refused.
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

It is all-or-nothing: both heads and the fixture are staged (`*.export-tmp.*`,
next to their destinations) and verified before anything is published, and
publication renames them all or rolls every replaced file back from its
`*.export-bak` link — one failing head, a failed fixture write or a failed
rename leaves the served set exactly as it was. `--check` is strictly read-only (it refuses `--fixture`/`--report`).
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
| `--mlock` (2026-10-03, A1) | unset | set, + `LimitMEMLOCK=infinity`, `MemoryMax` 1G → 1280M | The 278 MB GGUF mapping was measured at **Rss 0 kB** with 7.7 M file refaults (~12 MB per request); journald `total time` p50/p90 was 293/450 ms when the previous request was <10 min earlier and 427/638 ms after a longer gap (n = 2,384, max 1,588 ms > the 1.5 s client timeout). Locked pages are unreclaimable, so the ceiling grows by the GGUF. Evidence: `docs/research/infra-data-config-2026-10-03.md` D1. |

`--no-repack` is deliberately NOT used. Without repack the weights are served from
reclaimable file pages, which is the paging failure the unit's `MemorySwapMax=0`
exists to prevent. Repack alone was not enough, though: the tensors llama.cpp still
serves straight from the mmap stayed file pages and were evicted between turns,
which is what `--mlock` (above) closes. `tests/unit/test_llama_server_unit_flags.py` pins
`--cache-ram` as a positive cap under a quarter of `MemoryMax`, and the lock: spelled
for this build, `LimitMEMLOCK=infinity`, and `MemoryLow` + locked GGUF under `MemoryMax`.

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
