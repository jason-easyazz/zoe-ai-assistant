---
type: reference
title: Brain flags tuning (B6.6) — cache-ram, ctx-size, draft-MTP depth
description: Measured on the Orin 2026-09-27 against llama.cpp b11194 (the B0.4 live set). What --cache-ram actually holds with one slot, the real prompt-length distribution behind --ctx-size, and draft-MTP n-max / p-min sweeps. Each candidate is one flag vs the live set, replay-gated, with a same-session control.
---

# Brain flags tuning (B6.6, 2026-09-27)

Scope: three flags on the live brain unit
(`scripts/setup/systemd/llama-server.service`, llama.cpp **b11194**, Gemma 4 E4B-QAT + MTP,
FA on, K/V q8_0, `--parallel 1`, `--fit off`). The rock is unchanged. The live RSS was
~5.7–5.9 GB.

**Outcome:**

| flag | live | decision | why |
|---|---|---|---|
| `--ctx-size` | 16384 | **→ 8192 (adopt)** | −170 MiB RSS at load and −120 MiB after traffic. The Flue client already windows every prompt to 8192. p99 prompt+reply is 3280 tokens. |
| `--cache-ram` | 2048 | **keep 2048** | `0` adds +4.1 s TTFT on every repeat chat turn. `512` shows no difference in a short window, but one main-turn entry is ~170 MiB, so it cannot be shown safe. See below. |
| `--spec-draft-n-max` / `--spec-draft-p-min` | 4 / 0.6 | **keep 4 / 0.6** | No candidate beats the control by more than the ~±3 % noise band, and none does so consistently across the two throughput measures. n-max 6/8 are slightly worse, as upstream predicts. |

## 1. `--cache-ram`: what it holds (source: b11194 `tools/server`)

- **It is a cap, not a reservation.** `server_prompt_cache` (`server-task.cpp`) is a
  `std::list` of saved slot states. The states are allocated lazily (`std::vector` resize
  in `alloc()`), and the oldest entry is evicted when the next save would exceed the limit
  (`update()`). An empty cache costs nothing. RSS grows with distinct prompts until it
  reaches the cap.
- **When it writes:** `get_available_slot()` (`server-context.cpp`) calls
  `prompt_save()` + `prompt_load()` whenever the chosen slot's current prompt would be
  mostly lost: selected by LRU, or LCP similarity with `f_keep < 0.5`. `load()` restores
  the best cached entry (`f_keep ≥ 0.25`, best keep/sim) and **removes** it from the
  cache. The slot holds it until it is saved again.
- **An entry = target KV state + draft state + the prompt's SWA context checkpoints.**
  Checkpoints dominate. Each one is ~10.6 MiB on this model (SWA state), and a slot keeps
  up to `--ctx-checkpoints` (default **32**). Measured on b11194 at `-lv 4`: a
  2788-token main turn = **172 MiB** (13 checkpoints); 2226 tok = 72 MiB; 1404 tok = 65 MiB;
  aux one-shots (59–282 tok) = 4–20 MiB.
- **One slot makes it load-bearing.** A single chat turn issues ~5 brain calls: the main
  turn (~2.7k-token context), a ~1.4k aux call, and three ~0.1–0.3k one-shots. Each call
  evicts the slot. With the cache on, each call re-prefills only ~5–41 tokens (restored
  from RAM). With it off, each re-prefills everything.
- **Occupancy on real traffic:** on the pre-B0.4 build (`--parallel 2`, same 2048 cap)
  the cache reached **52–54 entries / 1.07–1.20 GB** within 17 h. Most entries were stale
  one-shots. B6.6 did not measure live occupancy on b11194: at default verbosity those
  lines are TRACE. The live unit RSS rose from 5.45 GB at load to 5.89 GB after 57 min.

**Hit-retention simulation** (pre-B0.4 journal, 34 h, 40 cache hits; for each hit, the
cap that would still have held the restored entry, from the logged `cache state` dumps):

| cap (old-build MiB) | hits lost | tokens re-prefilled |
|---|---|---|
| 256 | 6 / 40 | 8887 |
| 512 / 768 | 4 / 40 (3 of them ~2.7k-token turns, ~4–5 s each) | 8378 |
| 1024 | 1 / 40 | 290 |
| 1536 / 2048 | 0 | 0 |

