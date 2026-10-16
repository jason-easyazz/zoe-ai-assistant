---
type: Runbook
title: Omi voice tagging - speaker turns, unknown voices, learned voices (ZOE_OMI_SPEAKER_TAG, ZOE_OMI_LEARN_VOICES, flag-dark)
description: How pendant recordings are cut into speaker turns, how unknown voices are grouped per member, how a member's tag teaches Zoe a voice for suggestions only, the measured cost and calibration, the guardrails, the flags and the operator steps.
tags: [omi, pendant, speaker-id, diarisation, resnet34, biometrics, flags, runbook]
timestamp: 2026-10-17T00:00:00Z
---

# Omi voice tagging

**Status: built, FLAG-DARK.** Design: [memories-space.md](../architecture/memories-space.md) sections 2.4, 2.6, 3 and the
2026-10-17 build note in section 9. Owner decision 2026-10-14: tagging a person teaches Zoe their voice, with no spoken-consent
gate, for suggestions in the tagging member's own recordings only.

## What runs, and where

Code: `services/zoe-data/omi_diarize.py`, `omi_voices.py`, `routers/omi_voices.py`, migration 0054.

1. **Turns.** The pass runs inside the existing transcription claim, under the same gate (night window, voice turn, RAM floor),
   before the WAV is deleted:
   - Silero VAD regions, from a private single-threaded session.
   - 2 s windows with a 1 s hop, embedded by the panel's own pinned ResNet34 INT8 file. `scripts/setup/zoe_speaker_resnet34.py`
     is loaded by path, with its sha256 pin, centring pin and self-test.
   - Centroid-linkage clustering cut at 0.45.
   - Each turn is labelled against the wearer's consented panel profile in that model: `you` at 0.351 or above, `other` below
     0.15, `unsure` between, `mixed` when a quarter of the windows say each of you and other. With no profile the label is
     `unverified`.
2. **Text.** Moonshine is unchanged. It transcribes each turn on its own, splitting a long turn at its quietest points, so the
   text lines up with speakers without word times. The turns cover the whole recording, so no audio goes untranscribed.
3. **Unknown voices** (`ZOE_OMI_LEARN_VOICES`).
   - Only `other` turns, and `unsure` turns at least 0.10 below the wearer's bar, of 2 s or longer, are kept: the embedding
     for 14 days. The wearer's own `you` turns are never kept.
   - Each kept turn is filed under one of the member's own unknown voices ("Voice 3"), joining a voice at a cosine of 0.45 or
     more.
4. **Tags.** The member's tag is the confirmed word:
   - It creates or links their own `people` row, with `visibility='personal'` and a name field of `entered_by_member`
     provenance.
   - When learning is allowed, the turns' embeddings join that person's learned voiceprint: the mean after leave-one-out
     outlier removal, rebuilt from each contribution.
5. **Suggestions.**
   - A suggestion needs a cosine of at least 0.45 and must beat the runner-up by 0.10. Each "no" raises that voice's margin
     by 0.02.
   - A suggestion is never a tag until the member says yes.
6. **Not a person.** The member can mark a voice or a line as Zoe, TV, radio or media. Its lines then leave suggestions and the
   day review, and a later line matching that voice is marked when it arrives.
   - `omi_voices.zoe_spoken_spans` is the seam for marking lines that overlap Zoe's own spoken replies. It returns nothing today,
     because no store logs TTS playback with wall-clock times.

## Why this and not an adopted diariser (evaluated 2026-10-17, sources read with opensrc)

| Option | Verdict |
|---|---|
| **VAD + the pinned ResNet34 + linkage (chosen)** | No new dependency and no second runtime. It keeps the embeddings, which matching against the wearer and learned voices needs. |
| sherpa-onnx 1.13.8 offline diarization (Apache-2.0; pyannote segmentation-3.0 ONNX, MIT, an ungated GitHub asset) | It bundles its own ONNX Runtime 1.28 beside zoe-data's pinned 1.23.2, so it would need a subprocess. It returns speaker indices only, with no embeddings. At its default shift it computes about 3 times the embeddings. It is the fallback if overlapping speech proves to be the weak point. Its clean-up rules (bridge pauses under 0.5 s, drop blips under 0.3 s) were adopted. |
| pyannote community-1 (pyannote.audio 4.0.7; model CC-BY-4.0, HF-gated) | Rejected. It needs torch, torchaudio and lightning, well over 1 GB on this box. |
| Omi's own backend | Diarisation and embeddings are a cloud service. Its prompt set ("Was this you?", "Is this <name>?", "Who is this?", "not a person") and its runner-up margin rule were adopted. |

## Cost (measured on the Jetson, 2026-10-17)

- **RAM:** +41 MB RSS when the session loads (89 MB peak for a standalone process with ORT and numpy). The session is loaded
  per pass and released.
- **CPU:** about 100 ms and 0.1 CPU-s per window on ONE thread, about 6 CPU-s per minute of speech.
- **Threads:**
  - BLAS is pinned to one thread while embedding. The numpy fbank otherwise fans out over every core: 5 times the CPU for the
    same wall time, measured.
  - The VAD uses its own one-thread session.
  - Both run off the event loop.

## Calibration

Reproduce with `scripts/perf/omi_voice_calibrate.py`. Its gates: not 01:50-04:55, MemAvailable at least the measured peak RSS
plus 600 MB, and no voice turn in the last 5 minutes. The owner's numbers go to a private report in `~/.zoe/reports/` and never
into the repo.

