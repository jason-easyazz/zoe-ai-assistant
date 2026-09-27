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

## 4. Every program that opens the store (as of the cutover PR)

The code, the client and the store switch **together**, because the format change is one-way.
Two guards make a mismatch loud instead of destructive; both read the format from SQLite
(`mode=ro`: sysdb migration 00010 exists only in 1.x) before chromadb touches the file.
- `memory_service._check_palace_format` in zoe-data
- `scripts/lib/palace_client.py` for the scripts

A 1.x client on a 0.6 palace would migrate it **in place**, which would destroy the rollback
snapshot. A 0.6.3 client on the 1.x palace dies anyway.

| program | how it opens | interpreter | when | state after the cutover PR |
|---|---|---|---|---|
| zoe-data (`memory_service.get_drawers_collection` + audit; `zoe_agent.migrate_mempalace_legacy_records`; `mcp_server.py`/recall via MemoryService) | **raw chromadb** (no mempalace wrapper), one cached client per resolved dir, one cached MiniLM EF named `"default"`, format guard | py3.12 venv | always | pins `chromadb==1.5.9`, `mempalace==3.10.0` in `requirements-py312.txt` (mempalace is installed but not imported by the runtime) |
| `zoe-nightly-dreaming.py` | `palace_client.open_palace_client` | py3.12 venv (drop-in `60-py312-venv.conf`) | `zoe-dreaming.timer` ~02:33 | guarded |
| `~/bin/nightly-training-cycle.sh` §10.5–10.7 (quality snapshot, dreaming, music digest; off-repo) | chromadb / MemoryService | **moved to the venv 2026-09-27** (`ZOE_PALACE_PY`; backup `~/bin/nightly-training-cycle.sh.pre-b08-20260927`) | `zoe-training.timer` ~02:05 | done (works with either format, since the venv carries the matching client) |
| `export_memory_store.py` | SQLite only | `/usr/bin/python3` (3.10) | `zoe-memory-export.timer` ~02:41 | none needed (proof a ran its SQL on 1.x) |
| `check_memory_tombstones.py` report | pickle + SQLite, no chromadb | `/usr/bin/python3` (3.10) | same unit | **ported**: reads 1.x's dict pickle (it used to report 0/0 "ok"), lists segments with no persisted index metadata yet; `--execute` goes through the guard |
| `check_emotional_thread.py`, `remediate_ownerless_memories.py` (hand-run) | `palace_client` guard | run them with `~/.zoe/venvs/zoe-data-py312/bin/python` | manual | under 3.10 they now refuse with that instruction |
| `mempalace-nightly-backup.sh` / `zoe-backup-verify.sh` (off-repo) | SQLite only | 3.10 | `zoe-backup.timer` ~02:34 | none (the `embeddings` table persists in 1.x) |
| `~/scripts/maintenance/mempalace-wing-migration.py` (off-repo, one-shot 2026-04) | `mempalace.palace.get_collection` | 3.10 | never scheduled | leave; it must not be re-run |
| `~/bin/zoe-memory-mcp.py` (off-repo) | chromadb | 3.10 | retired Hermes config backups only | leave dead |

The system 3.10 user site keeps chromadb 0.6.3. It hosts Kokoro's CUDA `onnxruntime-gpu`, and
nothing on 3.10 may open the palace. Moving zoe-data back to 3.10 (the B0.7 rollback) now
requires the B0.8 rollback as well.

## 5. Cutover sequence (🧑 operator, one window, outside 01:45–03:15)

**Status (2026-09-27 22:20): NOT executed.** The agent's first window step (stop the timers and
zoe-data) was refused by the permission system. The box was left unchanged: old store, old
client, everything running. Everything else is prepared: the cutover PR, the moved 3.10 opener,
and the rehearsal (10/10).

Why the order matters. The live checkout's code, the venv pins and the store must all flip while
zoe-data is down:
- **New code + old store** → the guard refuses to open (loud; memory is down, the data is safe).
- **Old code + new pins** → zoe-data runs mempalace 3.10's untested wrapper.

The deploy gate also refuses the merged PR until a fresh replay artifact exists, because
`requirements-py312.txt` is on the voice path. Refused means blocked *before* the reset, so the
live tree stays at `prev`. The replay needs the new stack running, so the order is: merge, then
the window, then the replay, then re-run the deploy.

