#!/usr/bin/env python3
"""Report (and, on the owner's say-so, purge) durable memory filed under a GUEST identity.

Why (2026-10-14): with strict voice identity an unrecognised voice runs as ``guest``, and the
post-turn digest stored that turn's personal disclosures as ``user=guest`` approved rows - a
household member's words in the shared guest bucket, attributed to nobody. Every writer now
refuses an unidentified turn (``user_filters.is_unidentified``, ``MemoryService.ingest``); this
tool finds what was stored before that. Whether to purge or leave it is the owner's decision.

Guest sentinels: ``user_filters.GUEST_USERS`` (``guest`` / ``anonymous`` / ``voice-guest`` /
``voice-daemon`` / empty). A row is guest-scoped when its ``user_id`` (else ``wing``) is one.

Usage
  # read-only report: counts by owner / status / type / writer / day - NO row text. Palace
  # opened ``mode=ro``; Postgres counts via psql (SELECT only). Safe while zoe-data runs.
  python3 scripts/maintenance/guest_memory_report.py
  python3 scripts/maintenance/guest_memory_report.py --json
  python3 scripts/maintenance/guest_memory_report.py --show     # + id and text, on this terminal only

  # purge: a DRY RUN unless --execute (lists the ids it would erase, erases nothing)
  python3 scripts/maintenance/guest_memory_report.py --purge
  # the real thing, operator-run after reading the report, zoe-data stopped:
  systemctl --user stop zoe-data
  ~/.zoe/venvs/zoe-data-py312/bin/python scripts/maintenance/guest_memory_report.py \\
      --purge --execute --i-stopped-zoe-data
  systemctl --user start zoe-data

``--purge --execute`` goes through ``MemoryService.erase_rows`` - the audited forget path
(content-free intent row first, the rows and their per-row audit trail, a done row, then the
physical residue erase). It touches only palace rows owned by a guest sentinel; Postgres rows
are reported, never purged, by this tool.

``--show`` output is household speech: keep it on the box, never paste it into a PR or issue.

Exit codes: 0 = report printed / purge done (or dry run), 2 = refused, the data could not be read, or an executed
purge erased fewer rows than it was asked to (the owner id is passed exactly as stored).
"""
from __future__ import annotations

import argparse
import json
import shlex
import socket
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))
from user_filters import GUEST_USERS, is_unidentified  # noqa: E402

DEFAULT_PALACE = "~/.mempalace"
DEFAULT_PSQL = "docker exec zoe-database psql -U zoe -d zoe"
DRAWERS = "mempalace_drawers"
PURGE_ACTOR = "guest_memory_purge"
PURGE_REASON = "guest-scoped rows purged by the owner (unidentified turns are never anyone's memory)"
# Postgres: every base table carrying a user_id column, counted for the sentinel owners (SELECT only).
_SENTINEL_SQL = ", ".join("'" + u.replace("'", "''") + "'" for u in GUEST_USERS)
TABLES_SQL = (
    "select table_name from information_schema.columns where table_schema = 'public' "
    "and column_name = 'user_id' and table_name in (select table_name from information_schema.tables "
    "where table_schema = 'public' and table_type = 'BASE TABLE') order by table_name"
)


def load_guest_rows(palace: str) -> list[dict[str, Any]]:
    """Every drawers row (any status) owned by a guest sentinel: ``{id, owner, text, meta}``. Read-only sqlite."""
    db = Path(palace).expanduser() / "chroma.sqlite3"
    if not db.exists():
        raise OSError(f"no such palace database: {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        segs = [r[0] for r in con.execute(
            "SELECT s.id FROM segments s JOIN collections c ON c.id = s.collection "
            "WHERE c.name = ? AND s.scope = 'METADATA'", (DRAWERS,))]
        rows: list[dict[str, Any]] = []
        for seg in segs:
            for rid, eid in con.execute("SELECT id, embedding_id FROM embeddings WHERE segment_id = ?", (seg,)):
                meta: dict[str, Any] = {}
                for k, s, i, f in con.execute(
                        "SELECT key, string_value, int_value, float_value FROM embedding_metadata WHERE id = ?", (rid,)):
                    meta[k] = s if s is not None else (i if i is not None else f)
                owner = str(meta.get("user_id") or meta.get("wing") or "")
                if not is_unidentified(owner):
                    continue
                if not (meta.get("user_id") or meta.get("wing")):
                    continue        # ownerless rows are remediate_ownerless_memories.py's, not a guest's
                text = str(meta.pop("chroma:document", "") or "")
                # the owner EXACTLY as stored: the purge's erase_rows matches it byte for byte ("Guest" / " guest " are
                # their own owners - normalising them would make the erase skip the row and still report success)
                rows.append({"id": str(eid), "owner": owner, "text": text, "meta": meta})
        return sorted(rows, key=lambda r: (str(r["meta"].get("added_at") or ""), r["id"]))
    finally:
        con.close()


