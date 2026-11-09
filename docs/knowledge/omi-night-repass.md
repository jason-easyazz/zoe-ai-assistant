---
type: spec
title: The pendant night re-pass - the best transcript the box can make, while Zoe sleeps
description: Flag-dark ZOE_OMI_NIGHT_REPASS=off|shadow|on - a pluggable offline ASR (Qwen3-ASR on the GPU, Parakeet as the budget fallback) plus ECAPA speaker windows over the day's pendant audio, run in the night window before the 12B loads; the wearer print, the audio hold, the forget rules, the budget, the owner steps.
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

1. **ASR**: **Qwen3-ASR-1.7B** (`Qwen/Qwen3-ASR-1.7B-hf`, transformers 5.17, bfloat16 on the GPU) over VAD chunks of at most 14 s. When the day's audio would overrun the budget the planner drops to **Parakeet-TDT-0.6B-v3** (`nvidia/parakeet-tdt-0.6b-v3`, about 5x faster). No candidate in the bake-off spelt a household name unbiased, and a keyword prompt made one engine invent a name on non-speech, so the engines are **not prompt-biased**: names are normalised afterwards from the lexicon (household first names + the owner's named guests, minus anything on the forget ledger), e.g. Billy -> Billie when that person exists. faster-whisper stays registered as a CPU last resort only.
2. **Speakers**: ECAPA embeddings of 1.5 s windows (0.75 s hop) over the speech spans, matched against (a) the wearer's pendant-channel print, (b) Zoe's own TTS voice (kokoro cache; matched lines are marked `not_person='zoe'`), (c) voices the owner taught Zoe in this speaker space.
3. **Store** (mode `on`): the clean transcript and new `omi_segments` replace the Moonshine ones, with provenance (`repass_model`, `repass_at`). Shadow stores nothing and logs counts.

## The seam (pluggable backends) and the bake-off result

The round-2 bake-off (2026-11-09, Sunday audio, this Jetson; lab record `~/.zoe/lab/omi-retranscribe-r2/`) chose the default and the fallback. The seam stays so the next winner is one entry:

| backend id | role | device | measured RTF (per speech second) | process RSS | GPU peak | load |
|---|---|---|---|---|---|---|
| `qwen3-asr` | **default** | GPU | 0.42 | ~8.7 GB | ~4.0 GB | ~14 s |
| `parakeet-tdt` | budget fallback | GPU | 0.08 | ~5.7 GB | ~2.5 GB | ~12 s |
| `faster-whisper:large-v3` / `:distil-large-v3` | CPU last resort | CPU | 1.0 / 0.45 (bake-off round 1) | ~2.1 / 2.4 GB | - | - |

GPU large-v3 reached RTF 0.74 and needs a hand-built CTranslate2, so Whisper is not the default; Gemma 12B's own audio was rejected (PR #2363 stays parked).

- **ASR**: `ZOE_OMI_NIGHT_ASR` (default `qwen3-asr`). Contract: `AsrBackend.transcribe(audio, hint, deadline) -> words [[word, t0, t1, confidence]...]` in `scripts/night/jobs/omi_repass_worker.py` (`ASR` registry). The transformers engines return text per VAD chunk; word times are spread evenly across the chunk with a flat 0.6 confidence, and speakers come from ECAPA windows (so a chunk with two voices is labelled at word granularity only roughly).
- **Diarisation**: `ZOE_OMI_NIGHT_DIAR` (default `ecapa`). Contract: `windows(audio, speech) -> [(t0, t1, embedding, rms)]` (`DIAR` registry). A different embedder needs its own `MODEL_ID` (prints are per speaker space - never mixed).
- **Budget per backend**: `omi_repass.BACKENDS` declares each engine's `rtf`, `gpu_rtf`, `peak_rss_mb`, `gpu_peak_mb`, `device` and `fallback`. The planner, the capped scope's `MemoryMax`, the pre-start RAM check (`need_mb` = peak + 0.7 GB speaker model + the 1.2 GB floor; a box that cannot hold it carries the recordings with status `low_mem`) read those; a night's measured RTF (`~/.zoe/omi-repass/state.json`) overrides the declared one.

## Where it runs in the window, and why (RAM / GPU arithmetic)

Measured 2026-11-08: after the stops MemAvailable was **12,637 MiB**; the 12B load takes it to **4,220 MiB**; the jobs' low was **3,166 MiB**; the floor is **1,200 MiB**. The default pass needs **Qwen3 ~8.7 GB process RSS (unified memory: the ~4.0 GB of GPU weights are inside it) + 0.7 GB (ECAPA + VAD) = ~9.4 GB**; the Parakeet fallback ~5.7 + 0.7 = 6.4 GB.

| order | RAM | verdict |
|---|---|---|
| **before** the 12B loads (chosen) | 12.6 GB free - 9.4 = ~3.2 GB left (2.0 GB over the floor), then the process EXITS, the GPU memory is freed and the box is compacted and re-measured before the lever choice | no contention; the 12B still gets its 9.4 GB |
| beside the 12B | 3.2 GB low - 9.4 GB: impossible | no. A mismatched config cannot do it: the window runs the pass only as a pre-job |
| after the 12B | fits, but the day review and the VAGO extract need the CLEAN text and run on the 12B | wrong order |

