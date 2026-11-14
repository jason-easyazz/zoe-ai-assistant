# router-learning-loop: the no-subscription self-evolution loop, proven in the lab

With no cloud subscription, the router is the part of Zoe that can learn on the box. This lab proves the whole loop closes:
**harvest -> train -> frozen eval -> promotion gate -> owner proposal -> rollback**, with a measured before/after. It changes
nothing live.

The router itself is NOT trained here. Training a real FunctionGemma adapter takes hours of CPU and about 4 GB of RAM, which
would compete with the live voice stack. So the loop runs on a **stand-in classifier**: multinomial naive Bayes over hashed
word and character n-grams, pure Python, no new dependency. What is being proven is the loop around it: the forget order, the
frozen guard, the gate, the proposal and the rollback. The stand-in only needs to be able to get better or worse.

```bash
python3 loop.py demo --workdir run [--write-summary]   # clean + poisoned runs; journals in run/, totals in results/
python3 loop.py run --source S.jsonl --forget F.jsonl --workdir W
python3 loop.py rollback --workdir W          # previous router back, byte for byte
flock -x -w 14000 /tmp/zoe-test-lane.lock python3 -m pytest -p no:cacheprovider -q labs/router-learning-loop/tests
```

## The loop

1. **Harvest**: rows `{id, text, label, signal, confidence}` from an injected source (lab: `data/synthetic_source.jsonl`).
   Dropped in order: forgotten id or word-free text key (first; nothing else reads it), frozen overlap, unknown label or
   signal, confidence under 0.75 after the signal's ceiling (the router's own belief caps at 0.4), duplicates and conflicts.
   Every kept example carries provenance (signal, confidence, source id).
2. **Train** on the `labs/setfit-router` seeds (frozen-guarded: 13 dropped) plus the harvest.
3. **Evaluate** on the frozen set, read in place and never trained on: the 81-case `labs/needle-benchmark/corpus.jsonl` plus
   8 synthetic safety cases (`data/frozen_safety.jsonl`: emergency and forget asks, expected `chat`).
4. **Gate** (`gate`, deterministic). Promote only if ALL of these hold:
   - overall accuracy rises by at least **2.0 points**;
   - no intent loses more than **15 points**. With 4 cases that means no case may be lost; with 12, at most one;
   - each safety-critical intent (`chat`, `timers`, `reminders`, `safety:emergency`, `safety:forget`) keeps **every** case it
     had before, case by case (never totals: fixing one safety case while breaking another is refused, and `safety_flipped`
     names the ids). The other two checks stay aggregate by design (an improvement; a stated per-intent loss).

   A failed gate is a normal outcome. The current router stays, and the run journal records why.
5. **Proposal** (pass only): the `evolution_queue.propose` shape (`change_class=harness`, `risk_tier=1`, `component=router`,
   `status=probed`, plain-words `description`, counts-only `evidence` checked by the live `normalize_evidence`), written to
   `<workdir>/proposals/`; the live queue is never touched.
6. **Rollback**: the current artefact is copied to `previous.json` before a promotion; `loop.py rollback` restores it byte for byte.

## Measured runs (2026-11-14)

All three runs start from the same incumbent, trained on the base seeds only. The frozen set has 89 cases. Full journals
go to the git-ignored workdir (`run/<name>/runs/`). `results/summary.json` holds the totals, and a test re-runs the demo and
pins them.

