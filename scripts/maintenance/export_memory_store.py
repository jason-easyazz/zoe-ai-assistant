#!/usr/bin/env python3
"""Nightly plain-JSON export of the vector memory store.

WHY THIS EXISTS (2026-07-31 incident): a torn HNSW persist crash-looped zoe-data
and the recovery was a delete-collection + re-embed rebuild. The documents
survived only because Chroma keeps them in SQLite — but nothing had ever
verified that, and there was no independent copy to reconcile against. This
script makes that copy, nightly.

Two design rules follow from the incident and are load-bearing:

1. **Read SQLite directly, never through chromadb.** The moment you need this
   backup most is the moment the native index is damaged and `get_collection()`
   segfaults or hangs. Opening the DB read-only (`mode=ro`) sidesteps the index
   entirely, needs no embedder, and cannot write.
2. **Never touch live data.** Read-only URI, no VACUUM, no repair, no deletes.
   Retention prunes only this script's own dated exports, matched by a strict
   filename pattern.

Output: <out-dir>/memory-export-YYYYmmdd-HHMMSS.json(.gz)
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

DEFAULT_DB = "~/.mempalace/chroma.sqlite3"
DEFAULT_OUT = "~/.zoe/memory-exports"
# Strict: only ever prune files this script itself wrote.
EXPORT_RE = re.compile(r"^memory-export-\d{8}-\d{6}\.json(\.gz)?$")
DOC_KEY = "chroma:document"


def _connect_ro(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise SystemExit(f"memory export: no such database: {db_path}")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _row_metadata(conn: sqlite3.Connection, rowid: int) -> tuple[str | None, dict]:
    """Return (document, metadata) for one embedding_metadata row id."""
    doc = None
    meta: dict = {}
    for key, s, i, f, b in conn.execute(
        "SELECT key, string_value, int_value, float_value, bool_value "
        "FROM embedding_metadata WHERE id=?",
        (rowid,),
    ):
        if key == DOC_KEY:
            doc = s
            continue
        # Exactly one typed column is populated per row.
        value = s if s is not None else i if i is not None else f if f is not None else b
        if b is not None and s is None and i is None and f is None:
            value = bool(b)
        meta[key] = value
    return doc, meta


def _apply_forget_ledger(collections: dict) -> tuple[str, int]:
    """Redact what the user asked Zoe to forget from the export BEFORE it is written ("forgotten means forever": a plaintext
    backup must not keep the name). Uses the forget ledger (hashes only; needs the service environment and the database, which the
    unit loads). Never fails the backup: when the ledger cannot be loaded the export is written whole, says so in its payload and in
    the log, and ``redact_backups.py`` redacts it later. Returns ``(status, spans replaced)``."""
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(repo, "services", "zoe-data"))
    try:
        import asyncio

        import forget_redact
        from db_pool import close_pool, init_pool

        async def load():
            await init_pool()
            try:
                return await forget_redact.load_redactor()
            finally:
                await close_pool()

        red = asyncio.run(load())
        n = forget_redact.redact_export({"collections": collections}, red)
        return "applied", n
    except Exception as exc:  # noqa: BLE001 - the backup matters more than the redaction; the maintenance command closes the gap
        print(f"memory export: FORGET LEDGER NOT APPLIED ({type(exc).__name__}) - run scripts/maintenance/redact_backups.py",
              file=sys.stderr)
        return f"skipped: {type(exc).__name__}", 0


def export(db_path: str, out_dir: str, *, compress: bool, keep: int) -> str:
    conn = _connect_ro(db_path)
    # SNAPSHOT CONSISTENCY (review: Greptile). The scan issues one query per
    # collection plus one per row, and zoe-data writes memory continuously — so
    # without a spanning read transaction the queries observe different database
    # states and the "recovery" export can contain a row set that never existed
    # at any instant. `BEGIN` on a read-only connection starts a deferred read
    # transaction; under WAL (Chroma's default) that pins one consistent
    # snapshot for its lifetime WITHOUT blocking writers. Isolation is set to
    # None so sqlite3 stops managing transactions implicitly and our explicit
    # BEGIN/COMMIT is honoured.
    conn.isolation_level = None
    conn.execute("BEGIN")
    collections: dict[str, list] = {}
    total = 0
    for cid, cname in conn.execute("SELECT id, name FROM collections ORDER BY name"):
        records = []
        for rowid, emb_id in conn.execute(
            "SELECT e.id, e.embedding_id FROM embeddings e "
            "JOIN segments s ON s.id = e.segment_id "
            "WHERE s.collection = ? AND s.scope = 'METADATA' "
            "ORDER BY e.seq_id",
            (cid,),
        ):
            doc, meta = _row_metadata(conn, rowid)
            records.append({"id": emb_id, "document": doc, "metadata": meta})
        collections[cname] = records
        total += len(records)
    conn.execute("COMMIT")
    conn.close()

    ledger_status, ledger_spans = _apply_forget_ledger(collections)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    # PERMISSIONS (review: Greptile). This file is the household's complete
    # personal-memory payload in plaintext. `os.makedirs`/`open` take their mode
    # from the inherited umask, so a permissive umask would publish it to every
    # local account with parent-directory access. Set the modes explicitly here
    # rather than relying on a service-level UMask, so a hand-run export is as
    # private as the timer-run one. (The unit sets UMask=0077 as well — belt and
    # braces, since neither alone covers both invocation paths.)
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    try:
        os.chmod(out_dir, 0o700)  # exist_ok=True skips mode on an existing dir
    except OSError:
        pass
    name = f"memory-export-{stamp}.json" + (".gz" if compress else "")
    path = os.path.join(out_dir, name)
    payload = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_db": db_path,
        "total_records": total,
        "collection_counts": {k: len(v) for k, v in collections.items()},
        "forget_ledger": ledger_status,
        "forget_spans_redacted": ledger_spans,
        "collections": collections,
    }
    blob = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
    # Write to a temp sibling then rename, so a crash mid-write can never leave a
    # truncated file that looks like a valid export.
    tmp = path + ".partial"
    # 0600 before any bytes land: create the fd with the mode already set rather
    # than chmod-ing after writing, which would leave a readable window.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as raw:  # closes fd
        if compress:
            with gzip.GzipFile(fileobj=raw, mode="wb") as fh:
                fh.write(blob)
        else:
            raw.write(blob)
    os.replace(tmp, path)  # rename preserves the 0600 mode

    print(f"memory export: {total} records across {len(collections)} collections -> {path}")
    print(f"  forget ledger: {ledger_status}, {ledger_spans} span(s) redacted")
    for cname, recs in sorted(collections.items()):
        if recs:
            print(f"  {cname:32s} {len(recs)}")
    _prune(out_dir, keep)
    return path


def _prune(out_dir: str, keep: int) -> None:
    if keep <= 0:
        return
    files = sorted(f for f in os.listdir(out_dir) if EXPORT_RE.match(f))
    for stale in files[:-keep]:
        try:
            os.remove(os.path.join(out_dir, stale))
            print(f"  pruned old export: {stale}")
        except OSError as exc:  # never fail the export over cleanup
            print(f"  could not prune {stale}: {exc}", file=sys.stderr)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB, help=f"chroma sqlite path (default {DEFAULT_DB})")
    ap.add_argument("--out-dir", default=DEFAULT_OUT, help=f"export directory (default {DEFAULT_OUT})")
    ap.add_argument("--keep", type=int, default=14, help="dated exports to retain (0 = keep all)")
    ap.add_argument("--no-compress", action="store_true", help="write plain .json instead of .json.gz")
    args = ap.parse_args(argv)
    try:
        export(
            os.path.expanduser(args.db),
            os.path.expanduser(args.out_dir),
            compress=not args.no_compress,
            keep=args.keep,
        )
    except SystemExit:
        raise
    except Exception as exc:
        print(f"memory export FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
