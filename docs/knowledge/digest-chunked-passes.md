---
type: Reference
title: Nightly passes read the whole day - the digest's chunked pack step
description: The fact extractor, the emotional pass and the open-loop pass used to read the first 3,000 characters of the day (200 / 50 turns, fixed 30-45 s timeouts), so on a busy day ~70-80 percent of what the owner said never reached a model. digest_pack packs the day into chunks that fit the slot, maps one call per chunk, reduces in code with the existing dedup; ZOE_DIGEST_CHUNKED (default on) restores the legacy cut. Carries the call budget, the timeout formula, the coverage line and the measured dense-day result.
tags: [memory, digest, nightly, chunking, timeouts, coverage, samantha]
timestamp: 2026-10-09T12:00:00Z
---

# Nightly passes read the whole day

**Defect** (`docs/research/night-mind-2026-10-09.md` section 0 item 3): `memory_digest` cut the transcript to 3,000 characters before the model saw it
(fact extractor and emotional pass), loaded at most 200 turns, and the open-loop pass read the newest 50 turns of two days inside a 3,000-character
budget. Every call had a fixed timeout (45 / 30 / 45 s) shorter than a 500-token reply at the 8 tok/s the passes were sized for.

**Fix** (`services/zoe-data/digest_pack.py` + `memory_digest.py`): PACK (code) -> MAP (one call per chunk, the pass's own prompt) -> REDUCE (code).

| | |
|---|---|
| Chunk budget | the night mind's: 2,400 turn-tokens at the 8,192 slot (`ZOE_BRAIN_SLOT_TOKENS`), proportional at a bigger slot, never more than slot - instructions - reply cap - 300 margin. The estimate (3.3 chars/token), 650 tok/s prefill and `prefill + out/rate + 20 s` timeout are `night_mind`'s (PR #1930); `tests/unit/test_digest_pack.py` fails if the two ever disagree |
| Read cap | the loader reads up to 600 turns (was 200); open loops up to 600 of the last two days (was 50) |
| Calls per member-night | at most `ZOE_DIGEST_MAX_CHUNKS` (default **5**) per pass x 3 passes = **15 map calls**, however long the day. Over the cap the highest-signal turns (names, dates, first person, a feeling, anything safety-ish; the newest wins a tie) survive in time order and the rest are counted (`skipped_cap`). A short day is ONE call with the legacy prompt byte for byte |
| Reduce | facts and emotional moments: `memory_overlap.dedup_verdict` (a restatement is dropped, a richer statement replaces the thinner one, anything with a new name / number / date stays); at most `ZOE_DIGEST_MAX_FACTS` (40) facts, which bounds the <= 3 contradiction checks per fact that follow. Loops: `_loop_is_dup` containment, best by weight (newest on a tie), up to 8 when several chunks answered (5 for one chunk, as ever) |
| Unchanged | the dedup (#1876), the observation gate (#1911: a fact needs the owner's verbatim words, checked against the WHOLE day), the own-words wall, the forgotten-turn skips, every write path |
| Timeouts | per call `max(legacy, prefill + max_tokens / rate + 20 s)` then `ZOE_DIGEST_LLM_TIMEOUT_SCALE` (the 12B night window's). `rate` = `ZOE_DIGEST_DECODE_TOK_S`, default **60** = the live 4B measured in the #1930 smoke (60.2 tok/s, prefill off); the 12B window runs at ~5 and should export its measured rate. Never shorter than the old fixed value |
| Failure | inside a nightly run a failed chunk costs that chunk (the 30 h lookback re-reads it tomorrow); every chunk failing is still `extractor_failed:<kind>`. Outside a run (the idle consolidation) any failed chunk raises so its watermark holds |
| Flag | `ZOE_DIGEST_CHUNKED` (default on; `0/false/no/off` = legacy: first 3,000 characters, 200 / 50 turns, 45 / 30 / 45 s) |

**Count line** (counts only, logger `memory_digest.open_loops`, which the dreaming runner prints): one per member-night and one per open-loop run:
`DIGEST_COVERAGE job=nightly user=U turns_total=240 turns_read=240 chunks=4 calls=4 failed=0 skipped_cap=0 chunked=1 facts_calls=2 facts_turns_read=240 emotional_calls=2 emotional_turns_read=240`.
`turns_read` is the fact pass's (turns whose chunk was answered); legacy mode reports the turns that fit the 3,000-character cut. For `job=open_loops`, `turns_total` is
after Zoe's own mechanics (timers, lights, music) are skipped.

**Measured** (offline, `scripts/perf/dense_day.py`: 240 turns / 14.7k characters; 6 planted facts, 2 emotional moments and 3 open loops, the last 3 facts / both moments / 2 loops in the last 15 % of the day, including the
day-sim's own scenario 4 dentist and scenario 5 Kestrel seeds verbatim; the stand-in model reads exactly the transcript it is shown, so this measures the PACKING, not the 4B):

| | legacy (`ZOE_DIGEST_CHUNKED=0`) | chunked (default) |
|---|---|---|
| facts found | 1 / 6 (only the early one) | 6 / 6 |
| emotional moments | 0 / 2 | 2 / 2 |
| open loops (newest-first legacy sees the late ones, never the early one) | 2 / 3 | 3 / 3 |
| `turns_read` / `turns_total` (nightly) | 48 / 200 (the 200-row cap, then the 3,000-character cut) | 240 / 240 |
| model calls (facts + emotional + loops) | 1 + 1 + 1 = 3 | 2 + 2 + 2 = 6 |

`services/zoe-data/tests/test_digest_chunked_passes.py` runs it through the real `run_memory_digest` and `_extract_open_loops`; the flag-off run is the negative control (the fix, broken, turns the suite red).

**Residuals**: a day over 600 loaded turns still stops at the ASC row cap (the newest are not read; a warning says so); how well the real 4B extracts from a 2,400-token chunk is the
night-mind cells' (E3 / K7) to measure, not this fixture's.
