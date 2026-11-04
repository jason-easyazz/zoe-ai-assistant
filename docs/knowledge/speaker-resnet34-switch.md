---
type: Runbook
title: The ResNet34 speaker-model switch (ZOE_SPEAKER_MODEL, flag-dark)
description: How the panel's voice recognition moves from Resemblyzer to WeSpeaker ResNet34 (static INT8 QDQ, cohort-centred) - what is built, the model_id rule, the operator steps (build the model, push it, deploy, dual-run, re-enrol at the panel, compare, flip, roll back), the measured cost, the pins and the provenance of the centring vector.
tags: [speaker-id, resnet34, onnx, int8, flags, biometrics, voice, runbook]
timestamp: 2026-10-13T00:00:00Z
---

# The ResNet34 speaker-model switch

**Status (2026-10-29): RESNET34 IS THE ONLY SPEAKER MODEL zoe-data supports. The running process had `ZOE_SPEAKER_MODEL=resnet34` (the flip happened), and Resemblyzer + `dual` were then RETIRED from zoe-data - see *Retirement of Resemblyzer* below. The sections after that describe the original flag-dark rollout (history; `resemblyzer`/`dual` rows in the tables are no longer reachable on the server).** Original status: built, FLAG-DARK; nothing changed live until the owner re-enrolled and flipped the flag. With `ZOE_SPEAKER_MODEL` unset
(= `resemblyzer`) the panel imports nothing new, makes the sync request it always made, writes the shadow rows it always wrote, and the
server serves only Resemblyzer profiles. The spec is the measured record
[speaker-resnet34-optimisation-2026-10.md](../research/speaker-resnet34-optimisation-2026-10.md) (bake-off:
[speaker-model-bakeoff-2026-10.md](../research/speaker-model-bakeoff-2026-10.md)); this page is what was built from it and how to turn it on.

## Retirement of Resemblyzer (zoe-data side, 2026-10-29)

- `ZOE_SPEAKER_MODEL` accepts `resnet34` (also what unset/empty means). `resemblyzer`, `dual` and any typo raise
  `speaker_models.UnsupportedSpeakerModel` from `main.lifespan`, so zoe-data **refuses to start** with a message naming the fix, rather
  than running a server that recognises nobody. The Pi's `.env.voice` must say `resnet34` too (the daemon code still carries the
  legacy modes; that side is untouched here).
