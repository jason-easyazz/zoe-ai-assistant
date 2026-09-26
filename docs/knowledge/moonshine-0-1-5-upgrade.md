---
type: Runbook
title: Moonshine 0.1.5 upgrade (B1.10) — HELD after measurement; API deltas, keyterms, install/replay/rollback
description: What changes for Zoe's STT call sites when moonshine-voice goes 0.0.62 → 0.1.5 (rock unchanged — still Moonshine v2 Medium / MEDIUM_STREAMING), the ZOE_MOONSHINE_KEYTERMS biasing flag, and the box-first install + replay-gate + rollback sequence.
tags: [voice, stt, moonshine, upgrade, runbook, b1-10]
timestamp: 2026-09-26T12:00:00Z
---

# Moonshine 0.1.5 upgrade (B1.10) — HELD

> **Status 2026-09-26: HELD, box rolled back to 0.0.62.** The runbook below was executed the same
> day: 0.1.5 passes said-vs-did but **fails the per-stage speed rule** (+43 % median STT, ~1.9× on
> every non-trivial file, Orin CPU). Numbers, method and retest conditions are in §8. Everything
> else on this page stands as the readiness record — the `ZOE_MOONSHINE_KEYTERMS` plumbing is
> merged dormant, `requirements.txt` pins the version the box actually runs (0.0.62).

**API verdict (2026-09-26, measured in a throwaway venv on the Orin, aarch64 / Python 3.10):
compatible.** Every call Zoe makes into `moonshine_voice` has the same signature and return
shape on 0.1.5 as on the installed 0.0.62. The rock does not move: the arch is still
`ModelArch.MEDIUM_STREAMING` = Moonshine v2 Medium (English streaming), and
`warm_moonshine` — the loader marker `docs/CANONICAL.md` pins — is untouched. What changes
on the box is (a) a **new model bundle download** on first load, (b) speculative decoding
**on by default**, and (c) an **opt-in** `set_keyterms` biasing step behind
`ZOE_MOONSHINE_KEYTERMS` (default empty = off). Program item:
[`beat-the-bar-2026-program.md`](../architecture/beat-the-bar-2026-program.md) B1.10;
context: [state-of-zoe review §5.2](state-of-zoe-review-2026-09-25.md);
the path itself: [voice-pipeline.md](voice-pipeline.md).

Not verified here, and deliberately: no model was loaded (the 0.1.5 bundle is not cached on
the box and the box had ~520 MiB available), so whether `set_keyterms` measurably helps on
Jason's corpus is exactly what the with/without replay in §5 answers.

## 1. Zoe's call sites (all in `services/zoe-data/routers/voice_tts.py`)

| Zoe calls | 0.0.62 | 0.1.5 | Change for Zoe |
|---|---|---|---|
| `mv.ModelArch.MEDIUM_STREAMING` (+ `getattr(mv.ModelArch, ZOE_MOONSHINE_ARCH)`) | `TINY, BASE, TINY_STREAMING, BASE_STREAMING, SMALL_STREAMING, MEDIUM_STREAMING` | identical members | none |
| `mv.get_model_for_language("en", arch)` → `(model_path, arch)` | `(lang, arch, *, cache_root)` | `(lang, arch, *, cache_root, on_progress, include_word_timestamps=False)` | none (new kwargs optional); **downloads a new bundle**, §2 |
| `Transcriber(model_path, arch)` | `(model_path, model_arch, update_interval=0.5, options=None, spelling_model_path=None)` | identical | none |
| `tr.transcribe_without_streaming(audio, sr)` → `Transcript` | `(audio_data, sample_rate=16000, flags=0)` | identical | none |
| `out.lines[i].text` (+ `out.text` fallback) | `TranscriptLine.text` | `TranscriptLine.text` unchanged; `has_speaker_id/speaker_id/speaker_index` replaced by `speaker_spans` | none — Zoe reads only `.text` |
| `moonshine_voice.utils.load_wav_file(path)` → `(list[float], int)` | same | same (still no resample; `_prepare_audio_for_moonshine` keeps closing that gap) | none |
| `warm_moonshine`, `_prewarm_stt_on_wake` | — | — | unchanged (same functions, same calls) |

