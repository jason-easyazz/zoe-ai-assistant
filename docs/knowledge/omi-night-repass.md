---
type: spec
title: The pendant night re-pass - the best transcript the box can make, while Zoe sleeps
description: Flag-dark ZOE_OMI_NIGHT_REPASS=off|shadow|on - a pluggable offline ASR (Qwen3-ASR on the GPU, Parakeet as the budget fallback) plus the panel's own ResNet34 speaker step over the day's pendant audio, run in the night window before the 12B loads; the wearer print, the audio hold, the forget rules, the budget, the owner steps.
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
- [ ] **"Everything is transcribed; the important things get the best transcription."** Qwen3 reads every recording each night, important ones first; the extra voters go to names / numbers / times / commitments first; the morning line says how many minutes got three voters, how many Qwen3 only, and how many carried.
- [ ] **Nothing that was forgotten comes back, and no audio is lost before it is read.** A forgotten name stays `[forgotten]`; an erased member gets nothing written; a recording's audio is kept until the pass has seen it (and never past the 7-day retention).

Each box has a test (`services/zoe-data/tests/test_omi_repass.py`, `tests/unit/test_night_repass_window.py`) or a live check below. The shadow weeks decide `on`.

## What it is

Moonshine (the live STT rock) writes the FIRST transcript minutes after a sync. It is fast and sits between voice turns, but on pendant audio it garbles names and invents text on near-silence. The **night re-pass** is the SECOND transcript, offline, with the 4B brain, Kokoro, the router and zoe-data all stopped (the box has ~12.6 GB free then):

1. **ASR**: **Qwen3-ASR-1.7B** (`Qwen/Qwen3-ASR-1.7B-hf`, transformers 5.17, bfloat16 on the GPU) over VAD chunks of at most 14 s. When the day's audio would overrun the budget the planner drops to **Parakeet-TDT-0.6B-v3** (`nvidia/parakeet-tdt-0.6b-v3`, about 5x faster). No candidate in the bake-off spelt a household name unbiased, and a keyword prompt made one engine invent a name on non-speech, so the engines are **not prompt-biased**: names are normalised afterwards from the lexicon (household first names + the owner's named guests, minus anything on the forget ledger), e.g. Billy -> Billie when that person exists. faster-whisper stays registered as a CPU last resort only.
   (Production runs two more voters and ROVER fuses them; see below.)
2. **Speakers** (replaces ECAPA; see "Who said it" below): the panel's own WeSpeaker ResNet34 int8, per-channel mean removal, one person registry shared with named guests, Zoe first, the wearer prior, and an abstain rule - 97-100 % turn accuracy on the hand-checked turns against 89-95 % for ECAPA and 60 % for the shipped labels.
3. **Store** (mode `on`): the clean transcript and new `omi_segments` replace the Moonshine ones, with provenance (`repass_model`, `repass_at`). Shadow stores nothing and logs counts.

## The seam (pluggable backends) and the bake-off result

The round-2 bake-off (2026-11-09, Sunday audio, this Jetson; lab record `~/.zoe/lab/omi-retranscribe-r2/`) chose the default and the fallback. The seam stays so the next winner is one entry:

| backend id | role | device | measured RTF (per speech second) | process RSS | GPU peak | load |
|---|---|---|---|---|---|---|
| `qwen3-asr` | **default** | GPU | 0.42 | ~8.7 GB | ~4.0 GB | ~14 s |
| `parakeet-tdt` | budget fallback | GPU | 0.08 | ~5.7 GB | ~2.5 GB | ~12 s |
| `whisper-gpu:distil-large-v3` | **third ROVER voter** (production) | GPU (CUDA CTranslate2) | ~0.44 | declared 4.5 GB | - | - |
| `whisper-gpu:large-v3` | evaluation contender | GPU (CUDA CTranslate2) | ~0.74 | declared 5.5 GB | - | - |
| `granite-speech` | evaluation contender | GPU | ~0.47 | ~9.7 GB | ~4.5 GB | ~16 s |
| `faster-whisper:large-v3` / `:distil-large-v3` | CPU last resort | CPU | 1.0 / 0.45 (bake-off round 1) | ~2.1 / 2.4 GB | - | - |

GPU large-v3 reached RTF 0.74 and needs a hand-built CTranslate2, so Whisper is not the default; Gemma 12B's own audio was rejected (PR #2363 stays parked).

