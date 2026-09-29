---
type: Reference
title: User-model block and stale-block elision A/B (user_model_ab.py)
description: How to flip ZOE_USER_MODEL_BLOCK and ZOE_BRAIN_ELIDE_STALE_BLOCKS live with evidence. Why the Samantha bar cannot measure either flag, the twin-user A/B and the 10-turn hygiene probe that can, the pre-registered decision rules, and the exact operator steps, measurement commands and rollback.
tags: [memory, samantha, eval, flue, context-engineering, user-model, harness]
timestamp: 2026-09-30T06:30:00Z
---

# User-model block and stale-block elision A/B

Probe: `scripts/perf/user_model_ab.py`. Tests: `tests/unit/test_user_model_ab.py` (ci_safe, pure).
Features: #1783 `ZOE_USER_MODEL_BLOCK` and #1785 `ZOE_BRAIN_ELIDE_STALE_BLOCKS`. Both merged
flag-dark on 2026-09-29. Research: [context audit §4 PR 2/PR 3](../research/zoe-context-audit-2026-09-29.md).

## How each flag is gated

**`ZOE_USER_MODEL_BLOCK`** (zoe-data).
- `user_portrait.load_user_model_block` reads the flag from `os.environ` on every call. zoe-data
  gets its env from systemd `EnvironmentFile=` (`services/zoe-data/.env`), so a flip needs a
  zoe-data restart.
- It returns `{"version": "", "text": ""}` for guests, `family-admin`, `voice-daemon`, and every
  id `user_filters.is_synthetic_user` flags. That covers every `demo_*`/`test_*`/`probe_*`/`ci_*`/
  `e2e_*`/`bench_*` id unless it is listed in `ZOE_SYNTHETIC_USER_ALLOWLIST`. The allowlist is
  exact ids, comma-separated, read per call, and it never re-admits a guest sentinel.
- The text is the `users.name` line plus the stored portrait, PII-scrubbed and cut to 1,400
  chars. `version` is a content hash. A `USER_MODEL_BLOCK user=… chars=… version=…` line is
  logged only when the flag is on and the id is real.
- The sidecar has no flag of its own. `src/user-model.ts` fetches `GET /api/memories/user-model`
  per user in the background and caches the result for `ZOE_USER_MODEL_TTL_MS` (default 300000).
  A turn never waits on the fetch: a user's first turn after a sidecar start, or after the TTL
  runs out, uses whatever is cached (or nothing) and starts a refresh. After a zoe-data flip,
  the sidecar only notices when a cached entry expires. **Restart `flue-zoe-brain-2x` after
  every zoe-data flip**, or wait out the TTL plus one turn per user.
- When `ZOE_BRAIN_USER_ID` is set in the sidecar env (it is live), a turn with no identity
  envelope (`parity/recall_reliability.py`) acts as that user, so it gets that user's block.

**`ZOE_BRAIN_ELIDE_STALE_BLOCKS`** (sidecar).
- `src/context-blocks.ts` reads it per call from `process.env`, and the env comes from
  `EnvironmentFile=labs/flue-zoe-brain-2x/.env`, so a flip needs a restart of
  `flue-zoe-brain-2x`.
- The accounting runs in both flag states. `stale` is the tokens of injected blocks in older
  stored user messages, and it is the same number in both states. `elided=1` means they were
  removed, in which case `history` excludes them.

## Portrait generation

`user_portrait.run_portrait_synthesis(user_id)` needs at least 5 approved memories (the count
comes from `load_for_prompt`, limit 200). It sends one llama-server call (600 tokens max,
temperature 0.7) and upserts `user_portraits`. It has no synthetic filter of its own. Only the
weekly caller, `run_portrait_synthesis_for_all` (Sunday phase 4 of `run_dreaming_cycle`), drops
synthetic ids. It can be run for ONE user on demand in two ways:
- `POST /api/portrait/{id}/regenerate` with an admin `X-Session-ID` (`_require_self_or_admin`).
  An internal token is not enough, because the route uses `get_current_user`, not
  `resolve_acting_user`.
