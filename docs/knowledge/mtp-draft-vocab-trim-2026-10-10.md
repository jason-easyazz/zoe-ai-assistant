---
type: record
title: MTP draft-head vocabulary trim (d2t) on llama.cpp b11194 - bounded at +4 %, end-to-end A/B NOT yet run
description: 2026-10-10. Little Gemma trims the Gemma 4 MTP draft head from 262,144 to ~16k rows (+7.6 % there). Does it speed Zoe's llama.cpp brain? Measured here - the head is 84 % of one draft step's matvec time (1.49 ms of 1.78 ms) and a 16k slice costs 0.14 ms, which bounds the gain at roughly +4 to +5 % decode tok/s and saves ~34 MiB. The patch, the d2t GGUF builder and the A/B harness are built and verified at the unit level; the live bench window died twice on CUDA OOM at model load, so byte-identity and the real tok/s are INCONCLUSIVE. Nothing is installed live.
tags: [brain, llama.cpp, mtp, speculative-decoding, performance, little-gemma]
timestamp: 2026-10-10T09:45:00+08:00
---

# MTP draft-head vocabulary trim (2026-10-10)

**Verdict: INCONCLUSIVE, leaning small-win.** Kernel-level numbers (locked, repeated) bound the gain at about +4 to +5 %
decode tok/s, which sits at the edge of the +/-3 % run-to-run noise band the brain-flags record measured. The end-to-end
A/B that would settle it (and prove byte-identical greedy output) did not run: both permitted brain-stop windows failed
at model load for lack of GPU-visible memory (section 6). Adoption is **not recommended** until that A/B is run; the
change itself is small, flag-free in the sense that it is selected by *which draft GGUF you load*, and fully reversible.

## 1. How b11194 computes the draft logits (source facts)

- `src/models/gemma4-assistant.cpp`: the draft is a 4-block transformer on `n_embd = 256` whose LM head is the **tied
  `token_embd.weight`**, Q4_0 `[256, 262144]` (37.7 MB, 63 % of the 59.7 MB draft file). The input embedding of the
  draft token is taken from the *target* model (`model_other->tok_embd`), so the draft's own `token_embd` is used only
  as the head. Any row subset is therefore a valid head.
- `common/speculative.cpp` (`common_speculative_impl_draft_mtp`): each draft step is one `llama_decode(ctx_dft)` followed
  by a **backend** `top_k(10)` over the full 262,144-wide logit row (`backend_sampling` defaults on). The draft token is
  `cur_p->data[0]`; it is dropped if its probability is below `--spec-draft-p-min`. Verification runs on the target's
  full vocabulary, so an out-of-subset token can only be a missed draft, never a wrong output.
- `src/models/eagle3.cpp` and `dflash.cpp` already implement a reduced draft vocabulary: an optional I64 `d2t` tensor,
  the head sized `n_draft_vocab`, and the logits scattered back to `n_vocab` (`ggml_fill(-INF)` + `ggml_set_rows`).
  Gemma 4 assistant had no such path (`MASKED_EMBD_CENTROIDS` / `ORDERING` are created `TENSOR_NOT_REQUIRED` and never
  used; the production E4B draft file carries neither).

## 2. Measured: the head is almost the whole draft step

`scripts/perf/mtp_head_microbench.cpp`, built against the installed b11194 ggml and run under the shared lock next to
the live brain (< 100 MiB; Orin NX, MAXN_SUPER). ms per call, back-to-back, one sync, 3 repeats (spread < 1 %):

| configuration (CUDA, batch 1) | ms | note |
|---|---|---|
| head, full: Q4_0 `[256, 262144]` matvec | **1.486** (1.485 - 1.488) | 25 GB/s effective: this shape is far from bandwidth-bound |
| head, 16,384 rows + fill(-INF) + set_rows to 262144 (the patch) | **0.136** | scatter included |
| head, 16,384 rows, no scatter | 0.116 | |
| head, 32,768 rows + scatter | 0.235 | |
| the other 22 weight matvecs of one draft step (4 blocks + pre/post projections) | 0.297 | attention over KV not included |
| `top_k(10)` over a 262,144-wide row (stays, because the patch scatters back to full width) | 0.339 | |
| `top_k(10)` over 16,384 / 32,768 | 0.112 / 0.107 | would need a narrow-logits variant |

