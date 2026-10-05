---
type: Reference
title: Zoe Memory Bench (ZMB) - foundation
description: The benchmark that measures what no public memory benchmark does - authority (who may change what the user said), forgetting, identity, extraction fidelity, abstention - with generated gold, deterministic scorers, a negative control on every claim, per-axis Wilson intervals, an in-process lab driver, an arm-adapter interface for the memory-system bake-off (Z0, Hindsight, Graphiti) and the pre-registered bake-off decision rule. What is built, how to run it, what each axis proves, the artifact format, and what is stubbed.
tags: [memory, benchmark, zmb, authority, forgetting, negative-control, bake-off, eval, zoe-data]
timestamp: 2026-10-05T18:00:00Z
---

# Zoe Memory Bench (ZMB) - foundation

Code: `scripts/perf/zmb/` (package), `scripts/perf/zoe_memory_bench.py` (entry point).
Tests: `tests/unit/test_zmb_scorers.py`, `tests/unit/test_zmb_runner.py`, `services/zoe-data/tests/test_zmb_lab.py` (all `ci_safe`).
Design record: `/home/zoe/.zoe/agent-tools/research-drop/zoe-memory-bench-design-2026-10-05.md`; the bake-off it serves:
`/home/zoe/.zoe/agent-tools/research-drop/memory-system-decision-2026-10-05.md` section 6. This is build-plan "PR 3" (the
foundation); the live driver (synthetic store routes, the chat tier, Telegram, the timer) is not in it.

## Why it exists

LoCoMo, LongMemEval and BEAM measure recall of things the user said. None measures **who is allowed to change what the
user said** (authority), whether a deleted thing **stays deleted**, whether text that is not the user's can **write a
memory**, or whether a name is the **account's** and not a recalled row. Those are exactly the failures of the week of
2026-10-05 (a speech-to-text fragment became the owner's name; the nightly digest superseded what he said; a forgotten
name returned). "Better than mem0 / Zep / Letta / ChatGPT / Nomi" therefore cannot be a LoCoMo number. It has to be a
per-axis pass rate **with n, a Wilson interval and a negative control that turns the cell red** - and the same spec has
to be runnable against another system. ZMB is that.

Nothing here is a claim about any other system: **no competitor has been run**. What it supports today is "Zoe's Z0, on
this spec, with these controls".

## Running it

```bash
python3 scripts/perf/zoe_memory_bench.py --dry-run                # the plan; nothing runs
python3 scripts/perf/zoe_memory_bench.py --list [--axis b]        # every cell: id, axis, tier, expected, controls
python3 scripts/perf/zoe_memory_bench.py --tier store             # controls first, then the measurement (~3 s)
python3 scripts/perf/zoe_memory_bench.py --control off            # ONLY the negative controls; exit 2 if one stays green
python3 scripts/perf/zoe_memory_bench.py --arm Z0-off             # the same thing: the negative-control arm
python3 scripts/perf/zoe_memory_bench.py --only 'A1.digest.*' --seed fresh
python3 scripts/perf/zoe_memory_bench.py --arm H1                 # a stub arm: every cell SKIPs with the install hint
python3 scripts/perf/zoe_memory_bench.py --record-baseline        # alone; full run, baseline seed, Z0, controls proven
python3 scripts/perf/zoe_memory_bench.py --compare-baseline       # exit 1 on a regression; REFUSES with no baseline
```

| Flag | Meaning |
|---|---|
| `--tier store` (default) | the brain-free cells, run by the lab driver. Brain-tier cells are listed as declared SKIPs, each with its reason |
| `--tier full` | the same, and the brain-tier cells are requested: the lab cannot run them, so they SKIP and the run is `partial` (the live driver, a later PR, runs them) |
| `--arm <name>` | `Z0` (default), `Z0-off`, `H0`, `H1`, `H2`, `G` (see "Arms") |
| `--seed baseline\|fresh\|<str>` | the corpus seed. `baseline` = `zmb-v1`; `fresh` = a held-out world of the same shapes, recorded in the artifact; a held-out run sits BESIDE the baseline and never becomes one |
| `--only <ids>` / `--axis <a,..>` | a subset (ids, or a prefix ending `*`; axis letter or name). Unknown id/axis/arm/control = exit 2. A partial run NEVER records a baseline |
| `--control off\|<csv>` | run only the control pass with those features off |

