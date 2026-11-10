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
- [ ] **"Who said this?" (second kind, added with the people-tagging design).** A turn the night labelled "?" or "X?" (leaning) is asked the same way: the line in the caption style, the candidate people (best first) as big targets, "Someone new" (a persistent unnamed voice he can name later by voice - "that's Quinn" - or on the phone), "Same as <unnamed voice>" to merge two unnamed voices, and "Play it". His answer relabels the turn and adds its embedding to that person's voiceprint. A forgotten or deleted person is never offered.
- [ ] **Private to him.** Only the member whose recording it is, on a verified voice session or a PIN session. A guest, or a guest-scoped panel, sees nothing - not even a count. A forgotten name is never shown.

Each box has a test (`services/zoe-data/tests/test_transcript_check.py`, `test_touch_transcript_check_harness.py` -> `services/zoe-ui/dist/test_touch_transcript_check.js`) or a live check below.

## What it is

Two halves around one table (`omi_check_items`, migration `0076`).

**Night (the re-pass).** The night re-pass runs Qwen3-ASR and Parakeet every night, and Whisper / Granite on a sample, then a ROVER word vote fuses them. Where no strict majority agreed (a *contested slot*), or every other model is more than 50% away from Qwen3 (a chunk flagged `[low confidence]`), the span goes on the check queue. `transcript_check.candidates_from_row` takes the stored ROVER chunk rows `omi_repass_eval.is_contested` selects (`t0`/`t1`, `hyps` {model: text}, `rover` the pick, `slots` `{pos, pick, votes, who, n}`, `low_conf`; the shape `omi_repass_eval.check_queue` returns) and `enqueue` ranks it:

