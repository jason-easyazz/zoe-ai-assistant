---
type: Reference
title: Keeping the panel's KV resident (P-3, 2026-10-19) - what evicts it, and why a second slot or slot save/restore is not the fix
description: Deep dive 2 measured 29 of 100 household panel brain turns re-prefilling more than 500 tokens and blamed the harness. A slot trace (llama-server journal joined to FLUE_PROMPT_CACHE) shows another conversation between two warm panel turns in only 2 of the 18 warm misses; the rest are post-turn helpers and deep prompt divergence. A second llama slot is blocked by upstream #28286 (draft-MTP leaks content between slots); slot save/restore was measured on the running build to restore exactly what the --cache-ram host cache already does. What shipped: harness quiet hours, the traffic class, and a panel-only PANEL_KV_MISS alarm.
tags: [voice, latency, prefix-cache, llama-server, slot, swa, harness, traffic-class, measurement]
timestamp: 2026-10-19T05:30:00+08:00
---

# Keeping the panel's KV resident (P-3)

**The ask** (deep dive 2, section 10, P-3): 29 of 100 household panel brain turns re-prefilled more than 500 tokens
(18 of them warm, back-to-back), which is the household's brain-TTFT p90 (5.9 s) and TTFA p90 (5.6 s). Options
named: a second llama slot for the panel, or slot save/restore around harness traffic; plus a panel-only
`first_prompt_n` alarm. The deep dive's cause ("the harness evicts the panel's KV") was **inferred from timing
coincidence, not a slot trace** (its own caveat).

## 1. What actually took the slot (measured, read-only)

`FLUE_PROMPT_CACHE` rows of the 100 panel turns (`~/.zoe-logs/zoe-data.app.log*`, 10-06..10-19) joined to the
llama-server slot trace (`journalctl --user -u llama-server`: per task the selection rule - `LCP similarity` or
`LRU` - the prompt tokens evaluated, tokens generated, and `n_tokens` at release). Every panel turn was matched to
its first model call by `prompt eval = first_prompt_n` within 90 s (100/100 matched). For each WARM panel turn
(same session, under 5 min after the previous one, 69 turns) the tasks launched after the previous panel turn
finished and before this one started:

| outcome of the warm panel turn | another conversation used the slot (bar / day-sim / probe / Telegram) | only post-turn helper calls | nothing |
|---|---|---|---|
| hit (re-prefill <= 500) | 3 | 21 | 35 |
| **miss (> 500)** | **2** (728 and 711 tokens, both partial: `first_cache_n` 2,981 / 2,154) | **9** | **7** |

- **The harness intervened in 2 of the 18 warm misses**, and both were partial re-prefills (~0.7k tokens, ~1 s),
  not the 3.4-4.4k full misses that make the 5-7 s tail.
- **The 11 FULL warm misses** (`first_cache_n=0`): 10 on 10-12 17:20-18:42 - the helper-trio evictions that
  `brain_slot_guard` fixed that evening ([brain-prefix-cache-2026-10-12.md](brain-prefix-cache-2026-10-12.md)) - and
  one on 10-14 21:53, after 6 guarded helper calls ran in a 62 s conversational pause (the guard's quiet window,
  `ZOE_BRAIN_SLOT_QUIET_S`, is 45 s).
- The 7 misses with nothing between are deep prompt divergence (the previous turn's recall block elided,
  the tool block growing): 523-1,363 tokens re-prefilled, which no slot policy changes.
- zoe-data's own log shows no other Flue conversation (bar, Telegram, web) between any two warm panel turns;
  the slot trace's 5 "conversation" cases (2 misses, 3 hits) came from callers outside zoe-data's logs - the
  in-process replay probe or a bench script going straight to the sidecar or `:11434`.

Scripts (read-only, counts only, not committed): `kv_miss_cause.py` (zoe-data log) and `kv_slot_trace.py` (the join).

## 2. A second llama slot: blocked on correctness, not RAM