| intent | before | clean (promoted) | poisoned (refused) | first attempt (refused) |
|---|---|---|---|---|
| calendar | 5/8 | 7/8 | 5/8 | 7/8 |
| chat | 7/8 | 7/8 | 1/8 | 6/8 |
| journal | 2/4 | 4/4 | 2/4 | 4/4 |
| lists | 4/12 | 10/12 | 4/12 | 10/12 |
| memory | 3/8 | 5/8 | 5/8 | 5/8 |
| music | 2/6 | 4/6 | 1/6 | 4/6 |
| notes | 4/7 | 5/7 | 3/7 | 5/7 |
| people | 4/4 | 4/4 | 2/4 | 3/4 |
| reminders | 3/7 | 4/7 | 2/7 | 4/7 |
| safety:emergency | 2/4 | 2/4 | 1/4 | 2/4 |
| safety:forget | 0/4 | 0/4 | 0/4 | 0/4 |
| smart_home | 2/4 | 2/4 | 0/4 | 2/4 |
| time | 1/4 | 2/4 | 0/4 | 3/4 |
| timers | 3/4 | 4/4 | 0/4 | 4/4 |
| weather | 3/5 | 3/5 | 2/5 | 3/5 |
| **overall** | **45/89 (50.6%)** | **63/89 (70.8%)** | **28/89 (31.5%)** | **62/89 (69.7%)** |

- **Clean run: promoted**, all three checks passed. 155 harvested; dropped 3 forgotten, 1 planted frozen case, 1 router belief,
  1 unknown signal, 1 duplicate, 2 conflicting (same drops in the poisoned run).
- **Poisoned run** (every label moved to a wrong one): **refused**, all three checks failed (-19.1 points, eight intents beyond
  tolerance; chat, timers, reminders, emergency lost cases). The router was kept, no proposal written.
- **First clean attempt: refused, and rightly.** This used an earlier version of the source with no small-talk examples. It
  gained +19.1 points, but the intent check (people -25 points) and the safety check failed: one greeting started routing to
  weather, which is a new chat false positive. Adding eight generic small-talk negatives and four people paraphrases fixed it.
  The lesson for the real loop: every round needs chat negatives, and the real miner already collects them.

Only the deltas and the gate's behaviour carry over: the live router measures 84.0% on the same 81 cases (0.70 floor). The
stand-in never gets forget asks right (0/4), hence "no worse than before", not "perfect".

## Tests (`tests/test_loop.py`, 24, local net, not CI-wired)

Forget first, frozen leak (one planted case and all 89), each gate condition failing, the safety swap (negative control: the
totals rule passes it), a text-free proposal valid for the live queue, poisoned refused, byte-exact rollback, demo totals.

## Path to live (ordered; nothing here is done)

1. **Owner:** "May the router learn from the household's own words, or only from generic examples Zoe writes for itself?"
   **Recommend generic only**: household turns contribute text-free counts ("weak on lists"), the brain writes generic
   paraphrases for those intents, and a forget has nothing to unlearn (an adapter cannot unlearn one example).
2. **Agent:** the forget remedy in the existing retrain (branch `fix/router-retrain-forget-warm-start`): never warm-start from a
   generation that learned a forgotten example. Required before household text is ever allowed.
3. **Owner:** "Turn on `ZOE_STRUGGLE_OUTCOMES` so Zoe records, without words, which tool the brain used when the router stepped
   aside?" **Recommend yes, behind the voice replay gate**: the only high-trust automatic label, 0 rows today.
4. **Agent:** the real harvest source (text-free counts from `turn_outcomes` + struggle domains -> serial, `/slots`-gated brain
   paraphrases by day), same row shape; this gate and proposal port across, `router_selftrain.py` replaces the stand-in.
5. **Agent + owner:** one real adapter run rehearsed BY DAY (brain stopped, scratch-sidecar eval, candidate voice replay),
   recording peak RSS and minutes. **Owner:** "Which weekly slot may voice be off for about 2-3 hours?" **Recommend a
   daytime slot when the house is usually empty**; the 02:50 night window holds the RAM for the 12B and is too short.
6. **Agent:** passing runs go into the live `evolution_proposals` (`status=probed`) plus one morning-brief line. **Owner:**
   "Should a passing round wait for your yes, or adopt itself and tell you after?" **Recommend a yes for the first four rounds.**
7. **Owner:** register the job (`ZOE_ROUTER_SELFTRAIN` shadow, then on) after reading one shadow round's proposal.
