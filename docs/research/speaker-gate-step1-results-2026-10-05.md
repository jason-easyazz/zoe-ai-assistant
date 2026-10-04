---
type: research
title: Speaker gate rebuild — step 1 results on the Pi (2026-10-05)
date: 2026-10-05
status: measured — no live path, flag, unit or service changed; aggregates only (no clip names, transcripts or embeddings)
description: Step 1 of the speaker-gate rebuild (Q15) — the candidate embedder (3D-Speaker CAM++ ONNX, Apache-2.0) and the incumbent (resemblyzer) run over the replay corpus ON THE PI, scored against the four agreed targets. Three of four targets pass on pseudo-labels but those labels do not survive an independent check; the labelled TV clips fail the zero-accept bar; near-silence is accepted by the cosine gate. Verdict - GO for the shadow week as a measurement instrument, NO-GO for any claim that the targets are met.
---

# Speaker gate rebuild — step 1 results on the Pi (2026-10-05)

Run 2026-10-04 21:30–22:00 AWST on `zoe-pi`. Plan and targets: [speaker-gate-rebuild-2026-10-04.md](speaker-gate-rebuild-2026-10-04.md)
(§5.3), owner decisions Q15. Code: `scripts/voice/speaker_shadow_embed.py`,
`scripts/voice/speaker_shadow_eval.py`, pinned by `tests/unit/test_speaker_shadow_scripts.py`.
Nothing here touches a live path: no daemon file, flag, unit or Jetson service changed. **Aggregates
only** — the speaker embeddings are biometrics and stayed on the Pi.

## 0. TL;DR

| Q15 target | CAM++ (candidate) | resemblyzer (incumbent, same clips) | Verdict |
|---|---|---|---|
| Separation ≥ 0.20 (owner median − runner-up median) | **0.500** (strict, vs pool p95: 0.342) | 0.106 (strict 0.057) | passes on pseudo-labels only |
| False rejects < 10 % at the FAR = 1 % threshold | **8.3 %** (threshold 0.607; pool FAR 0.88 %) | 61.6 % (threshold 0.816) | passes on pseudo-labels only |
| False accepts < 1 % at the FRR = 10 % threshold | **0.66 %** of the pool (threshold 0.631) | 7.3 % (threshold 0.728) | passes on pseudo-labels only |
| Zero accepts of the TV clips | **FAIL — 2 of 5 accepted** at both points; enforcing zero needs threshold 0.867 = **56 % FRR** | FAIL — 1–2 of 5 | fails on the only labelled impostors |
| < 200 ms per turn on the Pi | **p50 148 ms**, p95 376 ms, max 574 ms; 74 % of clips under 200 ms | p50 172 ms, p95 522 ms | p50 passes, p95 does not |

The honest reading: **the targets are not met, they are unproven.** Everything marked "pseudo-labels"
rests on a 2-means cut of the corpus, and that cut does not replicate in a second, independent
embedder (§4). The only independently labelled impostors (five TV clips) fail the zero-accept bar, and
near-silence is accepted as the owner by 34 of 50 non-speech clips (§5). CAM++ is nonetheless
decisively better than the incumbent on every yardstick we have (§6), and it fits the Pi.

**Go / no-go (§8): GO for the shadow week as a measurement instrument — flag-dark, Pi-only — with four
changes to the design; NO-GO for graduating the gate or writing any threshold into a live `.env`.**

## 1. What ran

- **Corpus on the Pi** (`~/.zoe-voice/speaker-shadow/corpus`, mode 700/600, 151 MB; the Pi had 14 GB free
  against the 3 GB stop-rule): 1,312 top-level clips + the 5 TV false-wake clips + the 50 non-speech
  quarantine clips = **1,367**. The 62 byte-identical replays and 5 unreadable files were not copied.
  Clip durations p5 / p50 / p95 = 1.56 / 3.04 / 8.08 s. Clips < 0.5 s: **0** (none skipped).
- **Candidate**: 3D-Speaker CAM++ en-voxceleb, sherpa-onnx ONNX export, 29,596,978 B, Apache-2.0,
  sha256 `357a834f…129b` (pinned in the script; trust-on-first-use — the GitHub release API publishes no
  digest, only the matching size). Downloaded on the Pi only, under the shadow dir. **Correction to the
  record: the output dimension is 512, not 192** (read from the ONNX output shape and metadata; the
  metadata also says `normalize_samples=1`, `feature_normalize_type=global-mean`, 80-mel input).
