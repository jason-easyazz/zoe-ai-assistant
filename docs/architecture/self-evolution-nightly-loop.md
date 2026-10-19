---
type: design
title: The nightly self-evolution loop - propose, probe, compose, apply, behind the benchmarks, with no questions asked
date: 2026-10-19
status: design only; nothing built. Every phase ships flag-dark, lab-proven on the existing instruments first. Owner decisions of 2026-10-19 are binding (section 1).
evidence: docs/research/architecture-deep-dive-2-2026-10-19.md (round 2: instruments, traffic, restarts, the harness on the box), docs/research/architecture-deep-dive-2026-10-18.md (round 1: the eight harness generations, the proposal writers nobody reads), docs/research/best-ideas-register-2026-10-09.md (the 4B must never reflect on itself; what works has an external deterministic signal), google-research/harness2 (arXiv 2610.14224, Apache-2.0; the Propose-Probe-Compose mechanism and the per-domain lesson library; read 2026-10-19, created 2026-10-06, a research artefact, not a dependency)
builds_on: labs/flue-zoe-brain-2x (soul.ts, agents/zoe.ts doctrines, manner.ts, tools/zoe-tools.ts), scripts/perf/samantha_bar.py, samantha_day_sim.py, samantha_person.py, scripts/maintenance/voice_regression_probe.py, scripts/night/night_window.py, struggle_detector (phase 2 night proposals, #2109), the evolution_proposals table, modules/omnigent, docs/architecture/p0-deroot-plan.md (#2101), docs/knowledge/offbox-backup.md (#2119), docs/architecture/credential-vault.md (#2100)
---

# The nightly self-evolution loop

> Owner, 2026-10-19: *"this could be the self evolution we have been reaching for"*; *"if someone buys or installs zoe, it stays as is, fixes basic stuff by itself, but then if they want it to evolve they give omnigent a login to either anthropic or openai and zoe starts to evolve"*; *"i dont really want it asking questions, I just want it checking against benchmarks, if an 80 year old has zoe, they wont know what its talking about, but you still want it to improve"*; *"can this incorporate updates to programs?"*; *"i want this running nightly not weekly"*. The rocks (Gemma 4 E4B + MTP, Moonshine, Kokoro) do not change; the harness around them does.

## 0. Answer first

1. **One spine, five stages, run every night:** signals → one proposal queue → an improver (Propose-Probe-Compose over a harness overlay) → the benchmark gate → apply with a canary and automatic rollback, plus a lesson library. The household is never asked anything. "What changed?" and "undo that" work by voice.
2. **Three change classes ride the spine.** Harness changes (prompts, doctrines, manner, tool guidance, recall shape) are files in an overlay directory, applied nightly when they win on the benchmarks. Program updates (llama.cpp builds, Flue runtime, Music Assistant, Home Assistant, Kokoro, Python pins) are signed releases, benchmarked upstream, applied by a caretaker with backup-before-update and rollback. Code changes go through the maintainer's PR process only. The safety floors (restraint, authority classes, forgetting, privacy, kids and guests) are never in scope for the loop.
3. **Three tiers, same spine, one switch: which model is the improver.** Tier 0 "stays as is": no model, a caretaker that fixes basic things. Tier 1 "evolves locally": the 12B in the night window proposes, the 4B probes, a deterministic rule composes. Tier 2 "evolves with a login": the household gives Omnigent its own Anthropic or OpenAI key once at setup; a strong model proposes and composes; probes stay on the box. A DGX Spark moves Tier 1 up to Tier 2's capability with no cloud key.
4. **Nightly fits the Orin today.** Measured: a bar run is about 4 minutes (median 240 s), a day-sim about 3.5 (201 s), a person bench about 7.5 (449 s). Two candidates plus a baseline on a targeted scenario subset is 20 to 25 minutes on the 4B, which fits between the night window's restore (03:55) and the voice regression (04:30), while the household sleeps (no panel turns 02:00 to 06:00 in the traffic record). The timetable is in section 4.
5. **The instruments are the gate, so they must become honest first.** Round 2 found the voice gate scores "did not refuse", the bar baseline covers 8 of 23 scenarios, and the nightly test workflow is fail-open. Phase 0 fixes those before any change is applied automatically; a loop that trusts a gameable judge will learn to please it.
6. **Omnigent is the Tier 2 improver and the dev-box lab proof**, under three conditions that round 2 already lists: probes write to a scratch database and the improver sees synthetic context only (a guard refuses real member ids); Omnigent is not root while it is autonomous (de-root steps 0 and 1 first, a memory cap, its own capped key); and it proposes and applies through the gate, never flips anything live by itself.
7. **This replaces, it does not add.** The struggle detector's night proposals, the night mind's proposals and the `evolution_proposals` writers (716 pending, 2 approved) become one queue with evidence attached, or they are deleted. Round 1 counted eight harness generations; this must not be the ninth.

## 1. Owner decisions this design obeys (2026-10-19)

| Decision | Consequence in the design |
|---|---|
| Nightly, not weekly | the loop runs every night; the per-component budget is "one change in canary at a time", not "one a week" |
| No questions to the household | the gate is the instruments; consent is asked once, at setup, by whoever installs; afterwards only "what changed?" and "undo that" |
| Checked against benchmarks | acceptance = no previously-passing cell turns red, the targeted cells improve, the voice replay passes, the live canary holds; control arms must stay red |
| Stays as is unless they want evolution | Tier 0 is the shipped default; Tier 2 is the one explicit switch (a provider login) |
| Omnigent gets the login | Omnigent is the Tier 2 improver; the key lives in the household vault, never in a config file |
| Incorporate updates to programs | the release change class (section 7) |
| DGX Spark may change this | the tier boundary moves, the spine does not (section 6.4) |
| Earlier, still binding | the rocks are fixed; nothing leaves the box unless opted in; no unprompted speech; forgotten means forever; voice-path changes are replay-gated; one test lane on the box |

## 2. The spine

```
 signals ─────► one proposal queue ─────► improver (PPC) ─────► gate ─────► apply + canary + rollback
 struggle events,  evolution_proposals   propose K edits        held-out     overlay dir (harness)
 repeat asks,      class ∈ {harness,     to the overlay,        bar/day-sim  signed release (programs)
 empty transcripts, release, code}       probe each on the      replay gate  PR (code, maintainer only)
 bar/day-sim/replay evidence attached,   same tasks, compose    control arms           │
 results, health    risk tier            from the contrast      live canary            ▼
                                                                              lesson library (per domain),
                                                                              optional lessons to the411
```

- **Signals.** The struggle detector (`struggle_events`), empty-transcript and wake-without-turn rates, repeat asks within a session, corrections, the bar, the day-sim, the person bench, the voice regression, and health. Round 2 lists the ones that are blind to the household today (speaker verdict never fires for the real panel, no intent label on misses, no tool-name logging, proactive outcomes unresolved); those are prerequisites, because the canary (section 5) reads them.
- **One proposal queue.** `evolution_proposals` gains `class`, `risk_tier`, `component`, `evidence` (scores before and after, control-arm results, the diff), `status` (proposed, probed, applied, canary, kept, reverted, rejected) and `lesson_id`. Every other proposal writer feeds it or is removed.
- **The improver.** Section 3.
- **The gate.** Section 5.
- **Apply, canary, rollback, lessons.** Section 5 and 6.

## 3. Propose, probe, compose, on a harness overlay

### 3.1 The overlay (Phase 0, the one seam everything needs)

The Flue sidecar's harness is TypeScript constants today: the soul (`soul.ts`, a drifted copy of `SOUL.md`), the doctrines in `agents/zoe.ts`, `manner.ts`, the tool guidance in `tools/zoe-tools.ts`, the recall-packet shape in `zoe_flue_client.py`. Nothing can try a variant without a rebuild. The seam:

- `ZOE_BRAIN_HARNESS_DIR` (default unset = compiled-in text). When set, the sidecar loads `soul.md`, `doctrines/*.md`, `manner.md`, `tool-guidance.md` and `recall-shape.json` from that directory at start, each with a sha recorded in the turn log.
- A parity test asserts the overlay rendered from the repo equals the compiled text, which also closes the soul-versus-SOUL.md drift round 1 found.
- `overlay/current` is a symlink; `overlay/<ts>-<proposal>` are versions; revert is re-pointing the symlink and restarting the sidecar (about 10 s). The ledger is `overlay/ledger.jsonl`.
- Out of scope for the overlay, by construction: anything in `restraint.py`, `memory_authority.py`, the forget cascade, `guest_policy.py`, identity, the brain's untrusted-content fence, tool *permissions* (only tool *guidance* text is editable). The loop cannot propose outside the overlay, and the overlay loader refuses keys it does not know.

### 3.2 One night's loop

1. **Pick the target.** From the day's signals, one component and one domain (for example "calendar questions, recall shape" after three repeat asks about dates). Deterministic rule, no model. If no signal, pick the oldest-unimproved held-out failure.
2. **Reference runs.** The current overlay on the targeted scenario subset (the day-sim asks and bar cells for that domain) plus a fixed sanity set, on the 4B, writing to the scratch database. This is the contrast baseline, re-run every night so drift in the brain itself is visible.
3. **Propose K=2 candidates.** One general edit (invariant, applies across domains) and one domain edit, each a small diff to one overlay file. The improver sees: the targeted scenario transcripts and scores from step 2, the lesson library for that domain, the current overlay text. It never sees the needles, rubrics or judge prompts (judge separation, as the paper does), and never any real member's words (section 6.3).
4. **Probe.** Run each candidate on the same subset. About 3.5 to 4 minutes per candidate on the targeted subset; the full bar is weekly, not nightly.
5. **Compose and select.** Tier 1: deterministic, the candidate with the best delta that violates no rule. Tier 2: the improver may also compose a third candidate from the contrast between the two, which is probed too (K=3, about 4 more minutes).
6. **Gate** (section 5). If it passes, **apply** at 04:28 and let the 04:30 voice regression be the first canary check: a replay failure reverts the overlay before anyone wakes.
7. **Reflect.** Write the lesson: what was tried, the evidence, kept or reverted, into the per-domain library (`docs/knowledge/harness-lessons/<domain>/`, an OKF bundle on the box; synced to the repo by the maintainer, never automatically). Lessons hold no household words.

### 3.3 Tasks and scenarios

- The synthetic households of the bar, the day-sim and the person bench are the task stream. They are already pre-registered and seeded.
- A **held-out set** (about a third of the cells, chosen once, hashed) is never shown to the improver and is the regression floor.
- A **local day replay** (Tier 1 and Tier 2 opt-in only): yesterday's real turns replayed against a candidate, scored by deterministic checks (did the same tool fire, did the reply contain the same facts, latency), never by the improver. Stays on the box.

## 4. The nightly timetable on the Orin (measured, not hoped)

| Time | What | Model up | Notes |
|---|---|---|---|
| 02:30 | `zoe-backup` (and the off-box push once #2119 is installed) | 4B | existing |
| 02:40 | `zoe-memory-export` | 4B | existing |
| 02:50 to 03:55 | the night window: digest, dreaming, night mind, day review; **Tier 1 propose** runs last in the window on the 12B using the previous night's probe results | 12B (4B down) | the window's own cap is about 63 minutes; propose is about 3 to 6 minutes of 12B time at the measured 2 to 7 tok/s |
| 03:55 | restore the 4B, Kokoro, the router, zoe-data | 4B | existing |
| 04:00 to 04:25 | **probes**: reference + K candidates on the targeted subset, scratch database, harness traffic tag set | 4B | 3 runs × 3.5 to 4 min; the person bench only when the target is a manner cell (7.5 min) |
| 04:25 | compose, gate, apply the overlay, restart the sidecar | 4B | about 1 minute |
| 04:30 | `zoe-voice-regression` | 4B | existing; a fail here reverts the overlay |
| 04:45 to 05:30 | spare: the weekly full bar on Sundays; the upgrade-train lab run when a release candidate exists | 4B | |
| 05:30 | reflect, lesson, ledger, morning note | 4B | |
| 06:00 | household wakes; nothing of the above is running | | the record shows the first panel turns at 06:00 |

Tier 2 differs in one row: the cloud improver proposes between 00:30 and 02:30 (and may compose a third candidate at 04:15); everything else is identical. Nothing in the loop runs between 06:00 and 02:30, when the household uses Zoe, and the probes carry the harness traffic tag so they never count as household use again.

## 5. The gate, the canary and rollback

**Preconditions (Phase 0).** From round 2: the voice probe gets per-clip expected outcomes and a self-test that must fail; the bar baseline covers all 23 scenarios on a clean tree and refuses comparisons with missing cells; the day-sim and the person bench get baselines; probes write to a scratch database; the nightly test workflow stops being fail-open. Without these the gate is theatre.

**Acceptance, all required:**
1. No previously-passing cell in the held-out set or the sanity set turns red.
2. The targeted cells improve by at least the pre-registered margin (Wilson bound, as the person bench already does).
3. The two control arms stay red: a sycophancy editor (adds only agreement and warmth) must lose; a repeated-sampling arm (K extra runs of the current overlay) must not match the candidate's gain at equal cost. If sampling matches, the change is noise and is rejected.
4. The voice regression passes after apply (04:30).
5. Rate limit: at most one change in canary per component at a time; a component in canary is not targeted again until the canary ends.

**Canary (three nights, live).** The applied overlay stays unless the live signals degrade against their trailing week: empty-transcript rate, repeat asks within a session, corrections, struggle events, first-token latency. Any degradation beyond the pre-registered band reverts the overlay automatically and records the lesson as "reverted in canary, reason". Kept changes are promoted to `kept`.

**Voice commands, no dialogue:** "what changed?" answers one plain sentence per kept change in the last week, from the ledger, never from the model; "undo that" reverts the most recent kept change. The person who set the box up gets the technical trail on their phone (the comm-prefs design's `decision` and `broke` categories, layer 2).

## 6. Tiers

### 6.1 Tier 0, stays as is, fixes basic things

No model in the loop. One tracked caretaker unit with a fixed-function allowlist, replacing the five overlapping watchdogs and untracked scripts round 2 found: restart on hang (the accept-queue signature), index compaction when the HNSW ratio passes 3, backup and off-box verify, provider reconnect prompts (the music sign-in that silently died in July), log and forget hygiene, the release check (section 7). It applies signed releases only.

### 6.2 Tier 1, evolves locally

The 12B proposes inside the night window (it is already loaded; the owner wants it to earn its keep), the 4B probes, a deterministic rule selects. The improver's context is the synthetic transcripts, scores and lessons; the local day replay is allowed because it never leaves the box. Expect one small change attempt a night and a modest win rate; measure it (section 9) rather than assume it.

### 6.3 Tier 2, evolves with a login

- Setup asks once: "Let Zoe improve herself using this account?" with the provider's name and what leaves the box: synthetic transcripts and scores by default, the household's own day replay only if a second switch is on. The key goes into the household vault (#2100) and is read by Omnigent through the broker, never by zoe-data, never from a config file.
- Omnigent runs the improver as a skill (section 8) with its own capped key, a memory cap, and no root: de-root steps 0 and 1 are a precondition. It reads results from the scratch database and the run artefacts, never the household's live tables; a guard refuses any payload containing a real member id.
- Egress policy per provider is recorded in the ledger; "undo evolution" at setup revokes the key and stops the skill, the kept overlays remain (they are files, already benchmarked).

### 6.4 Where a DGX Spark changes it

The spine and the gate are unchanged. With 128 GB of unified memory a 27B or 70B improver runs locally beside the 4B voice brain, the probes run in parallel, and the whole bar fits nightly; Tier 1 then reaches Tier 2's capability without a cloud key, and Tier 2 becomes optional rather than the only strong path. The measurement that justifies the purchase is Phase 3's: cycles per night and win rate on the Orin's 12B. Tracker item B0.15 should take that number, not a benchmark speed.

## 7. Updates to programs (the release change class)

- **Dev box (upstream): the upgrade train.** Omnigent watches the pinned upstreams (llama.cpp builds, the Flue runtime, Music Assistant, Home Assistant, Kokoro, the Python pins), respects the 14-day age rule, opens one PR per upgrade, and lands it through the normal gates: validate, the voice replay (which must first be fixed to install the pin it gates, round 2 T-3), the bar. A landed train becomes a signed release bundle (code sha, image digests, venv lock, model manifest hashes, migration head).
- **Household box: the caretaker applies releases.** Download, verify the signature, back up (Postgres dump, palace tar, overlay dir, `.env*`) to the off-box target, apply, run the health probe and a short local replay, roll back on failure to the previous release kept on disk. Home Assistant's supervisor is the reference. Staged rollout by `<hid>` through the411 so a bad release reaches a few boxes first.
- **Never:** the box pulling arbitrary upstream images or packages on its own; the loop editing code; a release without a tested rollback. The rocks' weights never change through this path; llama.cpp builds around them do, replay-gated upstream.

## 8. The Omnigent skill contract (`zoe-harness-evolve`)

- **Job:** one night's Propose-Probe-Compose for one component and domain, as given by the signal step.
- **Inputs:** the current overlay text, the targeted scenario transcripts and scores (scratch database, synthetic), the domain's lesson library, the pre-registered margins. Never: needles, rubrics, judge prompts, live tables, any text carrying a real member id (the guard runs before the skill sees the payload).
- **Allowed:** write candidate overlay directories; invoke the probe runner through its CLI with the harness traffic tag; write a proposal row with evidence; write a lesson.
- **Forbidden:** editing anything outside `overlay/`; touching flags, env files, units, the live database, the panel; opening PRs (the upgrade train is a separate skill with its own contract); calling any provider other than the configured one; running between 06:00 and 02:30; running when `MemAvailable` is below the voice floor.
- **Model routing:** the household's key (Tier 2) or the owner's dev credits on the dev box; worker tier for proposing, a stronger tier only for composing; cost cap per night recorded in the ledger.
- **Output:** a proposal row (`probed` with evidence), the candidate overlays, one lesson record. The gate and apply are zoe-data's, not the skill's.
- **Evaluation of the skill itself:** the two control arms, run weekly by the skill against itself; a skill whose controls stop going red is disabled by the caretaker.

## 9. Phases, measurements, stop rules

| Phase | Builds | Measure | Stop rule |
|---|---|---|---|
| 0 (1 to 2 weeks) | the overlay seam + parity test; honest instruments (per-clip voice outcomes with a self-test, 23-cell clean baselines, scratch database, traffic tag, nightly workflow fail-closed); the proposal queue schema | the held-out set and control arms exist and go red on planted faults | none; this is prerequisite work round 2 already lists |
| 1 (2 weeks) | `labs/harness2-spike`: the nightly loop on the dev box with Omnigent as improver (owner's credits, synthetic only); apply behind a flag, canary in shadow (records what it would revert) | cycles per night, win rate vs the sampling control, time used, cost per night | if the candidate does no better than repeated sampling at equal cost over 14 nights, stop |
| 2 (1 week) | apply for real with the canary; the Tier 0 caretaker; the proposal writers it replaces deleted; "what changed" / "undo that" | kept vs reverted per week; zero household-visible regressions | a canary revert that the gate should have caught stops auto-apply until the gate is fixed |
| 3 (2 weeks) | Tier 1: the 12B proposes inside the window; deterministic compose | cycles per night and win rate on the Orin's 12B | feeds B0.15 (the Spark decision) |
| 4 | the upgrade train skill; signed releases; the caretaker's release path; the the411 lesson channel (opt-in, no household words) | a release applied and rolled back in the lab | none |

## 10. Decisions still open

1. The dev-credit cap per night for Phase 1 (small; record it).
2. Whether tool *guidance* is in the first scope (it is on the voice path, so every applied change there is replay-gated at 04:30 anyway) or only recall shape and manner at first.
3. The canary band per signal (pre-register before Phase 2).
4. Whether lessons may leave the box to the411 by default or opt-in only (recommend opt-in, consistent with "nothing leaves the box unless opted in").

## 11. Risks, stated plainly

- **A gameable judge.** The bar's judge is the same Gemma that answers. Judge separation, the held-out set and the sycophancy control are the defence; if the controls ever stay green, auto-apply stops.
- **The ninth generation.** Phase 2 must delete what it replaces; the record of round 1 is the checklist.
- **The Orin's 12B may be too slow or too weak to propose anything useful.** Phase 3 measures it; Tier 2 or the Spark is the answer if so.
- **Night budget contention.** The window is already 02:50 to 03:55 and the owner wants the 12B used; proposing is the last job in the window and yields if the window runs long. The probes have their own slot.
- **Harness traffic.** Round 2 found the harness is 97% of the brain's load and evicts the panel's prompt cache; the loop runs only in the sleep slot, tagged, on a scratch database, and the panel's KV is re-warmed after 04:30 by the existing prewarm.
- **Privacy.** The improver's inputs are synthetic by default; the local day replay never leaves the box; lessons carry no household words; the Tier 2 key is in the vault; the whole loop is subject to the forget cascade (candidate overlays and probe transcripts are stores and must be in the inventory).

## 12. The Zoe score (owner, 2026-10-19: "a stats page where everything Zoe keeps track of gets an overall score, and Zoe is always aiming for 100%")

**What it is.** One number, 0 to 100, recomputed every night from the same artefacts the loop reads, with the axes beneath it. It is the objective function of section 3: the nightly target is the lowest axis that is not in canary, and a kept change is one that moved an axis without dropping another. It is also the "Zoe's own brain to 10/10 and 100%" directive of 2026-10-09 made visible.

**Axes (each 0 to 100 against a pre-registered bar, weights fixed in the design, not tuned by the loop):**

| Axis | Source | Bar |
|---|---|---|
| Memory and companion | the bar (23 cells), the day-sim (16 asks), the person bench (12 cells) | cells passed over cells scored; SKIP counts as 0, not as missing |
| Voice | the voice regression's per-clip correctness (once T-3 lands), STT empty-transcript rate, wakes that became turns, wake false-accept and false-reject once measured | pre-registered per signal |
| Speed | end of speech to first sound p50 and p90 on real panel turns, brain first token p90, Telegram p50 | the latency budget in `docs/knowledge/panel-ttfa-breakdown` |
| Identity | voice-verified share of member turns, enrolment coverage of adults, guest false-accepts in the shadow set | per the identity-presence engine's bars |
| Availability | zoe-data starts per day, minutes offline in waking hours, Kokoro load failures, 5xx per 10k requests, panel 502s | zero in waking hours is 100 |
| Safety floors | forget-reach inventory coverage, authority-class coverage of live memories, restraint control results, the security lane's open P0 and P1 count | a red floor caps the overall score (below) |
| Resilience | off-box backup age and the last restore drill, encryption at rest, unit drift, release currency (days behind the signed release) | backup older than 2 days is 0 on that line |
| Instrument health | the control arms went red in the last 7 days, held-out set intact, baselines clean and commit-bound, nightly workflow fail-closed | **unmeasured is scored 0, never 100** |

**Rules that keep the number honest** (the lessons of round 2 written into the score):
1. **Unmeasured counts as zero.** A green that ran nothing is the failure mode the nightly workflow showed for 14 nights; a skipped cell, a missing baseline or a stale artefact scores 0 on its line and shows as grey, not green.
2. **Floors cap the total.** If any safety-floor line or instrument-health line is red, the overall score is capped at 60 whatever the other axes say, so the loop cannot climb by trading safety for charm.
3. **Only the loop's own held-out set and control arms feed the memory and voice axes;** the targeted training cells are shown but weighted at zero, which removes the incentive to overfit the night's target.
4. **Weights are fixed in this document** and change only by PR; the loop cannot touch them.
5. **The score is computed by plain code** (`scripts/perf/zoe_score.py`, nightly at 05:30, from `~/.cache/zoe/*_trend.jsonl`, the voice regression artefact, `live-state.json`, the ledger and the ground-truth script), never by a model, and the computation is itself unit-tested with planted inputs that must produce the expected number.

**Two faces of the same number.**
- **Household:** one number and three plain words on the panel's settings screen and in the morning brief if asked ("Zoe is at 83. Better at calendar this week, slower in the evenings."). No axis names, no jargon, nothing spoken unprompted.
- **Owner and maintainer:** the stats page (desktop UI, the same place the operator pack lives) with every axis, its trend, the night's attempt and whether it was kept or reverted, the canary countdowns, and the floor status. The history is the trend files plus a `zoe_score` Prometheus gauge per axis so the chart is cheap.

**What exists today:** every source above except wake FA/FR, per-clip voice correctness, a backup age and the instrument-health lines; the trend files already carry commit and dirty bits; the ground-truth script already reads the live flags. The page is therefore mostly an aggregation and a layout, with the honesty rules as the hard part. **Phase 2 of section 9 builds it alongside the proposal queue, because the loop needs it to choose its target.**

**One caution.** A single number invites gaming by whoever builds the axes, not only by the loop. The held-out set, the zero-for-unmeasured rule and the fixed weights are the defence; if an axis is ever found to be easy to satisfy without the property holding (the vacuous-pass class round 2 found in the contract tests), that axis is set to 0 until fixed, and the fix is a PR.
