---
type: Runbook
title: Kokoro on ONNX Runtime (B5.1) — measured 2026-09-27, NOT a win; do not cut over
description: What the opt-in ONNX Runtime backend for the Kokoro TTS sidecar is, how it was measured against the live PyTorch sidecar on the Orin (RAM, cold start, latency, audio parity), why the numbers do not justify a cutover, and the gated apply/rollback recipe to use only if a later re-measure passes.
tags: [voice, tts, kokoro, onnx, onnxruntime, memory, runbook, b5-1]
timestamp: 2026-09-27T12:30:00Z
---

# Kokoro on ONNX Runtime (B5.1)

**Verdict (2026-09-27): not a win — the live sidecar stays on PyTorch.** The ONNX backend is
merged **opt-in only** (`ZOE_KOKORO_BACKEND=onnx`; the default stays `pytorch`) so the
measurement can be re-run when the blockers below move. The live unit is unchanged.

The tracker's hypothesis was *2.3 GB → ~0.6–1 GB at RTF < 0.3*. Measured on the box:

| | PyTorch KPipeline (live `:10201`) | ONNX fp16, CUDA EP (`:10202`) | ONNX fp32, CPU EP (control) |
|---|---|---|---|
| Resident (VmRSS — on Tegra it **includes NvMap** GPU memory) | 2.07–2.14 GB fresh; **2.7–3.0 GB** after use (NvMap 0.74 → 1.14 GB) | 1.54 GB ready → 1.68 after phrase precache → **1.89 GB** after 60 synths (NvMap 0.35 → 0.65 GB); one ~1,600-char multi-chunk reply grew it past what the box had free | **0.78 GB** |
| Cold start (process → `/health`) | ~17 s (torch import ~10 s + model) | 7.2–9.0 s | 3.3 s model load |
| `/synthesize_stream`, 20 synthetic phrases × 3 (p50 / p95) | 250 / 328 ms | 204 / **482** ms | — |
| …first time a text is seen (the production case — repeats hit the phrase cache) | **317 ms** median | **460 ms** median | — |
| `measure_tts.py` cold first unit (median / p90) | **235 / 261 ms** | **441 / 477 ms** | — |
| RTF on novel text | ~0.08 | 0.107 (fp16) · 0.124 (fp32 on CUDA) | **0.70** — fails the < 0.3 gate |
| Parity vs PyTorch | — | durations **identical** (median ratio 1.000, min 0.994); log-mel similarity median **0.978**; **+1.4 dB** louder | — |
| Silent outputs | 0 | **3 of 32 all-NaN = silence** (e.g. "Tomorrow looks cloudy with a chance of light rain in the afternoon.") | — |

Why it is not a win:

1. **RAM saving is ~0.2–0.4 GB fresh (~1 GB vs a long-running PyTorch), not ~1.5 GB.** Both
   runtimes carry ~1.2–1.4 GB of CPU-side CUDA state (cuDNN/cuBLAS kernel images and handles,
   created on the first run: stepwise probe `session` 0.70 GB → `first_run` 1.38 GB). The ONNX
   graph is small; the CUDA stack is not. ORT's arena also grows with input length.
2. **Slower where it matters.** On text not seen before — every real reply, since repeats are
   served from the phrase cache — ONNX is ~1.4–1.9× slower to first audio (441 vs 235 ms in
   `measure_tts`). `cudnn_conv_algo_search=DEFAULT` helps (377 ms in-process) but does not
   close it; a `kNextPowerOfTwo` arena or EXHAUSTIVE search do not help. G2P is not the cost
   (misaki ≈ 6 ms/phrase) — it is ORT kernel time.
3. **The fp16 graph returns NaN on ~9 % of inputs on the CUDA EP** → silent replies.
   Disqualifying by itself. fp32 on CUDA costs +~160 MB and is slower still (RTF 0.124); its
   NaN rate is **unmeasured** — the probe never got a safe memory window (run it first if fp32
   is ever reconsidered).
