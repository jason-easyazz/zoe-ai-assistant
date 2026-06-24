---
type: research
title: Brain Speed Tuning — llama.cpp / Gemma 4 E4B-QAT + MTP on Orin NX
status: research-only
scope: docs/architecture
owner: zoe-engineering
created: 2026-06-25
verified_against:
  - llama.cpp source cached at ~/.opensrc/repos/github.com/ggerganov/llama.cpp/master (MTP-supporting master, has --spec-type mtp)
  - measured live config (ctx 4096, flash-attn off, MTP draft-mtp, TTFT 883ms, ~24 tok/s)
do_not_change:
  - models (Gemma 4 E4B-QAT brain, Moonshine v2 STT) are rocks — optimise around, never swap
  - this is a RESEARCH record; it changes no code or config
---

# Brain Speed Tuning — Gemma 4 E4B-QAT + MTP on Orin NX 16GB

> Research record only. No code/config changed by this document. Every flag below is
> a **recommendation** to be lab-proven on the Orin before it touches the live
> `llama-server` unit. Rocks are fixed: E4B-QAT (brain) + MTP drafter stay; we tune
> around them.

## 0. The real bottleneck (root cause, from source)

Measured pain: system prompt **3,773 tok** + **27 tool schemas ≈ 3,100 tok** = **7,310 tok**
of fixed prefix, against `--ctx-size 4096`. Because the prompt **exceeds the context
window**, the server raises `ERROR_TYPE_EXCEED_CONTEXT_SIZE` and the slot is released
*before* its KV is ever committed:

```
tools/server/server-context.cpp  (slot prompt-processing path)
  if (slot.task->n_tokens() > slot.n_ctx) { ... ERROR_TYPE_EXCEED_CONTEXT_SIZE; slot.release(); return; }
  if (slot.task->params.cache_prompt) {
      n_past = slot.prompt.tokens.get_common_prefix(input_tokens);   // <-- never reached for an over-ctx prompt
      ...
  }
```

So the cause of the cold 3,773-token prefill **every** turn is not a missing cache flag
— it is that **the prompt never fits, so the common-prefix cache is never populated**.
`get_common_prefix()` (the exact-prefix reuse that *would* skip the system prompt) only
runs once a prompt fits in `n_ctx` and a prior slot's KV is still resident.

**Therefore the #1 lever is: make the fixed prefix fit, and keep its KV resident
between turns.** Everything else (KV quant, MTP tuning, FA) is secondary.

Two independent ways to make 7,310 fit:
- **A. Raise `--ctx-size`** (needs KV headroom → KV-quant section).
- **B. Shrink the prefix** (tool-schema cost section — the 3,100-tok schema block is the
  fattest, most compressible part).
Do both; they compound.

---

## 1. KV-cache quantization — and the hard FA constraint

### The load-bearing source fact

With **flash-attn OFF** (your build needs FA off for MTP), llama.cpp **forbids a
quantized V cache but ALLOWS a quantized K cache**:

```
src/llama-context.cpp:401
  if (!cparams.flash_attn) {
      if (ggml_is_quantized(params.type_v)) {
          throw std::runtime_error("quantized V cache was requested, but this requires Flash Attention");
      }
  }
```

There is **no equivalent throw for K**. So the supported combo on your build is:

```
--cache-type-k q8_0     # allowed with FA off
--cache-type-v f16      # MUST stay f16 with FA off (q8/q4 V would throw at startup)
```

Do **not** set `--cache-type-v q8_0` while flash-attn is off — the server will refuse to
start. (Note: the committed `scripts/setup/systemd/llama-server.service` *template* shows
`--cache-type-v q8_0` + `--flash-attn on` + ctx 16384 for an E2B build; that template is
**not** the measured MTP/FA-off live config and must not be copied blindly onto the FA-off unit.)

### Memory math for E4B (Gemma-3n-class text tower)

