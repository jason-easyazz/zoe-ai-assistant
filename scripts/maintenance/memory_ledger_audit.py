#!/usr/bin/env python3
"""Reconcile the memory audit trail against the palace, and list test pollution. READ-ONLY.

Why: on 2026-10-05 the memory-fidelity audit reported "93 of 114 owner ingests since 09-20 are no
longer in the palace, with no deletion record". The reconciliation below (a) classifies every
audit-recorded ingest into exactly one bucket and (b) lists drawers whose text is a literal from
the repo's own tests/scripts under a REAL user id, so the operator can retire them through the
normal review path. Findings and root causes: docs/knowledge/memory-loss-audit-2026-10-05.md.

Safety contract (pinned by tests/test_memory_ledger_audit.py):
  * The palace is opened with ``sqlite3 mode=ro``. No chromadb import, no write, no VACUUM.
  * There is NO purge here. ``--plan-archive FILE`` writes a JSON plan of row ids; applying it
    is the operator's step through the normal review/forget path (``POST /api/memories/{id}/review``
    with ``decision=archive``) — never a raw delete.
  * Output is COUNTS and short HASHES only. No memory text is printed, written or logged.

Buckets (every ledger id lands in exactly one):
  present              the drawer exists (``present_status`` counted separately)
  test_session_gone    gone, a test-shaped session id (sess-/test-/probe-...), too few events to prove
                       it never persisted: a test row, presence unknown
  recorded_removal     gone, but an archive/edit/delete_user audit row accounts for it
  phantom_audit_only   gone, and the same id was "ingested" again and again while approved — the
                       durable dedup (memory_service.ingest) makes that impossible for a row that
                       persisted, so the rows never reached THIS palace (a test wrote the audit
                       here and its drawers elsewhere)
  lost_recoverable     gone with no record, but present in a ``--reference`` backup/export
  unexplained          gone with no record and no reference copy

Usage:
  python3 scripts/maintenance/memory_ledger_audit.py            # dry run, human summary
  python3 scripts/maintenance/memory_ledger_audit.py --json
  python3 scripts/maintenance/memory_ledger_audit.py --since 2026-08-15 --owner jason \\
      --reference ~/.zoe/palace-backups/mempalace-drawers-export-20261004-085708.json \\
      --reference ~/.zoe-backups/jason-mem-20260705-184122.json
  python3 scripts/maintenance/memory_ledger_audit.py --plan-archive /tmp/plan.json
"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import json
import os
import re
import sqlite3
import sys

DEFAULT_DB = "~/.mempalace/chroma.sqlite3"
DRAWERS = "mempalace_drawers"
AUDIT = "mempalace_audit"
DOC_KEY = "chroma:document"
# Mirrors services/zoe-data/user_filters.SYNTHETIC_USER_RE (scripts cannot import service code).
SYNTHETIC_RE = re.compile(r"^(test|probe|demo|ci|e2e|bench)[-_]", re.IGNORECASE)
GUEST_IDS = ("guest", "anonymous", "voice-guest", "voice-daemon", "")
# A real conversation leaves a channel-shaped session id; a unit test does not.
REAL_SESSION_RE = re.compile(r"^(telegram|voice-panel|web|ask|livekit|panel)[-_]", re.IGNORECASE)
TEST_SESSION_RE = re.compile(r"^(sess|test|probe|diag|demo)[-_]", re.IGNORECASE)
PHANTOM_MIN_EVENTS = 3          # an approved row cannot be re-ingested: 3+ events = never persisted
BATCH_MIN_IDS = 8               # >= this many distinct ids ingested in ONE minute = a suite run
BATCH_FRACTION = 0.8            # ...and this share of an id's events fall in such minutes
MIN_LITERAL_LEN = 12            # shorter strings collide with real facts by chance


def h(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:10]


def connect_ro(path: str) -> sqlite3.Connection:
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        raise SystemExit(f"memory ledger audit: no such database: {path}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _segment(conn: sqlite3.Connection, collection: str) -> str | None:
    row = conn.execute(
        "SELECT s.id FROM segments s JOIN collections c ON c.id = s.collection "
        "WHERE c.name = ? AND s.scope = 'METADATA'", (collection,)).fetchone()
    return row[0] if row else None


def load_collection(conn: sqlite3.Connection, collection: str) -> list[dict]:
    """Every row of a chroma collection as ``{"eid": ..., <metadata>..., "chroma:document": ...}``."""
    seg = _segment(conn, collection)
    if not seg:
        return []
    rows: dict[int, dict] = {}
    for rid, eid in conn.execute("SELECT id, embedding_id FROM embeddings WHERE segment_id = ?", (seg,)):
        rows[rid] = {"eid": eid}
    q = ("SELECT m.id, m.key, m.string_value, m.int_value, m.float_value, m.bool_value "
         "FROM embedding_metadata m JOIN embeddings e ON e.id = m.id WHERE e.segment_id = ?")
    for rid, key, sv, iv, fv, bv in conn.execute(q, (seg,)):
        rows[rid][key] = sv if sv is not None else iv if iv is not None else fv if fv is not None else bv
    return list(rows.values())


def reference_ids(path: str) -> set[str]:
    """Row ids present in a backup: a chroma sqlite, a compaction export, or a list-of-dicts export."""
    path = os.path.expanduser(path)
    if path.endswith((".sqlite3", ".sqlite", ".db")):
        return {r["eid"] for r in load_collection(connect_ro(path), DRAWERS)}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and isinstance(data.get("ids"), list):
        return {str(i) for i in data["ids"]}
    found: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("id", "mem_id", "mempalace_id") and isinstance(v, str):
                    found.add(v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return found


def repo_literals(repo: str) -> dict[str, list[str]]:
    """String literals (>= MIN_LITERAL_LEN chars) of the repo's tests and scripts -> files."""
    out: dict[str, set[str]] = collections.defaultdict(set)
    for base in ("services/zoe-data/tests", "tests", "scripts", "services/zoe-data/scripts"):
        root = os.path.join(repo, base)
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        tree = ast.parse(fh.read())
                except (OSError, SyntaxError, ValueError):
                    continue
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                            and MIN_LITERAL_LEN <= len(node.value.strip()) <= 400):
                        out[node.value.strip()].add(os.path.relpath(path, repo))
    return {k: sorted(v) for k, v in out.items()}


