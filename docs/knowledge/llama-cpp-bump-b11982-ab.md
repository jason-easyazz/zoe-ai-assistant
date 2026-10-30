---
type: record
title: llama.cpp b11982 vs live b11194 - A/B on the voice brain (2026-10-29)
description: Lab build of llama.cpp b11982 (8a199ad31) run against the live b11194 with the identical Gemma 4 E4B QAT + MTP command line - prompt-processing, decode, warm TTFT, MTP acceptance, RSS, and the voice replay probe. Recommendation - do not switch for speed alone.
tags: [llama-cpp, brain, mtp, benchmark, voice, measurement]
timestamp: 2026-10-29T23:45:00+08:00
---

# llama.cpp b11982 vs b11194 (live) - A/B, 2026-10-29

**Update 2026-10-30: the owner decided to switch anyway** - see [llama-cpp-b11982-switch-runbook.md](llama-cpp-b11982-switch-runbook.md).
The measurements below stand; the original verdict is kept as written.

**Verdict at measurement time: DON'T switch now.** b11982 is safe on this box (replay probe identical, no flag breakage) but the gain is
small - prompt processing +6%, everything else flat - and the 788-commit gap is not worth a voice-path landing
unless a specific fix is wanted. The live brain was not touched beyond a 12 s swap; the box ended on b11194.

## What was built

| | live | candidate |
|---|---|---|
| tag / commit | b11194 / `9f70b2cec` | **b11982 / `8a199ad31`** (latest release at 2026-10-29) |
| dir | `~/llama.cpp-b11194` | `~/llama.cpp-b11982` (shallow clone of the tag, never touched the live dir) |
| cmake | `GGML_CUDA=ON`, arch 87, Release, `LLAMA_CURL=OFF` | same flags (`LLAMA_OPENSSL` found no OpenSSL dev package in the candidate configure -> HTTPS support off; irrelevant, the server binds 127.0.0.1 plain HTTP) |

Build: `llama-server` + `llama-bench`, `-j2`, inside `systemd-run --user --scope -p MemoryMax=2500M -p CPUWeight=10`,
`nice 19`, `ionice -c3`: ~53 min wall. MemAvailable dipped to ~270 MB at one point (box was already ~15 GB into swap
with agents running); Kokoro was stopped by the coordinator for headroom during the build.

## Breaking changes / relevant commits (b11194..b11982, 788 commits)

No renamed or removed flag among those Zoe passes: every one of `--spec-type draft-mtp --spec-draft-n-max --spec-draft-p-min
--spec-draft-ngl --swa-full --cache-ram --cache-type-k/v --flash-attn on --load-mode mmap+mlock --jinja --reasoning off
--fit off --metrics` is accepted by b11982 and the server came up with the identical command line. Items worth knowing:

