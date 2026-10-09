---
type: Reference
title: The night mind - the nightly reflection pass (v1, flag-dark)
description: How each household member's day becomes a few cited, pointer-shaped observations (threads in progress, what changed, what is quiet, what to raise and what to leave alone) on the 8,192-token slot - the four stages, the store, the readers, the call budget, the standalone CLI contract for the 12B night window, the bench cells and negative controls, what landed in the first PR and what is deferred.
tags: [memory, reflection, night-mind, flags, bench, 12b]
timestamp: 2026-10-09T03:00:00Z
---

# The night mind (v1)

Design record: `docs/research/night-mind-2026-10-09.md` (PR 1923). Why Hindsight's reflection cannot run here: `docs/research/memory-bakeoff-decision-2026-10-08.md`
(its consolidation prompt peaks at 8,313 tokens against the 8,192 slot; its observations failed the precision veto). Code: `services/zoe-data/night_mind.py`
(pass + readers), `night_store.py` (tables, alembic `0040_night_mind.py`), `scripts/maintenance/zoe-night-mind.py` (standalone), bench: `scripts/perf/zmb/`
(`night_brain.py`, `z0n_window.py`, `z0_brain.py`, cells K1-K12).

**Flag: `ZOE_NIGHT_MIND` = off (default) | shadow | enforce.** Off: no model call, no read, no write, byte-identical packets. Shadow: every call and every check
runs, counts only, nothing is written. Enforce: written and served. `ZOE_NIGHT_MIND_URL` / `_MODEL` / `_MAX_CALLS` (default 7) / `_CTX_TOKENS` / `_CHUNK_TOKENS` /
`_DECODE_TOK_S` tune the pass; `ZOE_NIGHT_MIND_SCHEMA=1` sends the moments' JSON schema as `response_format` (default off: the llama.cpp overhead is unmeasured, E3).

## The four stages (the model points, code decides)

1. **PACK (code)** - the day's own-words turns (the digest's `Transcript`: `own_words`-filtered, forgotten turns skipped, now with their times) lose routine commands
   (`is_routine`: timers, lights, music, weather, "remind me", small talk, unless the turn carries a name, a date, a feeling) and are cut into chunks of about 2,400
   turn-tokens (scaled by the context size), at most `max_calls - 1` chunks; a very full day keeps its highest pre-scored turns and reports what it skipped. This
   **replaces the 3,000-character cut** the digest's extractors apply to a busy day, **for the night pass only**: the fact extractor, the emotional pass and the open-loop pass keep their cuts (deferred, below).
2. **MOMENTS (4B, one call per chunk)** - the model emits only `{ids, quote, kind, who, feeling, weight, later}`. Code drops a moment whose cited id is not in the
   chunk or whose quote is not an exact span of one cited turn (counted, never repaired), then runs `memory_authority.check_observation` on the quote against that
   turn (a hedged or unsupported quote is **held**, class `pending`, never served) and the authority wall's conflict test (a quote the owner's later word replaced is **history**).
3. **THREADS (4B, one call per member)** - Hindsight's create / update shape: `{op, title|thread, moments, status, reason}` with ids in and ids out; the mandatory
   `reason`, PREFER UPDATE OVER CREATE (enforced again in code: `merge_groups`, `link_existing`), ABSENCE IS NOT CONTRADICTION (a thread not mentioned tonight is left alone), a thread is **resolved only by a moment that says it finished**. A bad id drops that one operation.
4. **DECIDE (code)** - quiet (an open thread unmentioned for max(9 days, twice its own usual gap)), what changed (new / advanced / resolved / quiet, with the cited turn ids),
   the mood line (a template over counts, only for a member the household affect policy allows), salience, and the raise / leave policy. **Leave** is a deny-list - health
   conditions, grief, money trouble, a quarrel, "don't bring that up", two ignored raises, too fresh - and is **never written into any prompt**; at most **one** thread is `raise` for the morning;
   the back-off doubles after each ignored raise (1, 2, 4 days; two ignored = leave).

An observation is a **pointer**: `chat_messages` id + an exact span of that turn (kept beside it, as `exact_turns` keeps the words, so a forget can erase by text), the day,
enums, a validity interval, `state` (current | history | held), `authority_class` (`user_stated_derived` at most; `pending` when held). There is no field for model prose.

## Where it plugs in (all flag-dark)