So the head is **1.486 / (1.486 + 0.297) = 83 %** of the draft step's matvec time, and slicing it to 16k saves
**1.35 ms per draft step** (32k: 1.25 ms). This is much larger than the head's byte share suggests because the
`ne0 = 256` Q4_0 matvec runs at a fraction of memory bandwidth. A narrow-logits variant (sample on the 16k row and map
the id) would save a further ~0.23 ms per step but touches the sampler; deliberately not done (not minimal, not
upstreamable as a small change).

### Projection (arithmetic on measured numbers, not a measurement)

Round model from `brain-flags-tuning-2026-09.md` (control arm: 2313 drafted, 1484 accepted, 1.84 accepted per draft,
34.7 tok/s all-traffic, 28.0 tok/s `spec_bench`): about **2.87 draft steps** and 2.84 tokens per round, i.e. a round of
~82 ms (all-traffic) to ~101 ms (`spec_bench`). Saving 2.87 x 1.35 = **3.9 ms per round** gives:

| basis | round | 16k slice | 32k slice |
|---|---|---|---|
| all-traffic 34.7 tok/s | 82 ms | +5.0 % | +4.6 % |
| `spec_bench` 28.0 tok/s | 101 ms | +4.0 % | +3.7 % |

Upper bound: it assumes acceptance is unchanged (section 4: held-out coverage 98 - 99 %, so about 1 - 2 % of drafted
tokens become unreachable; expect a slightly lower figure). Little Gemma's own number on the same head and a different
engine was +7.6 %. At voice reply lengths (p50 22 tokens, ~8 rounds) the gain is ~30 ms per reply. Memory: the head
goes 37.7 MB -> 2.36 MB at 16k (34 MiB less on the device, GGUF 59.7 -> 24.4 MB), plus 128 KiB for `d2t`.

## 3. The patch and the builder

- `scripts/maintenance/patches/llama-b11194-gemma4-assistant-d2t.patch` (one file, `src/models/gemma4-assistant.cpp`,
  ~35 lines): if the draft GGUF has an I64 `d2t` tensor, size the tied head from `d2t->ne[0]` and scatter the head's
  logits to `n_vocab` with `-INF` exactly as eagle3/dflash do. No `d2t` -> behaviour is byte-for-byte the old path, so
  existing draft GGUFs keep working. No change to `common/` or the sampler.
- `scripts/maintenance/mtp_draft_vocab_trim.py` (`select` / `coverage` / `build`): `build` is an **exact byte-level row
  gather** of the Q4_0 head (row = 144 B) plus the `d2t` tensor; no dequantize or requantize, every other tensor and all
  metadata copied unchanged. Verified: the gathered rows equal the original rows, `d2t` is sorted and unique, 0 other
  tensors differ, no metadata keys added or dropped; unit test `tests/unit/test_mtp_draft_vocab_trim.py` (with a
  negative control). The pinned set always contains every CONTROL and BYTE token of the target tokenizer, so end-of-turn
  stays draftable.
- Verified as far as it can be without the model: the patched tree **compiles and links** (CPU-only ggml build of
  `llama-server`, run against the stock CUDA ggml libs) and starts. It was **never run on the sliced GGUF** (no window,
  section 6), so the loader path and the CUDA `ggml_fill` / `ggml_set_rows` ops in that graph are exercised only by the
  microbench (which runs the same two ops on CUDA and works), not by a real draft.

## 4. Token subset from Zoe's own domain

