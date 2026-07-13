# functiongemma-feasibility — FunctionGemma-270M as a complete-call fast-tier router (LAB SPIKE)

Feasibility check (RAM / latency / **stock** accuracy / cold-load) for
[FunctionGemma-270M](https://blog.google/innovation-and-ai/technology/developers-tools/functiongemma/)
(Google's function-calling Gemma-3-270M) as a tiny always-available router that
emits a **complete function call** (tool + args) so common commands skip the
~4.8 s Gemma-4-E4B brain turn. **No fine-tuning in this spike** — this sizes the
fine-tune win. Successor question to `labs/needle-benchmark/` (Cactus Needle
26M, verdict REJECT), reusing its 81-case corpus and 20-tool schema verbatim.

LAB-ONLY: nothing wired into the voice path, `fast_tiers`, `zoe-data`, systemd,
or CI. The test server ran on **:11435 CPU-only** (plus one full-offload run),
never touching the live brain on :11434.

## VERDICT: **GO** for the fine-tune follow-up

Stock full-schema accuracy (33%) loses to the current 61.7% routing baseline —
expected, and exactly why the follow-up is a fine-tune — but every structural
disqualifier that killed Needle is absent:

- **Latency is in the target band stock**: ~0.7 s p50 CPU-only / ~0.4 s
  full-GPU on the full 2,235-token schema, ~0.4 s / ~0.16 s on a 3-tool block —
  vs Needle's 2–4 s (and vs the 4.8 s brain).
- **No context ceiling**: all 21 tools fit (Needle truncated 8 of 21).
- **Chat discipline stock**: 0% chat false-positives at Q8 (Needle: 75–100%
  misroute — its disqualifier).
- **The decoder is already strong when selection is easy**: 93.8% overall /
  100% canonical / 75% paraphrase on a 3-tool block at Q8 CPU. The gap is
  tool *selection* among 21 schemas — precisely what functional-token
  fine-tuning targets (literature: stock ~58% → fine-tuned ~85%, distil labs;
  our stock number on our harder 21-tool schema is 33%).

## Measured numbers

Box: Jetson Orin NX 16 GB, live services running (zoe-data, brain :11434,
Kokoro) — numbers are conservative. llama.cpp build:
`/home/zoe/llama.cpp/build-jetson-new/bin/llama-server` (b-series w/ ggml 0.15.2).
GGUFs: **`unsloth/functiongemma-270m-it-GGUF`** → `Q4_K_M` (253 MB) and `Q8_0`
(292 MB), kept at `/home/zoe/models/lab/functiongemma-270m-it-{Q4_K_M,Q8_0}.gguf`.

Invocation (exact cmd also embedded in each `results/*.json`):

```
llama-server --model <gguf> --host 127.0.0.1 --port 11435 \
  --ctx-size 4096 --n-gpu-layers {0|99} --jinja --parallel 1 --threads 4
```

### RAM + startup

| run | RSS after load | RSS after bench | cold load |
|---|---|---|---|
| Q4_K_M, CPU (`-ngl 0`) | 561 MB | 651 MB | 2.4 s |
| Q8_0, CPU | 594 MB | 659 MB | 2.2 s |
| Q8_0, GPU (`-ngl 99`) | 803 MB | 858 MB | 2.4 s |

Box stayed healthy on every run (harness gates on MemAvailable ≥ 2 GB before
loading and re-reads it after; no OOM, zram state unchanged). Note: this box
sits under chronic memory pressure — during setup MemAvailable swung between
0.15 GB and 3 GB; the gate genuinely fires.

### Latency (end-to-end per routing call, temp 0, stop `<end_function_call>`)

| run | prompt | prompt tokens (p50) | p50 | p90 |
|---|---|---|---|---|
| Q8 CPU | full 20+1-tool block | 2,235 | 789 ms | 1,042 ms |
| Q8 CPU | 3-tool block | 300 | 378 ms | 754 ms |
| Q4 CPU | full block | 2,235 | 722 ms | 1,012 ms |
| Q4 CPU | 3-tool block | 300 | 485 ms | 754 ms |
| Q8 GPU | full block | 2,235 | 514 ms | 979 ms |
| Q8 GPU | 3-tool block | 300 | 339 ms | 558 ms |

GPU-offload latency is the most load-sensitive number (shares the GPU with the
live brain): an earlier run measured 383/448 ms full-block — treat GPU figures
as indicative only. Accuracy reproduced exactly across regenerations (temp 0).

### Stock accuracy (81-case corpus; scored as in needle-benchmark: predicted ∈ expected, no-call ≡ general_chat)

| run | overall | canonical | paraphrase | chat false-positive |
|---|---|---|---|---|
| **Baseline** Tier-0 regex + Tier-1 bge-small | **61.7%** | 85.3% | 35.9% | 12.5% |
| Q8 CPU, full block | 33.3% | 41.2% | 12.8% | **0.0%** |
| Q4 CPU, full block | 27.2% | 35.3% | 7.7% | 12.5% |
| Q8 CPU, 3-tool block (16-case subset: time/timer/chat) | **93.8%** | 100% | 75.0% | 0.0% |

Q8 clearly beats Q4 at 270M scale — **use Q8_0** everywhere downstream.
The 3-tool row is a 16-case subset (small n) but mirrors needle's "oracle
shortlist" ceiling probe: a bge-prefiltered 3-schema prompt is already near
target *stock*.

## Resident vs on-demand

**Resident, CPU-only, Q8_0.** Cold load is ~2 s — on-demand would burn nearly
half the latency budget the router exists to save. Resident cost is ~0.6–0.7 GB
RSS; on this memory-TIGHT box that must be explicitly budgeted by the operator
(it's roughly what the MALLOC_ARENA_MAX fix reclaimed from zoe-data). CPU-only
so it never contends with the brain's GPU slot; GPU offload is a nice-to-have
(~2x) not a need. Shrinking `--ctx-size` to ~2,560 will trim the KV/compute
buffers below the numbers above.

## Follow-up (the actual project, not this spike)

1. Fine-tune (functional-token + **chat-negative** examples) via Unsloth on
   Zoe's 21-tool schema — off-box or slowly on-box; target ≥85% overall with
   chat-FP ≤ baseline.
2. Pair with the existing bge-small Tier-1 embedder as a 3–5 tool prefilter
   (drops prompt 2,235 → ~300 tokens; halves latency AND is the 94%-accuracy
   regime today).
3. Integration note: llama.cpp `--jinja` does **not** map FunctionGemma's
   functional-token output to structured `tool_calls`; the runtime must parse
   `<start_function_call>call:NAME{arg:<escape>v<escape>}` itself and pass
   `stop: ["<end_function_call>"]` (without the stop string the model rambles
   repeated calls to `max_tokens`).

## Files

- `run_feasibility.py` — self-contained harness: MemAvailable ≥ 2 GB hard gate
  (no override flag by design), starts/kills its own llama-server on :11435,
  measures cold-load/RSS/latency/accuracy, writes `results/<tag>.json`.
- `corpus.jsonl`, `zoe_tools.json` — vendored verbatim from
  `labs/needle-benchmark/` (branch `feature/needle-router-benchmark`):
  81 cases (34 canonical / 39 paraphrase / 8 chat), Zoe's real 20-tool set
  (+`general_chat` escape tool added by the harness).
- `results/q4-cpu.json`, `results/q8-cpu.json`, `results/q8-gpu.json` — full
  per-case records for the three runs above.
