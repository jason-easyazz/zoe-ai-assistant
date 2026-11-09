---
type: spec
title: The pendant night re-pass - the best transcript the box can make, while Zoe sleeps
description: Flag-dark ZOE_OMI_NIGHT_REPASS=off|shadow|on - a pluggable offline ASR (faster-whisper large-v3 first) plus ECAPA speaker windows over the day's pendant audio, run in the night window before the 12B loads; the wearer print, the audio hold, the forget rules, the budget, the owner steps.
tags: [omi, pendant, night-window, asr, diarisation, spec]
timestamp: 2026-11-09T00:00:00Z
---

# The pendant night re-pass

## Acceptance - true when done

In the owner's words (2026-11-09: "for omi to be useful you will have to have the best transcription possible"; "it runs at night after a sync, so there is more flexibility"):

- [ ] **"Pendant conversations read cleanly, like a person typed them."** Names are spelt right, sentences are whole, and a quiet room does not become invented words.
- [ ] **"Every line says who said it - me, family, or a named guest."** Me comes from a print the owner confirmed, never a guess; family and guests are suggested from voices the owner has taught Zoe and become names when the owner agrees. Nothing is ever silently labelled.
- [ ] **"The day review and Zoe's notes use the clean version."** The day review (and the VAGO extract after it) read the re-passed text, in the same window, after the re-pass.
- [ ] **"It all happens in one night window and Zoe is back before morning."** The re-pass runs inside `zoe-night-window` before the 12B loads; the window still ends by 03:55, before the 04:10 voice gate; what does not fit carries to tomorrow.
- [ ] **Nothing that was forgotten comes back, and no audio is lost before it is read.** A forgotten name stays `[forgotten]`; an erased member gets nothing written; a recording's audio is kept until the pass has seen it (and never past the 7-day retention).

Each box has a test (`services/zoe-data/tests/test_omi_repass.py`, `tests/unit/test_night_repass_window.py`) or a live check below. The shadow weeks decide `on`.

## What it is

Moonshine (the live STT rock) writes the FIRST transcript minutes after a sync. It is fast and sits between voice turns, but on pendant audio it garbles names and invents text on near-silence. The **night re-pass** is the SECOND transcript, offline, with the 4B brain, Kokoro, the router and zoe-data all stopped (the box has ~12.6 GB free then):