def _json(s: str | None) -> dict:
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def reconcile(drawers: list[dict], audit: list[dict], *, owner: str, since: str,
              references: dict[str, set[str]], literals: dict[str, list[str]]) -> dict:
    live = {r["eid"]: r for r in drawers}
    by_id: dict[str, list[dict]] = collections.defaultdict(list)
    for a in audit:
        by_id[a.get("mempalace_id", "")].append(a)

    # Suite-run batches: minutes in which >= BATCH_MIN_IDS distinct owner ids were "ingested" at once.
    minute_ids: dict[str, set[str]] = collections.defaultdict(set)
    for a in audit:
        if a.get("user_id") == owner and a.get("action") == "ingest":
            minute_ids[str(a.get("timestamp", ""))[:16]].add(a.get("mempalace_id", ""))
    batch_minutes = {m for m, v in minute_ids.items() if len(v) >= BATCH_MIN_IDS}

    def batch_share(mid: str) -> float:
        ev = [e for e in by_id[mid] if e.get("action") == "ingest"]
        return sum(1 for e in ev if str(e.get("timestamp", ""))[:16] in batch_minutes) / len(ev) if ev else 0.0

    ledger = [a for a in audit if a.get("user_id") == owner and a.get("action") == "ingest"
              and str(a.get("timestamp", "")) >= since]
    ids = sorted({a["mempalace_id"] for a in ledger})
    events_per_id = collections.Counter(a["mempalace_id"] for a in ledger)
    first_text: dict[str, str] = {}
    for a in sorted(ledger, key=lambda a: a["timestamp"]):
        first_text.setdefault(a["mempalace_id"], str(_json(a.get("after")).get("text", "")))

    buckets: dict[str, list[str]] = collections.defaultdict(list)
    table: list[dict] = []
    for mid in ids:
        all_events = by_id[mid]
        total_ingests = sum(1 for e in all_events if e.get("action") == "ingest")
        recorded = [e for e in all_events if e.get("action") in ("archive", "edit", "delete_user")
                    and (e.get("action") != "edit" or _json(e.get("before")).get("id") != _json(e.get("after")).get("id"))]
        text = first_text.get(mid, "")
        if mid in live:
            bucket = "present"
        elif recorded:
            bucket = "recorded_removal"
        elif total_ingests >= PHANTOM_MIN_EVENTS and (
                _json(next((e["after"] for e in all_events if e.get("action") == "ingest"), "{}")
                      ).get("status", "approved") == "approved"
                or batch_share(mid) >= BATCH_FRACTION):
            # approved rows cannot be re-ingested (durable dedup), pending ones can — for those
            # the evidence is that nearly every event sits inside a many-ids-at-once suite batch
            bucket = "phantom_audit_only"
        elif TEST_SESSION_RE.match(str(_json(next(
                (e["after"] for e in all_events if e.get("action") == "ingest"), "{}")).get("session_id", ""))):
            bucket = "test_session_gone"   # test-shaped session id; too few events to prove it never persisted
        else:
            refs = [name for name, ids_ in references.items() if mid in ids_]
            bucket = "lost_recoverable" if refs else "unexplained"
        buckets[bucket].append(mid)
        table.append({
            "id": mid, "bucket": bucket, "events_in_window": events_per_id[mid],
            "events_all_time": total_ingests, "text_sha": h(text), "text_len": len(text),
            "repo_literal": text.strip() in literals,
            "reference": sorted(n for n, ids_ in references.items() if mid in ids_) if bucket.startswith(("lost", "unex")) else [],
        })
    event_counts = {b: sum(events_per_id[m] for m in ms) for b, ms in buckets.items()}
    return {
        "owner": owner, "since": since,
        "ledger_events": len(ledger), "ledger_ids": len(ids),
        "buckets": {b: {"ids": len(ms), "events": event_counts[b]} for b, ms in sorted(buckets.items())},
        "missing_events": sum(v for b, v in event_counts.items() if b != "present"),
        "missing_events_literal": sum(
            events_per_id[t["id"]] for t in table if t["bucket"] != "present" and t["repo_literal"]),
        "table": table,
    }


