---
type: runbook
title: Router self-training loop (TRAIN → EVAL → RATCHET → DEPLOY)
description: How Zoe's two-stage router retrains itself on mined real-traffic mistakes and promotes a new model ONLY if it is provably better — the ratchet, the flag, rollback, and where the journal and scoreboard live.
---

# Router self-training loop

Zoe's router improves itself: mistakes from real traffic become training data, the
stage-2 decoder retrains on them, and the new model goes live **only if it is
provably better than the one currently serving**. The promotion ratchet is what
makes this safe instead of drift-prone — an autonomous loop that can promote a
worse model is a loop that will eventually do so.

- **Driver:** `scripts/maintenance/router_selftrain.py`
- **Flag:** `ZOE_ROUTER_SELFTRAIN` (**default off**) — weekly job in zoe-data
- **Subject:** the FunctionGemma-270M sidecar (`functiongemma-router.service`, :11436),
  stage 2 of the two-stage router. The Gemma 4 brain and Moonshine STT — the
  rocks — are never touched.
- **Related:** [Two-stage router rollout](two-stage-router-rollout.md) ·
  [Voice pipeline](voice-pipeline.md) · [Merge & deploy discipline](merge-and-deploy.md)

## The ratchet (the whole point)

A candidate is promoted **only if every one of these holds**. Any failure ⇒ the
candidate is discarded and the incumbent keeps serving.

| Gate | Rule | Why |
|---|---|---|
| `no_accuracy_regression` | candidate overall ≥ incumbent overall | The loop may only move forward. A tie promotes (equal quality, strictly more training signal). |
| `chat_fp_zero` | chat false positives == 0 | A tool call on a conversational turn hijacks the turn. The 92.6% ungated model was *more accurate* and still unshippable at 12.5% chat-FP — accuracy never buys off a chat-FP. |
| `p50_under_budget` | p50 < 600 ms | The router sits in front of every voice turn. |
| `replay_gate_passed` | `voice_regression_probe.py` **ran** and passed | Said-vs-did on Jason's real-voice corpus must not regress. |
| `corpus_intact` | candidate scored the same case count as the incumbent | 100% of 5 cases must not beat 91.4% of 81. |

Plus a **measurement-validity** pre-condition (below): if the box is too contended
for the numbers to mean anything, the run aborts *inconclusive* rather than ruling
on noise.

**There is no `--force-promote`, and there must never be one.** A candidate that
cannot pass the ratchet is not safe to serve; the fix is the model, not the gate.
The script rejects any `--force*` argument outright, and
`tests/unit/test_router_selftrain_gate.py` has a tripwire test that fails if a
bypass symbol ever appears in the source.

### The rig noise is bigger than the signal — measure like-for-like

**Measured on this box, all three with the *same* GGUF:**

| rig | overall | p50 |
|---|---|---|
| live sidecar (warm) | 86.4 % | 488 ms |
| scratch sidecar, box quiet | 87.7 % | 590 ms |
| scratch sidecar, box **contended** | **71.6 %** | 796 ms |

Frozen-corpus accuracy swings **~16 points with load alone**: under contention the
sidecar's decode crosses the router's 1.5 s timeout, the turn becomes an
`error_fallback`, and a correct tool call is scored as a miss. That noise is far
bigger than any improvement a retrain will plausibly produce.

Two consequences, both baked into the loop:

1. **The candidate and the incumbent are measured on the SAME scratch rig,
   back-to-back**, one sidecar at a time. Comparing a candidate-on-scratch against
   an incumbent-on-live would inject that noise straight into the ratchet — and
   would mostly reject good candidates, so the loop would look like it was working
   while silently never improving anything.
2. **The incumbent is also measured live, and the two are compared.** If they
   disagree by more than 10 points the box is too loaded for the corpus to mean
   anything, and the run **aborts INCONCLUSIVE** — it neither promotes nor rejects
   on noise. The incumbent keeps serving.

This is why the job is scheduled for a quiet window (default Sat 01:00), and why
pre-flight refuses to start below ~1.6 GB MemAvailable.

### A skipped replay gate is NOT a pass