4. **The CPU EP is the only large RAM win (0.78 GB) and it misses the speed gate** (RTF 0.70) —
   the same "voice in pieces" failure the retired ONNX/CPU backend had (#1617).

Kept because it worked: exact phoneme/duration parity (misaki G2P + a port of KPipeline's
chunking), byte-compatible endpoints, the shared phrase cache, and the `/health` contract.

## What the backend is

- `scripts/setup/kokoro_onnx_backend.py` — ORT session (CUDA EP with a capped
  `gpu_mem_limit`, `kSameAsRequested` arena, HEURISTIC conv search, never TensorRT; CPU EP on
  request), voices from an NPZ bin, **misaki** G2P (what KPipeline uses) + a port of
  `KPipeline.en_tokenize` so the graph sees the same phonemes and ≤510-phoneme chunks as the
  live voice, style row `pack[len(ps)-1]`, no trimming or inserted pauses (KPipeline adds
  none). ORT silently falls back to CPU, so `device` / `degraded_reason` come from
  `session.get_providers()`, not from the request.
- `scripts/setup/kokoro_sidecar.py` — `ZOE_KOKORO_BACKEND=pytorch|onnx` (default `pytorch`,
  unknown values → `pytorch`). The ONNX path reuses the brain-health wait, the phrase cache,
  `/synthesize`, `/synthesize_stream` and `/health` (which now also carries `"backend"`).
- `scripts/setup/install_kokoro_onnx.sh` — idempotent provisioning into its own uv venv
  (`~/.venvs/kokoro-onnx`, never the system site-packages) plus sha256-verified model files in
  `~/models/kokoro-onnx/`. Touches no service.
- `scripts/perf/kokoro_ab_compare.py` — capture-then-compare A/B (latency p50/p95, duration
  ratio, log-mel similarity, loudness) that writes WAV pairs for a human listen. It uses
  `/synthesize_stream`, which never reads or writes the phrase cache, so it is safe to point at
  the live sidecar. Capture-then-compare means the two engines never need to be resident
  together.

Knobs (`ZOE_KOKORO_ONNX_*`): `MODEL` (default `~/models/kokoro-onnx/kokoro-v1.0.fp16.onnx`),
`VOICES` (falls back to `ZOE_KOKORO_VOICES`, the NPZ zoe-data's voice catalogue lists — so the
`zoe_*` blends would be speakable, which KPipeline cannot do), `PROVIDER` (`cuda`|`cpu`),
`G2P` (`misaki`|`espeak` — espeak is lighter but pronounces differently from the live voice),
`GPU_MEM_LIMIT_MB` (1024), `THREADS` (4).

## Versions (verified on the Orin: JetPack 6, CUDA 12.6, Python 3.10)

- `onnxruntime-gpu==1.24.0` from `https://pypi.jetson-ai-lab.io/jp6/cu126` — **imports and runs
  under numpy 2.2.6**; providers `Tensorrt/CUDA/CPUExecutionProvider`. Harmless noise: a
  `device_discovery … /sys/class/drm/card0/device/vendor` warning on import (Tegra has no DRM
  PCI node) and `ScatterND with reduction=='none'` warnings at session load.
- `kokoro-onnx==0.6.1` installed `--no-deps` (its hard `onnxruntime` CPU dependency would
  shadow the GPU wheel); `espeakng-loader 0.2.4`, `phonemizer-fork 3.3.2`, `misaki 0.9.4`,
  `spacy 3.8.14` + `en_core_web_sm 3.8.0` from the spacy-models wheel URL (`spacy download`
  needs pip, which a uv venv lacks). In the venv misaki imports without torch (~240 MB peak incl.
  spaCy); in the system Python it pulls torch in via thinc.
- Model files: release `model-files-v1.1` (PR #198 re-export: duration output, float `speed`,
  embedded vocab). `kokoro-v1.0.fp16.onnx` 164 MB, fp32 326 MB, int8 114 MB (upstream spectral
  correlation 0.916 vs fp32; its quantised ops are CPU kernels — not pursued).
  `voices-v1.0.bin` is byte-identical to `/home/zoe/models/voices-v1.0.bin`, which the zoe-data
  catalogue already uses, and carries `af_sky`. Same weights as the live KPipeline
  (`hexgrad/Kokoro-82M` v1.0); no newer Kokoro weights exist.

## How it was measured (reproducible)

The box usually cannot hold a second ~1.8 GB Kokoro beside the brain, the live Kokoro and the
agent fleet (steady MemAvailable 0.4–1.2 GB). Every load was therefore gated: MemAvailable
> 2 GB **and** the live `kokoro-tts` up ≥ 90 s with `pipeline_loaded` — never start CUDA while
another process is initialising CUDA: one such collision made the live sidecar's first CUDA
attempt fail with `NvMapMemAllocInternalTagged error 12` (its retry loop recovered 11 s later).
Each run also had a `systemd-run --user --scope` ceiling, a watchdog that SIGKILLs the candidate
if MemAvailable drops below 600 MiB, and `flock /tmp/zoe-voice-harness.lock`. The live
`kokoro-tts`, `llama-server` and `zoe-data` were never stopped or restarted by this work.

```bash
scripts/setup/install_kokoro_onnx.sh                      # venv + models; no service touched
# candidate on a parallel port with a scratch phrase cache (never ~/.zoe/kokoro_cache):
(cd scripts/setup && env -u PYTHONPATH ZOE_KOKORO_BACKEND=onnx KOKORO_SIDECAR_PORT=10202 \
   ZOE_KOKORO_CACHE_DIR=/tmp/kokoro-onnx-cache ~/.venvs/kokoro-onnx/bin/python kokoro_sidecar.py) &
P=scripts/perf/kokoro_ab_compare.py
flock /tmp/zoe-voice-harness.lock python3 $P --capture http://127.0.0.1:10201 --tag pytorch
flock /tmp/zoe-voice-harness.lock python3 $P --capture http://127.0.0.1:10202 --tag onnx
python3 $P --compare pytorch onnx --json ~/.cache/zoe/kokoro-ab/compare.json
ZOE_PERF=1 ZOE_KOKORO_SIDECAR_URL=http://127.0.0.1:10202 \
  python3 scripts/perf/measure_tts.py --replies-file <20 synthetic lines>
sudo cat /sys/kernel/debug/nvmap/iovmm/clients              # per-process GPU memory (read-only)
```

`measure_tts.py` posts to `/synthesize`, so against the live sidecar it leaves its synthetic
phrases in the live phrase cache as 1-hit entries (the first to be evicted). WAV pairs for the
human listen: `~/.cache/zoe/kokoro-ab/NN_pytorch.wav` vs `NN_onnx.wav` (`05_onnx.wav` is the
silent NaN case). **MOS needs a human listen**; the similarity figure is a proxy (same words,
timing and timbre), not a quality score.

## What would change the verdict — re-measure when one of these moves

- **A NaN-safe reduced-precision graph** (a mixed-precision re-export that keeps the decoder's
  overflow-prone ops in fp32) or a newer Jetson ORT wheel. Run the 32-phrase NaN probe first —
  it must return zero silent outputs.
- **ORT latency on novel inputs** within ~10 % of PyTorch's `measure_tts` cold first unit.
- **A smaller CUDA footprint.** The ~1.2 GB CPU-side CUDA state is a floor for either runtime,
  so only a non-cuDNN path, or a CPU path that beats RTF 0.3, changes the arithmetic. The next
  CPU candidate to measure is Moonshine 0.1.5's two-stage Kokoro ORT graph (see §3 of the
  2026-09-26 ecosystem watch).