def _writer(meta: dict[str, Any]) -> str:
    return str(meta.get("origin") or meta.get("source") or meta.get("added_by") or "-")


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts only - never text. Pure."""
    def count(key) -> dict[str, int]:
        return dict(sorted(Counter(key(r) for r in rows).items()))

    return {
        "palace_rows": len(rows),
        "by_owner": count(lambda r: repr(r["owner"]) if r["owner"] != r["owner"].strip() else (r["owner"] or "(empty)")),
        "by_status": count(lambda r: str(r["meta"].get("status") or "approved")),
        "by_type": count(lambda r: str(r["meta"].get("memory_type") or "-")),
        "by_writer": count(lambda r: _writer(r["meta"])),
        "by_day": count(lambda r: str(r["meta"].get("added_at") or "-")[:10]),
    }


def _psql(psql_cmd: str, sql: str) -> str:
    return subprocess.run([*shlex.split(psql_cmd), "-Atc", sql], capture_output=True, text=True,
                          timeout=60, check=True).stdout.strip()


def postgres_counts(psql_cmd: str) -> dict[str, int]:
    """``{table: n}`` of rows owned by a guest sentinel, for tables with a ``user_id`` column (n > 0 only)."""
    out: dict[str, int] = {}
    for table in [t for t in _psql(psql_cmd, TABLES_SQL).splitlines() if t.strip()]:
        if not table.replace("_", "").isalnum():
            continue
        try:
            n = int(_psql(psql_cmd, f'select count(*) from "{table}" where user_id::text in ({_SENTINEL_SQL})') or 0)
        except (subprocess.SubprocessError, ValueError):
            continue
        if n:
            out[table] = n
    return out


def render(summary: dict[str, Any], pg: Optional[dict[str, int]], rows: list[dict[str, Any]], show: bool) -> str:
    lines = [f"Guest-scoped palace rows: {summary['palace_rows']}"]
    for key in ("by_owner", "by_status", "by_type", "by_writer", "by_day"):
        if summary[key]:
            lines.append(f"  {key[3:]:<7} " + ", ".join(f"{k}={v}" for k, v in summary[key].items()))
    if pg is None:
        lines.append("Postgres: not read (--no-pg, or psql unavailable)")
    else:
        lines.append("Postgres rows owned by a guest sentinel: "
                     + (", ".join(f"{t}={n}" for t, n in pg.items()) if pg else "none"))
    if show and rows:
        lines += ["", "--show: household speech below - keep it on this box, never paste it anywhere."]
        for r in rows:
            m = r["meta"]
            lines.append(f"  {r['id']}  owner={r['owner']} status={m.get('status') or 'approved'} "
                         f"type={m.get('memory_type') or '-'} writer={_writer(m)} at={m.get('added_at') or '-'}")
            lines.append(f"      {r['text']}")
    return "\n".join(lines)


def _service_listening(host: str = "127.0.0.1", port: int = 8000) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _service_active(unit: str = "zoe-data") -> bool:
    try:
        out = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return out in ("active", "activating", "reloading")


async def purge(rows: list[dict[str, Any]], *, palace: str, svc=None) -> dict[str, Any]:
    """Erase the guest rows through ``MemoryService.erase_rows`` (the audited forget path), one call per sentinel owner."""
    if svc is None:
        from memory_service import MemoryService

        svc = MemoryService(data_dir=str(Path(palace).expanduser()))
    by_owner: dict[str, list[str]] = {}
    for r in rows:
        if is_unidentified(r["owner"]) and r["owner"]:
            by_owner.setdefault(r["owner"], []).append(r["id"])
    done: dict[str, Any] = {}
    for owner, ids in sorted(by_owner.items()):
        res = await svc.erase_rows(owner, ids, actor=PURGE_ACTOR, reason=PURGE_REASON)
        removed = int((res or {}).get("rows_removed") or 0)
        done[owner] = {"asked": len(ids), "rows_removed": removed, "complete": removed == len(ids)}
    return done


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--show", action="store_true", help="also print each row's id and TEXT (local terminal only)")
    ap.add_argument("--json", action="store_true", help="machine-readable counts (never text)")
    ap.add_argument("--purge", action="store_true", help="erase the guest palace rows (a dry run without --execute)")
    ap.add_argument("--execute", action="store_true", help="with --purge: really erase")
    ap.add_argument("--i-stopped-zoe-data", action="store_true",
                    help="acknowledge zoe-data is stopped (the purge writes the palace in-process)")
    ap.add_argument("--palace", default=DEFAULT_PALACE)
    ap.add_argument("--psql-cmd", default=DEFAULT_PSQL)
    ap.add_argument("--no-pg", action="store_true", help="skip the Postgres counts")
    args = ap.parse_args(argv)

    if args.execute and not args.purge:
        print("refused: --execute only applies to --purge", file=sys.stderr)
        return 2
    if args.purge and args.execute:
        if not args.i_stopped_zoe_data:
            print("refused: --purge --execute writes the palace in-process; stop zoe-data and pass "
                  "--i-stopped-zoe-data (two writers on one chroma index corrupt it)", file=sys.stderr)
            return 2
        if _service_listening() or _service_active():
            print("refused: zoe-data still looks up (:8000 listening or the unit is active)", file=sys.stderr)
            return 2
    try:
        rows = load_guest_rows(args.palace)
    except (OSError, sqlite3.Error) as exc:
        print(f"guest memory report: could not read the palace: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    pg: Optional[dict[str, int]] = None
    if not args.no_pg:
        try:
            pg = postgres_counts(args.psql_cmd)
        except (OSError, subprocess.SubprocessError):
            pg = None
    summary = summarise(rows)
    if args.json:
        print(json.dumps({**summary, "postgres": pg}, indent=2))
    else:
        print(render(summary, pg, rows, args.show))
    if not args.purge:
        return 0
    if not args.execute:
        print(f"\nDRY RUN: --purge would erase {len(rows)} guest palace row(s) via MemoryService.erase_rows; "
              "nothing was changed. Add --execute --i-stopped-zoe-data (zoe-data stopped) to do it.")
        return 0
    import asyncio

    done = asyncio.run(purge(rows, palace=args.palace))
    print(json.dumps({"purged": done}, indent=2))
    if not all(d["complete"] for d in done.values()):
        print("INCOMPLETE: some rows were not erased (rows_removed < asked); re-run the report", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