- **Features**: a numpy Kaldi-fbank (25/10 ms, Povey window, 0.97 pre-emphasis, per-utterance mean
  normalisation), torch-free. Checked against `torchaudio.compliance.kaldi.fbank` on four clips: max
  absolute log-mel difference 4.1e-4. (Embedding-level parity against sherpa-onnx's own C++ front end
  was **not** checked — see §9.)
- **Incumbent baseline**: resemblyzer, run exactly as the daemon runs it (`preprocess_wav` +
  `embed_utterance`), weights hashed in place from the daemon's venv (`39373b86…134e`, 17,090,379 B,
  read-only import; nothing installed or modified). 1,361 embedded, 6 clips trim to nothing and cannot
  be embedded at all.
- **Run discipline**: `nice -n 19 ionice -c3`, 2 threads (`OMP_NUM_THREADS=2`, ORT intra-op 2), `nohup`
  with a log in the shadow dir.

| | CAM++ | resemblyzer |
|---|---|---|
| Wall time (1,367 clips, all-in incl. read + resample) | **236.5 s** (≈173 ms/clip) | 314.4 s |
| Peak RSS of the whole process (`getrusage`; the Pi has no `/usr/bin/time`) | **249.9 MB** | 731.9 MB |
| Embedded / skipped short / errors | 1,367 / 0 / 0 | 1,361 / 0 / 6 |

- **Daemon health**: the scripts poll the daemon's `/health` every 60 s and abort on a failure streak or
  an uptime reset; 4 polls (CAM++) and 6 polls (baseline) all `ok`. My own polls every ≈90 s from the
  Jetson plus checks before and after each job: `zoe-voice` `active`, uptime strictly increasing
  (6,434 s → 7,442 s over the session), **no daemon restart**, no change to the daemon's shadow-metrics
  row count (319). The run ended 22:00, far inside the 03:30 start / 04:10 stop window.

## 2. Method — and why the labels are the weak point

The corpus has **no speaker labels** (record §5.1). The evaluation therefore uses:

- **Owner** = cluster A of a spherical 2-means over the top-level clips, scored with
  **leave-sessions-out** centroids (193 capture sessions in A from mtime gaps > 30 min, dealt into 5
  contiguous folds; a clip is never scored against a centroid that contains its own session — the
  same-session trap is closed).
- **Impostors** = the 5 TV clips (the only independently identified non-owner audio) ∪ cluster B
  (452 clips, "pseudo-labelled": cut from the same embeddings it is then scored in).
