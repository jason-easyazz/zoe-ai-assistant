#!/usr/bin/env python3
"""Reset the engineering boards to empty — tickets only, never configuration.

Clears the accumulated ticket/history content from BOTH engineering stores so
real issues can be filed into a clean surface:

  * **Multica** (its own Postgres DB) — the board: issues, comments, inbox,
    activity history, the agent task queue, autopilot RUN history.
  * **Hermes** (`~/.hermes/kanban.db`) — the legacy executor queue: tasks,
    events, runs, links, comments.

**Configuration and identity are preserved in both.** In Multica that means
the workspace, users/members, agents, agent runtimes, skills, squads,
projects, label definitions, tokens, the GitHub installation, and — load
bearing — the `autopilot` DEFINITIONS, because zoe-data has scheduled
APScheduler jobs (`multica_autopilot_<uuid>`) that reference them by id.
Only `autopilot_run` (execution history) is cleared. In Hermes the schema and
migration state are left intact so the CLI keeps working.

Dry-run by default per `scripts/AGENTS.md`: it prints exactly what it would
delete and changes nothing until `--execute`. A restorable backup of both
stores is written before any delete.

Usage:
    python3 scripts/maintenance/reset_engineering_boards.py            # dry run
    python3 scripts/maintenance/reset_engineering_boards.py --execute
    python3 scripts/maintenance/reset_engineering_boards.py --execute --multica-only
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKUP_DIR = Path(os.environ.get("ZOE_BACKUP_DIR", Path.home() / ".zoe-backups"))
HERMES_DB = Path(os.environ.get("HERMES_KANBAN_DB", Path.home() / ".hermes" / "kanban.db"))
DB_CONTAINER = os.environ.get("ZOE_DB_CONTAINER", "zoe-database")
MULTICA_DB = os.environ.get("MULTICA_DB_NAME", "multica")

# Cleared, in this order. `issue` cascades to activity_log / comment /
# attachment / inbox_item / agent_task_queue / issue_* junctions, but the
# others are listed explicitly because rows with a NULL issue_id do NOT
# cascade — those are exactly the orphans a bare `DELETE FROM issue` leaves.
MULTICA_CONTENT_TABLES = [
    "issue",              # cascades broadly (see module docstring)
    "agent_task_queue",   # + task_message / task_usage via CASCADE
    "activity_log",
    "comment",
    "comment_reaction",
    "attachment",
    "inbox_item",
    "autopilot_run",      # RUN HISTORY only — `autopilot` definitions are kept
    "chat_message",
    "chat_session",
    "feedback",
    "pinned_item",
    "github_pull_request",
    "issue_pull_request",
    "task_usage",
    "task_usage_daily",
    "task_usage_daily_dirty",
    "task_usage_dashboard_daily",
    "task_usage_dashboard_dirty",
]

# Never touched. Deleting any of these would break the install, not clean it.
MULTICA_PRESERVED = [
    "workspace", "user", "member", "workspace_invitation", "verification_code",
    "agent", "agent_runtime", "agent_skill", "skill", "skill_file",
    "squad", "squad_member", "project", "project_resource",
    "autopilot", "autopilot_trigger",
    "issue_label", "notification_preference", "personal_access_token",
    "daemon_token", "daemon_connection", "github_installation",
    "schema_migrations",
    "task_usage_rollup_state", "task_usage_dashboard_rollup_state",
]

HERMES_CONTENT_TABLES = [
    "task_events", "task_runs", "task_comments", "task_links",
    "task_attachments", "tasks",
]


def psql(sql: str, db: str = MULTICA_DB) -> str:
    out = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "psql", "-U", "zoe", "-d", db, "-tAc", sql],
        capture_output=True, text=True, timeout=120,
    )
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()}")
    return out.stdout.strip()


def multica_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in MULTICA_CONTENT_TABLES:
        try:
            counts[table] = int(psql(f'SELECT count(*) FROM "{table}";') or 0)
        except SystemExit:
            counts[table] = -1  # table absent in this Multica version
    return counts


def hermes_counts() -> dict[str, int]:
    if not HERMES_DB.is_file():
        return {}
    counts: dict[str, int] = {}
    with sqlite3.connect(HERMES_DB) as conn:
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in HERMES_CONTENT_TABLES:
            if table in present:
                counts[table] = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    return counts


def backup(stamp: str, do_hermes: bool) -> list[Path]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    dump = BACKUP_DIR / f"multica-{stamp}.sql"
    proc = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "pg_dump", "-U", "zoe", "-d", MULTICA_DB],
        capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        raise SystemExit(f"pg_dump failed, refusing to delete: {proc.stderr.strip()}")
    dump.write_text(proc.stdout)
    if dump.stat().st_size < 1024:
        raise SystemExit(f"pg_dump output implausibly small ({dump.stat().st_size}B) — refusing to delete")
    written.append(dump)

    if do_hermes and HERMES_DB.is_file():
        target = BACKUP_DIR / f"hermes-kanban-{stamp}.db"
        shutil.copy2(HERMES_DB, target)
        written.append(target)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--multica-only", action="store_true", help="leave ~/.hermes/kanban.db alone")
    args = ap.parse_args()
    do_hermes = not args.multica_only

    print("=== Multica board (its own Postgres DB) ===")
    m_counts = multica_counts()
    m_total = sum(v for v in m_counts.values() if v > 0)
    for table, n in m_counts.items():
        if n > 0:
            print(f"  {table:<32} {n:>7}")
        elif n < 0:
            print(f"  {table:<32}   (absent)")
    print(f"  {'TOTAL rows to clear':<32} {m_total:>7}")

    h_counts = hermes_counts() if do_hermes else {}
    if do_hermes:
        print("\n=== Hermes executor queue (~/.hermes/kanban.db) ===")
        if not h_counts:
            print("  (no kanban.db found — nothing to clear)")
        for table, n in h_counts.items():
            print(f"  {table:<32} {n:>7}")
        print(f"  {'TOTAL rows to clear':<32} {sum(h_counts.values()):>7}")

    print(f"\nPreserved in Multica (configuration + identity): {', '.join(MULTICA_PRESERVED)}")
    print("  ^ includes the `autopilot` DEFINITIONS that zoe-data's scheduled jobs reference by id.")

    if not args.execute:
        print("\nDRY RUN — nothing was changed. Re-run with --execute to apply.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"\nBacking up to {BACKUP_DIR} …")
    for path in backup(stamp, do_hermes):
        print(f"  wrote {path} ({path.stat().st_size:,} bytes)")

    print("\nDeleting Multica content …")
    present = [t for t, n in m_counts.items() if n >= 0]
    # One transaction: either the board is clean or nothing changed.
    stmts = ";".join(f'DELETE FROM "{t}"' for t in present)
    psql(f"BEGIN; {stmts}; COMMIT;")
    print("  done")

    if do_hermes and h_counts:
        print("Deleting Hermes queue content …")
        with sqlite3.connect(HERMES_DB) as conn:
            for table in HERMES_CONTENT_TABLES:
                if table in h_counts:
                    conn.execute(f'DELETE FROM "{table}"')
            conn.commit()
            conn.execute("VACUUM")
        print("  done")

    print("\n=== verification ===")
    ok = True
    for table, n in multica_counts().items():
        if n > 0:
            print(f"  FAIL  multica.{table} still has {n} rows")
            ok = False
    for table, n in (hermes_counts() if do_hermes else {}).items():
        if n > 0:
            print(f"  FAIL  hermes.{table} still has {n} rows")
            ok = False
    # Configuration must have SURVIVED — an empty board is the goal, an empty
    # install is a disaster.
    for table in ("workspace", "agent", "agent_runtime", "autopilot"):
        n = int(psql(f'SELECT count(*) FROM "{table}";') or 0)
        print(f"  {'PASS' if n > 0 else 'FAIL'}  preserved {table}: {n} rows")
        ok = ok and n > 0
    print("\nBOARDS RESET" if ok else "\nRESET INCOMPLETE — inspect above")
    print(f"Restore if needed: docker exec -i {DB_CONTAINER} psql -U zoe -d {MULTICA_DB} "
          f"< {BACKUP_DIR}/multica-{stamp}.sql")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