- **ASR**: `ZOE_OMI_NIGHT_ASR` (default `qwen3-asr`). Contract: `AsrBackend.transcribe(audio, hint, deadline) -> words [[word, t0, t1, confidence]...]` in `scripts/night/jobs/omi_repass_worker.py` (`ASR` registry). The transformers engines return text per VAD chunk; word times are spread evenly across the chunk with a flat 0.6 confidence, and speakers come from speaker units (so a chunk with two voices is labelled at word granularity only roughly).
- **Speaker units**: `ZOE_OMI_NIGHT_DIAR` (default `resnet34`). Contract: `units(audio, turns) -> [(t0, t1, embedding, rms)]` (`DIAR` registry; a CAM++ seam is another entry). A different embedder needs its own `MODEL_ID` (prints are per speaker space - never mixed).
- **Budget per backend**: `omi_repass.BACKENDS` declares each engine's `rtf`, `gpu_rtf`, `peak_rss_mb`, `gpu_peak_mb`, `device` and `fallback`. The planner, the capped scope's `MemoryMax`, the pre-start RAM check (`need_mb` = peak + 0.4 GB speaker model + the 1.2 GB floor; a box that cannot hold it carries the recordings with status `low_mem`) read those; a night's measured RTF (`~/.zoe/omi-repass/state.json`) overrides the declared one.

## Production voters, ROVER fusion and the contender ledger (owner policy 2026-11-09)

"Run all the strong contenders for a while, until we can safely say one is not needed." So the re-pass is not one engine:

- **Production, every night, on ALL the audio**: Qwen3-ASR (the pivot) -> Parakeet-TDT -> a **third voter**, then **ROVER** over their chunk texts. ROVER needs at least three voters to outvote the pivot (measured on Sunday's audio against a human reference of 56 chunks / 381 words: Qwen3 alone 7.3 % WER, ROVER over the voters 3.7-5.0 %; LLM free-generation fusion did worse, 7.1 %, and LLM slot arbitration only tied ROVER, so **there is no LLM stage**).
- **ROVER rules** (`omi_repass_eval.rover`, adopted from the lab's `fusion/rover.py`): words are aligned to the Qwen3 pivot; majority vote; Qwen3 breaks ties (weight 1.01); an EMPTY hypothesis abstains; an inserted word needs a strict majority of the voters; Granite's "zoe" is ignored unless Qwen3 also says it; with fewer than three voters the pivot's text stands; if EVERY other engine is more than 50 % of words away from Qwen3 on a chunk the chunk is flagged `low_conf` and the Qwen3 text kept. Insertions vote as whole word-sequences per gap (the lab's measured design: two voters inserting different-length runs do not combine into a majority; changing it would change the measured WER, so it is kept and noted). Then the speaker labelling and the name normalisation run as before. Residual supply-chain risk: Python packages are version-pinned (top-level + transitive constraints) over TLS, not hash-locked; every model's weights file IS sha256-pinned. Deterministic CPU work, a few seconds a day. `ZOE_OMI_REPASS_FUSION=rover|primary`.
- **The third voter**, chosen by budget. Measured on the same hand reference (56 chunks / 381 words), ROVER with Qwen3 + Parakeet + X: **Whisper large-v3 4.5 %** (GPU RTF 0.74, ~44 min per speech hour), **distil-large-v3 3.9 %** (RTF ~0.43, ~26 min) - within noise of each other (one or two words), so large-v3, the measured best by the lab's own run, is used **when the night has room, else distil** (`ZOE_OMI_NIGHT_ASR_THIRD` is the preference list; the first that fits 85 % of the budget wins; none fits = two voters = the pivot's text). Adding **Granite as a fourth voter HURTS** (5.0 % keyword-prompted, 5.2 % plain, against 4.5 % without it), so Granite never votes in production: it stays on the evaluation sample. Both Whisper engines need the hand-built CUDA CTranslate2 (`ZOE_OMI_REPASS_CT2_DIR`, default `~/.zoe/ct2build`; see the operator steps): without it the third voter is skipped (`skipped: model_unavailable`) and the night votes with two, i.e. the pivot's text.
- **Evaluation, every night, on a rotating SAMPLE** (never in the stored text): Granite 5.0 and whichever Whisper (large-v3 / distil) did not vote that night, on about 25 minutes of speech (`ZOE_OMI_REPASS_EVAL_MIN`) spread across the day, a different slice each night (a per-night offset). They run only when production finished for every recording with time to spare. Every engine's per-chunk hypothesis is kept in `~/.zoe/omi-repass/hyps/hyps-<night>.jsonl` (private, 0600, 7 days, forget-reached).
- **Contender ledger** (`~/.zoe/omi-repass/ledger.jsonl`, counts only, one line per model per night): chunks run, speech seconds, agreement with the fused output, `sole` (final words whose only voter was this model - for an evaluation contender, the words the vote WOULD have taken from it had it been a voter), `invented` (words only this model produced that the fused text rejected), `changed` (how many words adding it would have changed), and `owner_hit` / `owner_miss`: when the day review lets the owner fix a line it calls `omi_repass_eval.note_owner_correction(home, user, rid, start_ms, end_ms, fixed_text)`, which scores every model's stored hypothesis for that span against the fix as GROUND TRUTH (the seam is built and tested; the day-review hook is the other helper's).
- **Verdicts**: `zoe-omi-repass.py --contenders` prints one plain line per model; on Mondays the night job also emits it as `NIGHT_JOB omi_repass_contenders ...` into the morning report. A model is called a **candidate to drop only after >= 14 nights and >= 3 sampled hours**, when its sole rate is below 1 % AND it adds invented words (>= 1 % of its words); otherwise "not enough data yet", "harmless", or "earns its place: keep". Nothing ever prunes a model: the owner decides.
- **The owner's morning transcript check**: contested ROVER slots (no strict majority) and low-confidence chunks are stored with every model's alternative, the ROVER pick and the vote counts. `omi_repass_eval.check_queue(home, user_id)` returns exactly that (`rid`, `i`, `t0`/`t1`, `hyps`, `rover`, `slots` [{pick, votes, who}], `low_conf`); `zoe-omi-repass.py --check-queue --user ID` prints the counts.

