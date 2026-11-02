---
type: research
title: Synthesis deep dive (2026-10-29) - what three rounds and eleven days of watching say together, and the order that gets Zoe to Samantha
date: 2026-10-29
status: research-only; a synthesis of the three evidence rounds (2026-10-18, 10-19, 10-27) and daily live reads since, with this morning's ground truth. No new lanes; nothing built or changed. Handed to the "Zoe comprehensive review and modernization" session as the frame for its next month.
description: Owner ask - "do another deep dive based on your knowledge." The diagnosis - Zoe no longer lacks capability; it has three imbalances (throughput over verification, instruments over-trusted, headroom spent on development) and one missing input (the household barely talks to her, so there is little signal for anything to evolve on). Six cross-cutting patterns with their instances, a re-ranking by what the household feels, a cut list, a thirty-day order, and the owner decisions that gate it.
evidence: docs/research/architecture-deep-dive-2026-10-18.md, architecture-deep-dive-2-2026-10-19.md, stack-currency-and-field-2026-10-27.md and their lane bundles; docs/architecture/self-evolution-nightly-loop.md; the open-problems ledger; live reads 2026-10-19 to 10-29 (ground truth, PR history, memory, the night-window log, usage counts).
---

# Synthesis deep dive (2026-10-29)

> Three rounds produced about four hundred findings. This record is not a fourth list. It is what the lists say when read together, and the order that follows from it. The rocks are fixed; nothing here is about the model.

## 0. The diagnosis in one paragraph

Zoe's memory core is better than the engines it was benched against, her voice path is measured more carefully than anything comparable, and her safety floors are real. What she lacks is not capability. She lacks balance in three places and one input. **Throughput over verification:** about fifteen deploys and seventeen restarts a day for a month, with four live security items unchanged a week after their fixes merged and the panel running a stale daemon for a week after its fix merged. **Instruments over-trusted:** a nightly suite that was green for fourteen nights while running nothing, a voice gate that scores "did not refuse", a bar baseline covering eight of twenty-three cells, a judge that is the model under test. **Headroom spent on development:** the test harness is ninety-seven percent of the brain's traffic and evicts the panel's prompt cache; development agents hold about two gigabytes; this morning the box had 465 MB available against a 700 MB voice floor and the nightly gate failed on STT speed. And **the missing input:** the household used Zoe on nine of thirty days in October, one member, in bursts, with no panel turns for days at a time. A self-evolving system with no signal evolves toward its harness. Everything below follows from those four sentences.

## 1. Six patterns that no single lane saw

