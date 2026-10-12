---
type: research
title: "Speaker-embedding bake-off (2026-10-12): Resemblyzer vs SpeechBrain ECAPA vs WeSpeaker vs TitaNet-small for Zoe's voice ID"
date: 2026-10-12
status: measured on the Jetson CPU and the panel Pi 5. No live path, flag, service, unit or profile changed. Aggregates only - no clip names, transcripts, audio or embeddings (docs/knowledge/biometric-retention-policy.md). Owner-corpus speaker labels are INFERRED (section 2.2); the labelled-public axis is the one to trust for ranking.
description: Six speaker-embedding models scored on the same trials (3-clip enrolment, far-field probes, short-clip split) against a labelled public set (LibriSpeech test-clean + Kokoro TTS voices) and the owner's real-voice corpus, with CPU cost on the Jetson and the Pi. Recommendation - ADOPT WeSpeaker (CAM++ for the panel, ResNet34 as the accuracy challenger) behind a flag-dark dual-run; do not keep Resemblyzer. Also corrects the 2026-10-05 CAM++ result, which used a 3D-Speaker export that does not separate speakers on this stack.
evidence_labels: "[measured] this run, scripts/perf/speaker_bakeoff.py | [record] copied from a cited repo record | [src] file:line read in this worktree | [fetched] model-card / registry metadata read today | [derived] arithmetic on measured numbers | [unverified] not confirmed"
---

# Speaker-embedding bake-off (2026-10-12)

Question (owner): adopt a maintained speaker model, or keep Resemblyzer (GE2E d-vector, 256-d, last release
2023-10-12)? Standing rules applied: adopt-don't-rebuild (a tie goes to the maintained project), RAM is the hard gate
on this 16 GB Jetson, the voice brain must never be starved, the panel Pi computes embeddings on its own CPU.

## 0. Answer first

**ADOPT the WeSpeaker family; do not keep Resemblyzer.** Behind a flag-dark dual-run (section 6), not a flip.

- On the **labelled public set** (certain labels, far-field, 3-clip enrolment) Resemblyzer's EER is **21.9 %** (FRR 53 % at
  FAR 1 %); WeSpeaker ResNet34 / CAM++, SpeechBrain ECAPA and TitaNet-small are **3.3 - 4.8 %** (FRR 6 - 12 % at FAR 1 %).
  The gap is 5x and survives clean probes (17.4 % vs 1.7 - 2.0 %), centring the scores (12.7 % vs 2.2 - 3.4 %) and the
  short-clip split (33 % vs 4 - 7 % for clips of 2 s or less) **[measured]**.
- On the **owner's real corpus** the ranking is the same and ResNet34 leads (EER 1.3 % vs Resemblyzer 4.7 % on the
  consensus core; 3.1 % vs 11.6 % on the lenient core), but those labels are inferred, so treat it as supporting evidence.
- **Pick for the panel: WeSpeaker CAM++ (`wespeaker_en_voxceleb_CAM++.onnx`, 29 MB, 512-d, Apache-2.0).** Pi 5 median
  **133 ms** per 3.65 s clip vs Resemblyzer **243 ms**, and ~230 MB peak process RSS vs ~730 MB. **Accuracy challenger:
  WeSpeaker ResNet34** (best on every owner-corpus row; public EER 3.3 % vs CAM++ 3.7 %, i.e. no real gap there) at
  **369 ms** Pi median - 2.8x CAM++ - so it is the shadow-week comparison, not the first deployment.
- **Two traps found on the way, both must be fenced in code before anything ships (section 4):**
  1. WeSpeaker CAM++ returns **wrong embeddings under onnxruntime's default graph optimisation on aarch64** (cosine 0.03
     against `ORT_DISABLE_ALL`, reproduced on the Jetson's ORT 1.23.2 and the Pi's 1.24.4). It must be loaded with
     `ORT_DISABLE_ALL` and guarded by a self-test.
  2. WeSpeaker ResNet34 is **256-d, the same width as Resemblyzer**: the daemon's shape check
     (`ref.shape != embedding.shape` skip, `zoe_voice_daemon.py:2119`) would NOT stop it scoring against old profiles.
     Profiles need an explicit model id.
