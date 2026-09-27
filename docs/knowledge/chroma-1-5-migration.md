---
type: Runbook
title: Chroma 0.6.3 → 1.5.x memory-store migration (B0.8)
description: Why Zoe's palace is migrated one collection at a time instead of with `mempalace migrate`, the measured rehearsal on a copy (10/10 proofs, peak RSS 372 MB, 4.4 min), every program that opens the store and the interpreter it runs under, the operator cutover checklist, and rollback by restoring the directory.
tags: [memory, chromadb, mempalace, migration, runbook, b0.8]
timestamp: 2026-09-27T00:00:00Z
---

# Chroma 0.6.3 → 1.5.x memory-store migration (B0.8)

This is program item **B0.8** in [beat-the-bar-2026-program.md](../architecture/beat-the-bar-2026-program.md).
The tool is `scripts/maintenance/chroma_migrate_rehearsal.py`. Its unit tests are in
`tests/unit/test_chroma_migrate_rehearsal.py`.

The store is `MEMPALACE_DATA_DIR=/home/zoe/.mempalace`, read from the live zoe-data unit env. It
is one `chroma.sqlite3` plus HNSW segment directories. The format change is **one-way**: a 1.5
client migrates the 0.6 sysdb in place on its first open, and a 0.6.3 client cannot read the
result. This is proven below.

## 1. Why not `mempalace migrate`

`mempalace` 3.10.0's `migrate.py::extract_drawers_from_sqlite()` selects every row in
`embeddings ⋈ embedding_metadata`, with **no collection filter**. It then adds all of them into a
fresh `mempalace_drawers`, which is the only collection it creates (line 340). Zoe's palace is
almost entirely audit rows:

| collection | rows (2026-09-27 21:20) | what it holds |
|---|---|---|
| `mempalace_drawers` | 365 | recall: the memories |
| `mempalace_audit` | 21,389 | one row per memory mutation (`"<action> <id> by <actor> for <user>"`), metadata-filtered only |
| `mempalace_audit_sec_*` (9) | 11 | leftovers from a security test; nothing reads them |

So the tool would do three things:
- push ~21k audit summaries into recall as drawers (each carries a `user_id`, so they surface for that user)
- re-embed all of them with MiniLM
- drop the audit collection

Its "readable and writable" happy path does nothing, and the 1.5 first open is exactly the
forward-only in-place migration. Chroma ships no 0.6→1.x tool (`chroma-migrate` is 0.4-only).

## 2. The per-collection recipe

`chroma_migrate_rehearsal.py run` never opens the live directory with any chromadb version.
Every mutating step calls `refuse_live()`.

1. **copy.** `rsync -a` the segment dirs first (rsync only reads the source). Then snapshot
   `chroma.sqlite3` with SQLite's online-backup API from a `mode=ro` handle. A plain copy of a
   live SQLite can tear, as the 09-25 backup did. Taking the snapshot *after* the segments means
   the log is never older than the HNSW files. The copy is then checked with `integrity_check`.
2. **export.** Read each collection from the copy's SQLite (`mode=ro`), with the same SQL as
   `export_memory_store.py`. It carries the ids, the documents, typed metadata (str/int/float/bool)
   and a type-preserving per-id hash. The 0.6.3 HNSW is **not** loaded.
   - Collection policy is fail-closed: drawers → re-embed, audit → constant vector, `_sec_`
     leftovers → skip and list. **Any other name aborts the export before a file is written.**
3. **lab venv.** `uv venv --python 3.12` under the rehearsal dir, then
   `uv pip install chromadb==1.5.9` with `--exclude-newer <today−14d>`. `numpy`, `onnxruntime`
   and `tokenizers` are **constrained to the zoe-data venv's versions** (1.26.4 / 1.23.2 / 0.23.2),
   so the vectors come from the same numerics the cutover venv will run. The wheel is
   `cp39-abi3-manylinux_2_17_aarch64`, and it installs.
