---
type: Research / bake-off setup verification
title: "Bake-off setup verification 2026-10-06: the whole stack on the real components, before the second window"
status: RECORD of what was run and measured on 2026-10-06 in daylight with the live brain never stopped. The GO / NO-GO line is the verifier's; the owner decides.
date: 2026-10-06
---

# Bake-off setup verification, 2026-10-06 (first contact with the real stack)

Owner instruction: "test this further and make sure you have it all set up properly" before the second bake-off window. Everything below was run on the real components
(hindsight-api 0.10.2, the scratch Postgres container, the loopback embeddings shim, the live Gemma brain's OpenAI-compatible endpoint, the real MemPalace 3.10.0 library),
on the tree of PR #1901 (`bench/h-arm-edges-disk`) plus this PR. The live brain was never stopped, the live store, the live Postgres and `/home/zoe/assistant` were never touched,
every process started here is stopped (listed at the end). Evidence labels: **[measured]** run and read; **[source]** read in code; **[fixed]** a defect found and repaired with a red-before-green test.

## 0. The verdict

**GO for run 2, on this PR's tree, with the risks in section 9.** Five defects would have wasted or corrupted the window; all are fixed here (section 8). Two real zoe-data
bugs found on the way are NOT in the bake-off's scope and are handed off as tasks (section 8, last rows). Plainly:

* The instrument is now proven end to end on the real stack: a 10-cell smoke window ran the lab (Z0 / Z0-off / Z0e), the adapter's six negative controls on the real
  server (all red, including the physical-erase control on real Postgres), H1 / H2 / HM / H0 cells, recall latency, extraction validity, brain-slot seconds, the forgetting probes,
  the HM driver in the bake-off venv, the report, the artifact and the restore. Its egress gate read `0 non-loopback connects over 385 observed (hook 0; ss sampler 0 peers)`.
* The first smoke window FAILED that gate, correctly: the embeddings shim was uploading telemetry to Microsoft (section 3). Run 1 could not have seen it. Fixed and re-measured.
* HM runs on the real tiers and fits the 90-minute cap as ONE seed box next to H1 x 3, H2 and H0, at the price of H2's and H0's seed boxes (section 7).

## 1. Component table (each: command, measurement, result)

| # | Component | Command / how | Measurement | Result |
|---|---|---|---|---|
| 1 | Scratch Postgres | `docker compose -f scratch-postgres.compose.yml up -d` | accepting connections in < 6 s; pgvector 0.8.2, pg_trgm 1.6, btree_gin 1.3 [measured]; idle 18 MB, 76 MB under load (cap 256) | PASS |
| 1a | Migrations through Hindsight's own startup | a FRESH database (`CREATE DATABASE hs_fresh`) and the real one | alembic ran from empty to head `e5b1c7d3a902` in 1.1 s, 25 tables; the existing store was at the same head | PASS |
| 1b | Instrument settings #1901 applies | `ScratchPostgres.prepare()` against the fresh schema | `show wal_recycle` = `off`; `STORAGE EXTERNAL` (`e`) on `audit_log.request/response`, `documents.original_text`, `memory_units.text`; every table and column `pg_store` names exists in 0.10.2 (no missing table, no missing column) | PASS |
| 1c | `docker cp` residue scan on a canary | retain a 2.9 KB document naming `Qwertyzilla`, scan, API delete, scan, `erase_text` + `compact`, scan | baseline 0; after retain 16 byte-hits in 6 relations incl. WAL; after the API delete alone 17 (the engine's delete leaves it); after the scrub 0; after `erase_bank` clean | PASS (after fix D1) |
| 2 | Embeddings shim | `embed_shim.py --find`, `--selftest`, then `--serve` as a `systemd-run` unit (MemoryMax 300M) | bge-small-en-v1.5, 384-d; paraphrase cosine 0.89 vs 0.345 unrelated; RSS 139-158 MB (HWM 161 MB: the selftest's 150 MB limit tests current RSS, not HWM); the hindsight venv's own `openai` client: 2 inputs OK, batch 8 OK, 64 OK, 65 -> HTTP 400 "at most 64", a 3,000-word text 0.56 s, `dimensions=256` -> 400, empty input -> 400 | PASS |
| 2a | No-download refusal | `find_model` with every candidate directory empty | `ModelNotFound: ... this shim never downloads one`; the shim imports none of huggingface_hub / fastembed / chromadb | PASS |
| 2b | Shim egress | the window's `ss` sampler | the shim opened HTTPS connections to Azure addresses (20.42.73.30/31, 4.150.223.111, 52.168.117.175) every ~2 s starting ~6-9 s after start: onnxruntime 1.30.0's built-in Microsoft 1DS telemetry | **FAIL, fixed (D2)**, then 0 sockets in 30 s and 0 telemetry files open |
| 3 | Hindsight server | `hindsight-api` as a unit, loopback :18888, the generated env, `PYTHONPATH` = the egress hook | healthy in 11-12 s including migrations; log line `uvloop not installed, using default asyncio event loop`; hook log `hook-loaded uvloop=blocked`, then connects to 127.0.0.1:11501 (shim), :55432 (Postgres), :11434 (the brain's start-up check) and NOTHING else; PSS 326-348 MB | PASS |
| 3a | Hook negative control | a subprocess with the hook connects to 192.0.2.1 and 127.0.0.1 | `VIOLATION connect ('192.0.2.1', 9)` and `ok connect ('127.0.0.1', 9)`; `import uvloop` fails | PASS |
| 4 | First contact with the LLM path | live brain :11434, `flock /tmp/zoe-brain-window.lock`, panel quiet (last voice turn 2 days ago), anchored `pgrep`, panel re-checked every call | 20 verbatim + 10 concise retains: 30/30 HTTP 200, LLM trace 30/30 `success`; verbatim p50 3.12 s (max 4.06), prompt 1,384-1,399 tokens, output 101-163; concise p50 4.70 s (max 7.55), prompt 2,143-2,155, output 142-193 | PASS |
| 4a | Provenance and validity through the adapter | 26 owner turns into H1, `arm.stats()` | 26/26 rows carry `source_excerpt`, `user_turn_id`, `authority_class`, `origin`, `valid_from`; a replaced fact (`I live in Perth` then `I moved to Hobart last week`) came back `superseded` with `invalid_at`, `superseded_by_id`, and a `Before that (until October 2026): I live in Perth.` row for a history question | PASS |
| 4b | Adapter contract on a synthetic household (H1) | ingest 26, recall, forget, history, hard_delete, stats | ingest 26 written, 0 refused, 0 failures; hit@5 **20/20 direct and 20/20 paraphrase** with the real bge-small (recall p50 103 ms, p95 141 ms, n=40; retain p50 3.2 s, p95 4.8 s); forget: the t+0 scan read 0 before any read; `as_of` raises `NotImplementedError` (documented: 0.10.2 has no belief-time read); `hard_delete` removed 22 memories and the scan read 0 | PASS (after D1, D3) |
| 4c | H2 (observations) on the real server | 3 facts, scoped consolidation, delete a document | observations are separate `fact_type: observation` units; consolidation of 3 facts took 14 s; deleting a document cascaded to its observation at once | PASS; fake corrected (D4) |
| 5 | Runner: `--dry-run` on this tree | `bakeoff_window.sh --dry-run` | prints every step, the generated clone command (differs from the live ExecStart in exactly `11434 -> 11500`), the schedule (77.3 of 77.5 planned minutes) and the restore; while my own script held the lock it REFUSED correctly | PASS |
| 5a | Runner in anger (brain-stop shimmed) | `BAKEOFF_SKIP_BRAIN_STOP=1 BAKEOFF_SMOKE_CELLS=10 bakeoff_window.sh --arms H1,HM ...` then `--arms H1,H2,HM,H0` | phases, artifact, report, restore all ran (section 6) | PASS |
| 6 | Instrument stability | Z0 store tier on 3 seeds; the H fake-arm tests 3x | see section 5 | PASS with one finding (C6) |
| 7 | Z0e | `make_arm("Z0e")` on the recall cells | real Chroma + MiniLM, 4/4 recall cells pass per seed, about 25 s per seed (section 4) | PASS |
| 8 | HM on the real tiers | `hm_window.py` through `mp_run.sh` | 17-18 of 18 graded HM cells pass, 5 of 5 real-tier controls red, wall-clock latencies (section 7) | PASS with findings |

## 2. What the LLM path showed (first contact)

[measured] on the live brain, single slot, so no timing here is a bake-off number:

* **`verbatim` is not model-free in Hindsight.** Each verbatim retain still calls the LLM (about 1.4 k prompt tokens, 3 s) and extracts facts with entities and an event time; "User met Anika
  in Dunedin last spring" came back with `occurred_start/end` 2026-03-01 to 2026-05-31 (the model picked northern-hemisphere months for a southern household): event times are the model's
  guess, which is why the Zoe layer stamps `valid_from` itself from the owner's own words.
* **`concise` appends `| Involving: <entities>`** (and `| When: ...`) to every fact text, and lower-cases `user`.
* **Observations do not invalidate.** "I live in Perth", "My dentist is Dr Okonkwo", "I moved to Hobart last week" consolidated into THREE valid observations, including "The user lives
  in Perth" next to "The user moved from Perth to Hobart". The pre-registered H0 claim that native consolidation lets the newest evidence win (the S1 signature) did NOT reproduce with
  Gemma 4 E4B in this probe; it is a single probe, the H0 cells in run 2 will say whether it holds.
* Retain usage tokens: verbatim 1,358-1,399 in / 101-163 out; concise 2,116-2,155 in / 142-193 out: inside the 8,192 slot with the 2,048 output cap.

## 3. The defect that would have failed every arm's egress gate: onnxruntime telemetry

The first smoke window's G0 line read `3 non-loopback connects over 37 observed (hook 0; ss sampler 3 peers)`. The `ss` sampler is the second, independent egress instrument (the Python audit
hook cannot see a connect made from C), and it caught the embeddings shim: onnxruntime 1.30.0 ships Microsoft's 1DS telemetry SDK ("on supported Linux architectures ... uses the
cross-platform 1DS telemetry SDK that is built into ONNX Runtime", `onnxruntime/Privacy.md`, telemetry ON by default) and uploads to `mobile.events.data.microsoft.com` (Azure addresses). The
process had `~/.cache/Microsoft/DeveloperTools/.onnxruntime/onnxruntime.db` and `/tmp/mat-debug-<pid>.log` open. Run 1's hook was blind, so its egress gate read NA and this went
unseen; run 1's shim phoned home too (same package). Only the bake-off venvs carry onnxruntime 1.30: **the zoe-data venv has 1.23.2 and is not affected** [measured].

Fix [fixed, D2]: `ORT_DISABLE_TELEMETRY=1` forced (not defaulted) in the shim before onnxruntime can load, plus `ort.disable_telemetry_events()`; set at every place the window starts a
process (`bakeoff_window.sh`, the shim's unit, the Hindsight env, `hm_window.py`, `mempalace_verbatim`, `lab_driver`, `mp_env.sh`, `hindsight.env.example`). Re-measured: 30 s of the shim with
the fix: no non-loopback socket, no telemetry file open; the full smoke window afterwards: `0 non-loopback connects over 385 observed (hook 0; ss sampler 0 peers; by phase: H0 291, H1 9, H2 59,
HM 19, setup 3, shared 4)`. A user unit cannot enforce `IPAddressDeny=` ("unit configures an IP firewall, but not running as root", measured), so the env var, the hook and the sampler are the
guarantees. The sampler now also watches the HM driver (a child process of the window) and reads `::ffff:127.0.0.1` as loopback.

## 4. Z0e: the recall axis on real retrieval

The lab's Z0 ranks by bag-of-words (a paraphrase that shares the subject's name is trivially found), so D compared an embedding arm with a word counter. **Z0e** is the same
`MemoryService` over a real in-process Chroma collection with the service's own MiniLM embedding function (`_drawers_embedding_function`, l2 space: the live default), `lab_driver.EmbedCollection`.
It never downloads: no chromadb or no cached model is a SKIP and the report says the D baseline fell back to Z0. In the window it runs the four recall cells on all three seeds (about 25 s per
seed, 1.4 planned minutes) and the winner clause compares D with Z0e. [measured] 4/4 recall cells pass on Z0e per seed; with `retrieval` switched off D1 goes red (the control reaches the
embedder-backed arm); "what is the name of my pet" finds "My dog is called Biscuit" among distractors where the lab's word overlap has nothing to go on.

## 5. Stability

* Z0 store tier on `zmb-v1`, `stab-14` (friend Ines) and `stab-1` (friend Tove): 139 / 138 / 138 PASS of 145, instrument ok, 132/132 controls red on every seed. **One cell differs:
  `C6.no_collateral_invalidation` is PASS on the baseline seed and FAIL on both held-out seeds** (a friend's "moved to X" also retires that friend's job fact on those names). That is a real
  zoe-data defect the baseline seed was hiding (exactly the Goodhart gap the held-out seeds exist for): handed off as a task; the bake-off does not hide it (the cell runs on three seeds per arm).
* The H fake-arm suites (`test_zmb_hindsight_arm.py` + `test_zmb_pg_store.py`) three times: 90 passed, 2 skipped, 90 / 90 / 90; no flake.
* One measurement is borderline by nature: HM-L2 (the second lookup adds <= 25 ms p95 on the real tiers) read 29.8 ms (FAIL) in one run and 11.7 ms (PASS) in the next, 50 queries each.
  It is a threshold-level wall-clock figure: run 2's single result should be read with that spread.

## 6. The runner in anger (brain-stop shimmed)

`BAKEOFF_SKIP_BRAIN_STOP=1` (default off, documented in `bakeoff-howto.md`) runs the window against the LIVE brain: no stop, no clone, Hindsight's LLM on :11434, the restore only stops what
the window started and checks the brain is still healthy, and the window ends if a voice turn starts while it runs. `BAKEOFF_SMOKE_CELLS=N` limits each arm to one seed of N cells spread over the axes.
The report opens with a TEST-HOOK banner. Two runs [measured]:

* Run A, `--arms H1,HM`, 12.8 min: lab (Z0 132/132 controls red on 3 seeds; Z0e 4/4 on 3 seeds); the six adapter controls on the real server all FAIL as required (authority, identity, ledger,
  A8 authority, supersede, **physical_erase on the real Postgres**); H1 10/10 cells in 149 s, 0 hard violations; latency n=50 p50 102 ms / p95 108 ms; verbatim validity 10/10 (trace 10/10);
  slot 1.353 s per turn; the forgetting probe t+0 clean then t+6 min after a real wait; HM (below); report written; brain restored healthy (`WINDOW_OPEN` removed, units stopped, container gone,
  lock free). Its G0 egress line was the FAIL of section 3.
* Run B, `--arms H1,H2,HM,H0`, aborted at 21:10 by the window's own guard: `MemAvailable 1148 MB < 1200 MB floor (sampler)` (floor measured 975 MB) during H0's phases; the abort, the report
  (H0 reported as not run) and the restore all worked. This is the guard doing its job on a daylight box where the live brain, zoe-data and the CI runner share 15.6 GB; at night with the brain
  stopped the clone's RAM is the brain's (about 5.6 GB) and the floor is not the same question. It also means my own "MemAvailable stays >= 1.5 GB" limit was NOT held in this run (see section 9).

## 7. HM (Hindsight + MemPalace) on the real tiers

What was built: `HindsightDistilledTier` (was a stub) speaks the HTTP API: a bank per user, a distilled bundle is one Hindsight document whose metadata names its verbatim chunk ids, a deterministic
fact is written with a per-item `det` strategy (the bank's `retain_strategies` -> `chunks` extraction: zero model calls, verified), an authority-gated add, forget by document with the chunk ids
remembered so the innocent chunks are re-queued, and a separate `scrub` (Postgres erase + compact) that the `physical_erase` control can switch off. `hm_window.py` runs in the bake-off venv through
`mp_run.sh` (real MemPalace 3.10.0, scrubbed HOME, `MALLOC_PERTURB_=85 PYTHONMALLOC=malloc` for the heap-scrub cell) and hands one JSON back; the window folds it into the same axes / gates / winner
clause as an H arm's seed, with its own gate block (HM-G0a .. HM-G3a) and its own column.

[measured] first contact on the real tiers: **17 of 18 graded HM cells pass** (HM-L2 the borderline one), sanity 2/2, the known target HM-F5 (an STT misspelling of a forgotten name) still fails as
documented; **5 of 5 real-tier controls go red** (`forget_verbatim` on F1, `cascade_provenance` on F3, `physical_erase` on F6 and on the new F8, `tier_isolation` on T1); the voice lane serves from
the write-behind cache in 0.01 ms; verbatim query p95 70-73 ms (<= 100); the HM-F8 scan of Hindsight's Postgres reads 0 after the forget. The two-tier forget with the Postgres scan works end to end.

Findings, all in the arm and not in the bake-off tooling:

* **HM kept 14 of 20 needles at hit@5** because the lab's attribute key had no subject: "friend A lives in X" and "friend B lives in Y" were one attribute (`home`) and the packet's authority
  rule dropped all but one of each shape. Fixed (the key carries the subject, `home:priya`) with a red-before-green test. The same class of bug is in zoe-data's own nightly pass (section 8).
* The lab's attribute detector knew four attributes with capitalised values only, so a model proposal about work, age, birthday, spouse or pet was never held back (40 of the generic authority cells failed
  on HM for that reason). Fixed (the spec's slots are covered): 131 generic cells ran in 171 s on the real tiers (89 FAILED there before the fix; on the doubles 40 fail after it; the rest are structural: HM has no extraction, identity wall or
  provenance export of its own, so the H / E / B / A3 / I cells fail it, and two hard cells fail the HM gate in the smoke: E1 and H1.digest).
* **RAM**: the servers during the HM phase 570-580 MB plus the verbatim tier's own growth on a warm embedder +191 MB (gross, including a cold embedder load zoe-data already pays: +381 MB) = about 770-895 MB
  against the 600 MB steady / 900 MB burst budget: G0 FAILS for HM on the measured figure (the design assumption was "<= 50 MB with the shared embedder"; here the library's palaces and caches grew 191 MB over
  the 21 cells).
* G3 lines: the HM glue is 915 non-comment lines (hm.py + hm_policy.py), PASS (an earlier version of the report double-counted the Hindsight layer: fixed).

**Fit in the 90-minute cap** (`plan_budget`, H1 x 3 untouched at 11 min per seed): HM needs the HM cells (5.0 planned minutes; 217 s measured with controls) plus a generic box of 3.0 minutes
(131 cells at 1.3 s per cell, measured) = 8.0 minutes, plus Z0e's 1.4. The plan still totals 77.3 of 77.5 available minutes. **What was cut to make room: H2's seed box 8.5 -> 4.0 minutes
(about 42 -> 20 cells at 12 s per cell) and H0's 4.0 -> 1.0 minute (about 12 -> 3 cells at 20 s)**, both INCOMPLETE-by-design arms that cannot be adopted on one seed; H1's three full seeds, every
latency / validity / slot phase and the forgetting probes are unchanged. HM sits after H2 and before H0 because it is a candidate and H0 only measures what Hindsight does natively. If the owner would
rather keep H0's cells at the old size, the next thing to cut is HM's generic box (its cells fail structurally on many hard cells anyway), not H1.

## 8. Defects found, and what was done

| # | Defect | Where | Fix | Test |
|---|---|---|---|---|
| D1 | The residue scan counted the system catalogs: on an EMPTY store `Ines` read 261 hits, `Tove` 20, `Leo` 3,556 (`pg_proc`, `pg_description`, `sql_features` hold ordinary English: "lines", "unicode"). Two of the eight friends a seed can draw would have made F5 / F6 (HARD cells) FAIL any arm on a clean store; the planner statistics were read by byte | `arms/pg_store.py` `scan` | the owner map is the engine's schema only (catalogs / other databases counted apart as `ignored_system_hits`), statistics counted exactly through `pg_stats`; all 46 pool names now scan 0 on an empty store (one residual: `Leo` has 15 hits in the WAL, not a disk-cell token today) | `test_zmb_pg_store.py` x3 |
| D2 | The embeddings shim uploaded onnxruntime 1.30 telemetry to Microsoft; the Python hook is blind to it | `embed_shim.py` and every launch point | `ORT_DISABLE_TELEMETRY=1` forced + API call; HM driver and `ss` sampler cover the child process; `::ffff:` loopback read as loopback | `test_zmb_embed_shim.py`, `test_zmb_bakeoff.py` (4) |
| D3 | A recall QUERY is logged by Hindsight's audit log: after a forget, a verification read that names her re-creates residue (3 hits: `audit_log` + WAL). The disk cells scan before any read, so F5 / F6 are right; a probe that reads then scans would be wrong | the double and the docs | the fake now writes the query to `audit_log`; test pins the order | `test_zmb_hindsight_arm.py` |
| D4 | The double lied about the real server in seven places (concise text suffix, tokens for LLM modes, per-item strategy, observation shape and non-invalidation, list/recall fields, trace scope/started_at and its outliving the bank, `/version` features) | `arms/fake_hindsight.py`, `test_zmb_hindsight_arm.py` | corrected; the old merge model kept behind `merge_observations=True` | 4 new tests |
| D5 | The busy-check patterns were unanchored (an editor, `tail -f` or the shell asked matched) and incomplete (`samantha_bar_conv.py` and the voice regression probe / its `/tmp/zoe-voice-harness.lock` were not checked) | `bakeoff.py` | anchored to the interpreter, the missing bar and probe added, the harness lock probed | real-process tests (a real spawned bar, a real decoy) |
| D6 | The validity count read trace rows of an EARLIER bank of the same name (a bank delete does not touch `llm_requests`): 23 rows for 20 calls | `bakeoff_measure.py` | rows older than the phase are ignored | `test_zmb_bakeoff.py` |
| D7 | HM glue: no subject in the attribute key (14/20 needles), four lab attributes with capitalised values only; G3 double-counted the Hindsight layer for HM | `arms/hm.py`, measure | subject key, the spec's slots, HM's own line count | `test_zmb_hm_arm.py` (2) |
| D8 | Report: no way to run the window in daylight, no D baseline for an embedding arm, no HM, a missing driver counted nowhere | measure / gates | the hook, Z0e, HM, TEST-HOOK banner | `test_zmb_bakeoff.py` (14) |
| X1 | zoe-data `memory_supersede.exclusive_conflict`: `subject_key` ignores a friend's NAME, so "friend Quill lives in Ashgrove" retires "friend Ivo lives in Dunwich" in the nightly pass (5 of 20 friend facts retired on the real run; a "where did I live before?" then answered with those homes) | `services/zoe-data/memory_supersede.py` | NOT fixed here (the live service): task handed off | repro in the task |
| X2 | zoe-data cue path: "friend moved to X" also retires that friend's job fact on two of three seeds (C6 seed-dependent) | same module | NOT fixed here: task handed off | repro in the task |

Also noted, not fixed: 668 `/tmp/mat-debug-*.log` files (onnxruntime 1.30 debug logs from earlier runs in the bake-off venvs) litter `/tmp`; a reboot clears them.
The 1,107-warning chroma pydantic deprecation noise in the test output is pre-existing.

## 9. Remaining risks for run 2

1. **Memory.** In daylight with the live brain the stack plus the HM driver took MemAvailable to 975 MB (the window's 1.2 GB guard aborted run B). My own limits were exceeded: peak stack RSS
   about 930 MB (limit 700) and MemAvailable 975-1,383 MB (limit 1.5 GB), because the HM verbatim tier brings its own 190-380 MB and the box runs at 9.5-10.6 GB used with the brain up. In the real window
   the brain is stopped and the clone takes its place, so the margin is different, but nobody has measured the clone + HM driver + stack together: the guard exists for that.
2. **The Gemma clone was never in the loop** (the live brain served this run: same weights and flags, but a shared slot). The clone command is generated and was printed; start-up of the clone itself is
   covered only by the shimmed unit tests.
3. HM's measured RAM (895 MB steady) FAILS G0; HM's generic cells fail on structure. Neither is a defect of the window; both will read as NOT_ADOPTABLE in the record, which is the honest result.
4. HM-L2 is borderline by nature (11.7 ms / 29.8 ms in two runs); read it with that spread.
5. Z0e needs chromadb and the cached MiniLM model (`~/.cache/chroma/onnx_models`); the shim needs `/tmp/fastembed_cache` (a reboot clears it): the preflight checks the second, the first degrades to a note.
6. X1 and X2 are live zoe-data bugs that make the Z0 baseline look worse on temporal cells than a fixed Z0 would; if they are fixed before run 2 the Z0 column moves.
7. H0 and H2 observation behaviour on the real server (no invalidation) may make H0 better than the pre-registered expectation; that is what the window is for.
8. The smoke ran 10 cells per arm and 10-call validity; the 100-call JSON validity (G0) and the full cell boxes are unmeasured until the window.

## 10. Lane results and what was stopped

Lanes (separate processes, 2026-10-06 21:3x): `tests/unit -m ci_safe` **2,793 passed, 10 skipped** (54 deselected: not `ci_safe`); `scripts/perf/zmb services/zoe-data/tests/test_zmb_lab.py` **86 passed** (the lab lane also holds the three new Z0e tests; two of them skip where chromadb or the cached MiniLM model is absent). Stopped: `zoe-bakeoff-hindsight`, `zoe-bakeoff-embed`, the scratch Postgres container, every `hm_window.py` driver; the window's own
restore stopped the units in both runs. Left on disk (runtime files under `/home/zoe/.zoe/bakeoff-2026-10/`): the generated `hindsight-*.env`, `egress-*.log`, `run-*.log/json`, `hm-*.json`, the `pgdata` directory
(it holds the synthetic first-contact banks' log rows and the database `hs_fresh`; the window's stale-bank sweep and `erase_orphans` clear them).