The b11194 entries are larger: one main turn is ~172 MiB against ~63 MiB on the old
build, because it carries more checkpoints. So on b11194, 512 MiB holds about three main
turns, and voice, chat and aux prefixes already overflow it.

## 2. `--ctx-size`: real prompt lengths

**Source:** `journalctl --user -u llama-server.service`, which is the whole retained
journal: 2026-09-25 22:27 → 2026-09-27 10:03, 6 server PIDs, **630 completed turns**,
0 truncated. The script is `parse.py`: per task it reads `stop processing: n_tokens`,
which is prompt + reply, the quantity the context must hold. zoe-data logs carry no
token counts. `/metrics` has only `n_tokens_max`, and it reset at 09:20 (3248).

| slice | n | p50 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|
| prompt + reply (all turns) | 630 | 2852 | 3171 | 3216 | **3280** | **3338** |
| prompt only | 630 | 2828 | 3154 | 3191 | 3255 | 3314 |
| newly evaluated (cache miss part) | 630 | 141 | 3097 | 3158 | 3235 | 3257 |
| reply | 630 | 22 | 39 | 44 | 52 | 353 |
| main brain turns (≥2000) | 408 | 2994 | — | 3235 | 3283 | 3338 |
| aux 700–2000 | 26 | 1712 | — | — | — | 1712 |
| aux one-shots (<700) | 196 | 194 | — | 299 | 324 | 337 |

The longest turns are tool-heavy main turns, ~3.3k tokens. There are two structural
bounds:

1. The Flue brain client windows every prompt to `DEFAULT_CONTEXT_WINDOW_TOKENS = 8192`
   (`labs/flue-zoe-brain-2x/src/context-window.ts`), with a 1536-token reply reserve. A
   16384 slot buys nothing that client can use.
2. 8192 per slot is what the brain served from 2026-07-21 until B0.4 (`16384 / --parallel 2`),
   with no context-overflow line in the retained journal.

Some zoe-data callers (`memory_digest`, `user_portrait`, `contact_backfill`, …) call
llama-server **directly**, so the Flue window does not cover them. Most cap their inputs
per item. `user_portrait.run_portrait_synthesis()` did not cap its total: up to 120 facts,
30 insights and 10 journal entries could exceed 8192. It now assembles its prompt through
`build_portrait_prompt()` with a 5500-token budget (`PORTRAIT_PROMPT_BUDGET_TOKENS`).
The budget is counted with llama-server's own `POST /tokenize`. Plain chars/4
undercounts token-dense text: a CJK fixture read 2171 by chars/4 and 4909 real tokens.
chars/4 is only the fallback when `/tokenize` is down. Trimming drops the lowest-ranked
facts and the oldest journal entries first, and never touches the instructions.
zoe-core's `local-gemma` Pi provider (`provider-local-gemma.ts`) had declared 32768 /
2048 and now declares 8192 / 1024, pinned against the unit. Under budget the prompt is byte-identical to before
(`services/zoe-data/tests/test_user_portrait_prompt_budget.py`). p99 is 40 % of 8192, so
**8192 is adopted**.
`tests/unit/test_llama_server_unit_flags.py` pins per-slot ctx ≥ the Flue window, so the
two cannot drift apart. The residual risk is a turn that fills the client's estimate
exactly with token-dense text (CJK, emoji, code). That turn errors and recovers as the
window slides, as documented in `context-window.ts`.

## 3. Draft-MTP depth (`--spec-draft-n-max`, `--spec-draft-p-min`)

Upstream guidance at b11194:

- The default is `n_max = 3`, `p_min = 0.0`. For Gemma 4 the MTP head shares the target
  KV (`is_mem_shared`) and is applied autoregressively. Drafting stops at `n_max` or at
  the first token whose top probability is below `p_min`.