def reference_gaps(drawers: list[dict], audit: list[dict], references: dict[str, set[str]]) -> dict:
    """Per backup: rows it holds that the palace does not, split by whether ANY audit row accounts
    for the removal (archive/edit/delete_user) — the unrecorded ones are the true-loss candidates."""
    live = {r["eid"] for r in drawers}
    removal: set[str] = set()
    for a in audit:
        act = a.get("action")
        if act in ("archive", "delete_user"):
            removal.add(a.get("mempalace_id", ""))
        elif act == "edit":
            before, after = _json(a.get("before")), _json(a.get("after"))
            if before.get("id") != after.get("id"):   # a no-op in-place rewrite retires nothing
                removal.add(str(before.get("id", "")))
    out = {}
    for name, ids_ in references.items():
        gone = sorted(i for i in ids_ if i not in live)
        recorded = [i for i in gone if i in removal]
        out[name] = {"rows": len(ids_), "absent_now": len(gone), "recorded_removal": len(recorded),
                     "unrecorded": len(gone) - len(recorded),
                     "unrecorded_id_sha": sorted(h(i) for i in gone if i not in removal)[:50]}
    return out


def literal_rows(drawers: list[dict], literals: dict[str, list[str]]) -> list[dict]:
    """Drawers under a REAL user id whose text is a repo test/script literal."""
    out = []
    for r in drawers:
        uid = str(r.get("user_id") or r.get("wing") or "")
        text = str(r.get(DOC_KEY) or "").strip()
        if uid in GUEST_IDS or SYNTHETIC_RE.match(uid) or text not in literals:
            continue
        sess = str(r.get("session_id") or "")
        out.append({
            "id": r["eid"], "user_id": uid, "status": r.get("status"), "source": r.get("source"),
            "text_sha": h(text), "files": literals[text][:3],
            # a channel-shaped session id means a person said it: keep, a literal that merely
            # copies a real utterance. No session / a bare one is test-shaped.
            "verdict": "likely_real_utterance_copied_into_a_test" if REAL_SESSION_RE.match(sess)
            else "likely_test_row",
        })
    return out


