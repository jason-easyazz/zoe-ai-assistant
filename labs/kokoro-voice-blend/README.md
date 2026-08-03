# kokoro-voice-blend — a unique "Zoe" persona voice (LAB SPIKE)

Kokoro voice identity is a `(510, 1, 256)` float32 style tensor. New voices are
made by weighted linear blends or slerp of existing voice tensors — computed
pure-numpy from `/home/zoe/models/voices-v1.0.bin`, so generating candidates
never loads a second Kokoro next to the live sidecar (the OOM hazard).

**Status:** candidates generated. Nothing here is wired into the live voice
path; Jason auditions and picks first.

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

## How to audition

Generate the candidate tensors, then audition through the **live Kokoro
sidecar** (the only synthesiser — the in-process ONNX renderer was retired
with its ONNX dependency):

```bash
python3 labs/kokoro-voice-blend/blend_zoe_voices.py   # tensors only
```

Build an augmented voices bin (below), point the sidecar at it via
`ZOE_KOKORO_VOICES`, restart `kokoro-tts.service`, then use the touch panel's
"Zoe's voice" **Preview** to hear each candidate by name. The sidecar's
`/synthesize` accepts a voice **name** only, so a candidate must be in the
loaded bin to be auditioned.

To tweak a mix: edit the `CANDIDATES` recipes and rerun — everything is
deterministic from the stock voices bin.

## Wiring plan (deploy step — NOT done in this spike)

Where the live voice comes from today:

- **Sidecar** `scripts/setup/kokoro_sidecar.py` (systemd `kokoro-tts.service`,
  port 10201, Kokoro PyTorch/CUDA) reads the voice catalogue from
  `ZOE_KOKORO_VOICES=/home/zoe/models/voices-v1.0.bin`; default voice name
  from `KOKORO_VOICE` (default `af_sky`). `/synthesize` accepts a voice
  **name** only — it must exist in the loaded voices bin.
- **zoe-data** (`services/zoe-data/tts_waterfall.py`) sends
  `ZOE_KOKORO_VOICE` (default `af_sky`) to the sidecar over HTTP; it holds no
  in-process TTS model of its own.

Once Jason picks a candidate (say `zoe_dawn`):

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
