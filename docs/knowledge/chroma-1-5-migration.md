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

Step 0: merge #1745 (squash). Its deploy will be REFUSED by the voice gate before the reset,
so the live tree is untouched. That is expected; §B clears it.

**A. The transition is ONE fail-closed script. Paste it whole.** It starts with a PREFLIGHT that touches nothing. The run id `cutover-<date>-<HHMMSS>` must pass the tool's own `check-date` (the exact format `run` accepts, dir not taken), and the refused #1745 deploy run must exist and be finished; its id is captured by the merge commit sha. Only then does it take the lock and stop anything. At the end it prints `D=` and `DEPLOY_ID=` for block B. It runs in its own
`bash -euo pipefail`, so `set -e` cannot kill your login shell. Any failure stops it **before**
`systemctl --user start zoe-data`, and the ERR trap prints the exact next step for the stage it
reached:
- Before the swap, nothing changed: restart the old service.
- After the swap, do not start anything: roll back per §6.

So a failed copy/rebuild, swap, venv refresh or ff-only merge can never start the old opener on
the new client/store, or the new opener on the old store (the format guard would refuse that
anyway, loudly).

```bash
bash -euo pipefail <<'CUTOVER'
WT=/home/zoe/.worktrees/b0-8-cutover                   # any checkout of the MERGED main
D=cutover-$(date +%F-%H%M%S); TS=""; STAGE=preflight    # unique per attempt: a retry gets a NEW dir
TIMERS="zoe-training.timer zoe-dreaming.timer zoe-memory-export.timer zoe-backup.timer"
fail() {
  echo "!! B0.8 cutover FAILED at stage=$STAGE (line $1)." >&2
  if [ -n "${MY_HOLDER:-}" ]; then kill "$MY_HOLDER" 2>/dev/null || true; rm -f /tmp/zoe-b08-deploy-lock-holder.pid /tmp/zoe-b08-deploy-lock.acquired; fi   # release ONLY this attempt's holder
  case $STAGE in
    preflight)
      echo "!! Preflight only: NOTHING was stopped or changed." >&2 ;;
    pre-stop|stopped|rebuilt)
      echo "!! Nothing was swapped: old store + old client intact. Restore service:" >&2
      echo "   systemctl --user start zoe-data $TIMERS" >&2 ;;
    started)
      echo "!! zoe-data started but is not ready. Stop it and roll back per runbook §6:" >&2
      echo "   systemctl --user stop zoe-data   # then §6 with TS=$TS" >&2 ;;
    *)
      echo "!! The store WAS swapped (TS=$TS). Do NOT start zoe-data: roll back per runbook §6." >&2 ;;
  esac
}
trap 'fail $LINENO' ERR

# 0. PREFLIGHT, before anything is stopped:
#    (a) the run id passes the tool's own validation (the format `run` accepts, dir not taken);
#    (b) the refused #1745 deploy run exists and has finished. Block B re-runs exactly that run.
python3 $WT/scripts/maintenance/chroma_migrate_rehearsal.py check-date "$D"
MERGE_SHA=$(gh pr view 1745 --json mergeCommit --jq .mergeCommit.oid)
test -n "$MERGE_SHA"
DEPLOY_ID=$(gh run list --workflow deploy.yml --limit 50 --json databaseId,headSha,status \
  --jq "[.[] | select(.headSha==\"$MERGE_SHA\" and .status==\"completed\")][0].databaseId // empty")
test -n "$DEPLOY_ID"                                    # empty = deploy not finished yet: wait, re-paste
#    (c) that run must have been REFUSED before the checkout reset: the live tree must still be
#        on the pre-merge commit (old opener). If the live HEAD already equals the merge commit,
#        the deploy went through — stop here and follow §6 (the order code→store is broken).
LIVE_HEAD=$(git -C /home/zoe/assistant rev-parse HEAD)
test "$LIVE_HEAD" != "$MERGE_SHA"                       # live checkout must NOT be on the merged main yet
test "$(gh run view "$DEPLOY_ID" --json conclusion --jq .conclusion)" = failure   # the refused run
echo "preflight OK: D=$D DEPLOY_ID=$DEPLOY_ID (merge $MERGE_SHA; live tree at ${LIVE_HEAD:0:8})"
exec 9>/tmp/zoe-brain-window.lock; flock -w 7200 9     # no replay window overlaps
# Deploy exclusion across BOTH blocks: deploy.yml takes /tmp/zoe-deploy.lock before it resets the
# live checkout. Blocks A and B are separate shells, so the lock is held by a small background
# holder whose lifetime spans both; block B kills it right before the deploy rerun. Preflight also
# refuses if a deploy is already in progress (one past its checkout step cannot be excluded by the lock).
test "$(gh run list --workflow deploy.yml --limit 3 --json status --jq '[.[]|select(.status!="completed")]|length')" = 0
# An ACTIVE holder from another cutover attempt means that attempt is between its blocks: REFUSE
# (never kill it). A genuinely abandoned holder is released by the explicit recovery below, by hand.
if [ -f /tmp/zoe-b08-deploy-lock-holder.pid ] && kill -0 "$(cat /tmp/zoe-b08-deploy-lock-holder.pid)" 2>/dev/null \
   && grep -q zoe-deploy.lock "/proc/$(cat /tmp/zoe-b08-deploy-lock-holder.pid)/cmdline" 2>/dev/null; then
  echo "!! another cutover attempt still holds the deploy lock (pid $(cat /tmp/zoe-b08-deploy-lock-holder.pid)). Finish or abandon it first:" >&2
  echo "   abandoned for sure?  kill \$(cat /tmp/zoe-b08-deploy-lock-holder.pid); rm -f /tmp/zoe-b08-deploy-lock-holder.pid /tmp/zoe-b08-deploy-lock.acquired" >&2
  false
fi
rm -f /tmp/zoe-b08-deploy-lock-holder.pid /tmp/zoe-b08-deploy-lock.acquired      # stale file from a dead holder only
# The new holder must ACQUIRE (flock -n exits at once if the lock is busy) and prove it with a marker.
( exec -a zoe-b08-deploy-lock-holder bash -c 'flock -n 8 && echo acquired > /tmp/zoe-b08-deploy-lock.acquired && sleep 14400' ) 8>/tmp/zoe-deploy.lock &
MY_HOLDER=$!; echo "$MY_HOLDER" > /tmp/zoe-b08-deploy-lock-holder.pid
sleep 2; test -f /tmp/zoe-b08-deploy-lock.acquired && kill -0 "$MY_HOLDER"   # lock OWNED by this attempt (else a deploy has it — do not proceed)
STAGE=pre-stop

# 1. Stop every writer/opener
systemctl --user stop $TIMERS
systemctl --user stop zoe-data
STAGE=stopped
if fuser ~/.mempalace/chroma.sqlite3; then echo "store still open" >&2; false; fi

# 2. Final copy + rebuild from the STOPPED store (exit 1 unless all 10 proofs PASS)
python3 $WT/scripts/maintenance/chroma_migrate_rehearsal.py run --date $D
STAGE=rebuilt

# 3. Swap: the old dir becomes the rollback, and nothing opens it
TS=$(date +%Y%m%d-%H%M%S)
mv ~/.mempalace ~/.mempalace.pre-b08-$TS
STAGE=swapped
cp -a ~/.zoe/chroma-migration-rehearsal/$D/dst ~/.mempalace
chmod 700 ~/.mempalace

# 4. New client into the live venv: the exact deploy path, additive
ZOE_PY312_VENV=$HOME/.zoe/venvs/zoe-data-py312 bash $WT/scripts/setup/build_py312_venv.sh --refresh
test "$(~/.zoe/venvs/zoe-data-py312/bin/python -c 'import chromadb; print(chromadb.__version__)')" = 1.5.9
STAGE=client-installed

# 5. Live code to the merged main (it must carry the new opener)
cd /home/zoe/assistant
git fetch origin main
git merge --ff-only origin/main
grep -q "def get_drawers_collection" services/zoe-data/memory_service.py
STAGE=code-ff

# 6. Start + readiness (is-active lies; poll, then REQUIRE self-recall ok)
systemctl --user start zoe-data
STAGE=started
for i in $(seq 1 36); do curl -sf localhost:8000/readyz >/dev/null && break; sleep 5; done
curl -sf localhost:8000/readyz | python3 -c 'import json,sys; d=json.load(sys.stdin); mc=d["memory_capture"]; print(d["status"], mc); assert d["status"]=="ok" and "self-recall ok" in mc.get("detail","")'
STAGE=live
echo "B0.8 transition OK: TS=$TS (rollback dir ~/.mempalace.pre-b08-$TS)"
echo "If you abandon the cutover after block A, release the deploy lock: kill \$(cat /tmp/zoe-b08-deploy-lock-holder.pid)"
echo "FOR BLOCK B:  D=$D DEPLOY_ID=$DEPLOY_ID"
CUTOVER
```