```bash
# 0. Merge #1732 then #1745 (squash). The deploy for #1745 will be REFUSED by the voice gate:
#    live tree untouched. That is expected.
exec 9>/tmp/zoe-brain-window.lock; flock -w 7200 9          # no replay/deploy window overlaps
WT=/home/zoe/.worktrees/b0-8-cutover                        # or any checkout of the merged main
D=cutover-$(date +%F)

# 1. Stop every writer/opener
systemctl --user stop zoe-training.timer zoe-dreaming.timer zoe-memory-export.timer zoe-backup.timer
systemctl --user stop zoe-data
fuser ~/.mempalace/chroma.sqlite3                          # must print nothing

# 2. Final copy + rebuild from the STOPPED store. 10/10 must PASS.
#    If not: systemctl --user start zoe-data, re-enable the timers, stop here.
python3 $WT/scripts/maintenance/chroma_migrate_rehearsal.py run --date $D

# 3. Swap (the old dir becomes the rollback; nothing opens it)
TS=$(date +%Y%m%d-%H%M%S)
mv ~/.mempalace ~/.mempalace.pre-b08-$TS
cp -a ~/.zoe/chroma-migration-rehearsal/$D/dst ~/.mempalace && chmod 700 ~/.mempalace

# 4. New client into the live venv: the exact deploy path, additive
ZOE_PY312_VENV=$HOME/.zoe/venvs/zoe-data-py312 bash $WT/scripts/setup/build_py312_venv.sh --refresh
~/.zoe/venvs/zoe-data-py312/bin/python -c 'import chromadb; print(chromadb.__version__)'   # 1.5.9

# 5. Live code to merged main
cd /home/zoe/assistant && git fetch origin main && git merge --ff-only origin/main

# 6. Start + readiness (is-active lies; poll)
systemctl --user start zoe-data
for i in $(seq 1 36); do curl -sf localhost:8000/readyz >/dev/null && break; sleep 5; done
curl -s localhost:8000/readyz | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["memory_capture"])'
#    must be: ok {'status': 'ok', 'detail': '... self-recall ok'}

# 7. Verify
~/.zoe/venvs/zoe-data-py312/bin/python $WT/scripts/maintenance/chroma_migrate_rehearsal.py probe recall \
  --store <scratch copy of ~/.mempalace> --demo-user <recall_demo_user from $D/manifest.json> --out-file /tmp/b08_live_top.json
python3 $WT/scripts/maintenance/chroma_migrate_rehearsal.py compare-recall \
  --old ~/.zoe/chroma-migration-rehearsal/$D/recall-parity/old_top.json --new /tmp/b08_live_top.json   # PASS = identical order
/usr/bin/python3 $WT/scripts/maintenance/check_memory_tombstones.py                                    # 1.x-aware report
grep -E 'VmRSS|VmSwap' /proc/$(systemctl --user show -p MainPID --value zoe-data)/status              # before: 1028 MB RSS, 0 swap

# 8. Replay gate (writes the artifact the deploy gate needs)
#    Stop Kokoro for its duration, then restart it and health-check it.
set -a; . ~/.hermes/.env; set +a
ZOE_VOICE_REPLAY_STT=remote flock /tmp/zoe-voice-harness.lock nice -n 5 ~/.zoe/venvs/zoe-data-py312/bin/python \
  $WT/scripts/maintenance/voice_regression_probe.py --samples 20 --stt remote --service-dir /home/zoe/assistant/services/zoe-data
gh run rerun <refused deploy run id>          # now passes; no-op reset + restart

# 9. Re-arm the timers
systemctl --user start zoe-training.timer zoe-dreaming.timer zoe-memory-export.timer zoe-backup.timer
```

## 6. Rollback

Stop zoe-data and the timers. Then:
- `mv ~/.mempalace ~/.mempalace.b08-failed-<ts>`
- `mv ~/.mempalace.pre-b08-<ts> ~/.mempalace`
- reinstall the old pair: `~/.local/bin/uv pip install --offline --python ~/.zoe/venvs/zoe-data-py312/bin/python chromadb==0.6.3 mempalace==3.3.1`
- revert the cutover PR (the pins and the opener move back with the store). The format guard
  will otherwise refuse the 0.6 store under 1.5.9, and it will refuse the 1.x store under 0.6.3.
- start and poll `/readyz`

**Restore the directory. Never just re-pin**: 0.6.3 cannot open the 1.x sysdb (proof f). Writes
made after cutover live only in the 1.x store. Export them first with `export_memory_store.py`,
which reads both formats.

## 7. Known facts and traps

- **chromadb 1.x's `DefaultEmbeddingFunction` rebuilds the ONNX session on EVERY call.**
  Measured per query on the migrated copy:

  | client | per query |
  |---|---|
  | 1.5.9 with no EF passed | 0.42–0.89 s |
  | 1.5.9 with one cached `ONNXMiniLM_L6_V2` named `"default"` | 0.18–0.27 s |
  | 0.6.3 | 0.11–0.22 s |

  zoe-data therefore opens the drawers with its own cached EF. It must be named `"default"`:
  any other name raises "Embedding function conflict" against the persisted identity.
- **`mempalace` 3.3.1 must not run against a 1.x palace.** Its backend runs `_fix_blob_seq_ids`
  (a raw SQLite UPDATE) on every new client. The rebuilt store has no BLOB `seq_id`s, so it
  would no-op, but it is unsupported, so the runtime no longer imports mempalace at all.

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
