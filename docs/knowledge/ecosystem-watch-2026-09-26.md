---
type: Reference
title: Ecosystem watch — 2026-09-26 (delta since the 2026-09-25 review)
date: 2026-09-26
audience: Jason + every agent working the beat-the-bar tracker
status: complete — one pass, every claim dated + linked; UNVERIFIED is written where it applies
description: Re-check of the twelve upstream dependencies and platforms that the 2026-09-25 review (§5/§6) and the tracker rely on, recording what changed in the 24 h window and what it changes in the program.
---
# Ecosystem watch — 2026-09-26

> **Scope.** Re-checks every external item the tracker
> ([`beat-the-bar-2026-program.md`](../architecture/beat-the-bar-2026-program.md)) and the
> review ([`state-of-zoe-review-2026-09-25.md`](state-of-zoe-review-2026-09-25.md) §5, §6)
> depend on, against upstream as of **2026-09-26**. Each section: *what changed / what it
> means for Zoe / action (tracker item)*. Read-only research (`gh api`, registry JSON, web);
> nothing was built, installed or restarted. The rocks (Gemma 4 E4B-QAT+MTP, Moonshine v2
> Medium, Kokoro) are untouched by everything below. Tracker deltas are proposed in §13 —
> the tracker itself is not edited here.
>
> **Headline (top 5):** (1) the repo has **no Actions event policy**, so `voice-gate.yml` and
> `break-glass.yml` fail from **2026-11-02** — one path-scoped policy fixes it (§9);
> (2) Copilot's review default flips to the ~5× dearer Balanced tier on **2026-09-28** — set
> Lite now (§9); (3) **Flue 2.2.0 is not a drop-in**: it exists only as `2.2.0-next.1`
> (2026-09-25) and its Pi 0.87 bump changes the stream contract that Zoe's
> `capped-completions.ts` / `context-window.ts` read (§4); (4) Moonshine upstream is silent
> (zero commits since 0.1.5) and nobody has reported the decoder-step regression — the
> issue draft is in §2; (5) the Kokoro ONNX-CUDA path is de-risked by kokoro-onnx 0.6.1's
> fp16/int8 exports and a cp310 `onnxruntime-gpu` 1.24.0 on the Jetson index (§3).

## 1. llama.cpp + Gemma 4 (B0.4 rebuild at b11194)

