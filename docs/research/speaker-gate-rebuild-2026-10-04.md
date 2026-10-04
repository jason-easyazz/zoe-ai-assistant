---
type: research
title: Speaker gate rebuild on Omi's postmortem (2026-10-04)
date: 2026-10-04
status: research-only — no code, flag, unit or live service changed by this document
description: Deep-research record for P3 of the 2026-10-03 companion-field audit — rebuild Zoe's voice-ID gate on what Omi's speaker-identification postmortem taught (one embedder end-to-end, multi-clip centroids, score normalisation, relative ranking, confirm-to-teach). Field half (models that run on a Pi 5 / Jetson CPU in ONNX, sizes, EER, licences, how OVOS and Home Assistant do it) plus a read-only trace of Zoe's live voice-ID path with file:line, then a flag-dark design, an enrolment plan that covers both known acoustic clusters, a measurement plan on the replay corpus, and a go/no-go against VISION.
---

# Speaker gate rebuild on Omi's postmortem (2026-10-04)

Research date: 2026-10-04. The idea is **P3** in
[companion-field-vs-samantha-2026-10-03.md §2](companion-field-vs-samantha-2026-10-03.md). It
is tracked as **B4.1** (margin rule + sherpa-onnx embedder in shadow) in the
[beat-the-bar tracker](../architecture/beat-the-bar-2026-program.md) and feeds **B4.2**
(speaker-gated follow-up / wake), **B3.9** (owner attribution in consolidation) and **B9.3**
(the ambient speaker gate). Sources are cited inline and listed in §7. **[unverified]** marks a
claim taken from a secondary source, a summary or a number not measured on our hardware.

Hard constraints honoured: the rocks (Gemma 4 E4B+MTP, Moonshine v2 Medium, Kokoro) are
untouched; nothing here runs on the Jetson's hot path; everything ships flag-dark; no live
service was run, restarted or queried; no audio was read; no household data is quoted.

## 0. TL;DR

