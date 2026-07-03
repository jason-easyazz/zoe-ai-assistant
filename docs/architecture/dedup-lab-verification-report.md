# Dedup-gate lab verification report (2026-07-03)

Lab verification of the two plausible-but-unproven dedup-gate defects flagged by the
2026-07-02 memory audit. Both were tested experimentally against demo data / isolated
stores before any patching, per the verify-before-fix brief. Baseline commit: `3ef56f9b`.

**Outcomes at a glance**

| Finding | Verdict | Action |
|---|---|---|
| 1 — idle-consolidation idempotency-key mismatch | **REFUTED** (mismatch exists, but no duplicate accumulates — PARTIAL) | No code change (this report only) |
| 2 — `_same_value` over-merge on shared incidental modifiers | **CONFIRMED** | Fixed in PR #963 (merged 2026-07-03, + regression tests) |

---

## Finding 1 — idempotency-key mismatch (REFUTED / harmless)

**Hypothesis.** Idle consolidation keys its stable dedup id on `sha1(session_id + text)`
(`services/zoe-data/memory_idle_consolidation.py:349`) while the voice path keys
`(user_id, normalized_text)` (`services/zoe-data/expert_dispatch.py:459`). Because
`_ingest_or_supersede` only consults `MemoryService.search(limit=3)`
(`services/zoe-data/expert_dispatch.py:366`), a cross-session repeat of the same fact
might miss the top-3 in a well-populated store and accumulate as a near-duplicate row.

**Experiment.** Demo user `demo_dedup_lab_afb64dec` against a fresh isolated
Chroma/MemPalace store (`MEMPALACE_DATA_DIR=/tmp/dedup-lab-mempalace`); real
`MemoryService`, real MiniLM embedder, real `classify_against_existing` gate; the
idle-consolidation path called directly with a stubbed Gemma extractor returning the
target fact from two different session ids, after seeding 30 filler facts through the
normal ingest path.

**Measured results.**

| Measure | Result |
|---|---:|
| Filler facts seeded | 30 |
| Final approved rows for the repeated fact after 2nd cross-session ingest | **1** (collapsed) |
| Search hit for prior fact at `limit=3` — 10 fillers | hit, rank 1 |
| Search hit at `limit=3` — 20 fillers | hit, rank 1 |
| Search hit at `limit=3` — 30 fillers | hit, rank 1 |

**Why no duplicate forms.** The key mismatch is real but neutralized by a second,
durable collapse layer: `MemoryService` derives the idempotency key from the turn id
(`services/zoe-data/memory_service.py:773`), but the **durable row id** is derived from
`user_id`, text, source, scope, visibility, memory type — *not* session or turn id
(`services/zoe-data/memory_service.py:207`) — and writes are `upsert`
(`services/zoe-data/memory_service.py:403`). The second ingest arrives under a different
idempotency key but upserts onto the same durable row.

**Limitations.** Postgres was not exercised (transcript rows stubbed via
`consolidate_session(..., get_ctx=...)`); MemPalace was driven from cached source.
No embedding stub was needed — the real embedder ran.

**Cleanup.** `MemoryService.delete_user()` removed all 31 demo rows;
`export_user()` returned `[]`; worktree stayed clean. No real-user memory touched.

**Verdict: no fix warranted.** Aligning the idle path's key to
`(user_id, normalized_text)` would be a cosmetic consistency change with no observed
behavioural defect; per the brief, refuted findings ship no code change.

---

## Finding 2 — `_same_value` over-merge (CONFIRMED → fixed in PR #963)

**Hypothesis.** `_same_value` (`services/zoe-data/memory_quality.py:279-283`) returned
True when two same-attribute facts shared *any* salient value token, so a genuine
correction sharing an incidental modifier was collapsed-by-richness (`skip`) instead of
superseding (`update`).

**Experiment.** Pure unit-level drive of `classify_against_existing` at `3ef56f9b`,
correction pairs sharing incidental modifiers plus two control groups.

**Measured results (pre-fix).**

| Pair | `_same_value` | Gate said | Correct? |
|---|---:|---|---|
| `my car is a red Toyota` vs `my car is a red Honda` | True | skip | **No** |
| `my dog is a small brown poodle` vs `…small brown beagle` | True | skip | **No** |
| `my employer is the big tech company Google` vs `…Microsoft` | True | skip | **No** |
| `my favorite drink is iced black coffee` vs `…iced green tea` | True | skip | **No** |
| `my car is a Toyota` vs `my car is a Honda` (plain correction) | False | update | Yes |
| `my dog is a poodle` vs `my dog is a beagle` | False | update | Yes |
| `my favorite drink is coffee` vs `…tea` | False | update | Yes |
| `my car is a red Toyota` vs `my car is Toyota red` (rephrase) | True | skip | Yes |
| `my dog is a small brown poodle` vs `…brown small poodle` | True | skip | Yes |
| `my favorite drink is iced black coffee` vs `…black iced coffee` | True | skip | Yes |

4/4 shared-modifier corrections mis-merged; all controls behaved.

**Fix (PR #963).** `_same_value` now requires one fact's value-token set to be a
**subset** of the other's: rephrasings (equal sets) and richer restatements
(`Neil` ⊂ `Neil, spelled N-E-I-L`) still merge, while a correction leaves a leftover
token on *each* side and correctly supersedes. Regression tests cover all four
confirmed pairs (expect `update`) and the three reordering controls (expect merge,
never a duplicate row); the PR #794 "never drop a correction" tests stay green
(61/61 locally and in CI). Independent cross-vendor review passed with no blocking
findings.
