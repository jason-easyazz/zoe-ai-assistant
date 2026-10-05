#!/usr/bin/env python3
"""List (and, on request, supersede) memory rows that assert the user's OWN name or home
and CONFLICT with what the account says.

Why (2026-10-05): "whats my name" was answered with a full name that belongs to nobody
in the household. The row was written by the NIGHTLY DIGEST — its fact-extractor read a
day transcript holding a speech-to-text fragment that named a third person, asserted it
as the user's name, and its contradiction pass superseded the genuine name fact with it
(``MemoryService.review(edit)`` carries the old row's source/session forward, so it looked
like a regex/Telegram write). The writers are now walled off
(``identity_facts.AUTOMATIC_SOURCES``) and own-identity questions are answered from the
account (``identity_facts``); this tool finds the rows that were already stored.

Account side (never memory): ``auth_users`` / ``users`` / ``user_preferences`` /
``weather_preferences`` / ``system_preferences`` via ``psql`` (default: the zoe-database
container) — the same sources ``identity_facts.build_identity`` uses.
Memory side: the palace's ``chroma.sqlite3``, opened READ-ONLY (``mode=ro``).

Usage
  # read-only report; safe while zoe-data runs. ``--dry-run`` is required so intent is explicit
  python3 scripts/maintenance/identity_memory_audit.py --dry-run
  python3 scripts/maintenance/identity_memory_audit.py --dry-run --user jason --json
  python3 scripts/maintenance/identity_memory_audit.py --dry-run --redact     # mask the row text

  # supersede the conflicting NAME rows (operator-run, after reading the report):
  systemctl --user stop zoe-data
  ~/.zoe/venvs/zoe-data-py312/bin/python scripts/maintenance/identity_memory_audit.py \\
      --purge --i-have-reviewed --i-stopped-zoe-data [--user jason] [--only <memory id> ...]
  systemctl --user start zoe-data

``--purge`` goes through ``MemoryService.review(decision="edit")`` — the normal supersede
path: the conflicting row becomes ``status=superseded`` with ``superseded_by_id``, and a
fresh ``User's name is <account name>.`` row replaces it. NOTHING is raw-deleted, and the
old row stays in the palace for audit/rollback. HOME rows are listed but never purged by
this tool: a stated home may be legitimate (moved house) and the account location may be
the stale side — a person decides.

The output names household members: keep it on the box, do not paste it into PRs.

Exit codes: 0 = report printed / purge done, 2 = refused (missing acknowledgements, no
--dry-run, or the data could not be read).
"""
from __future__ import annotations

import argparse
import json
import shlex
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))
import identity_facts as idf  # noqa: E402

DEFAULT_PSQL = "docker exec zoe-database psql -U zoe -d zoe"
DEFAULT_PALACE = "~/.mempalace"
DRAWERS = "mempalace_drawers"

ACCOUNTS_SQL = (
    "select coalesce(json_agg(t), '[]'::json) from ("
    "select a.user_id, a.username, a.settings, u.name as users_name, p.prefs, "
    "w.city as wp_city, w.country as wp_country, w.use_current_location as wp_current "
    "from auth_users a left join users u on u.id = a.user_id "
    "left join user_preferences p on p.user_id = a.user_id "
    "left join weather_preferences w on w.user_id = a.user_id "
    "where coalesce(a.is_active, 1) <> 0 order by a.user_id) t"
)
SYSLOC_SQL = "select value from system_preferences where key = 'weather_default_location'"


def _psql(psql_cmd: str, sql: str) -> str:
    return subprocess.run(
        [*shlex.split(psql_cmd), "-Atc", sql], capture_output=True, text=True, timeout=60, check=True
    ).stdout.strip()


def load_identities(psql_cmd: str, only_user: Optional[str] = None,
                    accounts_file: Optional[str] = None) -> dict[str, idf.Identity]:
    """``{user_id: Identity}`` for every registered account, built by the SAME pure
    function the live tier uses. ``accounts_file`` (JSON: ``{"accounts": [...], "sysloc": {}}``)
    is the offline/test input."""
    if accounts_file:
        doc = json.loads(Path(accounts_file).read_text())
        rows, sysloc = doc.get("accounts", []), doc.get("sysloc") or {}
    else:
        rows = json.loads(_psql(psql_cmd, ACCOUNTS_SQL) or "[]")
        sysloc = idf._json_dict(_psql(psql_cmd, SYSLOC_SQL) or "{}")
    out: dict[str, idf.Identity] = {}
    for r in rows:
        uid = str(r.get("user_id") or "")
        if not uid or (only_user and uid != only_user):
            continue
        ident = idf.build_identity(
            uid,
            username=str(r.get("username") or ""),
            settings=idf._json_dict(r.get("settings")),
            users_name=str(r.get("users_name") or ""),
            prefs=idf._json_dict(r.get("prefs")),
            city=str(r.get("wp_city") or ""),
            country=str(r.get("wp_country") or ""),
            use_current_location=bool(r.get("wp_current")),
            sysloc=sysloc,
        )
        if ident is not None:
            out[uid] = ident
    return out