- **What Omi got wrong is exactly what Zoe has today**, only Zoe has not turned it on yet:
  one embedder of the 2019 generation (resemblyzer GE2E, 256-dim), a single running-mean
  profile that one bad clip poisons forever, a raw best-of-N cosine against a fixed threshold,
  no impostor cohort, no margin, and a threshold that was *found* (0.70) rather than measured
  on labelled turns. Omi's production numbers for that shape: 71 % cross-session owner
  false-reject at their old constant, 19–23 % enrolment completion, two incompatible embedding
  spaces ([#12765](https://github.com/BasedHardware/omi/issues/12765),
  [#18483](https://github.com/BasedHardware/omi/pull/18483)).
- **Omi's measured fix is small and portable.** Their live `speaker_match.py` (read 2026-10-04):
  cosine-distance threshold **0.65** (the old 0.45 "rejected 40–70 % of cross-session owner
  audio at 0.0 % false-accept; 0.65 rejects 14–22 % at <1 % false-accept"), a **0.10 margin**
  over the runner-up voiceprint ("owner-vs-own-person distances were measured as low as
  0.43"), **≥5 s of evidence** before deciding ("two-second clips alone had a 17 % equal-error
  rate in the bench against 10 % at five seconds"), a running centroid over the last 3 clips,
  plus the confirm-to-teach loop (voiceprint = base + last 5 confirmations, 20 h cooldown,
  7-day back-off after 3 ignored sets).
- **The embedder to shadow is CAM++ in ONNX on the Pi** (3D-Speaker, Apache-2.0, 7.2 M params,
  28 MB fp32 / 14 MB fp16, 192-dim, VoxCeleb1-O EER 0.65 % vs resemblyzer's 4.84 % clean /
  **10.49 % degraded** on the CASE benchmark, worst of six); CAM++ has the lowest published CPU
  RTF of the small models (0.013 single-thread x86, ~4× faster than ResNet34) and runs ~100 ms
  per utterance on a Pixel 6 ARM64 CPU — the Pi 5 has 5.65 GB and ~2.7 idle cores to spare,
  the Jetson has none. **No Pi 5 / Orin number exists publicly; we measure first.** WeSpeaker
  ResNet34 (OVOS's default) is the most *robust* small model on CASE but the slowest and
  **CC-BY-4.0**; ERes2NetV2 is the best on 2-s clips (1.48 %) at 68 MB. CAM++'s cosine scale is
  compressed (OVOS: enrolled vs guest ~0.17 / 0.14), so with it the margin + cohort rules are
  not optional extras — they are the gate.
- **Keep what Zoe already has that Omi did not**: consent-gated profiles, device-token-only
  sync, server-side acceptance, shadow-before-acting with `(boot, seq)` rows, a retention
  policy, and a 1,311-WAV replay corpus with a known second acoustic cluster to use as the
  negative control. The rebuild slots a second score column into the existing shadow thread
  (#1760) — no new capture path, no new endpoint, nothing on the Jetson.
- **Verdict: GO, flag-dark, Pi-only, measured** (§6). Four flags, all default off; shadow week
  compares both embedders on identical turns with identical labels; the gate graduates only
  on EER / FA / FR targets with the TV-cluster negative control, and never touches the rocks.

## 1. Field — what the postmortem, the models and the neighbours say

### 1.1 Omi's speaker-identification postmortem (primary sources)

**Issue #12765** ([link](https://github.com/BasedHardware/omi/issues/12765)), what failed:

- **Two incompatible embedding spaces.** Enrolment and live matching used
  `pyannote/wespeaker-voxceleb-resnet34-LM` with raw **cosine 0.45**; post-processing ran a
  *separate* SpeechBrain ECAPA-TDNN `verify_files` path. Scores from one are meaningless
  against profiles from the other.
- **One enrolment clip** — a single concatenated WAV of the onboarding Q&A answers, no quality
  gate (single-speaker check, minimum net speech after VAD).
- **Adoption**: only 19–23 % of users completed the speech-profile step (81 % skipped);
  ~20 % of uploads failed with 400s (a 120 s duration cap).
- **The fix list**: one model end-to-end; "centroid of the six answers" instead of one
  concatenation; server-side quality gates; "AS-Norm / relative ranking" instead of a raw
  cosine cut-off; swap ResNet34 for ERes2NetV2 or CAM++; honest per-stage telemetry
  (skip / upload ok / upload failed / embedding stored / match accept-reject).

**What they then measured and shipped** (`backend/utils/stt/speaker_match.py`, read via the
GitHub API 2026-10-04; constants and comments quoted):

- "Measured offline on real enrollments (2026-09-07): the same user's audio from a different
  session sits at a median distance of **0.40–0.53** from their stored voiceprint, while other
  users sit at **0.93**. The former 0.45 (taken from a clean-studio VoxCeleb figure)
  **rejected 40–70 % of cross-session owner audio at a 0.0 % false-accept rate; 0.65 rejects
  14–22 % at <1 % false-accept** against random users. Same-session audio matches at either
  value, which is why the old constant looked fine in demos." → `SPEAKER_MATCH_THRESHOLD = 0.65`
  (cosine *distance*; similarity 0.35).
- "A match must also beat the runner-up voiceprint by at least this much … owner-vs-own-person
  distances were measured as low as 0.43; the margin keeps 'the nearest is clearly nearest' as
  a second condition." → `SPEAKER_MATCH_MARGIN = 0.10`.
- "Two-second clips alone had a **17 % equal-error rate** in the bench against **10 % at five
  seconds**." → `SPEAKER_MATCH_MIN_EVIDENCE_SECONDS = 5.0`.
- `SPEAKER_MATCH_MAX_CLIPS = 3` recent clips feed the running centroid; `arbitrate_owner_matches`
  compares *all* evidenced voices, including ones just over threshold ("at 0.631/0.645 both
  passed 0.65 in production"), before publishing an owner verdict — added by
  [PR #19470](https://github.com/BasedHardware/omi/pull/19470) (merged 2026-09-27): "distinct
  speakers at distances 0.631 and 0.645 both passed owner verification … close claims become
  unnamed/ambiguous; a clear 0.53/0.73 owner still matches."
- Enrolment itself had to be fixed first: [PR #13805](https://github.com/BasedHardware/omi/pull/13805)
  (merged 2026-09-14) — recordings often fell under "the backend 5 s floor"; fix = record ≥5 s,
  retry uploads 3×, and a failed upload no longer marks enrolment complete. A `gh search code`
  for `eres2net|campplus` in the repo on 2026-10-04 found nothing, so the embedder swap the
  issue proposed has **not** landed; the margin/threshold/teach fixes shipped on the *old*
  ResNet34-LM space [unverified — code search only].

**PR #18483** ([link](https://github.com/BasedHardware/omi/pull/18483), merged 2026-09-24),
confirm-to-teach: against a measured **71 % cross-session owner false-reject**, a daily card
shows **5–10 s single-speaker** clips from the last 48 h ("Is this you?"), extracted from
consecutive segments with no intervening speaker; owner confirmations pool into the voiceprint
as **base + the last 5 confirmations** (normalised mean); **20 h cooldown** per prompt set;
**7-day back-off** after three unanswered sets; 2 h cache of empty scans.

Two lessons the numbers carry that the issue text does not: (a) *same-session* audio hides the
problem — any validation done minutes after enrolment on the same mic will pass at the wrong
threshold; (b) the household case (owner vs people the owner taught) is where the margin, not
the threshold, does the work.

### 1.2 Embedders that run on a Pi 5 / Jetson CPU in ONNX

| Model | Params | ONNX size | Dim | VoxCeleb1-O EER | CPU latency note | Licence | Source |
|---|---|---|---|---|---|---|---|
| **3D-Speaker CAM++** (`3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx`; zh-en "common_advanced" variant) | 7.2 M | 28.2 MB (en-voxceleb), 27.0 MB (zh-en); fp16 14 MB, int8 8.2 MB | 192 | **0.65 %** | Pixel 6 ARM64, 4 threads: **102 ms/utt fp32, 70 ms fp16**, int8 *slower* (287 ms) | Apache-2.0 | [3D-Speaker](https://github.com/modelscope/3D-Speaker), [sherpa-onnx release](https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models), [campplus-zh-en-onnx](https://huggingface.co/Luigi/campplus-zh-en-onnx) |
| 3D-Speaker ERes2Net-base (en-voxceleb) | 6.61 M | 25.3 MB | 192 | 0.84 % | Pixel 6: 256–316 ms/utt (≈2.5× CAM++) | Apache-2.0 | same |
| 3D-Speaker ERes2NetV2 (zh-cn common) | 17.8 M, 12.6 GFLOPs | 68.1 MB | 192 | **0.61 %** (E 0.76, H 1.45); **short trials: 0.98 % @3 s, 1.48 % @2 s** (vs ECAPA 1.95 %, ResNet34 2.12 % @2 s) | no CPU numbers published; heavier than ERes2Net-base | Apache-2.0 | [paper](https://arxiv.org/abs/2406.02167), release |
| **WeSpeaker ResNet34** (`wespeaker_en_voxceleb_resnet34[_LM].onnx`) | 6.34 M | 25.3 MB | 256 | 1.05 % plain; **0.723 % after LM fine-tune + AS-Norm** | OVOS default; speakeronnx lists it as "28M params" [conflicts with 3D-Speaker's 6.34 M — different ResNet34 variants; unverified] | **CC-BY-4.0** ("follows the licence of its dataset", VoxCeleb) | [WeSpeaker](https://github.com/wenet-e2e/wespeaker), [pretrained.md](https://github.com/wenet-e2e/wespeaker/blob/master/docs/pretrained.md), [pyannote wrapper](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM) |
| WeSpeaker CAM++ (`wespeaker_en_voxceleb_CAM++[_LM].onnx`) | ~7 M | 27.9 MB | 512 (per speakeronnx) | ≈0.65 % class [unverified for this export] | as CAM++ | CC-BY-4.0 | same |
| WeSpeaker ECAPA-TDNN (c1024) | 20.8 M | — | 192 | 0.86 % plain; 0.728 % LM + AS-Norm | ~3× CAM++ compute [unverified] | CC-BY-4.0 | same |
| SpeechBrain ECAPA-TDNN (`spkrec-ecapa-voxceleb`) | ~20 M | ~80 MB class [unverified] | 192 | 0.80 % (VoxCeleb1 cleaned) | wyoming-voice-match: **200–500 ms CPU**, ~500 MB RSS; needs torch or a hand-rolled ONNX export | Apache-2.0 | [HF card](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb), [wyoming-voice-match](https://github.com/jxlarrea/wyoming-voice-match) |
| NeMo TitaNet-S / TitaNet-L | 6.4 M / 25.3 M | 38.4 MB / 96.7 MB | 192 | 1.15 % / 0.68 % | **Pi 3B: 1–1.6 s per 3 s of speech** (TitaNet-S and CAM++, [atlas #5](https://github.com/thejoshtaylor/atlas/issues/5)) vs 54–62 ms on a server | TitaNet-L CC-BY-4.0; TitaNet-S "NeMo toolkit licence" (Apache-2.0) per NGC vs cc-by-4.0 per speakeronnx [conflict, unverified] | [paper](https://arxiv.org/abs/2110.04410), sherpa-onnx release |
| ReDimNet-B2 | 1.8 M | — | 192 | — [unverified] | smallest in speakeronnx's list | Apache-2.0 | [speakeronnx](https://pypi.org/project/speakeronnx/) |
| **Resemblyzer** (what Zoe runs) | 3-layer LSTM, size not stated | PyTorch, needs torch | 256 | **not reported** by the authors; GE2E on LibriSpeech 3.85 % ([study](https://arxiv.org/abs/2011.04896)); **CASE benchmark 4.84 % clean → 10.49 % degraded, worst of six** | Pi 5 measured **540 ms median / 1120 ms max** per turn ([ttfa doc](../knowledge/panel-ttfa-breakdown-2026-09-28.md)) | Apache-2.0 | [repo](https://github.com/resemble-ai/Resemblyzer), [CASE](https://github.com/gittb/case-benchmark) |

Robustness under codecs / mics / noise / reverb (CASE benchmark, 24 protocols × 10k trials,
[repo](https://github.com/gittb/case-benchmark)): WeSpeaker ResNet34 0.58 → 3.01 %, SpeechBrain
ECAPA 0.56 → 3.05 %, TitaNet-L 0.66 → 4.05 %, pyannote/embedding 1.68 → 4.47 %, **Resemblyzer
4.84 → 10.49 %**. Degraded conditions are the panel's conditions; the gap between today's model
and the field is ~3.5× there, not 5–7× as the clean numbers suggest — still decisive.
Compute, single-thread x86 RTF from the 3D-Speaker and WeSpeaker-2024 papers: CAM++ **0.013**,
ECAPA-512 0.018, CAM++ (WeSpeaker) 0.023, ECAPA-1024 0.042, ERes2Net 0.053, ResNet34 **0.061**
([3D-Speaker paper](https://arxiv.org/abs/2303.00332),
[WeSpeaker 2024](https://www.fit.vut.cz/research/group/speech/public/publi/2024/wang_speech%20communication_2024.pdf)) —
ResNet34 is the most robust small model and the slowest; CAM++ is the fastest by ~4×.
**No published Raspberry Pi 5 or Jetson Orin NX CPU number exists for any of these**
(searched 2026-10-04); the Pi 3B figure above is the only Pi data point, and it is a Pi 3.
Our own measurement is mandatory before any flag flips (§5). On ARM, **int8 is slower than
fp32** in sherpa-onnx's ORT build (no optimised MatMulInteger kernel) — use fp32 or fp16.

Runtime options on the Pi: **sherpa-onnx** (C++/Python, ships every model above as a single
`.onnx`, `SpeakerEmbeddingExtractor` API; "each model has its own licence"), or
**speakeronnx 0.0.1** (2026-06-13, Apache-2.0, "pure-onnxruntime … no torch at runtime",
deps onnxruntime + numpy + huggingface_hub) which downloads from HF on first use — fine for a
lab, **not** for the panel (no network dependence at boot; pin the file + SHA256 under
`~/.zoe-voice/models/` the way `zoe_face_id.py` does for buffalo_sc). The Pi already has
`onnxruntime>=1.18` for openWakeWord, so the only new weight is the model file.

What the numbers do and do not say: VoxCeleb1-O EER is clean-ish studio-to-YouTube audio with
long utterances; every neighbour that measured in a home reports an order of magnitude worse
on short far-field clips (Omi 10 % at 5 s, 17 % at 2 s; the literature's "short utterance" band
is 1–8 s net speech with EER roughly doubling from ~3.6 s to ~2 s —
[Poddar 2018 review](https://ietresearch.onlinelibrary.wiley.com/doi/10.1049/iet-bmt.2017.0065)).
Model choice buys the clean-condition headroom; **evidence length, enrolment coverage and
score normalisation decide the household number.**

### 1.3 Enrolment and scoring practice

- **Centroids from several separate clips, never one concatenation.** Omi: centroid of six
  answers. OVOS: "5 to 30 s total per person" across multiple clips. Murdock: 3–5 samples of
  5 s each. wyoming-voice-match: **≥30 WAVs × 5 s** "at varied volumes and distances", ideally
  recorded on the satellite's own mic. The common thread: cover the *conditions* (distance,
  loudness, time of day), not just the phrases.
- **Minimum evidence before deciding**: ≥5 s of speech (Omi's bench: 17 % EER at 2 s → 10 % at
  5 s). Below that, abstain rather than guess.
- **Score normalisation.** AS-norm (adaptive S-norm) normalises a trial score by the mean/std
  of its top-N most similar impostor-cohort scores; Matějka et al. 2017 report a **30 %
  relative EER improvement** for PLDA under mismatched conditions, with the adaptive cohort
  self-selecting matching gender 92 % of the time
  ([paper](https://www.fit.vut.cz/research/group/speech/public/publi/2017/matejka_interspeech2017_IS170803.pdf)).
  WeSpeaker's 0.723 % figure is *with* AS-Norm. At household scale the "cohort" is tiny, so
  the practical form is **relative ranking + margin** (best must beat second-best by ≥0.10)
  with a cohort built from the *other* enrolled members plus a fixed set of non-household
  embeddings (the TV cluster in Zoe's corpus is a ready-made impostor cohort).
- **Relative ranking at household scale is a published Amazon practice**, not just Omi's: the
  shared-device paper ([arXiv 2109.02576](https://arxiv.org/abs/2109.02576)) identifies the
  rank-1 speaker and accepts only if its score passes a threshold ("otherwise … a guest"), and
  for self-training keeps only utterances where "the difference between rank-1 and rank-2
  scores is above a second predefined threshold"; households of 2–7 "hard-to-discriminate"
  speakers, 4 enrolment utterances, household-adapted scoring cut EER 49 % on real data.
  VoxWatch ([arXiv 2307.00169](https://arxiv.org/abs/2307.00169)) adds the caveat that AS-norm's
  benefit "is less clear on the stronger models" — so the cohort rule in §3 is an *experiment*
  with its own number, not an assumption.
- **Enrolment count has a measured curve**: GE2E d-vectors on LibriSpeech, EER vs enrolment
  utterances 2 → 3.92 %, 3 → 2.57 %, 4 → 2.41 %, 7 → 2.27 %, 10 → 2.17 %, 15 → 2.01 %; short
  (<4 s) utterances cost ~59 % relative ([study](https://arxiv.org/abs/2011.04896)). Household
  baselines ([arXiv 2205.00288](https://arxiv.org/abs/2205.00288)) report an online centroid
  update `c ← αx + (1−α)c` worth "up to 15 % relative EER reduction with unknown non-targets" —
  the formal version of Omi's "base + last 5". Five to eight clips is where the curve flattens;
  Zoe's three today sit on its steep part.
- **Ageing is slow, mic change is fast.** VoxAging ([arXiv 2505.21445](https://arxiv.org/abs/2505.21445)):
  CAM++ EER 3.72 % → 4.80 % over 0 → 10 years since enrolment — re-enrolment for drift is a
  yearly event at most; a microphone or placement change is the real re-enrolment trigger
  (far-field vs close-talk roughly doubles EER in FFSVC-class data, 4.0 % vs 7.5 % [unverified
  from a search summary of arXiv 2008.03521]).
- **Thresholds are model-specific and do not transfer** (OVOS README says so explicitly: 0.45
  for wespeaker-resnet34, and "the same enrolled-vs-guest pair scored ~0.95 / 0.89 on
  titanet-small, but **~0.17 / 0.14 on campplus**" — CAM++'s cosine scale is compressed, so a
  raw threshold alone is nearly useless for it and normalisation/margin is doing the work;
  Murdock uses distance ≤0.30 for CAM++ with Platt-scaled confidence and adaptive per-speaker
  thresholds "bounded to ±0.08 around the global"; wyoming-voice-match 0.30 similarity for
  ECAPA with "your voice 0.35–0.70, TV/others 0.05–0.25"). Every neighbour re-tuned on its own
  data; Omi's whole failure was a borrowed clean-studio constant.
- **Drift and re-enrolment.** Omi's confirm-to-teach is the only shipped continual-enrolment
  loop in this set (base + last 5 confirmations; the base is never dropped — an immutable
  anchor against poisoning). The panel-identity plan already prescribes the same shape
  ("rolling gallery … original enrolment kept as an immutable anchor", §"Template update is
  the sharp knife"). Murdock keeps an "enrollment postbox" of unknown clips with a TTL for
  later review — the same idea as the shadow week's `truth` column, with audio, which Zoe's
  retention policy forbids (so Zoe keeps the *row*, not the clip).

### 1.4 How the neighbours wire it

- **OVOS `ovos-ww-verifier-plugin-speaker`** ([repo](https://github.com/OpenVoiceOS/ovos-ww-verifier-plugin-speaker)):
  embeds the audio *after the wake word*, matches against `~/.local/share/ovos_speaker_verifier/profiles.json`
  (embeddings only), default model `wespeaker-resnet34` via speakeronnx, `threshold: 0.45`,
  `per_profile_thresholds`, `fail_open: true` when nobody is enrolled; with profiles and
  `fail_open: false`, "a rejected speaker suppresses the wake-word event itself" — unknown
  voices are silently dropped. This is B4.2's shape (speaker-gated wake), done as a verifier
  plugin rather than a custom wake model.
- **Home Assistant / Wyoming**: no speaker identity in Assist core; the Wyoming protocol has
  no speaker event (feature request open since 2023,
  [community thread](https://community.home-assistant.io/t/speaker-recognition-in-voice-assistant/654276);
  architecture discussion [#1223](https://github.com/home-assistant/architecture/discussions/1223)
  proposes "speech processors" in the pipeline). Community fills the gap as **STT proxies**:
  **wyoming-voice-match** (MIT; SpeechBrain ECAPA; ≥30 × 5 s enrolment; similarity 0.30;
  unmatched audio → empty transcript unless `REQUIRE_SPEAKER_MATCH=false`; CPU 200–500 ms,
  ~500 MB) and **Murdock** (MIT, 2026; **CAM++ ONNX, 7 M params, 192-dim, "<20 ms" CPU**;
  centroids in sqlite-vec; distance ≤0.30; unknown voices never reach STT; MQTT discovery;
  tested on HA 2026.9). Both put the gate *in front of STT*, which is exactly where B9.3 puts
  Zoe's ambient gate.
- **Apple / Google**: HomePod recognises up to six users per home; "additional guests can still
  use Siri … to play music, set timers and alarms, or ask … the weather", and guest music plays
  from the primary account without touching its taste profile
  ([Apple](https://support.apple.com/HT204753)). Google: an unrecognised voice's activity "may
  be stored in your Google Account"; Guest Mode deletes recordings and "won't say or show
  personal results" ([Google](https://support.google.com/googlehome/answer/7177221?hl=en)).
  Neither authenticates with voice; both personalise — Zoe's `user_scoped` + PIN split is the
  same shape.

### 1.5 Licences, in one place

Apache-2.0: 3D-Speaker models (CAM++, ERes2Net, ERes2NetV2 — ModelScope `License` field checked
for the en-voxceleb and zh-cn exports), SpeechBrain ECAPA, speakeronnx, sherpa-onnx,
Resemblyzer, ReDimNet, `ovos-voice-embeddings-plugin`. **CC-BY-4.0** (dataset-inherited,
attribution required, commercial use allowed): all WeSpeaker VoxCeleb exports (incl. the
pyannote wrapper, which is not gated) and NeMo TitaNet-L. MIT: Murdock, wyoming-voice-match,
ha-voice-match-speaker. **No licence file detected** on `ovos-ww-verifier-plugin-speaker`
[unverified] — borrow its *pattern*, not its code. pyannote *community-1* diarization is
CC-BY-4.0 but **gated** (accept terms, share contact details) and is **not** needed here. No
model in the table is non-commercial. For Zoe's "borrow the piece" rule the cleanest pick is the **3D-Speaker CAM++
en-voxceleb ONNX** (Apache-2.0, 28.2 MB, trained on VoxCeleb so its EER is the quoted one).

## 2. Our system — the live voice-ID path, read-only (file:line)

Everything below was read from the worktree at `462a6876` (main, 2026-10-04). "Live" values
that come from the Jetson's `.env` (not in git) are marked as such.

### 2.1 The embedder, and where it runs

- **Model:** resemblyzer (GE2E LSTM, 256-dim), on **both** ends, always on CPU.
  - Pi daemon: `scripts/setup/zoe_voice_daemon.py:393-414` — `_get_voice_encoder()` singleton,
    `VoiceEncoder()` from `resemblyzer`; `:1476-1508` `_speaker_id_warmup()` runs one
    `preprocess_wav` + `embed_utterance` on 1 s of low-amplitude noise to pay the ~2.5 s
    librosa/numba JIT before the first real turn (silence would be VAD-trimmed to nothing and
    the embed JIT never runs).
  - Jetson: `services/zoe-data/voice_speaker_id.py:30-48` — same encoder, pinned
    `device="cpu"` so no CUDA context opens in zoe-data (NvMap sits outside every cgroup guard);
    lazily imported (torch ~360 MB RSS, `voice_speaker_id.py:3-10`), pinned by
    `tests/test_speaker_id_lazy_load.py`. The server only embeds for `/api/voice/enroll` and
    the `audio_base64` fallback of `/api/voice/identify`; a normal turn never embeds server-side.
- **Dependencies:** `scripts/setup/pi-requirements.txt` pins `resemblyzer>=0.1.3` with the
  comment "ARM CPU ~20ms" (the measured number is 370–540 ms median, see 2.5); the Pi already
  carries `onnxruntime>=1.18.0` (openWakeWord) and CPU torch (Silero VAD). On the Jetson,
  `services/zoe-data/requirements-py312.txt:76` pins `onnxruntime==1.23.2` and `:106-120`
  documents the two-phase resemblyzer install (CPU torch wheel + `--no-deps`).
- **Scoring:** raw cosine, best-of-N, no normalisation, no margin.
  - Pi: `zoe_voice_daemon.py:1434-1473` `_match_speaker_local()` — iterates the synced
    profile cache, skips rows whose dimension mismatches ("model-version mismatch — skip this
    row"), returns `(best_user, best_score)`; the acceptance decision is explicitly the server's.
  - Server: `routers/voice_tts.py:5718-5723` `_speaker_id_threshold()` reads
    `ZOE_SPEAKER_ID_THRESHOLD` per call, **code default 0.82**; `:5726-5756`
    `_accept_panel_voice_claim()` accepts a claim only from a device-token caller and only when
    `score >= threshold`; `:5756-5776` `_voice_claim_consented()` re-checks `consent_at` in the
    DB on every accepted claim (fails closed). `/api/voice/identify` (`:5892-5983`) does the same
    best-of-N cosine against `consent_at IS NOT NULL` rows.
- **Live threshold:** the operator set `ZOE_SPEAKER_ID_THRESHOLD=0.70` in the Jetson `.env` on
  2026-07-19 "from measured separation" (positive 0.73 vs negative 0.70 on the bad profile; the
  live enrolment then scored the owner at 0.859), recorded in
  [feature-audit-2026-09-25.md](../knowledge/feature-audit-2026-09-25.md) row 18
  (`ZOE_SPEAKER_ID_THRESHOLD=0.70`, `speaker_profiles` = 1 row). Not verifiable from the tree —
  the `.env` is not in git. The Samantha plan §W5 still quotes 0.82. Face: `routers/face_id.py:55`
  `ZOE_FACE_ID_THRESHOLD` default **0.45**; `scripts/setup/zoe_face_id.py:47` `FACE_MIN_PX`
  default 60, live 36 (ops record).

### 2.2 Enrolment — how a profile is built today

- **Server:** `routers/voice_tts.py:5780-5889` `voice_enroll()`. One WAV in → one resemblyzer
  embedding → **one row per user**, and each further sample is folded in as a **running
  weighted mean** (`averaged = (old_emb * n + new_emb) / (n + 1)`, re-normalised,
  `sample_count += 1`, `:5845-5858`). Consent is tri-state (`consent: true` stamps
  `consent_at`, `false` revokes, absent leaves it). The ownership gate is
  `biometric_scope.resolve_enroll_target` (`:5796-5798`; policy in
  `services/zoe-data/biometric_scope.py:1-27`). Raw audio is a temp file unlinked in `finally`.
- **Guided flow:** `scripts/setup/zoe_enroll_flow.py:229-280` `enroll_voice()` — stops the
  `zoe-voice` user unit (the Jabra cannot hold two input streams, `:240-242`), speaks three
  prompts (`VOICE_PHRASES`, `:57-61`), records each with `arecord` at 16 kHz mono for **6 s**
  (`:219-226`, `:252`), POSTs each to `/api/voice/enroll` with `consent: True`, restarts the
  daemon in `finally`. So a fresh enrolment is a 3-clip mean of ~6 s near-field prompted
  speech. The 2026-07-19 live profile was built differently: 12 samples picked from the
  replay corpus by the operator (ops record), which is where the cluster trap below bit.
- **Settings UI:** `services/zoe-ui/dist/touch/settings.html` client-side 16 kHz WAV + consent
  checkbox (PR #1418, contract pinned by `tests/unit/test_settings_enroll_contract.py`).
- **Retention:** [biometric-retention-policy.md](../knowledge/biometric-retention-policy.md) —
  one 256-dim float32 vector per person, no audio, self-service deletion, consent revocable.
  Any model change must keep the "embeddings only, one row, deletable" shape.

### 2.3 The two-acoustic-cluster trap

Recorded in the 2026-07-19 ops notes and
[state-of-zoe-review-2026-09-25.md:325-328](../knowledge/state-of-zoe-review-2026-09-25.md)
(cross-referenced in the tracker as the reason for B4.1): the replay corpus
`~/.zoe-voice-samples` holds **two acoustic clusters** — **A** (~78 %) near-field commands by
the owner at the panel, **B** far-field / ambient / television audio that reached the daemon
through false wakes (the `quarantine-tv-falsewakes-20260719/` directory holds five of them).
A naive "longest / centroid" pick for enrolment mixed B into the profile and produced a
profile where a positive scored **0.73** and a B negative **0.70** — a 0.03 separation, which
is why the threshold ended at 0.70 and why the memory rule says "enrol from cluster A only,
validate with held-out positives AND B negatives". The structural causes map one-to-one onto
Omi's postmortem list: a single running mean (so one bad clip poisons the profile forever —
there is no per-clip storage to recompute from, `:5845-5858`), raw cosine, no impostor cohort,
no margin. **Note the nuance:** cluster B is not "the owner far-field"; it is mostly *other
people's* voices (TV). The owner's own far-field voice (across the room, follow-up windows)
is a third, under-sampled condition the enrolment plan in §4 must cover explicitly.

### 2.4 The shadow-scoring pattern (#1760)

- Flags: `SPEAKER_ID_ENABLED` (Pi env, default **false**, `zoe_voice_daemon.py:245`);
  `SPEAKER_ID_SHADOW` (default **on**, parsed as a safety gate — only an explicit
  `0/false/no/off` lifts it, `:247-261`); `SPEAKER_ID_SHADOW_LOG`
  (`~/.zoe-voice/speaker_shadow_metrics.jsonl`, `:262-265`); `SPEAKER_ID_SYNC_TTL_S` (3600,
  `:1365`). Live: `SPEAKER_ID_ENABLED=true`, shadow on (ops record; feature audit row 18 says
  "P2 shadow week never run").
- Per turn: `_speaker_claim_to_attach()` (`:1863-1876`) — in shadow mode it hands the WAV to
  `_start_shadow_scoring()` (`:1791-1842`) and returns `None` at once, so the POST to
  `/api/voice/turn_stream` no longer waits on resemblyzer (that wait cost a median 0.54 s / max
  1.12 s before #1760, [panel-ttfa-breakdown-2026-09-28.md:117](../knowledge/panel-ttfa-breakdown-2026-09-28.md)).
  Scorers are daemon threads named `speaker-shadow`, **serialised FIFO** (each joins its
  predecessor before touching the encoder: one model copy, one inference in flight, rows in
  turn order); a failed thread start falls back to inline scoring after a bounded wait
  (`_SHADOW_INLINE_WAIT_S = 10`), and `_drain_shadow_scoring()` (`:1845-1860`) waits ≤3 s at
  shutdown. Pinned by `tests/unit/test_voice_daemon_speaker_shadow.py`.
- The row: `_record_speaker_shadow()` (`:1635-1710`) appends
  `{boot, seq, ts, panel_id, user_id, score, n_profiles, source, truth}` — metadata only, never
  audio or embeddings; `source` ∈ `local | server | error | encoder_unavailable`;
  `truth` is null until the operator labels it. `(boot, seq)` is the stable handle.
  **This is the exact hook a second embedder slots into**: score the same WAV with the new
  model on the same thread, write a second score column, and the shadow week compares the two
  models on identical turns with identical labels (§5).
- Outside shadow mode the claim is scored inline before the POST (`:1874-1876`) and attached as
  `voice_user_id` / `voice_score` (`:2126-2127`, `:2352`); `/api/voice/turn_stream` forwards
  both to `/voice/command` (`routers/voice_tts.py:4955-4960`, `:5177-5181`).

### 2.5 Measured cost of the current embedder on the Pi

[panel-ttfa-breakdown-2026-09-28.md:117](../knowledge/panel-ttfa-breakdown-2026-09-28.md):
resemblyzer shadow score **540 ms median (370 ms on brain turns), 1120 ms max**, "grows with
clip length", measured from the `Recorded …` → `Speaker ID (shadow)` journal gap on the live
panel; warm identify ≈ 0.3–0.7 s/turn per the ops record. It is off the TTFA path since #1760
but it still holds one Pi core for that long per turn and serialises every turn's score behind
the previous one. The Pi 5 has **5.65 GB of 8 GB free** and runs at about one third of one
core ([samantha-evolution-plan.md:504-506](../architecture/samantha-evolution-plan.md)); the
Jetson is the RAM-starved side (MemAvailable 0.42–0.56 GB on 2026-10-03,
[memory-pressure-profile-2026-10-03.md](../knowledge/memory-pressure-profile-2026-10-03.md)).
**Any new model goes on the Pi.**

### 2.6 The follow-up window that picks up TV / side talk

- After a reply, the daemon opens a wake-word-free window: `FOLLOW_UP_LISTEN_S` 5.0 s,
  `FOLLOW_UP_MAX_TURNS` 5, `FOLLOW_UP_VAD_THRESHOLD` 0.35 (`zoe_voice_daemon.py:322-326`);
  conversation mode is wider still — `CONV_WINDOW_S` 12 s, `CONV_MAX_TURNS` 40, `CONV_MAX_S`
  300 s, `CONV_SILENT_WINDOWS` 2 (`:333-338`).
- `_follow_up_listen()` (`:2434-2500`) opens a fresh mic stream, drains ~150 ms of chime echo,
  and starts recording on the first Silero chunk with `prob >= 0.35` (with a 4-chunk ≈320 ms
  lookback ring). **There is no identity check here at all**: any speech above a low VAD
  threshold — television, a second person, the owner talking to someone else — becomes a
  turn (`_turn_fn(pa, follow_wav, prompt_on_empty=False)`, `:2650`; conversation loop
  `:2610-2626`). The companion-field audit §2 names Gemini "proactive audio" as the bar and
  "speaker-gating the follow-up window (P3 + B4.2)" as the local approximation.
- The follow-up turn is scored by the same `_speaker_claim_to_attach` path as a wake turn, so
  a gate can be inserted **before the POST** with no new capture code: embed the follow-up
  WAV, and if it is not an enrolled household voice (or is a second voice), drop it and
  return to wake mode. Because scoring is serialised on one thread, a gate on the follow-up
  path would add the embedder's latency *to* the follow-up turn (not to the wake turn) — the
  model's speed (§1) decides whether that is tolerable.

### 2.7 Where the gate decision is consumed

- **Identity precedence** (`routers/voice_tts.py:3032-3041`, `:3195-3236`):
  `identified_user_id` (daemon claim, after the server threshold + consent re-check) →
  `_bound_user` → `_panel_recent_user` → `_panel_default_user` → caller → `guest`. P-F6: a
  guest sentinel never out-ranks a bound panel (`:3209-3230`). A real identified user is also
  **bound to the panel session** for the next turns (`:3112-3120`), so one accepted claim
  carries forward — a false accept persists for the session's 5-minute continuity window.
- **Writes keyed on it:** the user turn is persisted to `chat_messages` under `effective_user`
  (`:3240-3256`); the brain is called with `user_id=effective_user` (`:4445-4447`, `:4594`), so memory
  capture, history and the Samantha context are attributed to whoever the gate said spoke.
  `user_scoped` intents (lists, calendar, reminders, `:3769`, `:3815`, `:3903`, `:3952`) and the
  guest policy (`:4033-4036`) read `_scope_identity_user`; behind `ZOE_VOICE_IDENT=1`
  (`:4211`) a missing identity triggers the PIN challenge rather than a leak.
- **Ambient memory:** `/api/voice/ambient` (`:5640-5710`) stores a transcript under the
  **panel's resolved default user**, never under a voice match ("ownerless ambient audio must
  never be stored", P-F4) — there is no speaker gate on ambient today; the daemon's
  `AMBIENT_CAPTURE_ENABLED` is default false (`zoe_voice_daemon.py:294`). B9.3 / the
  [Omi plan](../architecture/omi-integration-plan.md) §"speaker gate BEFORE STT" (lines
  136-150, 215-226) plan to put *this* gate there: unknown → discard, multi-speaker → discard,
  owner or ambient-consented member → transcribe and attribute.
- **Consolidation:** B3.9 — "a 'Jason' fact is promoted only when the source turn carried a
  voice-ID match" (tracker line 933-935). Not built; it consumes the same decision.
- **Face fusion:** [panel-identity-plan.md](../architecture/panel-identity-plan.md) Phase 3
  z-score fusion with `ZOE_FUSION_STRONG_VOICE=0.90` / `ZOE_FUSION_AGREE_VOICE=0.82` are
  resemblyzer-space constants; they must be re-derived for any new embedder.

### 2.8 Lock-in that already exists (keep green)

`services/zoe-data/tests/test_voice_profile_sync.py` (claim gate server-side, sync is
device-token-only + consented rows only, consent plumbing), `test_speaker_id_lazy_load.py`
(no torch at import), `test_biometric_ownership_scope.py`, `tests/unit/test_voice_daemon_speaker_cache.py`,
`test_voice_daemon_speaker_shadow.py` (one turn = one row, `(boot, seq)` handle, nothing
attached in shadow), `test_enroll_flow.py`, `test_settings_enroll_contract.py`. Any rebuild
must keep every one of these passing unchanged or extend them; none may be loosened.

## 3. Design — flag-dark, Pi-side, one score column at a time

Principle: **change one thing per flag, measure it on the same rows, keep the server the
only place that accepts.** The daemon keeps claiming; the server keeps deciding; nothing new
runs on the Jetson.

### 3.1 Flags (all default OFF; Pi env unless prefixed `ZOE_`)

| Flag | Where | Default | What it does when on |
|---|---|---|---|
| `SPEAKER_ID_SHADOW_MODEL` | Pi `.env.voice` | unset (= off) | Name of a second embedder to score in shadow beside resemblyzer: `campplus` (3D-Speaker en-voxceleb ONNX). Loads the `.onnx` from `SPEAKER_ID_MODEL_DIR` (`~/.zoe-voice/models/`, SHA256-pinned in the installer like buffalo_sc). Adds `score2`, `model2`, `margin2`, `evidence_s` to each shadow row. Never attaches anything. |
| `SPEAKER_ID_MODEL` | Pi `.env.voice` | `resemblyzer` | Which embedder produces the *claim* (`voice_user_id`/`voice_score`). Switching it changes the embedding dimension, so the daemon must refuse to claim until `/profiles/sync` returns profiles of the matching `model_name` (the dimension-mismatch skip at `:1456-1457` already handles stale rows). |
| `SPEAKER_ID_MARGIN` | Pi `.env.voice` | `0` (= no margin rule) | Minimum (best − second-best) cosine similarity for a claim to be sent; below it the daemon sends no claim (abstain). Only meaningful with ≥2 enrolled profiles; with one profile the cohort rule below carries the job. |
| `SPEAKER_ID_MIN_EVIDENCE_S` | Pi `.env.voice` | `0` | Net speech (post-VAD) required before a claim; below it, abstain and log `source="short"`. Omi's bench says 5 s; Zoe's wake turns are ~2–8 s, so start at 2.0 and measure. |
| `SPEAKER_ID_FOLLOWUP_GATE` | Pi `.env.voice` | `false` | In `_follow_up_listen`'s caller: embed the follow-up WAV first; if the claim is below threshold / margin or evidence is short, discard the turn (log, no POST) and return to wake mode. Shadow variant: log what *would* have been dropped without dropping (`SPEAKER_ID_FOLLOWUP_GATE=shadow`). |
| `ZOE_SPEAKER_ID_THRESHOLD_<MODEL>` | Jetson `.env` | unset | Per-model threshold read by `_speaker_id_threshold()` when the claim carries `model_name`; the existing `ZOE_SPEAKER_ID_THRESHOLD` stays the resemblyzer value. A claim with an unknown `model_name` is rejected (fail closed). |
| `ZOE_SPEAKER_ID_COHORT` | Jetson `.env` | `false` | Server-side AS-norm-lite: `/profiles/sync` also ships a fixed impostor cohort (K≈20 embeddings of non-household voices — the TV-cluster files from the corpus, embedded once, never audio) and the daemon reports `z = (s − μ_cohort)/σ_cohort` beside the raw score; the threshold then applies to `z`. Lab-only until the shadow week shows it separates better than raw cosine. |

Wire format additions to the claim (`voice_user_id`, `voice_score` unchanged): `voice_model`
(string), `voice_margin`, `voice_evidence_s`. All optional; an old daemon keeps working.

### 3.2 Placement and budget

- **Pi 5 (all model work).** CAM++ fp32 ONNX: 28 MB on disk, ~60–120 MB RSS in onnxruntime
  [unverified on Pi 5 — measure], ~100 ms per utterance on a comparable ARM64 CPU, so **≈5×
  faster than resemblyzer's measured 540 ms** and with no torch in the path. Two embedders in
  shadow at once cost ~0.65 s serialised per turn on the shadow thread (off the TTFA path since
  #1760) — acceptable for a week, not forever: once the shadow week graduates CAM++, resemblyzer
  is retired from the Pi (torch stays only for Silero VAD). Budget check against the panel's
  5.65 GB free: +≤150 MB RSS, replay-gate PASS, `Recorded → Speaker ID` gap median ≤200 ms.
- **Jetson (storage + policy only, unchanged).** No embedder runs on a live turn today and
  none will. `/api/voice/enroll` is the one place the server embeds; for the new model it
  should instead **accept a daemon-computed embedding** (the identify endpoint already does:
  `embedding_base64`, `:5907-5913`) so torch and ONNX both stay off the Jetson. Storage shape:
  `speaker_profiles` gains `model_name` + `dim` (as `face_profiles` already has) and —the one
  real schema change— **per-clip rows or a JSON array of clip embeddings behind the centroid**,
  so a bad clip can be removed and the centroid recomputed (Omi's "centroid of the answers"
  and the panel plan's "immutable anchor + rolling gallery"). The retention policy keeps its
  "embeddings only, deletable" guarantee; the row count per person changes from 1 to ≤N+1
  (anchor + gallery), which the policy must state.
- **Where the margin and cohort live.** Margin on the Pi (it needs all candidates' scores,
  which only the Pi has). Threshold on the server (unchanged principle: "a panel can claim,
  never decide"). Cohort statistics computed on the server at sync time and shipped with the
  profiles (so the panel never needs the cohort audio, only 20 vectors).

### 3.3 The follow-up gate (B4.2's cheap half)

`_follow_up_listen` returns WAV bytes; today they go straight to `_turn_fn`. With
`SPEAKER_ID_FOLLOWUP_GATE=shadow` the daemon embeds first and logs
`followup_gate: would_drop=<bool> score=… margin=… evidence_s=…`; with `true` it drops. Cost:
the embedder's latency *on the follow-up turn only* (~100 ms with CAM++), inline, because the
decision must precede the POST. Policy: drop only on a *confident* non-match (score below
threshold **and** evidence ≥ `SPEAKER_ID_MIN_EVIDENCE_S`); short or ambiguous audio passes
through, so the gate fails toward today's behaviour. Conversation mode (`CONV_*`, 12 s windows,
up to 40 turns) gets the same hook. This is the local approximation of Gemini's "proactive
audio" that the 2026-10-03 audit named, and the negative control is literal: a TV on for 10
minutes in a follow-up window produces zero turns.

### 3.4 Confirm-to-teach, Zoe-shaped

Omi's card is a phone UI; Zoe's panel is voice-first, touch-second, no keyboard. The
equivalent: after a turn whose claim was *near* the threshold (within ±0.05) and whose evidence
was clean (single speaker — until a detector exists, "VAD speech fraction ≥0.8 and no
barge-in during capture"), the panel shows a one-tap card "Was that you, <name>?" (yes / no /
not me) **at most once per 20 h**, backing off 7 days after 3 ignored, exactly Omi's pacing. A
"yes" appends the turn's embedding (never the audio) to that user's rolling gallery (last 5),
recomputes the centroid, and re-syncs. A "no" adds it to the impostor cohort. Flag:
`ZOE_SPEAKER_ID_CONFIRM_TEACH` (server, default off); the panel card reuses the
`panel_pin_result`-style WS broadcast the panel-identity plan already lists. The Apple pattern
("Okay <name>" before anything personal) is already Zoe's `user_scoped` PIN path; with a
high-confidence voice match it becomes the greeting, with a low one it stays the PIN.

## 4. Enrolment plan — cover both clusters, keep an anchor

The 2026-07-19 enrolment used 12 corpus clips picked by hand and still produced a 0.03
separation, because it mixed cluster B in. The plan below replaces "pick clips" with "cover
conditions", and keeps the corpus for *validation*, never for enrolment.

1. **Fresh guided enrolment on the panel mic** (`zoe_enroll_flow.py voice`, extended): from 3
   prompts × 6 s to **8 prompts** across **three conditions** — 3 near-field at the panel
   (today's distance), 3 at **room distance** (2–3 m, the follow-up/conversation condition),
   2 **quiet / tired voice** (evening). The flow already speaks every instruction from the
   panel (the guided-enrolment rule); add "now step back to the couch". Each clip is embedded
   on the Pi with the enrolled model and POSTed as its own clip; the server stores the 8 clip
   embeddings and the centroid; the first session's centroid is the **immutable anchor**.
2. **Quality gate per clip** (Omi's fix list): ≥3 s net speech after Silero VAD, peak VAD
   prob ≥0.5, no barge-in/second speaker flag, and — the cheap single-voice check —
   the clip's own embedding must sit within a sane cosine of the running centroid once ≥3
   clips exist (an outlier is re-recorded, not averaged in).
3. **Both clusters, explicitly.** Cluster A (near-field) is covered by prompts 1–3; the owner's
   far-field voice by 4–6. Cluster B (TV / other voices) is **never enrolled** — it becomes the
   impostor cohort (§3.1 `ZOE_SPEAKER_ID_COHORT`) and the negative control (§5). Household
   members who consent enrol the same way; their clips are each other's impostors for the
   margin rule, which is where the panel plan says the real threat lives ("relatives share
   voice traits").
4. **Re-enrolment triggers**: a model change (dimension change ⇒ profiles of the old model are
   ignored, never mixed — Omi's two-space bug); a mic change (the planned powered USB hub or a
   different panel); a shadow-week false-reject rate above target; and the confirm-to-teach
   gallery hitting 5 "no" answers for one user. Rolling gallery never evicts the anchor.
5. **Consent and retention unchanged**: `consent: true` per clip, revocation drops all of a
   user's rows from sync, deletion is self-service; the shadow artifact is deleted when the
   week closes (policy already says so).

## 5. Measurement plan — on `~/.zoe-voice-samples`, with negative controls

### 5.1 What the corpus is (metadata only; no audio was read)

- **1,311 top-level WAVs** named `HHMMSS_mmm.wav` (`_maybe_capture_stt`,
  `routers/voice_tts.py:2672-2673`), the capture time carried by **mtime** (verified by
  `replay_samples.py::_select`'s docstring: 998/1003 files' mtime HH:MM:SS equals the name);
  spans 2026-06 → 2026-09 (oldest/newest mtimes 2026-07-19 00:04 and 2026-08-18 23:58 in the
  two files checked; the 2026-09-28 panel captures `182*.wav` are noted in the gate facts).
  Readers glob the top level only; subdirectories are safe quarantine
  (`scripts/maintenance/curate_voice_corpus.py` header).
- **Quarantine subdirectories** with manifests: `quarantine-format-20260804/` (5 unreadable),
  `quarantine-nonspeech-20260804/` (50, `manifest.json` rows carry `peak`, `frac_speech_hops`,
  `duration_s`, `rms`, `mtime_iso` — i.e. **per-file VAD statistics already exist**),
  `quarantine-replay-dups-20260727/` (62 byte-identical replays), and
  **`quarantine-tv-falsewakes-20260719/` (5 files)** — the only *labelled* non-owner audio.
- **There are no speaker labels.** No manifest, filename or sidecar says who spoke. The ops
  record says ~78 % is cluster A (owner near-field) and the rest B (far-field / TV); that split
  was made by *transcribing* and recognising content, not by a label file. Any EER computed on
  the corpus therefore needs a labelling pass first (§5.2); the shadow week's `truth` column is
  the other labelled source.

### 5.2 Building the evaluation set (operator + tooling, read-only on audio)

1. A new maintenance tool (sibling of `curate_voice_corpus.py`, same non-recursive glob, same
   `flock`) embeds every top-level WAV with **both** models on the Jetson CPU *during an
   operator window* (resemblyzer needs torch, ~360 MB; CAM++ ONNX ~100 MB; neither touches
   the GPU; run with the brain stopped per the dev-box rule, or on the Pi) and writes
   `speaker_eval_embeddings.npz` + a `labels.csv` skeleton (`file, cluster_guess, truth`).
   Embeddings only; no audio leaves the corpus directory.
2. **Cluster guess**: 2-means on the CAM++ embeddings (expected to recover A/B — the memory
   note says the clusters are acoustic, so they should be separable in a VoxCeleb-trained
   space far better than in GE2E space); the five TV false-wakes seed B. The operator
   confirms a sample of ~50 per cluster by transcript (the existing replay harness prints
   Moonshine text), and labels `truth ∈ {owner, other, unknown}`. Rows left `unknown` are
   excluded, exactly as the shadow-week protocol excludes unlabelled rows.
3. **Held-out protocol**: enrolment is *never* drawn from the corpus (§4). Trials: every
   labelled corpus file vs the enrolled centroid(s) → one score per model per file. Report,
   per model and per cluster:
   - **EER** (owner vs other) with the DET curve, and the score at EER;
   - **FA@FR=5 %** and **FR@FA=1 %** (the two operating points that matter: Omi chose <1 % FA);
   - **separation** = median(owner) − 95th percentile(other) — the number that was 0.03 on
     2026-07-19;
   - the same with the margin rule and with cohort z-scores, to show each adds separation.
4. **Negative controls (must-haves, the "verify your instruments" rule):**
   - **TV control**: the 5 `quarantine-tv-falsewakes` files + all cluster-B files must score
     *below* the chosen threshold → FA on B = 0 at the operating point, or the gate is not
     graduating. Record the max B score.
   - **Broken-model control**: run the eval once with a deliberately wrong profile (another
     user's or a random unit vector) — EER must go to ≈50 %; if it does not, the harness is
     scoring something other than identity.
   - **Same-session trap**: hold out the enrolment *session* entirely; an eval that only uses
     clips recorded minutes after enrolment on the same mic is Omi's demo trap and is not
     accepted as evidence.
   - **Length control**: bucket trials by net-speech duration (<2 s, 2–5 s, >5 s) and report
     EER per bucket; the `SPEAKER_ID_MIN_EVIDENCE_S` default is chosen from this table.
5. **Shadow week (live, flag-dark)**: `SPEAKER_ID_SHADOW_MODEL=campplus` for ≥7 days; the
   operator labels `truth` on the `(boot, seq)` rows as today. Both models' scores sit on the
   same rows, so FA/FR for each is computed on identical turns. Graduation
   (`SPEAKER_ID_MODEL=campplus`, resemblyzer retired from the Pi) requires **all** of: FR ≤
   10 % and FA ≤ 1 % on labelled rows for ≥2 enrolled speakers or the owner alone, zero FA on
   the TV control, no replay-gate regression (said-vs-did and per-stage speed), Pi RSS delta
   ≤150 MB, `Recorded → Speaker ID` median ≤200 ms. Then `SPEAKER_ID_FOLLOWUP_GATE=shadow`
   for a second week with its own would-drop review, and the live threshold per model written
   into `.env` from the measured DET, not borrowed.

### 5.3 Targets (stated before measuring)

| Metric | Today (resemblyzer, 2026-07-19 ops) | Target to graduate | Omi's measured bar |
|---|---|---|---|
| Separation (median owner − p95 other) | ≈0.03 (0.73 vs 0.70) | ≥0.20 | owner 0.40–0.53 vs others 0.93 (distance) |
| FR (owner rejected), cross-session | unknown — shadow week never run | ≤10 % | 14–22 % at <1 % FA |
| FA (other accepted as owner) | unknown | ≤1 % | <1 % |
| FA on TV cluster | ≥1 known (the 0.70 negative) | 0 of N | — |
| EER by length bucket | unknown | <2 s abstain; 2–5 s ≤10 %; >5 s ≤5 % | 17 % @2 s, 10 % @5 s |
| Per-turn cost on the Pi | 540 ms median / 1120 max | ≤200 ms median | — |

If CAM++ fails these on Zoe's room, the next candidates in order are ERes2NetV2 (0.61 %, 68 MB,
~2.5× slower) and WeSpeaker ResNet34-LM (CC-BY-4.0); not ECAPA on torch (RAM, torch) and not
a bigger model on the Jetson (the W3 gate).

## 6. Go / no-go against the VISION principles

| Principle | Verdict | Why |
|---|---|---|
| 1. Rocks are fixed | ✅ | Gemma, Moonshine, Kokoro untouched; the embedder is not a rock and the router contract is untouched. |
| 2. Local, private, fast | ✅ | On-Pi ONNX, no network at runtime (pinned model file), embeddings only, faster than today's path (≈100 ms vs 540 ms); the Jetson gains zero resident bytes. |
| 3. Lab-prove before prod; ship behind flags, default off | ✅ | Seven flags, all off; corpus eval with negative controls, then a shadow week on identical rows; thresholds derived from DET curves, never borrowed (Omi's root cause). |
| 4. Build it to STICK | ✅ with work | Existing tests stay green; add: per-model threshold tests, margin/abstain tests, dimension-mismatch refusal, follow-up gate fail-open, shadow row schema v2, replay gate on the daemon change, and the corpus eval tool pinned by a fixture test with the broken-model control. |
| 5. Capture, don't lose | ✅ | B4.1/B4.2/B3.9/B9.3 all point here; this record is the executable plan, and the tracker rows should link it. |
| 6. Borrow the piece, not the framework | ✅ | Pieces: Omi's four constants and card pacing, OVOS's drop-unknown-after-wake, Murdock's gate-before-STT. Not adopted: speakeronnx's HF download at runtime, pyannote, sherpa-onnx's diarization, any proxy daemon. |
| 7. Right tool, right place | ✅ | Pi does model work (2.7 idle cores, 5.65 GB free); Jetson keeps policy; the corpus and the shadow artifact are the instruments; the Serena/codebase-memory tools were not used for this record under the RAM rule, by instruction. |
| 8. Voice first, touch second, no keyboard | ✅ | Enrolment is spoken from the panel; confirm-to-teach is a one-tap card with Omi's pacing; no typing anywhere. |
| 9. Understand before you change | ✅ | This document is that step: the live path is traced to file:line, the 0.03-separation failure is explained structurally, and the measurement precedes the flip. |

**GO — as a measured experiment, in this order:** (1) corpus eval tool + labelling pass +
negative controls (no live change); (2) `SPEAKER_ID_SHADOW_MODEL=campplus` shadow week on the
panel (daemon change ⇒ replay gate, Pi RSS check); (3) per-clip enrolment storage + fresh
three-condition enrolment + per-model server threshold; (4) margin + min-evidence on the claim;
(5) follow-up gate in shadow, then on; (6) confirm-to-teach. Each step has its own flag and its
own numbers; none moves until the previous one's numbers are written down.

**No-go conditions:** the Pi cannot run CAM++ ONNX under 300 ms/utt or within 150 MB (then
try fp16, then ERes2Net-base, then stop); the corpus labelling cannot reach ≥200 owner + ≥50
other trials (then the shadow week is the only evidence and the bar stays at "shadow");
household members cannot be separated by ≥0.10 margin (then the PIN stays for them on
`user_scoped` scopes — the panel plan already says "don't pretend").

**Operator decisions this record needs:** (a) agree the targets in §5.3 before any run;
(b) a window to embed the corpus on the Jetson CPU with the brain stopped (dev-box rule), or
run it on the Pi; (c) consent to the fresh three-condition enrolment and to adding the TV
cluster as an impostor cohort (embeddings of non-household voices — the retention policy
should say whether that is acceptable, since those people never consented; the conservative
alternative is a synthetic cohort from a public corpus such as VoxCeleb test clips).

## 7. Sources

- Omi speaker-ID postmortem — https://github.com/BasedHardware/omi/issues/12765 ; confirm-to-teach
  PR #18483 (merged 2026-09-24) — https://github.com/BasedHardware/omi/pull/18483 ; live matching
  constants `backend/utils/stt/speaker_match.py` (read via the GitHub contents API 2026-10-04) —
  https://github.com/BasedHardware/omi/blob/main/backend/utils/stt/speaker_match.py
- 3D-Speaker (CAM++, ERes2Net, ERes2NetV2; Apache-2.0; VoxCeleb1-O EER table) —
  https://github.com/modelscope/3D-Speaker ; toolkit paper — https://arxiv.org/abs/2403.19971
- sherpa-onnx speaker-recognition model release (asset sizes read via the GitHub releases API) —
  https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models ; Python example —
  https://github.com/k2-fsa/sherpa-onnx/blob/master/python-api-examples/speaker-identification.py
- CAM++ zh-en ONNX fp32/fp16/int8 + Pixel 6 ARM64 benchmark — https://huggingface.co/Luigi/campplus-zh-en-onnx
- WeSpeaker (Apache-2.0 toolkit; models CC-BY-4.0; 0.723 %/0.728 % with LM + AS-Norm) —
  https://github.com/wenet-e2e/wespeaker ; pretrained list — https://github.com/wenet-e2e/wespeaker/blob/master/docs/pretrained.md ;
  pyannote wrapper — https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM
- SpeechBrain ECAPA-TDNN (0.80 % EER, Apache-2.0) — https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- NeMo TitaNet (6.4 M / 25.3 M params; 1.15 % / 0.68 % EER) — https://arxiv.org/abs/2110.04410
- speakeronnx 0.0.1 (pure onnxruntime; model list with dims/licences) — https://pypi.org/project/speakeronnx/
- Resemblyzer (GE2E, 256-dim, Apache-2.0, no EER reported) — https://github.com/resemble-ai/Resemblyzer
- OVOS speaker verifier wake plugin — https://github.com/OpenVoiceOS/ovos-ww-verifier-plugin-speaker
- Home Assistant: speaker recognition feature request — https://community.home-assistant.io/t/speaker-recognition-in-voice-assistant/654276 ;
  architecture discussion "speech processors" — https://github.com/home-assistant/architecture/discussions/1223 ;
  wyoming-voice-match (ECAPA, MIT) — https://github.com/jxlarrea/wyoming-voice-match ;
  Murdock (CAM++ ONNX proxy, MIT) — https://github.com/BobMcGlobus/Murdock
- Omi follow-ups: enrolment floor fix PR #13805 — https://github.com/BasedHardware/omi/pull/13805 ;
  owner-match arbitration PR #19470 — https://github.com/BasedHardware/omi/pull/19470
- CASE robustness benchmark (codecs/mics/noise/reverb, six embedders) — https://github.com/gittb/case-benchmark
- CAM++ paper (RTF table) — https://arxiv.org/abs/2303.00332 ; ERes2NetV2 paper (short-trial EER) —
  https://arxiv.org/abs/2406.02167 ; WeSpeaker 2024 (RTF, LM + AS-Norm results) —
  https://www.fit.vut.cz/research/group/speech/public/publi/2024/wang_speech%20communication_2024.pdf
- Household / shared-device speaker ID (rank-1 + margin, Amazon) — https://arxiv.org/abs/2109.02576 ;
  household baselines + online centroid update — https://arxiv.org/abs/2205.00288 ;
  VoxWatch (open-set scale, AS-norm caveat) — https://arxiv.org/abs/2307.00169 ;
  GE2E enrolment-count curve — https://arxiv.org/abs/2011.04896 ; VoxAging (drift) —
  https://arxiv.org/abs/2505.21445 ; Pi 3B latency data point — https://github.com/thejoshtaylor/atlas/issues/5
- OVOS voice-embeddings plugin — https://pypi.org/project/ovos-voice-embeddings-plugin ;
  ha-voice-match-speaker — https://github.com/dcshoes23/ha-voice-match-speaker ;
  HA discussion "Voice / Speaker Recognition" — https://github.com/orgs/home-assistant/discussions/527
- Apple HomePod voice recognition + guests — https://support.apple.com/HT204753 ; Google Voice
  Match / Guest Mode — https://support.google.com/googlehome/answer/7177221?hl=en
- Score normalisation: Matějka et al., "Analysis of Score Normalization in Multilingual Speaker
  Recognition", Interspeech 2017 — https://www.fit.vut.cz/research/group/speech/public/publi/2017/matejka_interspeech2017_IS170803.pdf
- Short utterances: Poddar et al., IET Biometrics 2018 review — https://ietresearch.onlinelibrary.wiley.com/doi/10.1049/iet-bmt.2017.0065
- Apple HomePod "Recognize My Voice" and Google Voice Match guest behaviour — as cited in
  [companion-field-vs-samantha-2026-10-03.md](companion-field-vs-samantha-2026-10-03.md) §1
- In-repo: [panel-identity-plan.md](../architecture/panel-identity-plan.md),
  [biometric-retention-policy.md](../knowledge/biometric-retention-policy.md),
  [panel-ttfa-breakdown-2026-09-28.md](../knowledge/panel-ttfa-breakdown-2026-09-28.md),
  [state-of-zoe-review-2026-09-25.md](../knowledge/state-of-zoe-review-2026-09-25.md) §5.4/§7,
  [feature-audit-2026-09-25.md](../knowledge/feature-audit-2026-09-25.md) row 18,
  [omi-integration-plan.md](../architecture/omi-integration-plan.md),
  [beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md) B3.9/B4.1/B4.2/B9.3,
  [samantha-evolution-plan.md](../architecture/samantha-evolution-plan.md) §W5/§6a.