def population(drawers: list[dict], audit: list[dict]) -> dict:
    users = collections.Counter((r.get("user_id"), r.get("status")) for r in drawers)
    edits = [a for a in audit if a.get("action") == "edit"]
    noop = sum(1 for a in edits if _json(a.get("before")).get("id") == _json(a.get("after")).get("id"))
    odd_ids = sum(1 for a in audit if a.get("mempalace_id") and not str(a["mempalace_id"]).startswith(
        ("zoe_", "delete_user:")))
    return {
        "approved_by_user": {u: n for (u, s), n in sorted(users.items(), key=lambda kv: str(kv[0])) if s == "approved"},
        "guest_approved": sum(n for (u, s), n in users.items() if s == "approved" and u in GUEST_IDS),
        "audit_edit_rows": len(edits), "audit_edit_rows_noop_same_id": noop,
        "audit_rows_with_non_row_ids": odd_ids,
        "audit_rows_by_action": dict(collections.Counter(a.get("action") for a in audit)),
    }


def render(report: dict) -> str:
    L = report["ledger"]
    lines = [f"Ledger: owner={L['owner']} since={L['since']}: {L['ledger_events']} ingest events over "
             f"{L['ledger_ids']} distinct rows; {L['missing_events']} events point at rows no longer present "
             f"({L['missing_events_literal']} of them equal a repo test/script literal)."]
    for b, v in L["buckets"].items():
        lines.append(f"  {b:20s} ids={v['ids']:4d} events={v['events']:6d}")
    for name, g in report["reference_gaps"].items():
        lines.append(f"Reference {name}: {g['rows']} rows, {g['absent_now']} absent now "
                     f"({g['recorded_removal']} recorded, {g['unrecorded']} UNRECORDED)")
    lines.append(f"Test-literal drawers under real ids: {len(report['literal_rows'])}")
    for r in report["literal_rows"]:
        lines.append(f"  {r['id'][-12:]} user={r['user_id']} status={r['status']} source={r['source']} "
                     f"sha={r['text_sha']} {r['verdict']}")
    p = report["population"]
    lines.append(f"Population: guest approved={p['guest_approved']} audit edits={p['audit_edit_rows']} "
                 f"(no-op same-id rewrites={p['audit_edit_rows_noop_same_id']}) "
                 f"audit rows with non-row ids={p['audit_rows_with_non_row_ids']}")
    lines.append("DRY RUN: nothing was written. Retire rows via the normal review path "
                 "(--plan-archive FILE writes the id list).")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--since", default="2026-08-15")
    ap.add_argument("--owner", default="jason")
    ap.add_argument("--repo", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    ap.add_argument("--reference", action="append", default=[], help="backup/export to look for gone rows in")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--plan-archive", metavar="FILE",
                    help="write the test-shaped literal rows as a JSON archive plan (no execution here)")
    args = ap.parse_args(argv)

    conn = connect_ro(args.db)
    try:
        drawers, audit = load_collection(conn, DRAWERS), load_collection(conn, AUDIT)
    finally:
        conn.close()
    refs = {os.path.basename(p): reference_ids(p) for p in args.reference}
    literals = repo_literals(args.repo)
    report = {
        "ledger": reconcile(drawers, audit, owner=args.owner, since=args.since, references=refs, literals=literals),
        "literal_rows": literal_rows(drawers, literals),
        "reference_gaps": reference_gaps(drawers, audit, refs),
        "population": population(drawers, audit),
    }
    if args.plan_archive:
        plan = [{"id": r["id"], "user_id": r["user_id"], "decision": "archive",
                 "note": "test-literal audit: row text equals a repo test/script literal"}
                for r in report["literal_rows"] if r["verdict"] == "likely_test_row"]
        with open(args.plan_archive, "w", encoding="utf-8") as fh:
            json.dump({"how_to_apply": "POST /api/memories/{id}/review {decision: archive} per entry; "
                                       "never raw delete", "rows": plan}, fh, indent=2)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