Public labelled speakers: LibriSpeech test-clean, speaker-disjoint from the centring cohort.

| Cell | Setting chosen | Result |
|---|---|---|
| Suggestions: 5 tagged voices of 3 clips each, against their other clips and 10 strangers, over 30 trials | min 0.45, margin 0.10 | precision 99.6 %, recall 74.4 %, a stranger suggested 0.2 %, wrong person 0.0 % (min 0.40 gives 98.7 % / 82.7 %, with strangers at 0.8 %) |
| Unknown-voice clustering: 8 speakers x 8 clips, shuffled | 0.45 | purity 100 %, 2.2 voices per speaker (0.40 gives 99.1 % / 1.8). Purity wins: a merged voice would mis-tag someone. A split voice only costs a second tag. |
| Two-speaker conversations: 10 conversations of about 45 s | link 0.45, you 0.351 | 76 % of the time correctly labelled; `you` precision 85 %, recall 89 % (you 0.309: 83 % / 90 %; link 0.35: 74 % / 75 %) |

**Pendant channel: calibration pending.**
- The WAVs still on the box are the ones Moonshine found no words in: the empty-transcript rule keeps those, and a transcribed
  recording's WAV is deleted. So they hold almost no speech, and the pendant-versus-panel shift cannot be measured from them.
- The labels need ground truth: the owner's per-line corrections (`owner_label`) during the `shadow` fortnight. Optionally keep
  audio (`ZOE_OMI_KEEP_AUDIO`) for a calibration week. That is an owner privacy decision.
- Until then, "you" is judged against the panel profile at the strict bar, which yields more `unsure` by design.

## Guardrails (each pinned by `services/zoe-data/tests/test_omi_voices.py`, with a negative control)

- **Layer.** Everything is in the tagging member's own layer. Another member, even an admin, cannot list, change or be suggested
  from it.
- **Never authentication.** An allow-list test pins the only modules that may name the tables. `auth`, the voice session,
  `/api/voice/identify` and the profile sync, adaptation and enrolment are not among them.
- **Suggestions are not tags.** The day review reads `omi_voices.review_segments` only: confirmed tags, with no suggestions and
  no not-a-person lines.
- **Erasure.**
  - "Forget <name>" runs `contacts_store.forget_voiceprints` then `omi_voices.purge_people`. It removes the voiceprint, the
    contributions, the tags and the suggestions.
  - Deleting a recording drops its turns and contributions, and rebuilds the voices they fed.
  - Erasing a member removes every voice row.
  - A name forget redacts the turn text too.
- **Expiry.** The sweep runs whether or not learning is on. Held embeddings and unknown voices expire after 14 days.
  Unconfirmed voices expire after 30 days, and confirmed ones after 365 idle days. Soft-deleted people are purged.
- **Children.** A child's pendant is not tagged. A member who is not positively an adult never holds a bystander voice. A tagged
  person who may be a child is a label only.
- **Backups.** The nightly backup carries no row data of the four biometric tables.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `ZOE_OMI_SPEAKER_TAG` | off | `shadow` / `on`: tag and store turns (the review's use of labels is P5's decision) |
| `ZOE_OMI_LEARN_VOICES` | off | hold bystander embeddings, cluster unknown voices, learn from tags, suggest |
| `ZOE_OMI_TAG_YOU_MIN` / `_OTHER_MAX` | 0.351 / 0.15 | wearer labelling |
| `ZOE_OMI_DIAR_LINK_MIN` | 0.45 | within-recording linkage cut |
| `ZOE_OMI_VOICE_CLUSTER_MIN` | 0.45 | across-recording unknown-voice cut |
| `ZOE_OMI_SUGGEST_MIN` / `_MARGIN` | 0.45 / 0.10 | suggestion bar and runner-up margin |
| `ZOE_OMI_SEGMENT_EMBED_TTL_DAYS` | 14 | life of a held bystander embedding and an unknown voice |
| `ZOE_OMI_VOICEPRINT_PROVISIONAL_DAYS` / `_IDLE_DAYS` | 30 / 365 | learned-voice expiry |
| `ZOE_OMI_TAG_MODEL_DIR` | `~/.cache/zoe-speaker-resnet34` | where `fetch_speaker_models.sh build` wrote the INT8 file |

## Operator steps (none needed for the merge; all are owner decisions)

1. Run migration 0054.
2. Make sure the INT8 file is in `~/.cache/zoe-speaker-resnet34`. It is already built on the Jetson.
3. The wearer needs a consented ResNet34 panel profile: `ZOE_SPEAKER_MODEL=dual` plus the 5-phrase re-enrolment. Without one,
   every turn is `unverified` and nothing is learned.
4. Set `ZOE_OMI_SPEAKER_TAG=shadow`, restart zoe-data, and run the voice replay once with a tagging pass beside it (cell T8).
5. After the owner has corrected some lines, re-derive the thresholds, then optionally set `ZOE_OMI_LEARN_VOICES=1`.

**Rollback:** set the flags off. An operator `DELETE` of `omi_segment_embeddings`, `omi_voice_clusters`,
`learned_voiceprint_segments` and `learned_voiceprints` removes every learned voice.

## Not built yet

- A pendant enrolment (`pendant_speaker_profiles`, the section 3.4 flow).
- The TTS-playback log behind `zoe_spoken_spans`.
- Household-member detection for D12.
- The UI: the desktop Pendant tab and the panel "Who is this?".
- The day review (P5, a sibling branch) reads `review_segments`.