Source: read-only `SELECT` of `chat_messages` where `role = 'assistant'`: 4,155 messages, 361,792 characters, **85,280
tokens, only 1,836 distinct ids** (Zoe's replies are short and repetitive). Tokenized with the live server's
`/tokenize`. The base corpus is generic English (Python `pydoc` topics, licences, this repo's `docs/`; 14,804 distinct
ids). Selection = pinned specials, then Zoe-domain ids by frequency, then base ids, then lowest ids as filler.

| K | held-out, temporal (select on oldest 70 %, test on newest 30 %, 32,163 tok) | held-out, random split (25,388 tok) | base corpus only, no Zoe text (all 85,280 tok) | in-sample |
|---|---|---|---|---|
| 16,384 | **98.08 %** | **99.37 %** | 91.48 % | 100 % |
| 32,768 | 98.81 % | 99.67 % | 96.07 % | 100 % |

Read it as: with Zoe text in the mix 16k already covers ~98 - 99 % of unseen replies; K = 32,768 buys under 1 point.
Little Gemma's measurement (acceptance = full-vocab acceptance x coverage, misses cost about their frequency) predicts
a 1 - 2 % relative acceptance loss at 16k. Caveats: the corpus is narrow (heavy duplication), so the in-sample 100 % is
trivial; the drift between the first 70 % and last 30 % of the history is the honest estimate; multilingual or
long-form output (the 12B night window, journal digests) is not represented and would need its own list. Recommendation
if adopted: K = 16,384, regenerated from the live DB at deploy time, never committed.

## 5. What would be measured, and the negative control

`scripts/perf/mtp_vocab_trim_ab.py` starts one server per arm on a **side port** with the live flags (same prompts,
order rotated per arm, warmup discarded, 10 Zoe-shaped prompts x {greedy, temp 0.7 seed 1234}, `max_tokens` 128) and
records decode tok/s from the server's own `timings`, draft acceptance, and the reply text. Arms in order:
`stockA1` (stock binary, full draft) -> `d2t16k` -> `patchedFull` (patched binary, **full** draft: proves the patch
and my build change nothing when `d2t` is absent) -> `d2t32k` -> `stockA2` (stock again: run-to-run noise) ->
`NCprefix16k` (**negative control**: the first 16,384 ids instead of a selected set; Gemma's vocabulary is not
frequency-ordered, so coverage collapses and acceptance must drop - if it does not, the harness cannot see the effect).
Byte-identity: greedy replies of every arm are SHA-1 compared with `stockA1`; the harness prints `n/10 byte-identical`.
It never stops or edits the live unit; the wrapper `scripts/maintenance/mtp_vocab_trim_window.sh` does that, with a trap.

## 6. What happened (honest log) - the A/B did not run

Both allowed brain-stop windows were spent. Neither measured anything, because a **second** E4B would not load beside
the other tenants on the 15.6 GB box:

| window | brain stopped | brain healthy again | downtime | what happened |
|---|---|---|---|---|
| 1 | 08:44:43 | 08:45:01 | 18 s | side server: `cudaMalloc` of the 170 MiB SWA KV failed (CUDA saw 5,975 MiB free; another agent's Kokoro profiler held ~2.9 GB) |
| 2 | 08:59:45 | 09:02:16 | 2 min 31 s | side server (flags slimmed to `--load-mode mmap`): `cudaMalloc` of the 2,493 MiB weight buffer failed with 5,601 MiB free. **The live unit's own restart at 09:00:02 failed with the same CUDA OOM** (`Failed to start`, 120 s `ExecStartPost` timeout) and succeeded only on systemd's retry at 09:02:15 |

zoe-data `/health` returned 200 after each restore. Takeaways: (a) the live brain needs ~5.9 GB of GPU-visible memory
at start and on a busy box it can fail to restart (logged in `open-problems.md`); (b) stopping the brain frees only
~3.9 GB (MemAvailable 1.99 -> 5.88 GB), so a second E4B needs a *quiet* box. The wrapper now **refuses to stop the brain
unless `MemAvailable` is at least 3.3 GB**, and the harness has a `--slim` mode (ctx 4096, no `--swa-full`, no prompt
cache) for tight memory. Not done, and why: no third window (brief allows two); no `-lv 4` draft-time statistics
(`statistics draft-mtp ... dur(b,g,a)`, which would give the draft step's measured total, not only the matvec share).

## 7. Exact steps for the operator (if the A/B is run and wins)

1. **Quiet box first.** No other agent benches, no second Kokoro load, `MemAvailable >= 3.3 GB` (the wrapper checks).
2. **Build** a patched binary out of tree (never inside `~/llama.cpp-b11194`): copy the tree, `patch -p1 <
   scripts/maintenance/patches/llama-b11194-gemma4-assistant-d2t.patch`, configure with the unit header's flags
   (`GGML_CUDA=ON`, arch 87, `GGML_CUDA_FA=ON`, `GGML_CUDA_GRAPHS=ON`, Release), `nice -n 10 cmake --build ... -j2`
   under `flock /tmp/zoe-voice-harness.lock`. (Lab shortcut used here: CPU-only ggml build of `llama-server`, run with
   `LD_LIBRARY_PATH` pointing at a dir with the new `libllama*.so` and the **stock** `libggml*.so`.)
3. **Make the draft GGUF** (original untouched):
   `python3 scripts/maintenance/mtp_draft_vocab_trim.py select --server http://127.0.0.1:11434 --target-gguf
   ~/models/gemma4-e4b-qat/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf --domain-text <assistant replies .txt> --base-text <generic .txt>
   -k 16384 --out ids.txt`, then `build --draft-gguf ~/models/gemma4-e4b-qat/mtp-gemma-4-E4B-it.gguf --ids ids.txt
   --out ~/models/gemma4-e4b-qat/mtp-gemma-4-E4B-it-d2t16k.gguf`.
4. **Run the A/B**: `PATCHED_BIN=... PATCHED_LIBS=... D2T_16K=... D2T_32K=... D2T_PREFIX=... flock
   /tmp/zoe-voice-harness.lock bash scripts/maintenance/mtp_vocab_trim_window.sh <outdir>` (<= 18 min; restarts the brain on
   any exit). Pass bar: greedy replies 10/10 byte-identical to `stockA1`; `patchedFull` within +/-3 % of `stockA1`;
   `NCprefix16k` acceptance clearly below `d2t16k`; `d2t16k` median tok/s above both `stockA1` and `stockA2`.
5. **Replay-gate** (voice path: brain config): `flock /tmp/zoe-voice-harness.lock python3
   scripts/maintenance/voice_regression_probe.py` against the patched server; said-vs-did must not regress.
6. **Deploy** via a drop-in only: `~/.config/systemd/user/llama-server.service.d/81-mtp-d2t.conf` overriding
   `ExecStart` (new binary path, `--model-draft ...-d2t16k.gguf`) and `Environment=LD_LIBRARY_PATH=` for the new build;
   `systemctl --user daemon-reload`, restart inside a brain window, poll `curl -sf http://127.0.0.1:11434/health`, confirm
   zoe-data `/health`. **Rollback:** delete the drop-in, `daemon-reload`, restart. The old binary and draft GGUF are
   never modified.

## 8. Draft upstream text (NOT posted)

> **gemma4-assistant: optional reduced draft vocabulary (`d2t`), as for EAGLE3 / DFlash**
>
> The Gemma 4 MTP assistant ties its LM head to `token_embd.weight` (`[n_embd_assistant, 262144]`) and reads all of it
> on every draft step. The head is only the draft's output projection (the input embedding comes from the target), and
> verification uses the full target vocabulary, so a row subset is safe: a token outside it is just a rejected draft.
> `eagle3` and `dflash` already accept an optional I64 `d2t` tensor. This adds the same to `gemma4-assistant`: when
> `d2t` is present the head is sized `d2t->ne[0]` and the logits are scattered to `n_vocab` (`-INF` elsewhere); without
> it nothing changes. ~35 lines, one file, no sampler change. A GGUF with a sliced head is an exact row gather of the
> quantized tensor (no requantize).
>
> Measured on Jetson Orin NX (CUDA, E4B assistant, Q4_0 head `[256, 262144]`): the head matvec is 1.49 ms vs 0.30 ms for
> all other draft-step weight matvecs; a 16,384-row head plus the scatter is 0.14 ms. [END-TO-END tok/s, acceptance and
> byte-identity numbers to be filled in from the A/B before posting.] Happy to add a `convert`/script option to emit the
> sliced GGUF if there is interest. Follow-up idea: sample on the narrow row and map ids to also shrink the backend
> `top_k` (0.34 -> 0.11 ms per step), at the cost of touching the sampler.

## 9. Open items

- Run the A/B (section 7, step 4); fill the table: tok/s per arm and mode, acceptance, byte-identity, noise band.
- Read `statistics draft-mtp ... dur(b,g,a)` at `-lv 4` to get the draft step's measured total time (not just matvecs).
- `p_min` gating uses the top-10 probabilities; confirm the scattered `-INF` row does not shift them (expected: no,
  `top_k` is taken before softmax mass is spread) - unverified.
- The 12B night window (`brain-flags`, night-window levers) has its own, larger draft head; this analysis does not
  transfer.
- Related: [Brain flags tuning](brain-flags-tuning-2026-09.md) (noise band, round model), [Brain KV-cache tuning](brain-kv-cache-tuning.md).
