---
type: Reference
title: The risk-cue tier (ZOE_RISK_CUES) - code finds the flaw in a plan, the 4B only voices it
description: A flag-dark tier that detects a plan-shaped adult turn with a risk cue (per-language lexicon data) and hands the Flue brain ONE short "Before you answer, check" block naming the specific element; how it is built, who gets it, what it respects, and the exact A/B command that must print KEEP before the flag may flip.
tags: [samantha, person-likeness, risk-cue, flue, zoe-data, flag-dark, ab-test, lexicon]
timestamp: 2026-10-12T00:00:00Z
---

# The risk-cue tier (`ZOE_RISK_CUES=off|shadow|on`, default off)

**Status 2026-10-12: built, flag-dark, NOT yet shown to help.** The live A/B runs after the owner's day-sim finishes; do not flip the flag
until `scripts/perf/risk_cue_ab.py --phase report` prints `VERDICT ... KEEP` on the held-out half with the regression phase run.

## Why

The manner-block A/B ([manner-block.md](manner-block.md)) measured the person bench's P5b.b ("the planted flaw is touched or asked about")
at **21/60 with the manner block off and 21-22/60 with it on**: prompt wording does not make the small 4B notice the problem in a risky plan
(it answers with sympathy - "That sounds like a really frustrating moment to send" - not the problem). The 2026-10-10 finding and
`docs/research/person-likeness-2026-10-09.md` say the fix is structural, in the same family as `docs/research/structural-floors-2026-10-09.md`
(language-independent structure in code, the words as per-language data): **code detects the risk and hands the brain a short, specific note,
so the 4B only has to voice the flaw, not find it.**

## What it does

On an **adult's** turn that announces a plan ("I'm going to ...", "tomorrow I'll ...", "sending it now") and carries a risk cue, the Flue seam
(`zoe_flue_client._risk_cue_block`) adds one block **after the user's words** (the same position and pattern as the personalisation hop's
`_hop_context_block`):

```
[MEMORY CONTEXT - a possible problem with the plan the user just described; use it silently; do not mention this block]
## Before you answer, check
- Safety: the plan says "driving" and they also said "awake since five" - that combination is risky.
Mention the concern once, briefly and kindly, then help with the plan. Do not lecture, refuse or repeat it.
[END MEMORY CONTEXT]
```

It reuses the `[MEMORY CONTEXT` delimiters on purpose: the sidecar's stale-block elision (`labs/flue-zoe-brain-2x/src/context-blocks.ts`) already
drops that block type from older turns, so **no sidecar change** is needed. It never blocks, refuses or rewrites the request.

| Piece | Where |
|---|---|
| flag, plan gate, detector, selection, rendering, eligibility, once-per-conversation memory | `services/zoe-data/risk_cues.py` |
| the words (plan gate, cues, notes, heading, rule) per language | `services/zoe-data/lexicons_data/{en,es}.json` key `"risk_cues"` |
| the seam (one construction site, exclusive with every other block) | `zoe_flue_client._risk_cue_block` + the `risk_block` lines in `_run_flue_brain_streaming_turn` |
| tests | `services/zoe-data/tests/test_risk_cues.py` (ci_safe), `tests/unit/test_risk_cue_ab.py` |
| the A/B | `scripts/perf/risk_cue_ab.py` |

**No English in Python.** Every word list, the plan gate, the notes, the heading and the rule are lexicon data (`lexicons.py` explains the
contract: a lexicon is a pre-filter that can only ADD one short note; a miss costs nothing). English and Spanish exist (Spanish is author-written,
`reviewed: false`); **a language with no entry gets no block** - English patterns are never run on French text. When the language detector has
no evidence (a short Spanish sentence full of cognates reads as English by default) every language with cue data is tried.

**Voice-path file touched** (`scripts/maintenance/voice_gate_check.py` globs): `services/zoe-data/zoe_flue_client.py`. The replay gate therefore
runs; flag off is the wire it always was (golden tests below).

## The plan gate

