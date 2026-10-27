---
type: Reference
title: Why the live brain re-prefilled its whole prompt on half of voice turns (2026-10-12)
description: Root cause of first_cache_n=0 on the 4B brain (one slot shared with post-turn callers + a host prompt cache that keeps only 512 SWA positions), the evidence, the lab proof on the running llama.cpp build, the fix (brain_slot_guard), before/after numbers, and what was not done.
tags: [voice, latency, prefix-cache, llama-server, swa, slot, brain, measurement]
timestamp: 2026-10-12T19:45:00+08:00
---

# Why the live brain re-prefilled its whole prompt on half of voice turns

**Symptom.** 2026-10-12 17:16-18:49 AWST, live panel session: `FLUE_PROMPT_CACHE first_cache_n=0` on 11 of 42 brain turns (more in the recall-heavy stretch: 8 of 16 between 18:27 and 18:35). A miss is `prompt eval 5.1-6.7 s / 3.4-4.4k tokens (670 tok/s)` = the whole time to first token; a hit is 160-600 ms. The 2026-10-09 note ([first-sound-latency-2026-10-09.md](first-sound-latency-2026-10-09.md)) measured "prefix cache healthy, median re-prefill 1 token" on an isolated harness: no other caller touched the slot.

## Root cause (two facts that only hurt together)

1. **The slot is shared.** `llama-server --parallel 1` (forced while `--spec-type draft-mtp` is set, upstream #28286). After every spoken turn zoe-data's post-turn memory passes POST to the same slot: three calls per turn, 2 output tokens each ("[]"), fixed prefixes of ~376 / ~476 / ~296 tokens with the utterance last. They are `person_extractor_llm` (370-token prompt), `memory_digest.run_turn_digest` (470), `latent_intent_detector` (288 with the person hint), all launched from `routers/voice_tts.py::_run_voice_memory_passes` (the digest skips "what is ..." turns, hence 2 calls on those). Token counts of the static prompts match the log's `new=9..53 / slot_after=385, 485, 303`; `ss` shows the connections come from the zoe-data process, 3 per turn. (The 9-token tasks in the review are these.)
2. **A conversation prompt that leaves the slot comes back truncated.** A helper that does not extend the slot's prompt makes llama-server save the slot into the `--cache-ram` host cache and load something else (`get_available_slot` -> `prompt_save` / `prompt_load`, `tools/server/server-context.cpp:1547-1660`). For a sliding-window model the saved state holds only the LAST 512 positions of the SWA layers: `llama_kv_cache::state_write` skips cells for which `is_masked_swa(...)` and `GGML_UNUSED(flags)` (`src/llama-kv-cache.cpp:2055-2123`); Gemma 4 E4B has `sliding_window=512` and 35 of 42 layers are SWA. `--swa-full` makes the IN-SLOT cache complete; it does not change what a saved state contains. On resume the server checks `pos_min >= pos_min_thold` (`server-context.cpp:3296-3385`); with `--swa-full` `n_swa=0`, so the host-cache copy passes only when the new prompt diverges from it within its last ~510 tokens, otherwise "forcing full prompt re-processing due to lack of cache data" (a TRACE line, invisible at the unit's default verbosity) and `cache_n=0`.