- **Correction to the 2026-10-05 record:** its CAM++ candidate was the 3D-Speaker export (sha256 `357a834f...129b`, the same
  file measured here as `campp_3dspeaker`). On this stack it **does not separate labelled speakers** (public EER 31.7 %,
  owner-corpus 35.5 %), which fits its "accepts near-silence / TV clips" findings. Those results were not evidence about
  CAM++ in general. Cause undetermined (ORT aarch64 kernel behaviour or the export); do not reuse that file.
- What this run does **not** prove: the thresholds (they must be re-derived on household-labelled shadow-week data), and
  that the owner-corpus numbers are error rates on the household - the corpus has no speaker labels.

## 1. Candidates

| id | model | dim | licence (code / weights) | maintenance [fetched] | runtime used here |
|---|---|---|---|---|---|
| `resemblyzer` | GE2E 3-layer LSTM, the incumbent (run exactly as `voice_speaker_id.py` / the daemon: `preprocess_wav` + `embed_utterance`, CPU) | 256 | Apache-2.0 / Apache-2.0 (repo) | **last push and last PyPI release 2023-10-12** | torch CPU wheel |
| `ecapa` | SpeechBrain `spkrec-ecapa-voxceleb` | 192 | Apache-2.0 / Apache-2.0 (HF card; VoxCeleb-trained) | speechbrain 1.1.1, 2026-08-27 | torch CPU wheel |
| `wespeaker_resnet34` | WeSpeaker VoxCeleb ResNet34 (sherpa-onnx export) | 256 | Apache-2.0 / Apache-2.0 (HF card) | wespeaker pushed 2026-09-22; sherpa-onnx pushed daily | onnxruntime, `ORT_ENABLE_ALL` |
| `wespeaker_campp` | WeSpeaker VoxCeleb CAM++ (sherpa-onnx export) | 512 | Apache-2.0 / Apache-2.0 (HF card) | same | onnxruntime, **`ORT_DISABLE_ALL` required** |
| `campp_3dspeaker` | 3D-Speaker CAM++ en-voxceleb (the 2026-10-05 candidate) | 512 | Apache-2.0 | 3D-Speaker pushed 2025-12-08 | onnxruntime, `ORT_ENABLE_ALL` |
| `titanet_small` | NVIDIA NeMo TitaNet-small (sherpa-onnx export) | 192 | NeMo Apache-2.0; weights: the large card says CC-BY-4.0, **the small card was not read [unverified]** | sherpa-onnx daily | sherpa-onnx |

Licence notes: nothing here carries a non-commercial restriction on the code or on the published weights **[fetched]**. All
the models are trained on VoxCeleb (+ other corpora for TitaNet); VoxCeleb's own data terms are research-oriented, which is an
upstream caveat for any public repo that redistributes weights - this repo does not (weights are fetched at run time and never
committed). I read licences from HF cards and the GitHub licence field, not the legal text. TitaNet needs attribution if used.
ONNX weights were downloaded from the sherpa-onnx `speaker-recongition-models` release; sha256 values match its published
`checksum.txt` **[measured]**.

## 2. Method

### 2.1 Trials (mimic the panel)

- **Enrolment:** 3 clips (each at least 2 s) from ONE capture session, averaged after L2 normalisation (the profile is a
  centroid). 30 random enrolment draws, **identical for every model**. Score = cosine to the centroid.
- **Genuine probes:** every other core clip from a *different* session (a clip is never scored against its own session).
- **Impostor probes (756 per enrolment, all labelled):** 476 LibriSpeech test-clean crops (all 40 speakers, 12 crops each:
  4 each at 1 - 2 s, 2 - 4 s, 4 - 8 s) + 280 Kokoro-82M TTS clips (28 English voices x 10 phrases, generated locally on CPU
  with kokoro-onnx). Both are scored as **synthetic far-field** (random reverb tail RT60 0.25 - 0.6 s, 120 - 6500 Hz band
  limit, pink-ish noise at 8 - 22 dB SNR, level about -26 dBFS rms to match the corpus median) and, for the LibriSpeech part,
  also **clean** (column "clean imp." below). The far-field channel is an approximation of the panel, not a measurement of it.