- Controls: label shuffle gives EER 50.3 % (harness measures identity, not noise); the same A/B +
  leave-sessions-out pipeline on **random unit vectors** gives EER 12.8 %, the harness's
  **selection-bias floor** — a pseudo-labelled number is only believable to the extent it sits under
  it (CAM++ pool EER 5.9 % does, 95 % CI 4.4–7.3 %; the incumbent's 8.0 % barely does).

## 3. Latency — the < 200 ms bar

Per-clip compute (feature extraction + inference, after 3 warm-ups; file read and resampling excluded —
the daemon already holds 16 kHz audio). n = 1,364.

| Clip length | n | p50 | p95 |
|---|---|---|---|
| < 2 s | 208 | 78 ms | 114 ms |
| 2–5 s | 953 | 148 ms | 243 ms |
| > 5 s | 203 | 332 ms | 481 ms |
| **all** | 1,364 | **148 ms** | **376 ms** (max 574) |

Cost is linear in audio: **≈ 47.5 ms per second of audio + ≈ 6 ms** (r = 0.92); feature extraction is
5 ms of it. The record's "≈ 5× faster than resemblyzer's 540 ms" is **not supported**: resemblyzer on
the same clips under the same conditions is p50 172 ms / p95 522 ms, so CAM++ is ≈ 14 % faster at p50
and ≈ 28 % faster at p95. The 540 ms live figure includes contention with the running daemon; **CAM++'s
in-daemon latency is unmeasured** (§9). The bar is met at p50 only for typical 2–5 s turns; a turn
longer than ≈ 4 s breaches 200 ms. Lever (derived from the fit, not measured): cap the embedded
window at ≈ 4 s of speech (≈ 196 ms); the length table below says longer audio does not buy accuracy.

## 4. The cluster finding (the two-acoustic-cluster trap)

| | CAM++ space | resemblyzer space |
|---|---|---|
| Best k by silhouette | **2** (k=2 0.42, k=3 0.28, k=4 0.33) | 2 (0.28, 0.27, 0.18) — weak |
| Cluster sizes A / B (own fit over the clips each model embedded) | **860 / 452** (65.6 % / 34.4 %) | 1,026 / 282 (**78.4 %** — the ops figure; 1,308 clips) |
| Centroid cosine A–B | 0.40 | 0.79 |
| Tightness (mean cos to own centroid) A / B | 0.81 / 0.53 (A tight, B diffuse) | 0.80 / 0.76 |
| Level / length of A vs B | rms −26.3 vs −26.7 dBFS; 3.0 vs 3.6 s — **not a loudness split** | −26.3 vs −28.9 dBFS; 3.2 vs 2.5 s |
| TV clips nearest A / B | 3 / 2 | 2 / 3 |

CAM++ finds a clean two-cluster structure; resemblyzer's is weak but lands on the remembered 78 / 22 split.
**The two cuts are independent of each other**, which is the finding that matters (the comparison re-fits both on the 1,308 clips both models embedded, where CAM++ gives 838 / 470):

- Membership agreement 56.4 % (Jaccard of the two A sets 0.53). P(resemblyzer-A | CAM++-A) = 0.772,
  P(resemblyzer-A | CAM++-B) = 0.806, base rate 0.784 — knowing the CAM++ cluster tells you **nothing**
  about the resemblyzer one.
- CAM++'s A set is invisible to the other model (mean pairwise cosine in resemblyzer space: lift
  +0.005 over random same-size sets, z = 1.6; the reverse, z = 2.5), while each is strongly coherent in
  its own space (z = 42 and 36).
- **Cross-label EER collapses to chance**: scoring CAM++ with resemblyzer's A/B labels gives EER 44.9 %;
  scoring resemblyzer with CAM++'s labels gives 49.3 %.
- Only the **consensus** clips (A in both: 647; B in both: 91) separate cleanly (CAM++ EER 2.1 %), and
  that subset is selected for being separable by construction.

So "cluster A = the owner, cluster B = everyone else" is **not established**. At least one of the two
partitions is cutting the corpus along something other than speaker identity — channel, distance,
state, time of day, or within-owner variation. The earlier single-embedder story (A = owner near-field
78 %) was a property of resemblyzer's space. For enrolment this repeats the 2026-07-19 lesson in a new
form: **no clip of this corpus may be used to enrol**, and no A/B label from it may be treated as
ground truth.

## 5. What the labelled evidence says

### 5.1 The five TV clips: two of them look like the owner, in both models

CAM++ scores of the five TV clips against the owner centroid (sorted): −0.002, 0.277, 0.356, **0.819,
0.867** — against an owner median of 0.857. The top two sit at the 37th and 56th percentile of the
owner's own scores. The accepted two are **loud (mean −26.3 dBFS — the same as the median near-field
owner clip) and long (4.6 s)**; the three rejected are quiet (−42.1 dBFS, 2.4 s). Resemblyzer shows the
same shape (loud, long clips accepted; the quiet ones rejected; at least one clip is owner-like in both
spaces — `tv_accepted` overlap 1). The best match of each TV clip to any single owner clip has median
cosine 0.92 (CAM++). Two unrelated embedders agreeing that loud, long, close-mic "TV" clips are
owner-like points to **the clips, not the model**: either the quarantine holds the owner's own voice
(a real utterance that false-woke, or someone near the panel), or it is a very similar voice at the
mic. I cannot tell which from aggregates and did not listen to or transcribe anything.
Consequence: the zero-TV-accept target as written is **unmeetable** at any usable FRR (threshold 0.867
rejects 56 % of the owner) until those clips are re-judged by ear.

### 5.2 Near-silence is accepted as the owner (CAM++)