No `ZOE_PERF`, no lock, no window: the lab driver is in-process (the real `MemoryService` over an in-memory collection),
so it cannot touch the running zoe-data, the live palace, Postgres, the brain or any backup. Default artifacts go to
`~/.cache/zoe/zmb_{last,baseline}.json` and `zmb_trend.jsonl`; `--results/--trend/--baseline` override them.

Exit codes: 0 `ok` / `partial` / `skip`; 1 `regression`; 2 `error` (a crash, or a REFUSAL), a bad flag, or `--control` with a
control that stayed green.

## The negative-control rule (the point of the whole thing)

> A cell that cannot go red when the feature it claims to measure is broken measures nothing. Silence is not proof.

Every store-tier cell names the **controls** (`controls: [...]` in the spec) whose removal must turn it red. A run is
ordered so this is mechanical, not a habit:

1. **Controls first.** The selected controlled cells run on the lab arm with their features switched OFF in-process (no
   restart, nothing left patched). Every one must come back FAIL. If one stays PASS - or errors, or skips - the instrument
   is **not instrumented** and the run is **REFUSED**: status `error`, exit 2, no axis table, nothing measured, the arm under
   test is never even built. The refusal is written to the artifact and the trend like every other exit.
2. **Then the measurement**, same spec and seed, on `--arm`.

The controls (`lab_driver.CONTROLS`), each a named feature the benchmark claims protects something:

| Control | Switches off |
|---|---|
| `authority` | `ZOE_MEMORY_AUTHORITY=off`: a model write may supersede / archive / contradict what the user said (the S1 signature) |
| `identity` | the automatic-writer name wall: a digest may assert the owner's name |
| `tombstone` | the forget tombstone: a late extractor pass may resurrect a forgotten name |
| `sweep` | the forget sweep archives nothing while the handler still says it forgot |
| `affect` | `ZOE_AFFECT_CONSENT_GATE=off`: feelings are recorded for guests and children |
| `extractor` | a lazy extractor: day/month flipped, roles guessed from first names, questions and assistant text mined, pronouns pinned on the user, pets as children, negation ignored |
| `gate` | the write-quality gate: questions, meta-rambling and transcript echoes are stored |
| `reader` | a scripted reader that answers from the nearest row instead of declining (the canary instrument check) |
| `physical_erase` | `ZOE_MEMORY_PHYSICAL_ERASE=0`: a hard delete / forget removes the row through the API and leaves the text on disk (the F5 / F6 disk cells, REAL Chroma, byte-scan of a copy) |

A cell lists every control that must be off together (`controls: ["extractor", "gate"]` = a two-layer defence: `--control
off` switches both). **Sanity** cells (`sanity: true`) are positive controls - "the owner teaching their own name is
stored", "after a forget a deliberate re-teach is stored", "the scripted reader DOES answer a question the store can answer"
- they must pass, but they guard the instrument against "block everything" and are not evidence about the system, so they
are left out of the axis pass rate.

Three layers prove it (all in `services/zoe-data/tests/test_zmb_lab.py`):

* every controlled cell goes red with its features off (96 of 96 on this spec), and each control individually flips exactly
  the cells it alone guards;
* a genuinely broken instrument (a control switch wired to nothing) and a genuinely vacuous cell (a probe that cannot fail)
  each make the real runner refuse - and the same command passes once the switch is real;
* the cells measure the real code, not the environment flag: break the wall itself (`find_conflict` / `may_override`, the
  identity guard, the tombstone lookup, the extractor) with no flag touched and the cells go red.

## Axes and cells (what this PR implements)

117 cells: 106 store-tier (run in the lab), 11 brain-tier declared-and-skipped. Per axis (`--list` is the source of truth):

