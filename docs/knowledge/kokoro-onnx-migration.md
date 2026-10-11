---
type: Record
title: Kokoro on ONNX Runtime (B5.1) — measured 2026-09-27, NOT a win; the code stays on its branch
description: The B5.1 measurement of an ONNX Runtime backend for the Kokoro TTS sidecar against the live PyTorch sidecar on the Orin (RAM, cold start, latency, audio parity), why the numbers do not justify a cutover, what would change the verdict, and the apply/rollback recipe to use only if a later re-measure passes. The backend code was never merged — it lives on branch feat/b5-1-kokoro-onnx (draft PR #1715, closed).
tags: [voice, tts, kokoro, onnx, onnxruntime, memory, measurement, b5-1]
timestamp: 2026-09-27T12:30:00Z
---

# Kokoro on ONNX Runtime (B5.1)

**Verdict (2026-09-27): not a win — the live sidecar stays on PyTorch CUDA.** This page is the
only place the measurement is kept; `main` carries just the one-paragraph verdict in
[beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md) (B5.1).

**What is and is not on `main`.** The opt-in ONNX Runtime backend that produced these numbers
(`scripts/setup/kokoro_onnx_backend.py`, `scripts/setup/install_kokoro_onnx.sh`,
`scripts/perf/kokoro_ab_compare.py`, and a `ZOE_KOKORO_BACKEND=pytorch|onnx` dispatch in
`kokoro_sidecar.py`) **lived on branch `feat/b5-1-kokoro-onnx` (draft PR #1715) and was not
merged.** `main` has no ONNX code path in the Kokoro sidecar (the earlier ONNX/CPU backend was
removed in #1617) and no `ZOE_KOKORO_BACKEND` switch. Everything below that names those files,
flags or the `:10202` candidate describes the branch, kept so the measurement can be re-run or
the backend revived if the blockers below move. The live unit was never changed.

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

Worth keeping from the experiment because it worked: exact phoneme/duration parity (misaki G2P
+ a port of KPipeline's chunking), byte-compatible endpoints, the shared phrase cache, and the
`/health` contract.

## What the backend was (branch `feat/b5-1-kokoro-onnx` only — not on `main`)

- `scripts/setup/kokoro_onnx_backend.py` — ORT session (CUDA EP with a capped
  `gpu_mem_limit`, `kSameAsRequested` arena, HEURISTIC conv search, never TensorRT; CPU EP on
  request), voices from an NPZ bin, **misaki** G2P (what KPipeline uses) + a port of
  `KPipeline.en_tokenize` so the graph sees the same phonemes and ≤510-phoneme chunks as the
  live voice, style row `pack[len(ps)-1]`, no trimming or inserted pauses (KPipeline adds
  none). ORT silently falls back to CPU, so `device` / `degraded_reason` come from
  `session.get_providers()`, not from the request.
- `scripts/setup/kokoro_sidecar.py` (branch version) — `ZOE_KOKORO_BACKEND=pytorch|onnx`
  (default `pytorch`, unknown values → `pytorch`). The ONNX path reused the brain-health wait,
  the phrase cache, `/synthesize`, `/synthesize_stream` and `/health` (which also carried
  `"backend"`).
- `scripts/setup/install_kokoro_onnx.sh` — idempotent provisioning into its own uv venv
  (`~/.venvs/kokoro-onnx`, never the system site-packages) plus sha256-verified model files in
  `~/models/kokoro-onnx/`. Touched no service.
- `scripts/perf/kokoro_ab_compare.py` — capture-then-compare A/B (latency p50/p95, duration
  ratio, log-mel similarity, loudness) that wrote WAV pairs for a human listen. It used
  `/synthesize_stream`, which never reads or writes the phrase cache, so it was safe to point
  at the live sidecar. Capture-then-compare means the two engines never need to be resident
  together.

Knobs on the branch (`ZOE_KOKORO_ONNX_*`): `MODEL` (default
`~/models/kokoro-onnx/kokoro-v1.0.fp16.onnx`), `VOICES` (falls back to `ZOE_KOKORO_VOICES`, the
NPZ zoe-data's voice catalogue lists — so the `zoe_*` blends would be speakable, which
KPipeline cannot do), `PROVIDER` (`cuda`|`cpu`), `G2P` (`misaki`|`espeak` — espeak is lighter
but pronounces differently from the live voice), `GPU_MEM_LIMIT_MB` (1024), `THREADS` (4).

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

## How it was measured (reproducible from the branch)

The box usually cannot hold a second ~1.8 GB Kokoro beside the brain, the live Kokoro and the
agent fleet (steady MemAvailable 0.4–1.2 GB). Every load was therefore gated: MemAvailable
> 2 GB **and** the live `kokoro-tts` up ≥ 90 s with `pipeline_loaded` — never start CUDA while
another process is initialising CUDA: one such collision made the live sidecar's first CUDA
attempt fail with `NvMapMemAllocInternalTagged error 12` (its retry loop recovered 11 s later).
Each run also had a `systemd-run --user --scope` ceiling, a watchdog that SIGKILLs the candidate
if MemAvailable drops below 600 MiB, and `flock /tmp/zoe-voice-harness.lock`. The live
`kokoro-tts`, `llama-server` and `zoe-data` were never stopped or restarted by this work.

These commands only work from a checkout of `feat/b5-1-kokoro-onnx` (the scripts are not on
`main`):

```bash
scripts/setup/install_kokoro_onnx.sh                      # venv + models; no CUDA, no service touched

# Everything that starts or uses the second CUDA process runs inside ONE lock: memory gate,
# capped candidate, both captures, measure_tts, watchdog, cleanup. Nothing is left running
# between steps, and nothing CUDA-related happens outside the lock.
REPLIES=/path/to/20-synthetic-lines.txt                   # synthetic text only
export REPLIES
# The `&&` on the next line is deliberate: --compare runs ONLY if the whole locked block exited 0
# (both captures and measure_tts finished). Any failure inside stops the block (set -e).
flock /tmp/zoe-voice-harness.lock bash <<'EOF' && python3 scripts/perf/kokoro_ab_compare.py --compare pytorch onnx --json ~/.cache/zoe/kokoro-ab/compare.json
set -euo pipefail
P=scripts/perf/kokoro_ab_compare.py
UNIT=kokoro-onnx-cand
avail_mib() { awk '/^MemAvailable:/ {print int($2 / 1024)}' /proc/meminfo; }
loaded() { curl -fsS "$1/health" 2>/dev/null | grep -E '"pipeline_loaded": *true' >/dev/null; }

# Gate 1: headroom. Refuse (start nothing) at or below 2 GB MemAvailable.
[ "$(avail_mib)" -gt 2048 ] || { echo "refusing: MemAvailable $(avail_mib) MiB <= 2048"; exit 1; }

# Gate 2: the live sidecar is up >= 90 s with its pipeline loaded, so two CUDA inits never overlap.
loaded http://127.0.0.1:10201 || { echo "refusing: live kokoro-tts not healthy"; exit 1; }
started=$(systemctl --user show kokoro-tts -p ActiveEnterTimestampMonotonic --value)
up_s=$(awk -v s="$started" '{print int($1 - s / 1000000)}' /proc/uptime)
[ "$up_s" -ge 90 ] || { echo "refusing: live kokoro-tts up only ${up_s}s"; exit 1; }

# Cleanup on ANY exit (success, failure, Ctrl-C, watchdog kill): no orphan CUDA process.
# Best-effort by design: `|| true` is only here, and a failed cleanup never masks the real status.
WD=""
cleanup() {
  if [ -n "$WD" ]; then kill "$WD" 2>/dev/null || true; fi
  systemctl --user kill --signal=SIGKILL "$UNIT.scope" 2>/dev/null || true
  systemctl --user stop "$UNIT.scope" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 1' INT TERM      # a signal (including the watchdog's) is a FAILURE, then cleanup runs

# Candidate: capped scope (hard memory ceiling, no swap), parallel port, scratch phrase cache
# (never ~/.zoe/kokoro_cache).
ROOT=$PWD
systemd-run --user --scope --unit="$UNIT" -p MemoryMax=2500M -p MemorySwapMax=0 \
  bash -c 'cd "$1/scripts/setup" && exec env -u PYTHONPATH ZOE_KOKORO_BACKEND=onnx \
    KOKORO_SIDECAR_PORT=10202 ZOE_KOKORO_CACHE_DIR=/tmp/kokoro-onnx-cache \
    ~/.venvs/kokoro-onnx/bin/python kokoro_sidecar.py' _ "$ROOT" &

# Watchdog: if MemAvailable drops below 600 MiB, or after 15 minutes total, SIGKILL the candidate
# AND fail the run: SIGTERM to the main shell ($$ is the main shell's pid even in this subshell)
# hits the `exit 1` trap above, so the block exits non-zero and --compare never runs.
(
  deadline=$((SECONDS + 900))
  while sleep 1; do
    if [ "$(avail_mib)" -lt 600 ] || [ "$SECONDS" -ge "$deadline" ]; then
      echo "watchdog: killing candidate and failing the run" >&2
      systemctl --user kill --signal=SIGKILL "$UNIT.scope" || true
      kill -TERM "$$"
      exit 0
    fi
  done
) &
WD=$!

# Wait for the candidate (bounded), then measure while it is the only second CUDA process.
ready=0
for _ in $(seq 1 120); do
  if loaded http://127.0.0.1:10202; then ready=1; break; fi
  sleep 2
done
[ "$ready" -eq 1 ] || { echo "candidate never loaded its pipeline"; exit 1; }

# From here any failing command ends the block (set -e): a failed capture or measure_tts is a
# failed run, not a partial one.
python3 $P --capture http://127.0.0.1:10201 --tag pytorch
python3 $P --capture http://127.0.0.1:10202 --tag onnx
ZOE_PERF=1 ZOE_KOKORO_SIDECAR_URL=http://127.0.0.1:10202 \
  python3 scripts/perf/measure_tts.py --replies-file "$REPLIES"
# Per-process GPU memory (read-only). Only when passwordless sudo exists; if it does and the
# read fails, the run fails.
if sudo -n true 2>/dev/null; then sudo -n cat /sys/kernel/debug/nvmap/iovmm/clients; fi
EOF
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

This recipe needs the backend code from the branch to be landed first (it is not on `main`).

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
