---
type: Reference
title: The self-improver (self-evolution Phase 1) - a nightly, shadow-only manner.md improver with a fourteen-night stop rule
description: GEPA-style reflective improver over ONE overlay file (manner.md) - two cloud-reflected candidates a night vs a repeated-sampling control at equal brain cost, scored on the held-out manner cells with the pass^k / collateral gate, shadow-apply only, 04:00 slot, capped dev credits, stop after 14 measured nights unless it wins 10.
tags: [self-evolution, samantha, manner-block, overlay, gepa, night-job, shadow, cost-cap]
timestamp: 2026-11-05T00:00:00Z
---

# The self-improver (self-evolution Phase 1)

**Status 2026-11-05: built, NOT scheduled.** The timer template ships uninstalled and there is no key; every live night refuses
until the owner does the steps below. One dry-run night has run (no key, no brain). Source of the experiment: Samantha
initiative roadmap item 13 / experiment 8 / lane B E1 ([record](../research/samantha-initiative-2026-11-03.md),
[lane B](../research/samantha-initiative-2026-11-03/lane-B-agents-self-improvement.md)); loop design
[self-evolution-nightly-loop.md](../architecture/self-evolution-nightly-loop.md) sections 3, 5, 8, 9.

## What it is, as built

| piece | where |
|---|---|
| the improver (stdlib; the SDK only for live reflection) | `scripts/perf/self_improver.py` |
| the night runner | `scripts/maintenance/self_improver_night.sh` |
| timer + service templates (NOT installed) | `scripts/setup/systemd/zoe-self-improver.{service,timer}` |
| the scoring rig, reused (not rebuilt) | `scripts/perf/manner_block_ab.py` (its DEV / frozen HELD-OUT split, scorers, fingerprints, tombstones, ledger) |
| the collateral gate, reused | `scripts/perf/bar_reliability.py` `aggregate` + `compare` (#2278, pass^k / collateral damage) |
| tests (ci_safe, fakes only) | `tests/unit/test_self_improver.py`; the rig seam in `tests/unit/test_manner_block_ab.py` |

**Target: ONE file, `manner.md`** - the brain's manner block (the English block in `services/zoe-data/lexicons_data/en.json`,
which an overlay `manner.md` replaces, `labs/flue-zoe-brain-2x/src/harness.ts`). Not the soul, not a doctrine, not tool
guidance, never the frozen Gemma model. The seed each night is the CURRENT text: the live overlay's `manner.md` if the brain
runs an overlay (`ZOE_BRAIN_HARNESS_DIR` in the sidecar's `.env`), else the shipped lexicon block.

**One night (04:00, deadline 04:25):**

1. **Gates, fail closed** (any one refuses the night, exit 3): the slot (03:30 to 10 min before the deadline), harness quiet hours
   + panel yield (`harness_quiet.check`), `night_busy.sh test` (the 12B night window or the backup is running),
   `zoe-voice-regression` not active (it starts 04:30 and must not share the 4B), no `land_voice_pr.sh`, the brain-window lock free,
   MemAvailable >= 1.5 GB. Then the shared harness lock (`/tmp/zoe-voice-harness.lock`) for the whole probe. `night_busy.sh voice`
   is deliberately NOT the gate: it pauses 04:00-04:55 to protect the voice-gate artifact from LANDERS, which this job never writes.
2. **DEV traces** of the seed (`manner_block_ab.py --phase dev`, cached per seed sha, so they are paid once per text). The
   reflection sees only these: the bench ask's words, the reply, which behaviours were missed or met, and the documented intent of
   each behaviour. Never held-out asks, needles, golds, scorer evidence or judge text; a `privacy_guard` refuses an email address,
   a held-out id or an owner-listed term (`~/.zoe/self-improver/deny-terms.txt`, one per line) before anything leaves the box.
3. **Reflection -> K=2 candidates**: one GENERAL rewrite, one TARGETED at the most-missed behaviour (GEPA's reflective mutation,
   borrowed as a pattern: the `gepa` library's optimize loop wants many evaluator calls inside one process and one probe here is a
   whole night). Each candidate must pass the overlay loader's rules (harness.ts `checkText`: no control character, no
   chat-template or fence marker, trimmed) and the product's 640-char block budget, and differ from the seed and each other.
