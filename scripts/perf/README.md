# scripts/perf — speed-regression harness

Measure every brain/voice speed change instead of guessing. Two probes, both
read-only against the live services, both CI-gated behind `ZOE_PERF=1`.

The rocks don't move (Gemma 4 E4B+MTP brain, Moonshine v2 Medium STT) — this
harness measures speed and **function** *around* them so a tuning change can't
silently regress either.

## What's here

| Script | Measures | Path |
| --- | --- | --- |
| `measure_speed.py` | Brain **TTFT** + **gen tok/s** (median over N runs), prompt-size configurable | LLM in isolation via `POST /v1/chat/completions` (`stream:true`) |
| `measure_voice.py` | Whole voice path: **stt / resolve / brain / e2e** latency **and said-vs-did correctness** | wraps `services/zoe-data/tests/replay_samples.py` over the saved utterance corpus |

## Running

Both require `ZOE_PERF=1` and a reachable brain (`http://127.0.0.1:11434`).
Without the env var they exit 0 with a skip notice, so CI on a brain-less
runner stays green.

```bash
# Brain probe — warm (KV-cache reuse), the common path:
ZOE_PERF=1 python3 scripts/perf/measure_speed.py --runs 5 --json /tmp/brain.json

# Brain probe — cold prefill (cache-defeating) and full-size (~3000-tok) prompt:
ZOE_PERF=1 python3 scripts/perf/measure_speed.py --runs 3 --cold
ZOE_PERF=1 python3 scripts/perf/measure_speed.py --runs 3 --cold --prompt-tokens 3000

# Voice e2e + correctness (run on the Jetson host; needs the live service .env):
ZOE_PERF=1 python3 scripts/perf/measure_voice.py --last 10 --json /tmp/voice.json
```

`measure_voice.py` defaults to `services/zoe-data` under the repo root. From a
git **worktree** (where `.env` is gitignored and absent) point it at the live
checkout: `--service-dir /home/zoe/assistant/services/zoe-data`.

## Before/after workflow (how to prove a speed change)

1. **Baseline** before touching anything:
   `ZOE_PERF=1 python3 scripts/perf/measure_speed.py --runs 9 --json before.json`
   and `... measure_voice.py --last 20 --json before_voice.json`.
2. Make the brain/voice change. Restart the affected service.
3. **Re-measure** into `after.json` / `after_voice.json` with the **same flags**.
4. Compare medians. TTFT or tok/s materially worse → regression, investigate or
   revert. Voice verdict mix shifting toward `CANT_DO`/`ERROR` → the change
   **broke function** even if it got faster: that's a hard fail.

Use identical `--runs` / `--prompt-tokens` / `--cold` between before and after;
warm vs cold and small vs large prefill are not comparable.

## Safety

- **Read-only.** The brain probe hits the stateless completions endpoint with a
  synthetic prompt. The voice probe runs the replay harness in its **dry**
  default (`allow_writes=False` → writes are planned, never fulfilled); it never
  passes `--execute`, never mutates the DB, and never triggers the consolidation
  sweep. Memory is read for the `jason` user only, to exercise recall.
- Generation is capped (`--max-tokens`, default 64) to keep probes light.

## Baseline — captured 2026-06-25 (Jetson Orin NX, Gemma 4 E4B QAT Q4_K_XL, GPU)

Brain (`measure_speed.py`):

| Scenario | TTFT median | gen tok/s median | total median |
| --- | --- | --- | --- |
| Warm, ~544-tok prompt (5 runs) | **71 ms** | **18.6 tok/s** | 3378 ms |
| Cold prefill, ~544-tok (3 runs, `--cold`) | **182 ms** | 16.9 tok/s | 3649 ms |
| Cold prefill, ~3000-tok (3 runs, `--cold --prompt-tokens 3000`) | **215 ms** | 17.4 tok/s | 3445 ms |

TTFT is dominated by prefill: warm KV reuse ~71 ms; a cold full-size system
prompt is still only ~215 ms. Decode steady-state ~17–19 tok/s.

Voice e2e (`measure_voice.py --last 5`, all turns `OK`):

| Stage | median | p90 | max |
| --- | --- | --- | --- |
| stt (Moonshine) | 678 ms | 2053 ms | 2053 ms |
| resolve (router/fast-tier) | 0 ms | 15 ms | 15 ms |
| brain (Gemma, non-stream oneshot) | 5170 ms | 12083 ms | 12083 ms |
| **e2e (stt+resolve+brain)** | **3863 ms** | 12761 ms | 12761 ms |

Correctness: 5/5 `OK` — no `CANT_DO`/`ERROR`. (The replay brain uses
`brain_oneshot`, a non-streaming full completion, so `brain_ms` is total
generation, not first-token — that's why it's much larger than the isolated
TTFT. First-audio latency in the *live* streaming voice path is bounded by the
~71 ms warm TTFT plus the first TTS chunk, not by full `brain_ms`.)