`voice_regression_probe.py` **exits 0 when it skips** (e.g. the box is too tight
to replay safely). Reading that exit code as "passed" would let a model go live
having never been replayed. The loop therefore requires a **fresh results file
with samples in it** — `ran` is tracked separately from `passed`, and only
ran-and-passed clears the gate.

Two traps that make the gate quietly *un-passable* (⇒ nothing would ever be
promoted, which looks identical to "no good candidates yet"):

- The probe must run against the **live** `services/zoe-data` (`ZOE_LIVE_SERVICE_DIR`),
  not a worktree copy. `measure_voice` takes a silent skip path (exit 0, no output)
  when the service dir has no `.env` — and a git worktree never has one.
- The probe's artifact cleanup reads **`POSTGRES_URL` from the environment**; a
  failed sweep is a warning-level non-zero exit. The loop loads it from the live
  `.env` so a clean run really does return 0.

### The frozen eval corpus is never a training input

The 81-case corpus (`labs/needle-benchmark/corpus.jsonl`) is what every number in
this loop is measured against. Training on it would make every downstream figure a
lie. Two independent checks run in pre-flight:

1. the miner must record that its held-out guard ran (`meta.heldout_guard.ran`); **and**
2. the loop **independently recomputes** the overlap between the candidate's texts
   and the frozen corpus. The miner's own say-so is not trusted.

Either check failing aborts the run before a single training step.

## Stages

Every stage is recorded in the run journal.

1. **PRE-FLIGHT** — newest `data/router_selftrain/candidate_<UTCSTAMP>.jsonl` +
   its `.meta.json`; held-out guard (above); disk/memory; and a **live
   re-measurement of the incumbent**. The incumbent's score is never read from a
   stale results file — it is whatever the sidecar is actually serving right now.
2. **TRAIN** — warm-start from the production checkpoint lineage on the existing
   training sets **plus** the new candidate. CPU-only (GPU training is impossible
   on this Jetson), `nice`'d, `oom_score_adj=1000` so the live services win any
   memory fight. Stops the brain for the window if memory is too tight and
   **always** restarts it.
3. **EXPORT** — merged checkpoint → GGUF. Uses the pristine-tokenizer recipe:
   `transformers`' `save_pretrained` re-serializes the tokenizer in a way that
   breaks `convert_hf_to_gguf` (`assert max id < vocab_size`), so the original
   tokenizer files are copied back over the merged checkpoint before converting.
4. **EVAL** — the frozen corpus through the **real production router**
   (`semantic_router.route_two_stage`), pointed at a **scratch sidecar on :11437**
   via `ZOE_ROUTER_SIDECAR_URL`. The live :11436 sidecar is never disturbed.
5. **RATCHET** — the table above.
6. **DEPLOY** (only on promote) — archive the outgoing GGUF as last-known-good
   (**copied, never moved** — an LKG must always exist), atomically swap the served
   model file, restart the sidecar unit, verify `/health` + `/props` identity
   (sha256, not a basename match), then **re-run the eval against the LIVE
   sidecar** to confirm the promoted numbers in situ. Any post-deploy failure ⇒
   **auto-rollback** to last-known-good, restart, verify, exit non-zero loudly.
7. **REPORT** — append to the scoreboard.

The loop's **only** production mutation is swapping the sidecar's model file and
restarting that one unit. It never edits the live routing code path.

## Prerequisite: the warm-start checkpoint (do this before the first real run)

The loop warm-starts from the **production lineage** — cold-starting from base
would produce an unrelated model wearing the incumbent's name, so the script
**aborts** rather than do it silently.

**The merged HF checkpoint behind the live r2 GGUF was not preserved** — only the
exported GGUF survives, and a Q8_0 GGUF cannot be converted back into a trainable
checkpoint. So before the first real training run, re-establish the lineage once:

```bash
# retrain from base on the committed datasets, and KEEP the merged checkpoint
python3 labs/functiongemma-finetune/train_lora.py --variant functok --cpu \
    --model-dir /home/zoe/models/lab/functiongemma-270m-it-hf \
    --data labs/functiongemma-finetune/data/train.jsonl \
    --out labs/functiongemma-finetune/runs/functok-r2
# then point the loop at it (default path is exactly this)
export ZOE_ROUTER_WARM_START=labs/functiongemma-finetune/runs/functok-r2/merged
```