A turn is an announced plan when it has **no veto** and **a first-person intent marker**, or - with none - **a forward time anchor in a message that
is not a past narrative**. Vetoes: a question or aux-led opening ("should I ..."), a hypothetical ("what if", "if I"), a negated plan ("not going
to"), a dropped plan ("was going to"), a past story. A message over 500 characters is not scanned. This is a pre-filter: the *risk* still has to
match a cue, so a loose gate costs a missed note, never a wrong one.

## The categories (everyday life, written as data)

| Category | Cues (examples; the full set is in the lexicon) |
|---|---|
| `safety_health` | a hazard with an impairment (driving, swimming, a ladder + tired, awake since, a few drinks); an injury with a load (a sore ankle + football); skipped protection (no helmet, skipping the warm-up); stopping medication; a meal that is not a meal (energy drinks and crisps, plain rice, no food for days) |
| `money_contract` | everything on one bet or investment; a "sure thing" in a bet; quitting with no savings; signing without reading; paying a stranger up front; handing over a code or password |
| `irreversible` | deleting everything or wiping a device with no backup mentioned; a hostile message that cannot be unsent; a rash step taken while angry (quit, walk out, email everyone) |
| `prerequisite` | a stated missing passport / ticket / booking / permission; "haven't booked"; a big step (quit, move, sell, propose) nobody has been told about |
| `timing` | two things set for the same time (clock arithmetic in code, on different days it does not fire); too little lead before a flight / train / ferry / exam / interview, or leaving at or after it |

Written from the categories of everyday life, **not from any bench sentence**. At most **2** concerns per block, from different categories, in the
order safety > money > irreversible > prerequisite > timing; a concern is raised **once per conversation** (a plan repeated on the next turn does
not re-trigger the note: the model is told "once", the code makes it true).

## Who gets it, and what it respects

* **Adults only**, by the manner block's allowlist (`manner_block.eligible`: no `minor` / `kid` `member_modes` row, an `auth_users` role in
  `ADULT_ROLES`, fail closed on a missing account, an unknown role or a failed / slow lookup), **plus never a guest and never a harness-minted
  synthetic id** (`user_filters.is_synthetic_user`; the repo-wide `ZOE_SYNTHETIC_USER_ALLOWLIST` is honoured). The id string is only ever used to
  **refuse**, never to allow. Detection runs first and is pure; the account lookup happens only on a turn that fired a cue.
* **Restraint and clean-goodbye are respected:** no block on a goodbye / presence check / silence (`clean_goodbye.classify`), a spoken mute or
  release (`restraint.parse_utterance`), a voice the speaker gate did not confirm (`restraint.current_verdict() is False`), or - in
  `ZOE_RESTRAINT=enforce` - a topic the member has muted.
* **One block per turn:** the cue yields to the recall, continuity, verify, hop, offer, raise and day-brief blocks.
* **Shadow** (`ZOE_RISK_CUES=shadow`) runs everything, logs `RISK_CUES mode=shadow user=... lang=en n=1 cats=safety_health` (counts and categories
  only, **never message text**) and injects nothing. `off` does no work at all.
* Only the Flue seam carries it (the live brain lane). The legacy core lane and the fast tiers do not.
* Known limits: third-person plans ("my brother is going to ...") can pass the time-anchor gate; a long message, a language with no data and a
  plan phrased in a way no cue covers all get nothing (a miss, never a wrong note). Provenance ("why did you say that?") is not labelled: the cue
  is derived from the user's own words this turn, not from stored context.

## Tests (all `ci_safe` / unit, negative controls)

`services/zoe-data/tests/test_risk_cues.py`: the data is sound (every pattern compiles, every note fits its slots, every category in en and es,
worst-case block inside the budget); **every one of 43 cue sentences fires in its language, and the same sentence with that cue deleted from the
data does not fire it** (red when removed); 42 non-fires (question, past story, hypothetical, negated plan, no time anchor, goodbye, plans with no
risk, a backup mentioned, times on different days, ample flight lead, shorthand clock times such as "leave at 1 for my 11pm flight"); the timing
arithmetic (a 75-minute lead fires, 150 does not, a train needs 15 not 120; a time without am/pm is read BOTH ways and fires only when every
reasonable reading is too tight or late; one am/pm and one bare time is never a double booking); who gets it (a real sqlite: minor, child roles, unknown role, missing account, guests, synthetic ids, failed and slow lookups);
restraint (goodbye, mute, unconfirmed voice, enforce-muted topic); one block per turn; once per conversation; shadow logs no text and injects
nothing; the flag-off wire is byte-identical for every shape; sha256 goldens of the rendered en and es blocks.
`tests/unit/test_risk_cue_ab.py`: the harness leaves `manner_block_ab` untouched, the wire audit, the verdict end to end on synthetic rows.

## The A/B (protocol fixed before any live number)