def load_memory_rows(palace: str) -> list[dict[str, Any]]:
    """Approved drawers rows ``{id, user_id, text, meta}`` — read-only sqlite."""
    db = Path(palace).expanduser() / "chroma.sqlite3"
    if not db.exists():
        raise SystemExit(f"identity audit: no such palace database: {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        seg_ids = [r[0] for r in con.execute(
            "SELECT s.id FROM segments s JOIN collections c ON c.id = s.collection "
            "WHERE c.name = ? AND s.scope = 'METADATA'", (DRAWERS,))]
        rows: list[dict[str, Any]] = []
        for seg in seg_ids:
            for rid, eid in con.execute("SELECT id, embedding_id FROM embeddings WHERE segment_id = ?", (seg,)):
                meta: dict[str, Any] = {}
                for k, s, i, f in con.execute(
                    "SELECT key, string_value, int_value, float_value FROM embedding_metadata WHERE id = ?", (rid,)
                ):
                    meta[k] = s if s is not None else (i if i is not None else f)
                text = str(meta.pop("chroma:document", "") or "")
                if str(meta.get("status") or "") != "approved":
                    continue
                rows.append({"id": eid, "user_id": str(meta.get("user_id") or meta.get("wing") or ""),
                             "text": text, "meta": meta})
        return rows
    finally:
        con.close()


