---
type: Research / decision record
title: "Memory bake-off run 2 decision: KEEP_Z0 (2026-10-08)"
status: DECISION (owner-read). Records the pre-registered rule's verdict on run 2; no code, flag or live-store change. All names are synthetic (the bench's own).
date: 2026-10-08
description: Run 2 measured three candidate arms (MemPalace agent-operated, Hindsight + MemPalace, Zoe + MemPalace verbatim tier) against today's Zoe. No arm passes every floor; Zoe leads every axis it shares with them. Zoe's own memory stays, the engines are not adopted, and the night-reflection question waits on RAM headroom or a second box.
evidence_labels: "[measured] from the run artefacts named below | [derived] arithmetic on measured numbers"
---

# Memory bake-off run 2 decision (2026-10-08)

Read with the instrument's own report, `docs/research/bakeoff-run-20261008-1805.md` (numbers copied from it, not
recomputed), the rule in `docs/research/memory-system-decision-2026-10-05.md` section 6.1 (G0-G3, fixed before any
run), and the run log `~/.zoe/bakeoff-2026-10/run-20261008-1805.log`.

## 1. Verdict

**`KEEP_Z0`, by the pre-registered rule.** No candidate passes every floor (G0-G3). Today's Zoe (Z0, and Z0e with real
retrieval) leads every axis it shares with the candidates. Per the rule: keep Z0 with audit P1-P3.

Axes: pass / n with Wilson 95% (cells that ran; sanity cells excluded; skips are not passes):

| Axis | Z0 | Z0e (real retrieval) | Z0-off (negative control) | MPA | HMA | ZMA |
|---|---|---|---|---|---|---|
| abstention | 12/12 (0.76-1.00) | - | 0/12 (0.00-0.24) | 4/4 (0.51-1.00) | 3/4 (0.30-0.95) | 1/4 (0.05-0.70) |
| authority | 237/237 (0.98-1.00) | - | 0/237 (0.00-0.02) | 38/77 (0.38-0.60), 3 skipped | 31/77 (0.30-0.51), 3 skipped | 79/79 (0.95-1.00) |
| emotional | 6/6 (0.61-1.00) | - | 0/6 (0.00-0.39) | 2/2 (0.34-1.00), 3 skipped | 2/2 (0.34-1.00), 3 skipped | 2/2 (0.34-1.00) |
| exact_words | 2/2 (0.34-1.00); items 40/40 | - | 0/2 (0.00-0.66); items 0/40 | 1/2 (0.09-0.91); items 29/40 | 0/2 (0.00-0.66) | 2/2 (0.34-1.00); items 40/40 |
| extraction | 27/27 (0.88-1.00) | - | 0/27 (0.00-0.12) | 6/9 (0.35-0.88) | 5/9 (0.27-0.81) | 8/9 (0.56-0.98) |
| forgetting | 18/21 (0.65-0.95) | - | 0/21 (0.00-0.15) | 2/5 (0.12-0.77), 2 skipped | 2/5 (0.12-0.77), 2 skipped | 5/5 (0.57-1.00), 2 skipped |
| identity | 39/39 (0.91-1.00) | - | 0/39 (0.00-0.09) | 8/13 (0.36-0.82) | 8/13 (0.36-0.82) | 13/13 (0.77-1.00) |
| multi_hop | 2/2 (0.34-1.00); items 39/40 | 2/2 (0.34-1.00); items 38/40 | 0/2 (0.00-0.66); items 0/40 | 0/2 (0.00-0.66); items 8/40 | 0/2 (0.00-0.66); items 10/40 | 1/2 (0.09-0.91); items 23/40 |
| poisoning | 18/21 (0.65-0.95) | - | 3/21 (0.05-0.35) | 3/7 (0.16-0.75) | 2/7 (0.08-0.64) | 0/7 (0.00-0.35) |
| protocol | 3/3 (0.44-1.00); items 36/36 | - | 0/3 (0.00-0.56); items 2/44 | 2/3 (0.21-0.94); items 28/32 | 2/3 (0.21-0.94); items 28/32 | 3/3 (0.44-1.00); items 36/36 |
| protocol_brain | - | - | - | 3/4 (0.30-0.95) | 3/4 (0.30-0.95) | 3/4 (0.30-0.95) |
| recall | 12/12 (0.76-1.00) | 12/12 (0.76-1.00) | 0/12 (0.00-0.24) | 1/4 (0.05-0.70) | 2/4 (0.15-0.85) | 4/4 (0.51-1.00) |
| recall_distance | 9/9 (0.70-1.00) | 9/9 (0.70-1.00) | 0/9 (0.00-0.30) | 0/3 (0.00-0.56) | 1/3 (0.06-0.79) | 3/3 (0.44-1.00) |
| reflection | 3/3 (0.44-1.00), 2 skipped; items 3/3 | - | 0/3 (0.00-0.56), 2 skipped; items 5/12 | 0/5 (0.00-0.43); items 5/12 | 2/5 (0.12-0.77); items 13/19 | 2/5 (0.12-0.77); items 19/21 |
| temporal | 39/39 (0.91-1.00) | - | 0/39 (0.00-0.09) | 0/3 (0.00-0.56), 10 skipped | 0/3 (0.00-0.56), 10 skipped | 5/13 (0.18-0.64) |