**Budget** (GPU minutes per hour of SPEECH, measured: Qwen3 ~25, Parakeet ~5, distil-large-v3 ~26, Whisper large-v3 ~44, Granite ~28). Production = ~56 min per speech hour with distil as the third voter (~74 with large-v3); the pre-job's budget is about 25 minutes, i.e. ~27 minutes of speech with all three voters. The order when a night is over budget is fixed: **Qwen3 first on everything** (its deadline leaves Parakeet's estimated time and a model load), then **Parakeet on everything**, then **the third voter where time allows**, then the rest carries to tomorrow. If even Qwen3 + Parakeet do not fit the plan, Parakeet-only. The evaluation sample is the last thing to get time. Each GPU model loads ALONE in its own process after a drop-caches + compaction, the 12B loads only after the last one exits. **If real days exceed ~27 speech-minutes, the three-voter production does not fit in 25 minutes**: the shadow weeks measure it; the levers are lowering `NIGHT_PRE_KEEP_MIN` (the 12B stage used 7 of its 28 minutes on 2026-11-08), a longer window, or dropping the third voter - the ledger and the report show which.

## Capacity: Qwen3 on everything, the other voters on what matters (owner decision 2026-11-09)

The owner's answer to "three-voter production covers ~27 min of speech in a ~25 min budget" was "go with your recommendation":
1. **More of the night.** The 12B stage's reserve is measured, not 28 min (`night_window.measured_keep_min`, floor 10, default 12), so the pre-job gets ~40 min.
2. **Everything gets transcribed.** Qwen3 (the pivot, ~7% WER) runs on ALL of the night's audio first, **important recordings first** (`rank_important`); nothing waits behind a voter. Parakeet and the third voter then run
   on as many of the important recordings as the time left reaches (`plan_voters`: the longest prefix that fits all three voters, then Parakeet alone down the list, the rest keep Qwen3 only; a recording needs the best third
   voter that covers everyone, else the one that covers most). **Important** = first-pass text with a household member's / named guest's name (+3), numbers (+2), times or dates (+2), commitment words (+2), or that feeds a pending
   day-review candidate (+4). Only recordings the pivot cannot reach even once carry to the next night.