def find_conflicts(identities: dict[str, idf.Identity], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pure: the rows that assert the user's own name/home against the account."""
    found: list[dict[str, Any]] = []
    for row in rows:
        ident = identities.get(row["user_id"])
        if ident is None:
            continue
        kind, asserted, review = "", "", False
        a_name = idf.asserted_user_name(row["text"])
        verdict = idf.classify_name_assertion(a_name, ident, row["meta"]) if a_name else "match"
        if verdict != "match":
            # needs_review = a plausible nickname or an explicit user teach: listed, never purged
            kind, asserted, review = "name", a_name, verdict == "needs_review"
        else:
            a_home = idf.asserted_user_home(row["text"])
            if a_home and idf.home_conflicts(a_home, ident):
                kind, asserted = "home", a_home
        if not kind:
            continue
        m = row["meta"]
        found.append({
            "id": row["id"], "user_id": row["user_id"], "kind": kind, "review": review,
            "text": row["text"],
            "asserted": asserted, "account_name": ident.account_name, "account_home": ident.city_region,
            "source": m.get("source", ""), "added_by": m.get("added_by", ""),
            "reviewed_by": m.get("reviewed_by", ""), "session_id": m.get("session_id", ""),
            "added_at": m.get("added_at", ""), "supersedes_id": m.get("supersedes_id", ""),
            "review_note": m.get("review_note", ""),
        })
    return sorted(found, key=lambda c: (c["user_id"], c["kind"], c["id"]))


def render(conflicts: list[dict[str, Any]], redact: bool) -> str:
    if not conflicts:
        return "No memory row asserts an identity that conflicts with the account."
    lines = [f"{len(conflicts)} conflicting identity row(s). Names are NOT purged unless --purge "
             "--i-have-reviewed; home rows and NEEDS REVIEW rows are never purged by this tool.", ""]
    for n, c in enumerate(conflicts, 1):
        text = "<redacted>" if redact else c["text"]
        tag = "  NEEDS REVIEW (possible nickname / explicit teach - never purged)" if c["review"] else ""
        lines.append(f"[{n}] user={c['user_id']} kind={c['kind']} id={c['id']}{tag}")
        lines.append(f"    row:      {text}")
        lines.append(f"    account:  {'<redacted>' if redact else (c['account_name'] if c['kind'] == 'name' else c['account_home'])}")
        lines.append(f"    written:  source={c['source'] or '-'} added_by={c['added_by'] or '-'} "
                     f"reviewed_by={c['reviewed_by'] or '-'} at={c['added_at'] or '-'}")
        lines.append(f"    session:  {c['session_id'] or '-'}   supersedes={c['supersedes_id'] or '-'}")
        if c["review_note"]:
            lines.append(f"    note:     {c['review_note']}")
    return "\n".join(lines)


def _service_active(unit: str = "zoe-data") -> bool:
    """``systemctl --user is-active`` says active. A missing systemctl / unreachable user
    manager counts as 'not active' (the port probe still guards)."""
    try:
        out = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return out in ("active", "activating", "reloading")


def _service_listening(host: str = "127.0.0.1", port: int = 8000) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def purgeable(c: dict[str, Any]) -> bool:
    return c["kind"] == "name" and not c["review"]


def validate_only(conflicts: list[dict[str, Any]], only: set[str]) -> Optional[str]:
    """An error message when any ``--only`` id is not a purgeable row of THIS report (a typo
    must not silently purge nothing - or the wrong thing), else None."""
    by_id = {c["id"]: c for c in conflicts}
    for mid in sorted(only):
        c = by_id.get(mid)
        if c is None:
            return f"--only {mid}: not a conflicting identity row in this report"
        if not purgeable(c):
            return f"--only {mid}: kind={c['kind']}{' (needs review)' if c['review'] else ''} is never purged by this tool"
    return None


async def purge(conflicts: list[dict[str, Any]], identities: dict[str, idf.Identity], *,
                only: Optional[set[str]] = None, svc=None) -> list[dict[str, Any]]:
    """Supersede the conflicting NAME rows through ``MemoryService.review(edit)``."""
    if svc is None:
        from memory_service import get_memory_service

        svc = get_memory_service()
    results = []
    for c in conflicts:
        if not purgeable(c) or (only and c["id"] not in only):
            continue
        ident = identities[c["user_id"]]
        try:
            ref = await svc.review(
                c["id"], decision="edit", actor="identity_audit",
                edits=f"User's name is {ident.account_name}.",
                note="identity audit: conflicted with the account name",
            )
            results.append({"id": c["id"], "ok": ref is not None, "new_id": getattr(ref, "id", None)})
        except Exception as exc:  # noqa: BLE001 — report and continue
            results.append({"id": c["id"], "ok": False, "error": type(exc).__name__})
    return results


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="read-only report (required unless --purge)")
    ap.add_argument("--purge", action="store_true", help="supersede conflicting NAME rows")
    ap.add_argument("--i-have-reviewed", action="store_true", help="acknowledge you read the report")
    ap.add_argument("--i-stopped-zoe-data", action="store_true",
                    help="acknowledge zoe-data is stopped (the purge writes the palace in-process)")
    ap.add_argument("--user", help="limit to one user_id")
    ap.add_argument("--only", action="append", default=[], help="limit the purge to this memory id (repeatable)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--redact", action="store_true", help="mask row text and account names")
    ap.add_argument("--palace", default=DEFAULT_PALACE)
    ap.add_argument("--psql-cmd", default=DEFAULT_PSQL)
    ap.add_argument("--accounts-file", help="offline accounts JSON (tests)")
    args = ap.parse_args(argv)

    if not args.dry_run and not args.purge:
        print("refused: pass --dry-run (report only) or --purge --i-have-reviewed", file=sys.stderr)
        return 2
    if args.purge:
        if not args.i_have_reviewed:
            print("refused: --purge needs --i-have-reviewed (read the --dry-run report first)", file=sys.stderr)
            return 2
        if not args.i_stopped_zoe_data:
            print("refused: --purge writes the palace in-process; stop zoe-data and pass "
                  "--i-stopped-zoe-data (two writers on one chroma index corrupt it)", file=sys.stderr)
            return 2
        if _service_listening():
            print("refused: something is still listening on 127.0.0.1:8000 (zoe-data running?)", file=sys.stderr)
            return 2
        if _service_active():
            print("refused: systemctl --user says zoe-data is active (it can be up while :8000 is not "
                  "yet listening)", file=sys.stderr)
            return 2
    try:
        identities = load_identities(args.psql_cmd, args.user, args.accounts_file)
        conflicts = find_conflicts(identities, load_memory_rows(args.palace))
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"identity audit: could not read the data: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.user:
        conflicts = [c for c in conflicts if c["user_id"] == args.user]
    if args.json:
        shown = [{**c, "text": "<redacted>", "account_name": "<redacted>"} if args.redact else c for c in conflicts]
        print(json.dumps(shown, indent=2))
    else:
        print(render(conflicts, args.redact))
    if args.purge:
        import asyncio

        bad = validate_only(conflicts, set(args.only)) if args.only else None
        if bad:
            print(f"refused: {bad}; nothing was purged", file=sys.stderr)
            return 2

        done = asyncio.run(purge(conflicts, identities, only=set(args.only) or None))
        print(json.dumps({"purged": done}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