## Apply / rollback — ONLY after a re-measure passes every gate

Gates: VmRSS after precache + the A/B at least 0.8 GB below PyTorch's after the same workload;
`measure_tts` cold first unit no worse than PyTorch's; zero NaN/silent outputs on the probe set;
duration ratio ≈ 1.0 and similarity ≥ 0.97; a human listen of the WAV pairs; then the replay
gate (`voice_regression_probe.py`, head-bound) in a Kokoro window.

Kokoro-window steps (coordinator/operator). The base unit's memory guards stay as they are:

```bash
scripts/setup/install_kokoro_onnx.sh
mkdir -p ~/.config/systemd/user/kokoro-tts.service.d
cat > ~/.config/systemd/user/kokoro-tts.service.d/onnx.conf <<'EOF'
[Service]
ExecStart=
ExecStart=%h/.venvs/kokoro-onnx/bin/python %h/assistant/scripts/setup/kokoro_sidecar.py
Environment=ZOE_KOKORO_BACKEND=onnx
Environment=ZOE_KOKORO_ONNX_MODEL=%h/models/kokoro-onnx/<the graph that passed>.onnx
Environment=ZOE_KOKORO_ONNX_VOICES=%h/models/kokoro-onnx/voices-v1.0.bin
Environment=PYTHONPATH=
Environment=PYTHONNOUSERSITE=1
EOF
# onnx.conf sorts after the live backend.conf (ZOE_KOKORO_BACKEND=pytorch) and overrides it.
free -m                                   # > 1 GB available before restarting on this box
systemctl --user daemon-reload && systemctl --user restart kokoro-tts
curl -s http://127.0.0.1:10201/health     # backend=onnx, device=cuda, pipeline_loaded=true, no degraded
curl -s http://127.0.0.1:8000/readyz      # dependencies.tts.provider == "kokoro-sidecar"
ZOE_PERF=1 python3 scripts/perf/measure_tts.py --replies-file <lines>   # compare with the table
# Rollback:
rm ~/.config/systemd/user/kokoro-tts.service.d/onnx.conf
systemctl --user daemon-reload && systemctl --user restart kokoro-tts
curl -s http://127.0.0.1:10201/health     # backend=pytorch, device=cuda
```

A cutover PR must also update `docs/CANONICAL.md`'s TTS row (it says *PyTorch on CUDA*) and
re-size the unit's `MemoryLow`/`MemoryMax` from the new measurement.

Related: [Voice pipeline](voice-pipeline.md) ·
[Ecosystem watch 2026-09-26 §3](ecosystem-watch-2026-09-26.md) ·
[numpy 2 on the Jetson](numpy2-jetson-migration.md) · tracker row B5.1 in
[beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md).
