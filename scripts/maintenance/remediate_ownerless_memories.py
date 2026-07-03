#!/usr/bin/env python3
"""Audit or remediate Zoe MemPalace rows stranded without ownership metadata.

Operator run-sheet, decision of record:

1. `python3 scripts/maintenance/remediate_ownerless_memories.py --audit`
2. `python3 scripts/maintenance/remediate_ownerless_memories.py --delete`
   Dry-run first and verify the nine sanctioned ids:
   `zoe_01075f3d7a996d38eea9`,
   `zoe_2859092b35dda8771a52`,
   `zoe_60ae521e9751e1c88d75`,
   `zoe_79ee002112a51b5087ff`,
   `zoe_8453aff7ca89516cdc40`,
   `zoe_8890d01119face08360c`,
   `zoe_a5be30c7fb150d577180`,
   `zoe_c87f34465c7a705092b6`,
   `zoe_e89b2cbb7d11825a6745`.
3. `python3 scripts/maintenance/remediate_ownerless_memories.py --delete --execute`
4. After an executed delete, verify from a fresh process:
   `python3 scripts/maintenance/remediate_ownerless_memories.py --audit`

Decision of record: DELETE, per Jason 2026-07-03. The rows are April 2026
test data, not family facts. `--backfill` remains implemented for auditability
and emergency rollback-style remediation, but it is not the chosen action.
The broader `--all-ownerless --execute` path additionally requires
`--confirm-all-ownerless`; it is an emergency override, not the run-sheet path.

Do not restart `:8000` or `:11434` for this script. The script talks to the
Chroma persistent store directly. Run it from zoe-data's exact ChromaDB
version/venv. `zoe-data` may have the store open while the script runs; if the
installed ChromaDB version requires exclusive access or reports a lock, stop
and rerun during a maintenance window rather than papering over it. After an
executed delete, use a fresh-process `--audit`; zoe-data's live client may not
observe deletions until it reopens the collection.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


DEFAULT_DB = Path("/home/zoe/.mempalace/chroma.sqlite3")
DEFAULT_COLLECTION = "mempalace_drawers"
DEFAULT_BACKFILL_USER_ID = "family-admin"
DEFAULT_BACKFILL_VISIBILITY = "personal"
BACKUP_MARKER = "ownerless-remediation"
OWNER_KEYS = ("user_id", "wing", "visibility")
MAINTENANCE_WINDOW_GUIDANCE = (
    "The Chroma/MemPalace store appears locked or unavailable. "
    "Do not force the remediation; rerun during a maintenance window when "
    "zoe-data has released the store."
)
NON_LOCK_STORE_GUIDANCE = (
    "Check --db, --collection, and zoe-data's exact ChromaDB version/venv."
)
LEGACY_OWNERLESS_IDS = (
    "zoe_01075f3d7a996d38eea9",
    "zoe_2859092b35dda8771a52",
    "zoe_60ae521e9751e1c88d75",
    "zoe_79ee002112a51b5087ff",
    "zoe_8453aff7ca89516cdc40",
    "zoe_8890d01119face08360c",
    "zoe_a5be30c7fb150d577180",
    "zoe_c87f34465c7a705092b6",
    "zoe_e89b2cbb7d11825a6745",
)


@dataclass(frozen=True)
class MemoryRow:
    row_id: str
    document: str | None
    metadata: dict[str, Any]

    @property
    def is_ownerless(self) -> bool:
        return is_ownerless_metadata(self.metadata)


def is_ownerless_metadata(metadata: dict[str, Any] | None) -> bool:
    """Rows missing every ownership key are stranded from Zoe's user filters."""

    if not isinstance(metadata, dict):
        metadata = {}
    return all(not str(metadata.get(key) or "").strip() for key in OWNER_KEYS)


def parse_ids(raw_values: Sequence[str] | None) -> list[str]:
    if not raw_values:
        return list(LEGACY_OWNERLESS_IDS)
    parsed: list[str] = []
    for value in raw_values:
        parsed.extend(part.strip() for part in value.split(",") if part.strip())
    return dedupe_preserving_order(parsed)


def dedupe_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def persistent_dir_for_db(db_path: Path) -> Path:
    return db_path.expanduser().resolve().parent


def require_existing_db(db_path: Path) -> Path:
    source = db_path.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Refusing to run; database file does not exist: {source}")
    return source


def is_lock_error(exc: BaseException) -> bool:
    return "database is locked" in str(exc).lower()


