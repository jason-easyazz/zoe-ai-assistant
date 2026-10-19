#!/usr/bin/env python3
"""Write the manifest of one Postgres dump: what a restore of it must reproduce (deep dive 2, S0-8).

Called by ``postgres-nightly-backup.sh`` right after each ``pg_dump -Fc``; read by the weekly restore drill
(``zoe_restore_drill.py``), which restores the dump into a THROWAWAY container and compares.

The row counts come FROM THE DUMP ITSELF, not from the live database: ``pg_restore -l`` lists every table in the
dump's table of contents, and ``pg_restore --data-only -f -`` streams its COPY blocks (one line per row in COPY text
format) so the counts are exactly what the dump holds, with no race against writes that landed after pg_dump took
its snapshot. Tables whose DATA was excluded (the biometric tables, ``--exclude-table-data``) are listed with 0
rows under ``data_excluded``.

The live catalog contributes only the TABLE SET (and the pgvector / Alembic versions), so a dump that silently
lacks a table the database has is caught at backup time (``problems``) and again by the drill.

NEVER writes to the live database: ``pg_restore -l`` and ``pg_restore -f -`` run inside the database container with
no connection (they read the dump from stdin and write SQL to stdout), and the catalog reads are plain SELECTs.

Exit: 0 manifest written (``problems`` may be non-empty: the drill reports them), 1 could not build a manifest.
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import IO, Iterable, Optional

# One TOC line per object: "<dumpId>; <catalog oid> <oid> TABLE <schema> <name> <owner>"; data is "TABLE DATA".
_TOC = re.compile(r"^\d+;\s+\d+\s+\d+\s+(TABLE DATA|TABLE)\s+(?!ATTACH\s)(\S+)\s+(.+?)\s+\S+$")
_COPY = re.compile(r"^COPY\s+(.+?)\s+(?:\(.*\)\s+)?FROM stdin;$")
CATALOG_TABLES_SQL = (
    "SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p') AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND n.nspname NOT LIKE 'pg\\_toast%' AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid "
    "AND d.deptype = 'e') ORDER BY 1"
)
EXTENSIONS_SQL = "SELECT extname || '=' || extversion FROM pg_extension ORDER BY 1"
# a CASE would still plan the missing relation: psql's \if skips it instead
ALEMBIC_SQL = """SELECT to_regclass('public.alembic_version') IS NOT NULL AS has_alembic \\gset
\\if :has_alembic
SELECT string_agg(version_num, ',' ORDER BY version_num) FROM public.alembic_version;
\\endif
"""


def _unquote_ident(s: str) -> str:
    return s[1:-1].replace('""', '"') if len(s) >= 2 and s[0] == s[-1] == '"' else s


def split_qualified(name: str) -> str:
    """``public."My Table"`` -> ``public.My Table`` (one dot outside quotes separates schema and table)."""
    in_q, cut = False, -1
    for i, ch in enumerate(name):
        if ch == '"':
            in_q = not in_q
        elif ch == "." and not in_q:
            cut = i
            break
    if cut < 0:
        return "public." + _unquote_ident(name)
    return _unquote_ident(name[:cut]) + "." + _unquote_ident(name[cut + 1:])


def parse_toc(lines: Iterable[str]) -> tuple[set[str], set[str]]:
    """``(tables, tables_with_data)`` from ``pg_restore -l`` output."""
    tables, with_data = set(), set()
    for line in lines:
        m = _TOC.match(line.strip())
        if not m:
            continue
        kind, schema, name = m.groups()
        q = f"{schema}.{name}"
        (with_data if kind == "TABLE DATA" else tables).add(q)
    return tables, with_data


def count_copy_rows(stream: IO[str]) -> dict[str, int]:
    """Rows per table from a data-only SQL script: every line between ``COPY ... FROM stdin;`` and ``\\.`` is one
    row (COPY text format escapes newlines inside values as ``\\n``)."""
    counts: dict[str, int] = {}
    current: Optional[str] = None
    for raw in stream:
        line = raw.rstrip("\n")
        if current is None:
            m = _COPY.match(line)
            if m:
                current = split_qualified(m.group(1))
                counts.setdefault(current, 0)
            continue
        if line == "\\.":
            current = None
            continue
        counts[current] += 1
    if current is not None:
        raise ValueError(f"COPY block for {current} never terminated: truncated dump")
    return counts


def build_manifest(toc_lines: Iterable[str], copy_stream: IO[str], live_tables: Optional[set[str]],
                   extensions: dict[str, str], alembic: str) -> dict:
    tables, with_data = parse_toc(toc_lines)
    copied = count_copy_rows(copy_stream)
    problems = []
    stray = sorted(set(copied) - tables)
    if stray:
        problems.append(f"COPY data for tables not in the TOC: {stray[:5]}")
    rows = {t: copied.get(t, 0) for t in sorted(tables)}
    excluded = sorted(tables - with_data)
    if live_tables is not None:
        missing = sorted(live_tables - tables)
        extra = sorted(tables - live_tables)
        if missing:
            problems.append(f"{len(missing)} live table(s) absent from the dump: {missing[:5]}")
        if extra:
            problems.append(f"{len(extra)} dumped table(s) not in the live catalog: {extra[:5]}")
    return {"tables": rows, "data_excluded": excluded,
            "live_tables": sorted(live_tables) if live_tables is not None else None,
            "extensions": extensions, "alembic_version": alembic, "problems": problems}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _psql(container: str, db: str, sql: str) -> list[str]:
    # `-e PGPASSWORD` (no value) copies it from this process's environment: never on a command line.
    r = subprocess.run(["docker", "exec", "-i", "-e", "PGPASSWORD", container, "psql", "-U", "zoe", "-d", db, "-XAt",
                        "-v", "ON_ERROR_STOP=1", "-f", "-"], input=sql, capture_output=True, text=True,
                       timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"catalog read failed: {r.stderr.strip()[-200:]}")
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--container", required=True, help="a container with pg_restore (the database container)")
    ap.add_argument("--db", required=True)
    ap.add_argument("--dump", type=Path, required=True, help="the dump: -Fc, plain or .gz")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--no-live", action="store_true", help="skip the live catalog reads (tests)")
    ns = ap.parse_args(argv)
    try:
        with plain_dump(ns.dump) as plain:
            man = _manifest(ns, plain)
    except Exception as exc:  # noqa: BLE001 - the caller logs and carries on; the drill reports a missing manifest
        print(f"pg_dump_manifest: {ns.db}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    man.update({"db": ns.db, "dump": ns.dump.name, "dump_sha256": _sha256(ns.dump),
                "created": datetime.now().astimezone().isoformat(timespec="seconds")})
    ns.out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ns.out.parent, prefix=".manifest-")
    with os.fdopen(fd, "w") as out:
        json.dump(man, out, indent=1, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ns.out)
    total = sum(man["tables"].values())
    print(f"pg_dump_manifest: {ns.db}: {len(man['tables'])} tables, {total} rows"
          + (f", PROBLEMS: {'; '.join(man['problems'])}" if man["problems"] else ""))
    return 0


@contextlib.contextmanager
def plain_dump(path: Path):
    """The dump as a real file a subprocess can read: a ``.gz`` is decompressed to a private temp file (a GzipFile
    passed as ``stdin=`` would hand the child the COMPRESSED descriptor)."""
    if path.suffix != ".gz":
        yield path
        return
    with tempfile.TemporaryDirectory(prefix="pg-manifest-") as td:
        os.chmod(td, 0o700)
        out = Path(td) / "dump"
        with gzip.open(path, "rb") as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
        yield out


def _manifest(ns, plain: Path) -> dict:
    with open(plain, "rb") as fh:
        toc = subprocess.run(["docker", "exec", "-i", ns.container, "pg_restore", "-l"], stdin=fh,
                             capture_output=True, timeout=600)
    if toc.returncode != 0:
        raise RuntimeError(f"pg_restore -l failed: {toc.stderr.decode(errors='replace').strip()[-200:]}")
    with open(plain, "rb") as fh, tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as sql:
        # -f - : SQL to stdout, NO database connection (nothing is restored anywhere)
        p = subprocess.run(["docker", "exec", "-i", ns.container, "pg_restore", "--data-only", "-f", "-"],
                           stdin=fh, stdout=sql, stderr=subprocess.PIPE, timeout=1800)
        if p.returncode != 0:
            raise RuntimeError(f"pg_restore --data-only failed: {p.stderr.decode(errors='replace').strip()[-200:]}")
        sql.seek(0)
        live = exts = None
        alembic = ""
        if not ns.no_live:
            live = set(_psql(ns.container, ns.db, CATALOG_TABLES_SQL))
            exts = dict(x.split("=", 1) for x in _psql(ns.container, ns.db, EXTENSIONS_SQL))
            alembic = "".join(_psql(ns.container, ns.db, ALEMBIC_SQL))
        return build_manifest(toc.stdout.decode(errors="replace").splitlines(), sql, live, exts or {}, alembic)


if __name__ == "__main__":
    sys.exit(main())