Approx text-tower shape (E4B-class): ~35 layers, head_dim 256, ~2 KV heads (GQA), and —
critically — Gemma 3/4 uses **sliding-window attention, 1024-token window, 5:1
local:global** ratio, so only the global layers hold full-length KV; local layers cap at
~1024 tokens. That is why Gemma's KV footprint grows far slower than a dense model's.

Order-of-magnitude per-token full-attention KV (f16):
`layers × 2(K+V) × kv_heads × head_dim × 2B ≈ 35 × 2 × 2 × 256 × 2 ≈ 143 KB/token`
**if every layer were global**. With 5:1 SWA most layers are window-capped, so the
*effective* KV at long ctx is a fraction of that — empirically Gemma 3/4 KV is ~15% of a
global-only model at long context.

Practical envelope (estimate — **measure on the Orin**, see Verification):

| ctx  | K/V dtype        | rough KV resident (SWA-discounted) | fits ~1.7 GB free? |
|------|------------------|------------------------------------|--------------------|
| 4096 | f16 / f16        | baseline                           | yes (current)      |
| 8192 | f16 / f16        | ~2× baseline                       | tight / risky      |
| 8192 | **q8_0 K / f16 V** | ~K halved, V same → ~25–30% saved  | **target**         |

q8_0 on **K only** roughly halves the K half of the cache (~25–30% total KV saving since
V stays f16). That saving is what buys the jump from 4096 → 8192 inside the ~1.7 GB free
budget. **q4_0 K** would save more but materially hurts quality on a 4B QAT model and is
not worth it at this ctx — start at q8_0 K.

> **Why 8192, not bigger:** you only need the fixed 7,310-tok prefix + a few-hundred-tok
> turn to fit with headroom. 8192 does that. Going to 16384 wastes scarce Orin RAM with
> no TTFT benefit once the prefix is cached.

---

## 2. Persistent prefix caching across turns (the actual win)

Goal: the 3,773-tok system prompt (and ideally the schema block) is prefilled **once** and
its KV is **reused** every subsequent turn — TTFT drops to "decode the user's new tokens
only."

### What works for Gemma, what doesn't

There are **two different reuse mechanisms** in the server, and only one works for Gemma:

1. **Exact common-prefix reuse — WORKS for Gemma.**
   `n_past = slot.prompt.tokens.get_common_prefix(input_tokens)` runs whenever
   `cache_prompt` is enabled (default on) **and is NOT gated by KV-shift support**. If the
   same slot already holds the system prompt, the identical prefix is reused for free.
   This is the mechanism Zoe needs, and it only requires the prompt to *fit* (§1) and the
   slot to stay resident.