3. **Measured.** Each night the summary (and the NIGHT_JOB line, so the morning report) carries `speech_min`, `fused3_min`, `fused2_min`, `qwen_only_min`, `carried` and `carried_min`; the same numbers are in `state.json` `last.capacity`.
   That is the evidence for a separate pre-window slot later - not built; the owner's call.

## Where it runs in the window, and why (RAM / GPU arithmetic)

Measured 2026-11-08: after the stops MemAvailable was **12,637 MiB**; the 12B load takes it to **4,220 MiB**; the jobs' low was **3,166 MiB**; the floor is **1,200 MiB**. The default pass needs **Qwen3 ~8.7 GB process RSS (unified memory: the ~4.0 GB of GPU weights are inside it) + 0.4 GB (R34 + VAD) = ~9.1 GB**; the Parakeet fallback ~5.7 + 0.7 = 6.4 GB.

| order | RAM | verdict |
|---|---|---|
| **before** the 12B loads (chosen) | 12.6 GB free - 9.1 = ~3.5 GB left (2.3 GB over the floor), then the process EXITS, the GPU memory is freed and the box is compacted and re-measured before the lever choice | no contention; the 12B still gets its 9.1 GB |
| beside the 12B | 3.2 GB low - 9.1 GB: impossible | no. A mismatched config cannot do it: the window runs the pass only as a pre-job |
| after the 12B | fits, but the day review and the VAGO extract need the CLEAN text and run on the 12B | wrong order |

**GPU recipe** (what made it load after earlier NvMap error-12 failures): with the 4B, Kokoro, the router and zoe-data stopped, `sync; echo 3 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory` IMMEDIATELY before the model loads (`omi_repass.gpu_prep`, run just before the worker starts), the DEFAULT torch allocator (the pass removes any `PYTORCH_CUDA_ALLOC_CONF`), ONE model loaded alone on the GPU (the R34 speaker model runs on the CPU). After the worker exits the window compacts again (`resettle`) before the 12B loads. Torch is the box's Jetson CUDA build (2.8.0 / CUDA 12.6, user site) seen by the venv through a `.pth`; `setup_omi_repass.sh --check` prints whether it sees the GPU (without a GPU the backends run on the CPU, far slower - the budget logic then carries more to the next night). The earlier finding stands for CTranslate2 only: its aarch64 wheel is CPU-only (`--probe-gpu`).

**Time**: the pre-job leaves `NIGHT_PRE_KEEP_MIN` (28 min) for the 12B stage (load + probe ~3, jobs 7 measured to ~20 with a full day review, margin). With the window starting 02:50 and its 65-minute cap that is a re-pass budget of about 25 minutes. The planner estimates `audio_s x rtf` against 85 % of the budget, drops to Parakeet when Qwen3 would overrun, and the worker takes recordings oldest first (recordings the first pass found no words in go last) and starts nothing it is unlikely to finish. **Unfinished recordings are not failures: they stay `pending` and carry to the next night**, bounded by the 7-day retention.

> **Capacity (read before `on`).** At the measured RTF a 25-minute budget (1,275 s usable) covers about 50 minutes of SPEECH with Qwen3, or about 4.4 hours with Parakeet. The pendant records up to ~12 h of audio a day, mostly silence the VAD skips; nobody has measured a real day's speech yet. **The shadow weeks measure it** (`audio_s`, `worker_rtf`, `carried`, `fits` in the job line and the report). If a day does not fit, Parakeet takes over automatically; past that, a longer window (the 04:10 voice gate is the limit) or Moonshine text stands for what carries.

## Who said it (the speaker step: panel ResNet34, mean-removed, abstaining)

