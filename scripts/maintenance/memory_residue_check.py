#!/usr/bin/env python3
"""Is forgotten text physically gone from the MemPalace files?  (owner rule: "forgotten means forever")

Copies ``chroma.sqlite3`` (+ sidecars) and every HNSW segment directory of the palace into a scratch
directory, byte-scans the COPY for each ``--token``, deletes the copy, and prints COUNTS ONLY - which file
and, for the SQLite file, which table / index / free page owns each hit. It never prints a document, never
prints bytes around a hit, and never opens the live database for writing (a plain file copy).

    scripts/maintenance/memory_residue_check.py --token canary-7f3a9c-Quillfeather
    scripts/maintenance/memory_residue_check.py --token A --token B --json

Exit status: 0 = no token found anywhere, 1 = residue found, 2 = usage / unreadable palace.

Use a SYNTHETIC canary (a unique string you ingested under a demo user and then forgot). Passing a real
person's name works, but then the counts are the only output you should share.

Orphan HNSW segment directories (left behind when a collection is deleted - a compaction rebuild does that)
are listed by name and size because they carry the pre-rebuild index; ``--scrub-orphans`` deletes them.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "services" / "zoe-data"))

import memory_residue  # noqa: E402

DEFAULT_PALACE = os.path.expanduser("~/.mempalace")
DEFAULT_SCRATCH = os.path.expanduser("~/.zoe/bakeoff-2026-10/residue")


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", action="append", required=True, help="string to look for (repeatable)")
    ap.add_argument("--palace", default=DEFAULT_PALACE, help=f"palace directory (default {DEFAULT_PALACE})")
    ap.add_argument("--scratch", default=DEFAULT_SCRATCH, help="where the scanned copy is made (deleted afterwards)")
    ap.add_argument("--no-copy", action="store_true", help="scan the palace in place (only for a throwaway directory)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--also-scan", action="append", default=[], metavar="PATH",
                    help="ALSO count the tokens in these files / directories, read-only and in place (e.g. "
                         "~/.zoe/palace-backups: compaction tars and JSON exports keep pre-forget bytes)")
    ap.add_argument("--scrub-orphans", action="store_true",
                    help="delete HNSW segment directories no segments row owns (safe under the running service)")
    args = ap.parse_args(argv)

    palace = Path(os.path.expanduser(args.palace))
    if not (palace / "chroma.sqlite3").is_file():
        print(f"no chroma.sqlite3 under {palace}", file=sys.stderr)
        return 2
    try:
        report = memory_residue.scan_palace(palace, args.token, scratch=args.scratch, copy=not args.no_copy)
    except (OSError, ValueError) as exc:
        print(f"scan failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    orphans = [{"segment": d.name, "bytes": _dir_size(d)} for d in memory_residue.orphan_segment_dirs(palace)]
    removed: list[str] = []
    if args.scrub_orphans:
        removed = memory_residue.remove_orphan_segments(palace)
    extra: dict[str, dict[str, int]] = {}
    for root in args.also_scan:
        rp = Path(os.path.expanduser(root))
        files = [rp] if rp.is_file() else sorted(f for f in rp.rglob("*") if f.is_file()) if rp.is_dir() else []
        for f in files:
            counts, _ = memory_residue._count_in_file(f, [t.encode("utf-8") for t in args.token])
            n = sum(counts.values())
            if n:
                extra[str(f)] = {"byte_hits": n}
    if extra:
        report["clean"] = False
    out = {"palace": str(palace), "clean": report["clean"], "seconds": report["seconds"], "also_scanned_hits": extra,
           "tokens": {f"token#{i + 1}(len={len(t)})": report["tokens"][t] for i, t in enumerate(args.token)},
           "orphan_segment_dirs": orphans, "orphans_removed": removed}
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(f"palace {palace}  scanned a copy in {report['seconds']}s  -> {'CLEAN' if report['clean'] else 'RESIDUE FOUND'}")
        for label, r in out["tokens"].items():
            print(f"  {label}: {r['total']} byte-hit(s)")
            for f, n in sorted(r["files"].items()):
                print(f"      file {f}: {n}")
            for owner, n in sorted(r["sqlite_pages"].items()):
                print(f"      chroma.sqlite3 page owner {owner}: {n}")
        for f, r in sorted(extra.items()):
            print(f"  also-scan {f}: {r['byte_hits']} byte-hit(s)")
        if orphans:
            print(f"  orphan HNSW segment dirs (no segments row): {len(orphans)} "
                  f"({sum(o['bytes'] for o in orphans)} bytes){'  REMOVED' if removed else ''}")
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
