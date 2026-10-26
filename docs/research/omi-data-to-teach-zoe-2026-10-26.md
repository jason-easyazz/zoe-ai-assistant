---
type: research
title: Omi pendant data as Zoe's teacher - what to learn from the day's audio and transcripts, how, and in what order (2026-10-26)
date: 2026-10-26
status: research-only - no code, flag, service, model or table changed; nothing installed or run; the live box was read only for counts, sizes, file lists and code (no transcript text, no audio content)
description: Ranked use cases (memory, tone, speech recognition, router, speaker ID, routines, reminders) for the owner's pendant data, the training techniques that fit a local box without feedback loops, the privacy and Western Australian recording-law picture, what the 12B night window can afford (including whether it can hear audio), and a phased plan with measurable gates.
tags: [omi, pendant, ambient, self-training, privacy, night-window, asr, tone, speaker-id, research]
evidence_labels: "[V] verified: read in a primary source, or measured read-only on this box today | [S] secondary: press, vendor page, search summary, third-party listing | [E] extrapolated: my estimate from another model, chip or study | [U] unverified: not checked or not findable"
---

# Omi pendant data as Zoe's teacher (2026-10-26)

Plain-language research record. It builds on, and does not repeat:
[omi-gap-audit-2026-10.md](omi-gap-audit-2026-10.md) (the pendant-to-memory audit, bystander and firmware findings),
[tone-of-voice-deep-dive-2026-10-26.md](tone-of-voice-deep-dive-2026-10-26.md) (the sister record: speech-emotion evidence,
prosody plus text fusion, phases 0-2) and the contracts
[omi-voice-tagging.md](../knowledge/omi-voice-tagging.md), [night-window.md](../knowledge/night-window.md),
[biometric-retention-policy.md](../knowledge/biometric-retention-policy.md) and
[emotional-safety-note.md](../governance/emotional-safety-note.md).

PUBLIC repository: no household names, utterances, transcripts or per-person data. Every number about the owner's data is an
aggregate (counts, sizes, hours). The rocks (Gemma 4 E4B-QAT+MTP, Moonshine v2 Medium, Kokoro) are untouched by every option.
Nothing leaves the box; api.omi.me is never used.

## 0. Executive summary

1. **The gold is real, but most of it is currently words without ground truth.** Ten days of pendant sync hold 560
   transcribed recordings (62.1 h) and 152 empty ones (3.4 h), 5,014 speaker-turn rows over 318 recordings [V, counts]. The
   owner has corrected **zero** of them (`owner_label` is empty on all 5,014) and the day review has proposed 52 items, all still
   pending [V]. Learning needs *labels*, and the cheapest label source is the owner's approve/reject tap on those proposals.
2. **The "you" label is broken for the pendant, which blocks every speaker-aware use.** Of 5,014 turns, only 7 are labelled
   `you`; 3,464 (19.5 h) are `other` and 1,512 (26.4 h) `unsure` [V]. The wearer talks most of the day, so most `other` is
   very likely the wearer on a mic the panel profile was not enrolled on [E]. The fix already named in the contract is a
   **pendant-channel enrolment** ("not built yet") [V]. Until then, nothing may treat `other` as "a bystander".
3. **Raw audio is mostly gone, by design.** A transcribed recording's WAV is deleted right after text lands unless the
   owner keeps it; empties are kept to the 7-day bound; a "held for tagging" flag keeps WAVs that still need a name [V, code].
   Today 414 WAVs (5.6 GB, about 49 h) sit on disk, all inside the 7-day bound [V]. So any audio-based learning (tone,
   wake word, ASR) must either run **at transcription time and keep only derived numbers**, or the owner must opt in to keeping
   a bounded, consented slice.
4. **The best first win is tiny and already half built: a personal vocabulary.** Moonshine already supports runtime
   key-term biasing, and the live process reports 6 terms configured and 6 applied [V, `/readyz`]. The day review already finds
   people and places. Feeding *approved* names and places (tens, not thousands) into that list improves recognition of the
   words that matter most, with no model swap, no training and an existing flag.
5. **The night window can afford more than it uses, but it cannot hear audio today.** The 12B launch **drops `--mmproj`** on
   purpose (`bakeoff.deep_clone_command`, pinned by `test_night_window.py`) [V]. Over 33 completed nights the window used a
   median 14.1 min and at most 35.8 min of a roughly 52 min job budget [V, night reports]. Two nights spent about 31 min on the
   day review alone, so audio work must queue behind it with its own budget (section 3).
6. **Do not fine-tune the brain.** On personal data, retrieval beats fine-tuning for small models (+14.9% vs +1.1% in the
   best benchmark I found [S]); LoRA on a QAT 4-bit base with MTP is unproven on this stack [U]; and weights change the rock.
   Spend effort on retrieval, vocabulary, router examples and verified labels instead.
7. **Law and ethics set the shape.** In Western Australia the Surveillance Devices Act 1998 s 5(1) makes it an offence to
   record a private conversation *whether or not you are a party*, unless an exception applies; participant recording is
   lawful where each principal party consents (s 5(3)) [V, statute]. A household privacy exemption in the Privacy Act does not
   touch that. This is not legal advice, but it means: consent norms in the home, nothing learned from non-members' speech,
   short retention of their words, and a WA lawyer's view before widening anything (section 6).

