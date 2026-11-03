---
type: Reference
title: Chroma HNSW corruption - crash-loop recovery
description: zoe-data SIGSEGV crash-loop after a reboot is chroma 0.6.3 HNSW index corruption, not an OOM; the two flaws, the resize_factor guard, and the re-embed rebuild recipe (the documents in sqlite are the truth).
tags: [chroma, mempalace, memory, incident-signature, runbook]
timestamp: 2026-10-29T00:00:00Z
---

# Chroma HNSW corruption - crash-loop recovery

Operational record from the 2026-07-31 incident, written for the next time. See [index](index.md); later chroma work is in
[chroma-1-5-migration.md](chroma-1-5-migration.md); the general triage page is [incident-runbook.md](incident-runbook.md).
The recipe below is for the chroma 0.6.3 era; check the installed chroma version before applying it.

## Signature

- zoe-data crash-loops after a reboot: `systemctl --user show zoe-data` gives `ExecMainCode=2 ExecMainStatus=11` (SIGSEGV),
  the app log ends right after "memory embedder warmed", about 7 s per cycle, `oom_kill 0` (so NOT an OOM).
- A foreground run with `PYTHONFAULTHANDLER=1` shows `chromadb ... local_hnsw.py _apply_batch -> index.add_items` during write-ahead-log
  backfill on the first touch of a collection.
- Worse: queries return nonsense BEFORE the crash surfaces, so recall can be silently broken for hours first.

## The two flaws

1. **Capacity at load.** Load allocates `live_count x hnsw:resize_factor (1.2)` slots, but the index file still holds tombstoned
   elements; once deletes pass about 17% the file's element count exceeds the allocation and the native code overruns.
   Guard: set `hnsw:resize_factor` to 2.0 in `segment_metadata` for every vector segment (drawers and audit were both done).
2. **Torn persist (the real killer).** `index_metadata.pickle` newer than `data_level0.bin`: the vector payloads are garbage,
   `add_items` hangs single-threaded or segfaults multi-threaded. The vectors are not recoverable from the index.

## Recovery recipe

1. Stop zoe-data and `tar` a backup of the palace directory.
2. Extract every record from `chroma.sqlite3`: the metadata segment is the source of truth (documents live under
   `embedding_metadata` key `chroma:document` and are never lost). The write-ahead log is auto-purged, so the index cannot be
   rebuilt from it.
3. In a FRESH process: `client.delete_collection(...)` first (before any read - delete only touches instantiated segments),
   `get_or_create_collection(name, metadata={"hnsw:space": <original space>, "hnsw:resize_factor": 2.0})`, then re-`add` in
   batches of 100. The default embedding function re-embeds (about 340 s for 3,465 documents).
4. Verify: a semantic query returns sane hits AND a metadata `update` succeeds (update is the crash path), then start the service.

## Traps

- The MemPalace chroma backend creates NEW collections with `hnsw:space: cosine`, but the legacy drawers/audit collections are
  `l2`. Match the original space or distances change meaning.
- The reboot is the trigger, not the cause: the corruption sits latent until the next cold load.
- hnswlib's load call in this fork is `Index.load_index(dir, is_persistent_index=True, max_elements=N)`; vanilla arguments fail
  with "corrupted or unsupported".