Things that changed upstream but Zoe never used: `DialogFlow` → `AgentFlow`
(`dialog_flow.py` → `agent_flow.py`); the ctypes header version `20000 → 30000` (internal to
the bundled `libmoonshine.so`); the `on_line_speakers_changed` listener; diarization models
now download on demand (`get_diarization_model`); `EmbeddingModel` public type; TTS/Kokoro
changes inside the package (Zoe's Kokoro is the separate PyTorch sidecar, not this).

**Do not version-gate.** The installed 0.0.62 reports `moonshine_voice.__version__ ==
"0.1.0"` (a hardcoded string). The code feature-detects
`hasattr(Transcriber, "set_keyterms")` instead; `importlib.metadata.version("moonshine-voice")`
is used only for the log line.

## 2. Model files: where MEDIUM_STREAMING lives now

Cache root is unchanged: `~/.cache/moonshine_voice/` (platformdirs user cache), folder =
download URL with the scheme stripped.

| | 0.0.62 (present on the box) | 0.1.5 (first load will fetch) |
|---|---|---|
| URL | `download.moonshine.ai/model/medium-streaming-en/quantized` | `download.moonshine.ai/model/medium-streaming-en/quantized_26_08_21` |
| dir | `…/model/medium-streaming-en/quantized/` (~430 MB on disk) | `…/model/medium-streaming-en/quantized_26_08_21/` |
| files | `adapter.ort 3.5M`, `cross_kv.ort 11M`, `decoder_kv.ort 139M`, `decoder_kv_with_attention.ort 139M`, `encoder.ort 90M`, `frontend.ort 45M`, `streaming_config.json`, `tokenizer.bin` | `adapter.ort`, `cross_kv.ort`, `decoder_kv.ort`, `encoder.ort`, **`frontend.model.ort` + `frontend.weights.ort`** (split in 0.1.5), `streaming_config.json`, `tokenizer.bin`; `decoder_kv_with_attention.ort` only with `include_word_timestamps=True` (Zoe never passes it) |

Expect roughly **290 MB** of fresh download (the same components minus the 139 MB
attention decoder, at 0.0.62 sizes; 0.1.1's "improved quantization" may shift that a little).
The old `quantized/` directory is left in place — it is the rollback's model, delete it only
after §7. Both are already `.ort` (ONNX Runtime flatbuffer) files: the 0.1.1 "ORT-only
models" break does not bite Zoe, which never loaded `.onnx` models through this package.

**Consequence for the restart:** with no pre-download, the FIRST `warm_moonshine` after the
upgrade is a ~290 MB network fetch on the startup path (the warmup is non-fatal and
`/readyz` shows `stt.loaded: false` until it lands). §5 step 3 pre-downloads outside the
service so the restart is a normal one.

## 3. The wheel on this box

- `moonshine_voice-0.1.5-py3-none-manylinux_2_31_aarch64.whl` (18.5 MB) installs cleanly on
  the Orin (Ubuntu 22.04 / glibc 2.35 / CPython 3.10). Declared deps: `filelock, numpy,
  platformdirs, requests, sounddevice, tqdm` — the same six as 0.0.62 (the "numpy + sounddevice
  only" note in the review was optimistic; nothing new is pulled in, `numpy` stays 1.26.4).
- ONNX Runtime is **private to the wheel**: `libmoonshine.so` (now 12 MB, was 36 MB) links
  `moonshine_voice.libs/libonnxruntime-ab8c4363.so.1`. The `onnxruntime==1.23.2` pip pin in
  `requirements.txt` is for Zoe's own ONNX use (Silero VAD etc.) and is unaffected either way.
- Licence: 0.1.5 makes the **models MIT by default** at every size and language (the code was
  MIT already). English Medium Streaming was usable before; the change removes the
  non-commercial Community License caveat. No change to Zoe's posture.

## 4. `keyterms` — the API, its limits, and Zoe's flag

```python
Transcriber.set_keyterms(keyterms: Optional[Sequence[str]]) -> None   # 0.1.2+
Transcriber.set_context(context: Optional[str], max_terms: int = 0) -> None  # 0 → 200
# load-time strength: Transcriber(..., options={"keyterm_boost": "<float>"})
```

- `set_keyterms` REPLACES the previous list; `None`/`[]` turns biasing off. Takes effect on
  the next transcription, never rewrites emitted text, may be called while a stream runs.
- **Streaming architectures only** — `TINY`/`BASE` raise `MoonshineError`. `MEDIUM_STREAMING`
  qualifies.
- **Terms must not contain commas** (the comma is the library's own delimiter; a term with one
  raises). Case matters: the transcript reproduces the capitalisation you pass.
- **No hard maximum in the wheel.** Vendor guidance: keep the list curated — "thousands of
  terms hurt accuracy"; `set_context` caps itself at 200 and warns that a long list "costs
  accuracy on the words you did not ask for". For a household, think **tens of terms**:
  people, rooms, device names, the odd surname. `keyterm_boost` is undocumented beyond its
  name (the native log prints "Compiled N key terms for contextual biasing (boost X.XX)"); leave
  it at the default for the A/B.
- Other load-time option keys present in the native library (for the record; Zoe sets none):
  `use_speculative_decoding` (default on since 0.1.1 — `"false"` disables it without a
  downgrade), `decode_incomplete_lines` (0.1.3, default true), `word_timestamps`,
  `identify_speakers` + `diarization_model_dir`, `use_cuda`, `max_tokens_per_second`,
  `max_extension_seconds`. Zoe has no options plumbing today; adding
  `ZOE_MOONSHINE_OPTIONS` is a follow-up only if §5 shows speculative decoding needs turning off.

**Zoe's flag — `ZOE_MOONSHINE_KEYTERMS`** (`services/zoe-data/routers/voice_tts.py`,
`.env.example`, flag inventory):

- comma-separated; parsed by `typed_env.env_list` (stripped, empties dropped) then
  de-duplicated in order — so no term can carry a comma by construction;
- **default empty = off**: `set_keyterms` is never called and the transcriber is built exactly
  as on 0.0.62;
- applied ONCE, right after the singleton loads in `_ensure_moonshine` (restart to change);
- **feature-detected**: on a `Transcriber` without `set_keyterms` (0.0.62) the list is logged as
  unsupported and ignored — the same code runs on both versions;
- **best-effort**: a refused list logs a WARNING and the rock keeps its transcriber; only a real
  load failure still raises and sets `moonshine_error()`;
- visible at `GET /readyz` → `dependencies.stt.keyterms = {configured, applied, supported,
  error}`. `supported: false` with `configured > 0` means the box is still on 0.0.62.

Pinned by `services/zoe-data/tests/test_moonshine_keyterms.py` (`ci_safe`, fakes the module,
loads no model, carries the "construction failure still raises" negative control).

## 5. Coordinator runbook (box first, file second — the `requirements.txt` header rule)

Preconditions: quiet box (no training window, no other replay), ≥ 2 GB headroom for the
replay (`free -m`), the `flock /tmp/zoe-voice-harness.lock` discipline, and a fresh
baseline for the current 0.0.62 path. Every replay below is **relative** (warm harness,
stops before TTS — see voice-pipeline.md); the numbers that matter are said-vs-did
(`ok_rate`, `fail`) and the `stt` stage delta.

1. **Baseline on 0.0.62 (skip if today's baseline is already green).**
   `flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py --samples 20`
   — must be green. If the path is known-good and the baseline is stale, `--update-baseline`.
2. **Install on the host** (this is the host site-packages the live STT imports from —
   operator-authorised; nothing else installs it, `deploy.yml`'s pip list does not include it):
   `pip3 install --user --upgrade-strategy only-if-needed "moonshine-voice==0.1.5"`
   then `pip3 show moonshine-voice | head -2` and
   `python3 -c "import moonshine_voice.transcriber as t; print(hasattr(t.Transcriber,'set_keyterms'))"` → `True`.
   `numpy` must still be 1.26.x afterwards (`python3 -c "import numpy; print(numpy.__version__)"`).
3. **Pre-download the bundle without loading a model** (network + ~300 MB disk, tiny RAM):
   `python3 -c "import moonshine_voice as mv; print(mv.get_model_for_language('en', mv.ModelArch.MEDIUM_STREAMING))"`
   → path ends in `medium-streaming-en/quantized_26_08_21`; `du -sh` it.
4. **Restart zoe-data** (`systemctl --user restart zoe-data`; poll `/health`, then `/readyz`
   until `dependencies.stt.loaded: true`; expect `keyterms: {configured: 0, applied: 0,
   supported: true}`; `~/.zoe-logs/` shows `Moonshine STT warmup completed in …s` — note the
   number against the previous boot's).
5. **Replay WITHOUT keyterms** (the pure-upgrade gate):
   `flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py --samples 20 --stt inprocess`
   with `ZOE_MOONSHINE_KEYTERMS` unset. Green = no said-vs-did regression and `stt` not
   slower than baseline (speculative decoding should make it faster, −28% decode in vendor
   numbers on a MacBook; on the Orin's CPU treat anything not slower as a pass).
   A previously-working command that now fails is a bug: stop, do not proceed to 6, roll back (§7).
6. **Replay WITH keyterms** (the feature A/B, same corpus, same box):
   `ZOE_MOONSHINE_KEYTERMS="Jason,Zoe,<household names>,<rooms>,<device names>" flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py --samples 20 --stt inprocess`
   The probe's in-process replay inherits the environment, so the list applies inside the
   harness process (the live service is untouched). Compare the two `stt` transcript sets: the
   list earns its place only if it fixes misheard names without breaking anything else. To
   adopt: put the line in `services/zoe-data/.env` (an `EnvironmentFile` of `zoe-data.service`),
   restart, confirm `/readyz` shows `applied == configured`, then re-run once with
   `--stt remote` so the LIVE transcriber is what got measured.
7. **Record.** `--update-baseline` on the accepted configuration; the `requirements.txt` pin
   (`moonshine-voice==0.1.5`) is already in this PR and stops reading as MISMATCH in
   `requirements_drift_check.py` the moment step 2 is done; flip B1.10 to ✅ in the program
   tracker; after a few quiet days delete the old bundle
   (`rm -rf ~/.cache/moonshine_voice/download.moonshine.ai/model/medium-streaming-en/quantized`, ~430 MB).
   For the PR's own `voice-gate`, the probe must run against a checkout of the PR head
   (`--service-dir ~/.worktrees/b1-10-moonshine/services/zoe-data`) so the artifact binds to
   that revision.

## 6. RAM / latency expectations (from the changelog — measure, do not assume)

- 0.1.1: speculative decoding on by default, Medium Streaming update pass ~103 ms → ~74 ms on
  a MacBook Pro; "streaming transcription backs off when falling behind realtime".
- 0.1.2: wheels rebuilt, "8–15% faster streaming".
- 0.1.5: streaming models "reuse mapped `.ort` bytes instead of copying" — if anything, a
  lower resident footprint; the split frontend cuts the streaming-frontend download ~75%.
- Nothing upstream quantifies the speculative-decoding RAM cost. Compare the zoe-data RSS
  after the warmup (`systemctl --user status zoe-data`, `scripts/maintenance/zoe_ground_truth.sh`)
  with the previous boot; the box has no headroom to absorb a surprise
  (`reference_voice_stack_memory_protection`).

## 7. Rollback

- **Whole upgrade:** `pip3 install --user "moonshine-voice==0.0.62"`; unset
  `ZOE_MOONSHINE_KEYTERMS`; `systemctl --user restart zoe-data`. The 0.0.62 bundle is still in
  `…/quantized/` (do not delete it before §5 step 7), so no download; the code path is the same
  file — the keyterms step feature-detects its absence and stays quiet.
- **Feature only:** `ZOE_MOONSHINE_KEYTERMS=` (empty) + restart. Speculative decoding cannot be
  toggled from Zoe today (no options plumbing) — that is the `ZOE_MOONSHINE_OPTIONS` follow-up in §4.
- The `requirements.txt` pin should follow the box either way (box first, file second).

## 8. Measured 2026-09-26 — HELD (rolled back to 0.0.62)

Runbook §5 was executed by the coordinator (install → pre-download → restart → replay).
Outcome per rule:

| Gate | Result |
|---|---|
| Said-vs-did (replay, in-process, keyterms OFF) | **PASS** — 13/13 OK, 7 EMPTY (identical to baseline) |
| Said-vs-did (in-process, keyterms ON) | **PASS** — 13/13 OK, 7 EMPTY |
| Said-vs-did (remote, live service) | **PASS** — 13/13 OK, 7 EMPTY |
| Per-stage speed (`stt`) | **FAIL** — live remote replay medians 546–619 ms on 0.1.5 vs 315–406 ms on 0.0.62 |

**Engine-only A/B** (`stt_bench`: `get_model_for_language` → `Transcriber` →
`transcribe_without_streaming` per file; isolated venvs per version; same 20 newest corpus
files, all 16 kHz, ~1.3 s each; 2 passes, warm, `nice -n 5`, Orin CPU; per-file ms):

| Configuration | median | p90 |
|---|---|---|
| 0.0.62 (run 1 / repeat) | **284 / 286 ms** | 885 / 929 ms |
| 0.1.5 default (speculative decoding on) | 408 ms (**+43 %**) | 1439 ms |
| 0.1.5, `use_speculative_decoding=false` | 508 ms (worse) | 1902 ms |
| 0.1.5 library + the OLD `quantized/` bundle | 382 ms | 1184 ms |

Per-file it is ~1.9× on every non-trivial clip (333→626, 1204→2816, 294→676, 460→879,
634→1297 ms). The old-bundle row shows **most of the cost is the 0.1.5 runtime/library, not
the moved model files**, and disabling speculative decoding makes it slower still, so the
headline latency feature does not pay for the regression on this CPU. Transcripts differed on
9/20 files, mixed: some better ("What's the time?" vs "Was it time?", "Turn the wall off" vs
"To love"), some worse ("The UAV is true" vs "destroyed") — a wash on this corpus, not a
reason to absorb 1.9×.

**Decision:** per the replay-gate rule (per-stage speed must not regress) 0.1.5 is **not
adopted**. Rollback (§7) was performed and confirmed: `pip3 install --user
moonshine-voice==0.0.62`, `ZOE_MOONSHINE_KEYTERMS` unset, zoe-data restarted; the 0.0.62 bundle
was still in `…/quantized/` so no download; `/readyz` reports `stt.loaded: true`,
`keyterms.supported: false` (the plumbing feature-detects its absence, as designed).
`requirements.txt` now pins `moonshine-voice==0.0.62` — reality, box-first.

**Why it is slower — what the wheel says (strings/symbols only; not benchmarked further):**

- The bundled ONNX Runtime carries the same auditwheel name in both wheels
  (`moonshine_voice.libs/libonnxruntime-ab8c4363.so.1`) but is **NOT the same file** — the
  sha256 differs (`29f74af7…` in 0.1.5 vs `2723a421…` in 0.0.62). So the ORT build itself is a
  candidate variable alongside `libmoonshine.so` (36 MB → 12 MB) and how it drives ORT.
- 0.1.5's `libmoonshine.so` carries a **new** `ort_maybe_force_single_thread(const OrtApi*,
  OrtSessionOptions*)` and a **new** environment-variable string `MOONSHINE_ORT_SINGLE_THREAD`;
  neither exists in 0.0.62. There is no other thread/CPU option string (`intra_op` / `inter_op` /
  `num_threads` / `OMP_*` do not appear; `SileroVad::init_engine_threads(int,int)` is present in
  both and belongs to the VAD). Nothing on the Python side reads that variable and it is not a
  `Transcriber` option. Disassembled (aarch64): the function calls `getenv`, returns
  immediately when the variable is **unset**, empty, or starts with `0`, and only then forces the
  single-thread session options — i.e. it is an **opt-in** single-thread switch, OFF by default.
  It does not explain the default-configuration slowdown; it is a knob for the retest (set it to
  `1` in the isolated venv to see whether *fewer* threads help on this CPU — ORT's spin-wait on
  a small model can cost more than it saves).
- Hypothesis to retest against, not a conclusion: the ORT build and/or the decode loop in
  `libmoonshine.so` changed its CPU threading behaviour between the versions, which on a 6-core
  Orin would show exactly as a uniform ~1.9× per-file cost that the bundle swap does not move.

**Retest conditions (any one re-opens B1.10):**

1. The next `moonshine-voice` release (> 0.1.5) — rerun the engine-only A/B first (cheap, no
   service restart), then §5 only if it is at parity with 0.0.62 on the Orin.
2. Evidence that a thread/CPU knob explains it — e.g. upstream documenting
   `MOONSHINE_ORT_SINGLE_THREAD` or an intra-op thread option; then A/B 0.1.5 with that knob set
   the other way in the isolated venv before touching the box.
3. An upstream changelog entry about aarch64 / CPU decode performance.

The engine-only A/B is the instrument for all three: same 20 files, 2 passes, warm, per-file ms,
compare medians and the per-file ratios — a median alone hides a bimodal result.
