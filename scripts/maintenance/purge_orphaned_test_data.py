#!/usr/bin/env python3
"""Soft-delete orphaned test/junk data left in the calendar + lists by earlier
testing (the kiosk `guest` account, throwaway `test-sec-b-*` security-test users,
plus — defensively — the timestamped throwaway owners the retired ad-hoc smoke
scripts tried to mint). These rows are family-visible so they show on the panel,
but no API session authenticates as their owner, so they can't be cleared via
the UI.

Scope (deliberately narrow — never touches a real household user's data):
  * events    : owner matches TEST_OWNER_PRED
  * list_items: rows whose owning list's owner matches TEST_OWNER_PRED

See `owner_pred()` for the exact predicate and its safety argument. The patterns
are pinned by tests/unit/test_purge_predicates.py so they cannot silently drift.

It only sets deleted = 1 (reversible; the panel already hides deleted rows) and
bumps updated_at so audit/sync paths that key off it don't miss the change.
Contacts (people) are intentionally NOT touched.

Usage (run on the zoe-data host):
    python3 scripts/maintenance/purge_orphaned_test_data.py            # dry-run (counts only)
    python3 scripts/maintenance/purge_orphaned_test_data.py --execute  # apply (asks to confirm)
    python3 scripts/maintenance/purge_orphaned_test_data.py --execute --yes \
        --expect-db zoe --expect-host localhost  # non-interactive

POSTGRES_URL is read from the environment; if unset it is loaded from the
zoe-data service .env (same file the service uses), so the documented command
works without manually sourcing anything.
"""
import argparse
import asyncio
import os
import sys
from urllib.parse import urlsplit

import asyncpg

# Owner ids the retired ad-hoc smoke scripts (tests/{integration,e2e}/
# test_comprehensive.py, test_simple.py — removed; see git history) TRIED to mint,
# as f"<prefix>_{int(time.time())}" passed via `?user_id=`.
#
# DEFENCE-IN-DEPTH, not a live cleanup: as of 2026-07-16 zero rows in the
# household DB carry these owners, because zoe-data ignores the unauthenticated
# `?user_id=` override (token-gated, #1054) and attributes the writes to `guest`
# instead — which the 'guest' arm below already covers. These patterns exist so
# that any row that ever DID land under a timestamped throwaway owner (an older
# build, or a run holding an override token) is still sweepable. If you are
# adding a new pattern here, verify against the DB first: the last audit's
# premise that `test_calendar_*` rows existed did not survive contact with it.
#
# SAFETY — why no real household user can match:
#   * fully anchored (^...$): no substring/prefix matches;
#   * the prefix comes from a CLOSED enumerated set of 6 literals, each one a
#     dead script's local variable name (test_calendar / test_shopping /
#     test_memory / test_isolation_a|b / final_test[_2]) — not a name shape any
#     human account uses;
#   * the suffix must be `_` + >=9 PURE digits (a unix timestamp). Real owners
#     ('jason', family names, 'zoe-touch-pi') carry no digit-timestamp suffix.
# A real user would have to be literally named e.g. "test_memory_1752624000" to
# match. Regex, not LIKE, precisely so the digit suffix is enforced — LIKE
# 'test_memory_%' would also match a hypothetical "test_memory_notes" list owner.
#
# Kept identical in Python `re` and PostgreSQL `~` syntax (plain anchors, a
# non-capturing-equivalent alternation and a bounded digit class are portable
# across both), so tests/unit/test_purge_predicates.py can assert on it directly.
TEST_OWNER_RE = (
    r"^(test_calendar|test_shopping|test_memory|test_isolation_[ab]|final_test(_2)?)_[0-9]{9,}$"
)


def owner_pred(col: str = "user_id") -> str:
    """The owner-scoping predicate, bound to `col` (e.g. 'user_id', 'l.user_id').

    Parameterised by column rather than string-replaced at the call site. The
    previous `LIST_OWNER_PRED.replace('user_id', 'l.user_id')` did blind string
    surgery on SQL: it rewrites every occurrence of the substring, including any
    inside a quoted VALUE. Today's literals happen not to contain 'user_id', so
    it produced correct SQL by luck — a future owner pattern that did (say an id
    like 'user_id_backfill') would be silently corrupted into a predicate that
    matches something else. Binding the column explicitly removes the trap.
    """
    return (
        f"({col} = 'guest' "
        f"OR {col} LIKE 'test-sec-b-%' "
        f"OR {col} ~ '{TEST_OWNER_RE}')"
    )


EVENT_PRED = owner_pred("user_id")
LIST_OWNER_PRED = owner_pred("user_id")

# Sibling PostgreSQL tooling reads the same service env file.
# Candidate .env locations: this checkout's, then the LIVE checkout's. The CI
# runner executes from its own workdir (actions-runner/_work/…) which carries
# no .env — the post-suite sweep silently exited 2 there and the test junk
# survived (operator's 2026-07-13 dentist-spam recurrence).
_SERVICE_ENVS = [
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "services", "zoe-data", ".env",
    ),
    os.path.expanduser("~/assistant/services/zoe-data/.env"),
]