## 1. What is in the data, and where it lives (verified, aggregates only)

| Fact | Value | Source |
|---|---|---|
| Span of synced days | 2026-10-12 to 2026-10-22 (10 days) | `omi_recordings.started_at` [V] |
| Recordings | 560 `done` (62.1 h), 152 `empty_transcript` (3.4 h); about 6 h/day of speech-bearing audio | DB counts [V] |
| Recordings with a transcript | 431 of 560 `done` | DB counts [V] |
| Speaker tags | 318 recordings `tagged`, 394 `unverified` | DB counts [V] |
| Speaker-turn rows | 5,014 over 318 recordings: `other` 3,464 (19.5 h), `unsure` 1,512 (26.4 h), `unverified` 25, `you` 7, `mixed` 6 | DB counts [V] |
| Owner corrections / tagged people | 0 owner labels, 0 tagged persons | DB counts [V] |
| Day review | 12 reviews (8 on the night lane, 4 on the day lane), 52 candidates, all pending: follow-up 12, list item 12, reminder 12, owner fact 8, person 7, calendar event 1 | DB counts [V] |
| Audio on disk | 414 WAVs, 5.6 GB, about 49 h, newest from 2026-10-23, none past the 7-day bound | `ls`/`du` [V] |
| Voice corpus `~/.zoe-voice-samples` | 1,644 WAVs (188 MB) including the quarantine folders; no emotion or transcript labels beyond filenames | `ls` [V] |
| Struggle events (panel turns) | 12 in total | DB count [V] |
| Speaker profiles enrolled (panel) | 2 | DB count [V] |

Storage and retention (code, `services/zoe-data/omi_recordings.py`, `omi_voices.py`) [V]:

- WAVs live in `~/.zoe/omi-recordings/` (mode 0600), 16 kHz mono. Default bound `ZOE_OMI_AUDIO_RETENTION_DAYS` = 7.
- A WAV is deleted right after a transcription that produced text, unless `keep_audio` or `ZOE_OMI_KEEP_AUDIO_FOR_TAGGING`
  holds it while a turn still needs a name. Empty-transcript WAVs (clicks) are kept to the bound so a deaf pipeline is visible.
- Transcripts, turn text and day-review candidates sit in Postgres. I found no age-based expiry for transcript text in
  `omi_recordings.py`; they go when the owner deletes a recording, on "forget <name>" (redacts the name) or on member erase.
  This is a gap for the policy in section 6.
- The pass over turn embeddings keeps bystander embeddings 14 days, unknown voices 30 days, confirmed voices 365 idle days.
- Nothing else may read pendant transcripts except the day review (static test `test_nothing_else_reads_pendant_transcripts`).
  Every use case below that wants the transcript therefore goes through the day review's approve-before-write gate, or needs a
  deliberate, tested widening of that allow-list.

## 2. What others do with always-on wearable data

Labels per section 0. Product facts come mostly from press; the Omi facts are from its repo and docs.