Of the 50 non-speech quarantine clips (median level −54 dBFS, i.e. the noise floor), **34 score above
the operating threshold** (median score 0.869 — above the owner median). Their mean vector sits at
cosine **0.93** from the CAM++ A centroid and 0.42 from B. The incumbent accepts 3 of 48 at 0.70 (its
own VAD trim removes most of them). Reading: CAM++'s cluster A carries a large **panel-mic
near-field channel** component, so the cosine alone cannot tell "the owner speaking" from "the panel's
own noise floor". This makes a **speech-evidence precondition** (net speech after VAD, ≥ 2 s) a hard
requirement before any embedder score may be used — it was an option in the record (`SPEAKER_ID_MIN_EVIDENCE_S`).

### 5.3 Length

| Clip length | owner n / pool n | EER | FRR at the operating threshold |
|---|---|---|---|
| < 2 s | 28 / 165 | **35.7 %** | 100 % |
| 2–5 s | 681 / 240 | 3.4 % | 4.0 % |
| > 5 s | 151 / 52 | 7.8 % | 10.6 % |

Below 2 s the gate is a coin flip (Omi's bench said the same: 17 % EER at 2 s). Abstain below 2 s;
the sweet spot is 2–5 s of speech.

## 6. The incumbent on the same clips

On the same pseudo-labels, resemblyzer fails every target but latency: separation 0.106, FRR 61.6 % at
the 1 %-FAR threshold, FAR 7.3 % at the 10 %-FRR threshold (EER 8.0 %, noise-floor control 9.8 %). At
the thresholds in use today:

| resemblyzer cosine | owner rejected | cluster-B accepted | TV accepted | non-speech accepted |
|---|---|---|---|---|
| 0.70 (server `.env`) | 4.5 % | **10.3 %** | 2 / 5 | 3 / 48 |
| 0.75 (Pi `.env.voice`) | 15.5 % | 4.3 % | 2 / 5 | 1 / 48 |
| 0.82 (code default) | 65.5 % | 0 % | 1 / 5 | 0 / 48 |

(The live profile is a 12-clip centroid, not this leave-sessions-out one, so read these as the shape,
not the exact live rates.) On every axis we can measure CAM++ is at least as good, on RAM it is 3×
lighter, and the ≈ 0.50 vs 0.11 separation gap is not subtle — even allowing for the label problem
the direction is not in doubt; the magnitude is.

CAM++ at fixed cosine lines on its own pseudo-labels (FRR / cluster-B FAR / TV / non-speech accepted):
0.50 → 6.1 % / 5.5 % / 2 / 34; 0.60 → 8.0 % / 1.1 % / 2 / 34; 0.65 → 11.5 % / 0 % / 2 / 34;
0.70 → 17.9 % / 0 % / 2 / 34; 0.80 → 32.1 % / 0 % / 2 / 29.

## 7. Recommendations

**Threshold(s).** On CAM++ raw cosine the provisional operating band is **0.61 (pool FAR ≤ 1 %, FRR
8.3 %) to 0.63 (FRR 10 %, pool FAR 0.66 %)**; the bootstrap 95 % interval on FRR at the 1 %-FAR
threshold is 6–37 % because the impostor tail is a handful of clips. **Do not write any number into a
live `.env`.** The shadow week logs the raw cosine only (no accept line), plus `evidence_s`, and the
threshold is derived afterwards from labelled DET data; use 0.62 only as the shadow "would-accept"
marker. Thresholds do not transfer between models (the record's OVOS note); CAM++'s scale here is wide
(owner 0.86, others 0.18), not the compressed one OVOS reported.

**What re-enrolment (8 prompts × 3 conditions) must capture.**

1. **≥ 8 clips, each ≥ 3 s of net speech** (hard floor 2 s). The enrolment-size curve (centroid of *n*
   random clips from other sessions, FRR at the 0.607 line): n = 1 → 38 %, 3 → 18 %, 5 → 14 %,
   **8 → 11 %**, 12 → 10 %, 24 → 9.8 % — it flattens at 8, matching the plan. Prompts of ≈ 4 s each.
2. **Three conditions, with room distance as the one that matters.** The corpus can only vary
   loudness: enrolling loud and testing quiet costs ≈ 1 point of FRR (13.6 % vs 12.9 % with all
   conditions), so level alone is not the risk — but rms is a weak proxy for distance, and the owner's
   far-field voice is **not measured by this corpus at all**. The 3-near / 3-room-distance / 2-quiet
   plan stands, and the room-distance clips are the new information.
3. **A labelled negative set the corpus cannot supply**: ≥ 10 minutes of TV/radio played near the
   panel during the session (with the log marking it), and — with consent — **at least one other
   household member** speaking the same prompts, because the margin rule (best − runner-up) needs a
   second enrolled voice and the corpus holds **no labelled second household speaker** (FA against
   another near-field voice is unmeasured, and §5.2 says to expect the channel component to inflate it).
4. **Noise-floor / silence controls** recorded on the same mic, to confirm the speech-evidence rule
   rejects them.
5. Quality gate per clip (record §4.2): reject a clip whose score to the running centroid is an
   outlier, or whose VAD speech is < 3 s.

## 8. Go / no-go for the shadow week

**GO — as a measurement instrument.** It is flag-dark, Pi-only, torch-free, ≈ 250 MB standalone,
≈ 150 ms per typical turn, and it is the only way to get the ground truth step 1 cannot. Changes to the
design in the record, from this run:

1. **Gate on speech evidence first.** Score only turns with ≥ 2 s of net speech (post-VAD) and log
   `evidence_s`; below that the row is `abstain`. (§5.2, §5.3.)
2. **Log the raw cosine, no accept line**, for both embedders on the same turns (the existing
   `(boot, seq)` rows), and have the operator fill `truth` on a sample that deliberately includes TV
   and a second speaker.
3. **Re-judge the five TV clips** (or at least the two loud ones) by ear before they are used as
   "impostors" again; if they hold the owner's voice, relabel them and the zero-TV-accept target
   becomes meaningful on the remaining three — and needs fresh TV recordings (§7.3).
4. **Measure in the daemon**: process RSS delta (budget ≤ 150 MB) and the `Recorded → Speaker ID` gap
   with the daemon live. The standalone numbers here do not answer either.

**NO-GO for graduation or for any claim that Q15's targets are met**, until labelled shadow-week rows
exist. Three targets pass only on labels that fail an independent check, the fourth (zero TV) fails on
the only labels that are independent, and the p95 latency is 1.9× the bar.

## 9. Unverified / limitations

- No ground truth: identity of every non-TV clip is inferred. "Owner" and "cluster B" are
  model-specific cuts (§4). The TV clips' content was not heard.
- numpy fbank parity was shown against torchaudio's Kaldi fbank (4 clips, log-mel 4.1e-4), not against
  sherpa-onnx's C++ front end; a small systematic difference would shift scores uniformly.
- Latency/RSS are standalone, `nice 19`, 2 threads, with the daemon running but presumably idle; the
  daemon's own CPU and memory contention is not modelled. RSS is the whole process (numpy + scipy +
  onnxruntime), so it overstates the increment inside the daemon, which already loads numpy and
  onnxruntime; by how much is unmeasured. `/usr/bin/time -v` does not exist on the Pi, so wall time
  and peak RSS come from `getrusage` inside the script.
- Bootstrap intervals treat clips as independent; capture sessions are clustered, so the true
  intervals are wider.
- The non-speech result shows the attractor exists; it does not say whether it is silence-specific or
  a general channel effect on short low-SNR speech.
- The quarantine of 62 duplicates and 5 unreadable files was not embedded.

## 10. Housekeeping and data

- Everything biometric stays on the Pi under `~/.zoe-voice/speaker-shadow/` (700/600): the corpus copy
  (151 MB), `embeddings.npz` ×2, manifests (keyed by hash of the relative path — no clip names),
  reports, the two model files with their licence/hash sidecars. **Nothing was copied off the Pi or
  committed.** Per the retention policy, delete the corpus copy and embeddings when the shadow week
  closes (or sooner on request): `rm -rf ~/.zoe-voice/speaker-shadow/{corpus,baseline-resemblyzer,embeddings.npz,manifest.json}`.
- Reproduce on the Pi, from `~/.zoe-voice/speaker-shadow/code/`: `speaker_shadow_embed.py` (CAM++),
  `speaker_shadow_embed.py --model resemblyzer_baseline --out-dir ../baseline-resemblyzer`, then
  `speaker_shadow_eval.py --compare-with ../baseline-resemblyzer [--fixed-thresholds 0.60,0.70]` and
  `speaker_shadow_eval.py --shadow-dir ../baseline-resemblyzer`. Env knobs (`SPEAKER_SHADOW_*`) are
  script-only; no live flag reads them.