def system_exit_for_store_error(exc: BaseException, *, action: str) -> SystemExit:
    if isinstance(exc, sqlite3.OperationalError) and is_lock_error(exc):
        return SystemExit(f"Refusing to {action}: {exc}. {MAINTENANCE_WINDOW_GUIDANCE}")
    return SystemExit(f"Refusing to {action}: {exc}. {NON_LOCK_STORE_GUIDANCE}")


def load_collection(db_path: Path, collection_name: str):
    try:
        import chromadb  # type: ignore[import]
    except ImportError as exc:
        raise SystemExit(
            "chromadb is required. Run this from zoe-data's exact ChromaDB version/venv."
        ) from exc

    try:
        client = chromadb.PersistentClient(path=str(persistent_dir_for_db(db_path)))
        return client.get_collection(collection_name)
    except Exception as exc:
        raise system_exit_for_store_error(exc, action="open Chroma store") from exc


def rows_from_result(result: dict[str, Any]) -> list[MemoryRow]:
    ids = result.get("ids") or []
    docs = result.get("documents") or []
    metas = result.get("metadatas") or []
    rows: list[MemoryRow] = []
    for index, row_id in enumerate(ids):
        document = docs[index] if index < len(docs) else None
        metadata = metas[index] if index < len(metas) else {}
        rows.append(
            MemoryRow(
                row_id=str(row_id),
                document=document,
                metadata=dict(metadata) if isinstance(metadata, dict) else {},
            )
        )
    return rows


def fetch_rows_by_ids(collection: Any, ids: Sequence[str]) -> list[MemoryRow]:
    if not ids:
        return []
    return rows_from_result(collection.get(ids=list(ids), include=["documents", "metadatas"]))


def fetch_ownerless_rows(collection: Any) -> list[MemoryRow]:
    result = collection.get(include=["documents", "metadatas"])
    return [row for row in rows_from_result(result) if row.is_ownerless]


def select_target_rows(
    collection: Any,
    *,
    ids: Sequence[str],
    all_ownerless: bool,
) -> list[MemoryRow]:
    if all_ownerless:
        return fetch_ownerless_rows(collection)

    rows = fetch_rows_by_ids(collection, ids)
    found = {row.row_id for row in rows}
    missing = [row_id for row_id in ids if row_id not in found]
    if missing:
        raise SystemExit("Refusing to continue; target ids not found: " + ", ".join(missing))
    non_ownerless = [row for row in rows if not row.is_ownerless]
    if non_ownerless:
        summary = ", ".join(row.row_id for row in non_ownerless)
        raise SystemExit(
            "Refusing to continue; targeted rows already have ownership metadata: " + summary
        )
    return rows


def validate_ownerless_rows(rows: Sequence[MemoryRow], *, context: str) -> None:
    non_ownerless = [row for row in rows if not row.is_ownerless]
    if non_ownerless:
        summary = ", ".join(row.row_id for row in non_ownerless)
        raise SystemExit(
            f"Refusing to continue; {context} rows gained ownership metadata: {summary}"
        )


def print_rows(rows: Sequence[MemoryRow], *, prefix: str) -> None:
    for row in rows:
        owner_bits = ", ".join(
            f"{key}={row.metadata.get(key)!r}" for key in OWNER_KEYS if row.metadata.get(key)
        )
        if not owner_bits:
            owner_bits = "ownerless"
        print(f"{prefix} {row.row_id}: {owner_bits}")


def create_backup(db_path: Path) -> Path:
    source = require_existing_db(db_path)
    store_dir = source.parent
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = store_dir.with_name(f"{store_dir.name}.{BACKUP_MARKER}.{timestamp}.bak")
    suffix = 1
    while backup_path.exists():
        backup_path = store_dir.with_name(
            f"{store_dir.name}.{BACKUP_MARKER}.{timestamp}.{suffix}.bak"
        )
        suffix += 1
    try:
        shutil.copytree(store_dir, backup_path)
        shutil.copymode(store_dir, backup_path)
    except OSError as exc:
        if backup_path.exists():
            shutil.rmtree(backup_path, ignore_errors=True)
        raise SystemExit(f"Refusing to execute; backup failed: {exc}") from exc
    print(f"Backup created: {backup_path}")
    return backup_path