- Removed from services/zoe-data: the Resemblyzer encoder, its clean-subprocess `LD_LIBRARY_PATH` workaround, server-side embedding of
  uploaded audio (`/enroll` with `audio_base64` -> 409 "built at the panel"; `/identify` needs `embedding_base64`), `dual`/legacy
  branches of the mode helpers, the `ZOE_VOICE_SESSION_ALLOW_RESEMBLYZER` override, and `resemblyzer`, `torch`, `librosa`,
  `webrtcvad-wheels` from `requirements-py312.txt` / `requirements.txt` (plus the build script's `--no-deps` phase 2). `scipy`,
  `scikit-learn` and `joblib` stay (the joblib router-head fallback); `transformers` stays pinned for one parity test only.
- **Old voiceprints are not a crash.** A `speaker_profiles` row with `model_id` `resemblyzer` (or NULL/empty, every pre-0051 row) stays
  in the table (the member's own "forget my voice" still deletes it) but is never compared, claimed, synced or adapted: every reader
  filters on the acting model's id, and the enrolment state reports such a member as not enrolled so Zoe offers the ResNet34 enrolment.
- **Deploy does NOT uninstall.** `deploy.yml` runs `build_py312_venv.sh --refresh`, an ADDITIVE `uv pip install -r`, so merging this
  stops requiring torch but does not remove it from the live venv; `build_py312_venv.sh --check` will flag the leftovers as EXTRANEOUS.
  Reclaim the disk with an operator rebuild (no args: freeze -> compile -> sync) or `uv pip uninstall torch resemblyzer librosa
  webrtcvad-wheels`. The resident-memory win is potential, not live: the live process never loaded torch in `resnet34` mode (no
  `libtorch` mapped in the running process, 2026-10-29), so what is avoided is the ~360 MB a Resemblyzer load would have cost.

## The recommended configuration, as built

| lever | built as |
|---|---|
| model | WeSpeaker ResNet34, **static INT8 QDQ** (U8 activations, S8 per-channel weights, MinMax over 100 public dev-clean clips), 6.8 MB, never committed |
| runtime | ONNX Runtime `ORT_ENABLE_ALL` (**EXTENDED or higher is mandatory** for this file: pinned per model, a lower level is refused before a session is built), 2 intra-op threads, `session.intra_op.allow_spinning=0`, CPU provider |
| front end | numpy Kaldi fbank, bit-identical to `scripts/voice/speaker_shadow_embed.py` (pinned by a test); the whole clip, no silence trimming (record 3c/3d) |
| normalisation | cohort-mean centring `l2(l2(e) - mu)` with the shipped 256-float public-cohort mean `mu`, version `c1` |
| thresholds | two length groups on the centred cosine, 2 s or less / longer: FAR 1 % = **0.233 / 0.265** (default), FAR 0.1 % = 0.309 / 0.351 (`ZOE_SPEAKER_RESNET34_TIER=far01`); `ZOE_SPEAKER_RESNET34_THRESHOLD_SHORT/_LONG` override. Starting values for the shadow week (record 4.3) |
| enrolment | **5 clips**; mean of the L2-normalised clip embeddings; leave-one-out outlier rejection at 0.30 - a dropped clip is **re-asked** ("that one sounded different"), at most twice; fewer than 3 surviving clips = nothing stored |
| start-up self-test | pin the file's sha256, then embed a bundled PUBLIC synthetic signal: cosine to the stored reference >= 0.9999 and one warm call within 3x the expected latency, else the embedder is **refused** (ResNet34 then scores nothing; `/health` says why) |
| adaptation (#2002) | per-model guards: ResNet34 score gate 0.342 **plus the ANCHOR gate, anchor cosine >= 0.45** (`ZOE_SPEAKER_ADAPT_RESNET34_MIN_SCORE` / `_ANCHOR_FLOOR`); Resemblyzer keeps 0.85 / 0.80, which never fire on ResNet34's scale. Only the ACTING model is ever adapted |
| where it runs | on the Pi, in the voice daemon. The server never loads this model; for ResNet34 the panel uploads a finished 256-float profile, never audio |

## The rule that keeps the models apart: `model_id`

Resemblyzer and ResNet34 are **both 256-d**, so a shape check cannot tell them apart. Every `speaker_profiles` row carries a `model_id`
(migration **0051**: one nullable column, existing rows backfilled to `resemblyzer`, NULL / empty still means `resemblyzer`):

- `resemblyzer`
- `wespeaker_resnet34+i8qdq-u8s8+c1` = model + quantisation + centring-vector version. Change any of the three and the id must change
  (an INT8 profile and an FP32 query do not mix: cosine about 0.97 for one clip).

**Profiles of different model_ids never compare** - not in `/api/voice/profiles/sync` (served per model, and only the models a panel names
with `?models=`; a panel that names none, i.e. every panel older than this, gets Resemblyzer only), not in `/api/voice/identify`, not in the
panel's matchers, not in claim acceptance (a claim is honoured only in the model the server ACTS on, against that model's threshold), not in
adaptation (its other-member margin and its adapted-profile overlay are keyed by model). A member holds one row per model_id; a finished
panel profile REPLACES that model's row and drops its adapted profile; "forget my voice" deletes every row. Code: `services/zoe-data/speaker_models.py`
(server registry) mirrored by `scripts/setup/zoe_speaker_resnet34.py` (panel), drift-pinned by `services/zoe-data/tests/test_speaker_models.py`.

## The flag: `ZOE_SPEAKER_MODEL` = `resemblyzer` (default) | `dual` | `resnet34`

Set the **same value on both hosts** - the Pi's `.env.voice` and zoe-data's environment. A mismatch fails closed: claims are refused, the
turn proceeds unidentified, and zoe-data logs `voice claim rejected: model ... is not the acting model`.

| mode | acts on | the Pi computes | enrolment builds | sync carries |
|---|---|---|---|---|
| `resemblyzer` | Resemblyzer | Resemblyzer | Resemblyzer, 3 clips (unchanged) | Resemblyzer |
| `dual` | **Resemblyzer** | both; ResNet34 is logged, never acted on | **both, from the same 5 clips** | both, to a panel that asks |
| `resnet34` | ResNet34 | ResNet34 only (Resemblyzer / torch is not loaded) | ResNet34, 5 clips; no audio leaves the panel | ResNet34 |

In `dual` the shadow row in `speaker_shadow_metrics.jsonl` is still ONE row per turn, so `truth` is labelled once and both models are compared
on it: the top-level fields are the acting model's (`model_id`, `user_id`, `score`) and `secondary` holds ResNet34's whole decision (model_id,
user_id, score, n_profiles, source, seconds, group, threshold, accept, ms). Scores and metadata only - no vector, no audio. With
`SPEAKER_ID_SHADOW` off, `dual` scores ResNet34 on its own thread as a separate `role: secondary` row, so it never delays a reply. In
`resnet34` a member with no ResNet34 profile yet gets no claim and NO verdict (source `no_profiles`) - never "verified: false", so a member
who has not re-enrolled is not treated as a guest.

## Measured added cost (panel Pi 5, 2026-10-13, public clips only)

The whole ResNet34 path as the daemon runs it (WAV decode, fbank, ORT, centring, match) on 30 public LibriSpeech clips (mean 3.24 s),
`nice -n 10`, 2 threads, three passes, all un-throttled (SoC 61 - 73 C, throttle bits and temperature sampled after every clip), no wake word
or turn in the preceding 6 minutes, `zoe-voice` left running and untouched (run from a temp dir with the panel venv, ORT 1.24.4, then removed):
**p50 69 - 71 ms, p95 215 - 223 ms**; fbank 3.9 - 4.1 ms of it; load + pin + self-test 0.33 s; peak RSS of that standalone process 122 MB.
In `dual` this runs on the background shadow thread after the Resemblyzer score, so time-to-first-audio is unchanged by construction; the
cost is Pi CPU and RAM. **[unverified]** the in-daemon RSS (does the embedder share the wake model's ORT allocator?): read the daemon's RSS
(`ps -o rss= -p` with the pid from `pgrep -f zoe_voice_daemon.py`) before and after the first `dual` turn. On the Jetson (contended) the
3 s self-test clip took 115 ms; at `ORT_ENABLE_BASIC` / `DISABLE_ALL` the same file gave cosine **0.989** to the reference (refused) and 2.4x the time.

## Operator steps, in order

Model steps on the Jetson hold `/tmp/zoe-voice-harness.lock`, run `nice -n 10`, and refuse below 2 GB `MemAvailable`.

1. **Build the model (Jetson, once):** `bash scripts/setup/fetch_speaker_models.sh build ~/.cache/zoe-speaker-resnet34`. The INT8 file is
   derived, not published: the tool (`scripts/perf/speaker_resnet34_artifacts.py build`) fetches the upstream FP32
   `wespeaker_en_voxceleb_resnet34.onnx` from the sherpa-onnx `speaker-recongition-models` release (sha256 `5ef208a9...57fe94`, matches the
   release `checksum.txt`) and LibriSpeech `dev-clean.tar.gz` (sha256 `76f87d09...728ab3`; OpenSLR md5 `42e2234b...300a1`), runs the record's
   `speaker_bakeoff.py prep-cohort` + `quantize --mode static --format qdq --act-type uint8 --w-type int8 --calib minmax`, checks the result
   against the pinned `d1f65aa0...eae32`, then recomputes the centring vector with that model. Needs the bake-off scratch venv
   (`onnx==1.19.1`, onnxruntime, soundfile, scipy; record section 9). **If the digest does not reproduce** (another onnx / ORT version can emit
   other bytes) it stops with exit 4: either obtain the exact pinned file (the record's scratch dir holds it), or treat it as a NEW model
   (`--allow-new-model`, new pins, a new id version, a new centring vector, re-enrol). Diff the refreshed artifacts JSON against
   `scripts/setup/zoe_speaker_resnet34_artifacts.json`: the vector should agree to about 1e-4 per element.
2. **Push it and let the Pi run the daemon's own check:** `bash scripts/setup/fetch_speaker_models.sh push ~/.cache/zoe-speaker-resnet34` copies
   `wespeaker_resnet34_i8qdq_u8s8.onnx` to `/home/pi/.zoe-voice/models/` and runs `verify` there (pin + artifacts + self-test; exit 3 = the
   daemon would refuse it). Re-run `fetch_speaker_models.sh verify` on the Pi after any ORT upgrade.
3. **Migrate, then deploy zoe-data:** `bash scripts/deploy/migrate.sh` (0051, additive, rerun-safe) BEFORE the restart - the new queries read
   `model_id`. Still nothing changes: the flag is unset.
4. **Deploy the daemon:** `bash scripts/setup/deploy-pi-voice.sh` (it now also ships `zoe_speaker_resnet34.py`, the artifacts JSON and
   `fetch_speaker_models.sh`). Its verification reads the daemon's speaker state FAIL-CLOSED: in `resnet34` it waits (`RESNET34_READY_TIMEOUT_S`, 45 s) for `ready`; a daemon reporting a different mode than `.env.voice` configures fails; an unreadable state passes only when `.env.voice` proves the legacy mode. Replay-gate it as any voice-path change (`scripts/maintenance/voice_regression_probe.py` under the harness lock).
5. **Turn on `dual`** on both hosts (`ZOE_SPEAKER_MODEL=dual` in zoe-data's env and the Pi's `.env.voice`; restart both). On the Pi,
   `curl -s localhost:7777/health` must show `speaker_resnet34: ready` (otherwise `refused: <reason>`).
6. **Re-enrol at the panel - no ssh, no command.** Recordings are not kept, so a Resemblyzer profile cannot be converted; a fresh sitting is
   the only path. With `ZOE_ENROLL_OFFER=on` (#2003) Zoe knows a `dual` member is missing the ResNet34 profile and offers at the natural
   moments (after a PIN step-up, at the close of a conversation), or the member says "Zoe, learn my voice". She says what is kept (a voice
   signature, not recordings), asks for 5 short phrases in the normal voice, re-asks a clip that sounds unlike the rest, and stores one
   profile per model from the same clips; a "no" is respected. `zoe_enroll_flow.py voice --user ... --name ...` is the hand-run equivalent.
   Done when `GET /api/voice/profiles` lists both model ids for the member. In `resnet34` mode the settings page's audio enrolment is refused (409, "say 'Zoe, learn my voice' to the panel", nothing stored): a Resemblyzer profile would never be used.
7. **Compare for a week:** label `truth` on the shadow rows (the W5 review: the member id who spoke, or `none` for a non-enrolled voice), then
   `python3 scripts/voice/speaker_dual_compare.py ~/.zoe-voice/speaker_shadow_metrics.jsonl --legacy-threshold 0.82` (aggregates only:
   TA / FR / FA per model and the ResNet34 added ms). Re-derive both length-group thresholds on the labelled household turns (the real-channel
   FAR is the record's missing piece, section 7), choose the tier, and watch the FAR 0.1 % tail the record flagged.
8. **Flip:** only when every member who should be recognised has a ResNet34 profile and the comparison agreed - `ZOE_SPEAKER_MODEL=resnet34`
   on the **server first, then the Pi**. Keep `ZOE_SPEAKER_ADAPT` off until the shadow week is read and a second member is enrolled (the
   margin guard is vacuous with one voice).

**Rollback** is the flag (`dual` or `resemblyzer` on both hosts). Resemblyzer rows are never touched by the ResNet34 path.

## Provenance and licences

- **Model:** WeSpeaker ResNet34 VoxCeleb, sherpa-onnx ONNX export; code and published weights Apache-2.0 per the HF card and repo licence field
  (bake-off record section 1; the card, not the legal text, was read). VoxCeleb is the upstream's training data, not redistributed here.
- **Centring vector `c1`** (committed in `scripts/setup/zoe_speaker_resnet34_artifacts.json`, its digest pinned in the panel module): the mean
  of the L2-normalised embeddings of 240 synthetic far-field crops of **LibriSpeech dev-clean** (40 speakers, 6 crops each, built by
  `speaker_bakeoff.py prep-cohort`, seed 7), embedded with the pinned INT8 model on ORT 1.23.2. LibriSpeech is **CC BY 4.0** (Panayotov, Chen,
  Povey, Khudanpur, "LibriSpeech: an ASR corpus based on public domain audio books", ICASSP 2015); the attribution is carried in the JSON. It
  is a 256-number statistic of public speech - not audio, not any person's voiceprint; no household member is in the cohort. It matches the
  record's own cohort mean to cosine 0.999999. The panel's real channel is not the synthetic one: recompute it on real panel-channel audio of
  non-enrolled speakers when that exists, as a new version (`c2` = a new model id = re-enrol).
- **Self-test reference:** the embedding of `zoe_speaker_resnet34.synthetic_clip(3.0)` - pure arithmetic, no RNG, not a voice.

## Tests

`services/zoe-data/tests/test_speaker_models.py` (registry, the claim matrix per mode, sync / identify never cross models, the finished-profile
enrolment, per-model adaptation and the anchor gate's zero-update lookalike, enrolment state, script, migration 0051 and the single head,
drift and pins), `tests/unit/test_speaker_resnet34.py` (front end, centring, self-test drift / slowness refusals, graph-level refusal, loader
pins, cross-model matching, length-group decisions, the 5-clip run with re-ask, dual acting only on Resemblyzer through the daemon's real
dispatch, default mode byte-identical) and `tests/unit/test_speaker_dual_compare.py`. Each guard has a negative control; the key ones were
also mutation-checked (remove the guard and the named test goes red).