Measured in the people-tagging bake-off (`~/.zoe/lab/omi-people-tagging`, hand-checked turns): WeSpeaker R34 (the panel's own model, int8, the same pinned artefact `scripts/setup/zoe_speaker_resnet34.py`) with per-channel mean removal 97-100 % turn accuracy; ECAPA 89-95 %; the shipped labels 60 %. Cost ~2-3 CPU-minutes per audio hour. Decision logic: `omi_repass_who.py`.

1. **Unit**: the ASR's VAD turns. A turn up to 8 s is ONE embedding; a longer turn gets 1.5 s windows (hop 0.75) that vote per word. Never 20 s slabs.
2. **Mean removal, per channel**: pendant turns AND pendant prints minus the mean of the night's turn embeddings (a session with >= 10 min of speech); a short session uses the stored per-device mean (`omi_channel_means`, stored with `model_id` + channel + device). Panel prints minus the mean of the registry's panel prints (the panel clips themselves are not kept; with fewer than three prints a mean would zero the only reference, so the STORED registry panel mean is used). The pendant mean is per (member, DEVICE): two pendants never share an offset.
3. **One person registry** shared with named guests: `learned_voiceprints` (a panel print and a pendant print per person, same model). A person's print is the panel print until the pendant print has >= 6 s of CONFIRMED speech (the owner's tags), then both fused; pendant prints keep the existing leave-one-out outlier rejection (cos < 0.3). Children stay label-only.
4. **Zoe first**: a turn overlapping a Zoe reply in the panel log is Zoe (exact; the seam `omi_voices.zoe_spoken_spans`), the Zoe print is the fallback; removed before deciding between people. **The seam is filled (below, "Panel anchors"): with `ZOE_OMI_PANEL_ANCHORS=night` the panel's account decides who is Zoe and the print is only the fallback when that account is unavailable.**
5. **Wearer**: the prior is the device owner's print, mean-removed - the CONFIRMED pendant print, else the owner's consented panel profile, which can only make a turn "you?" (leaning). Loudness is a +0.03 tie-breaker only (it measured weak, AUC ~0.75).
6. **Abstain**: label only when the margin over the runner-up is >= 0.10 (>= 0.20 under 1.2 s); >= 0.05 is "X?" (leaning); otherwise "?"; and a winner must score >= 0.25 (a wearer turn is not "the closest registered person"). `ZOE_OMI_REPASS_MARGIN` / `_MARGIN_SHORT` / `_LEAN` / `_MIN_SCORE`.
7. **"Who said this?"**: the "?" and "X?" turns are stored as `who_said` items in the check queue (`omi_repass_eval.check_queue(..., kind="who_said")`: `rid`, `seg_id`, `t0`/`t1`, `mark` lean | unknown, `cands` = [[person id | "you", score]] best first) and their embeddings are held (TTL as the first pass's) so the owner's tap works: `omi_repass.confirm_who(db, user, seg_id, person_id)` adds that turn to the person's PENDANT print through `omi_voices.tag` (the member's own person, the learning rules, never a child), or, for "you", records a confirmed wearer sample and labels the line "me". A recognised voice is only ever a SUGGESTION; a tap is the owner's word.

## Panel anchors (who was Zoe, and who the panel already knew) - `omi_panel_anchor.py`

The owner found a guest's line TO Zoe ("Well, Zoe, I've got some issues") labelled Zoe: the seam returned nothing and the Zoe voice print decided. The seam is now filled from
the panel's own records, with the measured rule (validated on one real evening: 11 Zoe turns at overlap 0.94-1.00; every non-Zoe turn of more than 4 words scored below 0.6;
two labels fixed). `ZOE_OMI_PANEL_ANCHORS=off|night|on` (default **off**; `night` = the re-pass only, one bounded ssh read of the Pi's `voice.log`; `on` also reads it in the first
pass, a network call in the upload path, so not recommended).

- **Sources.** Zoe's replies and the panel turns' STT text: the Flue session store (`labs/flue-zoe-brain-2x/data/zoe-brain.db`, read-only: `assistant_text_delta` joined per block,
  `user_message`); each panel turn's **per-clip** speaker verdict: the Pi's `voice.log` speaker lines (`scripts/lib/panel_voice_log.py`) joined to the STT text by time - the
  CLIP's verdict, never the session's (a guest's session can hold the owner's clip); the saved clip: `~/.zoe-voice-samples`. The logged `voice_greeting` prefix can be handed to
  the first reply (`panel_evidence(greeting=...)`).
- **The night clock offset** (panel time = pendant time + ~75-95 s, drifting) is measured per recording: log-mel pattern correlation of the panel clips against the pendant audio
  (`correlate_shift`, +-60 s around `ZOE_OMI_ANCHOR_PRIOR_S`=88), robust median (`estimate_offset`, needs >= 2 agreeing clips within 12 s). No offset, no anchors: an unaligned clock never decides
  who is Zoe. Only a `device` / `corrected` recording clock is aligned; an `estimated` one never is.