- Sending the chat line "Please rebuild your portrait of me." as that user. This hits the
  `portrait_refresh` intent, which replies deterministically: "I've updated my understanding of
  you — synthesised from N memories…". The router-head intent gate
  (`fast_tiers.keyword_intent_allowed`) has to agree for the intent to fire, so the probe
  treats a missing canned reply as "not confirmed".

Never call it from a second process. `MemoryService` opens the live Chroma store, which has a
history of HNSW corruption, and the bar's rule is that nothing but the running zoe-data opens it.

## What the Samantha bar can and cannot see

| id | measures | can the user-model block change it? | can elision change it? |
|---|---|---|---|
| S1 | same-day recall in a new session | no — bar users are synthetic, so their block is `""` | no |
| S2 | supersession | not for bar users. With a block, a **week-stale portrait** could reassert the old fact → the `race` guard below | no |
| S3 | decline when nothing was said | not for bar users. With a block, prose about the user could embolden a guess → the `dentist`/`conductor` guards | no |
| S4 | the day-1 worry acknowledged on day 2 | not for bar users. A block could help or crowd the continuity focus → the `worry` guard | no |
| S5 | proactive hook | no (hook-gated SKIP) | no |
| S6 | user isolation | not for bar users. The block is a NEW cross-user surface (per-user sidecar cache, single slot) → the `leak` check | no |
| S7 | richer fact kept | no (store-level) | no |
| S8 | recall after 32 filler turns | no (asks are in fresh sessions) | no |

- **User-model block.** With the flag on, bar users still get `""`, so their wire is
  byte-identical. The bar is only a side-effect check.
- **Elision.** No bar session has a block-carrying turn followed by another turn. All 153
  `FLUE_CONTEXT_BUDGET` lines logged since #1785 deployed (every one from a bar run) show
  `stale=0`, so elision changes no byte either.
- **Reading a compare.** A red S4 or S8 on a flag-on compare is the known flake until it
  repeats (see the samantha-bar re-run rule).

## Protocol

### A. User-model block — twin A/B with the flag on

- **P** = `demo_bar_ab0e0001`, listed in `ZOE_SYNTHETIC_USER_ALLOWLIST`, so it is served the block.
- **TWIN** = `demo_bar_ab0e0002`, not listed, so it is not.
- Both are named `Ottilie` (`users.name`, their own rows). Both get the same 11 synthetic facts:
  - vegetarian night-shift ICU nurse, cello in an orchestra, old greyhound Biscuit;
  - half-marathon in March, learning Portuguese, peanut allergy;
  - prefers short answers, hates coriander, doesn't drink;
  - nervous about a cello audition.
- Both get a portrait. After the portrait they both say "dropped the half-marathon, doing a 10k
  in May". The portrait therefore goes stale in a known way, which is exactly how a real
  weekly portrait goes stale.
- The only difference between the two users on the Flue lane is the block. The core lane and
  the continuity portrait line see a portrait for both.

Why a twin rather than flag off vs flag on:
- The two arms are interleaved in time, so brain state, slot cache and time of day cancel out.
- Every probe turn is captured by the digest, and both stores grow the same way.
- It needs only one restart pair, not two.

Why these ids:
- They must match `FORGET_SYNTHETIC_RE`, or `forget-synthetic` could never erase them. The
  suggested `demo_ab_portrait` does not match, so only the admin forget could clean it up.
- They are bar-family, so every `samantha_bar` guardrail applies unchanged.

`measure` does the following:
1. It refuses unless P's `/user-model` text starts with the name line and carries a portrait,
   and TWIN's text is `""`.
2. It sends two warm-up turns per user.
3. It asks 7 profile questions and 5 guard questions × `--samples 3`, alternating P and TWIN,
   each in a fresh session.
4. It runs the leak check: one fresh non-allowlisted user **L** asks 3 questions, each straight
   after P asked it.
