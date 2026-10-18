---
type: Reference
title: Voice lane parity - the spoken lane does what the typed lane does
description: The lane-parity instrument (the same utterance through the real chat path and the real voice path over a fake store and a stubbed model transport), the measured before/after table, what was ported or fixed on the voice lane, and what the rig cannot see.
tags: [voice, chat, parity, fast_tiers, samantha, harness, zoe-data]
timestamp: 2026-10-10T00:00:00Z
---

# Voice lane parity (`scripts/perf/lane_parity.py`)

Register row G11 / felt gap 7: "the voice panel behaves worse than typed chat". The ledger said the voice lane lacks the correction,
contacts and verify-on-challenge tiers; nothing measured it. This does.

## The instrument

`services/zoe-data/tests/lane_parity_rig.py` runs the REAL `routers.chat.chat_stream_generator` and the REAL
`routers.voice_tts.voice_command` (text-injected exactly as the Pi daemon posts it after STT) in one process. Real: `fast_tiers.resolve`
and every tier, the intent router and the contacts handlers, the provenance / correction / hold tiers, the memory service (a throwaway
palace), `brain_dispatch` and the Flue seam (verify-on-challenge, recall / offer / identity blocks, the outbound message). Faked, so
nothing can reach a live store: the database (in memory; it RECORDS statements it does not understand), the model transport under the
seam (one stub, so "what would the sidecar have been sent" is a measured fact for both lanes), TTS, broadcasts, the post-turn memory
passes. Demo identities only (`demo_lane_parity_<lane>_<cell>_<hex>`); no service contacted, no flag written, no brain time.

`tests/lane_parity_cells.py` holds the cells. A cell is PASS on a lane when the household gets the capability (the stored fact changed, the
contact exists, the claim was checked, the thumb exists, no model turn was spent where a lookup answers), not when the two reply strings are
byte-equal: a spoken write is read back before it is made, so the voice script of a write carries one extra turn ("Yes.").

```
python3 scripts/perf/lane_parity.py --details     # the table + why each cell passed or failed
python3 scripts/perf/lane_parity.py --controls    # the instrument's own proof
python3 -m pytest services/zoe-data/tests/test_voice_lane_parity.py
```

The flags the rig sets mirror the live service's conversation flags (read from its env on 2026-10-10, on/off only):
`ZOE_CORRECTION_APPLY`, `ZOE_VERIFY_ON_CHALLENGE`, `ZOE_CONTACTS_CONVERSATIONAL`, `ZOE_HOLD_THE_FACT=enforce`, `ZOE_SELF_MODEL=enforce`,
`ZOE_EXPERT_MODE=active`. The ledger line of 2026-10-05 that said the correction and verify tiers were off predates their flip.

## Measured (origin/main `dba34f08a` -> this change)

| cell | before chat / voice | after chat / voice | what was wrong on the voice lane |
|---|---|---|---|
| correction ("that's wrong, my sister is Marisol not Marisa") | FAIL / FAIL | PASS / PASS | no tier existed on EITHER lane (both fell to the model): new `correction_apply.maybe_swap`, read-back "Got it, your sister is Marisol." |
| contact_add ("add my brother Percival") | PASS / PASS | PASS / PASS | none (voice reads it back and waits for "yes", by design) |
| contact_list ("list my contacts") | PASS / FAIL | PASS / PASS | went to the 4B; now the same executor chat uses |
| contact_lookup ("who is Percival?") | PASS / FAIL | PASS / PASS | same |
| verify_on_challenge ("are you sure?" on a world fact) | PASS / PASS | PASS / PASS | none: the seam is shared and takes `voice=True` |
| feedback_down ("that was wrong") | FAIL / FAIL | PASS / PASS | `chat_feedback` had ONE writer (the thumbs endpoint); voice filed it as an engineering ticket and wrote no thumb |
| feedback_up ("good answer") | FAIL / FAIL | PASS / PASS | same; and a kind word cost a model turn |
| provenance ("why did you say that?") | PASS / PASS | PASS / PASS | none |
| forget_it | PASS / PASS | PASS / PASS | none |
| exact_words ("what exactly did I say") | PASS / PASS | PASS / PASS | none (the seam notes the turn on both lanes) |
| pending_not_a_yes (spoken-lane safety) | n/a / FAIL | n/a / PASS | "are you sure?" CONFIRMED a waiting contact write |
| pending_not_correct (spoken-lane safety) | n/a / FAIL | n/a / PASS | "no, that's not correct, it's Percy" CONFIRMED it |

Parity 5/12 before, 12/12 after. Three of the five tiers the ledger named were already shared; the real gaps were the lane's private stages
in front of the shared core.

## What changed

* `fast_tiers.resolve(phase=...)`: `"conversation"` (distress, provenance, feedback, self-model, correction, pull, the person half,
  identity, "remember that") and `"domain"` (Tier-0, router, experts); `None` is both, chat's order. The voice lane runs the conversation half
  FIRST (as chat does) and the domain half where it always did, so its confirmation gate, Skybridge cards and keyword intents can no longer
  pre-empt the conversation tiers.
* `conversation_feedback.py` + `ZOE_CONVERSATION_FEEDBACK` (default on): "that was wrong" / "good answer" write `chat_feedback` through the
  one writer `record_feedback` (the chat thumbs endpoint now calls it too).
* `correction_apply.maybe_swap` (under `ZOE_CORRECTION_APPLY`): value swap with evidence.
* `routers/voice_tts.py`: `_pending_decision` (a yes is a short yes), `_contains_decision_keyword` (multi-word keywords never matched),
  contacts READS through the shared executor for an identified speaker.

## The extra lanes: non-stream chat and LiveKit (A4, 2026-10-19)

`--all-lanes` also drives `chat_ns` (the NON-stream `routers.chat.chat`, `POST /api/chat/?stream=false` - the Telegram bot's call)
and `livekit` (`routers.voice_livekit._run_pipeline`, the estate Ask card's room agent, with STT returning the utterance). Both run
the REAL `brain_oneshot` down to the same stubbed transport. Their columns are REPORTED; the parity verdict stays chat vs voice.

Measured on the A4 branch (rig, live flag mirror):

| lane | cells passing | why |
|---|---|---|
| chat (stream) | 12 / 12 | - |
| voice (`voice_command`) | 14 / 14 | - |
| chat_ns (Telegram) | 12 / 12 | `chat()` still re-implements the generator block by block, but every cell agrees; `tests/test_voice_lane_parity.py::test_non_stream_chat_matches_the_stream` pins it per cell until the two are collapsed |
| livekit (Ask card) | 2 / 12 | brain-first by design: `fast_tiers` runs only under `ZOE_LIVEKIT_FAST_TIERS` (off live), so correction, contacts, feedback, provenance, forget and identity all go to the model; only `exact_words` and `ack_after_offer` (brain cells) pass. The control: with the flag on, `feedback_up` passes |

The `/ws/voice/` lane the rig never covered is retired (A4): the phone/desktop page now posts each turn to `/api/voice/turn`, i.e.
`voice_command`, the `voice` column.

## What the rig cannot see

The model (a stub: the brain's own use of tools, its wording and its latency are not measured here), the real Postgres (a fake that
understands `people`, `chat_messages`, `chat_feedback`), the post-turn memory passes, TTS and the panel UI, and the Pi daemon's wake / STT /
speaker gate. The voice replay gate (`tests/replay_samples.py`) mirrors the voice lane's `fast_tiers.resolve` call, not
`voice_command`'s private stages, so it cannot see the reorder; this rig is what covers that.