**1. Merged is not live, and nothing checks.** The Multica loopback fix merged 18 October; the container was never recreated and the deploy workflow does not touch the modules compose (fixed yesterday in #2192). Panel-as-code merged 20 October; the Pi ran the old daemon until this week (ground truth shows the sha only since yesterday). The off-box backup merged 18 October; the tooling sat uninstalled for ten days. The night mind was documented "off" while running in enforce. Ten flags were live while documented dark, five documented done while shadow. The pattern is structural: the repository is the unit of work, the box is an afterthought, and the only instrument that reads the box is a shell script someone has to run. **Consequence:** every "done" in PLANS needs a live column, and the Zoe score's "unmeasured is zero" rule is the fix at the root.

**2. Every instrument fails open somewhere.** `continue-on-error` on the nightly; `-x` in the gate; OK defined as "not a refusal"; `regressions` iterating only the baseline's keys; the bar's judge being the same Gemma; `REQUIRED_CONTEXTS` as a constant; critical-file checks that test existence; a flag inventory that could not see the flags driving the live chat path. The team repeatedly fixed instances (the person bench's control arms, #2137, #2154, #2176, #2187 this fortnight are good work). The class remains: nothing requires an instrument to prove it can turn red before its green counts. **Consequence:** the self-evolution loop, which is gated entirely by instruments, cannot start until the self-test rule is universal.

**3. The box is a development machine first and the household's second.** 97% of brain turns and 96% of STT calls were harness traffic; 29 of 100 panel turns re-prefilled thousands of tokens because the harness shared the one llama slot; Kokoro reloaded 178 times in eight days behind deploys; at 07:36 today two Claude sessions, Serena, Codex and the Omnigent container held about 2.3 GB while the voice floor was breached. Round 2's quiet hours and traffic tag landed (#2145); the slot question is now a research record (#2209). **Consequence:** until the household's hours are protected by policy and the dev budget on the box is explicit, the household's p90 is set by the engineering fleet, not by the brain.

**4. Generations accumulate; nothing retires on its own.** Eight harness generations, three brain lanes with one served, fifteen plans of record, 681 flags with 93% read in one file, five tool-schema lists, thirty-nine memory writers, forty-five empty tables, two voice pages, three proposal writers with 716 unread rows, nine retired units still installed. Each was a reasonable step; the sum is what makes "what is live" unknowable and every change expensive. Retire-by-removal is in the rules and rarely happens because deletion earns no merge. **Consequence:** the cut list in section 4 is the cheapest way to raise every axis of the score at once.

**5. Privacy is excellent at the core and uneven at the edges.** Authority classes held 237 of 237; forgotten-means-forever is enforced in the palace; the brain fence is genuinely good. Meanwhile a household chat database sat in the public repository for five months, raw utterances sit in the router shadow log and the STT log, the brain keeps a 447 MB transcript store no forget reaches, the kiosk logs transcripts to plaintext, and household names and coordinates are in the public tree. **Consequence:** the forget inventory must be generated from the live catalog and the filesystem, not from code that knows about zoe-data only; and the public-repo question is a decision, not a chore.

**6. The household has not been given a reason to talk to her.** Voice identity recognised nobody until 18 October; since strict identity, most turns ran as guest with PIN prompts. Music last played in July and nobody was told it was broken. One wake in five became nothing; one transcript in ten was empty; the p90 answer took six seconds. Nothing in Zoe says "I'm updating" or "music is down"; she goes quiet. No surprise that use is bursty. **Consequence:** reliability and identity are not polish items behind Samantha features; they are the precondition for having any Samantha signal at all. The owner's own test bursts (94 turns on 12 October, 37 before 07:36 today) are the only sustained use, and they are also harness in disguise.

## 2. What Samantha needs now, ranked by what the household would feel

1. **She is there when spoken to.** No restarts in waking hours; deploy windows; the daemon says "one moment" instead of silence on a 502; Kokoro not reloaded by every deploy; the dev budget on the box capped during the household's hours.
2. **The first word comes fast and warm.** The panel's prompt cache stays resident (a slot or save/restore; #2209 is the study); the endpoint tail trimmed; the cold-first-turn fix (#2189) verified live.
3. **She knows who is talking.** The ResNet34 and voice-session flip measured on fifty real turns; every adult enrolled at the panel; the enrolment offer reaching unknown voices; children not collapsed into guests by accident.
4. **The things she can do work.** Music reconnects or says it is down; timers ring on the panel (#2208 open); lists keep context (#2211 open).
5. **What she knows reaches what she says.** 83% of real replies used zero memory sources and continuity was empty on 97% of turns. Recall is built; it is not firing in the real path. This is the highest-leverage Samantha item that is pure tuning.
6. **She tells you the truth about herself.** One number on the Zoe page, three plain words, "what changed", "undo that".

Everything else in the three rounds (the comms layer, the iPhone app, the nightly loop, the relay) is real and should proceed, but after these six, because each of these six generates the signal the rest will need.

## 3. The constraint stack

RAM gates the slot, the slot gates the p90, the p90 gates use, use gates signal, signal gates self-evolution. Development on the box sits at the top of that stack and is the one lever that costs nothing to pull: a quiet-hours policy for agents (one interactive session during the household's peak hours, harness and probes only in the sleep slot, Omnigent capped) buys more than any upgrade in round 3. The DGX Spark decision should be made against this stack: it is worth buying when the Orin's 12B cannot run one useful nightly cycle, which Phase 3 will measure, not for raw speed.

## 4. The cut list (what a fresh senior engineer would delete first)

Each line removes risk and RAM or ambiguity, none removes a household capability, and all are in the three rounds with evidence:

- The legacy brain lane (`zoe_agent.py` as a lane; keep its library functions in neutral modules), the dormant core lane after one parity decision, and the boot-time KV warm of prompts flue never sends.
- Resemblyzer and torch from zoe-data; torch and torchaudio and tflite-runtime from the Pi.
- The seven test-only production modules, the 188 dead tests, the two unconditionally skipped "security" test files, the orphan touch voice page and the `/ws/voice/` lane (done in #2127), the retired units still installed, the eleven `.env.bak` copies and the 115 worktree env copies.
- The forty-five empty tables after one decision each (adopt the contacts and calendar models, or drop them), the two dead music-history tables, the SQLite dev database still mounted into zoe-auth.
- Fourteen of the fifteen plans of record, absorbed into one roadmap; the dated addenda in living docs; the three proposal writers folded into the single queue (#2165 built it).
- Flags that have been on or enforce for thirty days, promoted to code; the inventory then shrinks on its own.
- The engineering harness out of the household process (greploop, pipeline, Multica loop); Multica's autopilots moved to zoe-data's own scheduler so the board can be paused or retired without touching the morning check-in.

## 5. The thirty-day order

- **Week 1 (in progress):** finish and live-verify what is open; land the security-now batch (#2192 landed); apply the four stale live items with the owner's approvals; land or close the two draft records.
- **Week 2, reliability and identity:** deploy windows and the dev budget as policy; the daemon's "one moment"; Kokoro stable across deploys; the slot decision from #2209 implemented and replay-gated; fifty real turns measured for identity, then household enrolment; music health visible. No new features.
- **Week 3, honest instruments and the first number:** the universal self-test rule (every gate script must turn red on a planted fault before its green counts); the 23-cell clean baselines; the forget inventory from the live catalog; the Zoe score v0 computed nightly with "unmeasured is zero", shown on the owner's desktop only.
- **Week 4, the loop's first night:** Phase 1 of the nightly loop on the dev box with Omnigent, synthetic only, apply behind a flag with the canary in shadow; the recall-not-firing investigation as its first target; the cut list begun one item per PR.
- **Then:** the comms layer P0 (the native LAN intercom merged flag-dark in #2143 gets its lab cells), the iPhone app M0, the relay R1, the Spark measurement.

The rule for the month: nothing new starts until the Zoe page shows a number and the household has used her on five days in a row without a silent failure.

## 6. What this review itself got wrong, and the limit of more reviews

- My sessions are part of the 2.3 GB of development memory I am criticising, and my lanes were part of the harness traffic the traffic lane measured. Observation here is not free.
- Round 1 called the LiveKit container dead; round 2 said the panel's debug port was open; round 3 propagated a lane's wrong licence for Sygnal until checked. Every one was caught by a negative control, which is the only reason to trust the rest.
- Three records and sixteen lane reports in eleven days is itself the docs-mass pattern of section 1.4. The right end for them is absorption into the one roadmap and the Zoe page, then retirement from the tree. A fourth evidence round would be the wrong next move; the next move is the thirty-day order above, measured.

## 7. Decisions for the owner

1. **The development budget on the box during the household's hours** (recommendation: one interactive agent session at a time 06:00 to 22:00, harness and probes only in the sleep slot, Omnigent memory-capped; everything else on the Mac or the cloud).
2. **Whether the repository goes private** now that the leak and the exposure maps have shown the cost of public; the self-evolution and iPhone work make it more likely, not less, that household detail lands in a commit.
3. **The DGX Spark timing:** after Phase 3 measures the Orin's 12B, not before.
4. **The four self-evolution decisions** still open from the design (credit cap, tool guidance in scope, canary bands, lessons to the411).