5. It sends 8 recall-first prompts straight to the sidecar for both users, with the replay
   marker set so no writes happen.

- **Profile questions** do not trip the recall floor. This is pinned: `message_needs_memory` is
  False for each. On the Flue lane the block is therefore the only carrier, unless the model
  calls `recall_memory` itself.
- **Scoring is deterministic:**
  - word matching on whole words, negation-aware ("instead of chicken", "dropped the
    half-marathon");
  - violations of the profile, such as meat, peanut sauce or coriander;
  - stale-fact assertions;
  - a brevity rule for the style question.
- **Block delivery** is proven from the sidecar's own `FLUE_CONTEXT_BUDGET system=` estimate:
  the median for P minus the median for TWIN must be at least 80% of the expected
  `(3 + 367 + len(block)) / 4` tokens. If it is not, the verdict is INCONCLUSIVE, not a pass.
  A sidecar still holding a cached `""` for P fails here.

Pre-registered rule (`decide_user_model`):
- **DO_NOT_FLIP** when any of these holds:
  - an L reply, L's packet or L's block contains a P-only word (`cello`, `greyhound`, `icu`,
    `porto`, `ottilie`, …);
  - L's packet or block could not be read (ERROR);
  - P recites its block (a run of 7+ words, or 3+ profile words in a greeting);
  - a guard (`dentist`, `conductor`, `race`, `worry`) passes for TWIN but not for P;
  - P fires `recall_memory` more than 1 time fewer than TWIN, out of 8.
- **FLIP** when there is none of those, and P passes at least 3 more profile samples than
  TWIN (out of 21).
- **NO_MEASURABLE_BENEFIT** otherwise. That is the operator's call. The block costs ~330 tokens
  of system prompt, plus one re-prefill per user switch that misses llama-server's cache.

Also reported: P's use of the name, median latency P vs TWIN, and which lane served each
session (`BRAIN_LANE`). Chat memory-domain turns can go to the core lane, and there the block
does not exist.

### B. Elision — the 10-turn hygiene probe

`hygiene` runs one session for a fresh synthetic user. The turns are:
1. seed (the sister flying in);
2. filler;
3. recall (event question);
4. filler;
5. continuity (a worry);
6. filler;
7. recall (my-question);
8. continuity (on edge);
9. filler;
10. recall.

A pending contact offer can add an offer block on non-continuity turns. The probe reads that
session's `FLUE_CONTEXT_BUDGET` and `FLUE_PROMPT_CACHE` lines back from the app log.
- **A run is evidence only when:**
  - all 10 turns are logged;
  - `elided` is the same on every line;
  - `stale` never decreases and ends above 0.
- **Expected with the flag off:**
  - `elided=0`;
  - `stale` is 0 on turns 1–3, then climbs at every turn after a block turn;
  - at turn 10 it should be roughly 400–900 tokens (live means: recall block ≈66 tok,
    continuity block ≈260 tok, plus the offers);
  - `history` includes all of it.
- **Expected with the flag on:**
  - `elided=1`;
  - `stale` is about the same as with the flag off;
  - `history` is about `history_off − stale_off`;
  - `first_prompt_n` is higher only on the turn right after a block turn, by about 70–300
    tokens. That extra is the previous user message (without its block) plus the previous reply
    being re-prefilled once, about 0.1–0.5 s.
- **Pre-registered rule** (`hygiene-compare`): FLIP when all of these hold:
  - both runs are valid;
  - the arms are labelled correctly (off `elided=0`, on `elided=1`);
  - `history_off − history_on ≥ 0.5 × stale_off`;
  - the mean extra `first_prompt_n` on post-block turns is ≤ 400.
- **Informational:** does the stale worry come back on filler turns, and is the contact offer
  re-asked.

## Operator steps

All runs go through `ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock nice -n 5 python3
scripts/perf/user_model_ab.py <phase>`. Run them from a worktree of `main`. They refuse inside
01:45–03:15 (−30 min), during a deploy, and below 1.2 GB MemAvailable. Keep the whole window
within one daytime session, well away from the 07:30 brief. While P is allowlisted, it is a
"real" user to the nightly dreaming passes and the proactive recipients. It has no panel binding
or push target, but tear it down the same day.

1. **Hygiene, flag off.** This needs no live change.
   - Run `… hygiene --label off`.
2. **Elision on.**
   - Add `ZOE_BRAIN_ELIDE_STALE_BLOCKS=1` to `labs/flue-zoe-brain-2x/.env`.
   - Run `systemctl --user restart flue-zoe-brain-2x`, then poll `curl -sf 127.0.0.1:3579/health`.
   - Run `… hygiene --label on`.
   - Run `python3 scripts/perf/user_model_ab.py hygiene-compare --off ~/.cache/zoe/user_model_ab_hygiene_off.json --on ~/.cache/zoe/user_model_ab_hygiene_on.json`.
3. **User model on, with P allowlisted.** Add both lines to `services/zoe-data/.env`:
   ```
   ZOE_USER_MODEL_BLOCK=1
   ZOE_SYNTHETIC_USER_ALLOWLIST=demo_bar_ab0e0001
   ```
   - Run `systemctl --user restart zoe-data` and poll `/health` until it is ready.
   - Then run `systemctl --user restart flue-zoe-brain-2x`, which drops any cached `""` entries.
   - Real users now get their block too. That is the end state being evaluated, and live
     traffic is light: 30 voice turns in the retained logs.
4. **Seed and portrait.**
   - Run `… seed`, then `… portrait`.
   - With an admin session, prefix the portrait run with `ZOE_BAR_ADMIN_SESSION=<X-Session-ID>`.
     This uses the REST route. Without it, the probe uses the chat intent and reports `ok`
     only on the canned reply.
5. **Measure.**
   - Run `… measure --samples 3`. The result is in `~/.cache/zoe/user_model_ab_measure.json`,
     field `decision`.
6. **Regression gates with both flags on.**
   - The Samantha bar: `ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock nice -n 5 python3 scripts/perf/samantha_bar.py --compare-baseline`.
   - The voice replay probe. It runs as `jason` by default, so it exercises his real block:
     `flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py --results ~/.cache/zoe/voice_probe_flags_on.json --trend ~/.cache/zoe/voice_probe_flags_on_trend.jsonl`.
     The alternate paths leave the deploy gate's shared artifact alone.
   - Recall reliability: `python3 labs/flue-zoe-brain-2x/parity/recall_reliability.py`. It runs
     as the sidecar's `ZOE_BRAIN_USER_ID`. Its rate must not fall below the flag-off rate.
7. **De-allowlist and tear down.**
   - Remove the `ZOE_SYNTHETIC_USER_ALLOWLIST` line.
   - Restart zoe-data, then the sidecar.
   - Run `… teardown --fixed`. It tries P's forget first and stops before the Postgres sweep
     if P is still allowlisted.

**Keep or roll back** each flag on its own verdict.
- To roll back the user-model block:
  - delete `ZOE_USER_MODEL_BLOCK=1`;
  - restart zoe-data, then `flue-zoe-brain-2x`. Without the sidecar restart, the block ages out
    within `ZOE_USER_MODEL_TTL_MS`.
- To roll back elision:
  - delete `ZOE_BRAIN_ELIDE_STALE_BLOCKS=1`;
  - restart `flue-zoe-brain-2x`.
- The Flue store is never modified by either flag, so a rollback is exact.

Read-only checks at any time:
- Grep `FLUE_CONTEXT_BUDGET`, `FLUE_PROMPT_CACHE`, `USER_MODEL_BLOCK` and `BRAIN_LANE` in
  `~/.zoe-logs/zoe-data.app.log*`.
- Run `curl -s -H "X-Internal-Token: …" "127.0.0.1:8000/api/memories/user-model?user_id=demo_bar_ab0e0001"`.
