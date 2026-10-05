---
type: Reference
title: Forgotten text is physically gone — where Chroma leaves it, the erase, the one-time scrub (2026-10-06)
description: Measurement on the live palace with a synthetic canary under a demo user (where the text survives a hard delete and a compaction - SQLite free pages, in-page slack, the FTS5 index, the embeddings_queue write-ahead log, orphan HNSW dirs, HNSW heap residue, compaction backups), the fix in the hard-delete / forget / compaction paths (memory_residue.py, erase_residue_sync, erase_rows), the verifier script, the ZMB F5/F6 cells with a negative control, the measured cost, and the operator steps for the EXISTING store.
tags: [memory, mempalace, chroma, forgetting, privacy, sqlite, hnsw, zmb, operator]
timestamp: 2026-10-06T00:00:00Z
---

# Forgotten text is physically gone (owner rule: "forgotten means forever")

Trigger: the bake-off research (`docs/research/memory-arm-hm-hindsight-mempalace-2026-10-06.md` section 3.3, PR #1884)
found that a Chroma 1.5.9 API `delete` does not erase text from the files. It had never been tested on the live
palace. This is that test, and the fix.

**No household text appears here.** The live measurement used ONE synthetic canary sentence
(`canary-7f3a9c-Quillfeather`) ingested under a demo id (`demo_resid_7f3a9c01`) through the running service's own
API; the palace files were byte-scanned as a COPY (`memory_residue_check.py`, counts only) and the copy deleted.

## 1. Where the canary survived on the LIVE store (chroma 1.5.9, `~/.mempalace`)

Positive control first: 0 hits before ingest, 7 right after (the instrument sees a stored canary).

| Moment | Byte-hits | Where (SQLite page owner) |
|---|---|---|
| after `memory_store` ingest (control) | 7 | `embedding_metadata` 2, `embedding_metadata_string_value` index 2, `embedding_fulltext_search_content` 1, `embeddings_queue` 2 |
| after the audited hard delete (`forget-synthetic` -> `delete_user`), t+0 | 5 | SQLite **free pages** 2, string-value index slack 2, `embeddings_queue` 1 |
| same, 20 s and ~4 min later (organic ticks) | 5 | unchanged: nothing re-visits the pages |
| run 2: hard delete, t+0 | 8 | free pages 2, FTS content 1, string-value index 2, `embeddings_queue` 3 |
| run 2: after an in-process compaction (0.9 s, 284 rows, `status=ok`) | 3 | free page 1, string-value index 1, `embeddings_queue` 1 — and a NEW orphan HNSW dir |

* **FTS5 trigram index** (`embedding_fulltext_search_data`): after a delete the canary's rare trigrams are still in the
  index blocks (lab, chroma 1.5.9: 2 of 7 rare grams present vs 0 for a never-ingested control; 0 after an FTS5 `rebuild`).
  A whole-token scan cannot see them (the index stores 3-byte grams), the gram check can.
* **`embeddings_queue`** keeps the text: an ADD/UPSERT/UPDATE record carries the document and metadata as JSON until its
  segment persists past it (1,387 rows and 786 unpersisted drawers ops on 2026-10-06). `index-health` shows it:
  `unpersisted_ops`.
* **HNSW files**: the canary was NOT found in 2 delete-then-compact cycles on the live palace (n=2 is not a rate). But the
  freshly rebuilt live drawers index carries heap bytes: its `data_level0.bin` holds 104 sentence-like ASCII runs of 24+
  chars (2 with common English words) — text-shaped heap in an index file. In the ZMB lab (chromadb 0.6.3, python-hnsw)
  the residue reproduces in `length.bin` (see section 4); on 1.5.9 the lab shape gave 0/20.
* **Orphan HNSW directories**: chroma's `delete_collection` never removes the segment directory. The live palace had **2**
  (3.3 MB) before this work and **3** after my compaction. Each is a pre-rebuild index.
* **Compaction rebuild**: re-adds the STORED vectors and the live rows' documents verbatim (nothing is re-embedded). A
  hard-deleted row is not in the export, so its text is not carried; an archived / rejected / superseded row is live to the
  export and IS carried. The old collection's pages go to the SQLite freelist — every drawer's text, readable until a
  `VACUUM` — and each compaction writes a **plaintext JSON export + a tar of the whole palace** to
  `~/.zoe/palace-backups` (3 pairs, 320 MB on 2026-10-06). Those keep pre-forget bytes; see section 7.
* **The entity-forget path never deleted anything.** `memory_forget_entity` archived the matching approved rows
  (`review(archive)`): the document, `review_note = "forget_entity:<name>"` and the audit trail's before/after text all
  stayed, as did every pending / superseded / rejected row naming the entity. `sweep_soft_archive` is a status flip too (a
  decay policy, not a forget; unchanged).
* Not reachable through the live API: the chat route sent "forget everything about Quillfeather" from a demo id to the
  brain, so the forget handler was exercised on the real library in the lab (F5, tests) instead of live.

## 2. The fix (this PR)

| Piece | What it does |
|---|---|
| `services/zoe-data/memory_residue.py` (stdlib only) | `scan_palace` (byte-scan a COPY, attribute each SQLite hit to its table / free page), `scrub_sqlite` (blank the queue's copies of deleted rows, purge the queue below the persisted seq, FTS5 `rebuild`, `VACUUM`, WAL checkpoint), `remove_orphan_segments`, `enable_heap_scrub` |
| `memory_service.erase_residue_sync` | under the maintenance gate (ops drained, openers blocked): scrub + orphan dirs, then VERIFY the live files for the forgotten text; text still in an HNSW file -> rebuild the index (`ZOE_MEMORY_INDEX_COMPACT`) and verify again. Never raises; `ok=False` + a WARNING (counts only) if text remains |
| `MemoryService.delete_user` | calls it after the audited delete (and only when something was removed) |
| `MemoryService.erase_rows` | the forget path: content-free `forget_erase` intent row, rows, their per-row audit trail, `forget_erase_done`, then the erase. Only the caller's rows; fail-closed |
| `memory_forget_entity` / `memory_forget_last` | archive/reject first (the fail-safe), then `erase_rows`; the entity sweep also takes the pending / disputed / superseded / archived / rejected rows naming the entity |
| `compact_drawers_index_sync` | after a verified rebuild, scrubs the freed pages and removes the orphan dir |
| `POST /api/memories/maintenance/scrub-residue` | the one-time / on-demand scrub of the whole palace (internal token) |
| `index-health` | adds `orphan_segment_dirs`, `heap_scrub_active`, `physical_erase_enabled` |
| `scripts/maintenance/memory_residue_check.py --token T` | the verifier; counts only; `--also-scan DIR` for the compaction backups; `--scrub-orphans` |
| ZMB `F5` / `F6` + control `physical_erase` | REAL Chroma, byte-scan of a copy: the forget / the hard delete leave no byte; `ZOE_MEMORY_PHYSICAL_ERASE=0` must turn both red |

Flags: `ZOE_MEMORY_PHYSICAL_ERASE` (default ON; `0` = the old behaviour), `ZOE_MEMORY_HEAP_SCRUB` (default OFF, section 4).

Safety under the running service: the erase takes `_COMPACT_LOCK`, closes the maintenance gate, drains leased ops
(`_drain_collection_ops`) and only then touches the SQLite file; it reopens the gate in a `finally`. `VACUUM` and the
FTS5 rebuild run on a second connection while chroma's client stays open; measured on chromadb 1.5.9: the open client
keeps reading, writing and answering `where_document` queries, and a fresh client (a restart) replays the blanked queue
and sees every row. The queue blanking only touches ADD/UPSERT/UPDATE records that have a LATER DELETE for the same id
(replay ends with the id absent whatever they carried); a re-add after the delete is never touched.

## 3. Cost, measured on a COPY of the live store (63 MB `chroma.sqlite3`)

| One-time scrub (`scrub_sqlite`) | |
|---|---|
| wall time | **0.82 s** |
| peak RSS | **53 MB** (a separate process; in-service it is a SQLite `VACUUM`, temp file = DB size on disk) |
| file size | 63.3 MB -> 50.3 MB (13 MB of free pages gone) |
| canary | 3 hits -> 0 |

A per-forget erase is the same work plus a verify scan (~0.3 s for the whole palace per 24 needles). The gate is closed for
that second; a collection op that arrives waits (up to 60 s) instead of failing.

## 4. HNSW heap residue (the part an API cannot fix)

`hnswlib` writes its pre-allocated block from `malloc`: the bytes of an element slot it never filled are whatever the
heap held. A long-lived process that handled the text leaves it there. Where it shows depends on the library and the
shape of the palace (research: 13-21 of 60 runs on 1.5.9 in a tiny palace).

Fix: a scrubbing allocator on the HOST process (`MALLOC_PERTURB_=85`, optionally `PYTHONMALLOC=malloc`), or
`ZOE_MEMORY_HEAP_SCRUB=1`, which runs `mallopt(M_PERTURB, 85)` when the palace is first opened (same effect, no env
change). Measured (ZMB F6, a fresh process per run, 60 runs per mode, chromadb 0.6.3):

| Host allocator | runs with the name in an HNSW file |
|---|---|
| default | **5 / 60** (all in `length.bin`; 2 / 20 in an earlier batch) |
| `mallopt(M_PERTURB, 85)` at runtime (`ZOE_MEMORY_HEAP_SCRUB=1`) | **0 / 60** |
| `MALLOC_PERTURB_=85` + `PYTHONMALLOC=malloc` | 0 / 20 |

On chromadb 1.5.9 the same cells gave 0 / 20 both ways (the research found 13-21 / 60 on 1.5.9 with a different palace
shape), so the residue is real, shape-dependent, and the scrub removes it where it reproduces.

Cost (chromadb 1.5.9, 1,500-row write + 200 queries + 200 gets + 300 updates, median of 5): default 6.90 s wall,
`mallopt` 7.04 s (+2%), env pair 7.20 s (+4%); RSS unchanged (102 MB). It is process-wide and zoe-data also hosts the
in-process Moonshine STT, so it is **default OFF** until the voice-gate replay has measured it: this PR changes no voice
file and does not turn it on.

A rebuild in a clean child does not help on its own (the host's next persist writes its own heap again): that is why the
rebuild is a verify-then-rebuild step and the durable fix is the host allocator.

## 5. The ZMB cells

`F5.forgotten_text_not_on_disk` (the real `memory_forget_entity` handler) and `F6.hard_delete_not_on_disk` (the audited
`delete_user`) run over REAL Chroma in a throwaway directory (capability `disk`; they SKIP where chromadb is not
installed, as in the slim CI lane) and byte-scan a copy, with the host heap scrub ON (the recommended deployment; the
arm turns it on for the disk cells and restores it on close). Negative control `physical_erase` (`ZOE_MEMORY_PHYSICAL_ERASE=0`):
both go red (F5: 16 hits in FTS content, metadata, string index and queue; F6: 3 hits in the queue).

## 6. Operator steps for the EXISTING store

1. Merge; deploy restarts zoe-data (the usual `deploy.yml`). No config needed: `ZOE_MEMORY_PHYSICAL_ERASE` defaults on.
2. Once, on the box: `curl -s -X POST -H "X-Internal-Token: $ZOE_INTERNAL_TOKEN" http://127.0.0.1:8000/api/memories/maintenance/scrub-residue`
   (add `?tokens=<a synthetic canary>` to verify). It blanks the queue's stale copies, rebuilds FTS5, `VACUUM`s (about 1 s,
   collection ops wait), and removes the orphan HNSW dirs (3 today, 4.2 MB). Idempotent.
3. Check: `python3 scripts/maintenance/memory_residue_check.py --token <canary> --also-scan ~/.zoe/palace-backups`
   and `GET /api/memories/maintenance/index-health` (`orphan_segment_dirs` 0).
4. Optional, after a voice-gate replay: `ZOE_MEMORY_HEAP_SCRUB=1` in the service `.env` (or the unit's
   `MALLOC_PERTURB_=85`) and restart.

My own canary leftovers on the live store are synthetic (`demo_resid_7f3a9c01`); the scrub in step 2 removes the SQLite
copies, and `~/.zoe/palace-backups/mempalace-*-20261006-014900.*` (made by my compaction) can be deleted by the owner.

## 7. Not done here (owner decisions / next)

* **Backups keep forgotten text.** Every compaction writes a full-text JSON export and a tar of the palace (other backup
  jobs were not inspected). Disclose it, or rewrite backups on forget (research doc 2e). Measure with `--also-scan`.
* **Postgres**: `chat_messages` (the turns the digest skips via the ledger), `user_portraits`, `people` etc. are cascaded
  logically (`memory_forget_cascade`), not physically (dead tuples, WAL). Unmeasured.
* The forget ledger stores only a salted hash and never the name; the `forget_erase` tombstones store counts and id hashes.
* A name typed into a forget that is NOT whole-word in a row (a misspelling, a pronoun-only row) is still not matched
  (research hole 2c).
