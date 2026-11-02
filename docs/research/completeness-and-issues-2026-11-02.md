---
type: research
title: Completeness and issues (2026-11-02) - what is unfinished, what is half-built, what is breaking, and what stands between Zoe and a natural conversation
date: 2026-11-02
status: research-only, round 4. Lanes T (TV), U (unfinished inventory) and U2 (half-built surfaces, static sweep with path:line evidence) are in the bundle. Lanes V (code issues) and W (live issues) carry exposure detail and are held privately with the 2026-10-19 security review; their sanitised rankings are here. Findings handed to the "Zoe comprehensive review and modernization" session.
description: Owner ask - "another full analysis looking for issues on zoe or things not completed", then "get Zoe to Samantha level ... I want to be able to speak naturally to Zoe, and not feel like I'm talking to a half baked robot". Four lanes over origin/main 27d1b9d43 and the live box 10-26 to 11-02.
evidence: completeness-and-issues-2026-11-02/ (lanes T, U, U2); ~/.zoe/security-review/2026-11-02/ (lanes V, W, 0600); the night-window journal; the request log (35,927 requests in the window); the open-problems ledger.
---

# Completeness and issues (2026-11-02)

> Round 3's synthesis said a fourth evidence round would be the wrong move. This one was asked for anyway, and it was worth it for one reason: it is the first round to measure the household's experience turn by turn instead of the architecture. The headline is section 1.

## 1. Speaking naturally - the gap, measured

The owner's bar: talk to Zoe like a person, not a half-baked robot. What the week's traffic says is in the way, in the order the household feels it:

1. **She does not use what she knows.** Recall is built and firing almost never on real turns: both injectors are trigger-gated on phrasing real speech rarely matches (#2225 is the read-only diagnosis). The brain answers from the turn alone, so every conversation starts cold. **Fix:** inject recall on every household turn with a token budget, not on a trigger; measure "sources used" per turn as the Zoe-score axis.
2. **Silence and dead air.** Kokoro restarted 119 times in the window (44 CUDA load failures); landing probes stopped it while the household was speaking (#2249 fixes the probe); speech was dark for 214 minutes on 10-29. The daemon's "one moment" (#2232) and the quiet-household deploy wait (#2234) are merged; neither is yet verified at the panel over a full day.
3. **The first word is slow.** p90 answer about six seconds; the household prefix is evicted by helper batches and restarts (#2245 re-warms it). The owner switch to llama.cpp b11982 (#2231) is the one measured speed lever open.
4. **Tier answers that sound like a machine.** Fast-tier and fallback replies ("I didn't catch that", bare confirmations, a timer that says it set when it did not: `set_timer` fails closed in `zoe-tools.ts`) read as robotic. Empty transcripts (about one in ten) get a canned line instead of a human "sorry?". **Fix:** one voice for every tier, drafted once, with the brain's manner; empty transcript = a short natural re-ask, never a template.
5. **Confirmations keyed by panel, not speaker.** A pending "did you mean X?" belongs to the panel, so a second person's "yes" answers the first person's question. Correctness, not polish.
6. **Barge-in does not resume.** Duck-decide-resume is built (`BARGE_DUCK_ENABLED`) and has never run at the panel.
7. **Identity still strict-fails quietly.** Follow-up turns carried no verdict until #2242; children and guests still collapse under some phrasings (guest predicate issue 14 in the handoff).

None of these needs a model change. Items 1, 4 and 5 are code the modernization session can land this week; 2, 3 and 6 are verification of merged work plus one owner switch.

## 2. Self-evolution, self-healing, self-improvement - where it actually is

Asked directly on 11-02: "did we ever finish it?" No. Three different things carry that name; here is each one's honest state.

| Loop | What exists | What runs tonight | What is missing |
|---|---|---|---|
| **Night window (12B, 02:50)** | digest, dreaming, night mind, Omi day review, index compaction | Runs nightly. Outcomes 10-30 aborted (memory floor), 10-31 ok, 11-01 ok, **11-02 aborted: the 12B did not load (CUDA alloc)**, only dreaming ran on the 4B | Reliability: 2 of the last 4 nights failed on memory. Night mind writes in enforce but its readers are still off until the owner sets `serve`, so nothing it learns reaches a conversation |
| **Struggle detector (self-correction)** | P1 detect, P2 proposals, P3 router feed, outcomes | Detect and proposals in **shadow**; feed and outcomes **off**; `zoe-router-retrain.timer` not installed; one shadow run found zero labelled failures | A week of shadow data to read, then enforce; the retrain timer; the feed flag is not even in the registry |
| **Router self-train** | full loop, corpus in git, runs | **Dormant since 2026-07-26** (one run, verdict REJECT, never re-run) | Superseded in practice by the struggle feed; retire or re-run, not both |
| **Self-healing (ops)** | crash-loop watch (every 2 min), health timer, Kokoro CUDA retry, brain prefix keeper, daemon retry, deploy waits for a quiet household | All live | They heal restarts, not causes: Kokoro still reloaded 119 times, and alerts went to a Telegram token that has returned 401 eight times since 10-27, so nobody was told |
| **Nightly self-evolution loop (Harness², Zoe score, updates page)** | Design `docs/architecture/self-evolution-nightly-loop.md` (this PR lands it on main); Phase 0 pieces merged: overlay seam #2153, proposal queue #2165, honest-instrument fixes #2137 #2145 #2222 | **Nothing.** No nightly job, no Zoe score computed, no page | Phases 1 to 4 are design-only; four owner decisions open (credit cap, tool guidance in scope, canary bands, lessons to the411). Week 3/4 of the synthesis order |

So: Zoe self-heals restarts, dreams nightly when the box lets her, and watches herself fumble in shadow. She does not yet improve herself from any of it. The first thing that would change that is the cheapest: read the shadow data, flip detect and proposals to enforce, and compute Zoe score v0 nightly with "unmeasured is zero".

## 3. The unfinished inventory (lane U, full table in the bundle)

- PLANS claims corrected in this PR: Ask-card next step (merged #1364), change notices and music discovery ON live, relationship-graph recall live, household lists merged, streaming STT built, field-scan P1/P2/P5/P9 built, ROADMAP A3/A4 done (#2127).
- **Finish (25 smallest-cost items, bundle section 9a)**, top of the list: land the nightly-loop design (done here); night-mind readers to `serve` after a replay gate; register the struggle feed flag; install the retrain timer at shadow; the calendars writer; `set_timer` stale fail-closed; the off-box backup timer; the `deep_web_research` schema.
- **Kill (bundle section 9b):** flags dark for 60+ days with no owner pull (`ZOE_OTEL_ENABLED` 182 d, `ZOE_MEMORY_LINT_IN_DREAMING` 137 d, `ZOE_LIVEKIT_FAST_TIERS` 134 d, `ZOE_CHAT_INJECT_DB_MEMORY` 133 d), the tier-1 semantic router shadow, the router self-train loop, the stray `zoe-dreaming.timer`, the 8-day scratch harness container.
- **Phased plans:** none complete; ask-card phase 2, voice-tts split (0 of 6), chat split, tech-debt wave 4 all unstarted; iPhone app M0 box side dark, no Swift; alarms P1 dark, P2 in review, P3 draft.
- Two dead test files removed in this PR (`test_auth_security.py`, `test_experts.py`: unconditionally skipped, tested nothing).

## 4. Half-built surfaces (lane U2, bundle)

Static sweep of origin/main: 159 of 492 routes have no caller anywhere (system.py 52 of 71); 51 UI URL literals hit no route (memories collections and tiles against GET-only stubs, the project widget against a "coming soon" stub, 14 dead URLs in touch settings, the chat OpenClaw skills panel); four tables with zero writers, three written and never read; five builder intents that map to retired OpenClaw text and are swallowed as success in `chat.py`; a `/platform` endpoint that reports a retired engine and a `/modules/enabled` that is a hard-coded all-true list. Cut first: OpenClaw and Hermes dead routes in `system.py`, `chat.py`, `mcp_server.py` and the UI pickers; the projects and collections stubs and their widgets; dead tables `trust_allowlist`, `trust_audit`, `engineering_tasks`; the two fake-success endpoints in `routers/stubs.py`; orphan routes with no UI. Finish: the calendars writer, the alarms chain, the Settings face-enrolment card, a voice intent for Bluetooth.

## 5. Code issues (lane V, 40 ranked, held privately)

Public shape: fail-open instruments (the class round 3 named, still present in two gate scripts), confirmations keyed by panel, the stale fail-closed timer tool, a brain-tool list that drifts from the handler map, unregistered scheduled jobs, flags read outside the registry, and exception swallowing on the voice path that turns errors into "I didn't catch that". Nothing new in kind; the fixes are the handoff list numbered 1 to 14.

## 6. Live issues (lane W, 20 ranked, held privately)

Public shape, 10-26 to 11-02: 64 zoe-data starts (none crashes, all deploys); Kokoro 119 restarts; 2 of 4 night windows aborted on memory; 8 Telegram 401s (alerts undelivered, token rotation is an owner step); swap about 17 GB used with MemAvailable near 500 MB in waking hours; backups ran, off-box backup still not installed; panel daemon current since 10-28. The four live network items from the 10-19 review are unchanged and remain in the private file.

## 7. The TV (lane T)

Design in `docs/architecture/tv-display-panel.md`: Google TV is the only candidate, display-only role, launcher over ADB with the Free My TV safety pattern, P0 is a purchase and an afternoon.

## 8. Handoff and owner items

Handed to the modernization session: the natural-conversation list (section 1), the cut-first list (section 4), the numbered fix list. Owner-only: Telegram token rotation; the dev budget on the box; repo private; the four self-evolution decisions; `ZOE_NIGHT_MIND=serve`; the b11982 switch; de-root steps 0 and 1; Spark timing.