| Who | What it does with the data | What worked / what failed | Lesson for Zoe |
|---|---|---|---|
| **Omi (BasedHardware)** | Open source (MIT) hardware, firmware, app and backend; live transcription, summaries, action items, "memories" (key facts), speaker identification, an app/plugin marketplace and an MCP server ([repo](https://github.com/BasedHardware/omi), [docs](https://docs.omi.me/doc/get_started/introduction)) [V] | Launch-era criticism was always-listening plus cloud processing of conversations ([TechCrunch](https://techcrunch.com/2025/01/08/omi-a-competitor-to-friend-wants-to-boost-your-productivity-using-ai-and-a-brain-interface)) [S]. I found no documented personal-model fine-tuning and no documented offline mode on the pages read [V absence, shallow] | Zoe is already further on the privacy axis (local). Omi's useful ideas (memories, action items, "was this you?") are already adopted in the day review and tagging prompts |
| **Limitless (ex-Rewind)** | Pendant with summaries and search; "Consent Mode" paused recording on unfamiliar voices until they agreed. Meta acquired it and stopped new pendant sales in Dec 2025 ([Neowin](https://www.neowin.net/news/meta-acquires-ai-startup-limitless-ending-sales-of-its-popular-pendant-wearable), [analysis](https://www.solidaitech.com/2026/08/wearable-ai-notetaker-dead-picks-legal-risks.html)) [S] | Consent Mode is the best bystander precedent, but it needed cloud voice recognition and its voice-signature storage drew questions [S] | Local voiceprints plus a pause-on-unknown-voice policy is a design Zoe can do more privately |
| **Bee** | Wrist device, continuous transcription, stores transcripts not audio, has a mute button; Amazon agreed to acquire it in 2025 ([Tom's Guide](https://www.tomsguide.com/ai/amazon-buys-wearable-ai-startup-bee-a-wrist-device-that-hears-and-transcribes-every-word-you-speak)) [S] | Commentary focused on who owns the data after acquisition [S] | Transcripts-not-audio is a sound default; a mute control is expected |
| **Plaud, Rabbit** | Not researched in depth | | [U] |
| **Humane AI Pin** | Cloud wearable; devices stopped working on 2025-02-28 and customer data was deleted ([Tom's Guide](https://www.tomsguide.com/ai/the-humane-ai-pin-is-officially-dead-and-hp-is-picking-up-humanes-leftovers)) [S] | Product failure, not mainly privacy | On-box operation survives a vendor's death |
| **Friend pendant** | Cloud processing; public ad defacement ("surveils you") ([CNN via KVIA](https://kvia.com/news/business-technology/cnn-business-consumer/2025/11/16/how-this-tiny-device-became-a-symbol-for-the-backlash-against-ai/)) [S] | Backlash centred on bystanders | Same |
| **Microsoft Recall** | Screen snapshots searchable on the PC. Backlash: plaintext store plus on-by-default capture. Fixes: opt-in, Windows Hello to enable and view, encrypted store ([Windows Central](https://www.windowscentral.com/software-apps/windows-11/microsoft-addresses-windows-recall-backlash-promises-to-fix-security-issues-and-make-it-opt-in)) [S] | Opt-in plus encryption plus authenticated viewing recovered trust | Zoe's approve-before-write and member-scoped layers match the repaired pattern |
| **Meta Ray-Ban** | The 2024 flashpoint I found was a student demo joining glasses to face search, not recording as such ([Macworld](https://www.macworld.com/article/2480505/just-say-no-to-metas-glasses.html)) [S] | | Bystander harm comes from *linking* captured people to identity |
| **Academic** | Personalised-LLM benchmark LaMP: retrieval (RAG) about +14.9% over non-personalised, LoRA fine-tuning about +1.1%, both about +16.0%; retrieval wins for cold-start users ([arXiv 2409.09510](https://arxiv.org/html/2409.09510v2)) [S]. RAG beats unsupervised fine-tuning for injecting knowledge ([Ovadia et al.](https://arxiv.org/abs/2312.05934)) [V]. Bystander studies: wearers worry more than bystanders object, but bystanders want opt-in ([Hoyle 2014](https://homes.luddy.indiana.edu/kapadia/papers/hoyle-ubicomp14.pdf), [CISPA 2026](https://cispa.de/en/research/publications/105070-see-me-if-you-can-a-multi-layer-protocol-for-bystander-privacy-with-consent-based-restoration)) [S] | Small qualitative studies only; no large norm measurement found. NTCIR Lifelog / MyLifeBits / personal knowledge graphs not researched [U] | Retrieval first, weights last |

Pattern across the field [E from the above]: backlash came from cloud storage of other people's speech, capture on by default,
plaintext stores and no bystander control. Local processing answers the first and third; the other two are design choices Zoe
still has to make explicitly.

## 3. What the night window can afford

Read-only: `scripts/night/night_window.py`, the unit and timer, `docs/knowledge/night-window.md`, 45 night reports.

- **Does the 12B launch use an mmproj? No.** The parked unit text loads `--mmproj mmproj-gemma-4-12b-it-qat-q4_0.gguf`
  (175 MB on disk) with `--no-mmproj-offload`, but the window builds its command through `bakeoff.deep_clone_command`, which
  removes both flags ("no vision is needed"), and `test_night_window.py` asserts `--mmproj` is absent [V]. The 12B in the
  window is text-only. Re-adding the projector is a code change to the generator plus a measured trial, never a nightly default.
- **The model card says the 12B takes audio** (no separate encoder, audio through linear layers into the decoder; 30 s cap;
  25 tokens per second) ([card](https://ai.google.dev/gemma/docs/core/model_card_4), [audio guide](https://ai.google.dev/gemma/docs/capabilities/audio)) [V].
  Documented tasks: transcription, translation, "general understanding". No emotion or paralinguistic benchmark is published [V absence].
  llama.cpp has Gemma 4 audio support in its multimodal library since April 2026, BF16 projector recommended; whether the 12B audio
  path works on the box's b11194 build, and the Jetson-specific issues one tutorial reports for the smaller model, are unverified [S/U].
- **The budget.** The timer starts at 02:50; hard cap 65 min, 12 min reserved for the restore, end-by 03:55, so about 52 min for jobs.
  Measured decode is about 6.6 tok/s and prefill about 157 tok/s at ctx 8192 [V, doc].
  Of 45 reports, 33 were completed `ok` nights: **median 14.1 min, maximum 35.8 min elapsed**; the rest were refusals, quiet-night
  no-ops or aborts [V]. The free tail is therefore roughly 16 min in the worst completed case and about 38 min at the median.
  The day review is the *last* job and takes whatever is left (budget flag): on two nights it ran about 31 min, on one about 12 min,
  on the others under a minute [V]. **Any new overnight job competes with it**, so give each job an explicit budget slice, and order
  by value (vocabulary mining and label queue before any audio judging).
- **Audio-judging cost** [E]: a 10 s clip is about 250 audio tokens, about 1.6 s of prefill, plus the projector (CPU, unmeasured) and
  a 30-token verdict at 6.6 tok/s (about 4.5 s): roughly 8-12 s per clip. 150 owner-labelled clips is about 20-30 min, so the
  tone test in section 4b fits **one manual trial window** rather than a nightly budget.
- **RAM risk** [V + E]: the lowest MemAvailable seen beside the 12B was 1,251 MiB against a 1,200 MiB floor (a past trial). Adding
  a 175 MB projector and audio compute buffers could trip the floor, which kills the running job and restores. Hence "trial first".
- **Rules that stay**: no landing or harness in the quiet hours; the transcription loop already refuses to run during the window;
  `omi_recordings` WAVs are not touched at night.

## 4. The use cases, each researched

### 4a. Personal memory and facts (people, plans, preferences)

- **Already built** [V]: the day review proposes meetings, list items, reminders, follow-ups, people and owner facts with
  verification rules (exact quote, first-person cue for actions, broadcast stretches dropped, restraint classes counted not
  proposed), approve-before-write, forget cascade, a Telegram digest module (`omi_review_digest.py`), and the 12B night lane.
  The memory authority layer applies user-stated > confirmed > inferred with provenance.
- **What is missing** [E from the code and counts]:
  1. **Nobody has said yes or no yet.** 52 pending, 0 decided. The loop that makes the system better (accept/reject as labels) has not started.
     A review that is only "on in shadow" produces no learning signal.
  2. **Recall of the owner's own speech depends on the broken `you` label** (section 0, point 2): the day review reads `someone_else`
     lines as context only, so if the owner's lines are mislabelled `other`, owner facts are suppressed.
  3. **Precision measurement:** no accept-rate, no per-kind precision. Baselines are needed before any tuning.
  4. **Evidence for stability:** habits and preferences should need repetition across days before promotion, not one mention.
- **Verdict:** highest value, lowest risk, mostly a switch plus measurement. Gate: accept rate per kind and the false-proposal
  rate, measured on aggregate decisions.

### 4b. Tone and mood learning over time

Builds on the sister record (prosody plus text cues fused; speech-emotion recognition is poor on natural speech; audio LLMs mostly
follow the words; phase 0 is a gated, explainable detector).

- **What the pendant adds**: a long, natural, always-the-same-person record (62 h in 10 days) from which a **per-speaker, per-channel
  baseline** of level, rate and pause behaviour can be computed; the sister record called such a baseline the missing ingredient
  [V]. It is a different microphone and distance from the panel, so baselines must be **per channel**; do not mix.
- **Weak labels from what happens next** [E]: a later owner correction, a re-ask, an abandoned request or an approved/rejected
  proposal are *outcome* labels, independent of the audio. Use them as noisy labels for "something went wrong here" only, never as
  "the owner was angry". Report aggregate association between prosody deltas and these outcomes; train nothing on them yet.
- **Can Gemma 4 12B hear tone? Test, do not assume.** Evidence says audio LLMs follow the words, default to neutral and sit near chance
  on prosody-only clips (LISTEN benchmark, cited in the sister record). Test design (one manual window, section 3 budget):
  1. The owner labels about 150 clips of their own (frustrated / neutral / urgent / tired / unsure), local only, time-boxed.
     Use panel clips from the voice corpus first (known channel), pendant clips only if the owner keeps a consented slice.
  2. Four arms on the same clips: **text only**, **audio plus text**, **audio only**, and a **shuffled-audio control** (same transcript,
     audio swapped from another clip). A model that really listens must drop on the shuffled arm.
  3. Include about 30 negative controls (calm speech over loud TV, whispered speech) and exclude children and guests entirely.
  4. Report macro-F1, accuracy against chance with a confidence interval, and agreement (kappa) with the owner.
  5. **Gate:** audio-plus-text beats text-only by at least +0.10 macro-F1, the shuffled-audio arm falls to within chance, and the
     false-"frustrated" rate on calm clips is at most 5%. Otherwise the 12B audio judge is dropped (a valid, cheap result).
- **Policy limits** (governance note, unchanged): act in the moment; no per-clip emotion record; records only for consenting adults;
  children and guests never; an inference never overrides what was said; never tell the person "you sound angry".
- **Verdict:** valuable, uncertain, high creep risk. Do phase 0 of the sister record first; use the pendant only for the baseline
  and for the aggregate outcome association. Audio-LLM judging is a one-window experiment.

### 4c. Speech recognition from the owner's real voice

- **Personal vocabulary (names, places): best fit.** Moonshine has `Transcriber.set_keyterms` (streaming architectures only),
  already wired as `ZOE_MOONSHINE_KEYTERMS` and applied (6 terms) [V]. Vendor guidance: tens of terms, thousands hurt accuracy;
  terms may not contain commas; case is reproduced [V, `docs/knowledge/moonshine-0-1-5-upgrade.md`]. The model card and paper
  document no other biasing, no LM rescoring and no official fine-tuning recipe ([card](https://huggingface.co/moonshine-ai/moonshine-streaming-medium),
  [paper](https://arxiv.org/abs/2602.12241)) [V absence]. Cheap complements that keep the model: a post-ASR fuzzy/phonetic
  correction against the same approved lexicon, and the 4B/12B as a text-level corrector with the lexicon in the prompt [E].
  Source of terms: **owner-approved** people and places from the day review plus names in the contacts store; never un-approved text.
  Replay-gate every change (voice path), A/B with and without terms on the corpus, under the harness lock.
- **Fine-tuning Moonshine on the owner's far-field audio:** weights are MIT ([card](https://huggingface.co/moonshine-ai/moonshine-streaming-medium)) [V];
  a community recipe exists and the vendor offers paid customisation, both labelled experimental [S]. Pseudo-labels from the same model
  mostly teach it its own errors (confirmation bias; mitigations are mixup and a floor of true-labelled samples per batch,
  [arXiv 1908.02983](https://arxiv.org/abs/1908.02983)) [V]; Noisy Student worked with a *stronger* teacher, augmentation and filtering
  ([arXiv 2005.09629](https://arxiv.org/abs/2005.09629)) [V]. The pendant's transcripts would be labelled by the model being trained.
  Independent labels would have to come from owner corrections or the 12B as teacher, and the raw audio is mostly deleted. **Defer**;
  re-open only after the golden set (section 5) exists and vocabulary biasing has been measured.
- **Wake word (openWakeWord):** the panel uses openWakeWord with threshold 0.45 and a 3-hit confirm [V, env example]. Library facts [V]:
  code Apache-2.0, **pretrained models CC BY-NC-SA 4.0**, shipped models trained on synthetic speech; the per-speaker verifier is
  a logistic-regression second stage needing at least 3 real positives and 10 s of the speaker's other speech, and the project
  calls collected false activations "one of the most effective ways to reduce false activations"
  ([repo](https://github.com/dscripka/openWakeWord), [verifier doc](https://github.com/dscripka/openWakeWord/blob/main/docs/custom_verifier_models.md)).
  The pendant is not the wake-word microphone; its audio is a poor stand-in for the panel's far-field channel [E]. The right data are
  the **panel's own** saved clips and false wakes (the corpus already has a TV false-wake quarantine). The pendant's use here is
  **hard negatives**: run the wake model over transcribed pendant speech at transcription time and keep only the score
  distribution (no audio kept) to estimate the false-accept rate per hour of ordinary household talk [E]. Gate: false accepts per
  hour measured over at least several hours of real household audio, not a two-minute test (one community model passed a 120 s
  test and then false-woke 90 times an hour [S]).
- **Verdict:** vocabulary is rank 1; wake-word hard-negative scoring is cheap and safe as a measurement; ASR fine-tuning waits.

### 4d. Router and brain improvement

- **Router self-training exists** (`router_selftrain.py`, flag-dark, with a ratchet: no accuracy regression, chat false positives
  = 0, p50 under 600 ms, replay gate passed) and a struggle-fed variant (flag-dark). Only 12 struggle events exist in total [V].
- **What the pendant adds**: most pendant speech is *not addressed to Zoe*, so it is poor positive data for tool calls. It is
  good **negative data**: ordinary conversation is what the router's "chat false positive = 0" gate must keep ignoring [E].
  Mine it as derived text (no audio): sentences the router scores as a tool request while the day review marks them as
  conversation or media become hard negatives, owner-approved in aggregate. Real requests to Zoe are better mined from the panel's
  own turns and struggle events.
- **Eval cases:** owner-approved reminders and list adds that were *said to the pendant* show the phrasing the owner uses when
  Zoe is not listening, which is useful for a held-out natural-phrasing set but must not leak into the training split.
- **LoRA on Gemma 4 E4B?** Unsloth reports about 10 GB for E4B training and about 17 GB for LoRA ([Unsloth](https://unsloth.ai/docs/models/gemma-4/train)) [V], other
  guides say 18-22 GB [S]; the box has 15.6 GB shared with the OS and the day stack, so it would only be possible overnight with the
  stack stopped. Unverified for this stack [U]: Unsloth/bitsandbytes/PEFT on Jetson aarch64, a LoRA on a QAT parent surviving
  q4_0 quantisation, `--lora` with the MTP drafter (likely lowers draft acceptance [E]). Evidence favours retrieval (above).
  It also changes the brain's weights, which the canonical lock treats as a rock. **Verdict: no.** Revisit only for a narrow
  format/style adapter, after all retrieval and prompt options are exhausted.
- **Verdict:** small direct value from the pendant; useful as hard negatives. Keep using the existing ratchet.

### 4e. Speaker ID

- **The enrolment-quality opportunity is large**: the pendant supplies hours of the owner's speech in natural conditions. The
  contract already lists the missing piece: a **pendant-channel owner profile** (`pendant_speaker_profiles`), separate from the
  panel profile [V]. Owner confirmation ("was this you?") on a handful of high-confidence turns is the correct bootstrap, because
  the unlabelled labels cannot bootstrap themselves (7 `you` out of 5,014).
- **Risks of self-training on its own guesses** [S + E]: classic online-adaptation work shows impostor corruption and drift; the fixes
  are confidence-weighted updates and a fixed anchor ([Heck and Mirghafori](https://www.microsoft.com/en-us/research/?p=166284),
  [Preti et al. 2007](https://www.isca-archive.org/interspeech_2007/preti07_interspeech.html)). I found nothing modern for ResNet/ECAPA
  embeddings [U]. The repo already has the right shape for the panel (`ZOE_SPEAKER_ADAPT`, an anchor that is never rewritten, a slow
  moving average capped from the anchor) [V]. Rules for any pendant-derived update: only owner-confirmed or very high margin turns;
  update only the **pendant** profile; never the panel profile or any authentication path (the allow-list test already forbids it);
  keep a rollback copy; re-score a frozen enrolment test set after every update.
- **Evidence status**: the thresholds were calibrated on public speech and the current table is flagged stale after a leakage fix [V].
  Re-derive them from the owner's first corrections.
- **Verdict:** high value (it unblocks 4a, 4b and most of the rest), medium effort, biometric risk: owner consent, owner-confirmed only.

### 4f. Anything else valuable

| Idea | What the data gives | Notes |
|---|---|---|
| **Reminders the owner said aloud** | Commitments with date and time, already a day-review kind (12 pending) | Highest practical value; needs a decision loop and a timed delivery, not new research |
| **Conversation summaries** | A day or meeting summary from approved lines | Stored as owner-scoped derived text; excludes other people's words as facts |
| **Routines and habits** | Timestamps of approved events and lists across weeks (when shopping happens, recurring appointments) | Promote only after repetition across at least 3 separate days; label `inferred`, expiring, never overrides stated |
| **Anticipating needs** | Combine routines with calendar/list state | Surface as a suggestion on request or first turn, not unprompted speech (the owner turned unprompted morning speech off) |
| **Pendant health and coverage** | Hours of speech per day, empty-click rate, sync lag | Already in `omi_health.py`; aggregate only |
| **Meeting notes for other people** | | Out: third-party speech |

## 5. Training techniques that fit, and how they are fenced

Technique to use and the guard that makes it safe on a one-person data set:

| Technique | Where it fits | Guard against learning from itself |
|---|---|---|
| **Weak supervision with outcome labels** | Corrections, re-asks, abandoned requests, approve/reject taps as noisy labels | Labels come from events *after* the output, never from the model's own confidence; store as `inferred`; aggregate before use |
| **Pseudo-labelling with confidence gating** | Speaker turns, lexicon candidates, hard negatives | Gate on a margin over the runner-up and a fixed floor; teach from a *different* model or the owner, not the model being trained; keep a floor of human labels in every round (confirmation bias, [arXiv 1908.02983](https://arxiv.org/abs/1908.02983)) |
| **Active learning with a tiny budget** | Ask only through the day review's approve/reject tap | Spend labels on the lowest-confidence and highest-disagreement items first [E]; cap asks per day; never ask more than the owner answers (the digest already waits after unanswered rounds) |
| **Held-out evaluation** | Every claimed gain | **Frozen golden set**: labelled by the owner only, split by time and by speaker, never touched by any pseudo-labeller or model under training; same set before and after every change; shuffled and permuted controls must fall to chance |
| **Feedback-loop control** | Anything where output feeds the next input | Without fresh real data each generation, quality degrades ([Shumailov et al., arXiv 2307.01850](https://arxiv.org/abs/2307.01850)) [V]; therefore: no training on text the model wrote, no memory write from an inference, and `user-stated > confirmed > inferred` enforced by the authority layer |
| **Ratchets** | Router, wake word, keyterms | Promote only if no said-vs-did regression on `~/.zoe-voice-samples` and per-stage speed is not worse; every change reversible |

Measured on aggregate numbers only: accept rate per proposal kind, false-proposal rate, keyterm hit rate on a labelled subset,
false-accept rate per hour, WER on the owner-corrected golden set, macro-F1 for tone, time and RSS per job. No per-utterance
figure is written to the repo.

## 6. Privacy, law and ethics

**Western Australia (high level, not legal advice).** [V = read in the statute via a fetcher, s 5 and s 9 of the
[Surveillance Devices Act 1998 (WA)](https://www.legislation.wa.gov.au/legislation/prod/filestore.nsf/FileURL/mrdoc_45890.htm/$FILE/Surveillance%20Devices%20Act%201998%20-%20%5B02-g0-00%5D.html)]
- s 5(1): a person must not use a listening device to record or listen to a private conversation to which they are **not** a party,
  or to record one to which they **are** a party. Penalty for an individual: $5,000 or 12 months, or both [V].
- s 5(3): recording a conversation you are a party to is allowed where **each principal party consents**, expressly or impliedly,
  or one principal party consents and the recording is reasonably necessary to protect that party's lawful interests [V].
  So WA is not a one-party-consent state by default. "Private conversation" excludes circumstances where the parties ought reasonably
  to expect they may be overheard [V].
- A pendant that records two *other* people talking is within s 5(1)(a), unless the conversation is not private or an exception applies;
  the unintentional-hearing exception is unlikely to cover a device worn on purpose [E, no case law read].
- s 9: knowingly publishing or communicating a private conversation is also an offence, with exceptions (consent of each principal party;
  protecting lawful interests; legal proceedings). A transcript used only by the owner's local assistant is arguably not "published",
  but sending a summary to other people moves toward the line [E].
- Children: nothing in the Act I read treats them differently; whether a parent can consent for a child is not addressed [U].
- Other states: reported to differ (Victoria, Queensland and the NT permit participant recording; NSW and SA generally do not)
  ([ALRC Report 123](https://www.alrc.gov.au/publication/serious-invasions-of-privacy-in-the-digital-era-alrc-report-123/14-surveillance-devices/participant-monitoring/)) [S, 2014]; check current text before relying on it.
- Privacy Act 1988 (Cth) s 16: the APPs do not apply to an individual's handling of personal information for personal, family or household
  affairs [V]. That removes the federal privacy principles, not the state recording offences.

**What follows for the design (policy proposals for the owner, not legal conclusions):**
1. **Household consent norm**: every member who is regularly near the pendant knows and agrees; a visible indication and a physical mute
   are expected; a guest-mode pause when an unknown voice appears (the Limitless "Consent Mode" idea, done locally).
2. **Never learn from, and never keep long, the words of people who are not members.** Their turns are context at most for the same day;
   no voiceprint without a tag the member confirmed; the existing 14-day embedding expiry already points this way.
3. **Children**: no pendant tagging, no voiceprint, no affect record, no tone label, no vocabulary mined from their speech; their lines
   are dropped from the day review and any training set.
4. **Never learned or kept**: health, money, grief and family-conflict content (the day review already counts these without proposing them),
   anything said "off the record", credentials spoken aloud, identity claims, content from TV or radio, and anything about an entity on the
   forget ledger (**forgotten means forever**: the shield is permanent, and a candidate naming a forgotten entity is never stored).
5. **Raw audio versus derived features.** Prefer derived features computed at transcription time (prosody deltas, wake-word score
   histograms, speaker margins, key-term hit counts) and delete the WAV as today. Keep raw audio only as a **bounded, owner-consented slice**
   with a hard expiry, for the golden set and for failure analysis, never for bulk training.
6. **Retention**: WAV 7 days (unchanged); candidates and decisions kept until the owner deletes them; **transcript text needs an age bound
   or a stated reason to keep** (gap found in section 1); derived numbers aggregate-only; any derived record carries provenance and
   `inferred` status, and "forget" must erase it with the rest.
7. **Provenance on every write**: source recording id, the pipeline version, the model, and the owner decision that promoted it.

## 7. Ranking: value / effort / risk

Scale: value H/M/L, effort S (under half a day) / M (days) / L, risk L/M/H (privacy, regression, creep). Ranked by value divided by
effort and risk, my judgement.

| # | Use case | Value | Effort | Risk | Verdict |
|---|---|---|---|---|---|
| 1 | **Close the label loop on the day review** (accept/reject, per-kind accept rate, a daily ask the owner actually answers) | H | S | L | Do first. Nothing else can be measured without it |
| 2 | **Personal vocabulary into Moonshine key terms** from owner-approved people/places (+ fuzzy post-correction) | H | S | L | Do second. Flag and plumbing exist; replay-gate it |
| 3 | **Reminders and commitments said aloud** (already a kind) | H | S | L | Finish delivery and timing; it is the most tangible win |
| 4 | **Pendant-channel owner enrolment** (fixes `you`) | H | M | M | Do third. Unblocks 4a, 4b, 4e; owner consent; pendant profile only |
| 5 | **Summaries and routines** from approved items | M | M | M | After 1 and 4; repetition rule; inferred, expiring |
| 6 | **Tone: sister-record phase 0 plus pendant baseline** | M | M | H | Measure first; use pendant only for baseline and aggregate association |
| 7 | **Wake-word hard-negative scoring** (derived scores, no audio kept) | M | S | L | Cheap measurement; informs a verifier later |
| 8 | **12B audio tone test** (150 labelled clips, four arms) | M | M | M | One manual window; drop if it fails the gate |
| 9 | **Router hard negatives / natural-phrasing eval set** | L-M | S | L | Small; keep inside the existing ratchet |
| 10 | **Moonshine fine-tune on own voice** | M | L | M | Defer until golden set and measured vocabulary gain |
| 11 | **Adaptive update of the panel speaker profile from pendant audio** | L-M | M | H | No. Pendant profile only |
| 12 | **LoRA of the E4B brain** | L | L | H | No (retrieval wins; rock; unproven on this stack) |

## 8. Phased plan with measurable gates

All phases: lab-first, flag-dark, aggregate-only reporting, voice-path changes replay-gated under
`flock /tmp/zoe-voice-harness.lock`, nothing landing in the night window or while the household is awake, forget and provenance
rules applied in the same change.

### Phase 0 - make the data learnable (days, no model change)
1. Turn the day review's approve-before-write loop on for the owner and run it for 7 days; decide the 52 pending items.
2. Add counters (aggregate only): proposals per kind, accept/reject/ignore rate, time to decision.
3. Add an age bound or an owner-chosen retention for pendant transcript text.
4. **Gate:** at least 30 decisions recorded; per-kind accept rate known with a stated interval; zero accepted items naming a forgotten entity;
   zero candidates sourced from a child.

### Phase 1 - cheap wins (1-2 weeks)
1. Build the key-term list from approved people/places (cap 40), A/B with and without on the corpus, apply if the ratchet passes.
2. Pendant-channel owner enrolment with owner-confirmed turns; recompute thresholds on the first corrections.
3. Wake-word hard-negative scoring on pendant speech (scores only).
4. **Gates:** said-vs-did not worse and per-stage speed not worse on the corpus; keyterm hit rate up on a labelled subset by a margin set
   beforehand; `you` recall on owner-confirmed turns at least 0.85 with precision at least 0.95; false accepts per hour measured over
   at least 4 h of household audio.

### Phase 2 - measured experiments (after Phase 0 labels exist; manual windows)
1. The golden set: about 150 owner-labelled clips plus the owner's corrections, split by time and by speaker; frozen.
2. The 12B audio tone test (section 4b) in one manual window with the projector re-added; record MemAvailable low, time per clip,
   and the four-arm result.
3. Tone phase 0 of the sister record using the pendant baseline per channel.
4. **Gates:** audio-plus-text beats text-only by at least +0.10 macro-F1 and the shuffled-audio arm is within chance; tone flag precision
   at least 0.8 with false-apology rate at most 1 per 100 turns (sister record); projector fits with MemAvailable never below the floor.

### Phase 3 - only if Phases 1-2 leave a gap
Moonshine adaptation or a wake-word verifier, judged against the golden set with a held-out speaker split; router hard negatives through
the ratchet. **Never:** brain LoRA, panel-profile adaptation from pendant audio, cloud processing, training on the model's own text.

### Night-window use
Order inside the window: digest, dreaming, night mind, struggle proposals, day review (existing), then new work with explicit budgets:
lexicon mining (text only, seconds), label-queue preparation (seconds), and, only after the Phase 2 trial, a bounded audio-judging job
sized from the measured per-clip time. Never above the free tail left after the day review; the existing budget flag and the end-by
deadline are the stop.

## 9. Decisions for the owner

1. Run the day-review approve loop daily for a week and answer it? (Without answers there are no labels.)
2. Approve building the **pendant-channel enrolment** (your voice on the pendant mic, confirmed by you, pendant profile only)?
3. May Zoe feed **approved** names and places into the speech recogniser's key-term list (tens of terms)?
4. Raw audio policy: keep only transcripts and derived numbers (recommended), or also keep a small consented slice (about 150 clips,
   hard expiry) for the golden set and the tone test?
5. Transcript retention: an age bound (for example 90 days) or until you delete?
6. Bystanders: adopt a household notice, a mute habit and "nothing learned from non-members' speech"? Do you want a WA lawyer's view
   before the pendant is used with visitors?
7. Willing to label about 150 of your own clips in one time-boxed session (kept local)?
8. Confirm the "no" on a brain LoRA and on adapting the panel voiceprint from pendant audio.

## 10. Not researched or not verified

Plaud and Rabbit; NTCIR/LSC/MyLifeBits/personal knowledge graphs; any current text of non-WA surveillance Acts; whether the 12B audio
path runs on the box's llama.cpp build and with what RAM; Jetson support for Unsloth/PEFT; the live value of the day-review mode flag
(the counts show reviews are running); the owner-visible effect of the six live key terms (no A/B was found on disk).

## 11. Sources
- Omi: https://github.com/BasedHardware/omi ; https://docs.omi.me/doc/get_started/introduction ; https://techcrunch.com/2025/01/08/omi-a-competitor-to-friend-wants-to-boost-your-productivity-using-ai-and-a-brain-interface
- Products: https://www.neowin.net/news/meta-acquires-ai-startup-limitless-ending-sales-of-its-popular-pendant-wearable ; https://www.solidaitech.com/2026/08/wearable-ai-notetaker-dead-picks-legal-risks.html ; https://www.tomsguide.com/ai/amazon-buys-wearable-ai-startup-bee-a-wrist-device-that-hears-and-transcribes-every-word-you-speak ; https://www.tomsguide.com/ai/the-humane-ai-pin-is-officially-dead-and-hp-is-picking-up-humanes-leftovers ; https://kvia.com/news/business-technology/cnn-business-consumer/2025/11/16/how-this-tiny-device-became-a-symbol-for-the-backlash-against-ai/ ; https://www.windowscentral.com/software-apps/windows-11/microsoft-addresses-windows-recall-backlash-promises-to-fix-security-issues-and-make-it-opt-in ; https://www.macworld.com/article/2480505/just-say-no-to-metas-glasses.html
- Personalisation and bystanders: https://arxiv.org/html/2409.09510v2 ; https://arxiv.org/abs/2312.05934 ; https://homes.luddy.indiana.edu/kapadia/papers/hoyle-ubicomp14.pdf ; https://cispa.de/en/research/publications/105070-see-me-if-you-can-a-multi-layer-protocol-for-bystander-privacy-with-consent-based-restoration
- Law: https://www.legislation.wa.gov.au/legislation/prod/filestore.nsf/FileURL/mrdoc_45890.htm/$FILE/Surveillance%20Devices%20Act%201998%20-%20%5B02-g0-00%5D.html ; https://www.alrc.gov.au/publication/serious-invasions-of-privacy-in-the-digital-era-alrc-report-123/14-surveillance-devices/participant-monitoring/ ; https://20.austlii.edu.au/au/legis/cth/consol_act/pa1988108/s16.html
- Moonshine: https://huggingface.co/moonshine-ai/moonshine-streaming-medium ; https://arxiv.org/abs/2602.12241
- Wake word: https://github.com/dscripka/openWakeWord ; https://github.com/dscripka/openWakeWord/blob/main/docs/custom_verifier_models.md ; https://huggingface.co/gdiamos/hey-claude
- Gemma 4: https://ai.google.dev/gemma/docs/core/model_card_4 ; https://ai.google.dev/gemma/docs/capabilities/audio ; https://unsloth.ai/docs/models/gemma-4/train ; https://github.com/ggml-org/llama.cpp/pull/21309
- Training: https://arxiv.org/abs/2005.09629 ; https://arxiv.org/abs/1908.02983 ; https://arxiv.org/abs/2307.01850 ; https://www.microsoft.com/en-us/research/?p=166284 ; https://www.isca-archive.org/interspeech_2007/preti07_interspeech.html
- Repo: services/zoe-data/omi_recordings.py, omi_day_review.py, omi_review_digest.py, omi_voices.py, routers/voice_tts.py (keyterms); scripts/night/night_window.py; scripts/perf/zmb/bakeoff.py (`deep_clone_command`); tests/unit/test_night_window.py; docs/knowledge/night-window.md, omi-voice-tagging.md, moonshine-0-1-5-upgrade.md, router-selftrain-loop.md; docs/architecture/struggle-detector.md