1. **ASR**: faster-whisper large-v3 (CPU int8) - VAD, word timestamps, a **name hint** (household first names + the owner's named guests, minus anything on the forget ledger), names normalised to the hinted spelling. When the day's audio would overrun the budget the planner drops to distil-large-v3 (about 3x faster, agrees with large-v3 at 0.84).
2. **Speakers**: ECAPA embeddings of 1.5 s windows (0.75 s hop) over the speech spans, matched against (a) the wearer's pendant-channel print, (b) Zoe's own TTS voice (kokoro cache; matched lines are marked `not_person='zoe'`), (c) voices the owner taught Zoe in this speaker space.
3. **Store** (mode `on`): the clean transcript and new `omi_segments` replace the Moonshine ones, with provenance (`repass_model`, `repass_at`). Shadow stores nothing and logs counts.

## The seam (pluggable backends)

A second bake-off is evaluating newer ASR (Nemotron Transcribe, Canary-Qwen, Qwen3-ASR, Granite Speech, Cohere Transcribe) and diarisation (pyannote community-1, Sortformer) engines. The re-pass does not care which wins:

- **ASR**: `ZOE_OMI_NIGHT_ASR` (default `faster-whisper:large-v3`). Contract: `AsrBackend.transcribe(audio, hint, deadline) -> words [[word, t0, t1, confidence]...]` in `scripts/night/jobs/omi_repass_worker.py` (`ASR` registry).
- **Diarisation**: `ZOE_OMI_NIGHT_DIAR` (default `ecapa`). Contract: `windows(audio, speech) -> [(t0, t1, embedding, rms)]` (`DIAR` registry); the parent matches the embeddings, so a turn-segmenting engine can return its own turns as windows. A different embedder needs its own `MODEL_ID` (prints are per speaker space - never mixed).
- **Budget per backend**: `omi_repass.BACKENDS` declares each engine's `rtf`, `gpu_rtf` (None = never measured), `peak_rss_mb` and `fallback`. The planner, the capped scope's `MemoryMax` and the 12B-load arithmetic read those; a night's measured RTF (`~/.zoe/omi-repass/state.json`) overrides the declared one. Adding an engine = one registry entry in the worker + one row in `BACKENDS` (+ its venv pins). The bake-off installs models; this repo only holds the seam.

### Backend: `gemma4-12b-audio` - the night 12B's OWN audio encoder (candidate)

The 12B's mmproj (`mmproj-gemma-4-12b-it-qat-q4_0.gguf`, 175 MB) carries a `gemma4ua` audio encoder; the b11194 binary the window uses knows that projector (checked: `libmtmd` contains it, `--mmproj` / `--no-mmproj-offload` exist). Select it with TWO matching switches (the window reads no ZOE_* flag): `ZOE_OMI_NIGHT_ASR=gemma4-12b-audio` in the zoe-data env, and `--audio-mmproj` (or `NIGHT_AUDIO_MMPROJ=1`) on the window's `ExecStart`:

- **Window**: the 12B command gets `--mmproj <audio> --no-mmproj-offload --jinja` (CPU encoder: on the GPU it fragments the carveout and OOMs - measured in June) ONLY with `--audio-mmproj`. Without it the 12B command is byte-for-byte what it was (`--mmproj` stays dropped). A mismatch is loud and harmless: the re-pass job refuses with `wrong_stage` (a CPU backend inside the 12B stage would load ~2.8 GB beside the 12B; the audio backend before the 12B loads has nothing to talk to).
- **Order**: the re-pass is NOT a pre-job for this backend. It is the FIRST job of the 12B stage, run on the already-loaded 12B (`--model-url http://127.0.0.1:11500/v1`; the CLI refuses the live 4B on :11434 and any non-loopback url), clipped to leave 25 minutes for the jobs after it. The day review / VAGO extract still read its clean text, and the 12B jobs run only after it.
- **How**: VAD chunks identical to the bake-off's (silero, at most 14 s; the cap is 30 s) -> an OpenAI `input_audio` part (base64 16 kHz mono WAV) -> "transcribe verbatim, no summary, no commentary, no timestamps or speaker labels, output nothing if there is no speech" + the name hint -> text. Refusals/descriptions ("There is no speech...") and runaway loops (> 7 words per second) are dropped. The model returns text only, so word times are spread evenly over each chunk and every word's confidence is a flat 0.6; speakers still come from ECAPA windows (so a chunk with two voices is labelled at word granularity only roughly).
- **RAM**: the lever arithmetic reserves `NIGHT_AUDIO_EXTRA_MIB` (default 1600) on top of the 700 MiB job reserve: the mmproj plus its CPU encoder buffers (~0.5-0.9 GB, to be measured) and the worker (ECAPA / torch / VAD, ~0.7 GB). The measured 2026-11-08 free RAM (12,637 MiB) still clears the 8k / q8_0 12B with that reserve (test-pinned). A box with no mmproj file refuses before anything is stopped.
- **Budget**: declared in `omi_repass.BACKENDS` as decode-bound, `rtf` 0.7 s per second of audio (about 3.3 tokens per speech second at the measured 5.9 tok/s, plus prefill; pessimistic, all-speech). **The lab trial (`asr2_gemma12b.json`) replaces this with a measured figure** - update the row then; the planner also self-corrects from the first night's measured RTF. No fallback backend (the 12B is the only place it can run).
- Memory beside the 12B is why the worker's scope is capped at `(300 + 700) x 1.3` MiB and why this backend cannot use the fallback to distil: there is none.

## Where it runs in the window, and why (RAM / GPU arithmetic)

Measured 2026-11-08: after the stops MemAvailable was **12,637 MiB**; the 12B load takes it to **4,220 MiB**; the jobs' low was **3,166 MiB**; the floor is **1,200 MiB**. The re-pass needs about **2.1 GB (large-v3) + 0.7 GB (ECAPA + torch) = 2.8 GB** (bake-off peak RSS 2.0 / 2.3 GB).

| order | RAM | verdict |
|---|---|---|
| **before** the 12B loads (chosen) | 12.6 GB free - 2.8 = ~9.8 GB, then the process EXITS and the box is compacted and re-measured before the lever choice | no contention; the 12B still gets its 9.4 GB |
| beside the 12B | 3.2 GB low - 2.8 = ~0.4 GB < the 1.2 GB floor | breach. Also 6 CPU cores shared with the 12B's CPU layers |
| after the 12B | fits, but the day review and the VAGO extract need the CLEAN text and run on the 12B | wrong order |

**GPU**: the Orin's GPU is idle before the 12B loads, so headroom exists in principle. **Measured 2026-11-09 (`zoe-omi-repass.py --probe-gpu`)**: the only CTranslate2 wheel for aarch64 on PyPI (4.8.5) reports `get_cuda_device_count() == 0` and the cu130 torch wheel cannot initialise CUDA against the box's JetPack 6 driver (CUDA 12.6) - **there is no usable GPU path on the pinned stack, so the GPU timing run could not be made; every GPU figure in the bake-off report stays an estimate.** The pass is CPU. `ZOE_OMI_REPASS_GPU=1` is wired in the worker and falls back to CPU when no device exists; a GPU needs a CUDA build of CTranslate2 against JetPack's CUDA 12.6 (a lab task: build, then run the probe and one timed file, then record it here).

**Time**: the pre-job leaves `NIGHT_PRE_KEEP_MIN` (28 min) for the 12B stage (load + probe ~3, jobs 7 measured to ~20 with a full day review, margin). With the window starting 02:50 and its 65-minute cap that is a re-pass budget of about 25 minutes. The planner estimates `audio_s x rtf` against 85 % of the budget, drops to distil when large-v3 would overrun, and the worker takes recordings oldest first (recordings the first pass found no words in go last) and starts nothing it is unlikely to finish. **Unfinished recordings are not failures: they stay `pending` and carry to the next night**, bounded by the 7-day retention.

> **Capacity warning (read before `on`).** The pendant records up to ~12 h of audio a day. CPU large-v3 costs up to ~86 min per hour of DENSE speech; distil ~30. The bake-off audio was dense; a real day is mostly silence the VAD skips cheaply, but nobody has measured a real day yet. **The shadow weeks measure it** (`audio_s`, `worker_rtf`, `carried` in the job line and the report). If a day does not fit, the levers are: a faster backend from the second bake-off, a CUDA CTranslate2, a longer window (the 04:10 voice gate is the limit), or accepting that Moonshine text stands for what carries.

## Wearer reference (the panel print does not match the pendant)

The panel owner print matches the pendant channel at cos ~0.04. The pendant-channel wearer print is built only from **windows the owner has vouched for**:

- **Seed (automatic)**: first-pass segments the owner already labelled "me" (`omi_segments.owner_label='you'`, from the app) - their windows, re-embedded in the re-pass, become `omi_wearer_samples` (`source='owner_label'`, `status='confirmed'`).
- **Heuristic proposal (owner confirms)**: while no confirmed print exists, each night proposes the windows of the **loudest-nearest voice** (the loudest quartile of the speech windows, largest cosine cluster, up to 20 windows) as `status='proposed'`. A proposal is **never used**. The owner reads what was said in those windows and confirms or rejects (no audio is kept for this):

```bash
python3 scripts/maintenance/zoe-omi-repass.py --wearer-status --user <id>
python3 scripts/maintenance/zoe-omi-repass.py --wearer-lines --user <id> --proposal 2026-11-10    # was that you?  (private; never paste)
python3 scripts/maintenance/zoe-omi-repass.py --confirm-wearer 2026-11-10 --user <id>             # yes   |   --reject-wearer ... = no
```

- A print needs **10 confirmed windows** (`MIN_WEARER_WINDOWS`); below that every line reads `unverified`. Proposals older than 14 days are dropped. Follow-up (not built): ask the owner in the day review UI instead of the CLI.
- Labels: `you` >= 0.45 cosine, `unsure` within 0.12 below, else `other`; a taught person is only a **suggestion** (`suggested_person_id`) at >= 0.45 and 0.08 above the runner-up; Zoe's voice >= 0.55. Names reach the day review only when the owner confirms the suggestion (the existing guardrail: "Sam (you tagged)", never "Sam (suggested)").
- Family and named-guest prints in THIS speaker space start empty: they grow from the owner's tags (`omi_voices.tag` already learns per `model_id`; the re-pass also teaches a person's print from a tag carried over from the first pass). Named guests' PANEL prints (a different microphone and model) cannot be matched in ECAPA space - their NAMES are used in the ASR hint only.