4. **Held-out probe** on the 4B (`--phase heldout`, default cells `flaw,good`, 44 asks per arm): arms R0 (seed), C1, S1, C2, S2,
   where **S1 and S2 are the seed again - the repeated-sampling control at the same brain cost as the two candidates**.
5. **Verdict.** A WIN needs (a) the best candidate's held-out pass rate STRICTLY above the best of R0/S1/S2 - both sides take a
   max over draws, so the selection bias of picking the best of two is symmetric; and (b) the collateral gate:
   `bar_reliability.compare` of candidate vs R0 (one scenario per ask, sample and half) is not `worse` and shows no more per-ask
   regressions than the sampling arms show against R0 (the measured noise floor). On a would-be win, (c) the regression phase
   (`--phase regress --scale 0.5`: P2, P5a, P5c, P6.b, P7, P8 and the bar sample S1, S3, S4, S28-S31) runs R0 vs the winner;
   any half that separates WORSE (Wilson, the rig's own rule) vetoes. An incomplete arm or phase is UNMEASURED, never a loss
   or a win.
6. **Shadow-apply**: the winner is written to `~/.zoe/self-improver/nights/<night>/shadow/manner.md` and a
   `shadow-ledger.jsonl` row (`would_apply: true, applied: false`). Nothing else. The only write path (`safe_write`) refuses the
   repository, the live overlay root (`~/.zoe/brain-harness`, plus whatever `ZOE_BRAIN_HARNESS_DIR` names and its parent) and any
   planted symlink on the way (checked component by component before any directory is created; the bytes go to a fresh
   `O_EXCL | O_NOFOLLOW` temp file, then an atomic rename); the tests prove a forced winning night leaves the live overlay
   byte-identical. A regression phase the deadline cuts makes the night UNMEASURED, never a counted loss.

**The fourteen-night stop rule (pre-registered).** Only live, measured nights count, in order; skipped, refused, unmeasured and
dry-run nights never count. Ten wins in the first fourteen = Phase 1 passed. A fifth loss makes 10/14 impossible, so the loop
stops early: `STOPPED` is written and every later live night refuses (exit 4). Resetting is an owner decision, never automatic.

**The cost cap, in code.** Before EVERY reflection call the meter takes the worst case - the prompt's UTF-8 byte count as the
input bound (a byte-level token is at least one byte, so this can never under-count) plus the full 6000-token output allowance -
refuses the call if that would cross the night's token cap OR dollar cap, and otherwise CHARGES it and writes it to
`cost/<night>.json` before the request leaves; the billed usage replaces it afterwards. A process killed mid-call therefore keeps
the worst case counted, and a re-run of the same night resumes the same meter. A model without a price in `PRICES_PER_MTOK` is refused. No server-side refusal fallback is sent
(it would bill a second model outside the cap): a refusal yields no candidate and the night is unmeasured. Defaults: 40 000
tokens and $0.40 a night (two calls normally cost a few cents on `claude-sonnet-5-5`).

**The key** is read by NAME (`ZOE_IMPROVER_ANTHROPIC_KEY`) at call time and handed to the SDK client; it is never logged, stored,
put in a row, a file or an error string (pinned: a fake client that echoes the key in its exception leaves no trace of it), and
it is stripped from the A/B subprocess's environment.

## Settings (env, read by `typed_env`; all optional)

`ZOE_IMPROVER_MODEL` (default `claude-sonnet-5-5`), `ZOE_IMPROVER_MAX_TOKENS_NIGHT` (40000), `ZOE_IMPROVER_MAX_USD_NIGHT` (0.40),
`ZOE_IMPROVER_BRAIN_BUDGET_S` (1500), `ZOE_IMPROVER_DEADLINE` (`04:25`), `ZOE_IMPROVER_CELLS` (`flaw,good`; P6 and P9 sit at
their ceiling in this rig, see [manner-block.md](manner-block.md)). The key: `ZOE_IMPROVER_ANTHROPIC_KEY`.

## State and exits

`~/.zoe/self-improver/`: `nights.jsonl` (one row per night: status, scores per arm, candidates, verdict, reflection cost),
`nights/<night>/` (seed, traces, candidates, `night.json`, `shadow/`), `shadow-ledger.jsonl`, `lessons.jsonl` (each candidate's
one-line edit + won/lost: the only thing fed back to the next night's reflection), `cost/<night>.json`, `state.json` (the stop
rule), `ab/` (the rig's rows, its brain-time ledger, `plan.json`). Log: `~/.zoe-logs/self-improver.log`.
Exit: 0 measured (win or loss) / 2 unmeasured or error / 3 skipped by a gate / 4 refused (no key, rule decided, night already ran).

```
python3 scripts/perf/self_improver.py night --dry-run      # a full rehearsal on synthetic arms: no key, no brain, never counted
python3 scripts/perf/self_improver.py status               # the stop rule + the last five nights
```

## Owner steps (the only things between this and a first live night)

1. **Create the capped key.** In the Anthropic console, a dedicated API key for this job only, in a workspace with a monthly spend
   limit (suggested $10: fourteen nights at the $0.40 hard cap is $5.60).
2. **Store it by name, never on a command line:**
   ```
   install -m 600 /dev/null ~/.zoe/env/self-improver.env
   ${EDITOR:-nano} ~/.zoe/env/self-improver.env        # one line: ZOE_IMPROVER_ANTHROPIC_KEY=<the key>
   ```
   Optional lines in the same file: `ZOE_IMPROVER_MAX_USD_NIGHT=0.40`, `ZOE_IMPROVER_MAX_TOKENS_NIGHT=40000`.
3. **Provision the SDK for the wrapper's interpreter.** `scripts/maintenance/self_improver_night.sh` runs `/usr/bin/python3`, not the
   zoe-data venv: `/usr/bin/python3 -m pip install --user 'anthropic>=0.86,<1'` (check: `/usr/bin/python3 -c "import anthropic"`). Without it
   the night records `no reflection SDK` as a problem and ends unmeasured; it never crashes.
4. **Optional:** `~/.zoe/self-improver/deny-terms.txt`, one household name per line (the guard refuses any payload carrying one).
5. **Install the timer:**
   ```
   cp ~/assistant/scripts/setup/systemd/zoe-self-improver.{service,timer} ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now zoe-self-improver.timer
   systemctl --user list-timers zoe-self-improver.timer
   ```
6. **Watch:** `tail -n 40 ~/.zoe-logs/self-improver.log`; `python3 ~/assistant/scripts/perf/self_improver.py status`.
7. **Stop at any time:** `systemctl --user disable --now zoe-self-improver.timer`, and revoke the key in the console.

**Promotion is a separate, human decision** (Phase 2 of the design: apply + canary). Nothing here does it. If the fourteen-night
rule passes and the owner wants a shadow winner live: put it in a new overlay version and switch with
`labs/flue-zoe-brain-2x/scripts/harness-overlay.ts` (`npm run harness -- switch ...`, replay-gated like any voice change); the
overlay is read only when the brain runs with `ZOE_BRAIN_HARNESS_DIR`.

## Honest limits

- **The Samantha bar's live scenarios cannot see `manner.md`:** the bar's `demo_bar_*` users have no adult account, so zoe-data
  sends them no manner block (adults only, by allowlist, `manner_block.py`). Scoring a manner candidate on the bar would compare
  identical prompts. The held-out manner cells of the person-half rig (`manner_block_ab.py`, which overrides eligibility for its own
  throw-away user only) are therefore the scoring set, and the bar's chat-only scenarios enter through the rig's regression sample.
  #2285 (per-arm env overrides for the bar) is not needed for this reason and is not used.
- **The held-out half is reused nightly.** The reflection never sees it, but the loop learns one bit per night (won/lost) through
  the lessons; over fourteen nights that is a small, bounded leak. The pre-registered rule and the sampling control are the guard.
- **Equal cost is equal BRAIN cost.** The sampling control spends the 4B's time exactly as the candidates do; it does not spend
  cloud tokens.
- **Time.** 5 arms x 44 asks (+ 80 DEV asks once per seed, + 106 regression asks on a would-be win) is unmeasured on this box; the
  first live nights say whether 04:00-04:25 holds it. A night the deadline cuts is unmeasured and its rows are kept.
- **Not built (later phases):** the sycophancy-editor control arm, the different-judge rerun of winners, apply + canary, the
  local 12B as proposer (Tier 1).
