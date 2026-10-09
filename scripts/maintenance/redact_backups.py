#!/usr/bin/env python3
"""Redact forgotten text from the plaintext copies on disk. DRY RUN by default; prints COUNTS only, never a span or a name.

"Forgotten means forever": the forget ledger (hashes, no names) knows which spans of a text are forgotten. This applies it to
  * the palace JSON exports  ~/.zoe/memory-exports/*.json(.gz)  (nightly)  and  ~/.zoe/palace-backups/*.json  (every compaction),
  * optionally the brain's durable conversation store (``--flue-db``: the Flue sidecar's SQLite; the sidecar must be STOPPED).
Each file is rewritten atomically (0600) only when a span was found and the result still verifies (record counts and ids unchanged);
a file that would stop verifying is left as it is and reported. Nothing else in a file changes.

Tarballs are NOT rewritten: ``~/.zoe/palace-backups/mempalace-pre-compact-*.tar`` are one-shot rollback copies of the palace taken
before a compaction (``--delete-pre-compact-tars`` removes them; keep the newest if a compaction is about to run); the nightly
``~/.zoe-backups/mempalace/*.tar.gz`` and Postgres dumps rotate out on their own schedule (7 / see the backup script).

Run with the service environment (the database and ``ZOE_FORGET_LEDGER_SALT``) loaded; this script never prints it.
    python3 scripts/maintenance/redact_backups.py                       # dry run: what would change
    python3 scripts/maintenance/redact_backups.py --apply
    python3 scripts/maintenance/redact_backups.py --apply --delete-pre-compact-tars
    python3 scripts/maintenance/redact_backups.py --flue-db labs/flue-zoe-brain-2x/data/zoe-brain.db --apply --i-stopped-the-brain
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import gzip
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))

DEFAULT_DIRS = ("~/.zoe/memory-exports", "~/.zoe/palace-backups")
TAR_GLOB = "mempalace-pre-compact-*.tar"
#: the Flue conversation store tables whose ``data`` column is one JSON document (a spilled, chunked value is not and is counted, not read)
FLUE_TABLES = (("flue_conversation_stream_batches", "data", ("path", "seq")), ("flue_conversation_fold_checkpoints", "data", ("path",)))


def _load(path: Path):
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def _write(path: Path, payload) -> None:
    blob = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
    tmp = path.with_name(path.name + ".partial")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as raw:
        if path.suffix == ".gz":
            with gzip.GzipFile(fileobj=raw, mode="wb") as fh:
                fh.write(blob)
        else:
            raw.write(blob)
    os.replace(tmp, path)


def redact_file(path: Path, red, *, apply: bool) -> dict:
    """One export file: ``{"file", "spans", "changed", "verify"}``. Counts only."""
    import forget_redact

    out = {"file": path.name, "spans": 0, "changed": False, "verify": "ok"}
    try:
        payload = _load(path)
    except Exception as exc:  # noqa: BLE001
        return {**out, "verify": f"unreadable: {type(exc).__name__}"}
    if not isinstance(payload, dict):
        return {**out, "verify": "not an export"}
    problems = forget_redact.verify_export(payload)
    n = forget_redact.redact_export(payload, red)
    out["spans"] = n
    if not problems:
        problems = forget_redact.verify_export(payload)
    if problems:
        return {**out, "verify": "; ".join(problems)}
    if n and apply:
        _write(path, payload)
        out["changed"] = True
    return out


def redact_flue_db(db_path: Path, red, *, apply: bool) -> dict:
    """The Flue sidecar's SQLite: every JSON ``data`` cell has its strings redacted in place. The sidecar must be stopped."""
    counts = {"rows": 0, "spans": 0, "chunked_skipped": 0}
    con = sqlite3.connect(str(db_path))
    try:
        for table, col, keys in FLUE_TABLES:
            try:
                rows = con.execute(f"SELECT {', '.join(keys)}, {col} FROM {table}").fetchall()
            except sqlite3.OperationalError:
                continue
            for r in rows:
                data = r[-1]
                if data is None:
                    counts["chunked_skipped"] += 1
                    continue
                before = red.spans
                try:
                    new = json.dumps(red.json(json.loads(data)), ensure_ascii=False, separators=(",", ":"))
                except ValueError:
                    continue
                if red.spans != before:
                    counts["rows"] += 1
                    counts["spans"] += red.spans - before
                    if apply:
                        where = " AND ".join(f"{k} = ?" for k in keys)
                        con.execute(f"UPDATE {table} SET {col} = ? WHERE {where}", (new, *r[:-1]))
        if apply:
            con.commit()
            con.execute("VACUUM")
    finally:
        con.close()
    return counts


async def _redactor():
    import forget_redact
    from db_pool import close_pool, init_pool

    await init_pool()
    try:
        return await forget_redact.load_redactor()
    finally:
        await close_pool()


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", action="append", default=None, help="a directory of exports (default: the two above)")
    ap.add_argument("--apply", action="store_true", help="rewrite the files (default: count only)")
    ap.add_argument("--delete-pre-compact-tars", action="store_true", help="remove mempalace-pre-compact-*.tar from the directories")
    ap.add_argument("--flue-db", default="", help="the Flue sidecar SQLite to redact (needs --i-stopped-the-brain with --apply)")
    ap.add_argument("--i-stopped-the-brain", action="store_true", help="acknowledge the Flue sidecar is stopped (it holds its own copy in memory)")
    args = ap.parse_args(argv)
    if args.flue_db and args.apply and not args.i_stopped_the_brain:
        print("REFUSED: stop the brain's Flue sidecar first, then pass --i-stopped-the-brain", file=sys.stderr)
        return 2
    try:
        red = asyncio.run(_redactor())
    except Exception as exc:  # noqa: BLE001
        print(f"cannot load the forget ledger ({type(exc).__name__}): load the service environment", file=sys.stderr)
        return 1
    dirs = [Path(os.path.expanduser(d)) for d in (args.dir or DEFAULT_DIRS)]
    bad = 0
    for d in dirs:
        for f in sorted(glob.glob(str(d / "*.json")) + glob.glob(str(d / "*.json.gz"))):
            r = redact_file(Path(f), red, apply=args.apply)
            bad += r["verify"] != "ok"
            print(json.dumps(r, sort_keys=True))
        tars = sorted(glob.glob(str(d / TAR_GLOB)))
        for t in tars:
            if args.apply and args.delete_pre_compact_tars:
                os.remove(t)
            print(json.dumps({"file": os.path.basename(t), "tar": "deleted" if args.apply and args.delete_pre_compact_tars else "not rewritten"}))
    if args.flue_db:
        print(json.dumps({"flue_db": redact_flue_db(Path(os.path.expanduser(args.flue_db)), red, apply=args.apply)}, sort_keys=True))
    print(f"{'APPLIED' if args.apply else 'DRY RUN (nothing changed; add --apply)'}; {red.spans} span(s) found")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