- **Labelled public axis (the trustworthy one):** the same 40 LibriSpeech speakers enrol 3 of 4 far-field clips (4 hold-out
  combinations) and are probed by their own 12 crops (genuine) vs the other 39 speakers' crops (impostor), per length bucket.
- **Metrics:** EER; FRR at FAR 1 % and 0.1 % (threshold = the impostor-score quantile, accept when score > threshold);
  score distributions; per length bucket (matched impostor length); bootstrap 95 % interval over probe clips; label-shuffle
  control (EER must be about 50 % - it is, 49.6 - 50.3 % for every model).
- **Scoring conventions:** raw cosine (what Zoe runs today) is the headline. A second pass subtracts a public-cohort mean
  (the far-field LibriSpeech enrolment clips, never used as probes) before cosine - WeSpeaker's documented practice - and is
  reported where it changes the picture.

### 2.2 The owner's corpus and the label problem

`~/.zoe-voice-samples`, read in place and never copied: **1,377 top-level clips** (quarantine folders excluded), 218 capture
sessions (mtime gaps over 30 min), duration p5 / p50 / p95 = 1.6 / 3.1 / 8.1 s, 241 clips of 2 s or less. There are no speaker
labels. As in the 2026-10-05 record, a 2-means cut finds two acoustic clusters and the cut is model-dependent. So the
**owner core** is defined as the clips in the larger cluster for **all four** models that pass the labelled public check
(ECAPA, ResNet34, WeSpeaker CAM++, TitaNet-small; the incumbent does not vote - that would favour or penalise it - and neither
does the broken 3D-Speaker file): **1,068 clips (78 %), "strict"**; the lenient "majority" core (3 of 4) is 1,278 (93 %).
Consequences, stated plainly:

- The strict core is selected for being separable, so owner-corpus errors are **optimistic for every model**; the lenient core
  is the pessimistic bound. The owner-corpus rows are for relative ranking and for the short-clip / channel questions.
- The synthetic impostors can differ from the real panel channel in ways the owner clips do not (a channel shortcut). The
  clean-impostor column and the labelled public axis (where both sides share the channel) bound that.
- 4 clips fail to embed under Resemblyzer (VAD trims them to nothing) and are excluded from every model's trials.

## 3. Results (Jetson CPU; embeddings from `CUDA_VISIBLE_DEVICES=""`, 2 threads, `nice 10`)

### 3.1 Labelled public speakers, far-field enrolment and probes - raw cosine

| model | EER | FRR @ FAR 1 % | FRR @ FAR 0.1 % | clips of 2 s or less: EER | clips of 2 s or less: FRR @ FAR 1 % | clean probes: EER |
|---|---|---|---|---|---|---|
| resemblyzer | 21.9 % | 53.2 % | 76.4 % | 33.1 % | 72.5 % | 17.4 % |
| ecapa | 3.5 % | 6.9 % | 21.7 % | 5.3 % | 12.7 % | 1.7 % |
| **wespeaker_resnet34** | **3.3 %** | **6.4 %** | **19.7 %** | **4.1 %** | **8.9 %** | 1.9 % |
| **wespeaker_campp** | 3.7 % | 7.9 % | 22.7 % | 4.5 % | 11.2 % | 1.8 % |
| campp_3dspeaker | 31.7 % | 91.0 % | 99.3 % | 34.7 % | 73.4 % | 28.5 % |
| titanet_small | 4.8 % | 11.8 % | 28.6 % | 7.0 % | 19.7 % | 2.0 % |

