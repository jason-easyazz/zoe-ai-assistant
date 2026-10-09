---
type: Reference
title: Memory quote-backed retirement - a one-word change of state retires the right fact, with the owner's own words attached (Samantha bar S10)
description: How "I gave up the cello" retires "User plays the cello ..." without a rule that guesses and without a model that is trusted. A deterministic cue gate opens the door; the owner's top three current rows are offered; a judge (the per-turn digest off the turn, on BOTH lanes; the Flue brain's memory_retire tool is an optional extra on chat) names ONE number or none; the server enforces every wall whatever the judge said (the choice is one of the three, the quote is the owner's verbatim sentence, the speaker is the verified owner and the words are their own, the row is the owner's own); the retirement invalidates, never deletes, and cites the quote, which the forget sweep erases. ZOE_QUOTE_RETIRE = shadow | enforce | off, default shadow. What was measured, what is built, the guards, the proof (store-tier S10x cells with controls, the live-tier cells and driver), the voice-path files, and what is not measured yet.
tags: [memory, supersede, temporal, provenance, forgetting, s10, samantha-bar, zmb, quote-retire, voice-path]
timestamp: 2026-10-07T00:30:00Z
---

# Memory quote-backed retirement (S10)

Code: `services/zoe-data/memory_retire.py` (the whole feature), `MemoryService.retire_with_quote` (the write), the `memory_retire`
intent (`intent_router.py`, `routers/system.py` `_DISPATCHABLE_INTENTS`), the Flue tool `memory_retire` (`labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts`,
`tool-groups.ts`), the voice judge (`memory_digest.run_turn_digest`), the seam note (`zoe_flue_client.py`), the forget sweep (`intent_router.py`
`memory_forget_entity`). Bench: `scripts/perf/zmb/scenarios/retirement.json` (axis `retirement`), `scripts/perf/zmb/s10x_data.py`,
`scripts/perf/zmb/s10x_live.py`. Tests: `services/zoe-data/tests/test_memory_retire.py`, `test_flue_quote_retire_note.py`, `test_zmb_lab.py`,
`tests/unit/test_zmb_s10x.py`, the sidecar's `npm test`. Design record: `docs/research/mempalace-deep-dive-2026-10-06.md` sections 4.4 and 6.3 (PR #1904).

## The problem, measured

Samantha bar S10: "I play the cello in a community orchestra on Tuesday evenings." ... later "I gave up the cello." Zoe kept serving the cello
row as current. The deep dive's pilot (30 changes, a 100-row pool, 40 non-changes) located the gap: it is JUDGEMENT, not lookup.

| Measure (pilot, all synthetic) | Result |
|---|---|
| Zoe's rule `same_topic(new, old)` finds the old row | 7 of 30 |
| Zoe's cue table fires on the change / on a non-change / on the 10 hard non-changes | 15 of 30 / 6 of 40 / 6 of 10 |
| Embedding retrieval (MiniLM) puts the old row in the top 1 / 3 / 5 | 26 / 29 / 30 of 30 |
| Retrieval's top-1 on a NON-change is the row it merely mentions | 34 of 40 |

So "retire the retrieval top-1" is wrong most of the time, and a rule tweak is not the answer (a relaxed cue+shared-token rule found 10 of 30).
Finding the row is solved by an embedder Zoe already runs; deciding that this sentence ENDS a note, and which, is a language judgement.

## What is built

```
turn  ->  cue gate (deterministic)  ->  the owner's top 3 current rows  ->  judge: a number 0-3  ->  the wall  ->  retire (invalidate, cite the quote)
          no cue: nothing happens       eligible rows only                  chat: the brain's tool     server-side     never delete
          (no search, no model call)                                        voice: the digest, off-turn
```