2. **`--cache-reuse N` (KV-shift reuse) — DOES NOT WORK for Gemma.**
   ```
   tools/server/server-context.cpp:3159
     const bool can_cache_reuse =
         llama_memory_can_shift(llama_get_memory(ctx_tgt)) && !slot.prompt.tokens.has_mtmd;
     if (!can_cache_reuse && n_cache_reuse > 0)
         SLT_WRN(slot, "cache reuse is not supported - ignoring n_cache_reuse = %d\n", ...);
   ```
   Gemma 3/4's **shared-KV + SWA** architecture makes `llama_memory_can_shift()` false, so
   `--cache-reuse` is silently ignored and logs the warning (upstream issue #21468). **Do
   not bother setting `--cache-reuse`** for this model — it's a no-op here.

### Recommended flags (single-user, one slot)

```
--cache-prompt                 # default ON — keep it; this enables get_common_prefix reuse
--parallel 1                   # one slot → the system-prompt KV stays pinned to it (already set)
--cache-ram 0                  # see note: with 1 slot, in-slot KV is the cache; -cram host cache is for eviction
--keep <N_PREFIX_TOKENS>       # protect the fixed prefix from context-shift eviction (set to the
                               #   system-prompt+schema token count once measured, e.g. ~3800 or ~6900)
```

Notes from source:
- **`--cache-ram` / `-cram`** (PR #16391) is host-RAM prompt caching that saves *idle
  slots* to host memory and restores them on a matching prefix — most useful with multiple
  slots or when a slot gets reused for a different prefix. With **one persistent slot** and
  a single always-identical system prefix, the in-slot `get_common_prefix` path already
  gives the win; host caching is a belt-and-suspenders for after eviction. On the tight
  Orin budget, **leave host cache small/off** (don't spend the ~1.7 GB free on an 8 GiB
  default host cache — set `-cram` low, e.g. 256, or 0). Verify the default doesn't
  silently eat RAM (`llama-server --help | grep cache-ram`).
- **`--keep N`** pins the first N tokens across context-shift so the fixed prefix is never
  the part that gets dropped. Set it to the measured prefix length.
- **`--slot-save-path DIR`** + the `/slots` save/restore endpoints let you persist a
  slot's KV to disk and restore on restart — optional, only worth it if llama-server
  restarts are frequent.
- **Do not** rely on `--prompt-cache` / `--prompt-cache-all`: those are CLI-completion
  features (`--prompt-cache-all` is explicitly rejected outside interactive mode) and are
  not the server cross-request path.

### The structural fix that makes caching actually land

`get_common_prefix` only reuses the **longest identical leading token run**. So the
caller (`services/zoe-data`, the chat request builder) MUST send the prompt as:

```
[ fixed system prompt ][ fixed tool schemas ]  ← byte-identical every turn
[ dynamic memory / retrieved context ]         ← put AFTER the fixed block
[ conversation history ][ new user turn ]
```

If any dynamic content (timestamp, retrieved memory, per-turn context) is injected
*inside or before* the system prompt, the common prefix collapses to the first changed
token and the cache win evaporates. Industry reports (ProjectDiscovery) show cache-hit
rate going 7% → 84% purely by moving dynamic working-memory to the *end* of the prompt.
**This is a code change in the prompt builder, not a llama.cpp flag — and it is required
for any of the caching above to pay off.**

---

## 3. flash-attn + MTP coexistence

Today MTP requires FA **off** on this build (KV stays f16-V, no fast FA kernels). Whether
a newer llama.cpp lets FA + draft-mtp coexist is the unlock for (a) quantized V cache and
(b) faster attention.

- The cached master already gates the V-quant throw purely on `cparams.flash_attn`
  (`llama-context.cpp:401`) and has FA auto-disable paths (`llama-context.cpp:489–509`,
  `llama-graph.cpp:2712` sets `cparams_copy.flash_attn = false` for certain graphs). So
  the FA-off requirement for MTP is enforced in the **graph build for the MTP/draft path**,
  not a hard architectural wall.
- **Action (research, low priority):** track upstream `--spec-type mtp` + FA interaction.
  If/when a build allows FA on with draft-mtp, it unlocks `--cache-type-v q8_0` (full KV
  quant → more ctx headroom) **and** flash-attn's faster prefill (directly cuts the
  one-time 3,773-tok prefill cost too). Until then, FA stays off and V stays f16.
- **Do not** flip `--flash-attn on` on the live MTP unit speculatively — it will change
  the MTP code path and must be lab-proven first (it may disable MTP or change acceptance).

---

## 4. MTP / speculative-decode tuning

At ~24 tok/s you're near the E4B base rate, so MTP acceptance is low. Source defaults
(`common/common.h`, `common_params_speculative_draft`):

```
n_max   = 3     (--spec-draft-n-max)      // you already pass 3
n_min   = 0     (--spec-draft-n-min)
p_split = 0.1   (--spec-draft-p-split)
p_min   = 0.0   (--spec-draft-p-min)
backend_sampling = true                    // draft sampling offloaded to backend
```

Levers (lab-prove each, measure acceptance from the server's spec stats —
`common/speculative.cpp` logs `n_acc_tokens` / `n_acc_tokens_per_pos` and per-position
acceptance):

- **`--spec-draft-n-max 4` (try 4, maybe 5).** MTP heads degrade fast past position ~2–3,
  but at n=3 you may be leaving the cheap position-2 acceptance on the table. Test 3 vs 4.
  More draft tokens only help if acceptance at those positions stays high — watch
  `n_acc_tokens_per_pos`.
- **`--spec-draft-p-min 0.5–0.8`.** With `p_min = 0` the drafter proposes even
  low-probability tokens that the target rejects, wasting a verify step. Raising `p_min`
  makes drafting greedier (only confident tokens), which **raises acceptance rate** at the
  cost of fewer drafts — usually a net win for low-acceptance regimes. `speculative.cpp`
  drops a draft when `cur_p->data[0].p < params.p_min`.
- **Sampling determinism.** MTP acceptance is highest near-greedy. The measured live brain
  runs with sampling temp; if a path can run the *drafted* verify near-greedy (low temp /
  high top-k pruning) acceptance climbs. Don't change Zoe's user-facing sampling globally —
  this is a drafter-acceptance observation, validate before acting.
- **`--spec-draft-ngl 99`** (already set) keeps the MTP head on GPU — correct, keep it.

Expected: a well-tuned `n_max`/`p_min` on E4B+MTP can lift sustained gen from ~24 → ~30–40
tok/s **if** acceptance is the limiter. If acceptance stays low after tuning, MTP is simply
near its ceiling for this prompt distribution and the bigger wins remain TTFT (caching).

---

## 5. Tool-calling cost (the 3,100-token schema block)

27 tool schemas ≈ 3,100 tok shipped every turn is ~42% of the fixed prefix. Two
complementary fixes (industry-standard for local agents):

1. **Make the schema block part of the cached prefix (cheapest, do first).**
   If the 27 schemas are emitted **byte-identical and in fixed order** right after the
   system prompt (before any dynamic content), they fall inside `get_common_prefix` and
   cost **zero** prefill after the first turn. This requires the prompt builder to (a)
   stop re-ordering/re-serializing schemas per turn and (b) keep them ahead of dynamic
   context (§2 structural fix). **This alone neutralizes the 3,100-tok cost without
   dropping any tools.**

2. **Dynamic tool selection / tool-router (bigger lift, do if §5.1 isn't enough).**
   Don't send all 27 every turn — retrieve the top-k relevant tools for the current turn
   (vector similarity over tool descriptions, or a cheap classifier) and send only those
   (e.g. 5–8). Cuts the schema block ~60–75%. Trade-off: it makes the prefix *vary* per
   turn, which **breaks prefix caching for the schema portion** — so §5.1 (cache the full
   block) and §5.2 (send fewer) are somewhat mutually exclusive. For a single-user Orin,
   **§5.1 (cache the static full block) is the better first move**; only adopt a router if
   the 3,100 tokens still hurt cold-start TTFT or you grow well past 27 tools.
   Smaller models also tool-select *more accurately* with fewer schemas in context, so a
   router can improve correctness too.

---

## Prioritized recommendations (do in this order)

| # | Change | Type | Expected impact | Memory | Risk |
|---|--------|------|-----------------|--------|------|
| **1** | Raise `--ctx-size 4096 → 8192` **with `--cache-type-k q8_0` (keep `--cache-type-v f16`)** so the 7,310-tok prefix fits and the slot's KV persists | flag | **TTFT 883 ms → ~tens of ms** after turn 1 (prefix prefilled once, then `get_common_prefix` reuse). Removes the `exceed_context_size` cold path entirely | +~25–30% KV vs 4096-f16, offset by q8_0 K; target ≤ free 1.7 GB — **measure** | Med: must verify 8192+q8_0-K fits on Orin and MTP still loads. q8_0-K quality hit on QAT 4B is small at q8 |
| **2** | Prompt-builder fix in `services/zoe-data`: emit **fixed system prompt + fixed-order tool schemas first, byte-identical**, all dynamic/memory/timestamp content **after**; add `--keep <prefix_len>` | code + flag | Makes #1's caching actually land turn-after-turn; neutralizes the **3,100-tok schema cost** with zero tool loss (cache-hit 7%→80%+ per industry data) | none | Low: pure ordering/serialization discipline; needs a regression check that reordering didn't change brain behaviour |
| **3** | MTP tuning: try `--spec-draft-n-max 4` and `--spec-draft-p-min 0.5–0.8`, read acceptance from server spec stats | flag | gen ~24 → ~30–40 tok/s *if* acceptance is the limiter | none | Low/Med: must measure acceptance; revert if no gain |

Secondary / track-only:
- FA+MTP coexistence (§3): unlocks q8_0 V + faster prefill — watch upstream, lab-only.
- Host prompt cache `-cram` (§2) + `--slot-save-path` (§2): only if slot eviction or
  restarts become the observed problem; keep `-cram` small on the Orin.
- Dynamic tool-router (§5.2): only past ~27 tools or if cold-start schema prefill still hurts.

## Verification (before any of this touches the live unit)

1. **Lab, not live.** Bring up a second `llama-server` on a spare port with the proposed
   flags; never edit the live MTP unit first.
2. **Measure KV/RAM headroom** at ctx 8192 + q8_0-K: read the server's
   `kv_self size`/`KV buffer` startup logs and `tegrastats`/`free` for the ~1.7 GB budget.
   Confirm the process does not OOM with MTP draft layers resident.
3. **Confirm caching lands:** run `/slots` or `--verbose` and confirm subsequent turns log
   a large `n_past` (prefix reused) and TTFT collapses; confirm **no**
   `exceed_context_size` and **no** `cache reuse is not supported` spam (the latter only if
   you wrongly set `--cache-reuse`).
4. **MTP still works:** confirm `--spec-type mtp` loads and acceptance stats appear; diff
   gen tok/s before/after.
5. **Behavioural regression:** replay Jason's `~/.zoe-voice-samples` corpus
   (`tests/replay_samples.py`) — said-vs-did must be unchanged. Re-running the prompt-order
   change (#2) especially must not alter tool selection.

## Sources

llama.cpp source (cached, `~/.opensrc/repos/github.com/ggerganov/llama.cpp/master`):
- `src/llama-context.cpp:401-405` — quantized-V-requires-FA throw (K-quant allowed FA-off).
- `tools/server/server-context.cpp:3149` — `get_common_prefix` exact-prefix reuse (not shift-gated).
- `tools/server/server-context.cpp:3157-3165` — `can_cache_reuse = llama_memory_can_shift(...)`; warning when unsupported.
- `tools/server/server-context.cpp` (slot path) — `ERROR_TYPE_EXCEED_CONTEXT_SIZE` release before caching.
- `common/arg.cpp` — `--flash-attn` (1384), `--cache-prompt`/`--cache-reuse` (3024-3041), `--cache-ram`/`--cache-idle-slots` (1346-1368), `--keep` (1313), `--slot-save-path` (3064), `--swa-full` (1320), `--spec-*` (3554-3636); `--spec-type mtp` MTP-head fallback (451-458).
- `common/common.h` — `common_params_speculative_draft` defaults (n_max 3, n_min 0, p_split 0.1, p_min 0.0).
- `common/speculative.cpp` — MTP draft loop, `p_min` drop, per-position acceptance stats.

External:
- llama.cpp issue #21468 — cache reuse not supported for Gemma (shared-KV/SWA).
- llama.cpp PR #16391 / discussion #20574 — host-memory prompt caching (`--cache-ram`).
- Gemma 3 Technical Report (arXiv 2503.19786) — 5:1 SWA, 1024-token window, KV reduction.
- HF `google/gemma-3n-E4B-it` — E4B text-tower shape (≈35 layers, head_dim 256, GQA).
- lunar.dev / ProjectDiscovery — dynamic tool selection & prefix-cache-hit (7%→84%) by moving dynamic context to prompt tail.
