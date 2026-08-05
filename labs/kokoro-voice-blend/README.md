# kokoro-voice-blend — a unique "Zoe" persona voice (LAB SPIKE)

Kokoro voice identity is a `(510, 1, 256)` float32 style tensor. New voices are
made by weighted linear blends or slerp of existing voice tensors — computed
pure-numpy from `/home/zoe/models/voices-v1.0.bin`, so generating candidates
never loads a second Kokoro next to the live sidecar (the OOM hazard).

**Status:** candidate tensors generated. Nothing here is wired into the live
voice path, and there is currently **no working way to audition, preview, or
deploy a custom blend** — that needs sidecar-side `ZOE_KOKORO_VOICES` support
that does not exist yet (see *Wiring plan* below). Generating the tensors still
works; using the result does not.

## Candidates (pinned recipes in `blend_zoe_voices.py`)

| Name | Recipe | Character |
|---|---|---|
| `zoe_dawn` | linear 0.5 af_sky + 0.5 af_bella | warm + familiar, closest to today |
| `zoe_ember` | linear 0.4 af_heart + 0.4 af_sky + 0.2 af_nova | richer, rounder |
| `zoe_dawn_slerp` | slerp(af_sky, af_bella, t=0.5) | dawn pair, energy-preserving — often crisper |
| `zoe_kore_heart` | slerp(af_heart, af_kore, t=0.35) | heart-forward with a kore tint |
| `zoe_velvet` | linear 0.65 af_sky + 0.35 af_nicole | softest / breathiest |

Committed tensors: `voices/<name>.npy` (float16, ~261 KB each; upcast to
float32 on use). Regenerate byte-identically with the script.

## Auditioning (NOT currently supported)

Generate the candidate tensors:

```bash
python3 labs/kokoro-voice-blend/blend_zoe_voices.py   # tensors only
```

**There is no working audition path for these blended candidates today.** The
in-process ONNX renderer that could synthesise straight from a candidate tensor
was retired along with the `kokoro-onnx` dependency, and the live Kokoro
**PyTorch sidecar** is not a drop-in for it: `scripts/setup/kokoro_sidecar.py`
`_load_pipeline()` never reads `ZOE_KOKORO_VOICES`, so pointing that env at an
augmented bin and restarting `kokoro-tts.service` does **not** load the custom
`zoe_*` blends — the sidecar's `/synthesize` only accepts a voice **name**
already present in the pipeline's own loaded voice set. Auditioning a custom
blend therefore needs the sidecar-side wiring in the deploy step below (teaching
the sidecar to load an augmented bin), which is **not done in this spike**.

To tweak a mix: edit the `CANDIDATES` recipes and rerun — everything is
deterministic from the stock voices bin.

## Wiring plan (deploy step — NOT done in this spike)

Where the live voice comes from today:

- **Sidecar** `scripts/setup/kokoro_sidecar.py` (systemd `kokoro-tts.service`,
  port 10201, Kokoro PyTorch/CUDA) loads voices **by name** through `KPipeline`
  from the `kokoro` package's own bundled voice set; `_load_pipeline()` does
  **not** read `ZOE_KOKORO_VOICES`, so a custom bin path currently has no
  effect. Default voice name from `KOKORO_VOICE` (default `af_sky`).
  `/synthesize` accepts a voice **name** only — and it must already be in the
  pipeline's loaded set, which today is the stock voices only.
- **zoe-data** (`services/zoe-data/tts_waterfall.py`) sends
  `ZOE_KOKORO_VOICE` (default `af_sky`) to the sidecar over HTTP; it holds no
  in-process TTS model of its own.

> **This recipe does NOT work today — it is the intended future flow, not a
> runnable procedure.** It assumes a sidecar that loads an augmented
> `ZOE_KOKORO_VOICES` bin, and the sidecar does not do that yet (see the sidecar
> bullet above). Setting `KOKORO_VOICE=zoe_dawn` now would just name a voice the
> pipeline has never loaded. The missing prerequisite is **step 0**.

Once Jason picks a candidate (say `zoe_dawn`) — *and once the step-0 prerequisite
is done*:

0. **(PREREQUISITE — NOT done)** Teach the sidecar to load an augmented voices
   bin from `ZOE_KOKORO_VOICES` (a code change to `_load_pipeline()`), so the
   `zoe_*` blends actually enter the pipeline's voice set. Until this lands,
   steps 1–5 cannot load or select a custom voice.
1. Build an augmented voices bin (stock voices + candidates):
   `python3 labs/kokoro-voice-blend/blend_zoe_voices.py --emit-bin /home/zoe/models/voices-v1.0-zoe.bin`
2. Point both consumers at it and select the voice (operator env change):
   `ZOE_KOKORO_VOICES=/home/zoe/models/voices-v1.0-zoe.bin`,
   `KOKORO_VOICE=zoe_dawn` (kokoro-tts.service) and
   `ZOE_KOKORO_VOICE=zoe_dawn` (zoe-data env).
3. **Wipe the phrase cache** `~/.zoe/kokoro_cache/` — it is keyed by text only
   and persisted across restarts, so stale entries would speak in the OLD voice.
4. **Replay-gate before deploy (MANDATORY, per AGENTS.md):** run
   `scripts/maintenance/voice_regression_probe.py` (and
   `scripts/perf/measure_tts.py`) against `~/.zoe-voice-samples` under
   `flock /tmp/zoe-voice-harness.lock`; said-vs-did and per-stage speed must
   not regress vs baseline.
5. Operator restarts `kokoro-tts.service` + `zoe-data.service`. Instant
   rollback = revert the two env vars (the stock bin is untouched).

## Forbidden (inherited from `labs/AGENTS.md`)

Not wired into any service/unit/CI; hand-run only. Pure-numpy tensor math —
loads no model.
