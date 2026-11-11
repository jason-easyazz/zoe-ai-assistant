# recall-match-lab

Retrieval-only, offline, SYNTHETIC lab: on household-style turns, which rule puts the stored fact the turn needs in front of the
brain? Built for lane 7 of the 2026-11-11 research round (the "what she knows never reaches what she says" circuit).

- `fixtures.py` - one invented household: 40 facts (invented people, pets, routines; no real household data) with a seeded random
  activation, and 60 turns: 20 **direct** (share a word or name with the fact), 20 **oblique** (the fact bears on the turn the way
  real speech does: "what should I get my sister?" vs "sister loves gardening"), 20 **irrelevant**.
- `run_lab.py` - scores four methods. Never opens a database, Chroma, the network or the brain:
  - (i) production: `services/zoe-data/recall_every_turn._ranked` (the `ZOE_RECALL_EVERY_TURN` relevance test = `recall_gate`
    lexical scoring and threshold 2.0) CALLED AS A LIBRARY on an in-memory index of the synthetic rows, with the `select()` budget
    loop (3 rows / 90 tokens). Every row is given authority 4 and treated as speakable (the restraint, forget and recheck filters
    are not exercised). Also re-run with the threshold lowered to 1.5 and 0.8.
  - (ii) bge-small-en-v1.5 (fastembed, CPU, local cache, `local_files_only`) cosine top-3 above a threshold tuned (Youden) on the
    even turns of each class; held-out = odd turns. Plus a threshold sweep.
  - (iii) standing card: the top facts by activation that fit 150 estimated tokens (`recall_gate.est_tokens`), always present.
  - (iv) union of (ii) and (iii).

Run (heavier than a few seconds: under the test-lane lock, one at a time, ~130 MB RAM, CPU only):

    flock -x -w 3600 /tmp/zoe-test-lane.lock nice python3 labs/recall-match-lab/run_lab.py --json /tmp/out.json

## Results (2026-11-11, origin/main 1ddb70951) [Experimental, synthetic]

| method | direct hit | oblique hit | irrelevant turns injected | relevant turns, wrong rows only |
|---|---|---|---|---|
| (i) production as coded (threshold 2.0) | 18/20 | 2/20 | 0/20 | 1/40 |
| (i-b) production, threshold 1.5 | 20/20 | 8/20 | 10/20 | 5/40 |
| (i-c) production, threshold 0.8 | 20/20 | 9/20 | 11/20 | 6/40 |
| (ii) bge-small cos top-3 >= 0.67 (tuned on even turns), all 60 | 19/20 | 0/20 | 1/20 | 1/40 |
| (ii) same, held-out odd turns only | 10/10 | 0/10 | 1/10 | 1/20 |
| (iii) standing card (8 facts, ~146 tok, always present) | 4/20 | 5/20 | 20/20 | 31/40 |
| (iv) union (ii)+(iii), all 60 | 19/20 | 5/20 | 20/20 | 16/40 |
| (iv) union, held-out odd turns only | 10/10 | 2/10 | 10/10 | 8/20 |

Cosine rank of the needed fact: direct top-1 20/20; oblique top-1 12/20, top-3 14/20, top-5 16/20. Median cosine of the needed fact
0.665 vs 0.552 for the best fact on an irrelevant turn - but the distributions overlap:

| cosine threshold (top-3) | 0.50 | 0.54 | 0.56 | 0.58 | 0.60 | 0.62 | 0.64 | 0.66 | 0.68 |
|---|---|---|---|---|---|---|---|---|---|
| oblique hit /20 | 13 | 13 | 12 | 8 | 6 | 6 | 2 | 1 | 0 |
| irrelevant injected /20 | 12 | 10 | 9 | 8 | 7 | 4 | 2 | 1 | 0 |
| direct hit /20 | 20 | 20 | 20 | 20 | 20 | 20 | 19 | 19 | 17 |

## What it says (and does not)

- The production lexical gate is precise and nearly complete on DIRECT speech (18/20, 0 false), and blind to OBLIQUE speech
  (2/20). Its two direct misses ("camping", "saving") share exactly one rare stem key: 1.5 points (`recall_gate._score`, `_W_RARE`)
  is below the 2.0 threshold unless a capitalised name, a second key or a clock time joins it.
- Lowering the threshold buys oblique hits only by colliding on common stems ("like", "play", "time", "morning"): 1.5 gives 8/20
  oblique at 10/20 irrelevant turns injected. A threshold is not the fix.
- Similarity RANKS the needed fact well (oblique top-3 14/20) but no cosine threshold separates oblique-relevant from irrelevant.
  Injecting the top-3 unconditionally (a small fixed budget) would reach ~14/20 oblique and 20/20 direct at the price of rows on
  every turn - the same price the standing card pays, with far better coverage than a card (5/20, bounded by 8 of 40 facts fitting).
- A card with RANDOM activation is a lower bound; with activation that tracks what gets asked it would score higher.
- Synthetic, 60 turns, one invented world, written by the experimenter: says nothing certain about real household speech, and it
  is retrieval-only (whether the 4B brain is helped or distracted by an always-on block is not measured here).

Lab only: not imported by any service, CI job or unit (labs/AGENTS.md Forbidden).