**The cue gate** (`memory_retire.pick_quote`): `memory_supersede`'s cue table (the cues that name a change) plus the ending and replacing verbs it
lacks (sold, left, died, finished, deleted, got rid of, took over, again, now ...). It is a RECALL filter: a cue only opens the door to the judge. It does
not open for a question, a plan or wish ("I will sell the Corolla", "maybe I'll give it up"), a hedge or negated change ("I almost gave up the cello",
"I am not giving up running", "I haven't quit"), or somebody else saying it ("Quentin says the goldfish died", "I heard they sold the Corolla", "the doctor
said I should stop"). Measured: the 30 plain changes 30/30 (these were seen while the cues were written), a held-out list 14/15, 0/10 held-out mentions;
of the 10 hard non-changes the gate stops 3 ("almost", "not giving up", "nearly") and the other 7 are the judge's (`quit the meeting early`,
`stopped running to tie a shoelace`).

**The quote** is a sentence cut from the owner's OWN words (`own_words.analyze`: never pasted text, never a third person's quoted speech), capped at
300 characters, PII-scrubbed at the write boundary; it is stored on the retired row as `retire_quote`.

**The candidates** (`memory_retire.candidates`): `MemoryService.search(quote)` and the first three ELIGIBLE rows: the owner's own, `approved`, a
person-fact type, not a tombstone (`state_change`), not a pasted-text note, not a row only an operator may change, and ABOUT the owner or about someone the sentence
names (`subject_ok`: "User's sister Odette plays the cello" is never offered for "I gave up the cello", and is for "Odette gave up the cello").

**The judge** sees the sentence and numbered notes, and answers `{"pick": N}` (N = 0 none). It never supplies text, an id or an identity.

* CHAT lane, the DETERMINISTIC judge (2026-10-09, the S10 live fix): the same per-turn digest pass as voice (`memory_digest._quote_retire_pass` ->
  `memory_retire.distill_turn(lane="chat")`), after the reply, for any named account (a guest has no store; no speaker verdict is needed on a typed turn). It exists
  because the brain tool alone was NOT reached live: on main 4bb903d2 the S10 turn "I gave up the cello." was served by the Flue brain in ONE round with the
  tool disclosed (the `memory` group trigger matched) and never called - a 4B model does not call a tool on a statement that asks it nothing - so no `QUOTE_RETIRE`
  line was written, not even in shadow, and the bar read `store_retired: false`. The class: a capability must never depend on the model CHOOSING to call a tool
  when the server can see the cue itself. A chat turn the brain's tool already decided (a pick, even 0) is not judged twice (`memory_retire.brain_decided`);
  a turn that is off the record, opted out of memory, or the digest skipped for a third-person pronoun subject is not judged.
* CHAT lane, the optional extra: the Flue brain, as the `memory_retire` tool, two calls. No arguments: zoe-data shows the (up to three) notes. `pick=<n>`: the one it ends, or 0.
  The sentence is the turn `zoe_flue_client` noted (`memory_retire.note_turn`) - the tool sends only a number. The tool is in the `memory` group and is
  pre-disclosed when the owner's message states that something ended or changed (the group's trigger regex), so the always-on system prompt is
  byte-identical (the group catalogue is untouched; pinned in the sidecar tests).
* VOICE lane: never in the turn. `memory_digest.run_turn_digest` (which already runs after the reply is spoken) asks the local model the same question
  (`memory_retire.distill_turn`) once every deterministic check has passed: no cue, an unverified speaker or no candidate costs NO model call. A chat turn with a
  cue now pays the same one extra local-model call (max 24 tokens, after the reply, inside the capture the bar already waits on).

**The wall** (`memory_retire.decide`; each check is a module function so a bench control can break it alone), enforced whatever the judge said:

| Guard | Rule | Refusal label |
|---|---|---|
| (a) the choice is offered | the row must be one of the rows shown THIS turn; a number out of range, a made-up id, a row outside the three is refused | `row_not_offered`, `pick_out_of_range` |
| (b) the quote is attached | the verbatim owner sentence is stored with the retirement (`retire_quote`, `retire_turn_ref`, `retire_lane`, `retire_cue`) or nothing is retired; a hard PII reject drops the retirement | `store_refused` |
| (c) the verified owner's own words | chat: an authenticated named account (never a guest); voice: the server's own speaker gate said `True` (None, "no verdict", is NOT a yes); the cue must be in the owner's part of the turn, not a paste or a third person's speech | `speaker_unverified`, `not_the_owner`, `not_owner_words`, `voice_lane_is_off_turn` |
| (d) the owner's own row | ownership re-checked in the store under the per-user lock; about the owner or someone the sentence names; never an operator's row | `row_not_eligible` |
| forgotten entity | a sentence naming a forgotten entity (300 s tombstone, durable ledger) retires nothing | `forgotten_entity` |