def _resolve_dsn() -> str:
    """POSTGRES_URL from the environment, else from the zoe-data service .env
    (this checkout's, else the live checkout's)."""
    dsn = os.environ.get("POSTGRES_URL", "")
    if dsn:
        return dsn
    for env_path in _SERVICE_ENVS:
        try:
            with open(env_path) as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("POSTGRES_URL="):
                        return line[len("POSTGRES_URL="):].strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def _redacted_target(dsn: str) -> str:
    """host:port/dbname (as user) with the password never printed."""
    p = urlsplit(dsn)
    who = p.username or "?"
    host = p.hostname or "?"
    port = f":{p.port}" if p.port else ""
    db = (p.path or "/").lstrip("/") or "?"
    return f"{db} on {host}{port} (as {who})"


async def main(execute: bool, assume_yes: bool, expect_db: str, expect_host: str) -> int:
    dsn = _resolve_dsn()
    if not dsn:
        print(
            "POSTGRES_URL is not set and could not be read from the service .env "
            f"({_SERVICE_ENVS}). Run this on the zoe-data host.",
            file=sys.stderr,
        )
        return 2

    target = _redacted_target(dsn)
    parts = urlsplit(dsn)
    target_db = (parts.path or "/").lstrip("/")
    target_host = parts.hostname or ""
    target_port = parts.port or 5432
    target_hostport = f"{target_host}:{target_port}"
    print(f"Target database: {target}")

    # Non-interactive runs must positively assert the FULL target — db name,
    # host and port. A name alone ("zoe"), or host alone, could match a
    # different PostgreSQL instance (e.g. localhost:5433 vs :5432). --yes only
    # skips the interactive prompt, never these checks. --expect-host accepts
    # "host" (default port 5432) or "host:port".
    if execute and assume_yes:
        # DSN query options (host, hostaddr, port, dbname, …) can override the
        # effective connection target in ways the URL authority does not
        # reflect. Rather than track libpq's option set and precedence rules,
        # refuse non-interactive execution whenever the DSN carries ANY query
        # string — the zoe-data service DSN has none, and the interactive path
        # (which prints the parsed target and asks) remains available.
        if parts.query:
            print(
                "\nRefusing non-interactive --yes: POSTGRES_URL carries query options, "
                "which can override the connection target in ways this assertion "
                "cannot verify. Run interactively instead.",
                file=sys.stderr,
            )
            return 2
        if not expect_db or not expect_host:
            print(
                "\nRefusing non-interactive --yes without a full target assertion: pass "
                f"--expect-db {target_db!r} --expect-host {target_hostport!r} to assert the intended target.",
                file=sys.stderr,
            )
            return 2
        expect_hostport = expect_host if ":" in expect_host else f"{expect_host}:5432"
        if expect_db != target_db or expect_hostport != target_hostport:
            print(
                f"\nTarget mismatch: resolved target is {target_db!r} on {target_hostport!r} but the "
                f"assertion is {expect_db!r} on {expect_hostport!r}. Aborting — nothing changed.",
                file=sys.stderr,
            )
            return 2

    conn = await asyncpg.connect(dsn)
    try:
        ev_n = await conn.fetchval(
            f"SELECT count(*) FROM events WHERE {EVENT_PRED} AND deleted = 0"
        )
        # by owner+title, so the operator can eyeball exactly what will go
        rows = await conn.fetch(
            f"SELECT user_id, title, count(*) c FROM events "
            f"WHERE {EVENT_PRED} AND deleted = 0 GROUP BY user_id, title ORDER BY c DESC LIMIT 20"
        )
        li_n = await conn.fetchval(
            "SELECT count(*) FROM list_items i JOIN lists l ON i.list_id = l.id "
            f"WHERE {owner_pred('l.user_id')} AND i.deleted = 0"
        )
        print(f"events to soft-delete : {ev_n}")
        for r in rows:
            print(f"    {r['c']:>4}  {r['user_id']:<20} {r['title']!r}")
        print(f"list_items to soft-delete: {li_n}")

        if not execute:
            print("\nDRY-RUN — nothing changed. Re-run with --execute to apply.")
            return 0

        if ev_n == 0 and li_n == 0:
            print("\nNothing to do.")
            return 0

        if not assume_yes:
            print(f"\nAbout to soft-delete {ev_n} events + {li_n} list_items in: {target}")
            reply = input("Type 'yes' to proceed: ").strip().lower()
            if reply != "yes":
                print("Aborted — nothing changed.")
                return 1

        async with conn.transaction():
            ev_done = await conn.execute(
                f"UPDATE events SET deleted = 1, updated_at = NOW() "
                f"WHERE {EVENT_PRED} AND deleted = 0"
            )
            li_done = await conn.execute(
                "UPDATE list_items SET deleted = 1, updated_at = NOW() "
                "WHERE deleted = 0 AND list_id IN "
                f"(SELECT id FROM lists WHERE {LIST_OWNER_PRED})"
            )
        print(f"\nAPPLIED to {target} — events: {ev_done}, list_items: {li_done}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="apply the soft-delete (default is dry-run)")
    ap.add_argument("--yes", action="store_true",
                    help="skip the interactive confirmation (automation); requires --expect-db")
    ap.add_argument("--expect-db", default="",
                    help="assert the resolved target DB name; required with --yes")
    ap.add_argument("--expect-host", default="",
                    help="assert the resolved target DB host, as host or host:port (port defaults to 5432); required with --yes")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.execute, args.yes, args.expect_db, args.expect_host)))