| Axis | Cells (store tier) | What it proves | Control | Known failures (targets, tracked, never a regression) |
|---|---|---|---|---|
| (a) authority | **A1** writer x attribute matrix: 8 model writers (`digest`, `turn_digest`, `voice_turn_digest`, `idle_consolidation`, `consolidation`, `brain_tool`, `mcp`, `decay_sweep`) x 7 attributes (name, home, work, age, birthday, spouse, pet), each attempting an `edit`, an `archive` and an assertion against the owner's row = **56**; **A2** the 2026-10-05 incident through the REAL nightly `run_memory_digest` (4 attributes); **A5** the held-back contradiction is a `disputed` candidate linked by `contradicts_id`, not lost and not recalled (6). | a model write can never supersede, archive or contradict what the user said; it waits as a candidate | `authority` (name cells: `authority`; A2.name: `authority`+`identity`) | none |
| (b) extraction fidelity | B1 day-first dates, B2 pet not child, B3 stated roles, B4 roles never guessed, B5 negation, B6 assistant text not the user's, B7 pronoun anchored to the person, B8 entity precision/recall | the deterministic extractor stores the right fact for the right entity and none of the anti-facts | `extractor` (B6: + `gate`) | **B9** `I have two kids, X and Y` drops both names |
| (e) abstention | E1 a question is not a fact, E2 the assistant's denial is not stored, E3 a false premise is not written; **E4/E5** sanity: the scripted-reader canary check | asking about something never said leaves nothing behind to "remember" | `extractor`+`gate` (E4: `reader`) | **E1b** a recall question with no `?` (as STT delivers it) is stored |
| (f) forgetting | F1 the real `memory_forget_entity` handler archives every row naming X and keeps everyone else; F2 a late `turn_digest` / `digest` / `idle_consolidation` pass cannot resurrect X inside the TTL; F4 sanity re-teach; **F5 / F6** (capability `disk`, real Chroma, SKIPs where chromadb is absent) no byte of X is left in the on-disk palace after the forget / the audited hard delete | forget means forget, against the extractors that run seconds behind the conversation AND on disk (`docs/knowledge/forgotten-text-physical-erase.md`) | `sweep` / `tombstone` / `physical_erase` | **F3** six minutes later (the 300 s tombstone expired) a digest over the same transcript resurrects X |
| (h) identity | H1 seven writer labels (incl. one nobody has heard of) x three name phrasings; H2 the `review(edit)` door; H3 a third-person fragment becomes a person candidate, not the owner's name; H4 sanity | the owner's name comes from the account, never from a recalled row | `identity` | **H5** `User goes by X` is not recognised as a name assertion |
| (g) emotional | G3: an emotional record is kept for every household member incl. children with no stored consent row (owner decision 2026-10-05, default `ZOE_AFFECT_CONSENT_GATE=household`) and never for a guest; five identities, the real `_affect_allowed` gate | feelings are never recorded for a guest or an unrecognised voice (the household members are sanity cells that pin the owner's decision: a policy change must flip them deliberately) | `affect` (the guest sentinels are the controlled cells) | none |
| (c) temporal, (d) recall, (i) poisoning | not built yet (later PRs) | | | |

The known failure is **measured, not assumed**, and is a real gap in `main` today:
`F3` (audit F10: the tombstone is an in-process dict, 300 s). B9, E1b and H5 were targets until #1882 fixed them (2026-10-06): the speaker's own name list is kept, an unpunctuated recall question is never stored, and every self-name template is walled. `H5` was (`identity_facts.asserted_user_name` had no `goes by`,
yet the extractor's own template emits `User goes by {0}`). They are in the public table on purpose: a table without them
reads as cherry-picked. When one is fixed its `expected` flips to `PASS`, it gets a control, and `test_z0_measures_as_documented`
tells you it is time.

**Declared and skipped (brain tier), 11 cells, each with its reason:** the brain's reply after the incident (A), the pasted
roster, the third-party date and the correction (B; samantha_bar S20-S22), the never-said and false-premise replies (E; S3),
the forgetting cascade into Postgres-backed derived stores and a restart (F), the polluted-row identity reply (H), and the
emotional thread (G; S4, judged). `--tier full` lists them as SKIP; the live driver is a later PR.

### What a lab measures, and what it does not

* It measures **store logic**: the real `MemoryService.ingest / review / supersede_by`, the real authority and identity walls,
  the real forget handler, the real deterministic extractor and write-quality gate, the real nightly digest (its model calls
  scripted). It does **not** measure retrieval quality (the in-memory collection ranks by a deterministic bag-of-words
  distance, a stand-in for the embedder) or any reply of the brain.
* The `owner_typed` path is `extract_candidates` -> the write-quality gate -> `ingest(source="chat_regex")`. It skips the
  reconcile step and the person-link step (both need Postgres). A pasted email and a third-party fragment are treated as the
  owner's typed words - which is what the live pipeline does today (the known gap I2 of the design).
* The anti-fact cells (B4, B5, B6) are satisfied by an extractor that stores nothing; the **recall** cells (B1, B2, B3, B7, B8)
  are what a null extractor fails. Read the axis, not one cell.
* `claimable` in the artifact means the instrument is proven for that axis (controls red, no uncontrolled passing cell, no
  ERROR). A *public number* also needs the n in the design's pass-bar table; it is quoted with n, k and the Wilson 95%
  interval, never as a bare percentage.

## The spec format

JSON, stdlib only (`scripts/perf/zmb/scenarios/<axis>.json`; the docstring of `zmb/spec.py` is the schema):

```json
{"spec_version": 1, "axis": "authority", "cells": [{
  "id": "A1.<writer>.<attr.name>", "title": "...", "tier": "store", "kind": "script",
  "controls": ["authority"], "expected": "PASS", "lme_map": "...",
  "matrix": {"writer": ["digest", "mcp"], "attr": [{"name": "home", "owner_fact": "User lives in {home}."}]},
  "events": [{"text": "<attr.owner_fact>", "speaker": "owner_taught"},
             {"text": "...", "speaker": "system_writer", "writer": "<writer>", "proposes": ["..."], "op": "edit", "attr": "home"}],
  "probes": [{"kind": "store", "assertions": [{"op": "present", "contains": ["{home}"], "statuses": ["approved"]}]}]}]}
```

* `{slot}` placeholders are filled from the **seeded world** (`zmb/world.py`: `owner`, `spouse`, `home`, `dob_text`,
  `dob_flip`, `canary`, ...), so gold is generated, never annotated: no corrupt-golden-answer ceiling, and the same spec runs
  on the baseline seed, a held-out seed and every arm. All names are invented (nobody real); roles draw from disjoint pools so
  a role guessed from a first name is detectable. The artifact carries counts and labels, **never household text**
  (a test greps it for every pool string).
* `<name.field>` placeholders drive the matrix expansion (one cell per combination; a matrix entry's `_controls` /
  `_expected` / `_sanity` override that cell's own).
* **Events**: a turn (`speaker`: `owner_typed`, `owner_taught`, `owner_voice_verified`, `panel_unverified`, `third_party`,
  `pasted_email`, `assistant`, `system_writer`), or an action: `forget`, `advance_clock`, `ingest_as`, `idle_pass`.
* **Probes**: `store` (assertions over the row export: `present`, `absent`, `count_eq`, `count_at_least`, `all_have`,
  `field_set`), `facts` (gold facts recalled + anti-facts absent), `entities` (precision/recall of the names held),
  `recall` (needles / anti-needles / canaries in the recall packet), `answer` (the scripted reader).
* A malformed spec is a loud `SpecError` (unknown key, axis, tier, control, slot, a store cell with no kind, a brain cell with
  no skip reason, a known-FAIL target that also claims a control). A typo in a spec can never silently pass.
* **Scorers** (`zmb/scorers.py`, pure): store assertions, needles / anti-needles, canary tokens (`zorbl-NN`, which exist
  nowhere else in the world), fact recall + anti-fact precision, entity precision / recall / F1, Wilson intervals. Every FAIL
  carries a `stage` - `write` (the store is wrong), `read` (retrieval missed), `answer` (the store is right, the reply wrong).

## The artifact

`~/.cache/zoe/zmb_last.json`, written on **every** exit path (copied from `voice_regression_probe.py`: "a gate that can
silently not-run is not a gate"; a skip or a refusal is never a pass):

```text
status        ok | regression | partial | skip | error     run_kind  measure | control
harness_version, scorer_version, spec_digest, tier, arm, seed, corpus_seed, held_out, revision{commit,dirty}, selection{only,axis}
instrument    {controls_off, checked, red, lab_controls_red "96/96", green[], not_run[], ok, uncontrolled[]}
axes          {axis: {cells, n, pass, fail, skip, error, sanity_pass, sanity_fail, pass_rate, wilson95[lo,hi],
                      hard_violations[], targets_failing[], uncontrolled[], claimable}}
cells         [{id, axis, tier, verdict PASS|FAIL|SKIP|ERROR, stage, expected, controls[], sanity, duration_s, brain_turns,
                evidence (counts and labels), reason}]
hard_violations[], compare{has_baseline, comparable, regressions[], improvements[], new[], red, notes[]}
teardown      {proven, kind: "lab: in-memory store, nothing persisted", live_store_guard_trips}
refusal       (only when REFUSED) "instrument not instrumented: ..."
```

* **Statuses.** `ok` = every selected store cell ran, no hard invariant failed, nothing regressed. `regression` = a hard
  invariant failed (whatever any baseline says) OR a previously PASSING cell is not PASS. `partial` = a subset was asked for, or
  `--tier full` in the lab. `skip` = nothing was measured (a stub arm). `error` = a crash or a refusal.
* **Hard invariants** are zero-tolerance: authority, identity, forgetting, abstention, emotional (G3) cells that are neither
  targets nor sanity cells. One failure (or an ERROR - a hard cell that could not be scored is not a clean bill) is red with no
  baseline at all. Extraction is graded: only a previous PASS that stops passing is red.
* **Targets** (`expected: FAIL`) are tracked in `targets_failing`, never red; a PASS there is an `improvement` to lock in.
* **Baseline** (`zmb_baseline.json`): `cells` verdicts + `corpus_seed`, `spec_digest`, `scorer_version`, `arm`, `revision`. Recorded
  alone, from a full run, on the baseline seed, on Z0, with the instrument proven. A changed seed, spec digest or scorer version
  makes verdicts **not comparable** - noted, never red. `--compare-baseline` with no valid baseline REFUSES (exit 2).
* The trend line (`zmb_trend.jsonl`) carries status, per-axis `[pass, n]`, hard violations, regressions and `instrument_ok`.

## Arms and the adapter interface (`scripts/perf/zmb/arms/`)

A cell never imports `MemoryService` (a test pins that). It speaks `Turn` in and reads rows out through five calls plus
`reset` / `close`:

```python
class Arm(ABC):
    reset(user_id)            # a fresh, empty store for a demo_bar_<8 hex> id
    ingest(turns)             # -> IngestReport(turns, written, refused, retired, notes)
    recall(query, k)          # -> top-k rows (the answer packet)
    forget(entity)            # "forget everything about X" -> the arm's own confirmation
    as_of(query, ts)          # rows as the store believed them at ts (Z0: NotImplementedError - no as-of read yet)
    stats()                   # -> {"rows": [...], "counts": {status: n}, "writes_refused": n}  the store export
    # optional capabilities: advance_clock, ingest_as/stats_as, run_idle_pass, answer  (a cell needing one an arm lacks SKIPs)
```

A row is a plain dict (`id, text, status, authority_class, origin, contradicts_id, entity_type, memory_type, user_id`): scorers
read the store through this export, never a vector index.

| Arm | State | Notes |
|---|---|---|
| `Z0` | **implemented** | the current `MemoryService` + extractors + forget handler + nightly digest, in-process over the lab store |
| `Z0-off` | **implemented** | Z0 with every control off: the negative control (the S1 signature must appear). `--arm Z0-off` = `--control off` |
| `H0` / `H1` / `H2` | **stub** | Hindsight 0.10.2: no Zoe layer / verbatim, observations off, Zoe gate + forget ledger / concise + observations, fenced consolidation. Every call raises `NotImplementedError` with the install hint (py3.12 venv under `/home/zoe/.zoe/bakeoff-2026-10/`, `hindsight-api-slim==0.10.2`, scratch Postgres `pgvector/pgvector:pg17` on :55432 and the clone brain on :11500, in an operator-approved brain-stop window) |
| `G` | **stub** | Graphiti as a library with no Graphiti LLM: Zoe's extractors build `EntityNode` / `EntityEdge`, `add_triplet`, the authority wrapper around `resolve_edge_contradictions`. `NotImplementedError` with the install hint |
| `MV` | **implemented** (needs the bake-off venv) | MemPalace 3.10.0 as a LIBRARY (`get_collection` + `upsert/query/get/delete`): the VERBATIM tier alone. Real store = scratch palace + scrubbed HOME; without the library every call raises `NotImplementedError` with the install hint (SKIP). `InMemoryVerbatimStore` is a TEST DOUBLE for the CI lane |
| `HM` | **glue implemented, Hindsight tier STUB** | Hindsight (distilled) + MemPalace (verbatim) composed: one write gate in front of both tiers, one forget ledger, authority order at recall, evidence frame, voice-lane cache, per-tier failure isolation. `--arm HM` on the generic spec SKIPs with the Hindsight install hint; its OWN cells are `hm_cells.py` (below) |

A stub arm run is `status=skip` (every cell SKIP with the install hint), never a pass. The bake-off runner fills an arm in
without touching a scorer: implement the five calls against the system, report rows in the export shape, run the same specs.
The instrument (controls, scorers, cells) is Z0's lab and does not change per arm.

## HM cells (`scripts/perf/zmb/hm_cells.py`): the combined arm, Hindsight + MemPalace

Why separate: the generic runner's control pass runs the lab arm (Z0) with `lab_driver.CONTROLS` switched off; the HM protections are
`arms/hm_policy.Controls` (guest gate, speaker class, forget-verbatim, ledger write check, distiller skip, provenance cascade, requeue
siblings, physical erase, evidence frame, authority, voice policy, parallel lookup, tier isolation, wing isolation, no-model-on-write).
Same rule as everywhere: **each named switch is turned OFF, one at a time, and the cell must go red; only then is it measured with every
protection on.** A cell that stays green with its control off REFUSES the run (exit 2).

```bash
python3 scripts/perf/zmb/hm_cells.py --list
python3 scripts/perf/zmb/hm_cells.py                                   # double store (CI lane): controls first, then the measurement
MALLOC_PERTURB_=85 PYTHONMALLOC=malloc bash /home/zoe/.zoe/bakeoff-2026-10/mp_run.sh scripts/perf/zmb/hm_cells.py --store library
```

The distilled tier in every cell is `FakeDistilledTier`, a TEST DOUBLE (the real `HindsightDistilledTier` is a stub). The cells prove the
GLUE, never Hindsight's or MemPalace's retrieval quality. Result: 20 cells (17 graded, 2 sanity, 1 tracked target), 18 negative controls
all red, 17/17 graded green on the real library with a scrubbed heap (16/16 on the double; HM-F6 needs a disk store).

| Cell | Claim | Controls (each alone) |
|---|---|---|
| HM-F1 / F2 | forget clears BOTH tiers at t+0 and at t+6 min after a replay and a distiller re-proposal; the rest is kept | `forget_verbatim`; `ledger_write_check`, `distiller_skip` |
| HM-F3 / F7 | derived facts that never name the entity go with their source chunk; the bundle's innocent chunks' facts are rebuilt | `cascade_provenance`; `requeue_siblings` |
| HM-F6 | no file of the verbatim palace holds the name (needs the real store and a scrubbed heap) | `physical_erase` |
| HM-F5 | TARGET (expected FAIL): an STT misspelling of the forgotten name survives | none (already red) |
| HM-G1 | guest words reach neither tier; a child's emotional turn is kept | `guest_gate` |
| HM-I1 / I2 | a pasted email's instruction is not in ordinary recall; an explicit quote is framed, instruction withheld | `speaker_class`; `frame` |
| HM-H1 | a third person's "I'm Dev" never reaches the packet or the identity line | `speaker_class` |
| HM-A1 / A2 | a verified verbatim statement outranks an older derived fact; a distiller proposal cannot supersede a user-stated one | `authority` |
| HM-L1 / L2 | voice-lane p95 <= 600 ms (cache); the second lookup adds <= 25 ms p95 (modelled latencies in CI, measured on the real run) | `voice_policy`; `parallel_lookup` |
| HM-T1 | one tier down degrades the packet and says so | `tier_isolation` |
| HM-W1 | a verified turn is one chunk and zero model calls on the write path | `sync_distill` |
| HM-V1 | another member's chunk is never in this member's packet | `isolate_wing` |
| HM-R1 | the verbatim tier hosted in zoe-data fits the RAM gate (arithmetic over measured numbers) | the sidecar + second-session + batch-32 shape is red |
| HM-S1, HM-F4 | sanity: the verified user's words are stored/recalled/distilled; a deliberate re-teach after a forget is stored | none (positive controls) |

Pilot instruments (`scripts/perf/zmb/pilot/`, run with the bake-off venv): `household.py` (a deterministic 1,000-turn synthetic household +
60 exact-reference queries), `verbatim_pilot.py` (MemPalace library vs plain Chroma: ingest, size, RSS, hit@k with Wilson intervals,
latency, MemPalace's dedup in dry-run), `forget_probe.py` (what a delete leaves on disk), `two_store_ram_probe.py`, `batch_rss_probe.py`,
`ef_probe.py`, `lock_probe.py`. Measurements and the design: `docs/research/memory-arm-hm-hindsight-mempalace-2026-10-06.md`.

## `samantha_bar.py --only` / `--axis`

The live bar (`scripts/perf/samantha_bar.py`) gained the same selection, so a ZMB report can join its S-scenarios per axis
(`AXIS_OF`): `--only S21,S22`, `--axis b` (letter or name, intersected with `--only`). A short run is `status=partial` (it
used to be `error`), unknown ids exit 2, **`--record-baseline` is refused with a selection**, and `--compare-baseline` compares
only the selected scenarios (a selected scenario that regressed is still `regression`, exit 1). A scenario that only asks about
facts another seeds (`S8` reads `S1`/`S7`'s) still runs those seed turns but sends and reports only the selected asks; the
day-1 backdate runs only when a multi-day scenario is in play. See `docs/knowledge/samantha-bar.md`.

## The bake-off decision rule (pre-registered; copied from the decision record, section 6.1)

Fixed in advance; no threshold changes after seeing results.

A candidate arm is **ADOPTABLE** only if **all** hard gates pass, measured on three seeds with the Zoe thin layer ON and no
fork of the candidate's source (config, tags, wrapper only):

* **G0 feasibility:** zero non-loopback connects; installs on aarch64 py3.12; added steady RSS **<= 600 MB** and burst
  <= 900 MB (PSS of all candidate PIDs, net of what Chroma/ONNX frees in zoe-data) with `MemAvailable` never below 1.2 GB
  during the run; extraction JSON validity >= 95% over >= 100 calls on the 8,192 slot; stock prompt + chunk + output reserve
  fits the slot.
* **G1 latency:** recall p95 <= 600 ms warm (ADR-hindsight criterion) **or** served from a write-behind cache with a
  cache-miss path that is not on the voice turn; writes add 0 ms to the turn and <= the current serial-pass slot-seconds per
  turn.
* **G2 hard invariants, 0 violations each:** authority (A1 x56, A2, A3, A5, A8), forgetting (0 resurrections at t+0 and t+6
  min after the candidate's own re-processing), poisoning (0 canaries persisted or obeyed), abstention (0 canary leaks),
  affect (G3: 0 retained rows for non-consenting identities).
* **G3 sustainability:** the Zoe layer needs <= 1,000 new lines **and** the arm lets us delete >= 5,000 of the 17,923 memory
  lines. (If adoption leaves two systems, it is net negative.)

Among adoptable arms, **ADOPT the one that wins**: it must beat Z0 by more than the Wilson 95% interval on **at least two** of
{B extraction recall (incl. B-children-list >= 95%, B-names 0 conflations), C temporal (C1 >= 95%, C2 >= 90%, C6 0 collateral),
D hit@5 at 100 filler >= 90%, E decline >= 90%} and be **no worse beyond the interval** on any other graded cell.

* **Ties go to Z0** unless the candidate also meets G3 deletion; then the lower-RAM arm wins.
* **If H1 and H2 both pass:** choose H1 (verbatim, observations off) and treat observations as a later, separately-gated flag.
* **If Graphiti arm G does not beat Postgres bi-temporal columns on C2, C4, C6:** do not adopt; port the model.
* **If no arm is adoptable:** keep Z0 with audit P1-P3, port BM25 + half-open `as_of`, close the question until the brain slot
  or RAM changes (a second slot or >=16k context would reopen mem0-class and Graphiti-native extraction).

**Instrument checks first** (the record's rule, now mechanical here): Z0-off must be red on A1/A2/A3 with the S1 signature; a
stub brain that always answers from the nearest row must fire the canary in (e); the tombstone-with-fake-clock control must
resurrect in (f); an arm whose gate is bypassed must write the intruder row. **If any control is not red, the run is void** - that
is the refusal above.

Note on G2's affect line: copied verbatim, it predates the owner's 2026-10-05 policy decision (#1875: household members incl.
children are recorded, guests never). The cells here follow the shipped policy - "0 retained rows for a guest" is the hard
invariant, and the household members are sanity cells - and the rule's wording should be updated with the owner before the
bake-off is run, not after seeing results.

Mapping to this foundation: G2's authority / forgetting / identity / abstention / affect invariants are the hard cells here
(A1 x56 = `A1.<writer>.<attr>`; A2; A5; G3 = `G3.consent.*`; forgetting F1-F2, abstention E1-E3). **A3 (provenance honesty),
A8 (graph edges), the poisoning cells (I1-I6), temporal (C1-C7) and recall (D) are not built in this PR**, so an arm cannot be
declared ADOPTABLE on this spec yet; the rule applies once those land. F3 (resurrection past the TTL) is a known failure of Z0
itself and is the cell on which a candidate with a durable forget ledger should beat it.

Arm measurements the runner does not take (RAM / PSS sampling, recall p50/p95, brain-slot seconds, extraction JSON validity,
install size, cold start, egress audit) belong to the bake-off runner around the arm, not to the scorers.

### Added 2026-10-06 for the HM arm (Hindsight + MemPalace); G0-G3 above are unchanged

HM is ADOPTABLE only if every G0-G3 item passes **and**: **HM-G0a** the whole stack adds <= 600 MB steady / <= 900 MB burst with the verbatim
tier counted (in-process with the shared embedder <= 50 MB; flush and backfill batches <= 8); **HM-G1a** voice-lane recall p95 <= 600 ms served
from the write-behind cache, chat-lane second lookup adds <= 25 ms p95, verbatim query p95 <= 100 ms warm; **HM-G1b** either tier down is a
degraded packet, not a failed turn; **HM-G2a** 0 resurrections in either tier at t+0 and t+6 min after the arm's own replay, and 0 files of the
verbatim palace match the ledger within 60 s of the forget; **HM-G2b** 0 guest chunks, 0 canaries in an ordinary packet, explicit-quote packets
framed with the instruction withheld, 0 third-person names in the packet or identity line; **HM-G2c** a distiller proposal never supersedes a
user-stated fact and a verified verbatim statement outranks an older derived fact; **HM-G3a** the verbatim tier must REPLACE zoe-data's own
palace, not sit beside it (two stores for one job is net negative). **H1 is the default over HM**: HM is chosen only if it beats H1 and Z0
beyond the Wilson interval on at least one of {exact-quote hit@5 at 100 filler, tier-down answered, recall of a fact said in the last turn}
and passes every HM cell at 0 violations.

## Not in this PR (the build plan continues)

Synthetic store routes in zoe-data (`synthetic-ingest`, `synthetic-digest`, `capture-synthetic`) and the live driver that uses
them; the chat tier and the local judge with its calibration set; temporal, recall (hit@k at 30/100/300 filler) and poisoning
axes; provenance (A3) and graph-edge (A8) cells; the Telegram one-liner, the 05:15 timer and `non_pass_streak`; the
LongMemEval_S retrieval-only anchor; the `--claim-report` renderer; the Hindsight and Graphiti arms themselves. Everything here
is lab-only: the live service was not touched.