2 - 4 s / over 4 s EER: resemblyzer 10.3 / 5.4 %, ecapa 2.6 / 0.6 %, resnet34 2.5 / 0.5 %, CAM++ 2.7 / 0.8 %, titanet 3.6 / 0.5 %.
With cohort-mean centring: resemblyzer 12.7 % (FRR 37.6 %), ecapa 2.8 %, resnet34 2.2 %, CAM++ 2.4 %, titanet 3.4 %,
3D-Speaker 29.8 %. Resemblyzer improves with centring but stays 4x behind. The FAR 0.1 % column rests on 74,880 impostor trials
that reuse only 476 distinct probe clips - coarse; read it as an order of magnitude.

### 3.2 Owner corpus, strict core, 30 enrolment draws x 756 impostor probes - raw cosine

| model | EER [95 % CI] | FRR @ FAR 1 % [95 % CI] | FRR @ FAR 0.1 % | 2 s or less: EER / FRR@1 % | clean imp.: EER | lenient core: EER |
|---|---|---|---|---|---|---|
| resemblyzer | 4.7 % [4.2 - 5.2] | 11.4 % [9.6 - 13.5] | 25.8 % | 5.9 % / 14.4 % | 5.6 % | 11.6 % |
| ecapa | 3.8 % [3.3 - 4.2] | 8.8 % [7.3 - 10.7] | 21.3 % | 6.5 % / 20.0 % | 5.0 % | 10.9 % |
| **wespeaker_resnet34** | **1.3 %** [1.1 - 1.5] | **1.6 %** [1.2 - 2.2] | **6.4 %** | **1.4 % / 1.9 %** | **1.3 %** | **3.1 %** |
| wespeaker_campp | 2.6 % [2.3 - 3.0] | 5.6 % [4.5 - 7.3] | 16.2 % | 4.3 % / 13.1 % | 3.4 % | 10.2 % |
| campp_3dspeaker | 35.5 % [33.4 - 37.6] | 94.4 % | 98.0 % | 61.5 % / 98.9 % | 35.9 % | 36.4 % |
| titanet_small | 3.0 % [2.6 - 3.4] | 5.8 % [4.8 - 6.7] | 12.2 % | 5.9 % / 12.8 % | 1.1 % | 7.6 % |

The intervals resample probe clips only (the 30 enrolment draws stay paired), and the 95 % CIs do not cover the label
uncertainty of section 2.2 - the strict-vs-lenient swing (Resemblyzer 4.7 -> 11.6 %) is far larger than any interval.
With centring the owner-core EERs become resemblyzer 2.6 %, ecapa 2.6 %, resnet34 0.6 %, CAM++ 1.3 %, titanet 1.1 %.
**Short clips (2 s or less)** are where it hurts today: every model's EER roughly doubles to triples going from clips over 4 s to
clips of 2 s or less (ResNet34 0.7 -> 1.4 %, CAM++ 1.6 -> 4.3 %, ECAPA 3.0 -> 6.5 %, TitaNet 1.6 -> 5.9 %, Resemblyzer 4.0 -> 5.9 %),
and ResNet34 stays lowest in absolute terms.

An unlabelled fact worth recording: of the 22 % of corpus clips outside the strict core, ResNet34 accepts 47 %, TitaNet 27 %,
Resemblyzer 17 %, CAM++ 11 %, ECAPA 7 % at its FAR 1 % threshold. Whether those are the owner in another acoustic state or
other people is exactly the unknown; this is the open question for the shadow week.

### 3.3 Score distributions (owner corpus, strict core, raw cosine)

| model | genuine p5 / p50 / p95 | impostor p50 / p99 | threshold @ FAR 1 % | threshold @ FAR 0.1 % |
|---|---|---|---|---|
| resemblyzer | 0.597 / 0.725 / 0.827 | 0.490 / 0.634 | 0.634 | 0.677 |
| ecapa | 0.201 / 0.407 / 0.588 | 0.039 / 0.240 | 0.240 | 0.310 |
| wespeaker_resnet34 | 0.369 / 0.555 / 0.708 | 0.076 / 0.313 | 0.313 | 0.383 |
| wespeaker_campp | 0.289 / 0.492 / 0.663 | 0.057 / 0.295 | 0.295 | 0.371 |
| titanet_small | 0.261 / 0.481 / 0.672 | 0.062 / 0.271 | 0.271 | 0.327 |