| class | what | base score |
|---|---|---|
| name | a household name, or a capital mid-sentence | 100 |
| number | digit, number word, weekday/month, clock word | 90 |
| commitment | the sentence reads as a commitment / task (the day review's own regexes) | 70 |
| chat | everything else | 10 |

plus +40 when a PENDING day-review candidate cites the recording, plus 0..9 for how close the vote was. The day keeps the best `ZOE_TRANSCRIPT_CHECK_MAX_ITEMS` (10); whole-chunk items never take more than 3 of them. On the lab's real pendant day through the production 3-voter ROVER (184 chunks, 55 contested, 75 candidates) the cap keeps 10 (`zoe-transcript-check.py --rehearse-lab`).

Each item holds: the recording id and time, the sentence split `before | span | after`, each model's alternative with its vote count and which models offered it, the ROVER pick, the audio snippet bounds, and an `expires_at` equal to the recording's `audio_expires_at`. While an item is open its recording's WAV is **held** (`release_tagged_audio` skips it); answered / skipped / expired releases it; the existing 7-day retention still bounds everything. Owner-private layer: the table is erased with the recording and the member, and a forgotten name deletes the items that name it.

**Morning (the ask and the screen).**

1. `routers/voice_tts.py` notes who each voice turn was *verified* as (`transcript_check.note_voice_turn`, next to the day-review raise's note). Typed chat never notes, so it can never be asked.
2. After the reply: the brain stream (`zoe_flue_client`, `transcript_check.append_stream`) or a deterministic tier (`voice_tts._answer_from_tier`, `append_to_reply`) appends the line - only when the reply finished with real text, does not itself end in a question, is not an emotional turn (the emotional turn guard, fail-closed), it is after 06:00 household time, the queue holds something the member may see, and the day's lock (`omi_check_asks`, a primary key) is won. An interrupted reply releases the lock. The notification is left at the same moment.
3. The next turn: `transcript_check.tier` (a fast tier) takes it only if it is a plain yes / later to the ask within 2 minutes. Yes pushes a private `voice_card` (`open: "checks"`, a card token for THIS panel's open voice session, a count, never a sentence); the touch estate opens the Transcript check screen. A stray "yes" with no ask pending is never taken.
4. The screen (`services/zoe-ui/dist/touch/home.html` stage `checks`, `js/touch-transcript-check.js`): modelled on the Memories space - renders what the server gives, sign-in prompt on 401/403, sentence leaves the DOM after 2 minutes untouched, unmount on navigation. A quiet card on the home screen (`#tcNote`) appears only when the server gave THIS session a non-zero count.
5. Answering: `POST /api/transcript-check/{id}/answer {alt|word}` corrects the matching `omi_segments` row(s) and the recording transcript (anchored on the words around the span), sets `text_provenance='owner_corrected'`, scores every model's stored hypothesis for that chunk against the chunk as he corrected it (`omi_repass_eval.note_owner_correction` -> #2358's `ledger.jsonl`, counts only; no second ledger), then `omi_day_review.reopen_recording_in` deletes that recording's PENDING candidates and clears `day_review_id` so the next review reads the corrected text (approved / dismissed ones are decisions and stay). The correction, the re-open, the ledger credit and the `answered` status are ONE transaction (a failure fails the answer; a retry is safe), and an answer whose words are no longer in the stored text corrects nothing, so it credits nothing and the item is closed as `expired` (409 `text_moved`).
6. None of these: **Say it** - `POST .../listen` opens a 45 s window; the owner's next *verified* voice utterance (at most 4 words) is the answer (`via='voice'`). **Fix on phone** - a QR to the Pendant page.

## "Who said this?" (kind `who`)

Same queue, same ask, same cap (the ~10 a day is shared with the word items), same privacy. Input: `transcript_check.candidates_from_who(row)` takes a `who_said` row - `t0`/`t1`, `text`, `candidates` `[{person_id | cluster_id, name, score}]` best first (a candidate with a `cluster_id` and no `person_id` is an UNNAMED voice), `first` (answering gives a voice with no pendant print its first confirmation) and `turns`. **#2358 does not expose `who_said` rows yet**: until `omi_repass.finish_one` receives `res["who_rows"]` (the hook is in place) no `who` item exists; `candidates_from_who` is the whole interface. Design source: `~/.zoe/lab/omi-people-tagging/`.

- **Ranking**: `who_first` 95 (between names 100 and numbers 90) when the answer gives a voice its first confirmation, else `who` 60, plus +1 per waiting turn of that voice (max 10), plus the vote-margin tie-break. So the first confirmations for a voice with no print outrank everything but names.
- **Screen**: the turn as a caption line (`.cvf .cvl`), "Who said this?", one big chip per candidate person (names; the people store has no avatar field the panel can read today, so none are drawn), "Same as <voice>" chips for unnamed voices, "Someone new", Play it, Skip.
- **Applying**: a person -> `omi_voices.tag(person_id, segment_id)` = the CONFIRMED tag on the turn and, with learning allowed (not a child, member learning on), the turn's embedding joins that person's voiceprint (the pendant print in this speaker space; when #2358 / the named-guests registry expose a dedicated seam, `_answer_who` is the one place to switch). Someone new -> `set_owner_label(other)`: the turn stays in its persistent unnamed voice (`omi_voice_clusters`), nothing is named. Then the screen offers "Say the name" (the next verified utterance, "that's Quinn", `heard_name`: at most 2 words) or leaves it for the phone / the day review's "New voices" card; naming creates the person in his own layer through the same `tag(name=...)`. "Same as" -> the turn and every turn of its voice join the chosen unnamed voice, the emptied one goes, the centroid is rebuilt (`_merge_into`). The pending day-review candidates of the recording are re-opened in the same transaction (the review reads who said what).
- **Forget**: a candidate is offered only if the person is still one of his own live people (`omi_voices.own_people`) and the forget ledger does not hold the name; a stale tap on one is refused (410 `person_gone`); a typed / spoken name the ledger holds is refused (410 `forgotten`); a turn that no longer exists is refused (409 `text_moved`).

## Modes

| mode | night | morning |
|---|---|---|
| `off` | nothing | nothing; every hook returns before any I/O |
| `shadow` | builds the queue as `shadow` rows, logs `TRANSCRIPT_CHECK_SHADOW {...}` (counts per class, never a word), holds no audio | asks nothing, shows nothing, routes absent |
| `on` | real queue, audio held | the ask, the notification, the screen |

Flags: `ZOE_TRANSCRIPT_CHECK`, `ZOE_TRANSCRIPT_CHECK_MAX_ITEMS` (10), `ZOE_TRANSCRIPT_CHECK_ASK_FROM_HOUR` (6).

## Contracts with other work

- **Stacked on #2358** (the night re-pass; its ROVER fusion, `omi_repass_eval`, hyps store and ledger are consumed, not rebuilt). `omi_repass.run_night` hands `finish_one` each recording's contested chunk rows (`res["check_rows"]`) so items exist BEFORE the WAV is released; `omi_repass_eval.check_queue(home, user, rid)` reads the same rows back from `hyps/hyps-<night>.jsonl` (7-day cut).
- **Migration `0076`** chains on #2358's `0075` (the night re-pass); `alembic heads` is a single head. If another migration lands first, renumber at merge: the validate gate's single-head check will say so.
- The **contender ledger** is #2358's `ledger.jsonl` (`owner_hit` / `owner_miss` per model); `transcript_check.ledger()` summarises it.
- Conversation notes (#2360) and clip playback (#2346) are untouched; the screen plays its snippet through its own route (`omi_recordings.span_clip`, the same cut and rules as `segment_clip`).

## Rehearse by day (owner rule: rehearse a night job by day)

1. `python3 scripts/maintenance/zoe-transcript-check.py --rehearse-lab` - the night half on the lab's real fusion output; counts only, nothing written. Expect the cap (10) kept, names and numbers first.
2. Set `ZOE_TRANSCRIPT_CHECK=shadow` after the re-pass reaches shadow; after a night, `--shadow-report` shows what it would have asked (and `--status --user ID` the shadow rows). Nothing is spoken or shown.
3. Before `on`: with `shadow` rows from a real night, set `on` in the daytime (not 01:50-04:55; restart zoe-data under `night_busy.sh deploy`), and from the panel say one command as yourself (verified voice, after 06:00). Zoe answers it, then the line follows; say "yes"; the screen opens; touch a chip; check `--status` shows `answered: 1` and the ledger moved; ask again the same day: no second ask. Say "later" the next day: notification left, no second ask.
4. A guest check: ask the panel something as a guest after step 3 (no line), and open `/touch/home.html?domain=checks` signed out (sign-in prompt, no sentence).

## Not in scope

The fusion itself (lab, `rover.py`), the contender bake-off that reads the ledger, a desktop surface (desktop and touch stay separate), and any spoken prompt that is not riding a turn he started.