**Never delete `runs/<gen>/merged` again** — it is the only thing that makes the
next generation a small step rather than a fresh roll of the dice.

## Running it

```bash
# dry run — mine + train + eval, report the ratchet verdict, promote NOTHING
python3 scripts/maintenance/router_selftrain.py --dry-run

# evaluate an already-exported GGUF without retraining (debug / pipeline smoke)
python3 scripts/maintenance/router_selftrain.py --dry-run --skip-train <candidate.gguf>

# the real loop (exactly what the weekly job runs)
python3 scripts/maintenance/router_selftrain.py
```

Training is a multi-hour CPU job. This is a dev box: full-priority runs are fine,
and the loop may stop the brain for the window — but it always restores it and
verifies `/health` after.

## Enabling the weekly job (operator)

Default **off**. Flip it only after a manual `--dry-run` looks right.

```bash
# services/zoe-data/.env
ZOE_ROUTER_SELFTRAIN=on
ZOE_ROUTER_SELFTRAIN_DOW=sat     # default sat
ZOE_ROUTER_SELFTRAIN_HOUR=1      # default 1 — quiet, and clear of the sun@3 music batch
systemctl --user restart zoe-data.service
```

| Env | Default | Meaning |
|---|---|---|
| `ZOE_ROUTER_SELFTRAIN` | `off` | master flag for the weekly job |
| `ZOE_ROUTER_SELFTRAIN_DOW` / `_HOUR` | `sat` / `1` | cadence |
| `ZOE_ROUTER_SELFTRAIN_TIMEOUT_S` | `28800` (8 h) | CPU training is slow |
| `ZOE_ROUTER_WARM_START` | `labs/functiongemma-finetune/runs/functok-r2/merged` | production lineage checkpoint |
| `ZOE_ROUTER_SCRATCH_PORT` | `11437` | candidate-eval sidecar (never :11436) |
| `ZOE_ROUTER_ARCHIVE_KEEP` | `3` | last-known-good GGUFs retained (never 0) |

## Rollback

Automatic on any post-deploy failure. To roll back by hand:

```bash
ls /home/zoe/models/functiongemma-router/archive/          # lkg_<stamp>_*.gguf
cp /home/zoe/models/functiongemma-router/archive/lkg_<stamp>_*.gguf \
   /home/zoe/models/functiongemma-router/functiongemma-270m-zoe-functok-r2-Q8_0.gguf
systemctl --user restart functiongemma-router.service
curl -s localhost:11436/health
python3 labs/router-90-campaign/prod_path_eval.py          # confirm the restored numbers
```

The fastest kill switch is not a rollback at all: `ZOE_ROUTER_HEAD=off` in
`services/zoe-data/.env` + restart zoe-data takes the whole two-stage router out
of the path (see [Two-stage router rollout](two-stage-router-rollout.md)).

## Where the history lives

All under `data/router_selftrain/` — **git-ignored**: the candidate datasets are
mined real traffic (verbatim user utterances) and must never be committed.

| Path | What |
|---|---|
| `candidate_<stamp>.jsonl` + `.meta.json` | the mined training candidate (produced by the traffic-mining lane) |
| `runs/<stamp>.json` | run journal — every stage, both scores, the ratchet checks and reasons |
| `runs/<stamp>-{incumbent,candidate,post-deploy}.json` | the three full eval results |
| `scoreboard.jsonl` | one line per run: promoted/rejected, both scores, deltas, example counts |
| `work/` | merged training sets, checkpoints, exported GGUFs |
| `/home/zoe/models/functiongemma-router/archive/` | last-known-good GGUFs |
| `/home/zoe/models/functiongemma-router/provenance.json` | what is live, its sha256, and the LKG it replaced |

## Baseline to beat

The incumbent at the time of writing (`labs/router-90-campaign/results/prod-path.json`):
**91.4% overall / 0% chat-FP / 386 ms p50** over the 81-case frozen corpus, through
the real production path.