Resemblyzer's impostor floor sits at 0.49 (median) so its usable band is narrow. **Its production server threshold
(`ZOE_SPEAKER_ID_THRESHOLD`, default 0.82, `routers/voice_tts.py:6058` [src]) rejects 93 % of genuine probes in this
3-clip-centroid setup** (0 % false accepts): genuine p95 is 0.827. The 0.82 is not transferable to any other model; the
FAR-1 % thresholds above are indicative only and must be re-derived on household-labelled shadow data.

### 3.4 Cost

Latency is per clip, including features, mean clip 3.65 s, 60 public far-field clips (so the same audio on both boxes), 2 threads.

| model | weights MB | dim | **Jetson** p50 / p95 ms [pass 1] | Jetson ms per audio-s | Jetson peak RSS MB | **Pi 5** p50 / p95 ms [median of 3 passes] | Pi ms per audio-s | Pi peak RSS MB |
|---|---|---|---|---|---|---|---|---|
| resemblyzer | 17.1 | 256 | 191 / 493 | 43 | 466 | 243 / 554 | 47 | 729 |
| ecapa | 89.0 | 192 | 213 / 485 | 52 | 598 | 415 / 1,098 | 140 | 909 |
| wespeaker_resnet34 | 26.5 | 256 | 264 / 724 | 96 | 242 | 369 / 1,002 | 134 | 233 |
| wespeaker_campp | 29.3 | 512 | 124 / 260 | 30 | 233 | **133 / 350** | 47 | 228 |
| campp_3dspeaker | 29.6 | 512 | 113 / 250 | 31 | 198 | 126 / 355 | 46 | 226 |
| titanet_small | 40.3 | 192 | 60 / 154 | 20 | 274 | 113 / 300 | 39 | 290 |

- The Pi numbers are real measurements on a **Raspberry Pi 5 Model B Rev 1.1, 4 cores, 8 GB (6.4 GB available)**, run with
  `nice 19 ionice idle`, 2 threads, public clips only (no household audio left the Jetson), against an idle voice daemon
  (`zoe-voice` stayed `active`; no turn during the run; the kiosk browser kept running, which loads one core). Pass-to-pass
  spread is up to 1.6x (pass 1 was the fastest), so the table quotes the median of three.
- The Jetson numbers were taken on a box shared with the live brain, CI replays and other agents (load average 4+), and the
  Jetson model loads use torch's CPU wheel (the production torch is a CUDA build and larger). They are **relative, not live
  performance**. The corpus-wide pass (1,377 clips, contended) gave p50 116 / 188 / 307 / 125 / 120 / 66 ms for the six models
  in table order.
- The 2026-10-05 record measured Resemblyzer at p50 172 ms on a different clip set (median about 3 s); consistent with 243 ms
  at 3.65 s here (cost is linear, about 48 ms per audio second).
- **Estimate vs measurement:** nothing in this table is extrapolated. The Pi was measured directly.
- Budget check (record bar: under 200 ms per turn at p50 on the Pi): CAM++ 133 ms and TitaNet 113 ms pass at 3.65 s clips;
  Resemblyzer (243 ms), ResNet34 (369 ms) and ECAPA (415 ms) do not. Capping the embedded window at 3 s brings ResNet34 to
  about 400 ms - still over the bar - so ResNet34 is a server-side or shadow-only option on the panel.

## 4. Two traps this run found (both are action items)