**GPU recipe** (what made it load after earlier NvMap error-12 failures): with the 4B, Kokoro, the router and zoe-data stopped, `sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory` IMMEDIATELY before the model loads (`omi_repass.gpu_prep`, run just before the worker starts), the DEFAULT torch allocator (the pass removes any `PYTORCH_CUDA_ALLOC_CONF`), ONE model loaded alone on the GPU (ECAPA runs on the CPU). After the worker exits the window compacts again (`resettle`) before the 12B loads. Torch is the box's Jetson CUDA build (2.8.0 / CUDA 12.6, user site) seen by the venv through a `.pth`; `setup_omi_repass.sh --check` prints whether it sees the GPU (without a GPU the backends run on the CPU, far slower - the budget logic then carries more to the next night). The earlier finding stands for CTranslate2 only: its aarch64 wheel is CPU-only (`--probe-gpu`).

**Time**: the pre-job leaves `NIGHT_PRE_KEEP_MIN` (28 min) for the 12B stage (load + probe ~3, jobs 7 measured to ~20 with a full day review, margin). With the window starting 02:50 and its 65-minute cap that is a re-pass budget of about 25 minutes. The planner estimates `audio_s x rtf` against 85 % of the budget, drops to Parakeet when Qwen3 would overrun, and the worker takes recordings oldest first (recordings the first pass found no words in go last) and starts nothing it is unlikely to finish. **Unfinished recordings are not failures: they stay `pending` and carry to the next night**, bounded by the 7-day retention.

> **Capacity (read before `on`).** At the measured RTF a 25-minute budget (1,275 s usable) covers about 50 minutes of SPEECH with Qwen3, or about 4.4 hours with Parakeet. The pendant records up to ~12 h of audio a day, mostly silence the VAD skips; nobody has measured a real day's speech yet. **The shadow weeks measure it** (`audio_s`, `worker_rtf`, `carried`, `fits` in the job line and the report). If a day does not fit, Parakeet takes over automatically; past that, a longer window (the 04:10 voice gate is the limit) or Moonshine text stands for what carries.

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

## Daytime rehearsal - must pass before `ZOE_OMI_NIGHT_REPASS` goes to `shadow`

Owner rule (2026-11-09): no night job's first run is unattended at night. `night_window.sh --rehearse-repass` runs the REAL path once, in the daytime, as a short owner-approved run (about 5 minutes; Zoe is silent for it, like the night):

```bash
date +%F > ~/.zoe/agent-tools/voice-day-ok      # the owner's OK to lift the daytime voice pause for today (night_busy.sh voice)
scripts/night/night_window.sh --rehearse-repass
rm ~/.zoe/agent-tools/voice-day-ok
```

It refuses 01:50-04:55 and while `night_busy.sh voice` is busy; takes the brain-window lock; stops zoe-data, the router, Kokoro and the 4B (the night's stop order); compacts; runs `zoe-omi-repass.py --rehearse` (the same plan, manifest, GPU recipe, capped scope and worker as the night, on ONE recording - the newest 20-180 s one with its audio still on disk, or `--recording ID` - whatever the flag says); compacts again; and restores in the night's order (4B alone until healthy, then Kokoro, the router, zoe-data, each health-checked). It writes NOTHING: no segments, no state, no shadow log, no audio released, no attempt counted. It prints counts only: status `rehearsal_ok`, model, `worker_rtf`, `peak_rss_mb`, `gpu_peak_mb`, load time, words / lines / speakers / agreement with Moonshine. A rehearsal that does not pass exits non-zero (window exit 5). Pass = `rehearsal_ok`, the restore healthy, and `worker_rtf` / `gpu_peak_mb` near the table above.

## Operator steps (nothing here is done by the PR)

```bash
# 1. the runtime (once; needs network; the models are ~6.3 GB; torch is the box's existing Jetson CUDA build)
scripts/setup/setup_omi_repass.sh --seed-from ~/.zoe/lab/omi-retranscribe-r2/models   # or without --seed-from to download the pinned revisions
scripts/setup/setup_omi_repass.sh --check                  # must say: gpu ok, imports ok, both models ok
# 2. the schema (the normal deploy runs alembic: migration 0072)
# 3. wire the pre-job into the window: the shipped unit passes --pre-jobs omi_repass (re-run scripts/night/install_night_window.sh; a no-op while the flag is off)
# 4. THE DAYTIME REHEARSAL (above) - repeat until it passes
# 5. go SHADOW (the first-pass WAVs are then held for the pass; zoe-data env, then restart zoe-data):
#       ZOE_OMI_NIGHT_REPASS=shadow          (and ZOE_OMI_SYNC_ENABLED already on)
# 6. next morning: read the counts (never words)
journalctl --user -t zoe-night-window -n 3; grep -h omi_repass ~/.zoe/night-reports/$(date +%F).md
tail -n 5 ~/.zoe/omi-repass/shadow.jsonl         # per recording: words, lines, speakers, agree (word agreement vs Moonshine), model
# 7. confirm your wearer print (above), watch carried/audio_s/worker_rtf for ~a week, then flip: ZOE_OMI_NIGHT_REPASS=on
```

Rollback: set the flag `off` (held WAVs are released by the normal rules; replaced text stays - provenance is in `repass_model`). Remove nothing else.

## Files

`services/zoe-data/omi_repass.py` (decisions, planner, DB), `scripts/night/jobs/omi_repass_worker.py` (the model process), `scripts/maintenance/zoe-omi-repass.py` (the job + owner CLI), `scripts/setup/setup_omi_repass.sh` + `omi_repass_requirements.lock.txt`, migration `0072_omi_night_repass.py`, `night_window.py` (`--pre-jobs`, `--rehearse-repass`, `build_pre_jobs`, `run_pre_jobs`, `resettle`). Private state: `~/.zoe/omi-repass/` (0600: `state.json` = measured RTF + the Zoe voice reference; `shadow.jsonl` = counts only).
