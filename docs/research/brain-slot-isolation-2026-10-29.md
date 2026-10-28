---
type: research
title: Isolating the voice conversation's KV from background helpers on the brain's llama-server (2026-10-29) - second slot, slot save/restore, a separate server, night batching, and what current llama.cpp adds
date: 2026-10-29
status: research-only; nothing restarted, built, installed or changed on the live box. A lab needs its own approval (section 6).
description: Owner ask "Option 2" - give the brain a second slot, or another isolation mechanism, so post-turn helpers never evict the panel conversation's KV (Option 1, the quiet window 45 s to 180 s, is already live). Reads llama.cpp b11194 (live) and b11932 (round-3 target) from source, re-reads upstream #28286 (the rule's only basis), and measures the live journal and the PANEL_KV_MISS alarm. Verdict - a pinned second slot is the right destination but not a rescue; the measured prize today is small, it needs the newer build first, and it is net RAM-neutral only if bundled with --lazy-mode.
evidence: Live reads 2026-10-29 (names and numbers only) - systemctl --user cat llama-server.service and its drop-ins, /proc env of zoe-data (names), journalctl --user -u llama-server (4,659 completed tasks, 2026-10-18 16:44 to 2026-10-29 02:38), ~/.zoe-logs/zoe-data.app.log PANEL_KV_MISS lines, GGUF metadata of the brain and the MTP drafter, free/ps. Source - ~/llama.cpp-b11194 and opensrc ggml-org/llama.cpp@b11932, quoted by file and function. Upstream - issue 28286 and its comments, PRs 30295 30567 30498 29019 29994 26004 30574, issues 28433 27544 27388 29738, and the 742-commit b11194..b11932 title list.
builds_on: docs/knowledge/panel-kv-residency-2026-10-19.md, docs/knowledge/brain-prefix-cache-2026-10-12.md, docs/research/inference-speech-stack-2026-10-03.md, docs/research/stack-currency-and-field-2026-10-27.md (branch claude/stack-currency-2026-10-27, section 7(c) item 1), docs/CANONICAL.md (the brain is a rock; nothing here swaps it)
---

# Isolating the voice conversation's KV from background helpers (2026-10-29)

> The model, the drafter and the other rocks do not change. Evidence tags: [src] read in source, [live] measured on the running box today, [up] upstream issue or PR, [calc] arithmetic from measured inputs, [E] estimate that the lab must replace, [H] hypothesis that the lab must confirm or kill.

## 0. Answer first