**B. Verify, replay, re-deploy, re-arm: also ONE fail-closed script.** Run it only after A
printed `B0.8 transition OK`, and paste the `D=` and `DEPLOY_ID=` A printed; it refuses if either is empty. It re-runs exactly that refused deploy. Its EXIT trap restarts and health-checks `kokoro-tts` whenever the block stopped it, including on Ctrl-C, TERM or HUP. Every step must succeed: the
live snapshot copy, the recall probe, the order-exact parity check against the published
baseline, and the replay. The tombstone report may exit **3** (a count is UNKNOWN until 1.x
persists the drawers' index metadata); the script accepts that with a printed warning. Exit 2
(over the warn threshold) or 1 fails. Only when everything passed does it re-run the refused
deploy and re-arm the timers.

**On failure the store is ALREADY LIVE**: zoe-data keeps running on the new store and client.
The script leaves the timers STOPPED, changes nothing, and prints the §6 pointer. Decide
between a fix-forward and the §6 rollback.

```bash
D=<from block A> DEPLOY_ID=<from block A> bash -euo pipefail <<'VERIFY'
: "${D:?paste D= from block A}" "${DEPLOY_ID:?paste DEPLOY_ID= from block A}"   # refuse if empty
WT=/home/zoe/.worktrees/b0-8-cutover; R=~/.zoe/chroma-migration-rehearsal/$D; STEP=start
TIMERS="zoe-training.timer zoe-dreaming.timer zoe-memory-export.timer zoe-backup.timer"
SCR=$(mktemp -d); KOKORO_STOPPED=0
kokoro_back() { systemctl --user start kokoro-tts; for i in $(seq 1 60); do curl -sf localhost:10201/health | grep -q '"device":"cuda"' && { KOKORO_STOPPED=0; return 0; }; sleep 2; done; return 1; }
cleanup() {                    # runs on EVERY exit: success, failure, Ctrl-C, kill (TERM/HUP)
  rm -rf "$SCR"
  if [ "$KOKORO_STOPPED" = 1 ]; then
    echo "!! restoring kokoro-tts (this block stopped it)" >&2
    kokoro_back || echo "!! kokoro-tts did NOT come back healthy: check it NOW" >&2
  fi
}
trap cleanup EXIT
trap 'exit 130' INT; trap 'exit 143' TERM; trap 'exit 129' HUP
fail() {
  echo "!! B0.8 verification FAILED at step=$STEP (line $1)." >&2
  echo "!! The new store is ALREADY LIVE; zoe-data is still running on it. The timers stay STOPPED." >&2
  echo "!! Nothing was re-deployed. Fix forward, or roll back per runbook §6 (restore ~/.mempalace.pre-b08-<TS>)." >&2
}
trap 'fail $LINENO' ERR

STEP=live-copy
python3 $WT/scripts/maintenance/chroma_migrate_rehearsal.py copy --copy-from ~/.mempalace --copy-to $SCR/store
STEP=probe
DEMO=$(python3 -c "import json;print(json.load(open('$R/manifest.json'))['run']['recall_demo_user'])")
~/.zoe/venvs/zoe-data-py312/bin/python $WT/scripts/maintenance/chroma_migrate_rehearsal.py probe recall \
  --store $SCR/store --demo-user $DEMO --out-file $SCR/live_top.json
STEP=compare-recall        # exit 1 unless the top-10 ORDER is identical for all 20 queries
python3 $WT/scripts/maintenance/chroma_migrate_rehearsal.py compare-recall --baseline $R/recall-parity --new $SCR/live_top.json
STEP=tombstones
rc=0; /usr/bin/python3 $WT/scripts/maintenance/check_memory_tombstones.py || rc=$?
case $rc in
  0) ;;
  3) echo "WARN: tombstone count UNKNOWN for a 1.x segment with no persisted index metadata yet (expected right after the rebuild)" >&2 ;;
  *) echo "tombstone report rc=$rc" >&2; false ;;
esac
STEP=rss
grep -E 'VmRSS|VmSwap' /proc/$(systemctl --user show -p MainPID --value zoe-data)/status   # before: 1028 MB RSS, 0 swap

STEP=replay               # writes the artifact the deploy gate needs; Kokoro stopped for its duration
set -a; . ~/.hermes/.env; set +a
KOKORO_STOPPED=1; systemctl --user stop kokoro-tts      # the EXIT trap restores it on any exit
ZOE_VOICE_REPLAY_STT=remote flock /tmp/zoe-voice-harness.lock nice -n 5 ~/.zoe/venvs/zoe-data-py312/bin/python \
  $WT/scripts/maintenance/voice_regression_probe.py --samples 20 --stt remote \
  --service-dir /home/zoe/assistant/services/zoe-data
STEP=kokoro-health
kokoro_back

STEP=redeploy             # only now: re-run the SPECIFIC refused #1745 deploy (captured in block A)
kill "$(cat /tmp/zoe-b08-deploy-lock-holder.pid 2>/dev/null)" 2>/dev/null || true   # release the deploy lock held since block A
rm -f /tmp/zoe-b08-deploy-lock-holder.pid /tmp/zoe-b08-deploy-lock.acquired
gh run rerun "$DEPLOY_ID"
sleep 20
gh run watch "$DEPLOY_ID" --exit-status              # blocks until the deploy finishes; non-zero = failed
# readiness can be 200 while the memory-capture probe is still warming (it retries ~45 s after
# start): keep polling for memory_capture ok within the warm-up window, not just the first 200.
ok=0; for i in $(seq 1 48); do curl -s -m 5 localhost:8000/readyz | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("ready") and d["memory_capture"]["status"]=="ok" and "self-recall ok" in (d["memory_capture"].get("detail") or "") else 1)' 2>/dev/null && { ok=1; break; }; sleep 5; done
test "$ok" = 1                                          # memory self-recall must be ok after the redeploy
STEP=rearm
systemctl --user start $TIMERS
echo "B0.8 verified; deploy re-run; timers re-armed"
VERIFY
```
Kokoro is the `kokoro-tts` user unit on `:10201` (`KOKORO_SIDECAR_PORT`, checked 2026-09-28).
The health check requires `"device":"cuda"`, not just `status ok`: a CPU fallback means choppy
TTS.

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
