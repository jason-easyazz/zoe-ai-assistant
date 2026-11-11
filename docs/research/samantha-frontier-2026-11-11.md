---
type: research
title: The Samantha frontier (2026-11-11) - the circuit re-measured, what landed and what runs, and the sequence from here
date: 2026-11-11
status: research-only. Seven lanes (1 circuit re-measured; 2 landed vs live; 3 bench numbers; 4 the turn and the panel; 5 brain budget and hardware; 6 forget-first memory lab; 7 memory supply, recall matching and promises) are in the bundle. Two lab prototypes were built on local branches against synthetic data. Nothing live was flipped, restarted, written or deployed. Builds on the plan of record (samantha-initiative-2026-11-03.md) and does not replace it.
description: Owner brief - how does Zoe become Samantha, as an experience and not a voice; re-measure the open circuit on today's box, say what landed since 11-03 and whether it is live, and give the sequence of changes with numbers she cannot fake. Answer first - the plan of record was largely BUILT in eight days and the circuit is still open, because the break moved. It is no longer "flags are off". It is that Zoe has almost nothing to say (the stores the new features read are empty, and the panel's extractor has produced one durable row in nine days), the relevance test that decides injection misses ordinary speech, and the one supply line that carries a life (the pendant, 126 hours in 30 days, 106 candidates) ends at a shadow flag with nobody shown anything. The next step is supply and a standing card, not more delivery features.
evidence: samantha-frontier-2026-11-11/ (lane-1..7). Measured on 2026-11-11 - household brain turns 11-05..11-11 n=51, tool calls 0, recall calls 0, continuity 0 of 30; memory used in 2 of 32 member replies; every-turn recall (shadow) found a relevant row on 1 of 17 eligible turns; 23 roadmap items = 5 live-executing, 5 live-shadow, 1 flag-on-never-fired, 4 merged-dark, 1 offline instrument, 1 partial, 6 not built; speech end to first sound 72 real panel turns ~3.0 s median / 5.1 s p90; night window 5 of 8 completed; pendant funnel 2,263 recordings -> 106 candidates -> 0 offered; commitments ledger 33 of 33 rows are replay-harness artifacts.
---

# The Samantha frontier (2026-11-11)

> Eight days after the plan of record, most of it exists in the code. The household would not be able to tell. This record is about why, and what to do in what order.

Labels: [Proven] measured directly; [Experimental] measured through a proxy, a shadow log or synthetic data; [Speculative] reasoning not yet measured. Sources: [live] the running box on 2026-11-11, [code] origin/main 1ddb70951, [record] an earlier record, [contradicted] an earlier claim now shown wrong. Every number's method is in the lane that produced it.

## 0. The three things that most change the picture since 2026-11-03