| Where | What |
|---|---|
| `memory_digest.run_memory_digest` | after the facts and the emotional pass: `result["night_mind"]` (a failure is nested, never the digest's `error`) |
| `memory_digest._load_todays_messages` | selects `created_at` (`Transcript.times`); the day cap rises 200 -> 600 turns while the flag is on |
| `routers/memories.py` `memory_for_prompt` (**VOICE PATH**) | appends a "What I've noticed" block (<= 3 dated owner quotes) only when the message names a story or is an open check-in ("how has my week been"); a spoken worry ("I can't switch my brain off tonight") gets the one unresolved worry with a "check in once, gently" line; never in continuity mode; `leave` threads only when the member names them |
| `proactive/triggers/morning_checkin._build_morning_context` -> `brief_first_turn` | `night_items`: the one `raise` thread as `{text, source_ref: night_threads:<id>}`; `mentioned()` marks it so the next conversation does not raise it again; the settle stamps `last_raised_at` / `next_raise_after`; the `BRIEF_FIRST_TURN` log line gains `night=N` |
| forget / delete | `intent_router` `memory_forget_entity` -> `night_mind.erase_entity` (fail closed: an erase that fails is not confirmed); `MemoryService.delete_user` -> `night_mind.delete_user`; served rows re-check the forget ledger and tombstones on every read |
| day-sim | `POST /api/proactive/selector/run-synthetic/{id}?night_mind=1` runs the real pass for a harness id; ask **S9c** (below) |

## Call budget per member per night (measured by the counters in every run row)

`max_calls` = 7: up to **6 MOMENTS calls + 1 THREADS call**. A quiet day (< 3 turns or < 20 words after the routine drop) is **0 calls**; one chunk with nothing open is **2**; the cap is enforced in code and
reported (`capped`, `turns_skipped_cap`). Every prompt is sized with a conservative token estimate so prompt + output stay inside `ctx_tokens`. The HTTP budget of EVERY call
(member pass, `--cells`, K12 alike - one function, `Config.timeout_for`) is `prompt_tokens/prefill_tok_s + max_tokens/decode_tok_s + 20 s`, at least 30 s. Both rates are the server's MEASURED ones:
`--prefill-tok-s` / `ZOE_NIGHT_MIND_PREFILL_TOK_S` (default 650 = the live 4B) and `--decode-tok-s` / `ZOE_NIGHT_MIND_DECODE_TOK_S` (default 8); the 12B night window probes both and passes both to the member pass AND the
`--cells` run (2026-10-09: the 12B measured prefill 136 / decode 3.62; the cells ran on the 4B's constants and 8 of 10 came back ERROR on ReadTimeout). The in-run decode adaptation is only a safety net (it can only lower the
rate after a slow answer, so it cannot save the first call). A timeout logs `NIGHT_MIND user=... status=llm_timeout budget_s=... prompt_tokens=... max_tokens=... decode_tok_s=... prefill_tok_s=...`; the member row keeps
`status: llm_unreachable` with `llm_timeout: true`, `timeout_budget_s` and the counters of the calls it made; `--cells` prints `cells.reasons` (the reason of every ERROR/SKIP) and aggregates `totals`/`cells.model_totals`. **Never half a night**: the pass computes the whole night in
memory, probes the server first and commits at the end; an unreachable model or a transport error mid-night writes nothing (not even a run row).

### Measured on the live 4B at its 8k slot (2026-10-09 smoke, `z0n_smoke.sh`, 17 model calls, the bench's synthetic household, scratch stores)

* **Decode rate ~60 tok/s** (completion tokens / (call time - prefill at 650 tok/s) = 60.2; 53.7 raw): neither the 8 nor the 33 figure in the records. A 640-token reply takes ~11 s, a 60-token one ~1.5 s, so a member-night of 3 calls is ~25 s and the cap (7 calls) ~75 s on this brain. `--decode-tok-s` keeps its conservative default of 8 so the timeouts are generous; the pass also LOWERS its assumed rate (never raises it) after the first long answer if the server is slower.
* **Quote discipline held: 0 moments dropped for a bad id or a non-verbatim quote** across ~40 proposed (the 4B copies spans exactly); the observation gate held 1 of 11 (the owner's own hedge "might look around").
* **It did not fit the first output cap**: 3 of 14 replies hit 450 tokens (pretty-printed JSON), the JSON ended mid-object and the chunk was lost. Fixed before the final smoke: cap 640 and `_salvage` keeps the complete objects of a cut-off reply (each still checked); 5 of 17 calls still hit the cap (a compact one-line JSON instruction is the next experiment, E3).
* **Its THREADS reply glued strangers together once** (a dentist and a colleague's move in one thread); code now splits a group no name or matter word connects (`split_incompatible`).
* **Cells on the real 4B at 8k (seed `zmb-v1`)**: PASS K1 (4 decidable, 4 true, 0 false), K2, K3, K4, K5, K7, K8; FAIL K6 (it never picked the knee story: 3 of 4 stories bounded), K9 drift and flat, K10, K11, K12 (it picked 7 of the 12 labelled moments; accuracy 0.93, rank correlation 0.54). The ship bar (K1 Wilson lower bound >= 0.85 over >= 40 decidable observations) is **not** met by a 4-observation sample; this is a smoke, not E4.
* The smoke ran under the window's guards (brain lock, harness lock, panel quiet, MemAvailable >= 2,000 MB); a fourth run was stopped by its own 1,500 MB guard when another process's test run took the memory, and was not repeated.

## Standalone CLI (the 12B night window codes against this)

`scripts/maintenance/zoe-night-mind.py` - the same code as the digest hook, runnable on its own.

```
zoe-night-mind.py --model-url http://127.0.0.1:11500/v1 --ctx-tokens 16384 --all-members            # the night (mode enforce)
zoe-night-mind.py --model-url ... --ctx-tokens 16384 --user <id> --date 2026-10-08 --dry-run         # calls + checks, NOTHING written (mode shadow)
zoe-night-mind.py --model-url ... --ctx-tokens 16384 --cells                                          # score K1-K12 (lab, scratch stores) against that model
```

Flags: `--model-url` (loopback only unless `--allow-remote`; default `$ZOE_NIGHT_MIND_URL`, then `$GEMMA_SERVER_URL`), `--model-name`, `--ctx-tokens` (default 8192; the chunk budget derives from it:
8k -> 2,400, 16k -> 4,800, 32k -> 9,600 turn-tokens, capped so prompt + output stay inside the slot), `--chunk-tokens`, `--max-calls` (default 7), `--decode-tok-s` (default 8.0; scales the timeout),
`--user ID` | `--all-members` (`--allow-synthetic` keeps demo ids), `--date YYYY-MM-DD` (Zoe-local day; default the digest's rolling ~30 h), `--dry-run` | `--mode shadow|enforce`,
`--transcript-file F` (a JSON list of `{id, text, at}` for one member: synthetic runs with no Postgres; pins every store to a scratch directory), `--cells`, `--seed`. `--cell-budget S` (with `--cells`: the seconds the whole process may take; before each cell the CLI asks `zmb.cells_budget.fits_next`, and a cell that would overrun is not started - it and every later one is `SKIP` with a `cell_budget` reason in `cells.reasons` and listed in `cells.skipped_budget`; the one JSON line is still printed; every finished cell also logs `NIGHT_CELL id=K1 verdict=PASS wall_s=..` on stderr so a killed run keeps its verdicts). The 12B window sizes it from the measured speed (`docs/knowledge/night-window.md` section 12).

Stdout is ONE JSON object on ONE compact line (`--pretty` indents it for reading by eye; the 12B window parses either; logs go to stderr; never environment, tokens or the owner's words):
`{status: ok|nothing_to_do|llm_unreachable|error, mode, dry_run, model_url, model, ctx_tokens, chunk_budget, max_calls, date, members: [{user_id, status, turns_in, turns_dropped_routine, turns_skipped_cap, chunks, calls, calls_invalid, moments_proposed, moments_verified, moments_held, observations_written, observations_pending, threads_created, threads_updated, prompt_tokens, completion_tokens, wall_s, written, skipped_reason?, error?}], totals: {members, calls, prompt_tokens, completion_tokens, wall_s, observations_written, observations_pending, calls_invalid, moments_verified, completion_tokens_per_wall_s, members_written}, members_written, members_total, cells?: {K1: PASS, ..., pass, fail, skip, error, k1: {judged, true, false, observations}}}`.
Exit codes: **0** ok / nothing to do, **1** error, **2** the model is unreachable. "Nothing was written" holds PER MEMBER (each member's night is computed in memory and committed in one transaction at its end; the server is probed first): with `--all-members` an earlier member's committed night stays when a later member fails or the server drops, so read `members_written` / `members_total` (and the exit-2 line on stderr) for how far a run got.
`scripts/perf/zmb/runner.py --arm Z0n --model-url URL [--ctx-tokens N]` and `scripts/perf/zmb/z0n_window.py --clone-url URL --ctx N --out F` score the same cells (K1-K12) and Z0's `protocol_brain` half on that model;
the bake-off window runs them with `BAKEOFF_Z0N=1`.

## The bench (ZMB reflection axis, K)

- **Z0n** = Z0 + the night pass with its OWN model (`nightly_model = "own"`): the lab's FAKE brain (`night_brain.py`, deterministic, proves plumbing and the checks) or the clone at `--model-url` (the measurement). Plain Z0 keeps the scripted digest, so its recorded baseline is unchanged and K2 / K3 / K6-K12 SKIP there ("scripted").
- **K2 / K3** are real cells on Z0n. **K1** carries its judged / true / false counts on every run (a veto can say whether it saw false observations or too few) and a second control, `night_citations`.
- **New cells** (each with the control that must turn it red): **K6** compression (the echo control: copying every turn passes K1, K2 and K4, fails K6; control `night_echo`), **K7** dense day with late plants (1,200 routine commands, two stories planted in the last 10% of each day; control `night_chunking` = the old 3,000-char cut), **K8** citation validity (control `night_citations`), **K9** change and quiet + the flat-week false-notice control (`night_mind`, `night_notice`), **K10** restraint over 14 mornings (`night_restraint`), **K11** resolution (`night_absence`), **K12** weight / kind / feeling calibration against twelve labelled moments (`night_weights`; the lab's fake brain proves the plumbing only).
- **Z0 `protocol_brain` baseline** (`z0_brain.py`): rows `M4.<metric>.zoe` - the 32 protocol prompts through Z0's recall floor + `recall_memory` tool on the clone brain - so rule M stops reading "no data".
- The control pass proves the night cells on Z0n and every other cell on plain Z0 (`cells.uses_night`).

## Day-sim

`night_mind=1` on the run-synthetic hook stands in for the 03:00 digest the day-sim does not run; `BRIEF_FIRST_TURN ... night=N` shows whether the brief carried a night item; **S9c** ("I can't switch my brain off tonight" after the planted dentist worry): PASS = the reply connects to the dentist worry once, gently, and brings up nothing else from the week; the card-only twin (the same week with `ZOE_NIGHT_MIND` off) must FAIL it (generic sleep advice). Default mode only (the hook refuses an allowlisted id).

## Operator steps

1. Apply migration `0040` (the tables). Until then the pass writes nothing it cannot (an unreadable table is a nested `error`) and the forget / delete paths tolerate the missing table.
2. `ZOE_NIGHT_MIND=shadow` for a week: read the `NIGHT_MIND ...` log lines (counts only): calls, held, uncited, parse failures. Then `enforce`. Voice path: `routers/memories.py` changed - run the voice replay gate before enabling `enforce` on the box.
3. 12B window: `zoe-night-mind.py --model-url http://127.0.0.1:11500/v1 --ctx-tokens 16384 --all-members` after the 4B / Kokoro / router are stopped; check exit code 2 (model down, nothing written) and restore.

## What landed and what is deferred

Landed: the four stages, the store + migration, the digest hook, the readers (packet block, brief item with `source_ref`, mood-turn check-in), forget / delete / opt-out integration, the standalone CLI, Z0n + the fake brain, K1-K12 with negative controls, Z0's `protocol_brain` baseline, the window driver + flag-dark phase, the day-sim `night_mind` intent / `night=` field / S9c, tests in both lanes.

Deferred (recorded in `open-problems.md`): the fact extractor / emotional pass / open-loop pass still cut the day (3,000 chars; 50 turns) - only the night pass chunks; the night mind does not yet replace `_extract_open_loops` as the producer of `open_loops` rows (record section 6.1); the weekly roll-up (4.7); the 12B verifier + hypothesis tier (4.8); one-turn chunk overlap; grammar-constrained decoding is wired (`ZOE_NIGHT_MIND_SCHEMA`) but unmeasured; the owner's call on derived mood trajectories (the pass computes the mood line only for a member `_affect_allowed` covers, and no trajectory score is stored); minors are not special-cased beyond that gate; E10 (the two-week household shadow) is an owner step.
