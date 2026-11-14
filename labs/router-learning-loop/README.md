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
python3 loop.py run --source S.jsonl --forget F.jsonl --workdir W [--poison]
python3 loop.py rollback --workdir W          # previous router back, byte for byte
flock -x -w 14000 /tmp/zoe-test-lane.lock python3 -m pytest -p no:cacheprovider -q labs/router-learning-loop/tests
```

## The loop

1. **Harvest** (`harvest`). An injected source yields rows `{id, text, label, signal, confidence}`. In the lab it is
   `data/synthetic_source.jsonl` (synthetic only). Rules, in this order:
   - a row whose id or word-free text key is on the forget list is dropped **first** and counted. Nothing else reads it;
   - a row whose normalised text matches any frozen case is dropped (`frozen_overlap`);
   - unknown label, unknown signal, or confidence below 0.75 after the signal's ceiling is applied is dropped. The router's own
     belief has a ceiling of 0.4, so it is never a label on its own;
   - identical wordings are deduplicated, and conflicting labels for one wording drop the whole group.
   Every kept example carries provenance: signal, confidence, source id.
2. **Train** the candidate on the base set plus the harvest. The base is the `labs/setfit-router` seeds, frozen-guarded too
   (13 seeds that appear in the frozen corpus are dropped).
3. **Evaluate** the incumbent and the candidate on the **frozen set**, which is read in place and never trained on: the 81-case
   `labs/needle-benchmark/corpus.jsonl` (the real ratchet's corpus) plus 8 synthetic safety cases in `data/frozen_safety.jsonl`
   (emergency and forget asks; expected = `chat`, meaning no fast tool may claim them).
4. **Gate** (`gate`, deterministic). Promote only if ALL of these hold:
   - overall accuracy rises by at least **2.0 points**;
   - no intent loses more than **15 points**. With 4 cases that means no case may be lost; with 12, at most one;
   - each safety-critical intent (`chat`, `timers`, `reminders`, `safety:emergency`, `safety:forget`) keeps **every** case it
     had before. A single new chat false positive blocks the promotion, whatever it buys elsewhere.

   A failed gate is a normal outcome. The current router stays, and the run journal records why.
5. **Proposal**. Only on a pass, a file in the shape `evolution_queue.propose` takes: `source`, `change_class=harness`,
   `risk_tier=1`, `component=router`, `status=probed`, `title`, a plain-words `description`, and `evidence` with
   `signal`/`before`/`after`/`gate`/`ref`. The evidence is validated by the live module's own `normalize_evidence`. It holds
   counts and percentages only, never an utterance. In the lab it goes to `<workdir>/proposals/`; the live queue is never touched.
6. **Rollback**. Before a promotion, the current artefact is copied to `previous.json`. `loop.py rollback` puts it back byte for
   byte.

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

- **Clean run: promoted.** All three checks passed. 155 examples were harvested. Dropped: 3 forgotten, 1 planted frozen case,
  1 low-confidence router belief, 1 unknown signal, 1 duplicate, 2 conflicting.
- **Poisoned run: refused.** The same rows, with every label moved to a wrong one. All three checks failed (-19.1 points, eight
  intents beyond tolerance, and chat, timers, reminders and emergency all lost cases). The current router was kept and no
  proposal was written. The drops were the same as the clean run.
- **First clean attempt: refused, and rightly.** This used an earlier version of the source with no small-talk examples. It
  gained +19.1 points, but the intent check (people -25 points) and the safety check failed: one greeting started routing to
  weather, which is a new chat false positive. Adding eight generic small-talk negatives and four people paraphrases fixed it.
  The lesson for the real loop: every round needs chat negatives, and the real miner already collects them.

The stand-in's absolute numbers mean nothing about the real router. The live router measures 84.0% on the same 81 cases
(0.70 floor). Only the deltas and the gate's behaviour carry over. The stand-in also never gets forget asks right (0/4), which
is why the safety rule says "no worse than before", not "perfect". The real router already sends these to deterministic tiers.

## Tests (`tests/test_loop.py`, 23, local net, not CI-wired)

Forget first (by id and by word-free key), frozen leak (one planted case and all 89), each gate condition with a fail case,
a text-free proposal that passes the live queue's own validation, poisoned refused, byte-exact rollback, and the demo
totals against `results/summary.json`. Negative controls are in the tests; mutating out the forget, frozen or safety check
turns 3, 6 and 1 of them red.

## Path to live (ordered; nothing here is done)

Each step needs the one before it.

1. **Owner decides the training-text policy.** *Question:* "May the router learn from the household's own words, or only from
   generic examples Zoe writes for itself?" **Recommendation: generic only.** Household turns contribute counts ("weak on
   lists") from text-free signals. The brain writes fresh generic paraphrases for those intents from the intent catalogue, and
   those are what trains the router. A forget then has nothing to unlearn, because a trained adapter cannot unlearn one example.
   The alternative, the hash join to the member's own stored turns, needs the clean-ancestor retrain fix in step 2 first.
2. **Agent: fix the forget remedy in the existing retrain** (`scripts/maintenance/router_struggle_retrain.py`
   `warm_start_for`). Today a generation flagged `needs_retrain` is still the warm start for the next one, so the forgotten
   example's influence is inherited. The fix: warm-start from the newest ancestor that never saw it. Needed only if step 1 allows
   household text. Recommended anyway.
3. **Owner turns on failure capture.** *Question:* "Turn on `ZOE_STRUGGLE_OUTCOMES` so Zoe records, without words, which tool
   the brain actually used when the router stepped aside?" **Recommendation: yes, behind the voice replay gate.** It is the only
   high-trust automatic label, and it is not being collected today (0 rows).
4. **Agent: build the harvest source for real.** The real source reads text-free counts from `turn_outcomes` and struggle
   domains, then asks the brain (serial, `/slots`-gated, by day) for N generic paraphrases per weak intent. Same row shape as this
   lab. The lab gate and proposal port across unchanged; the stand-in is replaced by `router_selftrain.py`'s train, export and
   ratchet.
5. **Agent + owner: rehearse one real adapter run BY DAY** (owner rule: no first run at night). The brain is stopped for a
   measured window, the candidate is evaluated on the scratch sidecar, and the candidate is voice-replayed. This records real
   peak RSS and minutes. *Question:* "Which weekly slot may the router train in, with voice off for about 2-3 hours?"
   **Recommendation: a daytime slot when the house is usually empty.** The 02:50 night window already holds the RAM for the 12B
   and is too short.
6. **Agent: the proposal reaches the owner.** Write the passing run into the live `evolution_proposals` (`status=probed`) and
   add one morning-brief line. **Owner:** "Should a passing round wait for your yes, or adopt itself and tell you after?"
   **Recommendation: wait for a yes for the first four rounds, then auto-adopt** with the line "Zoe's router learned X, here is
   how to undo it".
7. **Owner registers the timer and flips the flag** (`ZOE_ROUTER_SELFTRAIN` shadow, then on), after one shadow round whose
   proposal they have read.