**What changed.** Tags now run to **b11194** (commit `9f70b2cec`, 2026-09-26T03:41Z, "opencl:
add A8 Q8_0 non-MoE dp4a binary kernel (#29439)"); the latest *release object* is b11193
(2026-09-26T03:30Z) and b11194 had no release asset yet at 07:30Z — CI lag, irrelevant to a
source build (`git checkout b11194` works; the prebuilt arm64 asset is CUDA 13.4, not
JetPack's 12.6, so source stays mandatory). b11178 → b11194 is 16 commits, **none touching
CUDA arch 87 / aarch64 / `GGML_CUDA_FA` / MTP / Gemma / jinja** — the only server-relevant
change is **cpp-httplib → 0.58.0 (#29407)**. So b11178 and b11194 are equivalent for this
rebuild; b9733 → b11194 is 1,461 commits.

Issues/PRs since 2026-06-30 on the rebuild's exact surface:
- **#25148** (CUDA: fix Gemma E4B MTP FlashAttention) merged 2026-06-30 — in b11194.
- **#25522** (Gemma 4 crashes with MTP) **still open, last activity 2026-09-19** — but every
  reproduction is **multi-GPU `--split-mode tensor`** (crash at `fattn.cu:730` from
  `draft_mtp::draft`, root-caused in-thread as a use-after-reset in the tensor-split backend
  recycling shared-KV global layers); no single-GPU report; `-sm layer` is the workaround;
  the only linked PR (#27781, draft since 08-27) is about qwen35-style shared-KV detection.
  **Not applicable to a single-GPU Jetson** — stop watching it as a Zoe risk.
- Gemma-4-family MTP loading was **broken by #28159 (~2026-09-01, build 10743)**
  (`GGML_ASSERT(n_layer_nextn < n_layer_all)` — the gemma4-assistant drafter has MTP layers
  from block 0) and **fixed the same day by #28183** — in b11194; a pin between 10743 and
  #28183 would not load the drafter.
- #26017 (E4B + MTP crash on a 6 GB GPU, `--fit off`, 131k ctx) closed stale 09-06 — the
  reporter's own fix was `--cache-type-v q8_0` (memory, not a kernel bug). **No issue pairs
  `--cache-type-v q8_0` with FA or MTP on CUDA.** Caveat: `GGML_CUDA_FA_QUANTS` at b11194
  defaults to `q4_0-q4_0;q8_0-q8_0;f16-f16;bf16-bf16`, so **K f16 + V q8_0 is a mixed pair**
  — verify it lands on a compiled FA kernel rather than a fallback (UNVERIFIED;
  `-DGGML_CUDA_FA_ALL_QUANTS=ON` is the safe cure if perf looks off). Quantising the *drafter's*
  cache (`-ctkd/-ctvd`) can increase VRAM (draft PR #28378, maintainer-confirmed).
- **Jetson/Gemma-4-positive perf changes absent from b9733, all in b11194:** **#28285**
  (2026-09-03) MMVQ→MMQ crossover tuned **for SM87**, benchmarked on AGX Orin + Orin Nano,
  "huge positive impact on speculative decoding"; **#29152** (2026-09-20) FA tuned for Gemma
  4 on Ampere+ (head sizes 256/512, bs 1–4, "primarily for the small models"); **#28549**
  (2026-09-16) CUDA graph for the MTP draft (no recapture per catch-up) — note
  `GGML_CUDA_GRAPHS_DEFAULT` is OFF in ggml's CMake at b11194; confirm the top-level build
  turns it on or this gain is dark. FA smem swizzle (#25635, #28536) churned the Ampere path
  with no Gemma 4 regressions filed.
- **Open, plausible on a 16 GB UMA box:** **#29391** (2026-09-24) Gemma 4 on b11159+ — a
  slot's `bad allocation` under prefix reuse aborts the whole server
  (`GGML_ASSERT(batch.slot_batched || batch.size()==0)`); fix PR **#29467** open 2026-09-26,
  tested on `9f70b2c` = b11194 — memory-pressure-triggered, so heed it. **#27282 / PR #27489**
  (open): native MTP reserves a *separate* CUDA compute arena → OOM near the limit — budget
  for it. **#28286** (open; fix #29454 opened 09-26): draft-MTP + `--parallel >1` cross-slot
  contamination — keep `-np 1`. **#29142** (09-19): the hardcoded 32 GB VMM reserve fails on
  the Orin iGPU, **only for mmproj/multimodal** — text-only Zoe unaffected;
  `-DGGML_CUDA_NO_VMM=ON` if B5.5 ever enables audio-in. #29168/#29381 (MoE fusion vs spec
  exactness, not merged) is MoE-only — E4B is dense, N/A (relevant to B0.15's 26B-A4B
  candidate only).

**Chat template (the 2026-07-17 re-upload).** llama.cpp **does not use minja** — it was
replaced by its own `common/jinja` engine in #18462 (2026-01-16); "minja" issues since July
are misfiled. Read at the b11194 tag, `common/jinja/value.cpp` registers `is none`,
`is string`, `is sequence`, `is mapping`, `is iterable`, `is defined`, `in`, plus builtins
`get`, `dictsort`, `namespace`, `tojson`, `trim`, `items`, `selectattr`, `startswith` — every
construct the new template uses (`argument is none`, `message.get('reasoning') or …`,
`messages and messages[0]['role'] in […]`, `part.get('type') in ['image','image_url']`,
`item.get('type') in ['audio','input_audio']`, `function['arguments'] is string`,
`tool_body is sequence and tool_body is not string`). **`--jinja` with the embedded template
renders at b11194 — not a blocker.** Two Gemma-4 template fixes landed after b9733 and are in
b11194: **#28511** (2026-09-07, Gemma 4 was classed `supports_typed_content=false`, so typed
`content: [...]` parts were flattened) and **#29115** (2026-09-19, `tool_choice: required`
grammar let malformed JSON through). Jinja-engine fixes #27034/#28620/#28817/#29244 are in;
#29443 (2026-09-26) is on master after b11194. **Open and directly about the clause the
re-upload added:** **#28827** (2026-09-13, 31B) — thinking emits progressively longer trailing
garbage on multi-tool turns because the template re-injects
`'<|channel>thought\n' + thinking_text + '\n<channel|>'` and the model never emits that
leading `\n`; workaround is a `--chat-template-file` using `(thinking_text | trim) +
'<channel|>'`. **Not tested on E4B (UNVERIFIED)** — the replay gate after the B6.2 swap must
include multi-tool-call turns with thinking on. Unsloth: `lastModified` 2026-07-17T12:37Z, the
only commit on/after 07-17 is the template update; **no newer re-upload, no template fix, no
drafter change**; file sizes/sha256 match the staged pair (`df0fd4ee…`, `423074e5…`).
Upstream `google/gemma-4-E4B-it` had one later commit (2026-07-20, `response_template` in
`tokenizer_config.json` — training-side only).

**What it means.** Go for b11194 — nothing since 09-25 changes the plan, and b11178 ≡ b11194
for this build. The rebuild inherits three Orin-relevant perf wins over b9733 (#28285,
#29152, #28549) plus the two Gemma 4 tool-path fixes. Four things to add to the gate: (1)
confirm `GGML_CUDA_GRAPHS` is on in the built binary, (2) confirm f16-K/q8_0-V hits a
compiled FA kernel, (3) keep `-np 1`, (4) include multi-tool + thinking turns in the replay so
#28827 shows if it reproduces on E4B; and watch #29391/#29467 (prefix-reuse abort) as the
one live regression class in the window.

**Action.** B0.4: replace "watch #25522" with the list above (#25522 is multi-GPU only);
add the four gate checks and the #27282 headroom note; B6.2's replay must cover multi-tool +
thinking turns.

## 2. Moonshine (B1.10 — HELD)

**What changed.** Nothing upstream. Latest release is still **v0.1.5 (2026-08-24)** on GitHub
and PyPI (tags: 0.1.0 07-27, 0.1.1 08-07, 0.1.2 08-13, 0.1.3 08-18, 0.1.5 08-24); the `main`
branch has **no commits since 2026-08-24**. Wheels are `py3-none-manylinux_2_34_aarch64`
(pure-python tag + bundled native lib) for every version, so cp310 is not a constraint.
Issues since 2026-08-01: #211 (streaming models not mmapped — fixed in 0.1.5), #216/#217
(`.ort` mapping leaks), #218 (medium-streaming `compute_cross_kv(): Memory is empty`), #223
(Android SIGABRT on close), #224/#227 (use-after-free in `free_stream`), and **#229 (open,
2026-09-25): concurrent streams share one Silero VAD instance and corrupt each other.**
**No issue anywhere about a 0.1.x decoder-step / per-file latency regression, CPU threading
or aarch64 performance** — Zoe's measurement is unreported upstream. Changelog facts that
matter: 0.1.1 turned speculative decoding on by default and made models `.ort`-only; 0.1.2
switched wheels from debug to optimised builds; 0.1.5 split the streaming frontend into
`frontend.model.ort` + int8 `frontend.weights.ort`. The C core's only thread knob is the
opt-in `MOONSHINE_ORT_SINGLE_THREAD` (intra=inter=1, sequential); the Python API exposes no
intra-op thread option (the TTS side does). CMake pins libonnxruntime 1.23.2 (macOS path
verified; Linux bundle UNVERIFIED).

**What it means.** Retest condition 1 of the runbook ("next release") has not triggered and
there is nothing in flight upstream that would; conditions 2/3 (a documented thread knob, an
aarch64 perf note) have not appeared either. B1.10 stays HELD on 0.0.62. #229 is the one
open upstream bug relevant to Zoe: it bites only with multiple concurrent `Transcriber`
streams in one process, which the replay harness and `voice_tts.py` do not do today — note
it before any "two panels, one zoe-data" design. The upstream issue below is the cheapest
way to get the regression looked at; it is outward-facing, so filing is Jason's call.

**Action.** B1.10 unchanged (HELD). Add "watch moonshine-ai/moonshine #229" to B1.10's retest
notes. 🧑 decide whether to file the issue below.

### Draft upstream issue (NOT filed)

**Title:** 0.1.5 aarch64 (Jetson Orin NX, Python 3.10): each decoder step ~3× slower than
0.0.62 (57–70 ms vs 20–22 ms), same model, same ORT

**Body:**

Environment: Jetson Orin NX 16 GB (aarch64, 6× Cortex-A78AE), JetPack 6.2.1 / Ubuntu 22.04,
Python 3.10.12, `moonshine-voice` PyPI wheels (`0.0.62` vs `0.1.5`), model
`MEDIUM_STREAMING` (English), CPU inference, `Transcriber.transcribe_without_streaming`
per file, `use_speculative_decoding` at its default.

Observation: 0.1.5 is ~1.9× slower per file than 0.0.62 on the same 20 clips (16 kHz,
~1.3 s each, 2 warm passes, `nice -n 5`):

| Configuration | median | p90 |
|---|---|---|
| 0.0.62 (run 1 / repeat) | 284 / 286 ms | 885 / 929 ms |
| 0.1.5 default (speculative decoding on) | 408 ms (+43 %) | 1439 ms |
| 0.1.5, `use_speculative_decoding=false` | 508 ms | 1902 ms |
| 0.1.5 library + the 0.0.62 `quantized/` bundle | 382 ms | 1184 ms |

Per file the ratio is uniform (~1.9×: 333→626, 1204→2816, 294→676, 460→879, 634→1297 ms).
Transcripts differ on 9/20 files in both directions — this report is about speed only.

Where the time goes (per-session ONNX log, `options={"log_ort_run": True}`): the encoder /
adapter / cross-KV runs are identical (~50 ms) across versions, the same 8 decoder steps run
with the same input shapes, but **each decoder step is 57–70 ms in 0.1.5 vs 20–22 ms in
0.0.62**.

Ruled out on this box:
- not the model bundle — 0.1.5 lib + the 0.0.62 bundle: still ~60 ms/step;
- not the bundled ONNX Runtime — 0.0.62's `libonnxruntime` swapped into the 0.1.5 package:
  still ~70 ms/step;
- not threading — `MOONSHINE_ORT_SINGLE_THREAD=1` → 1010 ms median; pinning to 2/4 cores →
  860–880 ms; `OMP_NUM_THREADS=2` → 584 ms;
- not speculative decoding — off → 508 ms;
- not the streaming update interval — 10 s → 525 ms.

What remains is the decoder-run path inside `libmoonshine.so` 0.1.x itself (36 MB in 0.0.62
→ 12 MB in 0.1.5). Happy to run any build or env knob you suggest and report per-step
numbers; a per-step timing on an x86 host would show whether this is aarch64-specific.

(Numbers: [`moonshine-0-1-5-upgrade.md`](moonshine-0-1-5-upgrade.md) §8 and tracker B1.10.)

## 3. Kokoro (B5.1 — ONNX Runtime CUDA)

**What changed.** Nothing at the model level, and nothing in the last 24 h — but the
August/September runtime moves matter. Weights: `hexgrad/Kokoro-82M` last modified
2025-04-10; `onnx-community/Kokoro-82M-v1.0-ONNX` 2025-02-08; PyPI `kokoro` 0.9.4
(2025-04-05). **No 2026 Kokoro weights exist.** Runtime: `kokoro-onnx` **0.6.0 (2026-08-18) /
0.6.1 (2026-08-19)** — PR #198 re-exported the model with `speed`/duration outputs and shipped
**fp16 (164 MB) and int8 (114 MB) variants** in release `model-files-v1.1` beside the 326 MB
fp32 file; CUDA EP is supported (`examples/with_cuda.py`) but the `[gpu]` extra is
`onnxruntime-gpu; platform_machine == "x86_64"` — on aarch64 you install `kokoro-onnx` plain
and bring your own ORT-GPU. The Jetson index `https://pypi.jetson-ai-lab.io/jp6/cu126/` serves
**`onnxruntime_gpu` 1.23.0 and 1.24.0, cp310 aarch64** (verified 2026-09-26); upstream ORT is
1.30.0 with no aarch64 GPU wheel, so 1.24.0 is the ceiling without a source build. Known trap
(NVIDIA forum, 2025-08): kokoro-onnx wants numpy 2.x while the Jetson ORT-GPU wheel was built
against numpy 1.x — resolved by pinning, not rebuilding. jetson-containers has
`kokoro-tts:onnx` (sed-patches providers to CUDA+CPU; last touched 2026-05-04). **No RAM
numbers for Kokoro-ONNX-CUDA on Jetson are published anywhere** — must be measured. Side note:
Moonshine 0.1.5 ships Kokoro as a two-stage ORT graph (`kokoro/prosody.*` + `kokoro/decoder.*`,
streaming, "+85 MB") — a CPU-only ~100–200 MB fallback if the sidecar ever has to drop CUDA.

**What it means.** B5.1 is de-risked: the fp16 graph on CUDA EP with ORT-GPU 1.24.0 (cp310,
so it lives on the system Python beside llama-server, not in the B0.7 venv) is a concrete
recipe. The RAM claim (2.5 GB → ~0.6–1 GB) is still a hypothesis; the negative control is the
same graph on the CPU EP, and the gate is RTF < 0.3 plus the replay corpus.

**Action.** B5.1: record the recipe (`onnxruntime-gpu==1.24.0` from `jp6/cu126`,
`kokoro-onnx==0.6.1`, fp16 graph, numpy pinned to the ORT wheel's ABI) and the "no published
Jetson numbers — measure with a CPU-EP control" note.

## 4. Flue (B1.11 — PARKED) and Pi (dormant `zoe-core` lane)

**Correction to the baseline.** Zoe does not pin `@mariozechner/pi-ai`. That scope is dead
(last publish 0.73.1 on 2026-05-07, deprecated in favour of `@earendil-works/pi-ai`). The
real pins are `@earendil-works/pi-ai` **0.83.0** (`labs/flue-zoe-brain-2x/package.json`) and
`@earendil-works/pi-coding-agent` **0.82.1** (`services/zoe-core/package.json`). Flue's repo
is `withastro/flue`.

**What changed — Flue.** `dist-tags`: `latest: 2.1.1`, `next: 2.2.0-next.1`. **2.2.0-next.0
and -next.1 were published on 2026-09-25** (17:43 Z and 19:18 Z); there is no 2.1.2 and no
2.2.0 final. 2.1.1 (2026-09-23): skill `allowed-tools` is guidance only; linear trace
truncation; tool args/results moved to standard `gen_ai.tool.call.*` attrs (the vendor
`flue.tool.call.*` keys are no longer emitted — check any trace consumer); Hono `^4.12.32`;
Pi stays `^0.83.0`. 2.2.0-next: bounded history reads (`client.history({limit})`,
`historyBefore(cursor)`, `observe({limit})`; old cursors 410 `history_cursor_not_found`), PDF
`document` attachments ("existing conversations and persistence adapters need no
migration"), server-authored `timestamp`; patch 89032c5 "**Flue now uses Pi 0.87.1**" — "no
public API changes" *on Flue's surface*. Nothing Flue-side about llama.cpp / strict schemas /
`enable_thinking` / first-delta; those come only through the Pi bump. No store
`format_version` bump listed (UNVERIFIED beyond changelog absence). Node floor unchanged
(`>=22.19.0`).

**What changed — Pi.** Releases after 2026-09-18: 0.86.0 (09-19), 0.86.1 (09-20), 0.87.0
(09-21), **0.87.1 (09-22, latest)** for pi-ai / pi-agent-core / pi-coding-agent. Breaking
vs 0.83, by version:
- **0.84.0 (2026-08-06):** RPC `message_update` emits only `assistantMessageEvent` deltas —
  cumulative `message` / `.partial` removed (#7290); coding-agent session store replaced by
  the v4 lane-based `Session`/`SessionRepo` (legacy JSONL repo APIs removed);
  `ModelsStreamTransforms` → `ModelsRequestTransforms`; provider `fetchModels`/auth must
  accept an abort signal. Added `shouldStopAfterTurn`, `compat.supportsFinishReason`.
- 0.84.2: strict schemas auto-closed with required-nullable optionals; 0.84.3:
  `toolcall_start` carries id+name, **llama.cpp/vLLM thinking-token-budget fields (#8275)**;
  0.84.4: `prepareNextTurn` only before another turn, RPC `clear_queue`.
- 0.85.0 (09-04): `createGatewayBindingFetch` → `createAiBindingFetch`; provider streams
  normalised to standard event sequences. 0.85.1: stdio RPC "unchanged".
- **0.86.0 (09-19):** `ProviderStreams`/`StreamFunction` input `Context` → `TranscriptContext`
  (system prompt and tools now travel in the leading system message; read them via
  `getCurrentSystemPrompt()` / `getCurrentTools()`); `ToolCall.arguments` restricted to JSON
  values. The **`enable_thinking` fix (#9528)** is in the *coding-agent* changelog (Pi's own
  llama.cpp provider), not pi-ai — it likely does not reach Flue.
- **0.87.0 (09-21):** `shouldStopAfterTurn` removed → `finishTurn` returning `{action:"end"}`;
  new `prepareRequest`; pi-ai: **unknown OpenAI-compatible endpoints no longer receive strict
  tool schemas unless they advertise support (#9816)** — this one is pi-ai-level and reaches
  Flue. 0.87.1: no empty text part for image-only messages (#9797); invalid `--mode` errors.

**What it means.** The 2.1.1 draft (#1694) is safe — Pi 0.83 contract unchanged. **2.2.0 is
not a drop-in for the brain sidecar**: Zoe's `capped-completions.ts` wraps pi-ai's
`openai-completions` stream as a `ProviderStreams` pair and `context-window.ts:189-190 /
259-260` read `context.systemPrompt` and `context.tools` for the budget maths, while
`applyCap` sets `tools: []` to enforce the iteration cap. Under Pi ≥0.86 that layer receives
`TranscriptContext`, where those fields do not exist: the tool strip becomes a **silent
no-op** (tools ride in the system message) and the budget estimate reads `undefined`. Port to
`getCurrentSystemPrompt()` / `getCurrentTools()` plus a tools-removed system message and
re-verify the cap with a negative control (cap must still trip) before any 2.2.0 bump. Of the
"two llama.cpp fixes" the review credited to 2.2.0, only #9816 (strict schemas) reaches Flue;
#8275 (thinking budget, 0.84.3) is the other pi-ai win; #9528 is CLI-side. The dormant
`zoe-core` RPC lane (0.82.1) still needs the 0.84.0 delta-assembly rewrite and the v4 session
format if it is ever revived — nothing since 09-25 changes that; retire-by-removal remains
the cheaper answer.

**Action.** B1.11 (parked on the sibling-directory contract): add "2.2.0 = `next` only;
requires the `TranscriptContext` port of `capped-completions.ts`/`context-window.ts` with a
cap negative control; only #9816 is a Flue-reachable llama.cpp fix". B0 (deps): note the
`@mariozechner` → `@earendil-works` correction wherever the review names the old scope.

## 5. Pi 0.84+ RPC vs the `pi-ai` 0.83 pin

Covered in §4: the RPC breaking change is 0.84.0's deltas-only `message_update` (+ the v4
session store in coding-agent); 0.85.1 and later state the stdio RPC surface is unchanged
apart from additions (`clear_queue`, `abort` cancelling compaction, `steer`/`follow_up`
`input` handlers, and — unreleased on main — per-input disposition on
`prompt/steer/follow_up`, #9098). Omnigent's floor is pi ≥0.84.2, so any Omnigent 0.15 move
lands on the new contract automatically. **Action:** none new; keep the review's step (8)
ordering (port or retire `zoe-core` before Omnigent 0.15).

## 6. Home Assistant (B0.12)

**What changed.** Nothing shipped in the window. Latest is **2026.9.3 (2026-09-18)**
(2026.9.1 09-05, 2026.9.2 09-11); **no 2026.10.0 and no 2026.10.0bN tag** exist (`dev` is
`2026.10.0.dev0`; first beta expected ~2026-09-30 on the usual cadence, UNVERIFIED). The
2026.9 breaking list is as recorded (tool names prefixed by the offering domain —
`homeassistant__GetLiveContext`, `intent__HassTurnOn`, also `llm__GetDateTime`,
`script__<name>`; unprefixed names "start to fail in 2027.3" per PR #179938 — that deadline
exists only in the PR text, not as a `breaks_in_ha_version` constant). New on `dev` for
2026.10 (all merged after the 2026.9 beta cut, none on tag 2026.9.0):
- `mcp_server` **`require_admin`** option (#180713, 08-30) + options flow (#180629). **New
  entries default `require_admin: True`; existing entries are migrated to minor_version 2
  with `require_admin: False`** — Zoe's existing entry keeps non-admin access until flipped.
  Per-API `/api/mcp/<API ID>` endpoints already require admin except the Assist API.
- `device_id`: `params._meta["io.home-assistant/device_id"]` → `LLMContext.device_id`
  (#182057, 09-13) — the panel-to-room hook the tracker wants.
- Tool schemas in OpenAPI 3.1.0 form (#182767, 09-20); required params advertised (#182105);
  tool metadata in the LLM API (#182614/#182700).
- `helpers/llm.py` deprecates tools returning raw JSON instead of `ToolResult` (breaks
  2027.11.0; #182487/#182538/#182551, 09-18/19) — relevant only if Zoe ever registers an
  LLM tool *inside* HA.
- `llama_cpp` integration: no breaking changes; 2026.9.x had streaming-capability (#179886)
  and date-serialisation (#179947) fixes.

**Recorder schema:** `SCHEMA_VERSION = 53` on 2026.5.2, 2026.9.0, 2026.9.3 **and `dev`** —
**no DB migration between 2026.5.2 and 2026.10-dev**; the upgrade is rollback-safe on the
schema axis.

**Custom integrations:** HACS still 2.0.5 (2025-01-28), no 2026.9 issue filed. `auth_oidc`
v1.2.1 (2026-08-19; fixes open-redirect GHSA-gg9c-6c2r-6x28 — "update as soon as possible");
**#422 (open, 2026-09-15): after v1.2.1, kiosk tablets on `trusted_networks` +
`allow_bypass_login` land on `/auth/oidc/welcome` instead of auto-login** — that is the panel's
path; test it before/after. localtuya: **HA 2026.9 removed `VacuumEntityFeature.BATTERY`, so
any localtuya vacuum with a battery DP crashes on load** on rospogrigio (#2285 open, fix PR
#2286 unmerged); the xZetsubou fork fixed it on master (#851, 2026-09-05) and the
`ATTR_VIA_DEVICE` deprecation on 2026-09-25 (#866) — **neither is in a tagged release**
(latest 2026.7.0); open #855 (EntityPlatform leak on reload → OOM over days).

**What it means.** 2026.5.2 → 2026.9.3 is schema-neutral and the tool-name work is already
done (bridge is REST; `ha_tool_names.py` detects the scheme). The two 2026.10 items are
exactly the runbook's follow-up — and `require_admin` will *not* lock Zoe out on upgrade
(migration keeps `False`). Two pre-flight checks join the runbook: the OIDC kiosk auto-login
path (#422) and whether any localtuya entity is a vacuum with a battery DP (if yes, stop at
2026.8 on rospogrigio or move to the xZetsubou master).

**Action.** B0.12 pt 2: add the two pre-flight checks and the "existing MCP entry migrates to
`require_admin: False`" fact; add the 2027.11 `ToolResult` deprecation as a note (no Zoe
impact today). Keep "one monthly release at a time".

## 7. Music Assistant 2.10.x

**What changed.** Nothing stable: **2.10.4 (2026-09-18) is still latest**; only
`2.11.0.devYYYYMMDD03` nightlies follow (latest `2.11.0.dev2026092603`). `ghcr.io/music-assistant/server:stable`, `:latest` and `:2.10.4` resolve to the **same
digest** today, so `stable` ≡ current release — pin `2.10.4` for reproducibility. Facts for
the 2.8.7 → 2.10.4 jump, read from the release notes and pins:
- **Sendspin protocol moved**: `aiosendspin` 5.1.1 (2.9.0) → **9.1.1** at 2.10.4, with
  breaking 8.0.0 (2026-08-07, playback-server selection) and 9.0.0 (2026-08-10, PIN pairing
  window); pairing-token support restored in 2.10.2 (#6122); encryption + `source@v1` in
  2.10.0. Expect to re-pair / re-verify Sendspin players, the panel included.
- **AirPlay** now the unified `cliairplay` binary (native AirPlay 2, PTP, MediaRemote —
  #4879), streaming-mode setting with automatic fallback (#5721), AP2 port-0 discovery fix in
  2.10.2 (#6185). **Regression pattern filed**: #6243 (2026-08-29, closed 09-01) — a
  shairport-sync 4.3.7-AirPlay2 + nqptp receiver produced **no audio in PTP mode** after
  2.9 → 2.10; workaround was forcing streaming mode away from Automatic. Open: 5–8 s AirPlay
  start latency vs instant Sendspin (#6407), Sendspin→AirPlay bridge start offset (#6419),
  HomePod OS27 silence (#6430/#6469). Nothing filed against shairport-sync 5.x specifically.
- **YT Music**: the provider still installs `yt-dlp[default]` + `bgutil-ytdlp-pot-provider`
  **unpinned at runtime**, defaults `po_token_server_url = http://127.0.0.1:4416`, needs the
  `__Secure-3PAPISID` cookie, and fails setup if the PO server is unreachable. **bgutil
  2.0.0 (2026-09-08)** is the mandatory security release (RCE GHSA-qpv9-8xfj-xx9m): the
  server **binds localhost by default**, rejects browser-originated and non-JSON `/get_pot`
  requests, blocks PAC proxy schemes; the docker recipe is `-p 127.0.0.1:4416:4416`. A server
  still on 1.3.2 fails with "plugin: 2.0.0, HTTP server: 1.3.2" (support #6390, closed
  2026-09-11). Zoe's container is on 2.0.0 already — **if MA and the PO server are not in the
  same network namespace, the new localhost bind refuses MA** unless the container is started
  with `--host 0.0.0.0` deliberately (then keep the port published on 127.0.0.1 only).
- Local audio provider retired (2.10.0, #5965); up to 3 concurrent YT streams (2.10.2,
  #6160); YT as realtime source + keep-retrying PO server (2.10.4, #6373/#6326).
- `music-assistant-client` **1.5.1 (2026-08-17)**: `/auth/login` now nests credentials under
  `credentials` and reads `token` (falls back to `access_token`); flat fields are still sent,
  so it is backward-compatible with older servers. `music-assistant-models` 1.1.212
  (2026-09-13).

**What it means.** The two-stage probe the review prescribes needs three explicit checks:
Sendspin re-pairing, the panel's shairport-sync 5.1 receiver in PTP/Automatic mode (be ready
to pin the streaming mode per #6243), and the PO-token bind. The client-library bump to
1.5.1 is required against ≥2.8.4 servers and is safe against 2.8.7.

**Action.** B0.12 "MA 2.10 client check": expand to "pin `2.10.4`; bump
`music-assistant-client` 1.5.1 first; probe Sendspin pairing + panel AirPlay (PTP) + PO bind
(`127.0.0.1:4416` reachable from MA's namespace)".

## 8. Omi (B9)

**What changed.** No firmware GitHub release since `omi_firmware_v2.0.4` (2024-11-26) —
firmware ships via app OTA; the CV1 config reports `CONFIG_BT_DIS_FW_REV_STR="3.0.21"` and
the app gates SD sync on ≥3.0.20. Firmware commits since 2026-06-01: SD durability + keep-SD-
powered-while-connected + ring-buffer sync fixes (07-05/10), auto-save to SD every 2 s in
sync mode (07-18), T5838 AAD hardware-VAD tuning with software VAD removed (08-27), "block
DevKit pusher while idle" (#15226, **2026-09-25**). Confirmed from source today
(`sdks/device/PROTOCOL.md`, `sdks/device/rust/src/lib.rs`, `app/.../bt_device.dart`):
- UUIDs unchanged: service `19b10000-…`, audio notify `19b10001-…`, codec read
  `19b10002-…` (suffix `e8f2-537e-4f6c-d104768a1214`), battery 0x180F/0x2A19; extra app
  services: settings `19b10010` (dim `…11`, mic gain `…12`, charging `…13`), features
  `19b10020/21`.
- Codec ids: **0 = PCM16, 1 = PCM8, 20 = Opus 160-sample/10 ms @100 fps (DevKit), 21 = Opus
  FS320 320-sample/20 ms @50 fps (CV1)**; the app also enumerates mulaw 10/11, aac, lc3. Codec
  21 was formally documented in every SDK by PR #11256 (2026-08-09); **docs.omi.me's Protocol
  page is stale (no 21)**; PCM16 recognition fixed in the Rust SDK #13050 (2026-09-08).
- Packet header **3 bytes** (u16 LE packet number + u8 index), then Opus payload; CV1 encoder
  config: 32 kbps VBR, complexity 3, `RESTRICTED_LOWDELAY`, CELT, 320-sample packages
  (~16 kB/s on the wire; codec 20 ~8 kB/s). Output PCM16 mono 16 kHz.
- Offline storage: legacy multi-file commands (`0x10 LIST`, `0x11 READ`, `0x12 DELETE`,
  `0x03 STOP`) and, for firmware ≥3.0.20, the **ring-buffer protocol**: `0x10 INFO`,
  `0x11 READ [start_seq u64 BE][count u32 BE]`, `0x12 ADVANCE [read_seq u64 BE]`,
  `0x13 CLEAR`, `0x03 STOP`; notifications `0x01 ACK`, `0x02 INFO`, `0x03 DATA`, `0x04 DONE`,
  `0x05 READ_BEGIN`; 444-byte records = 4-byte timestamp + 440 audio bytes
  (`app/lib/services/devices/ring_protocol.dart`). Storage characteristic UUID not
  re-verified in this pass (UNVERIFIED).
- **Offline / local transcription:** none in firmware; the official stack is cloud. The
  backend is self-hostable and the mobile docs now point at a local backend harness (PR
  #11532, 2026-08-14); the app supports custom STT providers for pcm8/pcm16/opus/opus_fs320
  and an offline-batch path that survives BLE disconnect (#11084, #15049 2026-09-20); the
  macOS desktop app has on-device dictation/summaries (#13886/#12181/#14513, Sep 2026); a
  "Local AI provider" PR (#13538) is **not merged**. No first-party bring-your-own-STT toggle
  in the mobile app.
- Hardware: CV1 = nRF5340 + nRF7002 Wi-Fi 6, dual T5838 PDM mics; DevKit 2 = XIAO nRF52840
  Sense (8 GB storage, speaker, button). Firmware SDK per docs: CV1 v2.9.0, DevKit v2.7.0.

**What it means.** `labs/omi-receiver/omi_bridge.py` already carries the right UUIDs and codec
table (0/1/20/21 with `CODEC_ID 21` for CV1); the confirmed 3-byte header and 320-sample
frames match. The `RING_CLEAR` posture in B9.0 maps to `0x13 CLEAR` on the ≥3.0.20 ring
protocol, and the B9.6 optional drain is `INFO/READ/ADVANCE` with big-endian integers over
444-byte records. Nothing upstream provides local STT — Moonshine remains Zoe's job, as
planned. The hardware-VAD firmware change (08-27) means the pendant may already suppress
silence before it reaches Zoe; the lab gate's "<1 % packet gaps" metric must distinguish
AAD-gated silence from BLE loss.

**Action.** B9.1: add "confirm firmware rev via DIS `2A26` ≥3.0.20 before relying on the ring
protocol; AAD gating vs packet loss in the gap metric". B9.0(a): name `0x13 CLEAR` as the
stock-firmware clear command. Note the stale docs.omi.me page (use `PROTOCOL.md`).

## 9. GitHub: `pull_request_target` policy, Copilot Lite, CodeRabbit, ggshield (B0.10)

**`pull_request_target` — enforced 2026-11-02.** Source: changelog 2026-09-17 "Workflow
execution protections in GitHub Actions generally available". Mechanics, read from the docs:
public repos with no applicable Actions **event policy** get a default rule that **disables
`pull_request_target`**; it is in *evaluate* mode now and auto-enforces on 2026-11-02 for
repos still on the default. The allow-list is an Actions event policy (rule type
`restrict_action_events` with `allowed_events`) at repo/org/enterprise level; rules can
target **specific workflow file paths** (`include`/`exclude` globs), with enforcement
`disabled` / `active` / `evaluate`. UI: **Settings → Actions → Policies**. A disallowed
event "will fail with an error" (`Event 'pull_request_target' is not allowed to trigger
Actions workflows. Workflow file: '…'`) — a failed run, not a silent skip and not a read-only
token (rendering under the *default* rule UNVERIFIED). Applies to personal repos; only
Policy Insights is Enterprise-only. No fork-vs-same-repo distinction; nothing about
self-hosted runners. API: `GET/POST /repos/{owner}/{repo}/actions/policies`,
`GET/PUT/DELETE …/policies/{id}`; no `gh` subcommand — `gh api -X POST … --input body.json`
(field names: read the REST page first). **Measured today:**
`gh api repos/<owner>/<repo>/actions/policies` → `{"total_count":0,"policies":[]}` — **no
explicit policy exists, so the default rule applies to Zoe and `voice-gate.yml` +
`break-glass.yml` fail from 2026-11-02.** Fix = one repo event policy allowing
`pull_request_target` scoped to those two paths, everything else default-blocked; a community
thread claims a user-created rule enforces immediately rather than on 11-02, so create it in
`evaluate` first and confirm via the Actions tab filtered on `event:pull_request_target`.

**Copilot code review — Lite → Balanced on 2026-09-28.** Source: changelog 2026-08-28
("the review effort value of Default uses Balanced starting September 28th, 2026"); effort
levels GA'd 2026-08-07. Cost per the docs: **Lite ≈ $0.05–$1, Balanced ≈ $0.25–$5** of AI
credits per review. Click paths: **repo** — Settings → Code, planning, and automation →
Copilot → Code review → "Review effort level" → **Lite** (not Default); **personal account** —
profile → Copilot settings → Code review (opened to all plans on 2026-09-23; also toggles
auto-review on new PRs / pushes / drafts). Effort chosen at request time wins; the repo
setting governs automatic reviews; `gh pr edit --add-reviewer @copilot` supplies no effort,
so which default it falls to is UNVERIFIED. Code review reads `.github/copilot-instructions.md`,
`.github/instructions/**/*.instructions.md` **and `AGENTS.md`** — Zoe's ~600-line
`AGENTS.md` is ingested wholesale and inflates credit use; a short review-scoped
`.github/copilot-instructions.md` would bound it.

**CodeRabbit (free on public repos).** Pricing: "free reviews forever for public
repositories"; docs: OSS repos get Team features free with **1–10 PR reviews/hour scaled by
star count, 100–300 files/review, limits per repo** (how "OSS" is classified is UNVERIFIED —
expect the low end). Setup: coderabbit.ai → Login with GitHub → personal account → "Only
select repositories" → Install & Authorize (app needs read+write on checks, code, commit
statuses, issues, pull requests). Minimal `.coderabbit.yaml`:

```yaml
reviews:
  profile: chill                    # quiet | chill | assertive
  request_changes_workflow: false   # default: verdict is COMMENT, never "Request changes"
  auto_review:
    enabled: true
    drafts: false                   # default: drafts skipped until ready-for-review
```

Non-blocking by default (no required check published); drafts skipped by default — matches
the draft-first flow with zero config. Its inline comments are ordinary review threads and
**count toward `required_conversation_resolution`** — the same resolve-every-thread cost as
Copilot.

**GitGuardian / ggshield.** Releases: **1.54.0 (2026-08-26), 1.55.0 (2026-09-24)**;
`ggshield-action` **v1.55.0 (2026-09-25)** (`v1` and `v1.55.0` are the same commit;
`validate.yml` pins `v1.53.0`). No breaking change to `secret scan ci`, exit codes or
`.gitguardian.yaml` in 1.53–1.55. Notables: 1.53 deprecates `ggshield install -t <assistant>`
for `ggshield machine setup` and **fixes the global hook skipping repo-local hooks in git
worktrees — existing global installs must re-run `ggshield install --mode global --force`**
(Zoe's workflow is all worktrees); 1.54 ships a native Rust `ggshield` via pip/pipx (Python
as `ggshield-py`); 1.55 adds `api_timeout` / `GITGUARDIAN_API_TIMEOUT` and parallel commit
batches per `GG_MAX_WORKERS` (`secret scan ci` included). Free Starter plan unchanged (25
devs, 1 GB, no honeytokens).

**What it means.** B0.10 has two dated deadlines inside a week and six weeks respectively,
and one of them (Copilot) is a click, the other (policy) is a settings object we can create
via API. The ggshield bump is low-risk; the worktree hook fix is a box step.

**Action.** B0.10 → split: (a) 🧑 **before 2026-09-28** set Copilot effort = Lite at repo and
account level; (b) **before 2026-11-02** create the repo event policy allowing
`pull_request_target` for `.github/workflows/voice-gate.yml` + `break-glass.yml` (evaluate →
active; verify in the Actions tab); (c) `ggshield-action` v1.53.0 → v1.55.0 in
`validate.yml`; 🧑 `ggshield install --mode global --force` on the box if a global hook is
installed; (d) CodeRabbit install + the yaml above; (e) optional
`.github/copilot-instructions.md` to stop `AGENTS.md` being billed per review.

## 10. Python 3.10 EOL (2026-10-31) — aarch64 wheels (B0.7 owns the deep dive)

| package | latest | `requires_python` | cp310 aarch64 @latest | last with cp310 aarch64 |
|---|---|---|---|---|
| onnxruntime | 1.30.0 | >=3.11 | no | **1.23.2** (2025-10-27); 1.24.x says >=3.10 but ships no cp310 wheel |
| onnxruntime-gpu | 1.30.0 | >=3.11 | no | none on PyPI (never aarch64) — Jetson index only |
| torch | 2.14.0 (2026-09-02) | >=3.10 | **yes** | 2.14.0 |
| av | 18.1.0 | >=3.11 | no | 17.1.0 (2026-06-07) |
| numpy | 2.5.3 | >=3.12 | no | 2.2.6 (2025-05-17) |
| scikit-learn | 1.9.1 | >=3.11 | no | 1.7.2 (2025-09-09) |
| websockets | 17.1 | >=3.11 | no | 16.1.1 (2026-07-17) |
| chromadb | 1.5.9 | >=3.9 | cp39-abi3 aarch64 (3.10 and 3.12) | 1.5.9 |

Jetson index (`pypi.jetson-ai-lab.io`; the `.dev` host is HTTP 410): `jp6/cu126` is
**cp310-only** (torch 2.8.0/2.9.1/2.10.0/2.11.0, last upload 2026-04-01; onnxruntime-gpu
1.23.0/1.24.0); `jp6/cu129` carries both cp310 and cp312 (torch 2.8.0 cp312, ORT-GPU
1.22/1.23 cp312); `sbsa/cu130` is cp312-only.

**Implication (one paragraph).** The CUDA side (Kokoro, llama-server, any Jetson torch or
ORT-GPU) can stay on 3.10 indefinitely because its wheels come from `jp6/cu126`, which is
cp310-only and still received uploads in 2026; moving it to 3.12 would mean the cu129 index
(a CUDA bump on JetPack 6.2.1) and is not worth it. That 3.10 environment is, however, now
frozen at onnxruntime 1.23.2, numpy 2.2.6, av 17.1.0, scikit-learn 1.7.2, websockets 16.1.1 —
every one has already dropped cp310 on PyPI, so security fixes stop arriving there after
10-31. That is exactly the case for B0.7 (zoe-data on a 3.12 venv): there it gets onnxruntime
1.30 (CPU aarch64 wheel exists), numpy 2.5, av 18, chromadb 1.5.9 (abi3) and PyPI CPU torch
2.14 — at the cost of a per-interpreter split of `requirements.txt` (markers or two files) and
a re-baseline of the voice-gate probe, which never installs requirements and must be pointed
at whichever interpreter actually runs the STT path.

**Action.** B0.7: note the two-interpreter split explicitly (system 3.10 = CUDA consumers
from `jp6/cu126`; venv 3.12 = zoe-data) and the probe re-baseline; B0.6's `requirements.txt`
header gains a one-line pointer to the split once B0.7 lands.

## 11. Chroma 1.5.x + MemPalace 3.10 (B0.8)

**What changed.** Nothing in either since 09-25. chromadb latest **1.5.9 (2026-05-05)**
(1.5.4 03-08 … 1.5.9 05-05); `requires_python >=3.9`; wheels **cp39-abi3
manylinux_2_17_aarch64** — install on both 3.10 and the 3.12 venv. `chroma-hnswlib` is gone
from runtime deps (dev extra only); the index is the Rust crate — 1.5.6 fixed an "assertion in
hnswlib integrity check", **1.5.9 "preserve legacy `hnsw:` metadata keys" (#6953)** — directly
relevant to MemPalace's `hnsw:space=cosine`. numpy `>=1.22.5`, no upper pin. **The official
migration doc moved** to `https://docs.trychroma.com/docs/overview/migration` (the old
`/production/administration/migration` URL 404s); its v1.0.0 entry lists the Rust rewrite,
built-in auth removed, `list_collections` returning `Collection` objects, four ignored
in-process settings, `--config config.yaml` — **and no data-migration step for 0.6 → 1.x**.
`chroma-migrate` is only the 0.4 duckdb→sqlite tool; `chroma utils vacuum` is only for
<0.5.6 upgrades. "Auto-migrates 0.4.1+ databases on first open" is asserted by MemPalace's own
`migrate.py`, not by Chroma's docs (UNVERIFIED); issue #4217 shows a 0.6.3 store failing on
early 1.0.3 (closed 2025-07). So: expect Rust sysdb migrations to run on first open, and test
on the copy.

MemPalace latest **3.10.0 (2026-09-16)**, `requires_python >=3.9`. **Correction:** the
chromadb bound flipped to `>=1.5.4,<2` at **3.4.0 (2026-06-06)**, not 3.6.0 — both the
`requirements.txt` comment (line ~82) and `migrate.py`'s docstring are wrong; 3.3.1 declares
`chromadb>=0.5.0`. `mempalace migrate` reads drawers straight from `chroma.sqlite3`
(bypasses the API), detects the source version by schema, preflights HNSW divergence, probes a
write round-trip (some 0.6→1.5-migrated collections "remain readable while writes and deletes
silently no-op" — the exact failure the requirements comment warns about), **copies the palace
to `<palace>.pre-migrate.<ts>`** (retention `max_backups=10` since 3.4.1), rebuilds in a temp
dir, swaps with `os.replace` (rollback on failure), and prints `Drawers migrated: N` plus
**`WARNING: Expected X, got Y`** on a count mismatch — that is the built-in row-count
reconciliation, to be cross-checked against `export_memory_store.py`. 3.3.1 already has the
`_fix_blob_seq_ids` fix, `repair --mode max-seq-id` and `hnsw:space=cosine` on create.
3.4 → 3.10 breaking for a 3.3.1 user is confined to 3.10.0: `mempalace rules --agent` removed
(`--host/--harness/--project`); `get_collection()` rejects names other than the configured
drawers collection and `mempalace_closets` (`_skip_name_check=True` for scripts); MCP event
listing newest-first; XDG config dir for **new installs only**. No MCP tool renames —
additions only (`mempalace_checkpoint`, `mempalace_delete_by_source`,
`mempalace_kg_supersede`, `mempalace_task_create`, `mempalace-light-mcp`). 3.6.0 fixes
recovery misquarantining valid HNSW; 3.8.0 fixes the ~440 MB per-collection-open growth.

**What it means.** The copy-first plan stands and gains three specifics: the migration must
run *on the copy* with mempalace's own `migrate` (it does the backup + reconciliation), the
Chroma side has no tool of its own (first open migrates), and 1.5.9 is the target because of
the `hnsw:` key fix. Any script that opens collections by name other than the drawers
collection must pass `_skip_name_check=True` after 3.10.

**Action.** B0.8: "target chromadb 1.5.9 + mempalace 3.10.0; `mempalace migrate` on the copy
(built-in `.pre-migrate.<ts>` backup + `Expected X, got Y` reconciliation), then
`export_memory_store.py` counts + `memory_recall_probe`; audit `get_collection` callers for
`_skip_name_check`". Fix the requirements comment (3.6.0 → 3.4.0) in the next deps PR.

## 12. New and material for a local voice companion (since ~2026-09-12)

- **STT — Parakeet Redux + Parakeet Ultra (Moondream, 2026-09-22).** Redux: ternary encoder,
  **178 MB / 149M params, CPU-targeted, 25 languages, streaming API, CC-BY-4.0** (EN WER
  6.26 → 6.55 avg, FLEURS 11.62 → 10.56); Ultra: full-precision post-trained v3 for GPU
  (TED-LIUM 2.71 → 1.94). Community GGUF/CoreML ports exist. The first credible
  Moonshine-class CPU STT — a lab benchmark on the replay corpus, not a rock swap.
- STT too big: Edge0 **Audio8-ASR-Infinite** (2026-09-21, Apache-2.0, streaming with
  built-in semantic turn detection, **4B / 8.17 GB**) — the ASR-owns-endpointing idea is the
  takeaway for B1; NetEase Confucius4-R2T2 (09-10, GGUF 09-20, licence "other") UNVERIFIED.
  No Whisper/Voxtral/Canary releases in the window.
- **TTS — Kyutai Pocket TTS v3.2.0 (09-23) / v3.3.0 (09-24)**: French 6-/24-layer models,
  retrained ES/IT/PT/DE, new Dutch, default temperature 0.3; training code released 08-25.
  Still the strongest B5.4 (Pi-panel CPU voice) candidate. Kokoro: nothing (§3). Supertonic 3
  / Chatterbox Turbo: no September releases.
- **VAD — Silero VAD v6.2.2 (2026-09-17)**: new offline **ONNX "sequence" model
  `silero_vad_16k_sequence.onnx` that releases the GIL** (`load_silero_vad(sequence=True)`,
  16 kHz only); **v6.2.3 (09-23)**: torchaudio optional, `import silero_vad` works without
  onnxruntime. **Not a drop-in for the live VAD:** the sequence model is an OFFLINE
  whole-utterance graph, while `services/zoe-data/voice_vad.py` runs the streaming model in
  512-sample hops with a `(2,1,128)` recurrent state passed to every inference
  (`voice_turn.py` is a different component — Smart Turn v3.2 scoring the last 8 s of a
  turn, not Silero). A file swap would NOT fall back to RMS: RMS is chosen only when
  `create_vad()` fails to *load* the model; a model that loads but rejects the streaming
  inputs makes `process_hops` swallow the inference error and return no probabilities, so
  the live VAD stays selected and simply reports no speech — a silent failure, worse than
  the fallback. It would need a streaming adapter to be used live; its GIL release is useful
  only where the whole clip is already in hand (replay harness, lab scoring). Live VAD stays
  on the streaming v6 model (the v6.2.1 file already in place).
- **Diarization — NVIDIA Nemotron 3 Diarization (2026-09-23)**: ~100M-param streaming/offline,
  up to 8 speakers, 320 ms labels; ONNX/GGUF ports 09-23/24. Relevant to B4.1/B9.3 (a
  multi-speaker detector), not the hot path.
- Smart Turn: `pipecat-ai/smart-turn-v3` unchanged since 2026-01-07 — no v3.3/v4.
- **LLMs ≤ ~5 GB**: LiquidAI `LFM2.5-2.6B` QAD checkpoint (09-22, Q4_0 1.59 GB) and
  **LFM2.5-VL-3B-DSpark (09-24)** — a speculative-decoding draft with day-one llama.cpp
  support (up to 3.13×). The DSpark draft pattern is the interesting piece for a fixed-brain
  box, not a swap. Everything else trending is too large (Ternary-Bonsai-2-27B PTQ1_0 5.95 GB,
  Xing4.0-29B-A4B, MiMo-V2.6). Nothing from Google/Qwen/Microsoft in the window.
- **Home devices.** Apple: iOS 27 with Siri AI shipped **2026-09-14**; Gurman (MacRumors
  09-21) puts the ~7-inch Siri-AI home display (J490 tabletop / J491 wall) in **October 2026**
  with household face recognition, intercom, apps; audioOS 27 carries the "Linwood" Siri
  subsystem (09-23). Google: Home Speaker with Gemini for Home shipped 2026-06-25; nothing new
  beyond the Home Premium promo ending 09-30. Amazon: **no dated 2026 fall event found** — every
  "September 30" hit is the 2025 event (UNVERIFIED/absent).

**What it means.** No rock moves. The bar-setting event is Apple's October display with
household face recognition — B4 (identity) is the line to protect; B4.3's face-ID decision
becomes more urgent, not less. Concrete follow-ups, in order: Silero 6.2.2 sequence ONNX for
the OFFLINE replay/lab path only (B1.4, not live); Kokoro → ONNX-CUDA (B5.1, §3); a Parakeet Redux vs Moonshine 0.0.62 lab
benchmark on the corpus (new, small); Nemotron 3 Diarization as the B9.3 multi-speaker
detector candidate.

**Action.** B1.4: evaluate the v6.2.2 sequence model for the OFFLINE replay/lab path only
(GIL-free whole-clip scoring); live VAD stays on the streaming v6 model — a swap would need a
streaming adapter plus validation that actually exercises the live VAD path, not a file copy.
The replay harness (`voice_regression_probe.py` / `measure_voice.py`) starts at transcription
and never runs VAD or barge-in, and the CI-safe VAD lanes inject a FAKE `voice_vad` with
scripted probabilities, so green there cannot catch an adapter that returns no probabilities.
The check that can: (a) `process_hops` on a corpus clip through the adapter returns
non-empty probabilities that go high on speech; (b) the **host-only real-model lane** in
`test_voice_barge_in.py` (`test_silero_real_model_detects_speech_across_corpus`,
`test_real_voice_triggers_barge_from_cooldown` — real Silero, real corpus audio, asserts a
`stop_playback`; skipped on CI runners, so it must be run on the box); (c) a **successful
live interruption** on the panel with Kokoro playing, alongside the B1.3 false-barge count
(which only measures unwanted interruptions). New lab item under B5/B1: Parakeet Redux benchmark on the replay corpus (WER + per-file ms
vs 0.0.62). B9.3/B4.1: Nemotron 3 Diarization added to the shadow-week candidates.

## 13. Proposed tracker deltas (for Jason to fold in — not applied here)

1. **B0.10 → split with dates:** (a) 🧑 Copilot review effort = Lite, repo + account, **before
   2026-09-28**; (b) repo Actions event policy allowing `pull_request_target` for
   `voice-gate.yml` + `break-glass.yml` (evaluate → active), **before 2026-11-02** — measured:
   no policy exists today; (c) `ggshield-action` v1.53.0 → v1.55.0 + 🧑 global hook
   re-install; (d) CodeRabbit install + minimal yaml; (e) optional review-scoped
   `.github/copilot-instructions.md`.
2. **B0.4 (llama.cpp rebuild):** "b11194 confirmed (≡ b11178 for this build; +httplib 0.58.0);
   #25522 is multi-GPU-only — drop it; gate adds: `GGML_CUDA_GRAPHS` on in the binary,
   f16-K/q8_0-V on a compiled FA kernel (`-DGGML_CUDA_FA_ALL_QUANTS=ON` if not), `-np 1`
   (#28286), multi-tool + thinking turns in the replay (#28827); budget the MTP compute arena
   (#27282); watch #29391/#29467 (prefix-reuse abort under memory pressure, b11159+)". B6.2:
   the same replay content applies to the template swap.
3. **B1.11 (Flue, parked):** "2.2.0 is `next`-only (2.2.0-next.1, 2026-09-25); it bumps Pi to
   0.87.1 which changes `ProviderStreams` input to `TranscriptContext` — port
   `capped-completions.ts` / `context-window.ts` (`getCurrentSystemPrompt()` /
   `getCurrentTools()`, tools-removed system message) with a cap negative control before any
   2.2.0 bump; only #9816 (strict schemas) is a Flue-reachable llama.cpp fix".
4. **B1.10 (Moonshine, held):** "upstream silent since 0.1.5 (no commits, no perf issue);
   watch #229 (shared Silero VAD across concurrent streams); 🧑 decide on filing the §2 draft".
5. **B5.1 (Kokoro ONNX):** add the recipe (`onnxruntime-gpu==1.24.0` from `jp6/cu126` cp310,
   `kokoro-onnx==0.6.1`, fp16 graph from `model-files-v1.1`, numpy pinned to the ORT wheel) and
   "no published Jetson RAM numbers — measure with a CPU-EP control".
6. **B0.12 pt 2 (HA):** add pre-flight checks — `auth_oidc` #422 kiosk auto-login path after
   v1.2.1; localtuya vacuum-with-battery crash on 2026.9 (rospogrigio unfixed, xZetsubou master
   only); record "recorder schema 53 unchanged 2026.5 → 2026.10-dev" and "existing MCP entry
   migrates to `require_admin: False`"; MA: pin `2.10.4`, `music-assistant-client` 1.5.1 first,
   probe Sendspin re-pair + panel AirPlay PTP mode (#6243 pattern) + PO-token bind from MA's
   namespace.
7. **B0.7 (py3.12 venv):** note the two-interpreter split (system 3.10 = CUDA consumers from
   `jp6/cu126`, cp310-only; venv 3.12 = zoe-data) and the voice-gate probe re-baseline.
8. **B0.8 (Chroma/MemPalace):** target chromadb 1.5.9 + mempalace 3.10.0; use `mempalace
   migrate` on the copy (built-in backup + `Expected X, got Y`); audit `get_collection`
   callers for `_skip_name_check`; fix the requirements comment (bound flipped at 3.4.0).
9. **B1.4:** Silero VAD v6.2.2 sequence-ONNX is NOT a live drop-in (offline whole-sequence
   graph; `voice_vad.py` runs 512-sample hops with recurrent state) — evaluate it for the
   OFFLINE replay/lab path only; live VAD stays on the streaming v6.2.1 file already in
   place; any live use needs a streaming adapter + real audio through it (`process_hops`
   returns non-empty, speech-high probabilities; the host-only real-model lane in
   `test_voice_barge_in.py` run ON THE BOX — CI fakes `voice_vad`) + a successful live
   interruption on the panel, not only the false-barge count — the replay harness starts at
   STT and does not exercise VAD. **New lab item:** Parakeet Redux (178 MB CPU STT, 2026-09-22) benchmark vs
   Moonshine 0.0.62 on the replay corpus. **B4.1/B9.3:** Nemotron 3 Diarization (09-23) as a
   multi-speaker-detector candidate.
10. **B9.1:** confirm firmware rev (DIS `2A26` ≥3.0.20) before relying on the ring protocol;
    separate AAD hardware-VAD gating from BLE loss in the "<1 % gaps" metric; B9.0(a) names
    `0x13 CLEAR`; prefer `sdks/device/PROTOCOL.md` over the stale docs.omi.me page.
11. **Review doc corrections** (state-of-zoe §5.6/§5.8): Pi scope is `@earendil-works/*`
    (0.83.0 / 0.82.1), not `@mariozechner/*`; the "two llama.cpp fixes in 2.2.0" are one
    Flue-reachable fix (#9816) plus #8275 from 0.84.3.
12. **§2 checklist row 12/14 context:** Apple's October home display (face recognition,
    intercom) is now dated by Gurman (09-21) — keep B4.3 ahead of B7.4.

## Sources

- llama.cpp: https://github.com/ggml-org/llama.cpp/releases/tag/b11193 ·
  https://github.com/ggml-org/llama.cpp/pull/25148 · https://github.com/ggml-org/llama.cpp/issues/25522 ·
  https://github.com/ggml-org/llama.cpp/pull/27781 · https://github.com/ggml-org/llama.cpp/pull/28183 ·
  https://github.com/ggml-org/llama.cpp/pull/28285 · https://github.com/ggml-org/llama.cpp/pull/29152 ·
  https://github.com/ggml-org/llama.cpp/pull/28549 · https://github.com/ggml-org/llama.cpp/pull/28511 ·
  https://github.com/ggml-org/llama.cpp/pull/29115 · https://github.com/ggml-org/llama.cpp/issues/29391 ·
  https://github.com/ggml-org/llama.cpp/issues/29142 · https://github.com/ggml-org/llama.cpp/issues/27282 ·
  https://github.com/ggml-org/llama.cpp/issues/28827 ·
  https://github.com/ggml-org/llama.cpp/blob/b11194/common/jinja/README.md ·
  https://huggingface.co/unsloth/gemma-4-E4B-it-qat-GGUF/discussions
- Moonshine: https://github.com/moonshine-ai/moonshine/releases ·
  https://pypi.org/pypi/moonshine-voice/json ·
  https://github.com/moonshine-ai/moonshine/blob/main/CHANGELOGS.md ·
  https://github.com/moonshine-ai/moonshine/issues/211 ·
  https://github.com/moonshine-ai/moonshine/issues/229 ·
  https://github.com/moonshine-ai/moonshine/blob/main/core/ort-utils/ort-utils.cpp
- Kokoro: https://huggingface.co/api/models/hexgrad/Kokoro-82M · https://pypi.org/pypi/kokoro/json ·
  https://github.com/thewh1teagle/kokoro-onnx/pull/198 ·
  https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1 ·
  https://pypi.jetson-ai-lab.io/jp6/cu126/+simple/onnxruntime-gpu/ ·
  https://forums.developer.nvidia.com/t/onnxruntime-gpu-wheel-with-numpy-2-x-support/341795 ·
  https://github.com/dusty-nv/jetson-containers/tree/master/packages/speech/kokoro-tts
- Flue / Pi: https://registry.npmjs.org/@flue/runtime ·
  https://github.com/withastro/flue/releases/tag/%40flue%2Fruntime%402.1.1 ·
  https://github.com/withastro/flue/releases/tag/%40flue%2Fruntime%402.2.0-next.1 ·
  https://registry.npmjs.org/@earendil-works/pi-ai · https://registry.npmjs.org/@mariozechner/pi-ai ·
  https://github.com/earendil-works/pi/blob/v0.87.1/packages/ai/README.md ·
  https://github.com/earendil-works/pi/blob/main/packages/ai/CHANGELOG.md ·
  https://github.com/earendil-works/pi/blob/main/packages/coding-agent/CHANGELOG.md
- Chroma / MemPalace: https://pypi.org/pypi/chromadb/json ·
  https://github.com/chroma-core/chroma/releases/tag/1.5.9 ·
  https://docs.trychroma.com/docs/overview/migration · https://docs.trychroma.com/docs/cli/vacuum ·
  https://github.com/chroma-core/chroma/issues/4217 · https://pypi.org/pypi/mempalace/json ·
  https://github.com/MemPalace/mempalace (CHANGELOG.md, mempalace/migrate.py)
- Home Assistant: https://github.com/home-assistant/core/releases ·
  https://www.home-assistant.io/blog/2026/09/02/release-20269/ ·
  https://github.com/home-assistant/core/pull/179938 ·
  https://github.com/home-assistant/core/pull/180713 ·
  https://github.com/home-assistant/core/pull/182057 ·
  https://github.com/home-assistant/core/blob/dev/homeassistant/components/recorder/db_schema.py ·
  https://github.com/hacs/integration/releases ·
  https://github.com/christiaangoossens/hass-oidc-auth/releases/tag/v1.2.1 ·
  https://github.com/christiaangoossens/hass-oidc-auth/issues/422 ·
  https://github.com/rospogrigio/localtuya/issues/2285 ·
  https://github.com/xZetsubou/hass-localtuya/releases · https://github.com/xZetsubou/hass-localtuya/issues/855
- Music Assistant: https://github.com/music-assistant/server/releases ·
  https://github.com/Sendspin/aiosendspin/releases ·
  https://github.com/Brainicism/bgutil-ytdlp-pot-provider/releases/tag/2.0.0 ·
  https://github.com/music-assistant/support/issues/6390 ·
  https://github.com/music-assistant/support/issues/6243 ·
  https://github.com/music-assistant/support/issues/6407 ·
  https://github.com/music-assistant/client/pull/214 · https://pypi.org/project/music-assistant-client/ ·
  https://www.music-assistant.io/installation/
- Omi: https://github.com/BasedHardware/omi/blob/main/sdks/device/PROTOCOL.md ·
  https://github.com/BasedHardware/omi/commits/main/omi/firmware ·
  https://github.com/BasedHardware/omi/releases · https://github.com/BasedHardware/omi/pull/11532 ·
  https://github.com/BasedHardware/omi/pull/13538 · https://docs.omi.me/doc/hardware/OmiConsumer ·
  https://docs.omi.me/doc/hardware/DevKit2 · https://docs.omi.me/doc/developer/Protocol
- GitHub: https://github.blog/changelog/2026-09-17-workflow-execution-protections-in-github-actions-generally-available/ ·
  https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target ·
  https://docs.github.com/en/actions/how-tos/administer/control-workflow-execution ·
  https://docs.github.com/en/actions/concepts/about-actions-policies ·
  https://docs.github.com/en/rest/actions/policies ·
  https://github.com/orgs/community/discussions/208237 ·
  https://github.blog/changelog/2026-08-28-upcoming-changes-to-github-copilot-policies-and-billing/ ·
  https://github.blog/changelog/2026-08-07-copilot-code-review-effort-levels-are-generally-available/ ·
  https://github.blog/changelog/2026-09-23-copilot-code-review-more-ways-to-request-and-configure-reviews/ ·
  https://docs.github.com/en/copilot/concepts/agents/code-review ·
  https://docs.github.com/en/copilot/how-tos/use-copilot-agents/request-a-code-review/configure-automatic-review ·
  https://www.coderabbit.ai/pricing · https://docs.coderabbit.ai/management/plans ·
  https://docs.coderabbit.ai/platforms/github-com · https://docs.coderabbit.ai/reference/configuration ·
  https://docs.coderabbit.ai/configuration/auto-review ·
  https://github.com/GitGuardian/ggshield/releases · https://www.gitguardian.com/pricing
- Python / wheels: https://pypi.org/pypi/onnxruntime/json · https://pypi.org/pypi/torch/json ·
  https://pypi.org/pypi/av/json · https://pypi.org/pypi/numpy/json · https://pypi.org/pypi/scikit-learn/json ·
  https://pypi.org/pypi/websockets/json · https://pypi.jetson-ai-lab.io/jp6/cu126/ ·
  https://pypi.jetson-ai-lab.io/jp6/cu129/
- New tech / devices: https://moondream.ai/blog/introducing-parakeet-redux-and-ultra ·
  https://huggingface.co/moondream/parakeet-redux · https://huggingface.co/Edge0/Audio8-ASR-Infinite ·
  https://github.com/kyutai-labs/pocket-tts/releases · https://github.com/snakers4/silero-vad/releases ·
  https://huggingface.co/nvidia/Nemotron-3-Diarization ·
  https://www.marktechpost.com/2026/09/25/liquid-ai-releases-lfm2-5-vl-3b-dspark-speculative-decoding-for-vision-language-models-with-up-to-3-13x-faster-decoding/ ·
  https://www.apple.com/newsroom/2026/09/siri-ai-a-profoundly-more-capable-and-personal-assistant-is-here/ ·
  https://www.macrumors.com/2026/09/21/apple-smart-home-display-coming-next-month/ ·
  https://www.macrumors.com/2026/09/23/siri-ai-coming-to-homepod/ ·
  https://blog.google/products-and-platforms/devices/google-nest/new-nest-cams-home-speaker-walmart/
