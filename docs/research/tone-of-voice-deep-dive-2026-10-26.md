---
type: research
title: Tone of voice - how assistants hear frustration, tiredness, urgency and sarcasm, and what Zoe should do (2026-10-26)
date: 2026-10-26
status: research-only - no code, flag, service or model changed; nothing installed or run (one read-only check of the live llama.cpp build)
description: What commercial, open-source and academic systems actually do to read tone from the voice; every realistic option for a local, private, RAM-tight Jetson (hand-built prosody features, small speech-emotion models, audio-native LLMs including Gemma 4, text-side cues, fusion); evidence on natural speech, RAM, latency, licence, maintenance, privacy and plug-in point for each; risks; a ranked recommendation with a phased, gated, corpus-measured plan.
---

# Tone of voice - what others do and what Zoe should do (2026-10-26)

Plain-language research record. It builds on, and does not repeat, the licence audit in
[arousal-detection-licence-scope-2026-10-04.md](arousal-detection-licence-scope-2026-10-04.md)
(per-model weight licences, the scope call, the Pi shadow-scorer slot) and the audio-native note in
[inference-speech-stack-2026-10-03.md](inference-speech-stack-2026-10-03.md) section 2.5. Read those for licence
detail; this record adds the wider field, the audio-native-LLM evidence, the cheap options, and one ranked plan.

Labels: **[verified]** read in a primary source or on this box today; **[secondary]** press or a third-party
listing; **[extrapolated]** my estimate from a different model or chip; **[unverified]** not checked.
The rocks (Gemma 4 E4B-QAT+MTP, Moonshine v2 Medium, Kokoro) are untouched by every option below.
The repo is public: no utterances, names, audio content or per-person data appear here, only aggregate counts.

## 0. Executive summary

1. **Nobody has solved this on natural speech, and the honest numbers are poor.** Acted-emotion benchmarks
   report 90-99% accuracy; on real conversational speech (TV dialogue, podcasts) the same model families
   score roughly 28-35%. The gap, not the headline, is the story.
2. **The shipped commercial features are narrow.** Alexa's documented use is frustration read from tone
   when a user says it got something wrong, started with music requests, answered with an apology and a
   clarifying question [secondary]. Hume has the broadest prosody product, but it is cloud, its standalone
   measurement API shut down on 2026-06-14, and its own science page says labels are proxies, not feelings.
   I found no documented tone detection in Siri or Gemini Live, and nothing in Home Assistant, OVOS or Willow
   (absence of search results, not proof of absence).
3. **Audio-aware LLMs mostly do not listen.** The LISTEN benchmark (six models incl. Qwen3-Omni and Gemini 2.5 Pro)
   found they follow the words, default to "neutral" when words are neutral, and drop to 16-23% on prosody-only
   clips (10% is chance) [verified, abstract + tables]. Gemma 4's own audio docs list transcription, translation and
   "general understanding", and make no emotion claim [verified].
4. **What does work is narrow, relative and boring:** loudness, pitch, speaking rate and pauses compared with
   *that speaker's own* baseline, used to corroborate something else (a repeated request, "no, that's not what I
   said"). That is also what the 2002 SRI result found: prosody beat language-model features for annoyance and
   frustration, at roughly human-labeller agreement, and best when labellers agreed [secondary].
5. **Recommendation:** do not buy a model yet. Phase 0 is free (text cues plus timing/level features from the
   audio we already save) and is measured on the real-voice corpus with aggregate numbers only. Phase 1 is a lab
   bake-off of one licence-clean small SER model against Phase 0. Audio-native Gemma is a later, flagged-turns-only
   second opinion. Anything inferred is hint-only, short-lived, never overrides what was said, never stored for
   kids or guests. Details in section 6.

## 1. What others do