## Invariants (each has a negative control in the tests)

- **Audio is never deleted before the pass has processed it.** With the flag `shadow`/`on`, the first pass sets `repass_state='pending'` and keeps the WAV; `release_tagged_audio` skips pending rows. The hold is bounded by `audio_expires_at` (7 days): past it the WAV goes whether or not the pass ran.
- **Forgotten stays forgotten.** Every new line goes through `_redact_forgotten` (the first pass's own function) and the write is inside `user_erase_gate.writing`: a member erased since the job started gets nothing written and the recording is tombstoned. The hint never contains a forgotten name. The wearer samples are erased with the recording (`drop_recording`) and the member (`omi_voices.forget_user`); the table is in `forget_reach.py`.
- **A human decision survives the pass.** `owner_label`, `tagged_person_id`/`tag_source` and `not_person` on a first-pass segment are carried onto the new segment that mostly overlaps it.
- **A failed pass does not release the audio.** After 3 failed nights a recording goes `failed` (the Moonshine text stands) but its WAV is held to the retention expiry, so a fix can be re-run on it: `zoe-omi-repass.py --requeue-failed`. Clearing `keep_audio` in the app cannot drop a WAV the pass still owes a look either (it is released right after the pass).
- **Redaction happens inside the erase gate.** An entity forget does not move the erase generation, so it is ordered against the write only by the lock; the ledger is applied after the lock is held.
- **An empty answer never erases text.** If the pass hears nothing, the Moonshine text stands (state `shadow`).
- **The pass is best effort.** Timeout / carry-over never fails the night; a missing runtime (rc 3) does (exit 5, loud) and the restore still runs.

## Order within the window

`preflight -> sleep (stops) -> [pre-jobs: omi_repass] -> compact + re-measure -> choose levers -> load the 12B -> digest -> [extract_prepass if #2352 is in] -> dreaming -> night_mind -> struggle_proposals -> self_review -> omi_day_review -> wake`. Both the VAGO extract and the day review read recordings AFTER the re-pass; in mode `on` the day review also skips a recording still waiting for the pass (`PENDING_SQL`: bounded - once its WAV is gone it is no longer waiting), so a carried recording is reviewed the night after it is re-passed, never on the Moonshine text. Modes off/shadow: unchanged.

## Operator steps (nothing here is done by the PR)

```bash
# 1. the runtime (once; needs network; ~5.6 GB: torch ships its CUDA libs)
scripts/setup/setup_omi_repass.sh && scripts/setup/setup_omi_repass.sh --check
# 2. the schema (the normal deploy runs alembic: migration 0072)
# 3. wire the pre-job into the window: the shipped unit passes --pre-jobs omi_repass (re-run scripts/night/install_night_window.sh; it is a no-op while the flag is off)
# 4. go SHADOW (the first-pass WAVs are then held for the pass; zoe-data env, then restart zoe-data):
#       ZOE_OMI_NIGHT_REPASS=shadow          (and ZOE_OMI_SYNC_ENABLED already on)
# 5. next morning: read the counts (never words)
journalctl --user -t zoe-night-window -n 3; grep -h omi_repass ~/.zoe/night-reports/$(date +%F).md
tail -n 5 ~/.zoe/omi-repass/shadow.jsonl         # per recording: words, lines, speakers, agree (word agreement vs Moonshine), model
# 6. confirm your wearer print (above), watch carried/audio_s/worker_rtf for ~a week, then flip: ZOE_OMI_NIGHT_REPASS=on
```

Rollback: set the flag `off` (held WAVs are released by the normal rules; replaced text stays - provenance is in `repass_model`). Remove nothing else.

## Files

`services/zoe-data/omi_repass.py` (decisions, planner, DB), `scripts/night/jobs/omi_repass_worker.py` (the model process), `scripts/maintenance/zoe-omi-repass.py` (the job + owner CLI), `scripts/setup/setup_omi_repass.sh` + `omi_repass_requirements.lock.txt`, migration `0072_omi_night_repass.py`, `night_window.py` (`--pre-jobs`, `build_pre_jobs`, `run_pre_jobs`, `resettle`). Private state: `~/.zoe/omi-repass/` (0600: `state.json` = measured RTF + the Zoe voice reference; `shadow.jsonl` = counts only).
