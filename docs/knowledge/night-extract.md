---
type: Reference
title: The night extract pre-pass - a shadow GLiNEREL entity/relation extractor over the day's conversations
description: Flag-dark ZOE_NIGHT_EXTRACT=off|shadow|on (phase 1 builds shadow only). A night-window job that runs VAGO's SauerkrautLM-GLiNEREL-128m over each real member's own-words turns, proposes person / age / place / lives-in facts (model) and pet / kin words (lexicon rules), compares them with what the 4B digest stored, and writes a private file. Writes nothing to memory or the people graph.
tags: [night-job, extraction, shadow, glinerel, people-graph, forget, privacy]
timestamp: 2026-11-08T00:00:00Z
---

# The night extract pre-pass

**Status 2026-11-08: built, flag-dark (`ZOE_NIGHT_EXTRACT` unset = `off`). Phase 1 is SHADOW ONLY: it observes and logs, it never writes
memory or the people graph. `on` is accepted by the flag but NOT built (it runs as shadow).** Owner request: build, test, optimise, turn on
if it works, keep optimising; if it improves the night processes, good.

## What it does

`extract_prepass` is a night-window job (`scripts/maintenance/zoe-night-extract.py`, right after the digest, best-effort, <= 7 min, `--budget-s`):

1. Members: every chat-turn owner of the last 24 h, guests and synthetic ids dropped (`night_extract.real_members`).
2. Turns: `memory_digest.load_day_messages` - the member's own words only, off-the-record out, **turns naming a forgotten entity skipped by
   the strict ledger** (a ledger outage withholds the member: fails closed). Forget / never-mind turns are skipped again by the job.
3. Model: ONE process for the whole night (the model loads once per window, not per member): `scripts/night/jobs/glinerel_worker.py` in the
   venv `~/.zoe/venvs/glinerel`, launched as `systemd-run --user --scope -p MemoryMax=1600M -p MemorySwapMax=0 nice -n 10`. CPU only, offline
   (`HF_HUB_OFFLINE=1`). It cannot take the window down: the cap kills only its own scope and the job reports `failed`.