def backfill_rows(collection: Any, rows: Sequence[MemoryRow], *, user_id: str) -> None:
    metadatas = []
    for row in rows:
        metadata = dict(row.metadata)
        metadata["user_id"] = user_id
        metadata["wing"] = user_id
        metadata["visibility"] = DEFAULT_BACKFILL_VISIBILITY
        metadatas.append(metadata)
    try:
        collection.update(ids=[row.row_id for row in rows], metadatas=metadatas)
    except Exception as exc:
        raise system_exit_for_store_error(exc, action="backfill ownerless rows") from exc


def delete_rows(collection: Any, rows: Sequence[MemoryRow]) -> None:
    row_ids = [row.row_id for row in rows]
    try:
        refreshed = fetch_rows_by_ids(collection, row_ids)
    except Exception as exc:
        raise system_exit_for_store_error(exc, action="re-validate ownerless rows") from exc
    found = {row.row_id for row in refreshed}
    missing = [row_id for row_id in row_ids if row_id not in found]
    if missing:
        raise SystemExit(
            "Refusing to delete; targeted rows disappeared before mutation: "
            + ", ".join(missing)
        )
    validate_ownerless_rows(refreshed, context="targeted")
    try:
        collection.delete(ids=row_ids)
    except Exception as exc:
        raise system_exit_for_store_error(exc, action="delete ownerless rows") from exc


def run(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    require_existing_db(db_path)
    target_ids = parse_ids(args.ids)
    if not target_ids and not args.all_ownerless:
        raise SystemExit("Refusing to continue; --ids parsed to an empty allowlist")

    if args.execute:
        create_backup(db_path)
    collection = load_collection(db_path, args.collection)

    if args.audit:
        try:
            rows = fetch_ownerless_rows(collection)
        except Exception as exc:
            raise system_exit_for_store_error(exc, action="audit ownerless rows") from exc
        print(f"Ownerless rows: {len(rows)}")
        print_rows(rows, prefix="ownerless")
        return 0

    try:
        rows = select_target_rows(
            collection,
            ids=target_ids,
            all_ownerless=bool(args.all_ownerless),
        )
    except Exception as exc:
        if isinstance(exc, SystemExit):
            raise
        raise system_exit_for_store_error(exc, action="select target rows") from exc
    action = "backfill" if args.backfill else "delete"
    print(f"Target ownerless rows: {len(rows)}")
    print_rows(rows, prefix="target")

    if not rows:
        print(f"No rows to {action}.")
        return 0

    if args.backfill:
        print(
            "Backfill metadata: "
            f"user_id={args.user_id!r}, wing={args.user_id!r}, "
            f"visibility={DEFAULT_BACKFILL_VISIBILITY!r}"
        )

    if not args.execute:
        print(f"DRY RUN: would {action} {len(rows)} row(s). No changes made.")
        return 0

    if args.backfill:
        backfill_rows(collection, rows, user_id=args.user_id)
    else:
        delete_rows(collection, rows)
    print(f"Executed {action} for {len(rows)} row(s).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--audit", action="store_true", help="count and list ownerless rows")
    mode.add_argument("--backfill", action="store_true", help="set owner metadata on targets")
    mode.add_argument("--delete", action="store_true", help="delete target ownerless rows")
    parser.add_argument("--execute", action="store_true", help="perform writes after backup")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"Chroma sqlite path (default: {DEFAULT_DB})")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help=argparse.SUPPRESS)
    parser.add_argument(
        "--ids",
        action="append",
        help="comma-separated target ids; defaults to the sanctioned nine legacy ids",
    )
    parser.add_argument(
        "--all-ownerless",
        action="store_true",
        help="target every row that is missing user_id, wing, and visibility",
    )
    parser.add_argument(
        "--confirm-all-ownerless",
        action="store_true",
        help="required with --all-ownerless --execute",
    )
    parser.add_argument(
        "--user-id",
        default=DEFAULT_BACKFILL_USER_ID,
        help="owner used by --backfill (default: family-admin)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not (args.audit or args.backfill or args.delete):
        args.audit = True
    if args.audit and args.execute:
        parser.error("--execute is only valid with --backfill or --delete")
    if args.audit and args.all_ownerless:
        parser.error("--all-ownerless is only valid with --backfill or --delete")
    if args.ids and args.all_ownerless:
        parser.error("--ids cannot be combined with --all-ownerless")
    if args.execute and args.all_ownerless and not args.confirm_all_ownerless:
        parser.error("--all-ownerless --execute requires --confirm-all-ownerless")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
