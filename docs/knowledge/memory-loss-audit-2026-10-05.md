---
type: Reference
title: Memory-loss audit — where the "missing" owner ingests went (2026-10-05)
description: Reconstruction of every audit-recorded owner ingest since 2026-08-15 against the live palace, 17 historical backups and the compaction exports; the bucket table, per-row evidence (ids and hashes only), the proven root causes with file:line, what was ruled out, and what the fixing PR changed. True loss since 2026-08-15 is zero; 21 owner rows were lost unrecorded in July.
tags: [memory, mempalace, audit, incident, test-pollution, chroma, samantha]
timestamp: 2026-10-05T00:00:00Z
---

# Memory-loss audit — where the "missing" owner ingests went (2026-10-05)

Trigger: `docs/research/memory-fidelity-audit-2026-10-05.md` (PR #1867) §4/§3.5 reported that **93 of
114** owner (`jason`) ingests since 2026-09-20, and **195** since 2026-08-15, are "no longer in the palace
with no deletion record", and that 107 of the 195 are literal strings from the repo's tests. The owner's
standing directive: *"The memory needs to be flawless… Samantha doesn't have dementia, neither can Zoe."*

**No household text appears in this document** — row ids (last 8 hex), dates, counts and `sha256[:10]` of
text only. Everything below was measured read-only: copies of `~/.mempalace/chroma.sqlite3` and of 17
historical palaces/exports, the app log + rotated segments (2026-09-22 → 10-05), Postgres untouched.

## 1. The answer first

| Question | Number |
|---|---|
| **True loss (bucket d) among the 195 / 93 flagged ingests** | **0** |
| Distinct rows behind the 195 events (≥ 08-15) | **17** (the 93 events since 09-20 are **14** of them) |
| …of which never existed in this palace (bucket a, audit-only phantoms from one test file) | **17 of 17** |
| Owner rows that existed, were removed, and left **no** record (all-time, all in July 2026) | **21** (20 recoverable from a backup JSON on disk, 1 not) |
| Owner rows lost since the 2026-07-31 backup | **0** (79 owner rows in every 08-10 palace, 82 on 09-28, 93 on 10-04: every one is still live) |
| Compactions (10-04 08:57, 10-05 01:21) that dropped a row | **0** (258→258, 267→267, id-set and document parity) |

So: the finding as stated is an **audit method error plus one test-isolation leak**, not memory loss. The
audit counted `ingest` *events* (222 since 08-15) as if they were rows (35), and treated an `ingest` audit
row as proof the drawer persisted. For one test file it does not. The genuine, unrecorded loss is older
(July) and is documented in §5 with a recovery path.

## 2. Method and sources

* **Ledger.** Every `mempalace_audit` row with `action=ingest`, `user_id=jason`, `timestamp ≥ 2026-08-15`
  (222 events, 35 distinct `mempalace_id`s; 114 events / 32 ids since 09-20). Reproduced the audit's 195 / 107 /
  93 exactly before reclassifying.
* **Presence.** The live `mempalace_drawers` (268 rows) and 17 other palaces: `pre-wipe-20260704`,
  `corrupt-20260713`, `backup-20260731`, the five 2026-08-10 recovery copies, `pre-b08-20260928`, the seven
  nightly tarballs 09-29 → 10-05, plus the two compaction exports and `~/.zoe-backups/jason-mem-20260705-184122.json`
  (the owner's 27 rows at 18:41 local on 07-05).
* **Repeat evidence.** `MemoryService.ingest` writes the drawer first and the audit row after, and a row that is
  not `pending` is never rewritten (durable dedup, `memory_service.py:1244`). So an *approved* id with N ≥ 3
  `ingest` audit events cannot have persisted between them.
* **Logs.** `MEMORY_*` lines in `zoe-data.app.log` + `.1–.5` (2026-09-22 →), stderr/stdout gz segments. There is
  **no** log coverage before 09-22 (rotation), which is why July is argued from snapshots, not logs.
* **Origin.** Exact-literal matching against every string constant (AST) in `tests/`, `services/zoe-data/tests/`,
  `scripts/`, plus `git log -S` over all history, plus the pre-fix version of `test_mempalace_integration.py`
  (`git show d723dd2b^`). The old test was read, **not re-executed**.
* Reproducible with `scripts/maintenance/memory_ledger_audit.py` (read-only; §8).

## 3. Bucket table

Buckets: **a** test/script literal under the real id; **b** rejected at write time / never stored; **c** stored
then superseded/archived *with* a record; **d** stored then removed with *no* record; **e** unknown.

| Window (owner `jason`) | events | distinct rows | present now | **a** | **b** | **c** | **d** | **e** |
|---|---|---|---|---|---|---|---|---|
| ingests since 2026-08-15 | 222 | 35 | 18 rows / 27 events | **17 rows / 195 events** | 0 | 0 | 0 | 0 |
| ingests since 2026-09-20 | 114 | 32 | 18 rows / 21 events | **14 rows / 93 events** | 0 | 0 | 0 | 0 |
| all-time ledger (since 07-04) | 7,820 | 127 | 89 rows | 19 rows + 1 test-session row (= 7,285 events) | 0 | 0 | 0 (ledger) | 0 |
| owner rows seen in any backup but absent now, no record | – | – | – | – | – | – | **21 rows** (July, §5) | – |

Notes on the empty buckets, with the evidence that each is genuinely empty rather than unexamined:

* **b = 0.** An `ingest` audit row exists only after a successful `_write_row`, so a rejected candidate can never
  *be* a ledger row. Rejections are therefore not the explanation for any of the 195. (They were, however,
  invisible: §6.) In the retained logs there are **0** `MEMORY_QUALITY_REJECT` / `MEMORY_STORE_DROPPED` lines for
  any user, 60 `MEMORY_DEDUP_SKIP` lines of which 59 are `demo_bar_*` and 1 is the owner, and no
  `PII scrubber` / `just-forgotten` drop. The gate, run now over the owner's 103 live rows and over all 127
  distinct texts the audit ever recorded for them, accepts 125 and rejects 2 (both `weather_report` — the
  pre-#1042 junk, correctly). It does **not** reject genuine owner facts.
* **c = 0 in the ledger.** No ledger id has an `archive`/`edit` audit row (the superseded/archived owner rows —
  17 + 4 — are *present* with that status; they were never "missing"). Real supersessions are 20 in 93 days.
* **d = 0 in the ledger.** For all 17 ids the same facts hold: only `ingest` audit rows, never in any palace
  snapshot, and (below) a producer that explains why.

## 4. Bucket a — the evidence (17 rows, 195 events)

All 17 are written by **one file**, `services/zoe-data/tests/test_mempalace_integration.py`, in the
versions before #1731 (merged 2026-09-27 21:32 +0800 = 13:32Z). The last audit row of any of them is
**12:59:58Z** that day — 33 minutes before the merge.

| row id (last 8) | first ingest | last ingest | ingest events (all / ≥08-15 / ≥09-20) | status | source | session shape | text sha10 | len | repo literal |
|---|---|---|---|---|---|---|---|---|---|
| `5778c889` | 2026-07-05 | 2026-09-27 | 856 / 22 / 10 | approved | zoe_agent | – | `a769dc60e7` | 19 | yes (tests) |
| `6ea95f4a` | 2026-07-05 | 2026-09-27 | 857 / 22 / 10 | approved | zoe_agent | – | `914d8baec5` | 20 | yes (tests) |
| `e9daad34` | 2026-07-05 | 2026-09-27 | 856 / 22 / 10 | approved | zoe_agent | – | `459a7602f2` | 20 | yes (tests) |
| `8b457786` | 2026-07-05 | 2026-09-27 | 433 / 15 / 9 | pending | chat_regex | sess-opencl… | `a24f00c8a3` | 51 | derived from a test turn |
| `a5919540` | 2026-07-05 | 2026-09-27 | 433 / 15 / 9 | pending | chat_regex | sess-opencl… | `f9a414a060` | 38 | derived |
| `a7e68977` | 2026-07-09 | 2026-09-27 | 242 / 15 / 9 | pending | chat_regex | sess-hermes | `e4323a68b3` | 25 | derived |
| `ef768618` | 2026-07-05 | 2026-09-27 | 433 / 15 / 9 | pending | chat_regex | sess-hermes | `369c8c55c4` | 57 | derived |
| `774fae40` | 2026-07-05 | 2026-09-27 | 432 / 14 / 8 | approved | chat_regex | – | `90668fe529` | 54 | derived |
| `5f990e39` | 2026-07-05 | 2026-09-27 | 428 / 11 / 5 | approved | zoe_agent | – | `a6e0117450` | 30 | yes (tests) |
| `63cd23a8` | 2026-07-05 | 2026-09-27 | 427 / 10 / 4 | approved | memory_digest | – | `296969a552` | 27 | yes (tests) |
| `907b302a` | 2026-07-05 | 2026-09-27 | 428 / 10 / 4 | approved | chat_regex | – | `a6e0117450` | 30 | yes (tests) |
| `e92ced0e` | 2026-07-05 | 2026-09-27 | 427 / 10 / 4 | approved | zoe_agent | – | `0c45fd79f3` | 20 | yes (tests) |
| `09bc778a` | 2026-07-05 | 2026-09-26 | 360 / 5 / 1 | approved | turn_digest | sess-opencl… | `f8a4cf27ac` | 29 | derived |
| `7b88afba` | 2026-07-06 | 2026-09-26 | 26 / 4 / 1 | approved | turn_digest | sess-hermes | `fa0047b84f` | 14 | derived |
| `78cadfa8` | 2026-07-05 | 2026-08-20 | 61 / 2 / 0 | approved | turn_digest | sess-opencl… | `1f00d8d8a4` | 33 | derived |
| `cef6e76e` | 2026-07-05 | 2026-08-28 | 365 / 2 / 0 | approved | turn_digest | sess-hermes | `3f54c8f766` | 32 | derived |
| `1e122550` | 2026-07-06 | 2026-09-03 | 14 / 1 / 0 | approved | turn_digest | sess-hermes | `123ff9a4ce` | 18 | derived |

(Seven of the 17 are exact repo literals = the audit's 107 events; the other ten are what the extractors
derive from the same test turns, which is why a literal search under-counts.) Three more rows of the same
family fall before the window and are phantoms/test rows too: `9ce68f84` (191 events), `abd913c3` (15),
`86fcf913` (1 event, `sess-hermes`, test-shaped, too few events to prove non-persistence).

Why this is "never stored", not "stored then lost":

1. **Repeat ingests of approved ids.** `5778c889`, `6ea95f4a`, `e9daad34` were "ingested" 856–857 times,
   every time with `status=approved`. A persisted approved row makes the second ingest a silent no-op
   (`memory_service.py:1244`) — 856 audit rows are only possible if the drawer was absent each time.
2. **Suite-run batches.** These ids appear in 477 distinct minutes; 354 of them carry 13–14 of the 17 at once and
   380 carry ≥ 10. Between 85 % and 100 % of each id's events sit in such minutes: one pytest run = one batch. Since
   08-15 there is exactly one batch per run day (17 ids, 08-15/16/20/22/28, 09-03, 09-26) and four on 09-27.
3. **No snapshot holds any of them.** Not `corrupt-20260713`, `backup-20260731`, the five 08-10 copies,
   `pre-b08`, the seven nightlies, nor the two compaction exports.
4. **No removal record and no remover.** Only `ingest` audit rows exist for the 17 ids; no
   `archive`/`edit`/`delete_user` row; inside the service the only code that deletes drawer rows is `delete_user` (admin forget and
   forget-synthetic; §7) plus the verified compaction rebuild.
5. **The test, pre-fix.** `git show d723dd2b^:services/zoe-data/tests/test_mempalace_integration.py` ingests
   user `"jason"` with sessions `"sess-hermes"` / `"sess-openclaw"` (the 38 "external harness" session ids the
   audit could not place) through `_persist_memory_candidates`, `_background_memory_save`, … The test replaces
   the `mempalace` module with an in-memory `_FakeCollection` (drawers), but `MemoryService._audit_collection`
   opens a **real** `chromadb.PersistentClient` at `MEMPALACE_DATA_DIR`, default `~/.mempalace` — the live
   palace. The commit message of #1731 states it: *"every ingest in this file wrote audit rows there."*
6. **A second product of the same defect:** 860 `archive` audit rows, `actor=system`, ids `user-row` /
   `wing-row` (430 runs × 2) — the entity-forget test (`test_mempalace_integration.py:250`). They are the
   audit's entire "861 archive" population and say nothing about real rows.

Why `jason`, and why the conftest pin did not stop it: the file hard-codes the owner's id as its fixture user
(it predates the `demo_*` convention), and the pin (`tests/conftest.py`, #1773, 2026-09-29) landed two days
**after** the last run and is configuration only — `pytest --noconftest`, a script, or a harness importing the
service modules bypasses it silently. No guard existed at the door every writer uses.

The remaining literal-equal pairs among **live** rows (7 owner rows, 1 `u`, 1 `guest`) are the reverse
direction — real utterances (channel-shaped session ids `telegram-…`) that were copied into tests as
fixtures. They are not pollution and the audit script labels them so.

## 5. Bucket d — the genuine, unrecorded loss (July 2026, outside the ledger window)

Found while proving d=0: the owner's palace at 18:41 local on 2026-07-05
(`~/.zoe-backups/jason-mem-20260705-184122.json`, 27 rows, all `approved`) versus every later palace. **20 of
those 27 rows are gone**, and neither the 07-13 palace (`corrupt-20260713`) nor any later one holds them;
**no** `archive`/`edit`/`delete_user` audit row accounts for any of them (two carry only the weekly no-op
`edit` stamp). A 21st owner row exists only in the 07-13 palace. All were `approved`, from the owner's real
Telegram session on 07-04/05 (channel-shaped session ids).

| row id (last 8) | added (UTC) | source | type | text sha10 | audit |
|---|---|---|---|---|---|
| `985c3fa6` | 07-04 10:00 | conversation | person | `5b88cd1875` | ingest |
| `fd2f1968` | 07-04 10:03 | idle_consolidation | fact | `500eb2fd90` | ingest |
| `8264bfbd` | 07-04 20:00 | voice_fact | fact | `914d8baec5` | no-op edit only |
| `cd6d6da9` | 07-04 20:00 | conversation | person | `914d8baec5` | no-op edit only |
| `172a6b45` | 07-05 01:51 | idle_consolidation | fact | `7093acf507` | ingest |
| `78d92425` | 07-05 01:51 | idle_consolidation | fact | `16c41e50ea` | ingest |
| `2d76a764` | 07-05 02:48 | conversation | person | `c7c77d8c29` | ingest |
| `54dc4a7c` | 07-05 02:48 | conversation | person | `b5fd0d15ba` | ingest |
| `b2a4e799` | 07-05 02:49 | conversation | person | `120509768f` | ingest |
| `d7d51beb` | 07-05 07:50 | conversation | person | `7bbcd1b124` | ingest |
| `20f74861` | 07-05 07:51 | turn_digest | relationship | `e979829a3c` | ingest |
| `76aadad9` | 07-05 07:51 | turn_digest | event | `87de23871a` | ingest |
| `ed7f8dd2` | 07-05 07:51 | conversation | person | `79eed839cd` | ingest |
| `3b471145` | 07-05 07:52 | turn_digest | relationship | `fb1f3ab52a` | ingest |
| `e0c52f7f` | 07-05 07:52 | conversation | person | `67fde1d69e` | ingest |
| `eb8d521c` | 07-05 07:52 | turn_digest | event | `05eb8a40f0` | ingest |
| `a0da813b` | 07-05 07:54 | turn_digest | relationship | `231f0291d9` | ingest |
| `b47cea2c` | 07-05 07:54 | conversation | person | `49300d3eb8` | ingest |
| `d29d38f0` | 07-05 07:54 | conversation | person | `093347b8b3` | ingest |
| `1f31ff38` | 07-05 07:58 | idle_consolidation | fact | `255cb05464` | ingest |
| `32ae5e9b` | 07-13 01:29 | turn_digest | pet | `e2e4d1956a` | none (present in the 07-13 palace only) |

What can and cannot be said. The 20 rows existed at 10:41Z on 07-05 and were absent by 07-13; the surviving 7
of the 27 are the deterministic/explicit sources (`chat_regex` ×2, `voice_fact` ×2) plus three extractor rows.
The same evening produced #1042 (weather-report junk gate) and a manual memory dedup/cleanup
session, and `d50c552a` was re-ingested at 10:46Z — **consistent with a hand cleanup that used a raw delete**,
but there is no record of the actor and the retained logs start 09-22, so this stays *unproven*. For the 21st
row (`32ae5e9b`) there is no backup copy of the text except the 07-13 sqlite. **Recovery:** the 20 are intact in
the 07-05 export JSON (`id`, `text`, `meta`); restore the ones the owner wants through the normal path
(`POST /api/memories` with the original source) — a decision for the owner, not done here. Two of the 20 share a
text sha with a real utterance that exists in a live superseded row, so some are legitimately duplicates.

Larger unrecorded removals exist but touched **only** fixture ids: the 2026-08-10 purge (4,052 → 472 rows by
09-28: `family-admin` 1,725, `u1` 1,180, `newbie` 406, `existing` 406, two `demo-*` batches; **0 owner rows**) and the
2026-09-29 purge (482 → 257: `u1` 135, `existing` 45, `newbie` 45; 0 owner rows). Both were raw operator deletes with
no audit row — the same unrecorded-removal shape, which is why §6 closes that door.

## 6. What this PR changes

| Class | Fix | Where |
|---|---|---|
| a. tests/harnesses/scripts writing the live palace | `live_store_guard`: a pytest session may not **open** the live palace; a pytest/`ZOE_HARNESS` process may not **write** it under a real (non-synthetic, non-guest) id. Wired at `_palace_client` (every call, cached or not), `ingest`, `_write_row` (the door `review`/edit/supersede share), `_append_audit_sync` (the door the 7,078 phantom rows came through), `delete_user`. Violations are never swallowed (`_append_audit`'s best-effort `except` and `ingest`'s write wrapper re-raise them). The root `tests/conftest.py` now pins `MEMPALACE_DATA_DIR` too (it had no pin; the integration lane would otherwise trip the guard). | `services/zoe-data/live_store_guard.py`, `memory_service.py`, `tests/conftest.py` |
| a. audit + cleanup | `scripts/maintenance/memory_ledger_audit.py`: read-only (sqlite `mode=ro`, no chromadb), `--dry-run` is the only mode, output = counts + hashes. Reproduces the table above; lists test-literal drawers under real ids with a verdict (`likely_test_row` vs `likely_real_utterance_copied_into_a_test`); `--plan-archive FILE` writes the id list for the **normal** review path. **No purge ran and none exists in the script.** | `scripts/maintenance/` |
| b. invisible rejections | `memory_reject_ledger`: every refusal (`record_reject(source, reason)`) is counted per UTC day, in memory and in a small JSON file so a restart does not zero it; counts only, never text. The digest's gate and the idle consolidation (both silent before), `person_extractor` (debug-only), the three logging sites and `MemoryService`'s `opt_out` / `pii_reject` / `tombstone_drop` / `dedup` all feed it. The nightly digest pass logs `MEMORY_REJECT_SUMMARY window=1d rejected=N reasons=… sources=…` (also at 0). `MEMORY_IDLE_CONSOLIDATE stored=` now counts only real writes (it counted dedup-skips and PII drops — log said 4, audit 3) and adds `rejected= skipped= dropped= failed=`. | `memory_reject_ledger.py`, `memory_digest.py`, `memory_idle_consolidation.py`, `memory_service.py` |
| d. removal without a record | `delete_user` writes a content-free `delete_user` audit row (actor, reason, row count, ≤ 200 id hashes) **before** it removes anything and fails closed if it cannot; the user's own per-row trail (which carries text) is still purged but tombstones survive the purge. A structural test fails if any module other than the four audited functions removes palace rows. | `memory_service.py`, `tests/test_memory_loss_class.py` |
| weekly no-op rewrites | `_merge_near_duplicates` (the *caller*; `MemoryService.review` untouched): an exact-text duplicate of the **same subject** (same memory type + entity) is archived through `review(archive)` instead of `review(edit)` (which re-derived the row's own id: 6,585 of 6,605 audit `edit` rows, an HNSW upsert per row per week); the same words on **different entities** (the 138 legacy `family-admin` rows are 3 texts × 47 distinct notes/journals/people) are left completely alone; near-duplicates (≥ 0.85, not identical) still use `edit`. | `memory_digest.py` |

Documented, **not** changed (and why):

* **8 approved `guest` rows** (`voice_fact` 2, `voice_turn_digest` 3, `voice_regex` 3; written, never read). Making
  `ingest` refuse the guest sentinel changes voice-path behaviour that the identity work (another PR) is
  reshaping. The audit script counts them; the right fix is "unidentified speaker ⇒ pending candidate".
* **Raw operator deletes** (`remediate_ownerless_memories.py --delete`, the 08-10 and 09-29 purges) still bypass
  `MemoryService`. They take a backup first and are sanctioned one-offs; route them through `delete_user`
  (or add the tombstone) next time. The structural test only covers service code.
* **`family-admin`** (155 rows, 17 distinct texts): fixture pollution under a legacy, non-account id. Not
  touched; the weekly pass no longer rewrites it. Retire through forget/archive once the owner confirms.
* **July's 21 rows** (§5): recoverable, owner decision.
* **`tests/unit/test_memory_quality_weather.py`** carries real family names/dates as fixtures in a public repo
  (the seven live literal-equal rows are the same facts). Not changed here; worth sanitising.

## 7. Ruled out, with evidence

| Suspect | Verdict |
|---|---|
| Compaction rebuild (#1827), 10-04 08:57 and 10-05 01:21 | **Not a loss path.** Export 258 ids == the pre-compaction palace 258 (ids, documents, statuses: 0 mismatches); export 267 == pre-state 267. The 258 are all present after, plus 9 new rows (7 with `ingest` audit rows, 2 recorded `edit` replacements) and 1 later. Code: count/dim parity before the delete, verify (`count == n`, reach, owner-filtered query, metadata round-trip) after, restore-from-export + fail-closed gate otherwise (`memory_service.py:474-612`). Nightly tarballs 09-30 → 10-04: 0 rows dropped day to day. |
| `delete_user` / forget-synthetic touching real rows | **No.** 300 `MEMORY_FORGET_SYNTHETIC` lines in the retained logs, every id `demo_bar_*` / `demo_s1probe_*`; the route refuses any id that is not `demo|test_<tag>_<hex>` or is a registered account. `delete_user` only matches `user_id`/`wing == target`. |
| Chroma HNSW segment corruption (2026-07-31 class) | Damages the *index*, not the documents (they live in sqlite); recall can go quietly wrong but rows are not deleted. No palace in the chain shows owner rows vanishing at a rebuild (07-31 → 08-10: 0 dropped; 08-10 → 09-28: 0 owner rows dropped). |
| Rust-client `col.count()` wedge (10-04, #1815) | A hang, not a delete. |
| Test teardown using semantic-search-then-archive | Archives leave `archive` audit rows; the only ones in the ledger are the 860 `user-row`/`wing-row` phantoms. |
| Quality gate / dedupe rejecting genuine owner facts | No: §3 (b). |
| The weekly consolidation losing rows | It rewrites in place (same id), it does not delete; 138 rows × weekly. Fixed as noise/churn, not loss. |

## 8. Reproduce

```bash
python3 scripts/maintenance/memory_ledger_audit.py --since 2026-08-15 \
  --reference ~/.zoe/palace-backups/mempalace-drawers-export-20261004-085708.json \
  --reference ~/.zoe-backups/jason-mem-20260705-184122.json
python3 scripts/maintenance/memory_ledger_audit.py --since 2026-07-04 --json | less   # all-time
```

Tests (each with a negative control; the mutation results are in the PR): `services/zoe-data/tests/test_live_store_guard.py`,
`test_memory_loss_class.py`, `test_memory_reject_ledger.py`, `test_memory_idle_consolidation.py` (new case),
`tests/unit/test_memory_ledger_audit.py`.