Winner clause (floors G0-G3 first; then beats Z0 beyond the Wilson interval on 2 capability axes, worse on none; a tie goes to the maintained candidate):

* MPA: C=WORSE; D=WORSE; J=WORSE; K=tie; L=WORSE; M=no data
* HMA: C=WORSE; D=tie; J=tie; K=tie; L=WORSE; M=no data
* ZMA: C=WORSE; D=tie; J=tie; K=tie; L=WORSE; M=no data

Floors beside the contest (never inside it): MPA: B=tie, E=tie; HMA: B=WORSE, E=tie; ZMA: B=tie, E=WORSE.

No arm beats Z0 on any axis, so the contest was never reached.

## 2. The three candidates

**MPA (MemPalace alone, operated by the agent).** The 4B clone brain drove MemPalace's tools: 172/172 tool calls were
schema-valid, and the longest prompt (protocol text, tool schemas, history) was 5,985 tokens, inside the 8,192 slot. It
searched before answering 36/100 times and retired a changed fact correctly 0/10 times. 50 hard violations; authority
38/77, identity 8/13, temporal 0/3, exact words 29/40, multi-hop items 8/40. The t+6 min forgetting probe is
unmeasured: the palace rebuild crashed inside MemPalace's Chroma backend (`CollectionNotInitializedError`). Verdict
NOT_ADOPTABLE.

**HMA (Hindsight + MemPalace, integrated).** The brain behaved identically to MPA's run: 172 calls, searched first
36/100, supersede 0/10. 58 hard violations; authority 31/77, extraction 5/9, multi-hop items 10/40. Reflection got 2/5
cells (items 13/19) against MPA's 0/5. The same palace-rebuild crash left the t+6 forgetting probe unmeasured. The Zoe
glue is 1,361 lines against the 1,000 ceiling. Verdict NOT_ADOPTABLE.

**ZMA (our stack with MemPalace as a verbatim tier).** Strong where Z0 is strong: authority 79/79, identity 13/13,
forgetting 5/5, exact words items 40/40, protocol items 36/36, recall 4/4. Weak where the added tier bites: poisoning
0/7 (Z0 18/21), temporal 5/13 (Z0 39/39), multi-hop items 23/40 (Z0e 38/40), abstention 1/4. 9 hard violations, steady
RSS 661.4 MB against the 600 MB ceiling, glue 1,283 lines. One instrument control did not turn red (`ZMA-W2.one-packet`
stayed green with one_packet off), so ZMA's instrument is not ok and its numbers carry that flag. Verdict NOT_ADOPTABLE.

All three ran one seed by design (the rule needs three), so none could be adopted by this run regardless.

## 3. What the run proved about the engines on this brain

* **For MPA and HMA, the 4B uses MemPalace's tool schemas perfectly (172/172 valid) but does not reach for memory first
  (36/100 searched-before-answering in both) and never retires a fact correctly (0/10 in both).** The engine is not the
  weak part; the brain's use of it is. ZMA is different by design: its brain only reads (`mempalace_status`,
  `mempalace_search`; 89/89 valid, 78/100 searched first), the harness writes the facts and Z0 retires them, so ZMA's
  0/10 on the shared MemPalace-triple supersede test measures the integration's write path, not the brain.
* **Hindsight's consolidation prompt does not fit the 8,192-token live slot:** max 8,313 tokens, 3 consolidation calls
  failed on a context-size error. Its reflection on the live brain fails by construction.
* **At 32k context the reflection (K) variants were run for HMA and ZMA, and both carry the instrument's `VETOED`
  label on K1.** That label fires on any K1 failure or error, including too few judgeable observations, and the run
  artefact records no judged/false counts for the 32k variants, so this is an instrument outcome, not proof that the
  observations were false. Cell verdicts from the report:
  * HMA@32k: K1 observations_are_true FAIL, K2 thread_recall PASS, K3 useful_answers FAIL, K4 invalidated_fact_not_restated FAIL, K5 user_stated_is_never_restated_as_inference FAIL.
  * ZMA@32k: K1 FAIL, K2 PASS, K3 PASS, K4 FAIL, K5 FAIL.
  The 8k K1 records for the three arms carry no precision counts (insufficient evidence, no veto at 8k); the veto
  applies to the 32k variants, which are variants and never contest entrants.