`scripts/perf/risk_cue_ab.py` reuses `manner_block_ab.py` as a library: the REAL zoe-data composition (`_run_flue_brain_streaming_turn`, the cue
seam, the recall floor) in-process, a scratch sidecar from this tree's `dist` on :3589 (scratch store, throw-away token, stub data URL: no live DB
read), the same `demo_bar_<hex>` + `replay_isolation=True` safety, the person bench's own scorers and Wilson bars, and **every integrity rule of
the manner A/B**: a saved plan, every planned ask scored in both arms, rows fingerprinted by `sha256(harness version, arm, cue-data digest, split
digest)`, tombstones on a zoe-data restart, both arms from one plan, a cumulative brain-time ledger (its own, `~/.cache/zoe/risk_cue_ab/brain_time.json`,
2400 s) and the frozen **dev / held-out split** (`SPLIT_DIGEST`: dev = the bench's three flaws and five good plans; held-out = the six flaw and five
good-plan messages written before the first run of either A/B).

* Arms: `none` (`ZOE_RISK_CUES` unset: today's bytes) vs `cue` (`ZOE_RISK_CUES=on`: the real eligibility, detector, seam and wire).
* Cells: **P5b.b** (flaw touched; 8 samples per held-out flaw ask), **P5b.u** (good plan praised, no concern invented), P5b.a; regression P2, P5a,
  P5c, P6.b, P7, P8 and the bar sample S1, S3, S4, S28-S31. The judged halves (P5b.j ...) are not run.
* A **per-ask wire audit** records whether the detector fired and whether the block rode (`<run>.fires.jsonl`): a block on the `none` arm, or on a
  turn the detector does not fire on, aborts the run as an instrument failure; a detected turn the block did not ride (another block took the turn)
  is recorded, never hidden.
* **KEEP** needs all of: P5b.b separates UPWARD on held-out (Wilson lower bound of `cue` above the upper bound of `none`); P5b.u does not separate
  DOWN and does not drop 2+ asks; P5b.a does not separate DOWN; no regression half separates DOWN (violation counts: up); every comparison has data
  in both arms; and the **delivery gates**: the block reached >= 75 % of the held-out flaw asks, <= 20 % of the held-out good plans, and no ask in
  the `none` arm. The wire audit must also be COMPLETE: exactly one record per planned held-out ask, flaw and good, in BOTH arms - a missing
  or short audit (an empty `none` arm would make "no block rode in none" vacuously true) is INCOMPLETE and never KEEP.
* **Held-out discipline.** The cue data was written from the categories before any held-out number existed, and `--selftest` never runs the
  detector over the held-out half (it checks the DEV half only: the three bench flaws fire, the five bench good plans do not). How much of the
  held-out set the detector catches is read for the first time in the live held-out phase (`delivery (heldout): ... detector fired X`). A cue edited
  after seeing that number is a **new run id** (the cue-data digest is in the fingerprint) and the held-out half is then spent for that purpose.

### Commands (the live run waits for the owner's day-sim; do NOT run it before)

```
cd <this worktree>; Z=/home/zoe/.zoe/venvs/zoe-data-py312/bin/python
(cd labs/flue-zoe-brain-2x && npm run build)                               # the scratch sidecar runs dist/
$Z scripts/perf/risk_cue_ab.py --selftest                                  # offline: protocol, decide() controls, dev fixtures
L="flock -w 3600 /tmp/zoe-voice-harness.lock flock -s -w 3600 /tmp/zoe-brain-window.lock"
ZOE_PERF=1 $L $Z scripts/perf/risk_cue_ab.py --phase canary  --arms none,cue --run-id rca-1    # positive control: the block rides the cue arm's wire
ZOE_PERF=1 $L $Z scripts/perf/risk_cue_ab.py --phase dev     --arms none,cue --run-id rca-1    # DEV: tune the cue DATA here only
ZOE_PERF=1 $L $Z scripts/perf/risk_cue_ab.py --phase heldout --arms none,cue --run-id rca-1    # the verdict half, ONCE
ZOE_PERF=1 $L $Z scripts/perf/risk_cue_ab.py --phase regress --arms none,cue --run-id rca-1    # P2/P5a/P5c/P6.b/P7/P8 + bar sample
$Z scripts/perf/risk_cue_ab.py --phase report --run-id rca-1               # tables + delivery + VERDICT (no brain)
```

Brain time is about 2.5 min for canary + dev, 8-10 min for held-out and 20-25 min for the regression phase at the default scale (`--scale 0.5`
halves it; pass the same `--scale` and `--flaw-samples` to `report`). The harness refuses without `ZOE_PERF=1`, without the harness lock, while the
brain-window lock is held exclusively, in the night window, during a landing, with low RAM, with the brain down, or with zoe-data up < 180 s - and
never run it during the owner's day-sim or a brain window. Flip `ZOE_RISK_CUES=on` only on a printed KEEP; then `shadow` first on the live box if
you want the fire rate on real turns before it speaks.