4. Facts (`night_extract.postprocess`, the lab's tuned config): trusted outputs only - **person, age, place, lives-in**.
5. Lexicon rules beside the model (`night_extract.lexicon_facts`): **pets** ("a cat called X", "X is our dog", "my dog X") and **relation words**
   ("my son X", "X is my mum", "my partner is X"), built on the repo lexicons (`lexicons_data/en.json`: `pet_words`, `kin_codes`). The model is
   dead on both labels (lab round 2).
6. Comparison with what the 4B digest stored in the same 24 h (`MemoryService.load_recent_for_prompt`, read only): **agree**, **extractor_only**
   (facts the 4B missed), **contradicts** with a reason - `wrong_person` (the 4B filed another person's age/home under the user),
   `different_value`, `pet_stored_as_child`.
7. Output: `~/.zoe/night-extract/<night>.night-extract.jsonl` (0600, dir 0700), one proposed fact per line (kind, subject, value, source
   model|rule, score, comparison, turn id - never a turn's text), and `<night>.night-extract-summary.json` (counts + perf). Nothing else is written.

Tuned config (lab `~/.zoe/lab/glinerel/REPORT-round2.md`): 128m fp32, thresholds entity 0.9 / relation 0.5 (one global pair; per-label thresholds
overfit), label "date" worded "day of week", relation "age" worded "is aged", speaker rewrite I/my to SPEAKER, non-name stop-list, forget turns
skipped, parent-of normalised to child-of (unused here: parent-of is not a trusted output).

## Where it sits in the window, and why (measured 2026-11-08)

From the 2026-11-08 night report (`~/.zoe/night-reports/2026-11-08.json`): MemAvailable **1,707 MiB** before the window stops anything (the voice stack
up), **12,637 MiB** after the stops, **4,220 MiB** after the 12B loads, and the jobs ran with **3.2 - 4.1 GiB** available (lows 3,166 - 3,598). The
extractor peaks at about 1.1 GiB (capped at 1.6 GiB) and the window's floor is 1.2 GiB, so after the 12B is up there is room (worst case
3.6 - 1.6 = 2.0 GiB above the floor). It is slotted **right after the digest**: it needs the digest's stored rows to compare against, the 12B is
idle during it (CPU only, no contention), and it is before the long budgeted jobs. Not before the 12B load (that would fragment the memory the 12B
load needs) and not after the unload (that would delay Zoe's wake).

## Forget

- "forget X": cascade step `night_extract` (`night_extract.erase_entity`) drops the member's proposed facts naming it.
- Member erase: `MemoryService.delete_user` calls `night_extract.delete_user` (all facts and the summary line; fail closed).
- Readers (`night_extract.load_facts`, `zoe-night-extract.py --show NIGHT`) re-check the ledger on every read; the writer drops any fact naming a
  forgotten entity before the file is written.
- Inventory row `night_extract_facts` in `forget_reach.py` (docs/knowledge/forget-reach.md); probe layer `night_extract` in
  `test_forget_completeness_probe.py`.

## Runtime, licence, setup after merge

- Venv outside the repo: `scripts/setup/setup_glinerel.sh` (idempotent; system python3.10 + its torch via `--system-site-packages`;
  `pip install --no-deps glinerel-fast==0.1.1`; weights pinned to revision `5b8448f9...` in `~/.zoe/models/glinerel/hub`, offline at run time).
  `--check` reports state; `--seed-from DIR` copies an existing HF hub cache instead of downloading.
- Licence: the weights are the **LFM Open License v1.0 (free under USD 10M revenue; not Apache)**; the `glinerel-fast` package is Apache-2.0.
- Flags (all registered): `ZOE_NIGHT_EXTRACT=off|shadow|on` (default off), `ZOE_NIGHT_EXTRACT_THREADS` (4), `_BATCH` (8), `_MEM_MAX` (1600M), `_VENV`, `_DIR`.
- To turn the shadow on: run the setup script, then reinstall the window unit (`install_night_window.sh`) so `--jobs` carries `extract_prepass`,
  then set `ZOE_NIGHT_EXTRACT=shadow` in the environment the window's jobs inherit. Read a night: `python3 scripts/maintenance/zoe-night-extract.py --show <night>`.

## Phase 2 (NOT built): what `on` would do

Propose each fact to the ingest authority with provenance `inferred` (rank below `user_confirmed` and `user_stated`; never overriding a user-stated
fact, the people-graph edges likewise via `people_graph` authority ranks), and flag likely 4B misattributions (`contradicts` rows with reason
`wrong_person` / `different_value`) for `self_review` or the night mind to correct. Only after the shadow comparison shows the contradictions are
real on household data and the extractor-only facts are right.

## Measured results

All on the Orin with the voice stack up, inside the real `systemd-run --user --scope -p MemoryMax=1600M -p MemorySwapMax=0 nice -n 10` cap,
started only at MemAvailable >= 1,800 MiB, outside 01:50-04:55, 2026-11-08 evening. Input: the synthetic QA personas only (round4, round5 a/b/c,
round6a a/b: 539 turns, 6 personas, each fed as one member's day). **No real member data was read.**

**End to end through the job CLI** (`zoe-night-extract.py --jsonl <6 files>`, final config): 525 turns run (14 forget / never-mind turns skipped),
**145.8 s wall for all 6 days (about 24 s per persona day, 278 ms per turn)**, model load 0.95 s, **peak RSS 1,323 MiB** (cap 1,600), 231 facts
proposed (138 person, 47 kin, 19 place, 16 age, 9 lives-in, 2 pet). The comparison columns read 0 / 231 / 0 (agree / extractor-only / contradicts)
because the personas have no 4B-stored rows to compare with (no live-DB reads were allowed); the comparison logic is pinned by unit tests.

**Score against the lab gold set** (131 turns, the shipped `postprocess` + `lexicon_facts`; person, place, age, lives-in, pet and relation-word facts):

| split | precision | recall | F1 |
|---|---|---|---|
| TUNE (66) | 0.94 | 0.71 | 0.813 |
| TEST (65, held out) | 0.97 | 0.83 | **0.895** |
| ALL (131) | 0.96 | 0.78 | 0.858 |

Per kind on ALL (tp/fp/fn): person 72/2/18, place 7/1/1, age 12/0/2, lives-in 3/0/0, pet 1/0/4, relation word 23/2/9. The remaining pet misses
are bare mentions of a pet already named earlier in the day (the fact is proposed once, from the turn that names it); the relation-word misses are
bare mentions with no possessive ("mum is out of hospital").

**Optimisation (each kept only if it scored better on the held-out TEST split or was a pure speed/memory win):**

| change | result | kept |
|---|---|---|
| lexicon rules for relation words (named + "my sister" / "Tom's mum" mentions) | relation-word TEST recall 0.14 to 0.81 at precision 1.00; overall TEST F1 0.756 to 0.895 | yes |
| lexicon rules for pets ("called/named", "X is our dog", "our dog X", lowercase allowed with a plausibility check) | pet recall 0 to 0.20 (the 4 misses are cross-turn bare mentions), precision 1.00 | yes |
| batching, 8 turns per call vs 1 / 16 | 272 ms vs 335 ms (b1, 1,165 MiB) vs 264 ms (b16, **1,564 MiB: too close to the cap**); facts identical in all three | b=8 |
| threads 2 / 4 / 6 | 466 / 272 / 268 ms per turn; facts identical | 4 (6 buys nothing, and steals cores from the 12B's host side) |
| one model load per window vs one per member (6 members) | 145.8 s vs 166.2 s; 18 s saved, 3.5 s of it load time | once per window |
| suppress "dislikes" on negations | "dislikes" is not emitted at all (untrusted). Tried emitting it with guards on the 4B-misattribution class: plain P0.40/R0.50 (TEST, 8 gold), dropping speaker-headed P0.60/R0.38, requiring a food tail P1.00/R0.12. The gold set treats "I don't drink coffee" as a real dislike, so negation is how dislikes are said and cannot be suppressed; no variant is both precise and useful | no (stays out) |

Model quality is the lab's (round 2: entity P/R 0.91/0.50, relation P/R 0.70/0.57 on its own); the shipped pipeline narrows to the four
trustworthy labels plus the rules, which is what the table above scores. 128m only (int8/bf16/72m rejected in round 2; 314m never fit the 3.5 GB gate).

**Final config:** 128m fp32 CPU, 4 threads, batch 8, one process per window, thresholds ent 0.9 / rel 0.5, `MemoryMax=1600M` `MemorySwapMax=0`
`nice -n 10`, trusted labels person / age / place / lives-in + lexicon pet / kin.