- The adaptive-depth PR (ggml-org/llama.cpp#27210, open) reports that a draft depth of
  2–3 is close to optimal for prose and reasoning, and that 4 "is known to be harmful to
  performance for reasoning and prose".
- The Gemma 4 MTP PR (#23398) benchmarked `--spec-draft-n-max 4` on the 31B.

The live `/metrics` (09:20–10:13) show 211 drafts, 586 drafted and 320 accepted (55 %).
Accepted per position: 150 / 92 / 51 / 27. Positions 4+ would be rarer still.

### W2 (2026-09-27 13:16–13:34): draft depth and confidence floor

Two throughput measures:

- **bench:** `spec_bench` median / aggregate decode tok/s.
- **all-traffic:** `/metrics` `tokens_predicted_total / tokens_predicted_seconds_total`
  over bench + chat + probe traffic.

Fixed seeds do not pin the text once the draft path changes: 1168–1339 tokens were
predicted per run. Treat ~±3 % as noise. The W1 and W2 controls differ by 1.5 tok/s
all-traffic on the identical config.

| run | replay | brain median ms | bench tok/s (median / agg) | all-traffic tok/s | drafted → accepted (rate) | accepted per draft | RSS after MiB |
|---|---|---|---|---|---|---|---|
| control (n-max 4, p-min 0.6) | PASS 13/13, 0 fail | 1813 | 28.02 / 29.75 | **34.70** | 2313 → 1484 (0.642) | 1.84 | 6156 |
| `--spec-draft-n-max 3` | PASS 13/13, 0 fail | 1769.5 | 28.33 / 29.73 | 32.89 | 2148 → 1415 (0.659) | 1.58 | 6171 |
| `--spec-draft-n-max 6` | PASS 13/13, 0 fail | 1741 | 27.61 / 29.25 | 32.18 | 2730 → 1486 (0.544) | 2.01 | 6166 |
| `--spec-draft-n-max 8` | PASS 13/13, 0 fail | 1858.5 | 27.82 / 28.51 | 32.65 | 3272 → 1616 (0.494) | 1.97 | 6160 |
| `--spec-draft-p-min 0.5` | PASS 13/13, 0 fail | 1909 | 28.73 / **30.27** | 33.19 | 2849 → 1535 (0.539) | 1.70 | 6142 |
| `--spec-draft-p-min 0.7` | PASS 13/13, 0 fail | 1789.5 | **28.92** / 29.86 | 33.06 | 2033 → 1354 (0.666) | 1.78 | 6150 |

What W2 shows:

- **Deeper drafts (6, 8)** draft 18–41 % more tokens for ≤ +0.17 accepted per draft.
  Aggregate bench throughput drops 1.7 % and 4.2 %. This is upstream's "depth > 3–4
  hurts prose" result on this box.
- **n-max 3** raises the acceptance rate but lowers accepted-per-draft, so the net is flat.
- **p-min 0.5 / 0.7** each lead one bench column, by ≤ 3 %. Both trail the control on
  all-traffic throughput.
- Replay is PASS with 0 fail on every run. RSS is unaffected (±30 MiB).

Decision: **no change.** A real gain here would need a larger, text-pinned benchmark
(greedy, or `--spec-synth-*`) to separate it from sampling noise. At voice reply lengths
(p50 22 tokens) even a 3 % decode gain is ~20 ms per turn.

## Method (reproducible)

- Window script `brain_flags_window.sh` is a copy of the coordinator's
  `b0_4_brain_window.sh` with the same structure: stop the unit and Kokoro, start the
  b11194 binary on :11434 with an explicit ARGS array, wait for health, run the probe
  under `flock /tmp/zoe-voice-harness.lock`, then restore the unit and Kokoro. The whole
  window runs under `flock /tmp/zoe-brain-window.lock`, after waiting for other voice
  chains to finish.
- Each run = the **live flag set with exactly one flag overridden**, plus `--verbosity 4`
  on every run including the control (so the prompt-cache TRACE lines are logged).
- Per run:
  - RSS right after load, and again after all traffic
  - `spec_bench.py`: 8 fixed prompts × 2, fixed seeds, `max_tokens` 160, read from the
    server's own `timings`. It is deterministic: identical draft counts across the
    non-spec candidates.
  - `chat_check.py`: zoe-data chat API as the **demo user `test-route-probe`**. T1 cold,
    T2 same prompt, then an unrelated 2.2k-token direct brain call to evict the slot,
    then T3. Run twice.
  - `voice_regression_probe.py --samples 20 --stt remote` (said-vs-did + brain median)
  - `/metrics`
- Probe brain medians swing ±20 % between identical configs, so treat them as a
  regression screen. The decode-speed signal comes from `spec_bench`.

### W1 (2026-09-27 11:03–11:16): cache-ram and ctx-size

| run | RSS load MiB | RSS after MiB | replay | brain median ms | decode tok/s (median / agg) | draft accept | chat T2 / T3 TTFT ms (run a; run b) | prompt cache at end MiB (entries) |
|---|---|---|---|---|---|---|---|---|
| control (live) | 5455 | 6136 | PASS 13/13, 0 fail | 1854 | 28.02 / 29.68 | 0.521 | 1095 / 974; 1533 / 1516 | 394 (14) |
| `--cache-ram 512` | 5450 | 6138 | PASS 11/11, 0 fail | 1824.5 | 27.97 / 29.44 | 0.521 | 1315 / 1386; 1002 / 1263 | 386 (15) |
| `--cache-ram 0` | 5447 | 5611 | PASS 11/11, 0 fail | 1848.5 | 27.68 / 29.53 | 0.519 | **5225 / 5357; 4988 / 4824** | — |
| `--ctx-size 8192` | **5281** | **6016** | PASS 13/13, 0 fail | 2076.5 | 27.96 / 29.24 | 0.521 | 1577 / 1583; 1042 / 1294 | 463 (17) |

What W1 shows:

- **`--cache-ram 0`:** +4.1 s on every repeat chat turn. The voice probe is flat
  because it does not rotate aux calls the way chat does.
- **`--cache-ram 512`:** identical to the control, because 2.5 minutes of traffic never
  filled 512 MiB. The window cannot show the steady state that the cap governs.
- **`--ctx-size 8192`:** saves ~170 MiB at load and changes nothing else measurable. The
  2076 ms brain median is within the probe's noise band (±20 % between identical
  configs), and decode speed matches the control.

## Apply (coordinator, in a Kokoro-paused brain window, after the PR merges)

The only serving change is one ExecStart line. On 2026-09-27 the installed unit matched
the template in every non-comment line except this one:

```diff
-  --ctx-size 16384 \
+  --ctx-size 8192 \
```

```bash
cp ~/.config/systemd/user/llama-server.service ~/.cache/zoe/llama-server.service.pre-b6-6
install -m 644 ~/assistant/scripts/setup/systemd/llama-server.service ~/.config/systemd/user/llama-server.service
diff <(grep -v '^\s*#' ~/.cache/zoe/llama-server.service.pre-b6-6) <(grep -v '^\s*#' ~/.config/systemd/user/llama-server.service)  # expect ONLY the ctx line
systemctl --user daemon-reload
systemctl --user stop kokoro-tts.service
systemctl --user restart llama-server.service      # ExecStartPost waits for /health
journalctl --user -u llama-server.service -n 80 --no-pager | grep n_ctx_slot   # -> n_ctx_slot = 8192
flock /tmp/zoe-voice-harness.lock python3 ~/assistant/scripts/maintenance/voice_regression_probe.py --samples 20 --stt remote
systemctl --user start kokoro-tts.service         # then verify /health: pipeline_loaded true, device cuda
```

**Rollback:** `install -m 644 ~/.cache/zoe/llama-server.service.pre-b6-6 ~/.config/systemd/user/llama-server.service`,
then daemon-reload and restart in the same Kokoro-paused way. Symptom that would call for
it: a llama-server `exceeds the available context size` error on a real turn.

## Follow-ups (not done here)

- **Checkpoint count** (`--ctx-checkpoints`, default 32): each checkpoint is ~10.6 MiB, so
  a long main turn can reach ~340 MiB per cache entry. Fewer checkpoints would shrink
  entries, but they trade against rollback after SWA invalidation (`restored context
  checkpoint` lines). That is a separate one-flag window. Pair it with a 24 h live
  occupancy read before revisiting `--cache-ram`.
- `labs/flue-zoe-brain-2x/src/context-window.ts` still describes the slot as
  "`--ctx-size 16384 --parallel 2`". The number is right (8192) and the comment is stale.
  It is left alone here, because a change under `labs/flue-zoe-brain-2x` restarts the
  sidecar on deploy.
