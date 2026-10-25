#!/usr/bin/env python3
"""Restore plan for a dump whose table DATA was partly excluded (the biometric tables, ``--exclude-table-data``).

The problem class: a backed-up table with a FOREIGN KEY into a data-excluded table (``omi_segments.voice_cluster_id`` ->
``omi_voice_clusters``) restores its rows but not the parents they point at, so ``ADD CONSTRAINT ... FOREIGN KEY`` fails
(``pg_restore`` exit 1, "errors ignored on restore") on every restore, and the weekly verify goes red for good.

Decision: biometric rows never enter a backup (owner privacy rule), and a link to a re-learnable biometric row is itself
re-learnable. So a restore runs in sections - ``pre-data`` (tables), ``data`` (rows), NULLING of the dangling
referencing columns (this module's SQL), then ``post-data`` (constraints and indexes, FKs now satisfiable). Nothing is
ignored: any other ``pg_restore`` error still fails the restore. A NOT NULL referencing column makes the UPDATE fail,
loudly, rather than inventing a value.

The FK set is discovered FROM THE DUMP (``pg_restore -s -f -``: SQL to stdout, no connection), so it is right for any
dump, new FK or old manifest; the backup records the same list in the manifest (``excluded_fks``) as an audit trail.

Pure functions (no subprocess, no Docker): the callers feed in ``pg_restore -l`` and ``pg_restore -s -f -`` output
(``zoe_restore_drill.py`` through the sandbox helper's ``inspect``; ``pg_dump_manifest.py`` through ``docker exec``).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pg_dump_manifest as _man  # noqa: E402

_ID = r"(?:\"[^\"]+\"|[\w$]+)"
# "ALTER TABLE ONLY public.omi_segments\n    ADD CONSTRAINT name FOREIGN KEY (a, b) REFERENCES public.parent(id) ..."
_FK = re.compile(
    rf"ALTER TABLE (?:ONLY )?(?P<child>{_ID}(?:\.{_ID})?)\s+"
    rf"ADD CONSTRAINT (?P<name>{_ID}) FOREIGN KEY \((?P<cols>[^)]*)\) "
    rf"REFERENCES (?P<parent>{_ID}(?:\.{_ID})?)", re.S)


def parse_fks(schema_sql: str) -> list[dict]:
    """Every FK in a ``pg_restore -s`` script: ``{child, name, columns, parent}`` (tables as ``schema.table``)."""
    out = []
    for m in _FK.finditer(schema_sql):
        cols = [_man._unquote_ident(c.strip()) for c in m["cols"].split(",") if c.strip()]
        out.append({"child": _man.split_qualified(m["child"]), "name": _man._unquote_ident(m["name"]),
                    "columns": cols, "parent": _man.split_qualified(m["parent"])})
    return out


def excluded_fks(fks: Iterable[dict], excluded: Iterable[str]) -> list[dict]:
    """FKs whose parent has no data in the dump while the referencing table does."""
    ex = set(excluded)
    return [f for f in fks if f["parent"] in ex and f["child"] not in ex]


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def _qt(qualified: str) -> str:
    schema, table = qualified.split(".", 1)
    return f"{_q(schema)}.{_q(table)}"


def nulling_sql(fks: Iterable[dict]) -> str:
    """One UPDATE per FK: the dangling link is cleared, the row itself is kept. Empty string when there is none."""
    lines = []
    for f in fks:
        sets = ", ".join(f"{_q(c)} = NULL" for c in f["columns"])
        where = " OR ".join(f"{_q(c)} IS NOT NULL" for c in f["columns"])
        lines.append(f"-- {f['name']}: {f['child']} -> {f['parent']} (parent data excluded from the dump)")
        lines.append(f"UPDATE {_qt(f['child'])} SET {sets} WHERE {where};")
    return "\n".join(lines) + ("\n" if lines else "")


def excluded_tables(manifest: Optional[dict], toc_lines: list[str]) -> list[str]:
    """The manifest's ``data_excluded`` when it recorded one, else derived from the dump's TOC (tables without a
    ``TABLE DATA`` entry): an old or missing manifest must not hide the problem."""
    if manifest and manifest.get("data_excluded") is not None and "tables" in manifest:
        return list(manifest["data_excluded"])
    tables, with_data = _man.parse_toc(toc_lines)
    return sorted(tables - with_data)


def plan_for(toc_lines: list[str], schema_sql: str, manifest: Optional[dict] = None) -> list[dict]:
    return excluded_fks(parse_fks(schema_sql), excluded_tables(manifest, toc_lines))
