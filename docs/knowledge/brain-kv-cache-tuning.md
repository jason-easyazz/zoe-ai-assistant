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
| V-quant **NOT applied**; K stays q8 | FA on **crashes with the MTP draft on this build** (documented at the #810 template sync), and q8 V requires FA (issue #10378) — so V stays f16 while MTP is in use. Cost: only the ~4 global layers, ~64MB at 16384. Revisit only if MTP is ever dropped or the crash is fixed upstream. |
| `--cache-ram 2048` | Oct-2025 host prompt cache (PR #16391): similarity hot-swap of whole cached prompts, **SWA-compatible** — the real replacement for prefix eviction. **Capped** because the 8192 MiB default is an OOM hazard on 15.6G unified memory. The running server today has NO cap — latent hazard until this deploys. |
| `--cache-reuse 256` **removed** | KV shifting cannot reuse past the 512-token SWA window (threshold `pos_next − n_swa`); it was a no-op for gemma3n |

## Apply (operator)

**⚠️ A warm-box restart CRASHES (proven live 2026-07-21, twice).** Once Kokoro
(~2.4G) + the agent stack fill memory, CUDA cannot allocate the brain's ~2.6G
transient load buffer — the unit core-dumps or fails with
`failed to allocate CUDA0 buffer`. Even the OLD config cannot reload on a warm
box. The brain fits because boot order loads it FIRST.

**Blessed path — apply + reboot:**
```bash
cp ~/assistant/scripts/setup/systemd/llama-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
sudo reboot
```

**No-reboot path — park Kokoro to recreate boot order (voice is down either way):**
```bash
cp ~/assistant/scripts/setup/systemd/llama-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user stop kokoro-tts
systemctl --user restart llama-server     # loads in ~12s with the memory free
systemctl --user start kokoro-tts
```
(The cp + daemon-reload are load-bearing: restarting without them relaunches the
OLD unit, and every later verification tests the wrong config.)

**Run the replay gate immediately post-boot, and nowhere else.** Right after
boot there is briefly well over 2G free; run the gate THEN, before the agent
fleet fills memory. Do NOT run it merely because free memory ticks past 2G on
a warm box: the probe loads a second Kokoro (~2.3G) and its transient burst can
crash the ALREADY-LOADED brain even when the probe's own 1500MB floor passes —
producing another CUDA-OOM `error` artifact instead of a verdict (this is the
standing cgroup-guards-don't-cover-CUDA rule). Later in uptime the floor makes
it SKIP anyway, and a skip is NOT a pass. (2026-07-21: the artifact currently
reads `error` from the crash window — it must be re-run green post-boot before
this change counts as done.)

## Verify

1. `curl -s localhost:11434/props | python3 -c 'import json,sys;print(json.load(sys.stdin)["total_slots"])'` → **2**
2. `free -m` — available should be within ~200Mi of pre-restart.
3. Persona alternation: two different ~4k system prompts, alternating turns —
   second visit to each should be **<0.5s**, not ~5s.
4. **Replay gate (MANDATORY — this is the voice path):**
   `flock /tmp/zoe-voice-harness.lock python3 scripts/maintenance/voice_regression_probe.py`
   needs ≥2G free; said-vs-did must not regress.

## Risks & rollback

- FA stays off — the earlier "reason unrecorded" was wrong: the #810 sync note
  records that **FA on crashes with MTP** on this build. Do not flip it while
  `--spec-type draft-mtp` is present.
- Rollback = restore previous unit (git), daemon-reload, restart. All changes
  are serving-config only; the model rock is untouched.

## Not chosen

- TensorRT-LLM on Orin: frozen at v0.12.0-jetson, mainline unsupported — dead end.
- `--swa-full`: defeats the SWA memory savings; checkpoints + cache-ram cover it.
- `--slot-save-path`: viable later for persona snapshots to NVMe if 2 slots prove insufficient.