A BRAIN tool call made during a SPOKEN turn changes nothing (the turn is noted as voice); the digest is the voice judge.

**The effect** (`MemoryService.retire_with_quote`): there is no successor row - the sentence IS the evidence. Under the per-user lock the row goes to
`status=superseded` with `invalid_at` = now and `expired_at` (the two timelines of #1896: `search(as_of=<a moment before>)` still returns the old fact),
its text and embedding untouched, and the quote on it. Nothing is deleted. The audit row names the retirement and never the sentence. The row's power
is the owner's own words (`user_stated`): a later statement of the owner always wins, except over an operator's cleanup.

**Forgetting**: `memory_forget_entity` matches a row's text OR its cited `retire_quote`, so forgetting "cello" archives, and with physical erase on erases, a row
whose own text never names the cello but whose cited sentence does (bench cells `S10x.forgotten_entity_leaves_no_quote` and, on real Chroma, `S10x.forgotten_quote_not_on_disk`).

## `ZOE_QUOTE_RETIRE`

| Value | What happens |
|---|---|
| `shadow` (DEFAULT; also any unrecognised value) | the cue gate, the candidates and the judge (both lanes) run; the decision is LOGGED (`QUOTE_RETIRE user= lane= mode= action= reason= cue= rank= cands= row=`: ids, ranks and counts, never text) and NOTHING is written. The brain tool's reply never claims a change. |
| `enforce` (`1`, `true`, `yes`, `on`) | the same, and a retirement that passes every wall is applied |
| `off` (`0`, `false`, `no`, `off`) | inert: no note, no search, no model call |

Read per call. Flip to `enforce` only after the live-tier measurement (below) and a review of the shadow logs.

## Proof

Store tier (`scripts/perf/zmb/scenarios/retirement.json`, axis `retirement`, 15 cells; Z0 only; every claim has a control that must turn it red):
the S10 shape retires the right row with the quote attached and keeps the sister's copy and every other row (and is returned `as_of` before); a mention
retires nothing even with a judge that always says yes; a cue that ends nothing reaches a judge that says none; questions, plans, hedges and negations open no
door; an unverified voice retires nothing (sanity: a verified voice does); a third person's quoted words and pasted text retire nothing; another person's row is never
offered; a row outside the three is refused; shadow writes nothing; forgetting leaves no retained row citing the sentence, nor a byte of it on disk; on the pilot's
100-row pool no other-person copy is offered or retired across 30 changes and a judge that always takes the first row; the cue gate; and on Z0e
(real Chroma + MiniLM through the service's own blended search) the old row is retired on 30 of 30 changes with an honest judge. Controls: `retire_cue`, `retire_judge`,
`retire_speaker`, `retire_ownwords`, `retire_offered`, `retire_owner_row`, `retire_shadow`, `retire_quote`, `retire_forget` (plus `retrieval`).

Live tier (`S10x.live.*`, declared `tier: full`, for the bake-off clone brain; run by `scripts/perf/zmb/s10x_live.py --clone-url http://127.0.0.1:11500`): the bars
of the record - at least 24 of 30 correct retirements; at most 2 of 40 wrong on non-changes and 0 of 10 on the hard set; 0 of 30 other-person copies; 0 retirements from a
third-party, unverified or pasted turn (and no judge call made for one); `as_of` before the change returns the old fact for every correct retirement; no model call and at
most 40 ms on a turn with no change cue. The driver is an instrument: `--self-check` runs a perfect judge (passes every bar) and the naive rule, retrieval's top-1 with no judgement
(26 of 30 right, 9 of 40 wrong - 7 of them hard - fails three bars). The real judge has NOT been run: it needs the clone brain window, which the owner schedules.

## What is measured and what is not

* Measured here (Orin, lab, synthetic): the cue gate rates above; the candidate stage on the real embedder (old row among the three on 30 of 30 with the copies filtered;
  26 of 30 as retrieval's top-1, as the pilot said); the walls; the effect; the forget erase on disk; a cue-less turn costs about 0.2 ms and no model call; a turn WITH a cue
  pays the candidate search, about 60 ms median (MiniLM, 100 rows) - reported, not gated, and off the reply path (chat: inside the brain's tool call; voice: after the reply).
* NOT measured: whether Gemma 4 E4B makes the judgement (>= 24 of 30, <= 2 of 40, 0 of 10) - the whole point of the live tier; whether the 4B brain CALLS the tool on a change
  (the description and the disclosure trigger are the only nudge; there is no doctrine line, so the always-on prompt and the replay baseline do not move); the voice replay gate with
  this change (see below). The record's fallback if the judge is below 80 percent or above 2 false retirements: the idle path with a stricter cue gate, no brain tool.
* Not built: "no, I still play" re-opening a retired row (the operator's `restore_superseded` exists but needs a successor id); an event-time `invalid_at` ("I gave up the cello last March"
  invalidates at the capture time); a similarity floor on the candidates (the judge's 0 is the floor); a successor tombstone row (the per-turn digest still writes its own `state_change`
  row for the same sentence, which is why a retired row is never offered twice).

## Replay isolation (step 1 is a read, but REPLAY-GATED)

The replay gate feeds recorded voice through the live pipeline and must never write, so no tool may dispatch on a replay turn. Step 2 (`pick=N`) is a write: `runWrite`, like every other write.
Step 1 (no argument) changes nothing, but it stages per-turn state in zoe-data, so it is gated as well - and it is NOT listed in `_BARE_DISPATCH_READS` of `test_replay_write_isolation.py`
(that allowlist is the owner's decision, and that file is untouched). Instead it goes through `runWrite`'s chokepoint in READ mode (`isRead`, the 7th argument): the replay gate stays, and only the two gates that
exist to stop a WRITE (`ZOE_BRAIN_ALLOW_WRITES`, the untrusted-turn tier) are dropped, so on a live turn step 1 behaves exactly as before. On a replay turn it never reaches zoe-data and the tool hears `Noted.`.
Pinned from both lanes, red-when-removed: `labs/flue-zoe-brain-2x/test/replay_isolation.test.ts` (the `memory_retire` no-pick case: a POST on a replay turn is red) and
`services/zoe-data/tests/test_memory_retire_replay_gate.py` (a bare `dispatchIntent('memory_retire', ...)`, the replay gate deleted from `runWrite`, or the read flag on step 2 is red).

## Voice-path files (the replay gate applies)

`labs/flue-zoe-brain-2x/src/tools/zoe-tools.ts` and `tool-groups.ts` (a new tool and a disclosure trigger; the always-on system prompt, the group catalogue and every existing tool's
schema are byte-identical), `services/zoe-data/zoe_flue_client.py` (one dict write per turn; the outbound request is byte-identical whatever the flag, pinned by `test_flue_quote_retire_note.py`),
and `services/zoe-data/memory_digest.py` (`run_turn_digest` wraps the unchanged digest; the extra pass runs only for `voice_turn_digest`, after the digest, off the turn). Default shadow means
a cue turn on the voice lane makes ONE extra local-model call after the reply, and the logs fill; nothing is written.

## Bake-off

The S10x cells are Zoe's own layer, not a memory-engine feature: they are on their own axis (`retirement`), need capability `quote_retire` (Z0 only), are SKIPPED on every Hindsight arm and left
out of the H arms' cell lists (`cells.z0_only`), so they cannot move the pre-registered arm-vs-Z0 comparison (the C temporal axis is untouched). `S10x.pool_right_rows_retired` runs on Z0e.