1. **The plan was built and the circuit is still open, because the break moved from delivery to supply.** [Proven][live] 133 commits landed. Night mind is set to serve (with no serve marker yet, so by this record's rule it counts as zero), the outcome edge is on, the manner block fires on 30 of 30 member turns, history is 8 turns. Yet on 51 real household brain turns the brain made 0 tool calls and 0 recall calls, continuity matched 0 of 30, and memory was used in 2 of 32 member replies. The features read stores that are empty (member register 0 rows, now cards 0, conversation notes 0, night-mind threads raised 0 of 42) or thin (one member, about 54 durable rows). The panel has written 1 durable row in 9 days; its idle extractor ran 25 passes over 224 member turns and returned zero candidates every time. (lanes 1, 2, 7)
2. **The life Zoe could know is on the pendant, and it stops at a flag.** [Proven][live] In 30 days the pendant synced 2,263 recordings (126 hours); the night day-review turned them into 106 verified candidates (reminders, follow-ups, people, facts), about 3.5 a day against the panel's 0.1. Offered to the member: 0. Approved: 0. Written: 0. The wearer is not enrolled as a voice, so 12 of 9,926 speaker segments are attributed to the person wearing it. Samantha knew a life because she was in the ear all day; Zoe hears about ten panel turns a day, 40% of them commands. (lane 7)
3. **"Merged" has been standing in for "done", and two of the instruments were wrong.** [Proven][live+code] Of 23 roadmap items, 5 are live and executing (two of those on empty tables); several flags are on with zero rows or zero markers behind them; the self-evolution improver has run 0 of its 14 nights and its timer is not installed. `ZOE_SMART_TURN_ENABLED=1` and `ZOE_VOICE_BARGE_IN=1` are read only by a LiveKit lane that served zero turns this week, so they do nothing at the wall. And the first follow-through number anyone produced ("31 of 33 reminder promises missed") is a harness artifact: all 33 rows are voice-replay sessions the ledger forgot to exclude. Household promises recorded in 14 days: 0. (lanes 2, 4, 5, 7)

One correction the owner made during the round, recorded so the ranking is honest: the box is in development, and the fleet stopping and restarting services (Kokoro was stopped 128 times in 7.9 days; the voice lander's probe window is one stopper found in code, `scripts/landing/landing_lib.sh:192`, but lane 4 could not attribute the 128 stops from the logs, so the share it causes is unmeasured; zoe-data restarts 24 to 27 times a day) is an accepted cost of this phase, judged by the end result. Those counts are kept in lane 4 and lane 5 as the baseline for the day development quietens; they are not ranked as household findings here.

## 1. The circuit and the box, re-measured (deliver 1)

Full table with window, n and method per row: [lane-1](samantha-frontier-2026-11-11/lane-1-circuit-remeasure.md). The new flags were live by 2026-11-08 evening, so their numbers cover about 3.5 days.

| Metric | 2026-11-03 | 2026-11-11 | Label |
|---|---|---|---|
| Brain tool calls, household brain turns | 1 of 51 | 0 of 51 | [Proven][live] |
| `recall_memory` calls, household | 0 | 0 (harness made 31, so the tool works) | [Proven][live] |
| Continuity injection matches | 0 of 91 | 0 of 30 | [Proven][live] |
| Recall gate | shadow | shadow; 9 household decisions, would_add 2 | [Proven][live] |
| Every-turn recall, coverage line (shadow) | no instrument | relevant row found on 1 of 17 eligible member turns; injected 0 | [Proven][live] |
| Memory served / used in member replies | "83% used none" | served 4 of 32, used 2 of 32 (6%; overlap proxy) | [Experimental][live] |
| Voice history floor | 3 rows | 8 in code and in the built sidecar | [Proven][code] |
| History tokens per turn | ~164 | p50 48; none on 20 of 51 turns | [Proven][live] |
| System prompt tokens (mean) | ~2,836 | 3,320 (resident prompt ~4.6k) | [Proven][live] |
| Night-mind items raised on a turn | readers off | 0 of 42 threads raised; first-turn brief logged 0 lines in 11 days | [Proven][live] |
| Outcome edge | none | 8 rows, 0 with an outcome | [Proven][live] |
| Promise follow-through | not measured | not measurable: 0 household promises recorded (the 33 rows are replay artifacts) | [Proven][live+code] |
| Manner block on member turns | n/a | 30 of 30 | [Proven][live] |
| Member register on member turns | n/a | 0 of 16; table empty | [Proven][live] |
| Speech end to first sound, panel | ~3.2 s / 6.5 s (estimated) | ~3.0 s median / 5.1 s p90, n=72 real turns, joined by turn id | [Proven][live] |
| Night windows completed, last 8 | 5 | 5 (1 refused on memory, 1 never ran, 1 killed mid-window) | [Proven][live] |
| Waking MemAvailable (median) | ~0.5-0.65 GB | ~1.7-2.5 GB | [Proven][live] |
| Real member turns / days per 30 | 349 / 19, one member | 308-394 / 22-25 by filter, one member | [Proven][live] |

**Verdict: open, with one wire closed (manner).** Why history is p50 48 tokens with an 8-turn floor: most panel sessions are one or two turns long, so there is rarely anything to carry. The window was never the constraint; the absence of a standing summary of the person is.

**Where exactly the break is now:**

- **Supply.** Nothing new is being learned from the panel (lane 7 A1), and what the pendant learns is held in shadow (A2). Calendar: 0 upcoming events, no external calendar. People: 0 birthdays. Presence: 190 observations, all `unknown`.
- **Matching.** Injection is decided per turn by a relevance test that needs a capitalised name, two rare keys or a clock time. Real speech is oblique (an invented example, not a household utterance: "what should I get her"). In the synthetic lab (lane 7 B) the production test found the needed fact on 18 of 20 direct turns and 2 of 20 oblique ones; these rows are scored on shared words, never on similarity, and one shared rare word scores 1.5 against a 2.0 threshold. Lowering the threshold to 1.5 lifts oblique hits to 8 of 20 and injects on 10 of 20 irrelevant turns, so the threshold is not the fix.
- **Readers that never fire.** Night mind writes threads nightly; no reader has raised one, and no marker records a serve, so the serve path is unobservable.

## 2. What landed since 11-03, and whether it runs (deliver 2)

Item-by-item table with flag, code default, live value and execution evidence: [lane-2](samantha-frontier-2026-11-11/lane-2-landed-since-1103.md). zoe-data restarted 32 s after the last merge, so every merged zoe-data change is loaded; the question is whether it executes.

| State | Roadmap items (of 23) | Which |
|---|---|---|
| Live and executing | 5 | night-mind serve (2), one voice for every tier (4), hygiene (6), outcome edge (8, no outcomes yet), identity layers (16, register table empty) |
| Live, shadow or log only | 5 | recall enforce (1), coverage instrument (5), HA presence (10), memory views (15), problem loop (17) |
| Flag on, never fired | 1 | proactive policy log (11): 0 markers ever |
| Merged, dark | 4 | duck-decide-resume and addressedness (3), now card (7), improver (13), capability-gap backlog (14) |
| Offline instrument only | 1 | pass^k and collateral damage (12) |
| Partial | 1 | harness out of the household process (18) |
| Not built | 6 | Smart Turn on the Pi (9), items 19-23 |

The eight first experiments: 1 (recall gate replay) and 3 (Smart Turn offline) ran and failed their bars; 2, 4 and 5 were not run; 6 shipped without the blind test; 7 is switched on and has produced no data; 8 has two daytime dry runs and no installed unit.

Other fix waves (QA rounds 4-7, the trust and write-claim guards, the medical floor, forget fixes, lists, alarms): loaded and unflagged or on; their markers fire on synthetic traffic and almost never on household traffic, because the household brain rarely says the things they guard. Named guests, the transcript check, the omi re-pass, the extract pre-pass and conversation notes are dark. The panel runs the 11-09 build exactly (no hand edits); the cut-off, speaker-ID and wake-chime fixes are at the wall.

Docs that now lag the box (not fixed in this record; handed to the modernization session): `docs/PLANS.md` live-flag block, `docs/ROADMAP.md` (says night-mind readers are off), `docs/CANONICAL.md` (`--cache-ram`).

## 3. The answer: from today's Zoe to one a household stops thinking of as software (deliver 3)

First principles. A person stops thinking of something as software when four things are true at once: it knows their life without being told twice, it does what it said, it answers like someone who was already listening, and it is the same someone every day. Each of those has a supply, a path and a proof. On 2026-11-03 the diagnosis was the path (memory never reaches the reply). The path is now mostly built. What is missing is the supply at one end and the proof at the other, and that changes the order of work.

**The rule that sits over every step: done means an execution marker on a real household turn, counted.** Not merged, not flag-on. Every feature ships with one log marker for "this ran on a household turn and changed the packet or the action", and a weekly scorecard reads those markers and nothing else. Lanes 1, 4 and 7 built the queries; they are the scorecard's first draft (section 4, experiment P1).

| Step | Change | The number she cannot fake | Today | Passes at |
|---|---|---|---|---|
| 1. Give her a life to know | Pendant day-review offers on, with an approve-before-write surface; enrol the wearer's voice; find why the panel extractor returns nothing; connect one real calendar | durable rows per day from real life, and the approved share | ~0.1 per day; 106 candidates held, 0 offered | >= 3 approved rows per day over 14 days |
| 2. Put what she knows in front of the brain on every turn | An always-on similarity top-3 after the cached prefix (no threshold, no trigger words) plus the standing per-member card (the now card, built, dark), fed by step 1; the word-match gate stops deciding whether memory is present | memory coverage: share of member replies that used a memory she had | 6% used (2 of 32); relevant row found on 1 of 17 | >= 60% of turns where a relevant memory existed, bar holds, no p90 regression |
| 3. Make her keep her word | Ledger excludes replay, counts promises before the guard, adds `corrected`; outcomes written on dated loops; night-mind threads raised with a serve marker | (kept + corrected) / promises made; follow-through on dated loops | 0 household promises recorded; 0 outcomes | 1.00 over >= 20 promises; >= 60% timely on >= 20 loops |
| 4. Make her answer like someone already listening | Stream the first clause; keep the prefix warm; a sound inside 600 ms; her own voice, never espeak or a bare beep, when the server is away | speech end to first sound, real panel turns | ~3.0 s median, 5.1 s p90; 0 of 72 turns made a sound inside 600 ms | 2.0 s median, 4.0 s p90; 0 espeak lines |
| 5. Make her know who is there, and be herself to each | Fill the member register from real turns; a second member using her; drift battery per release | members and days of real use per 30; persona drift probes | 1 member, 22-25 days; register 0 rows | 2 members, 25 days; no probe regression |
| 6. Make the night real | 7 of 8 windows; self-review given input; daytime consolidation moved into the window; the improver's 14 measured nights | nights completed; improver wins vs the sampling control | 5 of 8; improver 0 of 14 | >= 13 of 14; 10 of 14 or stop |
| 7. Make turn-taking judged | The owner lab hour for duck-decide-resume; Smart Turn as an extender only (it failed as an arbiter) | cut-offs and over-waits per 100 turns; said-vs-did on the corpus | fixed 640/800 ms tail; replay 18 of 18 | < 5 and < 5; 0 regressions |

Steps 1 to 3 are the product. Step 4 is what makes it feel alive. Steps 5 to 7 keep it that way. Nothing here lifts a rock.

**What I would design differently from scratch, and what it has to beat.** One thing: memory delivery that is always present (a standing card plus an unthresholded top-3 by similarity) and lets the brain ignore what is irrelevant, instead of retrieval that a word-match test must first permit. My first guess, the card alone, lost in the lab (R3: 4 and 5 of 20), which is why the top-3 is in the design. The maintained thing the card resembles is the core-block design of the Letta line of work; the house already built its own version (the now card), so the bake-off is internal and cheap: the bar's control arm against a card arm and a card-plus-top-3 arm with per-arm flags (section 4, P2). The risk it must clear is distraction: a 4B given three loosely related facts on every turn may answer worse. Everything else in the dream list resolved to "keep what exists and add one rule" (forget-first memory, section 4 R2) or "not on this build" (slot save/restore, R5).

## 4. Experiments, ranked, with results for those run (deliver 4)

### Run in this round

| # | Experiment | Result | Label |
|---|---|---|---|
| R1 | Re-measure the circuit with the 11-03 methods | Open; break moved to supply and matching (section 1) | [Proven][live] |
| R2 | Forget-first memory lab: can "forgotten means forever" be a property of the write API | 103 tests pass on synthetic data; zero residue over 60 random derivation graphs; lineage-less write caught in 20 of 20 seeds; 8 of 8 mutations turn the suite red; effective forget 2-3 ms to 1e5 sources. Found its own residue: an FTS5 delete leaves the term until `optimize`. Verdict: not a new engine; one rule, "no derived write without lineage". In the real code 12 of 24 derived writers carry source pointers, 4 partly, 8 not. Branch `labs/forget-first-memory` (local) | [Proven] on synthetic; [code] for the writer count |
| R3 | Recall-match lab: production relevance test vs plain top-3 vs standing card, synthetic | 60 synthetic turns, production code called as a library. Production as coded: direct 18/20, oblique 2/20, irrelevant injected 0/20. Production at threshold 1.5: 20/20, 8/20, 10/20. bge-small cosine with a tuned threshold: 19/20, 0/20, 1/20. A 150-token standing card alone: needed fact present on 4/20 and 5/20. Cosine ranks the needed fact in the top 3 on 14 of 20 oblique turns, but no threshold separates those from irrelevant turns. So neither a threshold nor a card alone closes it; an always-on, unthresholded similarity top-3 is the candidate, and whether the brain is distracted by it is untested. The 11-05 carry-over bug is still on main (`recall_gate.py:618`). Branch `labs/recall-match-lab` (local) | [Experimental] |
| R4 | Latency waterfall joined by turn id, Pi log to Orin log | ~3.0 / 5.1 s; largest removable chunk is brain dispatch to first speakable text, 2.04 s median: on 38 of 49 brain turns the first text reached the voice loop only when the brain had finished | [Proven][live] |
| R5 | Slot save/restore on llama.cpp b11982, from source | Rejected: save/restore and the host cache both drop the sliding-window cells. A full re-prefill now costs ~6.0 s median, not 3.6 s | [Proven][code] / [Experimental] for the cost join |
| R6 | Audit of the commitments ledger | 33 of 33 rows are replay sessions; 0 household promises in 14 days; the ledger reads the reply after the write-claim guard, so it cannot see a corrected promise | [Proven][live+code] |
| R7 | Bench numbers on today's commit (lane 3; scratch stack, evidence bound to 1ddb70951) | Samantha bar, 29 cells scored, k=1: 26 PASS, 2 FAIL (S8, a known retrieval-edge flake cell; S11, the forget leg: "forget that" left the item in the recall packet), 1 SKIP; the run's status is `error` because scratch teardown was unproven. Baselined cells: still 8 of 29, unchanged since 11-03 (the re-record launcher refused: live checkout dirty). ZMB store tier 171 PASS / 1 expected FAIL; person bench checkout all halves PASS; forget-completeness + minor-affect + warmth pins 212 of 212; minor-affect false positives 0 of 110. Voice replay, latest PR probe: 18 of 18, STT 484 / brain 1424 / e2e 1598 ms. The nightly's STT failure is a time-of-day effect: 04:xx runs have median STT 745 ms (n=62) against 535 ms (n=913) at other hours, 12 of the last 14 nightlies failed on it, and memory pressure is ruled out. Now-card A/B (harness arm only, one run per arm, same bound tree): 27 PASS / 1 FAIL / 1 SKIP with the card on against 26 / 2 / 1 off; the only difference is S8, the flake cell, so this is noise until repeated; recall packet p95 grew from 2350 to 2509 chars; whether the card was actually served is not verified. Both arms ended with the same unproven teardown, which marks every full run `error` and means the pass^k runner can accept no run: the k=3 night command cannot succeed until that is fixed. Persona drift battery, live, 60 turns: no drift (0.92 at turns 1, 20 and 60; 0.96 mid), with `acknowledges_first` and `voice_marker` failing at every probe point; warmth probes on that synthetic transcript: locale 60 of 60, follow_up 1 of 1, no_parrot 1 of 4. Day-sim: refused by its own gate (a deploy was in flight) and exited 0 on the refusal. | [Proven][live] verdicts; [Experimental] as reliability (k=1) |

### Proposed, in order (all [Speculative] until measured)

| # | Experiment | Success | Failure | Effort | Owner needed |
|---|---|---|---|---|---|
| P1 | Felt scorecard: one read-only script that prints the section 3 numbers from live counts and markers, weekly | reproduces lanes 1, 4, 7 within 5%; runs in < 60 s; no content read | any number needs household text | 2 days | no |
| P2 | Three arms on the bar (per-arm flags, harness only): control, standing card, card plus always-on top-3 | sources-used share >= 2x control; S1-S8 hold; no rise in off-topic memory mentions on the 20 irrelevant probes; p90 within +100 ms | bar regression, distraction on irrelevant probes, or p90 +100 ms | 2 days (the top-3 arm needs a small flag-dark build) | no (harness arm) |
| P3 | Panel extraction split test: replay 20 synthetic panel-style sessions through the idle extractor | separates "nothing to extract" from "extractor fails on panel transcripts" | cannot reproduce zero-candidate passes | 1 day | no |
| P4 | Pendant offers to one member for 14 days, approve before write | >= 50% of offered candidates approved; 0 wrong-speaker writes | < 25% approved or any wrong-speaker write | 2 days + 14 | yes (flag, enrol) |
| P5 | First-clause streaming (lane 4 X3) | dispatch to first text <= 1.2 s median; brain total <= 2.7 s; no rise in re-asks | re-asks rise | 1 day + a week | yes (ear check, flag) |
| P6 | Pi phrase cache in Zoe's voice (lane 4 X1) | 0 espeak lines, 0 beep-only replies across >= 5 outages | any espeak line | 1.5 days | no (Pi deploy by the owning session) |
| P7 | Lineage helper retrofit on three uncovered writers (lane 6) | forget-completeness probe passes with their own erasers removed | any layer still needs its own eraser | 2 days | no |
| P8 | Scheduler enforce, 7 days (lane 5 E1) | full re-prefill share 3.9% -> <= 3.0%; no lost facts | no change | 7 days | yes (flag) |
| P9 | Night reliability (lane 5 E4) | >= 13 of 14 nights; digest ok on >= 12 | <= 11 of 14 | 1-2 days | partly |
| P10 | Hard-budget runtime bake-off (lane 4 X11): native levers vs the dormant LiveKit lane vs an Assist satellite | native passes the 2.0 s budget on >= 70% with a sound inside 600 ms always, and no adopted arm within 10% | an adopted arm within 10%: adopt it | 5+ days | yes (lab time) |
| P11 | Audio-native brain (lane 5 E5; would require the owner to lift a rock to ship) | MTP and the audio encoder co-operate; RAM fits | either fails | 1 night window | yes |
| P12 | What a larger box changes (lane 5 E7), without buying | >= 70% of night failures and reloads are RAM-caused | most are software | 0.5 day | no |

## 5. Owner items (deliver 5)

Each needs a flag, a token, lab time, a purchase or a rock decision. Recommendation and cost in one line each.

1. **Show yourself the pendant's candidates.** Turn day-review offers on for yourself with approve-before-write, after the wearer's voice is enrolled from the panel. Recommend yes; this is the single largest lever in the record. Cost: about 15 minutes of enrolment, then a minute a morning.
2. **The now card on, after the bar A/B (P2) passes.** Recommend yes. Cost: one flag.
3. **First-clause streaming: a 15-minute ear check, then the flag.** Recommend yes. Cost: 15 minutes.
4. **The duck-decide-resume lab hour, still owed from 11-03 decision 3.** Recommend booking it. Cost: one hour at the panel.
5. **The read-only Home Assistant token, still owed from decision 4.** Presence has 190 observations and all are `unknown`. Recommend yes. Cost: minutes.
6. **Connect one real calendar.** 0 upcoming events means anticipation has nothing to anticipate. Recommend yes. Cost: a QR flow on the phone.
7. **Scheduler enforce for 7 days (P8).** Recommend yes after P3, since it moves daytime consolidation. Cost: one flag, one week.
8. **Clear the 33 replay rows from the commitments ledger** once the exclusion lands. Recommend yes. Cost: one approved delete.
9. **Install the improver's timer and start its 14 nights**, or stop calling it landed. Recommend start. Cost: capped dev credits, already approved in decision 5.
10. **A second member using her.** No engineering closes a one-member signal. Cost: the owner's.
11. **Rock-adjacent, for information only:** the audio-native lab night (P11) and the larger-box classification (P12). No decision needed until they report.

## 6. What could not be verified, and why (deliver 6)

- Whether the panel extractor returns nothing because there is nothing to extract or because it fails on panel transcripts: separating them needs turn text, which this round did not read (P3 does it synthetically).
- Night-mind serves per turn: no marker exists for the block.
- Judged relevance of recall: the coverage line and the overlap proxy are lexical; nobody scored relevance by hand.
- The exact time each flag went live: inferred from an env file mtime and first markers.
- The two usage counts (308 on 25 days, 394 on 22 days) differ by filter; the difference was not reconciled.
- The owner confirmed during the round that another session runs QA test chats and deletes those chats and what was learned from them afterwards. The lanes excluded harness-tagged, `qa`, replay and synthetic sessions, and the extractor finding (25 passes, zero candidates) is counted from log markers on panel sessions, which a later delete does not change. But any database COUNT here (member turns, durable rows per day, the 1 row since 11-03) is a count of what remained on 2026-11-11: if a QA chat ran under the member's identity and was then removed, it lowers those counts and could explain part of the usage-count gap. Treat the row counts as a floor and the log-marker counts as the firmer evidence.
- Whether a lineage rule survives model paraphrase, stores outside sqlite, and writers a single pass missed (lane 6).
- All third-party claims in lanes 4 and 5 not read from source are marked [record: unverified] in place; hardware claims about a box not on the bench are [Speculative].
- pass^k at k=3, the live continuity cells (no live adapter exists; carry at 1/7/30 days is still unmeasured), a now-card A/B that is admissible (k=1 per arm, card-served not verified), the conversation-notes and recall-every-turn A/Bs, the person bench live and the day-sim (refused: deploy in flight): see lane 3 for what ran, what was refused and why. S8 and S11 cannot be called flaky or regressed from a single run.
- Why Moonshine is about 1.5x slower in the 04:30 slot: unexplained; lane 3 lists three candidates and the one-probe test that separates them.

- Addendum, 2026-11-12 00:55: three findings handed over from this record were merged while it was being written (#2409 the pendant re-pass names a missing voter and degrades the night; #2410 an all-zero idle-consolidation pass is a warning that names why; #2411 a replay turn records no commitment). Every number above is for 1ddb70951, before those merges. None of the three has been re-measured on a household turn here, so by this record's own rule they are merged, not done. [record]