1. **CAM++ + ORT graph optimisation.** `wespeaker_en_voxceleb_CAM++.onnx` through onnxruntime with default optimisation
   (also `ORT_ENABLE_BASIC`) gives embeddings with cosine **0.03** to the `ORT_DISABLE_ALL` result: speakers collapse
   (same-speaker mean cosine 0.80 vs impostor 0.75, EER about 38 % on a 120-clip check) while `ORT_DISABLE_ALL` gives 0.80 vs
   0.15 and ResNet34-like separation. Reproduced on the Jetson (ORT 1.23.2, sherpa-onnx's bundled runtime) and on the Pi
   (the daemon venv's ORT 1.24.4) with the repo's numpy fbank and sherpa's own C++ front end, so it is the runtime, not the
   features. ResNet34 and the 3D-Speaker file are identical at every level. sherpa-onnx exposes no optimisation switch, so
   the harness runs the Kaldi-fbank models through onnxruntime directly (`scripts/voice/speaker_shadow_embed.py:kaldi_fbank`).
   **Fence before shipping:** pin `ORT_DISABLE_ALL` for this file and add a start-up self-test (embed two bundled public
   clips of two speakers plus a repeat; assert same-speaker cosine minus cross-speaker cosine is large) so an ORT upgrade that
   changes behaviour fails loudly instead of silently mis-identifying people.
2. **Width collision.** Resemblyzer and WeSpeaker ResNet34 are both 256-d. The daemon's profile matcher skips only
   *mismatched-width* rows. CAM++ (512), ECAPA (192), TitaNet (192) would be skipped safely; ResNet34 would score against
   stale Resemblyzer profiles with no error. Whatever ships adds a `model_id` to the synced profile row and matches on it.

## 5. What each choice buys

| | keep Resemblyzer | WeSpeaker CAM++ (recommended panel model) | WeSpeaker ResNet34 (challenger) |
|---|---|---|---|
| labelled public EER / FRR@FAR1 % | 21.9 % / 53 % | 3.7 % / 7.9 % | 3.3 % / 6.4 % |
| short clips (2 s or less) public EER | 33 % | 4.5 % | 4.1 % |
| Pi p50 / peak RSS | 243 ms / 729 MB | 133 ms / 228 MB | 369 ms / 233 MB |
| maintained | no (2023-10) | yes (2026-09) | yes |
| runtime risk | CPU-pinned torch, known quirks | ORT optimisation trap (fenced by pin + self-test) | none found |
| migration | none | re-enrol, dual-run | re-enrol, dual-run, width collision |

Tie rule: the maintained project wins ties, and here it also wins on the labelled numbers.

## 6. Migration cost and RAM

- **Profiles are model-specific; every enrolled member must re-enrol** (or be re-embedded from retained audio - the
  biometric retention policy keeps no audio, so re-enrol is the path). Guided enrolment is already a panel flow
  (`zoe_enroll_flow.py`); three clips each, same room. Until re-enrolled, the new model has no profile and the claim stays
  `None` - the existing behaviour for an unknown speaker.
- **Dual-run shadow period (flag-dark, one week):** the daemon embeds each turn with both Resemblyzer and CAM++ (and ResNet34
  as the challenger if the Pi has headroom: +~370 ms in a background thread, only after the turn), logs scores only (the same
  `speaker_shadow_metrics.jsonl` rows with a `model_id`), attaches nothing. Compare FRR/FAR on the household's labelled
  "truth" field; re-derive the threshold there.
- **Pi RAM:** measured standalone peaks - Resemblyzer 729 MB (torch + librosa/numba), CAM++ 228 MB. The daemon already loads
  torch for Silero VAD (`zoe_voice_daemon.py:788`), so the *incremental* saving from dropping Resemblyzer is smaller than the
  standalone gap and needs one in-daemon measurement; the dual-run adds roughly 100 - 230 MB for the ONNX session. The Pi has
  about 6.4 GB available, so this is not a risk either way.
- **Jetson RAM (the hard gate):** `voice_speaker_id.py` lazily imports torch (about 360 MB RSS on the box) for the first
  enrol / identify. An ONNX embedder in zoe-data would use onnxruntime instead: 233 - 265 MB peak process RSS here, CPU-only,
  no CUDA context, and no torch load from that path. Whether onnxruntime is already resident in zoe-data (Moonshine) is
  **[unverified]**; check before claiming a net saving. All Jetson runs here held `MemAvailable >= 2 GB` at start and ran
  inside a shared hold of `/tmp/zoe-voice-harness.lock`.
- **Threshold:** new model, new threshold. Do not carry 0.82 across (section 3.3).
- **Code touched when it ships (not in this PR):** `voice_speaker_id.py` (encoder + cosine), the daemon's
  `_get_voice_encoder` / `_match_speaker_local` / warm-up, the profile table (`model_id`), `ZOE_SPEAKER_ID_THRESHOLD`
  semantics, and a voice-path replay gate per AGENTS.md.

## 7. Limits (read before relying on any row)

- Owner-corpus labels are inferred; the strict core is optimistic, the lenient core pessimistic (section 2.2).
- The far-field channel is synthetic. The Resemblyzer gap persists on clean probes, which suggests the model, not the
  channel, but its VAD pre-trim may also suffer under heavy reverb; I did not run a clean-enrol / clean-probe control.
- 30 enrolment draws from one corpus of about 218 sessions; the FAR 0.1 % rows are coarse (a few dozen impostor trials).
- Jetson timings are contended; the Pi timings are not, but share a core with the kiosk browser.
- Only English models and English impostors; the household's accent mix is unaccounted for.
- 3D-Speaker CAM++ failing here is a finding about this stack (ORT on aarch64, this export), not a verdict on 3D-Speaker.
- Run discipline: the LibriSpeech cropping step ran at a 500 MB `MemAvailable` floor (about 150 MB process, no model); every
  model run held the 2 GB gate. The first Resemblyzer embedding pass started before the shared-lock rule arrived and ran
  outside it; every other CPU-heavy step ran inside `flock -s`. The first CAM++ pass was discarded when the ORT trap was found.

## 8. Reproduce

All artefacts live in a scratch directory outside the repo (`~/.cache/zoe-speaker-bakeoff`), including the 1,672 public
impostor clips and their manifest (below). Nothing is committed but `scripts/perf/speaker_bakeoff.py`.

```bash
# venv: torch+torchaudio (CPU index), speechbrain, sherpa-onnx, resemblyzer(--no-deps)+webrtcvad-wheels, librosa,
#       soundfile, scipy, scikit-learn, kokoro-onnx, onnxruntime; models from the sherpa-onnx release.
H="flock -s -w 3600 /tmp/zoe-voice-harness.lock nice -n 10"
$H python scripts/perf/speaker_bakeoff.py prep --libri <LibriSpeech/test-clean> --out <scratch>/impostors \
     --kokoro-model ~/models/kokoro-v1.0.onnx --kokoro-voices ~/models/voices-v1.0.bin
python scripts/perf/speaker_bakeoff.py index-corpus --out <scratch>
$H python scripts/perf/speaker_bakeoff.py embed --model wespeaker_campp --corpus-index <scratch>/corpus_index.json
$H python scripts/perf/speaker_bakeoff.py score --center raw --core-models ecapa,wespeaker_resnet34,wespeaker_campp,titanet_small \
     --corpus-index <scratch>/corpus_index.json
python scripts/perf/speaker_bakeoff.py bench --model wespeaker_campp --wavs <public wavs> --threads 2   # Pi or Jetson
```

Impostor set (public, reusable for an adaptation-poisoning test): `<scratch>/impostors/{libri,kokoro}/{clean,farfield}/*.wav`
with `manifest.json` (speaker, role, length bucket, variant, duration).

## 9. Next steps (proposed, not started)

1. Flag-dark dual-run in the daemon (CAM++ always; ResNet34 optional), `model_id` on profiles, the start-up self-test.
2. Shadow week on labelled household turns; re-derive thresholds per model; decide CAM++ vs ResNet34 on the accepted-remainder
   question in section 3.2.
3. File the ORT optimisation finding upstream-style note in `docs/knowledge/open-problems.md` and amend the 2026-10-05
   record's CAM++ conclusions with a pointer here.