1. **The "never raise `--parallel` with MTP" rule rests on one upstream issue that does not describe this stack.** #28286 was filed against a downstream fork of master (2026-09-01) running a **Qwen "qwen4exp" Gated-DeltaNet hybrid on AMD ROCm**, with a fork-only cross-turn KV patch, and says itself that it has "not yet tested on a non-recurrent target model". Zoe runs Gemma 4 (plain transformer, sliding window) on CUDA. The issue was **closed by the stale bot on 2026-10-18 as `not_planned`, with one comment, no maintainer reply and no fix**. The unit header still calls it "OPEN", as two knowledge docs did until this change corrected them (section 2). Closed-stale is not "fixed" and not "disproved"; it is "unknown for our model", which is a lab question, not a standing prohibition. Our own history is weak evidence only: the brain ran `--parallel 2` from 2026-07-21 to 2026-09-27 (MTP was part of the stack on that build) and no contamination was recorded, but the symptom has "no garbage-token signature", so nobody could have noticed.
2. **Source shows the Gemma 4 MTP path differs architecturally from the one in the bug; whether that removes the bug is a lab question.** The Gemma 4 drafter (`gemma4-assistant`, 4 layers, all four `shared_kv_layers`) has **no KV of its own**: it reads the target's KV (`is_mem_shared`, `common/speculative.cpp:1500-1581`, `Gemma4Assistant requires ctx_other`, `src/llama-context.cpp:240-245`). The per-slot state is a hidden-state carry-over row, a sampler and a verify buffer per sequence. **But b11194 has a real candidate for cross-slot drafting trouble that b11932 fixes** [H]: with separate KV streams (what `--parallel 2` without `--kv-unified` gives) a batch holding two slots' tokens is split and reordered, and b11194 reads the MTP hidden-state rows by raw batch position with no reordering (`embd_batch_idxs` does not exist in b11194; b11932 swaps unmasked NextN rows back, `output_reorder`, `src/llama-context.cpp:2440-2520`, PR #29019, merged 2026-09-30, after our build). Because the target verifies every draft, that can lower acceptance but not change a greedy output; it is the first thing the lab must test, and it argues for **testing on the newer build, not b11194**.
3. **The measured prize is small today; the structural value is real.** In the retained logs (2026-10-20 to 2026-10-29) the panel raised 18 `PANEL_KV_MISS` alarms (re-prefill over 500 tokens): **12 first-turn-after-idle, 6 warm, and only 1 of the 18 had a helper batch in between** (19 background calls across a 177 s pause; 1,285 tokens, about 2.1 s). Four of the 12 were full misses (`first_cache_n=0`); their cause is not established here (a restart is one candidate), and a second slot is not expected to touch them (unproven: if any was a displacement by Telegram, a harness or another caller, pinning would change it). What a second slot buys is (a) a guarantee that holds for every caller, not only the ones wrapped in `brain_slot_guard`, (b) the end of the long quiet window, so the memory passes land seconds after a turn instead of minutes, (c) one fewer thing that has to be true for the panel to stay warm.
4. **RAM is the real cost, and it is affordable only as a package.** A second 8,192-token slot is **+238 MiB** of KV under `--swa-full` [calc, section 3a] with no draft KV. The box has **801 MiB available now** against a voice-replay floor of 700 MB (remote) or 1,500 MB (in-process). It fits only if paid for in the same change: `--lazy-mode on` for the per-layer-embedding table (the 2026-10-03 record measured **-1.2 to -1.45 GB** and the flag is not in the live unit), and/or `--cache-ram 1024` to 512, and/or retiring Resemblyzer's torch from zoe-data (about -360 MB, round-3 item 3).
5. **The other options lose on the evidence.** Slot save/restore restores exactly the truncated state the host cache already restores (section 3b: SWA layers are written only for the last 512 positions, `src/llama-kv-cache.cpp:2077`), so it cannot fix deep-divergence evictions. A separate small server cannot do the helpers' job (the router is a 270M tool-picker with a 1,024-token context; a second Gemma-class helper model does not fit the RAM). Night batching is right for the heaviest helpers (already done) and for the two measurement-only ones (not yet done); it cannot hold the same-hour memory passes.

**Ranked recommendation (detail in section 5):** (1) **Two pinned slots on a current-tag build, bundled with `--lazy-mode on`, lab-first**; (2) **move the two shadow-only helpers (`structural_reader`, `structural_verifier`) to the night window now** - no model change, fewer slot users; (3) knobs inside (1) (`--no-cache-idle-slots`, `-b`, `--cache-ram`) decided by the lab; (4) slot save/restore - reject; (5) separate small server - reject. Keep the 180 s quiet window until the lab passes; the lab, not this note, decides the replacement.

**Decisions needed from the owner** are in section 8.

## 1. The problem, measured

**The mechanism** (already established, not re-derived): one slot, `--parallel 1`. A post-turn helper that does not extend the slot's prompt makes llama-server save the slot into the `--cache-ram` host cache and load something else. A sliding-window prompt that comes back from the host cache carries only the last 512 SWA positions, so it resumes only if the next prompt diverges within about 510 tokens; a deeper divergence re-prefills everything (`first_cache_n=0`, 3.2-3.3k tokens, about 5 s at 612 tok/s [live]). A prompt that STAYS in the slot is reused at any depth ([brain-prefix-cache-2026-10-12.md](../knowledge/brain-prefix-cache-2026-10-12.md), [panel-kv-residency-2026-10-19.md](../knowledge/panel-kv-residency-2026-10-19.md)). `brain_slot_guard` makes helpers wait for quiet; Option 1 widened the quiet window to 180 s (`ZOE_BRAIN_SLOT_QUIET_S=180` in the zoe-data process env [live]).

**What the retained evidence says about the size of the residual** [live]:

| measure | value |
|---|---|
| journal span | 2026-10-18 16:44 to 2026-10-29 02:38 AWST, 4,659 completed tasks |
| newly evaluated prompt tokens per task | p50 83, p75 139, p90 488, p95 1,356, p99 3,340 |
| tasks re-evaluating 500 or more / 1,000 or more tokens | 462 (9.9%) / 345 (7.4%) of 4,659 (includes harness, night and Telegram traffic, not only the panel) |
| prefill / decode (p50) | 612 tok/s (tasks of 500+ tokens) / 30.9 tok/s (replies of 25+ tokens; p10 22.4) |
| MTP acceptance, accepted / generated | 78,562 / 117,714 = **0.667** overall; by reply length (buckets cover the 4,506 tasks that logged both a reply length and an acceptance line; the other 153 produced no draft line): 3 or fewer 0.433 (n=823), 4-10 0.713 (319), 11-40 0.593 (2,674), over 40 0.768 (690); daily 0.596-0.757 |
| `PANEL_KV_MISS` alarms, 2026-10-20 to 10-29 | 18: 12 `first` (first turn after idle), 6 `warm`; **1** warm miss had helpers in between |
| the 4 full misses (`first_cache_n=0`, 3,223-3,244 tokens) | all `first`: 10-20 21:09, 10-21 05:30, 10-27 20:01, 10-28 06:39 |
| the 8 other `first` misses | cache 2,154 (the shared system+tools prefix) and 1,068-1,666 new tokens, about 1.7-2.7 s: the wake warm's speaker guess, not an eviction |

The journal's `draft acceptance` line is the instrument for the MTP gate; this is its baseline on b11194 (section 6, G2). Honest reading: the guard plus the quiet window have already removed most of the helper evictions; what remains is dominated by cold starts and prompt-shape divergence that no slot count changes. That is why this record does not promise a TTFT win on the panel's median turn.

## 2. Where the rule is written, and what its basis actually says

The rule appears in six places; all derive from #28286:

| where | what it says |
|---|---|
| `scripts/setup/systemd/llama-server.service` header | "OPEN, filed 2026-09-03 ... Do NOT raise --parallel while --spec-type draft-mtp is set, until #28286 is fixed AND replay-gated here." |
| `docs/knowledge/voice-pipeline.md` ("Single slot") | same wording, "open, filed 2026-09-03" |
| `tests/unit/test_llama_server_unit_flags.py::test_draft_mtp_implies_single_slot` | pins `--parallel 1` whenever `--spec-type draft-mtp` is present |
| `services/zoe-data/brain_slot_guard.py` docstring | "forced by upstream ggml-org/llama.cpp#28286" |
| `docs/knowledge/panel-kv-residency-2026-10-19.md` section 2 | "OPEN; no garbage-token signature ... MTP is part of the brain rock, so the test keeps `--parallel 1`" |
| `docs/research/inference-speech-stack-2026-10-03.md` | "`-np > 1` with draft-MTP still has #28286 cross-slot contamination upstream" |

What the issue is [up]: filed 2026-09-03 by a user on AMD Strix Halo (gfx1151, ROCm-TheRock); build is a downstream fork rebased on master `458681e1d` carrying "an unrelated cross-turn KV-cache patch plus the qwen4exp MTP integration from #27836"; model Qwen3.8-Flash-Next (a Gated-DeltaNet hybrid) with a Q8_0 MTP sidecar; `--parallel 4`, four concurrent `/v1/completions`, temperature 0; responses "drift into content belonging to a different request". It lists #28019 (qwen4exp multi-seq recurrent-state rollback corruption, now closed) as adjacent and states the effect "needs enough content complexity/entropy to surface" (a low-entropy control showed zero). It reproduces only at `--parallel > 1`. State: **closed 2026-10-18 by the stale workflow, `state_reason: not_planned`, labels `stale`, one comment (the bot's)**. The knowledge docs that said "OPEN" are corrected by this change; the unit header (still "OPEN") and the guard and test docstrings (which present #28286 as the reason without saying it is closed-stale) are voice-path files and are listed in the ledger for the replay-gated landing.

What the issue does not tell us: whether the bug exists on a non-recurrent target. Our target is non-recurrent and its drafter shares the target KV, which removes the qwen4exp-specific mechanism (a separate recurrent draft state that has to be rolled back per sequence). It does not remove the shared mechanism candidate in section 0 item 2. The rule's right reading is "do not raise `--parallel` without a content-isolation test on this exact model and build", which is what section 6 specifies. **The rule stays in force until that test passes.**

## 3. The options

### 3a. `--parallel 2` with slot pinning (`id_slot`)

**How it works.** `--ctx-size 16384 --parallel 2` gives two 8,192-token slots. Without `--kv-unified` (the default for an explicit `--parallel`; the live log prints `kv_unified = 'false'`) each slot owns a separate KV stream: `n_ctx_seq = n_ctx / n_seq_max` (`src/llama-context.cpp:389-396`), so a helper can never overwrite a conversation's cells. A request carrying `"id_slot": N` is routed to slot N (`json_value(data, "id_slot", -1)`, present in both builds); out-of-range ids wrap (`id_slot % slots.size()`, both builds), so **pinning code is harmless on a one-slot server** and rollback order is free. Policy: **slot 0 = the household panel conversation; slot 1 = everything else** (post-turn helpers, web chat, Telegram, harnesses). An unpinned conversation is not safe: slot choice is LCP similarity above 0.1 then least-recently-used, and Telegram and the panel share a 2,154-token system+tools prefix, so an unpinned Telegram turn can land on slot 0, or LRU can pick slot 0 simply because the helper slot was used last.

**Pinning is unsafe on b11194 and fixed on b11932.** In b11194 `get_available_slot` sets `ret` to the pinned slot even when it is busy, then computes `f_keep = f_sim_best*n / ret->prompt.size()`, which is 0 for a slot skipped as busy, so `update_cache = true`: the host cache's best match is loaded into a slot that is mid-generation (b11194 `server-context.cpp:1553-1634`). Upstream #30295 (merged 2026-10-10, in b11932) returns a busy pinned slot untouched and defers the request (`server-context.cpp:1782-1792` in b11932). A barge-in leaves a voice generation winding down while the next turn arrives, so this window is real. **The newer build is a prerequisite for pinning the voice slot, not an optional extra.**

**KV memory per slot** [calc, checked against the GGUF metadata read today]. Gemma 4 E4B: 42 layers, 18 share KV (`shared_kv_layers`), so 24 own it: 4 global (2 KV heads x (512 K + 512 V)) and 20 SWA (2 x (256 + 256)); with `--swa-full` the SWA layers are sized like the global ones. Elements per token: 4 x 2,048 + 20 x 1,024 = 28,672; at q8_0 (1.0625 B/element) **30,464 B per token, 238 MiB per 8,192-token slot**. Two slots: 476 MiB total, **+238 MiB**. The drafter adds nothing (all four layers borrow the target's cells). Compute-buffer growth with `n_ctx` 16,384 is small but unmeasured [E: under 50 MiB]. The unified pool (`--kv-unified`) is not recommended: it would save about 180 MiB on a 10,240-cell pool, but in unified mode `--cache-idle-slots` saves and CLEARS idle slots when a new task starts (`[TAG_IDLE_SLOT_CLEAR]`) and `try_clear_idle_slots` purges an idle slot when the pool fills, either of which empties the voice slot, defeating the purpose.

**Is the pool shared or split?** Split, by stream, as above. `--cache-ram` stays one shared host cache; with `cache_idle_slots` (default on, both builds) every task launch also saves the other idle slot into it (`alloc` skips a prompt already contained, so a steady conversation costs one copy per growth, about 50 MiB of memcpy [calc: 4.5k tokens x 8.5 KB global + 512 x 21.8 KB SWA]). `--no-cache-idle-slots` is a lab arm.

**MTP and SWA per slot.** SWA: unchanged, a resident slot keeps its full cells (`--swa-full`, `n_swa = 0` in the server), so any divergence depth reuses (the property we want). MTP: speculative state is per sequence (`pending_h`, `verify_h`, `smpls`, `chain` buffers sized `n_seq`, `common/speculative.cpp:1511-1525`); the drafter builds its batch across drafting sequences and the server batches only same-task-type slots (`can_batch_with`). The open question is the b11194 hidden-state row order in a two-slot batch (section 0 item 2 [H]); the lab compares acceptance on both builds. Not applicable here: #28433 (draft context sized from total `n_ctx`; on Gemma 4 the drafter has no own KV) and #27544 (Vulkan-only slowdown with `-np > 1` and MTP depth over 1; a commenter reports CUDA unaffected) - both are watch items for the RSS and speed gates, not blockers.

**Latency, and is the GPU contended? Yes, at token granularity.** One GPU, one decode thread: slots share batches, they do not run in parallel (`update_slots`: generating slots' tokens go in first, then pending prompt tokens fill the batch up to `n_batch`, default 2,048; the batch is decoded in `n_ubatch` 512 pieces). So a helper prefill that overlaps a voice turn stalls the voice step it shares a batch with by the helper's remaining prefill time: a 470-token helper is about 0.77 s at 612 tok/s [calc]; a 5.5k-token portrait or day-review prompt is multiple seconds per chunk of 2,048. Cheap overlaps are free: a helper decoding 2 tokens adds one row to a batch. Consequences: (i) isolation fixes **residency**, not **compute**; the guard stays, with a much shorter window (the lab picks it), (ii) heavy helpers stay night or idle only, (iii) `-b 512` is a lab arm that bounds one stall to about 0.84 s at no cost to the voice prefill (it already runs in 512-token ubatches). llama-server has no request priority between slots (queue is FIFO), so there is no flag that makes voice win a shared batch.

**Risks.** (1) The unknown #28286-class behaviour on Gemma/CUDA (gate G1). (2) The b11194 busy-pin bug (build prerequisite). (3) RAM (+238 MiB; section 0 item 4). (4) A wedge: #27388 (open) reports generation stalling with `/health` still OK and `/slots` hanging, seen with `--parallel 1` and 2, with and without MTP, on CUDA and CPU; a two-slot server does not add this exposure, but the lab soak must probe `/slots`, not `/health`. (5) Code that has to change: about fifteen direct callers build their own payloads; a pin must come from one helper in `brain_slot_guard` (code-structure cleanup rule: no fifteen copies), and the Flue sidecar needs a per-turn pin via its existing `streamSimple` wrapper (`labs/flue-zoe-brain-2x/src/providers/capped-completions.ts:376-402`; pi-ai 0.83.0 passes a returned object from `options.onPayload` to the request, `openai-completions.js:130`). (6) The wake-time KV warm (`ZOE_BRAIN_KV_WARM_ON_WAKE`) sends a prompt for the likely speaker; if that prompt diverges from what slot 0 already holds, the server truncates the slot back to the common prefix (the shared 2,154-token system+tools block at worst), so the warm must skip when slot 0 already holds that speaker's conversation.

**How to test.** Section 6.

### 3b. `--slot-save-path` save/restore (`/slots/{id}?action=save|restore`)

**How it works.** `POST /slots/0?action=save` with `{"filename": ...}` writes the slot's sequence state to `--slot-save-path`; `restore` reads it back (`server-context.cpp:3156-3280`, b11932). The path flag exists in both builds (`LLAMA_ARG_SLOT_SAVE_PATH` env was added after b11194, #30272). Newer builds also preserve context checkpoints (#26004) and write the draft state (#30574), neither of which changes the point below.

**The decisive fact: the file holds the same truncated state as the host cache.** `llama_kv_cache::state_write` skips every cell for which `is_masked_swa(...)` when a sequence id is given (`src/llama-kv-cache.cpp:2077`, identical in b11194 and b11932; `--swa-full` does not change it). So a saved Gemma slot contains the global layers whole and only the last 512 positions of the SWA layers. The 2026-10-19 lab on the running build (270M SWA model, `--swa-full --slot-save-path`) measured it: restore reports all 3,600 tokens but resumes exactly where the host cache does and fails exactly where it fails (divergence 600 tokens deep: full re-prefill). Restoring therefore buys nothing the automatic host cache does not already give, and it cannot restore a deeply diverging prompt. Server code also shows why no flag changes it: with `--swa-full` the server creates no SWA checkpoints (`do_checkpoint` needs `n_swa > 0`, `server-context.cpp:4296-4309`), so after a truncated restore there is no checkpoint to fall back on and the server logs "forcing full prompt re-processing" (`:4208-4214`).

**Cost on this box** [calc, [E] for I/O]: a 4.5k-token slot is about 38 MiB of global-layer KV plus about 11 MiB of SWA tail = about **49 MiB** per save (the earlier "240 MiB" figure was the full-cache size, which is not what is written). NVMe is ext4 on `/dev/nvme0n1p1`; at 1-2 GB/s that is roughly 25-50 ms per file, plus the host-to-device copy on restore; against a re-prefill of 3.2k tokens (about 5.2 s) restore is faster *when it works*. It runs inside the server's single task loop (the voice slot cannot be decoding while it saves), needs an orchestrator around every helper batch, and writes through the Tegra page cache, which the cold-start drop-in documents as a hazard (NvMap does not reclaim page cache). tmpfs (`/dev/shm`, 7.7 GB) avoids the disk but is RAM, i.e. the host cache again.

**Interaction with MTP.** Gemma 4's drafter has no state in the file (it shares the target KV), so MTP is unaffected.

**Verdict: reject for eviction.** The one niche is persisting a conversation across a brain restart (the night window stops the brain); there the same truncation applies, and the 4 full `first` misses are not shown to be restart-caused. Park it.

### 3c. A separate small llama-server for the helpers

**What the helpers are** (from the modules' own contracts; sizes are the 2026-10-12 trace):

| helper | where it runs | prompt / output | needs the 4B? | needs real time? |
|---|---|---|---|---|
| person extractor (`person_extractor_llm`) | after each spoken turn | ~370 tokens, up to 300 out, JSON facts | yes (extraction accuracy was a measured axis; grammar-constrained JSON) | no: results feed memory; same-day recall tolerates minutes |
| turn digest (`memory_digest.run_turn_digest`) | after each turn (skips "what is" turns) | ~470 tokens, 2 out when nothing | yes | no |
| latent-intent detector | after each turn | ~290 tokens, up to 350 out | yes | no, it raises an offer later |
| quote-retire judge | on a change cue | short, 24 out | yes (judgement) | no |
| structural reader / verifier | after the digest, `shadow` only | reader up to 640 out; verifier 2 tokens, grammar yes/no, ~280 ms | yes | **no, and they decide nothing: a measurement** |
| night mind, day review, portrait, consolidation | night window or idle | 1.4k-5.5k tokens | the 12B at night | no |

**Why not a smaller model.** The live candidates are (i) the FunctionGemma router: a 270M tool-picker, CPU-only, **`--ctx-size 1024`**, 634 MB resident, fine-tuned to pick a tool, not to emit fact JSON or judge a quote (its own record found the router better than the 4B at tool picking, 0.9996 vs about 14% wrong, which is a different job); (ii) a second Gemma-class instruction model for helpers (E2B-class, about 1.4 GB of weights plus KV plus a CUDA context) - **does not fit**: 801 MiB is available now, and a second instance of the brain is 5.8 GB. Weights cannot be shared across two llama-server processes on CUDA (each copies them into its own device buffer). Quality is also unmeasured: the memory records treat extraction accuracy as a gate (balanced accuracy 0.82-0.89 even on the 4B for the verifier). "Adopt, don't rebuild" and the fixed-rocks rule both say do not invent a helper model for a problem a flag solves.

**Verdict: reject.** If a helper ever needs to leave the 4B it is a quality bake-off, not an isolation fix, and RAM blocks it today.

### 3d. Move helpers to the night window, or batch them

- **Already done:** night mind, day review, portrait, consolidation, Omi review run in the 12B night window (`docs/knowledge/night-window.md`), outside the 4B's slot.
- **Not real-time at all, and not yet moved:** `structural_reader` and `structural_verifier` ("shadow only ... a measurement"; they feed `STRUCTURAL_FLOOR` log lines and a would-retire log and decide no row). They are 2 of the up-to-6 slot users per turn. Moving them to the nightly digest costs nothing the product can see and removes the largest per-turn count of tiny prompts.
- **Not safely movable:** person extractor, turn digest, latent intent, quote-retire. Memory freshness is the product ("capture, don't lose"; the facts a member says an hour ago should be recallable). A night-only design delays them up to a day, and the day's transcript is already stored, so nothing is lost, but a same-day "what did I tell you about the plumber" would miss. With the 180 s window they already run minutes late. A middle path is the 12B night pass re-deriving them, which is an owner trade (quality up, freshness down), not an isolation fix.
- **Batching** is what `brain_slot_guard` already does (serial flush after quiet, 240 s max defer). Batching cannot remove the eviction, only the count of evictions per quiet period.

### 3e. What current llama.cpp offers (b11932, from source)

| feature | verdict for Zoe |
|---|---|
| `--cache-ram` (host prompt cache) | live at 1024 MiB; entries are truncated for SWA (section 3b); lowering to 512 recovers up to 512 MiB of steady RSS once the conversation has its own slot - a lab arm |
| `--cache-idle-slots` (default on) | with two non-unified slots it copies the idle slot into the host cache at each task start; `--no-cache-idle-slots` is a lab arm; harmless on one slot |
| prompt-cache selection fixes since b11194: "prefer empty slots" (#30567), "save only restorable slot states" (#30498), off-by-two in the SWA reuse threshold (#30580), SWA checkpoint invalidation (#30666), failed-restore cleanup (#27530), busy-slot pin (#30295) | all reduce unforced re-prefills; the round-3 doc already names the SWA two as plausible causes of the warm misses; they come free with the build |
| `--cache-reuse N` (KV shifting) | becomes legal with `--swa-full` (`llama_kv_cache_iswa::get_can_shift` needs equal base and SWA sizes, true now) but it re-uses KV computed under a different earlier context, so outputs are not equivalent to a re-prefill; **not recommended**: breaks the byte-identical gate and the replay semantics, and does not address slot eviction |
| `--kv-unified`, `--kv-unified-per-slot` | rejected for the voice slot (section 3a) |
| `--lazy-mode on` (PLE table) | not an isolation feature but the **RAM offset that pays for a second slot**: -1.2 to -1.45 GB per the 2026-10-03 record, with the Gemma 4 prefetch fix (#29599) in the newer build |
| session-keyed automatic slot save/restore (#29738, open PR) | upstream interest, same truncation caveat; watch only |
| slot priority / per-slot GPU share | none exists; the queue is FIFO |
| `--spec-draft-n-ubatch` (#30459), probabilistic MTP sampling (#27694, #29638 EOG), logprobs with speculation (#27196) | outside this question; probabilistic MTP changes sampling at temperature above 0 and is its own one-flag window per the 2026-10-03 record |

### 3f. One more idea, not requested: prefix-forked helpers

Run each helper as a continuation of the conversation's own prompt (same system, tools and history bytes, then the helper instruction as the last message, tools disabled). The helper then extends the resident prompt, `f_keep` is high, nothing is saved or evicted, and the next turn truncates back to the shared prefix in place. Zero RAM, no pin, no MTP question. Costs: the sidecar renders the exact bytes, so the call has to go through it; the helper sees the persona and the whole conversation (quality and privacy review per helper); a 3k-token context for a 100-token job. Pin it on [docs/IDEAS.md](../IDEAS.md) as the zero-RAM alternative if the lab fails G1; not recommended before the lab.

## 4. Comparison

| option | fixes eviction? | RAM | latency effect | MTP / SWA interaction | main risk | effort | verdict |
|---|---|---|---|---|---|---|---|
| (a) `--parallel 2` + pins, newer build, `--lazy-mode` | yes, structurally, for every caller | +238 MiB KV; net **-1.0 to -1.2 GB** with lazy-mode | hot turn unchanged [E]; paused turn re-prefill 0; a helper overlapping a turn can stall it (guard stays) | draft shares target KV; per-seq state; b11194 row-order [H] fixed in b11932 | unknown #28286-class behaviour on Gemma/CUDA; busy-pin bug on b11194 | build lab + sidecar and zoe-data pin helper + unit drop-in + test rewrite | **do, lab-first** |
| (b) slot save/restore | no (state is truncated like the host cache) | ~49 MiB per file; page cache | restore ~25-100 ms when it works | none | disk and page-cache churn; orchestration | small | reject |
| (c) separate small server | partly (helpers off the slot) | +0.6 GB (router-class, wrong job) to +2 GB (Gemma-class) | none on voice | none | helpers' quality; RAM does not exist | large | reject |
| (d) night / batch | partly | 0 | none | none | memory freshness | small | **do for structural reader and verifier** |
| (e) cache-ram, idle-slot save, `-b` | tuning only | -512 MiB possible | bounded stall | none | tuning without evidence | flags | lab arms inside (a) |
| (f) prefix-forked helpers | yes | 0 | helper call cheaper | none | quality and privacy per helper; sidecar plumbing | medium | pin the idea, not now |

## 5. Ranked recommendation

1. **Two pinned slots on a current-tag build, bundled with `--lazy-mode on`, lab-first.** It is the only option that removes the failure by construction. It is not urgent: the measured helper-involved residual is one alarm in nine days (of 18 alarms). It is worth doing because it (i) makes the panel's warmth independent of every non-guarded caller (night, Telegram, harnesses, the next helper someone adds), (ii) lets the quiet window shrink so memory passes land promptly, and (iii) rides on work the round-3 plan already wants (the llama.cpp lab build, `--lazy-mode`), so the marginal cost is the pin plumbing and the gates. Do it in this order: build lab (round-3 item) -> G1/G2 isolation and acceptance on the new build -> RAM and TTFT gates -> replay gate -> landing in a normal brain window.
2. **Move `structural_reader` and `structural_verifier` to the night pass now.** Independent of 1, no model change, two fewer slot users per turn.
3. **Lab arms inside 1:** `--no-cache-idle-slots`, `-b 512`, `--cache-ram 512`, quiet window 20 s vs 180 s.
4. **Reject (b) and (c)** with the reasons above; they are recorded so nobody re-runs them.
5. **Keep the stopgap** (180 s) until the lab passes; do not shorten the window in the same landing as the second slot - change one thing per window.

## 6. Lab plan and gates (needs the owner's approval; nothing here has been run)

**Where.** The lab needs the real 4B, which cannot run beside the live brain (5.8 GB), so it is a **brain window**: `/tmp/zoe-brain-window.lock`, Kokoro paused, one flag set per window, outside the 01:50-04:55 night window and outside the awake-hours voice-harness pause (`night_busy.sh test`, `docs/knowledge/night-window.md`); evenings after 22:00 or 04:55-07:00. All voice harness runs under `flock /tmp/zoe-voice-harness.lock`. The new binary goes in its own directory (`~/llama.cpp-<tag>/build-jetson`, the b11194 recipe in `voice-pipeline.md`, same CMake flags: `GGML_CUDA=ON`, arch 87, `GGML_CUDA_FA=ON`, `GGML_CUDA_GRAPHS=ON`); the b11194 binary and unit stay untouched for rollback. Target tag: b11932 (round-3, published 2026-10-27) or the newest at lab time; v0.8.0 and b11968 appeared 2026-10-28, so re-diff `b11932..<tag>` for `tools/server`, `common/speculative` and `src/models/gemma4`. The 14-day dependency rule is waived in the dev phase (owner, 2026-10-17); record that it was.

**Arms** (each its own window, each against the same-session control):

| arm | build | slots | flags beyond the live set |
|---|---|---|---|
| C0 control | b11194 | 1 | live unit as is (control for everything) |
| C1 | new tag | 1 | same flags (isolates the build) |
| A1 | new tag | 2 (`--ctx-size 16384 --parallel 2`), pinned | same flags |
| A2 | new tag | 2 | A1 + `--lazy-mode on` |
| A3 | new tag | 2 | A2 + `--no-cache-idle-slots`, then `-b 512`, then `--cache-ram 512` (one at a time) |
| N1 negative control | b11194 and new tag | 2 | pin a request at a busy slot with a cached better match, to show #30295's difference (why the build is a prerequisite) |

**Gates** (all must pass for A2 or A3 to proceed; thresholds are proposals for the owner to adjust):

| gate | measure | pass |
|---|---|---|
| G1 isolation | 200 concurrent pairs, distinct high-entropy prompts (code vs history vs recipes, per the shape in #28286), temperature 0, fixed seed, each pair also run solo; canary n-gram scorer for any run of 8+ tokens that appears in the other prompt or its output | **zero** leaks; every non-identical output must differ from solo by no more than the run-to-run noise floor measured in C1 (solo vs solo with an idle neighbour slot); first-divergence index reported. A single leak kills the option. |
| G2 MTP acceptance | `draft acceptance` lines (accepted / generated) over the replay-corpus-shaped request mix, by reply-length bucket | within 0.03 of C1 in every bucket, and C1 within 0.05 of the live b11194 baseline 0.667 (so a build effect is not blamed on slots) |
| G3 paused conversation (the goal) | warm a panel-shaped conversation on slot 0, send 6 helper calls (the digest/extractor/intent trio twice) pinned to slot 1 inside a 60 s pause, then the next turn | re-prefill at most the true new tail plus 50 tokens (`first_cache_n` at least prompt minus new tail minus 50); TTFT within 10% of the no-helper control; the same sequence on C0/C1 shown as the failure |
| G4 hot turns | `scripts/perf/measure_brain_prefix_cache.py` isolated and guarded conditions (extend with a `two-slot` condition); then an *overlap* condition with a helper decoding, and one with a 470-token helper prefilling, when a turn arrives | isolated/guarded TTFT p50 within +5% and p95 within +10% of C1; overlap: p95 added latency at most 700 ms for helpers of 500 tokens or fewer, otherwise the guard window stays and the lab says by how much |
| G5 RAM | brain RSS (`ps`), MemAvailable, swap, cgroup `memory.current` before and after load and after a 2 h mixed soak | brain RSS delta at most +300 MiB for A1; A2 net negative; MemAvailable after is at least the value before and never below the replay floor (700 MB remote / 1,500 MB in-process); swap growth 0; `MemoryLow` raised to cover the new RSS in the drop-in |
| G6 voice replay | `scripts/maintenance/voice_regression_probe.py` against `~/.zoe-voice-samples`, baseline-compared, under the lock; per-stage speed from `scripts/perf/measure_voice.py` | said-vs-did no regression (a previously working command that now fails is a bug), per-stage speed no regression; the artifact is for the exact commit or a tree-identical one, because `deploy.yml` passes `--expect-tree-of` |
| G7 stability | 24 h soak with synthetic panel traffic plus helpers; probe `/slots` (not only `/health`) every 30 s | zero `decode() failed`, zero wedge (the #27388 signature), `n_slots = 2` in the start log, no unexpected `purging slot` or `forcing full prompt re-processing` on slot 0 after a helper batch |
| G8 pin plumbing | unit tests for the one pin helper and the sidecar `onPayload`; a test that `id_slot` on a one-slot server wraps to 0 | green; the unit-flags test is rewritten to "draft-mtp with `--parallel` over 1 requires a build allowlisted as gated, and the pin flag on" (not deleted) |

**Instruments that are known to lie, and the control for each** (`feedback_verify_your_instruments`): `systemctl is-active` (use `/health` and `/slots`), the in-process replay probe (invisible to zoe-data counters; read the llama-server journal), `first_prompt_n` alone (join it to the slot trace), a "byte-identical" claim without a noise floor (CUDA kernels are not guaranteed batch-invariant: C1's solo-versus-neighbour run sets the floor, and G1's discriminating test is the canary scorer, not equality). Break the fix on purpose in one arm (pin both to slot 0) and confirm G3 goes red.

**After a lab pass, the landing** (separate PRs, one thing per window): (1) unit drop-in `90-two-slot.conf` (binary path, `--ctx-size 16384 --parallel 2`, the lab's flags, `MemoryLow`) in `scripts/setup/systemd/llama-server.service.d/` like `50-cold-start.conf`, never a template copy, plus the rewritten unit-flags test; (2) the pin helper in `brain_slot_guard` and the sidecar change, both flag-gated `ZOE_BRAIN_SLOT_PIN` (default off); (3) the wake warm skips when slot 0 already holds the speaker; (4) the quiet window set to the lab's number. Voice-path: replay-gated, deploy-gated, not while the household is awake (probes stop Kokoro for about 15 minutes), not in 01:50-04:55.

## 7. Rollback

- **Before the server change:** nothing to roll back; the lab leaves the live unit and binary untouched.
- **After landing, fastest:** set `ZOE_BRAIN_SLOT_PIN=off` (no `id_slot` is sent; the two-slot server then behaves like LRU/LCP selection, safe but not isolated) and restart zoe-data and the sidecar; poll `/health`.
- **Full:** delete the `90-two-slot.conf` drop-in so the unit returns to the b11194 binary, `--ctx-size 8192 --parallel 1` and its current drop-ins (`80-swa-full.conf`, cold-start), `systemctl --user daemon-reload`, restart in a brain window (the cold-start drop-in handles the Tegra page-cache load); verify `n_slots = 1` in the start log, `/health`, a probe turn's `first_cache_n`, and the unit-flags test. Pin code needs no change because `id_slot` wraps on one slot. Cost: the voice is mute for the model load (tens of seconds), so do it in a window.
- **Rollback of the build alone:** point `ExecStart` and `LD_LIBRARY_PATH` back at `~/llama.cpp-b11194/build-jetson`; the flag spellings are the same (the b9733 rollback recipe in the unit header is the precedent).

## 8. Decisions needed from the owner

1. **Lab window.** Approve one brain-stop evening window (22:00-01:50) for the build lab; it is the same window round-3 item 1 already needs, so this adds arms, not a second outage.
2. **RAM package.** Approve `--lazy-mode on` as part of the same change (the −1.2 to −1.45 GB offset that makes +238 MiB safe), or choose another offset (cache-ram 512, torch out of zoe-data).
3. **Slot policy.** Slot 0 = the household panel conversation only; Telegram, web chat, helpers and harnesses share slot 1. (Alternative: a third slot for chat; costs another 238 MiB, not recommended.)
4. **Shadow helpers to night.** Move `structural_reader` and `structural_verifier` to the nightly pass.
5. **Stopgap.** Keep the 180 s quiet window through the lab (default).
6. **Rule change.** Replace "never raise `--parallel` with MTP" with "not without the isolation test on this model and build" in the unit header, voice-pipeline.md and the test - at the landing, not before.

## 9. Not verified, and limits

- No server was started and nothing was written to the box; every latency and RAM figure for a second slot is [calc] or [E] until the lab runs. The +238 MiB is arithmetic from the GGUF metadata and matches the 37-43 MiB host-cache entries the journal already shows for a 3.3k prompt; compute-buffer growth is unmeasured.
- The b11194 hidden-state row-order mechanism (section 0 item 2) is a reading of two code paths and the text of #29019; it is a hypothesis for the lab, and #29019's title and body speak of DFlash drafters, not MTP.
- #28286 was not reproduced; the claim is only that its stated environment differs from ours. No maintainer analysed it.
- The journal statistics mix panel, harness, Telegram and night traffic; the `PANEL_KV_MISS` alarm is household-panel only and starts 2026-10-20 in the retained logs. Nine days is a small sample; one helper-involved alarm in 18 should not be read as "helpers never matter".
- Storage speed and page-cache behaviour for slot files were not measured (writing to the live NVMe next to the brain was out of scope).
- The upstream commit list was read by title for 742 commits; only the PRs named above were read in full.

## 10. Sources

Repo: `scripts/setup/systemd/llama-server.service`, `tests/unit/test_llama_server_unit_flags.py`, `services/zoe-data/brain_slot_guard.py`, `labs/flue-zoe-brain-2x/src/providers/capped-completions.ts`, `docs/knowledge/{panel-kv-residency-2026-10-19,brain-prefix-cache-2026-10-12,brain-flags-tuning-2026-09,voice-pipeline,night-window}.md`, `docs/research/{inference-speech-stack-2026-10-03}.md`, round-3 doc on `claude/stack-currency-2026-10-27`, `scripts/perf/measure_brain_prefix_cache.py`, `scripts/maintenance/voice_regression_probe.py`.
llama.cpp b11194 (`~/llama.cpp-b11194`) and b11932 (`opensrc path ggml-org/llama.cpp@b11932`): `tools/server/server-context.cpp` (`get_available_slot`, `update_slots`, slot save/restore, checkpoints), `tools/server/server-task.cpp` (`server_prompt_cache`), `common/speculative.cpp` (`common_speculative_impl_draft_mtp`), `src/llama-context.cpp` (`n_ctx_seq`, `get_embeddings_nextn_ith`, `output_reorder`), `src/llama-kv-cache.cpp` (`state_write`), `src/llama-kv-cache-iswa.cpp` (`get_can_shift`), `common/arg.cpp`.
Upstream: issue 28286 (closed 2026-10-18, not_planned, stale) and its single comment; issues 28433, 27544, 27388, 29738; PRs 30295, 30567, 30498, 29019, 29994, 26004, 30574.
