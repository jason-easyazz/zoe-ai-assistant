#!/usr/bin/env python3
"""Compact the event-sourced engineering pipeline state store.

`~/.zoe/engineering_pipeline_runs.jsonl` is a STATE STORE, not an append-history
log. Every `pipeline_store.save_state()` re-appends a FULL state snapshot, and
the snapshot grows as evidence accumulates, so a single task_ref that goes round
the phase loop a few dozen times can write hundreds of ~200 KB records. As of
2026-07-16 the live store was 1592 MB / 8421 records for 129 task_refs, while the
latest snapshot per task_ref totalled 1.28 MB — 99.9% of the file is superseded.

Compaction, NOT rotation
------------------------
A sibling PR added size-based ROTATION for `router_head_shadow.jsonl`. That is
the right tool there and the WRONG tool here, and the distinction is worth being
explicit about:

  * `router_head_shadow.jsonl` is genuine append-history — every line is an
    independent observation and the reports aggregate ACROSS lines. Dropping old
    lines loses real information, so rotation (age out whole files) is correct.
  * this store is event-sourced state — for pipeline correctness only the LAST
    record per task_ref is ever read (see "Readers" below). Rotating it would
    delete live state for any task_ref whose latest snapshot happened to sit in
    the rotated-out segment, silently resurrecting a stale pipeline or losing the
    run entirely. Compaction instead drops only records that are already
    superseded by a later record for the same key — it is information-preserving
    with respect to every reader.

Readers (verified 2026-07-16 — re-verify before extending this tool)
-------------------------------------------------------------------
  1. `pipeline_store.load_latest_state()` / `save_state()` — both funnel through
     `_latest_state_from_lines()`, which keeps the LAST record whose `task_ref`
     matches and which carries a `state` key. Latest-per-ref is sufficient.
  2. `engineering_harness_loop.parse_pipeline_findings()` — reads the LAST
     `DEFAULT_PIPELINE_TAIL` (200) lines as an EVENT STREAM and counts recurring
     (task_ref, phase) gate blocks. This one genuinely consumes history: a
     latest-per-ref-only compaction would collapse every recurrence count to 1
     and quietly degrade harness triage.

So the keep set is the UNION of:
  * the latest `state`-bearing record per task_ref  (reader 1 — correctness), and
  * the last `--keep-tail` records verbatim         (reader 2 — diagnostics).

On the live store that union is ~15 MB (99.1% reclaimed) versus ~1.3 MB for
latest-only (99.9%) — 13.7 MB is cheap insurance for not breaking a reader.

Concurrency + crash safety
--------------------------
Writers (`save_state`) take `fcntl.LOCK_EX` **on the store's own inode**, so this
tool must too, and must PRESERVE that inode.

  * Temp-file + atomic `os.rename()` is therefore REJECTED here, despite being
    the usual crash-safe idiom. flock is a property of the inode, not the path: a
    writer that has already `open()`ed the store and is blocked on flock would,
    after we renamed a new file over the path, acquire the lock on the ORPHANED
    old inode and append its record there — a silently lost state write, and the
    lock would no longer exclude anything. Renaming is only safe if no writer can
    hold an fd on the old inode, which we cannot guarantee.
  * Instead we hold LOCK_EX on the original fd across the WHOLE operation and
    rewrite in place (`seek(0)` + write + `truncate()`). Writers block on the
    lock for the duration and then append to the compacted file correctly. That
    is exactly the mutual exclusion `save_state` already assumes.
  * In-place rewrite is not atomic, so crash safety comes from ordering: the
    compacted bytes are staged in a temp file and fsync'd, AND a full backup of
    the original is written and fsync'd, BOTH before a single byte of the
    original is touched. A crash mid-rewrite leaves the backup complete on disk
    (the tool prints its path before mutating); restore with a plain `cp`.

Everything streams: the file is never read into memory (a resumed harness pulling
1.5 GB into RAM on a memory-tight Jetson is the bug we are fixing, not repeating).
Peak memory is one record plus ~(task_refs + keep_tail) integer offsets.

Kept records are copied as VERBATIM BYTE RANGES — never re-serialized — so a
record that survives compaction is byte-identical, and record ORDER is preserved,
which is what makes `_latest_state_from_lines` (last-match-wins) still return the
same state afterwards.

Usage (run on the zoe-data host):
    python3 scripts/maintenance/compact_pipeline_state.py             # dry-run
    python3 scripts/maintenance/compact_pipeline_state.py --execute   # apply
    python3 scripts/maintenance/compact_pipeline_state.py --execute --yes
    python3 scripts/maintenance/compact_pipeline_state.py --path /tmp/store.jsonl

The harness (`hermes-agent`) should be inactive when this runs. It is safe if it
is not — writers just block — but a long lock hold on a 1.5 GB file will stall a
poll cycle, so prefer a dormant window.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO

# Mirrors engineering_harness_loop.DEFAULT_PIPELINE_TAIL. Deliberately duplicated
# rather than imported: scripts/ is not a package (see scripts/AGENTS.md), and the
# import would drag that module's argparse/urllib surface into this tool. The two
# constants are pinned equal by tests/unit/test_compact_pipeline_state.py so they
# cannot silently drift.
DEFAULT_KEEP_TAIL = 200

# Chunk size for the streaming copy passes.
_COPY_CHUNK = 1 << 20


def store_path(override: str = "") -> Path:
    """Resolve the store path with the same ladder pipeline_store.store_path uses."""
    if override:
        return Path(override).expanduser()
    env = os.environ.get("ZOE_PIPELINE_STORE_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(os.path.expanduser("~/.zoe/engineering_pipeline_runs.jsonl"))


class ScanResult:
    """Byte-offset index of a single streaming pass over the store."""

    def __init__(self) -> None:
        self.records = 0
        self.bad_json = 0
        self.total_bytes = 0
        # task_ref -> (offset, length) of its LATEST state-bearing record
        self.latest_by_ref: dict[str, tuple[int, int]] = {}
        # (offset, length) of every record, most recent last
        self.all_spans: list[tuple[int, int]] = []

    def keep_spans(self, keep_tail: int) -> list[tuple[int, int]]:
        """Union of latest-per-ref and the last `keep_tail` records, in file order.

        Sorting by offset preserves the original record order, which last-match-wins
        readers depend on. Offsets are unique, so the dedupe is exact.
        """
        keep: dict[int, int] = {off: ln for off, ln in self.latest_by_ref.values()}
        if keep_tail > 0:
            for off, ln in self.all_spans[-keep_tail:]:
                keep[off] = ln
        return [(off, keep[off]) for off in sorted(keep)]


def scan(handle: BinaryIO) -> ScanResult:
    """Stream the store once, indexing record byte spans. Never buffers the file."""
    result = ScanResult()
    handle.seek(0)
    offset = 0
    for raw in handle:
        length = len(raw)
        offset_start = offset
        offset += length
        result.records += 1
        result.total_bytes += length
        result.all_spans.append((offset_start, length))
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Never drop a record we cannot parse: it stays via the tail window if
            # recent, and a corrupt line is evidence, not garbage. Counted + reported.
            result.bad_json += 1
            continue
        if not isinstance(payload, dict):
            continue
        task_ref = payload.get("task_ref")
        if isinstance(task_ref, str) and "state" in payload:
            result.latest_by_ref[task_ref] = (offset_start, length)
    return result


def _copy_span(src: BinaryIO, dst: BinaryIO, offset: int, length: int) -> None:
    src.seek(offset)
    remaining = length
    while remaining > 0:
        chunk = src.read(min(_COPY_CHUNK, remaining))
        if not chunk:
            raise IOError(f"short read copying span at offset {offset} (len {length})")
        dst.write(chunk)
        remaining -= len(chunk)


def _fsync(handle: BinaryIO) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _verify_compacted(tmp_path: Path, expected: dict[str, tuple[int, int]], src: BinaryIO) -> None:
    """Re-scan the staged file and assert latest-per-ref is byte-identical.

    Byte preservation is true by construction (we copy spans verbatim), so this is
    a belt-and-braces self-check before we touch the original — it is cheap on the
    compacted file and turns any future indexing bug into a refusal rather than a
    silently mangled store.
    """
    with tmp_path.open("rb") as tmp:
        got = scan(tmp)
        if set(got.latest_by_ref) != set(expected):
            missing = sorted(set(expected) - set(got.latest_by_ref))
            added = sorted(set(got.latest_by_ref) - set(expected))
            raise AssertionError(
                f"compaction would change the task_ref set (missing={missing[:5]}, "
                f"unexpected={added[:5]}) — refusing to write"
            )
        for ref, (off, length) in got.latest_by_ref.items():
            src_off, src_len = expected[ref]
            if length != src_len:
                raise AssertionError(f"record length changed for {ref} — refusing to write")
            tmp.seek(off)
            new_bytes = tmp.read(length)
            src.seek(src_off)
            old_bytes = src.read(src_len)
            if new_bytes != old_bytes:
                raise AssertionError(f"record bytes changed for {ref} — refusing to write")


def compact(
    path: Path,
    *,
    execute: bool,
    keep_tail: int = DEFAULT_KEEP_TAIL,
    backup_dir: Path | None = None,
    log: Any = print,
) -> dict[str, Any]:
    """Compact the store in place under LOCK_EX. Dry-run unless `execute`.

    Returns a report dict. Raises nothing on an already-compacted file — it is a
    no-op (no backup, no rewrite) so the tool is safe to run on a timer.
    """
    if not path.is_file():
        log(f"store not found: {path} — nothing to do.")
        return {"ok": True, "found": False, "path": str(path)}

    with path.open("rb+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            result = scan(handle)
            keep = result.keep_spans(keep_tail)
            kept_bytes = sum(length for _, length in keep)
            reclaim = result.total_bytes - kept_bytes

            log(f"store            : {path}")
            log(f"records          : {result.records}")
            log(f"task_refs        : {len(result.latest_by_ref)}")
            if result.bad_json:
                log(f"unparseable lines: {result.bad_json} (kept if within the tail window)")
            log(f"size             : {result.total_bytes / 1e6:.1f} MB")
            log(f"keep (latest/ref): {len(result.latest_by_ref)} records")
            log(f"keep (tail={keep_tail:<4}): {min(keep_tail, result.records)} records")
            log(f"keep (union)     : {len(keep)} records / {kept_bytes / 1e6:.3f} MB")
            pct = (reclaim / result.total_bytes * 100) if result.total_bytes else 0.0
            log(f"reclaim          : {reclaim / 1e6:.1f} MB ({pct:.2f}%)")

            report: dict[str, Any] = {
                "ok": True,
                "found": True,
                "path": str(path),
                "records": result.records,
                "task_refs": len(result.latest_by_ref),
                "bad_json": result.bad_json,
                "bytes_before": result.total_bytes,
                "kept_records": len(keep),
                "bytes_after": kept_bytes,
                "reclaimed_bytes": reclaim,
                "executed": False,
                "backup": None,
            }

            if len(keep) == result.records:
                log("\nAlready compacted — every record is live. Nothing to do.")
                return report

            if not execute:
                log("\nDRY-RUN — nothing changed. Re-run with --execute to apply.")
                return report

            # --- staging: compacted bytes to a temp file, fsync'd, before any mutation
            tmp_path = path.with_name(path.name + f".compact-{os.getpid()}.tmp")
            try:
                with tmp_path.open("wb") as tmp:
                    for offset, length in keep:
                        _copy_span(handle, tmp, offset, length)
                    _fsync(tmp)

                _verify_compacted(tmp_path, result.latest_by_ref, handle)

                # --- backup: complete + fsync'd copy of the ORIGINAL before we touch it
                stamp = time.strftime("%Y%m%dT%H%M%S")
                target_dir = backup_dir or path.parent
                target_dir.mkdir(parents=True, exist_ok=True)
                backup_path = target_dir / f"{path.name}.backup-{stamp}"
                log(f"\nbackup           : {backup_path}")
                with path.open("rb") as src, backup_path.open("wb") as dst:
                    # NOTE: reads via a SECOND fd on the same inode. Safe — we hold the
                    # only LOCK_EX and no writer can be mid-append.
                    shutil.copyfileobj(src, dst, _COPY_CHUNK)
                    _fsync(dst)
                _fsync_dir(target_dir)
                report["backup"] = str(backup_path)

                # --- rewrite in place: inode preserved, so writers' flock still excludes
                with tmp_path.open("rb") as tmp:
                    handle.seek(0)
                    shutil.copyfileobj(tmp, handle, _COPY_CHUNK)
                handle.truncate(kept_bytes)
                _fsync(handle)
            finally:
                tmp_path.unlink(missing_ok=True)

            report["executed"] = True
            log(
                f"\nAPPLIED — {result.records} -> {len(keep)} records, "
                f"{result.total_bytes / 1e6:.1f} MB -> {kept_bytes / 1e6:.3f} MB "
                f"({pct:.2f}% reclaimed)."
            )
            log(f"Backup retained at {report['backup']} — delete it once you are satisfied.")
            return report
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Compact the event-sourced engineering pipeline state store (dry-run by default).",
    )
    ap.add_argument("--execute", action="store_true", help="apply the compaction (default is dry-run)")
    ap.add_argument("--yes", action="store_true", help="skip the interactive confirmation (for --execute)")
    ap.add_argument("--path", default="", help="store path override (default: $ZOE_PIPELINE_STORE_PATH or ~/.zoe/...)")
    ap.add_argument(
        "--keep-tail",
        type=int,
        default=DEFAULT_KEEP_TAIL,
        help=(
            "keep the last N records verbatim in addition to the latest per task_ref, "
            f"preserving engineering_harness_loop's tail-read diagnostics (default: {DEFAULT_KEEP_TAIL}; "
            "0 keeps latest-per-ref only and WILL degrade that reader)"
        ),
    )
    ap.add_argument("--backup-dir", default="", help="directory for the backup copy (default: alongside the store)")
    args = ap.parse_args(argv)

    if args.keep_tail < 0:
        print("--keep-tail must be >= 0", file=sys.stderr)
        return 2

    path = store_path(args.path)
    backup_dir = Path(args.backup_dir).expanduser() if args.backup_dir else None

    if args.execute and not args.yes:
        report = compact(path, execute=False, keep_tail=args.keep_tail, backup_dir=backup_dir)
        if not report.get("found") or report.get("kept_records") == report.get("records"):
            return 0
        print(f"\nAbout to rewrite IN PLACE: {path}")
        if input("Type 'compact' to proceed: ").strip() != "compact":
            print("Aborted — nothing changed.")
            return 1

    report = compact(path, execute=args.execute, keep_tail=args.keep_tail, backup_dir=backup_dir)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