| Who | What is documented | Evidence quality | Lesson for Zoe |
|---|---|---|---|
| **Amazon Alexa** | Chief scientist said in 2017 the team started on frustration; later reports: tone analysed when a user says Alexa got a command wrong, rolled out first for music requests, "No, Alexa" leads to an apology and a clarifying question. Far-field audio and the need for per-user baselines were named as hard. ([VentureBeat 2017](https://venturebeat.com/2017/12/22/amazons-alexa-wants-to-learn-more-about-your-feelings), [MIT Tech Review](https://www.technologyreview.com/s/601654/amazon-working-on-making-alexa-recognize-your-emotions/)) | Press only, 2016-2019 [secondary]; no primary Amazon paper found; Alexa+ status unconfirmed | The one proven pattern is exactly Zoe's own: tone only as a *tie-breaker after an explicit correction*. |
| **Hume AI** | Speech-prosody model: 48 expression dimensions from tune, rhythm, timbre; EVI 3 (May 2025) is a speech-language model said to adapt its voice to the user's. Expression Measurement API sunset: last use 2026-06-14. Science page: labels are "proxies for how people tend to label", not inferences of experience. ([docs](https://dev.hume.ai/docs/expression-measurement/models/prosody), [science](https://dev.hume.ai/docs/resources/science), [EVI 3](https://hume.ai/blog/introducing-evi-3)) | Vendor claims; blind tests vs GPT-4o are Hume's own | Cloud audio: out. The caveat from their own science page is the right framing for any label Zoe shows. |
| **OpenAI GPT-4o voice, Google Gemini Live, Apple Siri** | Native-audio models take audio directly; none publishes a tone-detection feature I could verify. Gemini Live coverage is about conversational feel, not emotion sensing ([TechCrunch](https://techcrunch.com/2024/08/13/gemini-live-first-look-better-than-talking-to-siri-but-worse-than-id-like)). OpenAI was not separately researched. | [unverified] | Do not assume the big assistants read tone; the visible "empathy" is mostly text and TTS style. |
| **Sesame CSM, Kyutai Moshi** | Full-duplex/speech-generation models (Moshi ~160-200 ms, datacentre-GPU sized; CSM-1B Apache-2.0). They *generate* expressive speech and model turn-taking; neither is an off-the-shelf tone *classifier*. ([Moshi](https://arxiv.org/abs/2410.00037), [CSM](https://huggingface.co/sesame/csm-1b)) | Primary | Interesting for a future voice, irrelevant to detection. |
| **Home Assistant, Rhasspy, OVOS, Willow** | Searched for emotion/tone/sentiment features and community requests; found none. | [unverified] absence | Open home stacks have not done this; no maintained project to "adopt" at the assistant level. |
| **Academic SER** | Standard corpora: IEMOCAP, MSP-Podcast (Odyssey 2024 challenge: 8-class categorical plus arousal/valence/dominance on 2,347 podcast segments from 187 speakers), MELD (TV). EmoBox benchmark unweighted accuracy: IEMOCAP 69-74%, CREMA-D 75-77%, MELD 28-32%, acted EmoV-DB ~99% (WavLM-large / Whisper-large-v3) ([EmoBox](https://arxiv.org/html/2406.07162v1), [Odyssey 2024 team paper](https://arxiv.org/abs/2406.10598)). A cross-corpus study found transfer between acted and natural corpora weak ([Milner et al.](https://arxiv.org/abs/2207.02104)). | Primary | Plan for 30-40% class accuracy on natural speech, and for arousal (energetic vs calm) being far easier than valence (good vs bad). |

Two more findings that shape everything below:

- **Sarcasm.** On the scripted-TV MUStARD++ set, audio-only models reach F1 about 75 (Qwen-Audio 75.1 vs text-only
  66.4; fusion 76.8); about 24% of clips need visual cues no audio system can have ([comparison](https://arxiv.org/abs/2509.15476),
  [ProSarc](https://arxiv.org/abs/2606.06168)). Those are performed sarcasm on TV, not a tired person in a kitchen.
  A diagnostic of Qwen Omni models found they over-call sarcasm from a stereotype of high pitch plus irregular
  pauses; editing only those cues raised false positives by up to 60% [secondary; check the paper ID before citing].
  In LISTEN's mismatched condition, sarcasm collapsed into "ridicule" and "frustration".
- **Tiredness.** Evidence is lab-grade: the 2011 Interspeech sleepiness challenge (99 speakers, ~21 h) and a
  20-person sleep-deprivation study at 88% ([Krajewski 2007](https://www.isca-archive.org/interspeech_2007/krajewski07_interspeech.html),
  [challenge overview in Pir and Krajewski 2018](https://www.scitepress.org/Papers/2018/67798/)). Controlled, repeated
  per-person recordings; no evidence for a one-off clip in a noisy room.

## 2. Zoe's side (verified in the repo and on the box today)

- **Struggle detector** (`services/zoe-data/struggle_signals.py`, `struggle_detector.py`): deterministic, text-only,
  English-only pair rules (re-ask, rephrase, correction, mishear, abandon). Built for precision over recall. It never
  looks at audio. This is the natural place to *corroborate* with a tone feature.
- **Affect memory**: gated by `ZOE_AFFECT_CONSENT_GATE` in `memory_authority.py`; policy is the normative
  [emotional-safety note](../governance/emotional-safety-note.md). Rule from the owner: inferred < confirmed <
  user-stated; an inference never supersedes what was said.
- **Prior decision (Q18, 2026-10-04):** research licence and scope, build nothing. Scope call: the *act* (apologise,
  clarify) may apply to anyone; any *record* only for consenting adults; children never; no identity-based logic until
  the speaker gate is live. The W4.1 SER bake-off stays blocked on the W3 RAM-reclamation gate
  ([ROADMAP](../ROADMAP.md)).
- **Audio exists**: `ZOE_VOICE_SAVE_AUDIO` (default off in code, `routers/voice_tts.py`) saves each utterance for the
  operator's corpus. `~/.zoe-voice-samples` holds 1,522 WAV clips plus quarantine folders (TV false-wakes, replay
  duplicates, non-speech) that must be excluded from any scoring. There are **no emotion labels** on it.
- **Live brain, checked today read-only:** `llama-server` build b11194 (Jetson build) links `libmtmd` (the multimodal
  library), exposes `--mmproj`, and the source tree has `tools/mtmd/models/gemma4a.cpp` (the Gemma 4 audio encoder)
  [verified]. But the live command line passes **no `--mmproj`**, and the only mmproj files on disk are for the 12B
  models; there is **no E4B mmproj** [verified]. So the brain cannot hear audio today.
  llama.cpp's Gemma 4 audio PR (#21421): up to 30 s per clip, 16 kHz, BF16 projector recommended, no streaming, no
  speed numbers published, E4B passed 19/21 short and 20/21 long tests across quantisations
  ([PR](https://github.com/ggml-org/llama.cpp/pull/21421)). Google: 25 audio tokens per second of audio
  ([Gemma audio docs](https://ai.google.dev/gemma/docs/capabilities/audio)); a 10 s turn is about 250 extra prefill tokens.
  Whether MTP speculative decoding co-operates with the multimodal path is **[unverified]**, and the earlier stack audit
  notes the Orin needs `-DGGML_CUDA_NO_VMM=ON` for multimodal.

## 3. The options, researched one by one

### A. Hand-built prosody features with a per-speaker baseline

- **What:** F0 (pitch) level and range, RMS energy, speaking rate, pause count/length, jitter/shimmer, spectral tilt,
  computed on the saved clip, then expressed as a **delta from that speaker's rolling median** (the "baseline" Alexa's
  team said it needed). eGeMAPS (88 features) is the standard, comparable set ([Eyben et al. 2016](https://doi.org/10.1109/taffc.2015.2457417)).
- **Tools:** librosa (ISC, `pyin` for F0; pure Python/numpy), Praat via parselmouth (Praat is GPL; check before
  bundling [unverified]), openSMILE (the open-source build is non-commercial per the prior audit, so lab-only), or just
  numpy over the VAD segments the pipeline already has.
- **Can detect:** arousal and urgency (louder, faster, higher), hesitation (pause and filler timing), sudden change
  from a person's norm. **Weak:** tiredness (slow, low-range speech needs a longitudinal baseline, and only lab evidence
  exists). **Cannot:** valence (angry vs excited are acoustically close), sarcasm, anything cultural.
- **Natural-speech evidence:** the best-supported old result (Ang et al. 2002, annoyance/frustration in real
  human-computer dialogue, [paper](https://people.cs.pitt.edu/~litman/courses/cs3710/AngEtAl.pdf)); arousal is the dimension
  all modern models also get right, so a features baseline is not a strawman.
- **Cost:** RAM tens of MB; CPU a few ms to tens of ms for a 3-10 s clip [extrapolated], off the critical path.
  Loudness caveat: the panel mic's gain/AGC and distance change energy, so level features need per-device
  normalisation, not just per-speaker.
- **Maintenance:** libraries are mature; the logic is ours, but it is arithmetic plus thresholds, not a rebuild of a
  product ("adopt, don't rebuild" is satisfied by using the libraries).
- **Privacy:** features are numbers, not audio; can be kept in RAM and discarded.
- **Plug-in:** a boolean "elevated vs this speaker's baseline" as an extra reason code in the struggle detector, and
  a one-line prompt hint on the *next* turn only.

### B. Small speech-emotion-recognition (SER) models

Licence and size detail is in the 2026-10-04 record; summary and updates:

| Model | Output | Size | Licence (home use OK? live-path OK?) | Natural-speech evidence | Jetson note |
|---|---|---|---|---|---|
| **emotion2vec+ base** (~90M) / **large** (~300M) | 9 categories (angry, disgusted, fearful, happy, neutral, sad, surprised, other, unknown) + embeddings | base: ~373 MB fp32 ONNX per prior audit | GitHub badge says MIT; the prior audit read the weights under the FunASR model licence (commercial OK with attribution). **Re-read the model card before adopting.** | No numbers on the repo page; a third-party listing shows 69-80% UA on one small acted set [secondary]. | Needs FunASR runtime; ONNX/int8 path plausible [unverified]; actively maintained |
| **SenseVoiceSmall** | transcript plus 7 emotion tags | 226 MB int8 | same family licence | not independently measured here | Duplicates Moonshine's job; only the tags are wanted |
| **Odyssey-2024 WavLM-large baseline** (3loi) | arousal/valence/dominance | 1.27 GB | MIT | Trained on MSP-Podcast (natural) | Judge/teacher only: too big to stay resident |
| **audEERING dimensional wav2vec2/Dawn** | arousal/valence/dominance | ~0.6-1.3 GB | CC-BY-NC-SA, "research purpose only" | Best-known natural-speech dimensional model | **No** for the repo's live path (licence rule); lab judge only |
| **LAION Empathic-Insight-Voice** | many heads on a Whisper-Small fine-tune | ~1 GB + heads | CC-BY-4.0 | New, little independent evidence | Judge/teacher material |
| **Whisper-encoder classifiers** | categorical | encoder-sized | MIT/Apache | EmoBox: Whisper-large-v3 best on most corpora | Zoe's STT is Moonshine, so this means loading a *second* encoder: RAM we do not have |
| **Wav2Small (distilled 72K model)** | arousal | ~100 KB in the paper | no released weights; a recipe whose teacher is partly NC | arousal CCC 0.66 (student) vs 0.76 (teacher) per the paper | Would have to be re-distilled from licence-clean teachers |
| **SpeechBrain wav2vec2 IEMOCAP** | 4 classes | ~380 MB [unverified] | Apache-2.0 [unverified] | IEMOCAP only, acted-style | Not recommended: trained on the least natural data |

- **Can detect:** arousal reasonably; coarse categories on clean, expressive speech. **Cannot:** sarcasm,
  tiredness (not a label in any of them), reliably tell "frustrated" from "emphatic".
- **Newer (2025-2026):** searched for a distilled, permissively licensed dimensional model and found none confirmed;
  lightweight papers exist (about 23 MB quantised) but none with released weights and a clear licence that I could
  verify [unverified]. This matches the 2026-10-04 finding: the resident-sized, arousal-outputting, licence-clean model
  has to be *made*.
- **RAM/latency on Orin:** base-class models are 0.1-0.4 GB resident and tens to a few hundred ms per clip
  [extrapolated, not measured]. Run in a worker on CPU or a small GPU slice, off path.
- **Privacy:** categorical emotion labels about a person are sensitive records; keep them in RAM only (section 5).

### C. Audio-native LLMs that hear the audio directly

- **Gemma 4 E4B (the rock) / Gemma 3n:** yes, E4B takes audio (30 s cap, 25 tokens/s) and llama.cpp supports it
  (non-streaming). Needs an E4B mmproj (BF16 recommended; ~300M conformer encoder, so roughly 0.6 GB resident
  [extrapolated]) loaded into the live brain, i.e. a restart and a replay gate. Whether it can *judge tone* is **not
  established**: Google documents ASR, translation and general understanding only; I found no published emotion or
  paralinguistic score for Gemma 3n or Gemma 4 in my searches. The LISTEN result for comparable models says assume
  words-first, neutral-by-default until measured on our own clips. Pro: no extra model, no second server. Con: RAM for
  the projector, longer prefill on every audio-carrying turn, and the audio goes through the *brain's* context, so
  use flagged-turns-only.
- **Qwen2.5-Omni-7B / Qwen3-Omni-30B-A3B, Kimi-Audio, Step-Audio, SALMONN-style:** better measured on emotion than
  Gemma (Qwen2.5-Omni 0.49 on MELD audio in AHELM; Qwen3-Omni 61% vs 55% overall on E3mo-Bench [secondary]) but with the
  same LISTEN weaknesses, and 7B-30B weights do not fit beside the three rocks in 15.6 GB. Kimi-Audio, Step-Audio and
  SALMONN were not researched in depth here [unverified]. Out for resident use; at most a lab *judge* on another
  machine with consented clips.
- **Can detect:** best at content-plus-tone together (a sarcastic phrase whose words contradict the delivery) if it
  works at all. **Cannot (today):** prosody-only cues. **False-positive risk is the highest of any option.**
- **Privacy:** local, so fine; but raw voice would sit in a prompt cache.
- **Plug-in:** a later "second opinion" call on turns the cheap detectors flagged, never every turn.

### D. Text-side cues only

- **What:** sentiment/intent from the transcript, plus disfluency and hedging cues (um, uh, "I guess", trailing
  off), repeated requests, bluntness, swearing, "never mind", "forget it".
- **Reality check:** this is what the struggle detector already does best (re-ask, correction, abandon). Moonshine
  v2's treatment of fillers is **unverified**; ASR systems often drop them by design, and a 2025 study found filler
  presence did not change WER much ([Interspeech 2025](https://www.isca-archive.org/interspeech_2025/wepner25_interspeech.html)).
  If Moonshine drops hesitations, that signal moves to timing (option A: pause length from VAD).
- **Can detect:** explicit frustration, repetition, urgency words ("now", "hurry"), some tiredness words. **Cannot:**
  sarcasm without context, any non-verbal affect. English-only in the current lexicons.
- **Cost:** zero RAM, microseconds; no new privacy surface. **Verdict:** keep and extend; it sets the precision bar
  every audio method must beat.

### E. Hybrid / fusion with confidence gating

- Evidence says fusion helps most for sarcasm and valence (text gives valence, audio gives arousal;
  [OmniVox](https://arxiv.org/abs/2503.21480) found context helps; text+audio topped MUStARD++).
- Design that fits Zoe: **a gate, not a classifier.** Tone only changes behaviour when two independent things agree,
  e.g. (elevated-vs-baseline level/rate) AND (a text struggle signal in the last two turns). Either alone does nothing
  except be counted in aggregate. Cheap, explainable, and it bounds false apologies by construction.
- Optional later layers: a small SER arousal score as a third vote; an audio-native judge as a fourth, only on the
  flagged turns.

## 4. Comparison

| | A. Prosody features | B. Small SER model | C. Audio-native LLM | D. Text cues | E. Fusion gate |
|---|---|---|---|---|---|
| Frustration | Arousal only; corroborating | Partial (angry class, arousal) | Words-first, unproven | Strong when explicit | Best precision |
| Urgency | Good | Arousal proxy | Unproven | Good (words) | Good |
| Tiredness | Weak, needs baseline over time | Not a label | Unproven | Words only | Weak |
| Hesitation | Good (pauses) | No | Unproven | Depends on Moonshine fillers | Good |
| Sarcasm | No | No | Best case, high false-positive risk | Only via mismatch | Partial, risky |
| Natural-speech accuracy | Human-agreement-level for frustration (2002, [secondary]) | 28-35% on conversational corpora | Prosody-only near chance | n/a (already deployed) | Depends on parts |
| RAM | ~0 | 0.1-0.4 GB | ~0.6 GB projector + restart [extrapolated] | 0 | sum of parts |
| Latency | ms, off path | tens-hundreds of ms, off path [extrapolated] | ~250 prefill tokens per 10 s + encoder, on the brain | ~0 | off path |
| Licence | clean (check parselmouth, openSMILE) | mixed; see B | Apache-2.0 (Gemma) | n/a | n/a |
| Maintenance | mature libs, our thresholds | emotion2vec active; others vary | llama.cpp active | ours | ours |
| Privacy | numbers only | labels about a person | raw audio in brain context | none new | as parts |
| Fits Zoe | Yes | Maybe (Phase 1) | Not now | Yes (extend) | Yes (target) |

## 5. Risks

- **Misreading children and accents.** Bias evidence for SER is thin and mixed (gender direction differs between 2019
  and 2025 studies; age and accent bias is documented for ASR, and I found no SER study that measures children or
  non-native speakers) ([CO-VADA](https://arxiv.org/abs/2506.06071)). Child voices (higher pitch, wider range) break
  adult baselines. Treat as unmeasured, and exclude children from any record.
- **Cultural and personal variance.** Loud, fast, emphatic talk is a normal register in some households and
  languages; a flat speaker may be furious. Only a per-speaker baseline mitigates this, and baselines need speaker
  identity, which is still in shadow mode. Before identity is live, only within-session deltas are defensible.
- **Acoustic confounds in a home.** TV in the background, far-field echo, a noisy kitchen, mic gain and a sore throat
  all move the same features as emotion. The corpus quarantine folders (TV false-wakes) are exactly this.
- **False frustration causes false apologies.** An unnecessary "sorry you're upset" is itself irritating and presumptuous.
  The Alexa pattern avoids it by acting only after an explicit correction. Budget: apology-trigger rate is a hard gate, with a reviewed false-apology share.
- **Sarcasm over-calling.** Models infer sarcasm from stereotyped pitch/pauses; do not ship a sarcasm label at all.
- **Creep factor and record-keeping.** An emotional measurement with a timestamp becomes linkable data the moment it
  is stored. Follow the existing scope call: act in the moment, record nothing per clip, aggregates only; any stored
  inference is `inferred`, expires in hours, can be wiped by "forget", and is never written for kids or guests.
  Regulators treat emotion recognition as high-risk and ban it in workplaces and schools
  ([FPF summary](https://fpf.org/blog/red-lines-under-eu-ai-act-unpacking-the-prohibition-of-emotion-recognition-in-the-workplace-and-education-institutions/));
  a private home is outside those bans, but the direction of travel is clear. Not legal advice.
- **Fairness and honesty to the user.** Never state "you sound angry". At most, quietly make the next reply shorter,
  calmer and more direct, and say nothing about the detection. Hume's own caveat applies: these are proxies.

## 6. Recommendation (ranked) and phased plan

**Rank 1 - E built from D + A (Phase 0): a gated, explainable detector.** Extend text cues, add per-speaker-relative
level/rate/pause features computed off-path from the saved clip, and require agreement of both before anything changes.
Free, no new RAM, no model licence, no latency on the voice path.
**Rank 2 - B, one model, in the lab (Phase 1):** emotion2vec+ (base, int8) as an added vote, only if Phase 0 leaves a gap
that a model demonstrably closes. **Rank 3 - C, Gemma 4 E4B audio as a flagged-turn second opinion (Phase 2),** after the
RAM gate and only if lab numbers beat chance on our clips. **Not recommended:** cloud APIs (Hume and others), NC-licence
models on the live path, any Omni-class 7B+ model resident, any sarcasm label, any per-clip emotion record.

### Phase 0 - build and measure the free detector (lab first, flag-dark)
1. Write the feature extractor (librosa/numpy; no new dependency if numpy suffices) as a pure function over a saved
   clip, run on a worker thread after the turn, not on the critical path.
2. Add text cues (hedges, urgency words, negation of the previous result); first **measure whether Moonshine keeps
   fillers** by counting filler tokens per 100 clips (aggregate).
3. Output: a reason code only ("elevated_vs_baseline"), consumed by the struggle detector and, at most, a one-line
   prompt hint for the next reply. No memory write.

### Measurement against `~/.zoe-voice-samples` (aggregate numbers only)
- Exclude quarantine folders. No transcripts, no audio and no per-clip rows leave the box or enter a repo doc.
- **Label a small set on the box:** the owner labels roughly 150 clips (frustrated / neutral / urgent / tired / unsure)
  in a time-boxed, owner-claimed session; labels stay local. Report only counts and rates. Add about 30 negative
  controls (calm clips with a loud TV, whispered clips) and a shuffle control (permuted baselines must give chance).
- **Weak-label shortcut for scale:** use existing struggle events as a proxy label and report only aggregate AUC and
  flag rate per 100 turns.
- **Report:** per-feature distributions; precision/recall of the gated flag; agreement (kappa) between detectors;
  p50/p95 compute time; RSS delta; fraction of clips where the baseline is undefined (cold start).

### Gates (proposed; fixed before any run)
1. Voice path untouched: replay-gate said-vs-did and per-stage speed unchanged (`voice_regression_probe.py`, under
   `flock /tmp/zoe-voice-harness.lock`); the new work is off-path.
2. Precision of the gated "frustrated" flag at least 0.8 on the labelled set; **apology-trigger rate no more than 1 per
   100 turns** on the unlabelled corpus (it counts how often the apology fires; the false-apology share cannot be measured without
   labels). The owner reviews a sample of the triggered replies (for example 30, or all if fewer) and the share judged unwarranted must
   be at most 10%; an explicit correction cue lowers the trigger rate but does not guarantee a correct trigger.
3. Model options only: RAM delta at most 300 MB resident and none during a voice turn; licence clean for the live path;
   beats Phase 0 by a margin set in advance (e.g. +0.10 F1) or it is dropped (ties go to the simpler system).
4. Governance: emotional-safety note updated in the same PR if anything reads or keeps affect; inferred-only,
   expiring, no kids, no guests, nothing by identity until the speaker gate is live.
5. Night-window and landing rules apply (no probe in the 12B window, no voice landing while awake).

### Phase 1 - lab bake-off (after the W3 RAM gate)
Run emotion2vec+ base int8 against Phase 0 on the same labelled set, plus one dimensional judge (MIT Odyssey WavLM,
lab only) to cross-check arousal. Adopt only on the gate above. No installs on the box until then.

### Phase 2 - audio-native Gemma, lab only
Stage an E4B mmproj on a spare path and a second llama-server on a lab port when RAM allows, and measure: (a) does MTP
work with the multimodal path, (b) projector RSS, (c) added time-to-first-token per 10 s clip, (d) accuracy on the
labelled set against Phase 0 including the neutral-default failure mode. A negative result is a valid, cheap result.

## 7. Open questions for the owner
1. Is a reason code that quietly shortens and calms the next reply acceptable, with Zoe never saying she detected a mood?
2. Willing to label about 150 of your own clips in a time-boxed session (kept local)?
3. Should the act apply to anyone at the panel, as the 2026-10-04 scope call proposed, or owner-only until the speaker gate is live?
4. Is tiredness worth chasing at all, given the evidence is lab-only and needs weeks of per-person baseline?
5. May the Phase 1 model download happen only when the RAM gate clears?

## 8. Sources
- Alexa: https://venturebeat.com/2017/12/22/amazons-alexa-wants-to-learn-more-about-your-feelings ; https://www.technologyreview.com/s/601654/amazon-working-on-making-alexa-recognize-your-emotions/
- Hume: https://dev.hume.ai/docs/expression-measurement/models/prosody ; https://dev.hume.ai/docs/resources/science ; https://hume.ai/blog/introducing-evi-3
- Gemini Live: https://techcrunch.com/2024/08/13/gemini-live-first-look-better-than-talking-to-siri-but-worse-than-id-like
- Moshi / Sesame: https://arxiv.org/abs/2410.00037 ; https://huggingface.co/sesame/csm-1b
- SER benchmarks: https://arxiv.org/html/2406.07162v1 (EmoBox) ; https://arxiv.org/abs/2207.02104 (cross-corpus) ; https://arxiv.org/abs/2406.10598 (Odyssey 2024 team) ; https://lab-msp.com/MSP/MSP-Podcast.html
- emotion2vec: https://github.com/ddlBoJack/emotion2vec ; https://arxiv.org/abs/2312.15185
- LISTEN: https://arxiv.org/abs/2510.10444 ; sarcasm: https://arxiv.org/abs/2509.15476 ; https://arxiv.org/abs/2606.06168 ; AHELM https://arxiv.org/abs/2508.21376 ; OmniVox https://arxiv.org/abs/2503.21480
- Gemma audio: https://ai.google.dev/gemma/docs/capabilities/audio ; llama.cpp PR https://github.com/ggml-org/llama.cpp/pull/21421
- Prosody: Eyben 2016 https://doi.org/10.1109/taffc.2015.2457417 ; Ang 2002 https://people.cs.pitt.edu/~litman/courses/cs3710/AngEtAl.pdf
- Sleepiness: https://www.isca-archive.org/interspeech_2007/krajewski07_interspeech.html ; https://www.scitepress.org/Papers/2018/67798/
- Fillers: https://www.isca-archive.org/interspeech_2025/wepner25_interspeech.html
- Fairness: https://arxiv.org/abs/2506.06071 ; EU AI Act: https://fpf.org/blog/red-lines-under-eu-ai-act-unpacking-the-prohibition-of-emotion-recognition-in-the-workplace-and-education-institutions/
