---
type: runbook
title: Brain KV-cache tuning — 2 warm slots + host prompt cache
description: Evidence, apply steps, verification and rollback for the 2026-07-21 llama-server config change (ctx 16384 / parallel 2 / q8 KV / cache-ram cap / FA on / cache-reuse removed).
---

# Brain KV-cache tuning (2026-07-21)

## The problem, measured live

- Persona-sized prompt (~4.4k tok), cold prefix: **~5.1s TTFT**. Warm: **0.2–0.4s**.
- At `--parallel 1` a single slot means voice/chat/session alternation evicts the
  prefix → up to ~5s re-process per switch. This was the documented reason the
  earlier `--parallel 2` attempt was reverted ("box lacks RAM for ctx 16384").

## Why the memory objection is obsolete (verified two ways)

GGUF metadata (`gemma4`): 42 layers, `shared_kv_layers=18` → only 24 store KV;
5:1 sliding pattern → only ~4 global layers scale with ctx; SWA layers cap at a
**512-token window**. llama.cpp's SWA-aware cache (PR #13194, default) sizes SWA
layers `PAD(n_swa·n_seq_max + n_batch)` — independent of `-c`. So 8192→16384
costs tens of MB, halved again by q8 V-quant. Build `f449e0553` (2026-06-20) is
after the Gemma `--swa-full` fix (#22288, 2026-04-24) and has `--cache-ram`.

## The changes (scripts/setup/systemd/llama-server.service)

| Change | Why |
|---|---|
| `--ctx-size 16384 --parallel 2` | two 8192 slots; voice+chat keep separate warm prefixes and no longer queue |
| `--cache-type-v q8_0` (K already q8) | halves the global-layer KV |
| `--flash-attn on` (was **off**) | REQUIRED for V-quant (llama.cpp refuses otherwise, issue #10378) |
| `--cache-ram 2048` | Oct-2025 host prompt cache (PR #16391): similarity hot-swap of whole cached prompts, **SWA-compatible** — the real replacement for prefix eviction. **Capped** because the 8192 MiB default is an OOM hazard on 15.6G unified memory. The running server today has NO cap — latent hazard until this deploys. |
| `--cache-reuse 256` **removed** | KV shifting cannot reuse past the 512-token SWA window (threshold `pos_next − n_swa`); it was a no-op for gemma3n |

## Apply (operator)

```bash
cp ~/assistant/scripts/setup/systemd/llama-server.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user restart llama-server
# ExecStartPost blocks until /health ok (120s)
```

## Verify

1. `curl -s localhost:11434/props | python3 -c 'import json,sys;print(json.load(sys.stdin)["total_slots"])'` → **2**
2. `free -m` — available should be within ~200Mi of pre-restart.
3. Persona alternation: two different ~4k system prompts, alternating turns —
   second visit to each should be **<0.5s**, not ~5s.
4. **Replay gate (MANDATORY — this is the voice path):**
   `flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py`
   needs ≥2G free; said-vs-did must not regress.

## Risks & rollback

- **`--flash-attn on` is the one real risk** — it was explicitly `off` before
  (reason unrecorded; possibly historical SM87 issues). If generation is garbled
  or /health fails: rollback below, then retry with `-fa on` but f16 V
  (`--cache-type-v f16`) to isolate FA from V-quant.
- Rollback = restore previous unit (git), daemon-reload, restart. All changes
  are serving-config only; the model rock is untouched.

## Not chosen

- TensorRT-LLM on Orin: frozen at v0.12.0-jetson, mainline unsupported — dead end.
- `--swa-full`: defeats the SWA memory savings; checkpoints + cache-ram cover it.
- `--slot-save-path`: viable later for persona snapshots to NVMe if 2 slots prove insufficient.