4. **rebuild** (under the lab python). This builds a **new** persistent store:
   - **Drawers** are re-embedded with `ONNXMiniLM_L6_V2`, one session, 4 texts per call (see §6).
     The archive SHA256 `913d7300…` is asserted three ways: 1.5.9's `_MODEL_SHA256`, our pin, and
     the bytes on disk. 0.6.3 pins the same value.
   - **Audit rows** get `memory_service._AUDIT_NULL_EMBEDDING`, `(1.0,)+(0.0,)*383`, which is
     pinned equal by a unit test. Nothing searches them semantically.
   - Each collection is created with `embedding_function=DefaultEmbeddingFunction()`, so the
     persisted identity is `"default"`. mempalace 3.10's spoofed MiniLM EF also reports
     `name() == "default"` (`embedding.py::_build_ef_class`), so 3.10 and raw chromadb callers
     both open it without an "Embedding function conflict".
   - The effective HNSW settings are carried as `configuration={"hnsw": …}`. **The live drawers
     are `l2` with `resize_factor` 2.0** (the 09-25 rebuild's segment metadata overrides the
     collection config's 1.2). They are not `cosine`. `memory_service._semantic_search` blends
     `1/(1+dist)` into its ranking, so changing the space would silently rescale every score.
     A move to cosine is a separate, measured decision.
   - `config.json` is copied from the source with `"embedding_model": "minilm"` added. That
     stops 3.10's onboarding default (`embeddinggemma`) from ever re-pointing a MiniLM palace.
5. **prove.** Every proof runs in its own subprocess, because chroma#1218 can SIGSEGV in HNSW.
   Mutating proofs run on scratch copies that are deleted afterwards. See §3.
6. The script writes `manifest.json` (per-collection counts, digests, policy, HNSW settings, the
   skipped leftovers, every step's wall time and peak RSS) and **deletes the lab venv**.

## 3. Rehearsal, measured (2026-09-27, on a copy)

Kept at `~/.zoe/chroma-migration-rehearsal/2026-09-27/` (0700): `src/` is the copy, `export/`,
`dst/` is the migrated store, and `manifest.json`.

| proof | result |
|---|---|
| a. row counts: 1.x API **and** the export SQL run on the 1.x sqlite, equal to the export; HNSW space/resize kept; no `_sec_` leftovers | **PASS**: drawers 365 = 365, audit 21,389 = 21,389, 1.x schema readable by `export_memory_store.py`'s SQL |
| b. per-id document+metadata hash through the 1.x API | **PASS**: 0 mismatches, 0 extras, digests equal |
| b-neg. one metadata value changed on a scratch copy | **PASS**: caught (1 mismatch) |
| c. write round-trip on **each** collection: upsert, then a fresh process sees it and deletes it, then a fresh process sees it gone and the count restored | **PASS** on both |
| d. embedding cosine, 200 drawers: stored 0.6.3 vectors (old client on a scratch copy) vs rebuilt | **PASS**: min 1.0000. The 15 drawer vectors still in the sqlite `embeddings_queue` are also 1.0000; the rest of the log was purged, so the HNSW is the only other place old vectors live |
| d-neg. the same vectors paired with the wrong ids | **PASS**: caught (min −0.053) |
| e. recall parity, demo user `demo_b08_*` only: 40 synthetic facts seeded into scratch copies of both stores, top-10 for 20 queries, then teardown | **PASS**: 20/20 identical order; top-1 20/20; 0 demo rows left |
| f. negative control: the 0.6.3 client on the migrated store | **PASS**: raises `KeyError: '_type'` on open (loud, not a silent empty read) |

- **RAM:** peak RSS 372 MB, in the rebuild. Everything else stayed under 312 MB. Each heavy step
  ran at `nice -n 15` inside `systemd-run --user --scope -p MemoryMax=700M -p MemorySwapMax=0`.
  RSS is `wait4` `ru_maxrss`, the same number `/usr/bin/time -v` prints; `time` is not installed
  on the box.
- **Wall:** 266 s end to end, under contention (other agents' pytest runs):
  - copy 2.3 s
  - export 2.1 s
  - rebuild 156 s: audit 70 s, drawers 78 s including the model load
  - proofs about 100 s
- **Disk:** copy 181 MB, migrated store 94 MB, export 24 MB.

Proof e's verdict requires the **identical top-10 order** for every query. The opt-in `compare-recall --parity-tolerance` accepts an unchanged top-1 plus a top-10 Jaccard ≥ 0.9 instead; it is off by default.

Re-run with `python3 scripts/maintenance/chroma_migrate_rehearsal.py run --fresh`. `--date` must be `[prefix-]YYYY-MM-DD`, and the run dir must be a direct child of the rehearsal root that does not overlap the live palace. `--fresh` only deletes a directory carrying the tool's `.b08-rehearsal` marker, or its manifest schema. If the RAM
gate stopped the proofs, use `run --prove-only`. The script waits up to `--wait-mem-s` (900 s)
for `MemAvailable ≥ --min-avail-mb` (1200) before each heavy step, then refuses.

## 4. Every program that opens the store

All of these must move to chromadb 1.5.x **in the same window**. A 0.6.3 client left behind
fails loudly on the migrated store (proof f). It is still an outage for that program.

| program | how it opens | interpreter today | when | cutover action |
|---|---|---|---|---|
| zoe-data (`memory_service.py`: `mempalace.palace.get_collection` + a raw `chromadb.PersistentClient` for audit; `zoe_agent.py`; `mcp_server.py` via MemoryService) | chromadb + mempalace | py3.12 venv `~/.zoe/venvs/zoe-data-py312` | always | install 1.5.9 (+ mempalace 3.10.0) into the venv |
| `zoe-nightly-dreaming.py` (own `PersistentClient` for the quality snapshot) | chromadb | py3.12 venv (drop-in `60-py312-venv.conf`) | `zoe-dreaming.timer` ~02:33 | same venv, nothing extra |
| `export_memory_store.py` | **SQLite only** | `/usr/bin/python3` (3.10) | `zoe-memory-export.timer` ~02:41 | none: proof a ran its SQL on the 1.x sqlite |
| `check_memory_tombstones.py` | reads `index_metadata.pickle` + SQLite; `--execute` uses chromadb | `/usr/bin/python3` (3.10), same unit, behind `-` | ~02:41 | **port before trusting it.** On the migrated copy it reports `mempalace_audit added 0 live 0 → ok` and does not list drawers (1.5 writes no drawer pickle yet). Its report is silently wrong on 1.x |
| `~/bin/nightly-training-cycle.sh` (inline `chromadb.PersistentClient('/home/zoe/.mempalace')`, off-repo) | chromadb | `/usr/bin/python3` (3.10) | `zoe-training.timer` ~02:05 | re-point to the venv python, or stop the timer until it is |
| `~/scripts/maintenance/mempalace-nightly-backup.sh` / `zoe-backup-verify.sh` (off-repo) | SQLite only (online backup; `COUNT(*) FROM embeddings`) | `python3` (3.10) | `zoe-backup.timer` ~02:34, verify weekly | none (the `embeddings` table persists in 1.x). The pre-cutover tarball is a 0.6 palace, so label it |
| `check_emotional_thread.py`, `remediate_ownerless_memories.py` (hand-run) | chromadb | `#!/usr/bin/env python3` → 3.10 | manual | run them with the venv python after cutover |
| `mempalace_baseline.py`, `zoe_memory_prompt_packet_measure.py` | via zoe-data modules | venv when run from zoe-data | manual | none |
| `~/bin/zoe-memory-mcp.py` (off-repo) | chromadb | 3.10 | referenced only by retired Hermes config backups | leave dead; do not revive |

**Both interpreters move together.** Prefer re-pointing the 3.10 callers at the venv python over
installing chromadb 1.5.9 into the system 3.10 user-site. That user-site also carries Kokoro's
CUDA `onnxruntime-gpu`, and a pip resolve that pulls CPU `onnxruntime` next to it can break TTS.
If 3.10 must carry 1.5.9, install it with a constraints file that pins the current ORT/numpy,
then import-check Kokoro before restarting anything. That path is unrehearsed.

## 5. Cutover checklist (🧑, one window, outside 01:45–03:15)

Preconditions:
- mempalace **3.10.0** is ≥14 days old (on or after 2026-09-30), or the operator accepts the risk.
- A fresh `run --fresh` rehearsal passes 10/10 on the same day.
- MemAvailable is ≥ 1.2 GB.

1. Stop the writers and every timer that opens the store:
   `systemctl --user stop zoe-training.timer zoe-dreaming.timer zoe-memory-export.timer zoe-backup.timer`,
   then `systemctl --user stop zoe-data`. Confirm no opener is running:
   `pgrep -af "chromadb|zoe-nightly-dreaming|check_memory|nightly-training"` prints nothing.
2. Final copy + rebuild from the **stopped** store:
   `python3 scripts/maintenance/chroma_migrate_rehearsal.py run --fresh --date cutover-$(date +%F)`.
   Nothing writes during the copy, so the counts are final. All 10 proofs must PASS.
3. Snapshot and swap:
   `mv ~/.mempalace ~/.mempalace.pre-b08-$(date +%Y%m%d-%H%M%S)`, then
   `cp -a ~/.zoe/chroma-migration-rehearsal/cutover-<date>/dst ~/.mempalace`.
   Copy the store rather than moving it, so the rehearsal copy stays pristine. Leave
   `export/` and `manifest.json` where they are.
4. Install into the venv:
   `~/.local/bin/uv pip install --python ~/.zoe/venvs/zoe-data-py312/bin/python chromadb==1.5.9 mempalace==3.10.0`.
   Constrain numpy/onnxruntime/tokenizers to their current pins. Update
   `services/zoe-data/requirements-py312.txt` in the same PR: pins, the ~line 82 comment, and the
   `_skip_name_check=True` audit of `get_collection()` callers. Re-point the 3.10 callers (§4).
5. Record the embedder identity. mempalace 3.10 warns on a populated collection with no recorded
   identity (`EmbedderIdentityUnknownWarning`) and resolves it with
   `mempalace palace set-embedder`. Check the exact CLI with `--help` first; this step is
   **unrehearsed**.
6. `systemctl --user start zoe-data`. Poll `/readyz` (`is-active` lies) until `status` is ok **and**
   `memory_capture` says `self-recall ok`. That check runs `memory_recall_probe.run_self_recall_check`
   on the new client.
7. Verify:
   - `/health` recall `ok`
   - `check_emotional_thread.py` under the venv python
   - one real chat turn that recalls a known fact
   - zoe-data RSS before and after (1.5's Rust core replaces `chroma-hnswlib`; expected about neutral, unmeasured)
8. Re-enable the timers and watch the next dreaming and memory-export logs.

## 6. Rollback

Stop zoe-data and the timers. Then:
- `mv ~/.mempalace ~/.mempalace.b08-failed-<ts>`
- `mv ~/.mempalace.pre-b08-<ts> ~/.mempalace`
- reinstall `chromadb==0.6.3 mempalace==3.3.1` in the venv
- start and poll `/readyz`

**Restore the directory. Never just re-pin**: 0.6.3 cannot open the 1.x sysdb (proof f). Writes
made after cutover live only in the 1.x store. Export them first with `export_memory_store.py`,
which reads both formats.

## 7. Known facts and traps

- **The EF call size sets RSS.** chroma's MiniLM tokenizer pads every text to 256 tokens, and
  ORT's arena keeps the peak. Measured on the 333-drawer export (ORT 1.23.2):
  - 706 MB at 32 texts per call
  - 469 MB at 16
  - 331 MB at 8
  - 264 MB at 4
  - 230 MB at 1

  The same code sits in 0.6.3, so this is not a regression. But any bulk re-embed on the box
  must chunk. The first rehearsal attempt was OOM-killed at the 700 MB cap for exactly this reason.
- **The drawer count is moving.** It was 333 → 344 → 365 across three copies in about 20 minutes
  on 2026-09-27. Something writes drawers continuously; the Samantha research found a harness
  writing `test*` ids. That is why the final copy must come from a stopped store.
- **A venv python must not be `realpath`'d.** Resolving `bin/python` escapes the venv into the uv
  base interpreter, which has no chromadb. The script keeps the symlink.
- **`hnsw:` legacy keys vs `configuration`.** The rebuild uses `configuration={"hnsw": …}`, so the
  collection metadata stays exactly what the source had (none). The source's `hnsw:*` values live
  in segment metadata, not collection metadata.