- `spec : add probabilistic sampling for simple draft and MTP` (#27694) - changes how drafts are accepted at temperature > 0
  (Zoe runs `--temp 0.7`). Measured effect below: none at 0.7.
- `common : stop accepting draft tokens at EOG` (#29638), `examples : fix draft sampling and rejection handling` (#30095),
  `spec: properly handle mtmd input for mtp` (#30257), new `--spec-draft-n-ubatch` (#30459) - additive.
- `server: use port 9931 by default` (#30159) - harmless, Zoe always passes `--port`.
- `common : --model or -hf is required` (#31242) - harmless, Zoe passes `--model`.
- SWA / prompt-cache work: `server: fix the off-by-two in the SWA reuse threshold` (#30580), `server : fix swa checkpoint
  invalidation logic` (#30666), `server: save only restorable slot states to the prompt cache` (#30498), `server: prefer
  empty slots and keep states overwritten by child tasks` (#30567), `kv-cache: fix restoring mismatched KV cache rotation`
  (#28498). Relevant to the prefix-cache findings in [brain-prefix-cache-2026-10-12.md](brain-prefix-cache-2026-10-12.md);
  the probe's COLD-turn check (below) shows no regression.
- `mtmd: fix gemma 4 ffn act` (#30882) - multimodal only; Zoe's brain is text.
- CUDA: `tune fp16 tile FlashAttention configs` (#26289), `prefer whole-tile FlashAttention scheduling` (#29435), MMVQ/MMVF
  refinements - the likely source of the prompt-processing gain; nothing sm_87-specific was broken (clean build + run).

## Method

Same session, same conditions, locks held (`/tmp/zoe-brain-window.lock`, `/tmp/zoe-test-lane.lock`).
`bench.py` (not committed; ~4.1k-token system prompt from public repo docs, 3 reps x 2 temperatures): per rep one cold
prefill (unique nonce), then four warm turns (same prefix, different question, 140 max tokens, streaming). Order:
live -> replay probe -> swap (12 s down) -> candidate -> replay probe -> restore live -> live again (drift control).
Candidate was started as a transient user unit with the live unit's properties (MemorySwapMax=0, MemoryLow=6G,
LimitMEMLOCK, CPUWeight/IOWeight 400) and the exact live argument list. Replay probe: `voice_regression_probe.py
--samples 20 --stt remote`, `ZOE_HARNESS_LANDING=1`, Kokoro stopped after locks were held, results to scratch paths,
**no `--update-baseline`**, shared artifact slot untouched.

## Results

| metric | b11194 live | b11982 | b11194 again (drift) |
|---|---|---|---|
| prompt processing, tok/s (4.1k tok cold prefill, median) | 663 | **703** (+6.0%) | 662 |
| cold prefill TTFT | 6.22 s | **5.87 s** | 6.23 s |
| warm TTFT, ms (median, ~22 new tokens) | 117 | 112 / 111 | 117 |
| decode tok/s, temp 0.7 (median of 12 turns) | 20.8 | 20.7 | 20.7 |
| decode tok/s, temp 0 (median) | 19.7 | 19.8 | 19.7 |
| MTP acceptance, temp 0.7 | 36.6% (213/582) | 36.1% (187/518) | 36.8% (217/590) |
| MTP acceptance, temp 0 | 37.1% (162/437) | 31.1% (141/453) | 36.1% (180/499) |
| server RSS / peak (VmRSS / VmHWM, MB) | 5874 / 6856 | 5813 / 6851 | 5786 / 6854 |
| replay probe: said-vs-did | 18/18 held, 0 failed | **18/18 held, 0 failed** | - |
| probe COLD first turn (cached/re-prefilled, brain TTFT) | 3192/47, 498 ms | 3192/47, 498 ms | - |
| probe medians STT / brain / e2e, ms | 550 / 1530 / 1840 | 470 / 1210 / 1525 | - |

(An earlier candidate-only probe run the same night: STT 543, brain 1682, e2e 1741 - 18/18, COLD TTFT 479 ms.)

Reading it:
- **Prompt processing is the only real change** (+6%, repeatable, drift control flat). On a cache-miss turn that is ~0.35 s.
- **Decode, warm TTFT, memory: no difference.** MTP acceptance at the live temperature (0.7) is equal; the temp-0 dip
  (31% vs 37%) is within the spread of 12 short turns and is not a live setting.
- **Probe speed medians are noise, not a win:** STT (which the brain swap cannot affect) moved 550 -> 470 ms between the
  two runs, and the candidate's own two runs spread brain 1210-1682 ms. The probe is warm and relative-only; the only
  conclusion is "no regression" - said-vs-did identical, COLD-turn prefix cache intact (99% hit).
- Note the acceptance (~37%) is for these synthetic general-chat prompts, lower than the ~74% lifetime figure on the live
  `/metrics` (real Zoe turns are short, templated tool/answer text); compare the columns, not the absolute number.

## Recommendation: DON'T (not for speed alone)

A +6% prompt-processing gain does not justify a voice-path landing (replay-gated deploy, Kokoro silenced for a probe,
serial voice merges). No blocker was found, so revisit when a wanted upstream fix lands (e.g. a Gemma 4 / MTP bug) or the
next release shows a larger gain. Cheap to re-run: the build dir `~/llama.cpp-b11982` and scratch bench exist.

### If the owner decides to switch anyway (follow-up PR, not part of this one)

1. `scripts/setup/systemd/llama-server.service`: `LD_LIBRARY_PATH` and `ExecStart` path `llama.cpp-b11194` -> `llama.cpp-b11982`.
2. The host-local drop-in `~/.config/systemd/user/llama-server.service.d/80-swa-full.conf` (not tracked; it carries the live `ExecStart=` with `--swa-full`, `--cache-ram 1024`) - same path change, then `daemon-reload`.
3. `docs/CANONICAL.md` llama-server paragraph: b11194 / `9f70b2cec` -> b11982 / `8a199ad31`, and any invariant test that pins the build path.
4. Land it as a voice-path PR (replay-gated, `land_voice_pr.sh`), then confirm `ps -o args= -C llama-server`.