- **The rule** (`AlignRule`, env `ZOE_OMI_ANCHOR_*`): candidates are pendant turns inside the panel event's window +-30 s after the offset. Normalise (lowercase, punctuation out, spoken numbers
  to digits). A turn is **ZOE only** if a sentence of it has >= 6 words and >= 85% of its words appear in one Zoe reply; mixed turns are **split at sentence boundaries** and each sentence is
  tested alone (time shared by word count). A turn that **names Zoe** is never Zoe. A turn that is not Zoe is never Zoe - the print no longer makes her once the panel log covers the recording
  (`build_lines(zoe_by_log=True)`); the print stays the fallback only when the panel record is unavailable. Otherwise a turn matching a panel utterance at >= 0.6 similarity with >= 3 words takes
  that panel speaker as a **strong anchor, above the voice match** (unless the panel verdict was unknown). Anything else keeps the voice-based label.
- **Limits, stated plainly.** The panel `voice.log` carries the MEMBER claim only: a named guest anchors only through the `guest_verdict` hook (not wired to a store yet); another member without a
  `people` row is not anchored (no key to give). The greeting prefix is a parameter, not yet read from a log. The correlation threshold (`z >= 15`) is checked on synthetic audio, not yet on a
  real night. Flip `night` only after a shadow night shows the anchors agreeing with the owner's corrections.

## Clean read and the book format - `omi_repass_clean.py`, `omi_transcript_book.py`

A DISPLAY layer (flag `ZOE_OMI_CLEAN_READ=off|on`, default off; migration 0075 adds `omi_segments.clean_text` / `clean_how`). Each turn gets a lightly cleaned twin beside the
verbatim; **memory, extraction, the day review, search and the ledger read the verbatim only** (a test pins that none of those modules mentions `clean_text`). One T=0 request per turn on
the 12B (the window job `omi_cleanread`, before the day review, best effort, capped 10 minutes; by hand `zoe-omi-cleanread.py --model-url <4B>` or `--fallback-url` when the 12B stage was
skipped or fails). Turns under 6 words use the deterministic cleaner only (fillers um/uh/ah/er/hmm/mm out, immediate 1-3 word repeats collapsed, "like" out only when sentence-initial or before a comma).