* **The 12B could not run.** With the 4B and Kokoro stopped the box has 8,003 MB available; the 12B at 32k needs
  9,262 MB (model 6,976 + KV 486 + compute 600 + the 1,200 floor): margin -1,259 MB. Whether a bigger model rescues
  reflection is therefore unmeasured, not negative.

## 4. H1 (Hindsight alone), from the aborted 14:16 run

The 14:16 run was aborted at 17:04 when Hindsight became unreachable during H2; H1 had completed all 3 seeds
(`run-20261008-1416.json`). H1 passed: zero non-loopback connects (0 over 1,357 observed); steady RSS 475.6 MB and
burst 496.1 MB; extraction validity 104/104 (100%) on the 8,192 slot, max prompt 1,358 tokens; recall p95 96 ms (n=50,
p50 89 ms); 1.39 s of brain slot per turn; **0 hard violations over 3 seeds**; forgetting clean at t+0 and t+6 min
(0 of 2 probes resurrected each); Zoe layer 289 lines. It is NOT_ADOPTABLE only on `hard_cells_all_ran`: the capability
cells (103 hard cells per seed) were skipped by the time box, which the rule counts as not run, not as an engine
result. This is a statement about the time box, not about H1's behaviour.

The tie-goes-to-the-maintained-candidate clause did not apply: no candidate reached the contest, because none passed
the floors and none beat Z0 on an axis.

## 5. Honest caveats

From the report ("What this run did not verify"):

* The t+6 min forgetting probe is unmeasured for MPA and HMA (palace rebuild failed in MemPalace's Chroma backend).
* Reflection 12B@32k did not run (floor breach, margin -1,259 MB).
* The real Hindsight extraction quality on the B cells (the unit tests use a rule-based double).
* Net RSS: the Chroma/ONNX that adoption frees inside zoe-data was not subtracted; the figure is gross and so conservative.
* G3 deletable lines is an estimate of files judged deletable, never proven.
* Cells an arm cannot run are skips with the reason; hard ones keep `hard_cells_all_ran` red by the pre-registered rule.
* F5/F6 (physical erase) and A8 (people graph) on the Hindsight arms are the first contact with the real rows; the engine's own writes were only modelled beforehand.
* The capability axes ran on seed 1 only; the lab half of M (protocol) is a scripted stand-in and never decides; M is "no data".
* The capability thresholds were written before any Hindsight arm ran them; Z0e's long-range numbers were seen afterwards.
* All arms share one Hindsight server and one egress log; the per-arm egress split is by wall-clock phase.
* Hindsight consolidation does not fit the 8,192 slot (max 8,313 tokens), and its background consolidation cannot be paused in 0.10.2.
* MPA and HMA are scored with the clone brain operating MemPalace's tools: the brain is the instrument, a different brain would move their J / K / L / M cells.

Added by this record:

* One seed per candidate; the rule needs three.
* The candidate arms were written this week by agents (about 12 review rounds); an arm's weakness may be the adapter's, not the engine's.
* The per-call cost `MPA_S_PER_CALL = 3 s` used to plan the MPA / HMA phases was a placeholder; the real cost is about 20 s per call (about 3.9k prompt tokens, about 8 decode tok/s).
* Kokoro was stopped by hand for the window (the run log: "kokoro-tts.service was not active before the window").
* The lab forget ledger salt was unset in the lab environment (`ZOE_FORGET_LEDGER_SALT` unset: only the 300 s tombstone shield was active in the lab), so the durable forget ledger was not exercised by this run.

## 6. What this means for the program (direction, not new work)

* **Zoe's own memory stays.** The engines are not adopted; there is nothing to migrate.
* The pieces taken from the engines stay (the alias sweep; quote-backed retirement is pending).
* The night-reflection capability (K) remains unmeasured for a bigger model until the box has about 1.3 GB more headroom or a second box (the Mac mini) exists.
* The next measured steps are Zoe's own gaps: poisoning and forgetting at 18/21 on Z0, the structural redesign of the lexical floors, and language independence.

## 7. Owner steps (operator)

* The 12B night phase needs headroom: stop zoe-data and the router during a night window, or use the Mac mini.
* The MemPalace palace-rebuild crash is reported upstream only if we keep the MemPalace arm.

Open items from this run are in `docs/knowledge/open-problems.md` (entries dated 2026-10-08).
