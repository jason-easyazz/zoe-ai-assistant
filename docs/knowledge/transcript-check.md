---
type: spec
title: The morning transcript check - Zoe asks, on a turn you started, for the words the night could not settle
description: Flag-dark ZOE_TRANSCRIPT_CHECK=off|shadow|on - the night re-pass's ROVER fusion leaves a ranked queue of uncertain words; the owner's first verified-voice turn after 06:00 carries one appended line; the panel's Transcript check screen lets him touch the right word, which corrects the stored text and teaches the per-model contender ledger.
tags: [omi, pendant, transcripts, panel, voice, spec]
timestamp: 2026-11-09T00:00:00Z
---

# The morning transcript check

## Acceptance - true when done

In the owner's words (2026-11-09, verbatim): "I can check over transcripts to pick the correct words, could this be something Zoe could present me in the morning, say I say a command in the morning and it does it, it could always say 'Hey Jason, I had a few issues with the transcripts last night, can you help me when you have a moment.' Then maybe leave it as a notification and I can check it later. This would be great visually on the panel, with the ability to set the right word by touching on it, written out the same way Zoe's speech gets written out now."

- [ ] **"I say a command in the morning and it does it."** The command is done and answered first; only then does the one line follow. Never before, never instead.
- [ ] **"Hey Jason, I had a few issues with the transcripts last night, can you help me when you have a moment."** One short line, once a day, in the member's own preferred name (never hard-coded), on the first VERIFIED-voice turn after 06:00. Never on a guest, an unverified voice or a typed turn, never in the middle of a feeling, never when there is nothing to ask. (He does not want Zoe speaking unprompted - `ZOE_PROACTIVE_SPOKEN=0` - so it only ever rides a turn he started.)
- [ ] **"Then maybe leave it as a notification and I can check it later."** "Yes" / "sure" opens the screen on the panel. "Later", or no answer, leaves a panel notification and Zoe never asks again that day.
- [ ] **"Visually on the panel, written out the same way Zoe's speech gets written out now."** Each sentence is drawn with the live-caption component (`.cvf .cvl`), with the uncertain word highlighted.
- [ ] **"Set the right word by touching on it."** Touch the highlighted word, touch the right chip. There are also Play it, None of these (then Say it, by voice, or Fix on phone, by QR) and Skip. No keyboard on the panel. "3 of 7" progress; it can be left and resumed.
- [ ] **A correction sticks and teaches.** The stored segment and transcript are corrected (provenance `owner_corrected`), the per-model contender ledger counts the owner as ground truth, and any pending day-review candidate that rested on that recording is re-made from the corrected words.
- [ ] **Private to him.** Only the member whose recording it is, on a verified voice session or a PIN session. A guest, or a guest-scoped panel, sees nothing - not even a count. A forgotten name is never shown.

Each box has a test (`services/zoe-data/tests/test_transcript_check.py`, `test_touch_transcript_check_harness.py` -> `services/zoe-ui/dist/test_touch_transcript_check.js`) or a live check below.

## What it is

Two halves around one table (`omi_check_items`, migration `0073`).

**Night (the re-pass).** The night re-pass runs Qwen3-ASR and Parakeet every night, and Whisper / Granite on a sample, then a ROVER word vote fuses them. Where no strict majority agreed (a *contested slot*), or every other model is more than 50% away from Qwen3 (a chunk flagged `[low confidence]`), the span goes on the check queue. `transcript_check.candidates_from_chunk` takes the fusion's per-chunk output (`tokens`, `slots` `{pos, best, cands{word: votes}, surf, n}`, `hyps`, `mode`) and `enqueue` ranks it:

| class | what | base score |
|---|---|---|
| name | a household name, or a capital mid-sentence | 100 |
| number | digit, number word, weekday/month, clock word | 90 |
| commitment | the sentence reads as a commitment / task (the day review's own regexes) | 70 |
| chat | everything else | 10 |

plus +40 when a PENDING day-review candidate cites the recording, plus 0..9 for how close the vote was. The day keeps the best `ZOE_TRANSCRIPT_CHECK_MAX_ITEMS` (10); whole-chunk items never take more than 3 of them. On the lab's real fusion output (184 chunks, 152 contested slots + 38 low-confidence chunks = 190 candidates) the cap keeps 10: 6 names, 4 numbers (`zoe-transcript-check.py --rehearse-lab`).

Each item holds: the recording id and time, the sentence split `before | span | after`, each model's alternative with its vote count and which models offered it, the ROVER pick, the audio snippet bounds, and an `expires_at` equal to the recording's `audio_expires_at`. While an item is open its recording's WAV is **held** (`release_tagged_audio` skips it); answered / skipped / expired releases it; the existing 7-day retention still bounds everything. Owner-private layer: the table is erased with the recording and the member, and a forgotten name deletes the items that name it.

**Morning (the ask and the screen).**

1. `routers/voice_tts.py` notes who each voice turn was *verified* as (`transcript_check.note_voice_turn`, next to the day-review raise's note). Typed chat never notes, so it can never be asked.
2. After the reply: the brain stream (`zoe_flue_client`, `transcript_check.append_stream`) or a deterministic tier (`voice_tts._answer_from_tier`, `append_to_reply`) appends the line - only when the reply finished with real text, does not itself end in a question, is not an emotional turn (the emotional turn guard, fail-closed), it is after 06:00 household time, the queue holds something the member may see, and the day's lock (`omi_check_asks`, a primary key) is won. An interrupted reply releases the lock. The notification is left at the same moment.
3. The next turn: `transcript_check.tier` (a fast tier) takes it only if it is a plain yes / later to the ask within 2 minutes. Yes pushes a private `voice_card` (`open: "checks"`, a card token for THIS panel's open voice session, a count, never a sentence); the touch estate opens the Transcript check screen. A stray "yes" with no ask pending is never taken.
4. The screen (`services/zoe-ui/dist/touch/home.html` stage `checks`, `js/touch-transcript-check.js`): modelled on the Memories space - renders what the server gives, sign-in prompt on 401/403, sentence leaves the DOM after 2 minutes untouched, unmount on navigation. A quiet card on the home screen (`#tcNote`) appears only when the server gave THIS session a non-zero count.
5. Answering: `POST /api/transcript-check/{id}/answer {alt|word}` corrects the matching `omi_segments` row(s) and the recording transcript (anchored on the words around the span), sets `text_provenance='owner_corrected'`, credits `asr_contender_ledger` (a model that offered the answer *agreed*, every other *contradicted*; counts only), then `omi_day_review.reopen_recording` deletes that recording's PENDING candidates and clears `day_review_id` so the next review reads the corrected text (approved / dismissed ones are decisions and stay).
6. None of these: **Say it** - `POST .../listen` opens a 45 s window; the owner's next *verified* voice utterance (at most 4 words) is the answer (`via='voice'`). **Fix on phone** - a QR to the Pendant page.

## Modes

| mode | night | morning |
|---|---|---|
| `off` | nothing | nothing; every hook returns before any I/O |
| `shadow` | builds the queue as `shadow` rows, logs `TRANSCRIPT_CHECK_SHADOW {...}` (counts per class, never a word), holds no audio | asks nothing, shows nothing, routes absent |
| `on` | real queue, audio held | the ask, the notification, the screen |

Flags: `ZOE_TRANSCRIPT_CHECK`, `ZOE_TRANSCRIPT_CHECK_MAX_ITEMS` (10), `ZOE_TRANSCRIPT_CHECK_ASK_FROM_HOUR` (6).

## Contracts with other work

- **Stacked on #2358** (the night re-pass, migration `0072`). The fusion stage is not in the repo yet: until the worker's result record carries `check_chunks` (a list of the chunk dicts above) the queue stays empty. That key is the whole interface; the lab prototype's `rover()` output is its shape. Per-slot `by_model {model: word}` gives exact ledger credit; without it a model is credited when its chunk text contains the word.
- **Migration number**: `0073` chains on `0072`. If another branch takes 0073 first, renumber this one.
- The **contender ledger** is `asr_contender_ledger(user_id, model, agreed, contradicted)`; the bake-off reads it through `transcript_check.ledger(user)`.
- Conversation notes (#2360) and clip playback (#2346) are untouched; the screen plays its snippet through its own route (`omi_recordings.span_clip`, the same cut and rules as `segment_clip`).

## Rehearse by day (owner rule: rehearse a night job by day)

1. `python3 scripts/maintenance/zoe-transcript-check.py --rehearse-lab` - the night half on the lab's real fusion output; counts only, nothing written. Expect the cap (10) kept, names and numbers first.
2. Set `ZOE_TRANSCRIPT_CHECK=shadow` after the re-pass reaches shadow; after a night, `--shadow-report` shows what it would have asked (and `--status --user ID` the shadow rows). Nothing is spoken or shown.
3. Before `on`: with `shadow` rows from a real night, set `on` in the daytime (not 01:50-04:55; restart zoe-data under `night_busy.sh deploy`), and from the panel say one command as yourself (verified voice, after 06:00). Zoe answers it, then the line follows; say "yes"; the screen opens; touch a chip; check `--status` shows `answered: 1` and the ledger moved; ask again the same day: no second ask. Say "later" the next day: notification left, no second ask.
4. A guest check: ask the panel something as a guest after step 3 (no line), and open `/touch/home.html?domain=checks` signed out (sign-in prompt, no sentence).

## Not in scope

The fusion itself (lab, `rover.py`), the contender bake-off that reads the ledger, a desktop surface (desktop and touch stay separate), and any spoken prompt that is not riding a turn he started.