**The code guard** (`guard`): the clean words must be an in-order subsequence of the verbatim (case, punctuation, apostrophes and number spelling ignored), at most 15% dropped, no dropped
run longer than 4 words. A violation falls back to the deterministic cleaner, then to the verbatim shown as "(lightly cleaned)". The night's NIGHT_JOB line carries counts only and the violation rate;
**above 20% (with at least 10 turns sent) the line ends `ALERT=...` and the morning report prints it at the top.** Unreachable-model turns are counted separately, never as violations.
Forgotten names are redacted from the twin by the same ledger/erase gate as the verbatim. Seam for later: `Suggestion` / `suggest_hook` (a whole-conversation correction pass handing "did you
mean ...?" items to the morning check) - nothing calls it; that pass is not built.

**Book format** (`zoe-omi-transcript.py --user U --date D --out <dir outside the repo>`): speaker in bold then a paragraph, consecutive same-speaker turns merged, no inline timestamps, a scene
break "- 2:41 am -" at each recording and at gaps over 60 s, uncertain turns in italics ("Someone (Name?)" leaning, "Someone" unknown), the verbatim with times as an appendix. The morning
review reads `omi_transcript_book.render(await load_day(...))`; nothing in the PR yet calls it from the morning review itself.

## Wearer reference (the panel print does not match the pendant)

The panel owner print matches the pendant channel at cos ~0.04. The pendant-channel wearer print is built only from **windows the owner has vouched for**:

- **Seed (automatic)**: first-pass segments the owner already labelled "me" (`omi_segments.owner_label='you'`, from the app) - their windows, re-embedded in the re-pass, become `omi_wearer_samples` (`source='owner_label'`, `status='confirmed'`).
- **Heuristic proposal (owner confirms)**: while no confirmed print exists, each night proposes the windows of the **loudest-nearest voice** (the loudest quartile of the speech windows, largest cosine cluster, up to 20 windows) as `status='proposed'`. A proposal is **never used**. The owner reads what was said in those windows and confirms or rejects (no audio is kept for this):

```bash
python3 scripts/maintenance/zoe-omi-repass.py --wearer-status --user <id>
python3 scripts/maintenance/zoe-omi-repass.py --wearer-lines --user <id> --proposal 2026-11-10    # was that you?  (private; never paste)
python3 scripts/maintenance/zoe-omi-repass.py --confirm-wearer 2026-11-10 --user <id>             # yes   |   --reject-wearer ... = no
```

- The confirmed pendant print needs **10 confirmed units** (`MIN_WEARER_WINDOWS`); below that every line reads `unverified`. Proposals older than 14 days are dropped. Follow-up (not built): ask the owner in the day review UI instead of the CLI.
- Labels: see "Who said it" (margin rules); a taught person is only a **suggestion** (`suggested_person_id`). Names reach the day review only when the owner confirms the suggestion (the existing guardrail: "Sam (you tagged)", never "Sam (suggested)").
- Family and named-guest prints are the panel's own (named guests' panel prints and the owner-taught pendant prints share this model, so they are USED here); pendant prints grow: they grow from the owner's tags (`omi_voices.tag` already learns per `model_id`; the re-pass also teaches a person's print from a tag carried over from the first pass). Named guests' panel prints are matched (mean-removed per channel) and fuse with a pendant print once the owner has confirmed 6 s of them.

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
# 0. the CUDA CTranslate2 build (the third voter and the large-v3 contender): it is hand-built in the lab; give the re-pass a stable path to it (once)
ln -s ~/.zoe/lab/omi-retranscribe-r2/ct2build ~/.zoe/ct2build      # or set ZOE_OMI_REPASS_CT2_DIR; without it those engines are skipped, loudly, and the night votes with fewer
# 1. the runtime (once; needs network; the models are ~11 GB incl. Granite; torch is the box's existing Jetson CUDA build)
scripts/setup/setup_omi_repass.sh --seed-from ~/.zoe/lab/omi-retranscribe-r2/models   # or without --seed-from to download the pinned revisions
scripts/setup/setup_omi_repass.sh --check                  # must say: gpu ok, imports ok, both models ok
# 2. the schema (the normal deploy runs alembic: migration 0075)
# 3. wire the pre-job into the window: the shipped unit passes --pre-jobs omi_repass (re-run scripts/night/install_night_window.sh; a no-op while the flag is off)
# 4. THE DAYTIME REHEARSAL (above) - repeat until it passes
# 5. go SHADOW (the first-pass WAVs are then held for the pass; zoe-data env, then restart zoe-data):
#       ZOE_OMI_NIGHT_REPASS=shadow          (and ZOE_OMI_SYNC_ENABLED already on)
# 6. next morning: read the counts (never words)
journalctl --user -t zoe-night-window -n 3; grep -h omi_repass ~/.zoe/night-reports/$(date +%F).md
tail -n 5 ~/.zoe/omi-repass/shadow.jsonl         # per recording: words, lines, speakers, agree (word agreement vs Moonshine), model
# 7. confirm your wearer print (above), watch carried/audio_s/worker_rtf for ~a week, then flip: ZOE_OMI_NIGHT_REPASS=on
# 8b. panel anchors (after a shadow night): ZOE_OMI_PANEL_ANCHORS=night; the clean read: ZOE_OMI_CLEAN_READ=on (re-run scripts/night/install_night_window.sh for the new job)
# 8. the contender ledger: python3 scripts/maintenance/zoe-omi-repass.py --contenders        (the Monday morning report carries the same line)
```

Rollback: set the flag `off` (held WAVs are released by the normal rules; replaced text stays - provenance is in `repass_model`). Remove nothing else.

## Files

`services/zoe-data/omi_repass.py` (decisions, planner, DB), `scripts/night/jobs/omi_repass_worker.py` (the model process), `scripts/maintenance/zoe-omi-repass.py` (the job + owner CLI), `services/zoe-data/omi_repass_eval.py` (ROVER, the ledger, the verdicts, the hypothesis store, the check queue), `scripts/setup/setup_omi_repass.sh` + `omi_repass_requirements.lock.txt` + `omi_repass_constraints.txt`, migration `0074_omi_night_repass.py`, `night_window.py` (`--pre-jobs`, `--rehearse-repass`, `build_pre_jobs`, `run_pre_jobs`, `resettle`). Private state: `~/.zoe/omi-repass/` (0600: `state.json` = measured RTF + the Zoe voice reference; `shadow.jsonl` = counts only).