So a prompt that STAYS in the slot is reused at any divergence depth, and one that went through the host cache is reused only if the next prompt extends it (or diverges in its last ~510 tokens). The live prompts diverge DEEPER than that on: a recall turn (the previous turn's ~550-token recall block is elided from history, `ZOE_BRAIN_ELIDE_STALE_BLOCKS`; the sidecar logs chars/4 = 434, real tokens ~1.27x), a tool-block growth (the router shortlist unlocks a group: divergence at ~2.7k), and a history-window jump. That is why the misses cluster on recall turns and after idle gaps, and why every hit had a tiny previous tail.

## Evidence

- Live `FLUE_PROMPT_CACHE` / `FLUE_CONTEXT_BUDGET` rows joined to the llama-server task log (`journalctl --user -u llama-server`, task ids, `selected slot by LRU`, `n_tokens` before/after): every miss follows a helper trio; every miss had a previous-turn block (tail 433-515 est.) or a tools change; every hit had a previous tail of 44-66.
- The trace is reproducible: a scratch `llama-server` (same b11194 build, `-lv 5`, a 270M Gemma-3 SWA model on CPU, `--swa-full --cache-ram 1024 --parallel 1`) prints `found better prompt ... state_read_meta: cell_count = 6131 ... cell_count = 512 ... forcing full prompt re-processing`. Divergence sweep on a 3601-token prompt (`cache_n` after one helper call evicted it): d=0 -> 3602, 200 -> 3402, 500 -> 3102, **511 -> 0, 600 -> 0, 1000 -> 0**; with no helper in between: every depth hits (d=1000 -> 2602).
- Checkpoint mode (no `--swa-full`, the pre-2026-10-04 config) recovers some depth but not reliably (d=520 -> 0, d=600 -> 2654, d=1000 -> 0 after a helper) and costs RAM: not a fix.
- Source of the history wipe found on the way: see "Second finding" below.

## Fix

1. **`services/zoe-data/brain_slot_guard.py`** (`ZOE_BRAIN_SLOT_GUARD`, default on; `ZOE_BRAIN_SLOT_QUIET_S` 45; `ZOE_BRAIN_SLOT_MAX_DEFER_S` 240). `run_flue_brain_streaming` brackets each turn (`turn_started` / `turn_finished`); each background 4B caller holds `background_slot(...)`: it waits while a turn is in flight or ended under 45 s ago, then runs serially. A backlog is flushed together if a call reaches the max defer (never one call per 240 s). Nothing is dropped or reordered; only WHEN the call is made changes, and memory capture that does not need the 4B (the regex passes, explicit "remember ...") is untouched. Callers gated: person_extractor_llm, latent_intent_detector, the per-turn digest, structural_reader, structural_verifier, memory_retire's judge (not when it targets a clone brain). `tests/test_brain_slot_guard.py` pins the list and the semantics (fake clock, negative control).
   Rejected: `--parallel 2` + `id_slot` pinning (#28286 leaks content between concurrent requests with draft-mtp; also RAM); checkpoint mode (above); a second llama-server (RAM, 15.6 GB box); warming the prefix after the helpers (a host-cache round trip leaves the slot truncated again).
2. Nothing in the server args or the unit changes: no restart of llama-server, no replay of the template.

## Numbers

Live-shape bench `scripts/perf/measure_brain_prefix_cache.py` (24 turns, bursts of 6, 1 in 3 turns a recall turn with a 560-token block, two tool-block growths, three helper calls per turn; synthetic text sized with `/tokenize`; first turn excluded). Same sequence per condition.

| target | condition | miss rate | first_prompt_n p50 / p95 | first_cache_n p50 | TTFT p50 / p95 |
|---|---|---|---|---|---|
| **live 4B** (Gemma 4 E4B QAT + MTP, `:11434`, 19:28-19:36 AWST, `/tmp/zoe-brain-window.lock`) | isolated (old harness) | 0% | 37 / 1251 | 4327 | 166 / 2156 ms |
| | **interleaved (before)** | **34.8%** | 606 / 5438 | 3413 | **1058 / 8504 ms** |
| | **guarded (after)** | **0%** | 37 / 1230 | 4323 | **160 / 1992 ms** |
| scratch 270M SWA model (CPU) | interleaved | 34.3% | 604 / 5676 | 4268 | (projected live 1060 / 8617) |
| | guarded | 0% | 37 / 609 | 5397 | (projected live 215 / 1067) |

The "guarded" rows replay the guard's schedule (helpers queued, flushed when the burst ends); the real guard in zoe-data has not been measured on the live box, because that needs the merged change deployed. The remaining p95 (~2 s) is the real work: a tool-block growth or a recall-block elision re-prefilling the tail after the divergence.

## Second finding: `history=0` (separate commit)

`FLUE_CONTEXT_BUDGET history=0` on 8 consecutive live turns (18:40-18:46) while the store held 20+ turns (verified: the conversation stream is a clean linear chain; replaying the session's stored message lengths through `applyPolicies` reproduces every logged `history` / `tail` / `stale` number). `windowHeadIndex` sized its quantum on the budget alone (1664 tokens), larger than the whole history of a short voice conversation, so "first block whose cumulative drop meets the target" fell off the end of the list and defaulted to the NEWEST block: all history dropped for a few tokens of overshoot (a slot-filling follow-up lost its context), and each such jump also moved the head of the prompt (a cache miss). Fix: a recency floor (`MIN_KEEP_TURNS = 3` earlier turns survive unless the budget itself needs them) and windowing against the tool block the model is actually offered, not every registered schema (~650 tokens the wire never carries). `labs/flue-zoe-brain-2x/test/history_floor.test.ts`, with a negative control that reproduces the wipe with the old rule.

## Operator steps

- zoe-data: restart to load `brain_slot_guard` (`systemctl --user restart zoe-data.service`, then poll `/health`). Kill switch: `ZOE_BRAIN_SLOT_GUARD=off`. Verify: in `~/.zoe-logs/zoe-data.app.log` look for `BRAIN_SLOT_GUARD label=... released=released_quiet waited_ms=...` after a burst of turns, and `FLUE_PROMPT_CACHE first_cache_n` > 0 on the recall turns of a session.
- Sidecar (`labs/flue-zoe-brain-2x`): auto-deployed by `deploy.yml` on merge (restarts `flue-zoe-brain-2x`).
- Voice replay gate: this PR touches the voice path (sidecar `context-window.ts` / `capped-completions.ts`, `zoe_flue_client.py`). The probe was NOT run before the PR: the box had 0.5-1.4 GB MemAvailable (the gate needs >= 2 GB quiet headroom). Run `scripts/maintenance/voice_regression_probe.py` under `flock /tmp/zoe-voice-harness.lock` against a checkout of the PR head before the deploy; `deploy.yml`'s gate refuses otherwise.
- Re-measure live after deploy: `python3 scripts/perf/measure_brain_prefix_cache.py --live` (takes the brain window lock, refuses 02:30-04:30), plus real turns: miss rate = share of `FLUE_PROMPT_CACHE` rows with `first_cache_n=0` and `first_prompt_n > 1000`.

## Not done, and why

- A conversation that resumes after a quiet window still starts with the slot holding a helper prompt: the first turn after a flush is the old behaviour (shallow divergence resumes from the host cache, deeper does not). Expected with one slot - and since 2026-10-14 mitigated at the wake word: `zoe_flue_client.warm_kv_prefix` (`ZOE_BRAIN_KV_WARM_ON_WAKE`, default on) re-primes the slot with the shared system+tools prefix during the wake-to-command window as an explicit guest (no envelope would fall back to the sidecar's env member), aborted if it stalls, logging `FLUE_KV_WARM ... first_prompt_n= first_cache_n=` (kept out of `FLUE_PROMPT_CACHE` so the miss rate above stays honest). The earlier wake prewarm only spawned the unused core (Pi) worker; it never warmed the KV.
- Deep divergences still cost the tail re-prefill (recall elision ~560 tokens, tool-block growth): ~0.4-2 s instead of 5-7 s. Shrinking the recall block or placing it so the next turn's elision is shallower is a quality trade for the Samantha program, not made here.
- The repo template `scripts/setup/systemd/llama-server.service` still carries `--cache-ram 2048` and no `--swa-full` while the live unit runs both via `80-swa-full.conf` (and `services/zoe-data/AGENTS.md` claimed the template had it). Not touched: voice-path scope, replay-gated. Logged in `open-problems.md`.
- Other callers that fill the slot (the autonomous `night_mind`, `user_portrait`, benchmark scripts run by other agents) are not gated; the review saw `python3` bench processes hit `:11434` during the session. They only matter while a conversation is hot.

## Addendum 2026-10-27: the guest wake warm could not serve a member (cold first turn after idle)

Live: wake warm hit (`FLUE_KV_WARM first_prompt_n=13 first_cache_n=2657`, slot held 2671 tokens), then the identified member's turn 9 s later re-prefilled all 3244 tokens (`first_cache_n=0`, 5.96 s TTFT). llama-server log: `selected slot by LCP similarity, f_sim_best = 0.664 ... f_keep = 0.806` = a common prefix of ~2152 tokens, then ~519 tokens (tool block + message) after the divergence in the slot: past the 512-position SWA window it can resume from, so it re-processed everything. Cause: the warm was an explicit guest; a member's manner block + user-model card are appended to the system prompt AHEAD of the tool block (`labs/flue-zoe-brain-2x/src/manner.ts`, `user-model.ts`), so the "prefix is identical for every user" premise held only up to ~2150 tokens. Slot is not the cause: `--parallel 1`, no `id_slot` anywhere.

Fix: the wake warm now renders the panel's likely speaker's system prompt (in-process memo of the member last verified by voice on that panel, `_LAST_VOICE_SPEAKER`; guest when none), with the replay marker so its tool calls commit nothing. Diagnostics: `FLUE_KV_WARM ... key=<hash>` and `FLUE_PREFIX_KEY kind=turn session=... key=<hash> slot=auto` hash the identity/manner/persona envelope lines actually sent; a miss with equal keys is not a prompt-shape problem, unequal keys is (wrong speaker guess). A wrong guess falls back to the old guest-warm behaviour. Unmeasured live (needs deploy + the voice landing).