- `--parallel 2` with `--spec-type draft-mtp` leaks content between concurrent requests (upstream
  ggml-org/llama.cpp#28286; no garbage-token signature). **Correction 2026-10-29:** that issue was filed against a
  downstream fork running a Qwen Gated-DeltaNet hybrid on ROCm, and was closed by the stale bot on 2026-10-18 as
  `not_planned` - not fixed, not shown to affect Gemma 4 on CUDA; the rule stays until a content-isolation test on this
  model passes ([brain-slot-isolation-2026-10-29.md](../research/brain-slot-isolation-2026-10-29.md)). MTP is part of the brain rock, so
  `tests/unit/test_llama_server_unit_flags.py::test_draft_mtp_implies_single_slot` keeps `--parallel 1`. A
  dedicated panel slot would put the household's prompts in the slot next to the harness's: the exact leak.
- RAM, for the record (computed from the GGUF metadata, cross-checked against the live host-cache entries):
  Gemma 4 E4B has 42 layers, 18 share KV, so 24 own it - 4 global (2 KV heads x 512+512 dims) and 20 SWA
  (2 x 256+256); at q8_0 (1.0625 B/elem) that is 30,464 B per token, so **a second 8,192-token slot under
  `--swa-full` costs ~238 MiB** (plus the MTP drafter's small KV). A ~3.3k-token prompt in the host cache (global
  layers whole, SWA layers' last 512 positions) computes to ~38 MiB; the journal's
  `making room for prompt cache entry` lines show 37-43 MiB. Affordable (MemAvailable 1.8-2.9 GB with the brain
  at ~6.0 GB RSS), but #28286 rules it out.

## 3. Slot save/restore: measured to buy nothing on this build

`llama_state_seq_save_file(ctx, path, slot->id, ...)` (the `/slots/0?action=save` path,
`tools/server/server-context.cpp:2574` in `~/llama.cpp-b11194`) goes through `llama_kv_cache::state_write` with a
sequence id, which skips every SWA-masked cell (`src/llama-kv-cache.cpp:2084`) - the same truncation as the
`--cache-ram` host cache. Lab on the running b11194 build (a scratch CPU `llama-server`, the 270M Gemma-3 SWA model,
`--swa-full --cache-ram 1024 --parallel 1 --slot-save-path`): prime a 3,600-token prompt, let a 400-token helper take
the slot, then send the prompt minus its last `d` tokens plus a new tail.

| arm | d=0 | 200 | 500 | 511 | **600** | 1000 | 2000 |
|---|---|---|---|---|---|---|---|
| resident (no helper) - `cache_n` | 3600 | 3400 | 3100 | 3089 | **3000** | 2600 | 1600 |
| host cache (today) | 3600 | 3400 | 3100 | 3089 | **0** | 0 | 0 |
| **save before, restore after** (`n_restored=3600`) | 3600 | 3400 | 3100 | 3089 | **0** | 0 | 0 |

The restore reports all 3,600 tokens back but carries only the last 512 SWA positions, so it resumes exactly where
the host cache already does and fails exactly where it fails. It would also need `--slot-save-path` in the live
drop-in, a disk write of the slot (~19 MB here, ~240 MiB on the 4B) per panel turn in the server's single task
loop, and an orchestrator. Not adopted.

## 4. What shipped instead (feat/traffic-class-panel-kv)

- **Harness quiet hours** (`scripts/maintenance/harness_quiet.py`, contract in `scripts/AGENTS.md`): the bar,
  day-sim, person bench and ad-hoc probes refuse 07:00-22:00 and yield to a panel used in the last 10 min; the
  landing loop's probe is exempt from the hours but yields to the panel, and the replay yields between clips.
- **Traffic class** (`services/zoe-data/traffic_class.py`, contract in `services/zoe-data/AGENTS.md`), so the
  numbers above can be computed per class from now on instead of by session-prefix archaeology.
- **`PANEL_KV_MISS`** (`zoe_flue_client._panel_kv_alarm`): WARNING + `zoe_panel_kv_miss_count{kind}` on every
  household panel turn that re-prefills more than `ZOE_PANEL_KV_MISS_TOKENS` (500), with
  `non_household_turns_between` and `background_calls_between`, so the next evidence is a log line, not a join.
- **The bench condition `harness`** in `scripts/perf/measure_brain_prefix_cache.py` (one harness conversation turn
  between every two panel turns) to price what quiet hours buy - section 5.

## 5. Before/after: what harness quiet hours buy the panel (live-shape bench)

`measure_brain_prefix_cache.py` (24 turns, bursts of 6, a recall turn in three, two tool-block growths, the helper
trio queued as `brain_slot_guard` does; synthetic text, no household words). `guarded` = today's guard with the
harness quiet; `harness` = the same plus a harness conversation turn (same system + tool block, own history)
between every two panel turns. First turn excluded.

| target | condition | full-miss rate | re-prefill > 500 | `first_prompt_n` p50 / p95 | TTFT p50 / p95 |
|---|---|---|---|---|---|
| scratch 270M (CPU) | guarded | 0% | 34.8% | 37 / 1,244 | projected live 215 / 2,014 ms |
| scratch 270M (CPU) | harness | 0% | 52.2% | 502 / 1,236 | projected live 908 / 2,002 ms |
| **live 4B** (`:11434`, 2026-10-19 04:57-05:05 AWST, brain-window lock held, panel idle since 10-16) | **guarded** (harness quiet) | 0% | 34.8% | 37 / 1,251 | **measured 151 / 2,015 ms** |
| **live 4B** | **harness** interleaved | 0% | 47.8% | 496 / 1,233 | **measured 926 / 2,381 ms** |

Reading: on the live brain, a harness conversation running while the household talks takes the panel's median
first token from **151 ms to 926 ms** (6x) and its p95 from 2.0 s to 2.4 s, and lifts the share of turns that
re-prefill more than 500 tokens from 34.8% to 47.8%. It does NOT force full re-prefills here (the harness shares
the system + tool block, so the panel's prompt resumes from the host cache when its next prompt only extends it):
it turns cheap ~37-token resumes into ~500-token ones - the shape of the two ~0.7k partial misses in section 1.
That is what quiet hours buy. The 34.8% floor is the recall-block elision and tool growth, which no slot policy
removes. (The deep dive's 5.9 s p90 is mostly the full misses of 10-12, before `brain_slot_guard`.)

## Not done, and why

- `ZOE_BRAIN_SLOT_QUIET_S` 45 -> ~120 s would have kept the 10-14 21:53 helper flush out of a 62 s pause. One
  instance is not evidence; `PANEL_KV_MISS ... background_calls_between=` will show whether it recurs (owner flag).
- Deep divergence (recall elision, tool growth) is a prompt-shape trade for the Samantha program, unchanged here.
- The replay probe (in-process, straight to the sidecar) is invisible to zoe-data's counters; the slot trace is the
  only instrument that sees it.
