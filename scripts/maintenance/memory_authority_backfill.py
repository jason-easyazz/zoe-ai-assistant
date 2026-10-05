#!/usr/bin/env python3
"""Report (and, on request, stamp) the ``authority`` of memory rows written before provenance.

Why (2026-10-05 -> docs/knowledge/memory-authority.md): every memory row now carries WHO said
it - ``authority`` in {user_stated, user_confirmed, inferred} plus ``origin`` / ``turn_ref`` /
``model`` - so a model inference can never supersede, archive or contradict something the
user said. Rows written before that have no stamp; the live code DERIVES one for them on read
(``memory_authority.row_authority`` -> ``legacy_authority_basis``), and this tool applies the
SAME rule in bulk so the operator can see the counts first and, if wanted, persist the stamps.

The backfill rule (one function, ``memory_authority.legacy_class_basis``; classes are ranked
operator 6 > user_confirmed 5 > user_stated 4 > user_stated_derived 3 > user_unverified 2 > model_from_turn 1 >
model_from_transcript 0, and an UNKNOWN source is rank 0):

  reviewed_by a MODEL (digest / consolidation / turn_digest / ...)  -> model_* (the text is
        that writer's whatever ``source`` was carried forward - the 2026-10-05 incident row)
  source on the user allow-list (voice_fact, chat_regex, notes, ...)-> user_stated
  source review_ui / an operator tool                               -> user_confirmed / operator
  source a model writer that read the user's turn (turn_digest,
        digest, idle_consolidation, ...)                            -> user_stated_derived only if
        the stored ``source_excerpt`` supports the text, else model_from_turn / model_from_transcript
  reviewed_by a person (approved a model row)                       -> user_confirmed
  any other source                                                  -> model_from_transcript

Usage
  # read-only report; safe while zoe-data runs (the palace's sqlite is opened mode=ro).
  # NOTHING is written and NO row text is printed - counts and writer labels only.
  python3 scripts/maintenance/memory_authority_backfill.py --dry-run [--user member-a] [--json]

  # persist the stamps (operator-run, after reading the report; zoe-data STOPPED - the apply
  # writes the palace in-process and two writers on one chroma index corrupt it):
  systemctl --user stop zoe-data
  ~/.zoe/venvs/zoe-data-py312/bin/python scripts/maintenance/memory_authority_backfill.py \\
      --apply --i-have-reviewed --i-stopped-zoe-data
  systemctl --user start zoe-data

``--apply`` writes metadata only (``authority``, ``authority_basis``, ``origin``; embeddings and
documents untouched), only to rows that carry no ``authority`` yet, and is idempotent. It is
NOT required for the wall to work - the runtime derives the same answer for unstamped rows.

Exit codes: 0 = report printed / applied, 2 = refused (missing flags, zoe-data listening, data
unreadable).
"""
from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import memory_authority as ma  # noqa: E402

import os  # noqa: E402

DEFAULT_PALACE = os.environ.get("MEMPALACE_DATA_DIR") or "~/.mempalace"  # the service honours it too
DRAWERS = "mempalace_drawers"


def load_rows(palace: str) -> list[dict[str, Any]]:
    """Every drawers row ``{id, user_id, text, meta}`` - read-only sqlite, any status."""
    db = Path(palace).expanduser() / "chroma.sqlite3"
    if not db.exists():
        raise SystemExit(f"memory authority backfill: no such palace database: {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        seg_ids = [r[0] for r in con.execute(
            "SELECT s.id FROM segments s JOIN collections c ON c.id = s.collection "
            "WHERE c.name = ? AND s.scope = 'METADATA'", (DRAWERS,))]
        rows: list[dict[str, Any]] = []
        for seg in seg_ids:
            for rid, eid in con.execute("SELECT id, embedding_id FROM embeddings WHERE segment_id = ?", (seg,)):
                meta: dict[str, Any] = {}
                for k, s, i, f, b in con.execute(
                    "SELECT key, string_value, int_value, float_value, bool_value "
                    "FROM embedding_metadata WHERE id = ?", (rid,)
                ):
                    meta[k] = s if s is not None else (i if i is not None else (f if f is not None else b))
                text = str(meta.pop("chroma:document", "") or "")
                rows.append({"id": eid, "user_id": str(meta.get("user_id") or meta.get("wing") or ""),
                             "text": text, "meta": meta})
        return rows
    finally:
        con.close()


def classify(rows: list[dict[str, Any]], only_user: Optional[str] = None) -> dict[str, Any]:
    """Pure: the plan + counts. ``plan`` holds one ``{id, authority, basis, origin}`` per row
    that has NO stamp yet; the counts never carry text."""
    plan: list[dict[str, str]] = []
    by_authority: Counter = Counter()
    by_class: Counter = Counter()
    by_basis: Counter = Counter()
    by_source: Counter = Counter()
    by_user: Counter = Counter()
    by_status: Counter = Counter()
    incident_shaped = 0
    stamped = 0
    for row in rows:
        if only_user and row["user_id"] != only_user:
            continue
        meta, text = row["meta"], row["text"]
        status = str(meta.get("status") or "")
        by_status[status] += 1
        if str(meta.get("authority") or "") in ma.AUTHORITIES:
            stamped += 1
            continue
        cls, basis = ma.legacy_class_basis(meta, text)
        authority = ma.authority_of(cls)
        origin = str(meta.get("source") or meta.get("added_by") or "")
        plan.append({"id": row["id"], "authority": authority, "class": cls, "basis": basis,
                     "origin": origin})
        by_class[(cls, status)] += 1
        by_authority[(authority, status)] += 1
        by_basis[basis] += 1
        by_source[(origin or "-", authority)] += 1
        by_user[(row["user_id"], authority)] += 1
        # the 2026-10-05 shape: a model reviewed/edited it but a user-path label was carried forward
        if basis == "legacy_reviewed_by_model" and origin and not ma.writer_is_inferred(origin):
            incident_shaped += 1
    return {
        "rows": sum(by_status.values()), "already_stamped": stamped, "to_stamp": len(plan),
        "by_status": dict(by_status),
        "by_authority_and_status": {f"{a}/{s or '-'}": n for (a, s), n in sorted(by_authority.items())},
        "by_class_and_status": {f"{c}/{s or '-'}": n for (c, s), n in sorted(by_class.items())},
        "by_basis": dict(sorted(by_basis.items())),
        "by_source_and_authority": {f"{s}/{a}": n for (s, a), n in sorted(by_source.items())},
        "by_user_and_authority": {f"{u}/{a}": n for (u, a), n in sorted(by_user.items())},
        "protected_approved": sum(n for (a, s), n in by_authority.items()
                                  if s == "approved" and a in ma.PROTECTED),
        "incident_shaped_rows": incident_shaped,
        "plan": plan,
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        f"{report['rows']} row(s); {report['already_stamped']} already stamped; "
        f"{report['to_stamp']} would be stamped.",
        f"approved rows that would be PROTECTED (user_stated/user_confirmed): {report['protected_approved']}",
        f"incident-shaped rows (model-reviewed, user-path label carried forward): {report['incident_shaped_rows']}",
        "",
    ]
    for title, key in (("by authority/status", "by_authority_and_status"),
                       ("by class/status", "by_class_and_status"), ("by basis", "by_basis"),
                       ("by writer/authority", "by_source_and_authority"),
                       ("by user/authority", "by_user_and_authority")):
        lines.append(title + ":")
        lines += [f"  {k:<46} {n}" for k, n in report[key].items()] or ["  (none)"]
        lines.append("")
    lines.append("No row text is printed. Nothing was written (--dry-run).")
    return "\n".join(lines)


def apply_stamps(col: Any, plan: list[dict[str, str]], *, batch: int = 200) -> int:
    """Merge ``authority`` / ``authority_basis`` / ``origin`` into each planned row's metadata.
    Only rows that still carry no ``authority`` are touched (idempotent). Returns the count."""
    done = 0
    for i in range(0, len(plan), batch):
        chunk = plan[i:i + batch]
        got = col.get(ids=[p["id"] for p in chunk], include=["metadatas"])
        by_id = {rid: dict(m or {}) for rid, m in zip(got.get("ids") or [], got.get("metadatas") or [])}
        ids, metas = [], []
        for p in chunk:
            m = by_id.get(p["id"])
            if m is None or str(m.get("authority") or "") in ma.AUTHORITIES:
                continue
            m.update(authority=p["authority"], authority_class=p["class"], authority_basis=p["basis"])
            if p["origin"] and not m.get("origin"):
                m["origin"] = p["origin"]
            ids.append(p["id"])
            metas.append(m)
        if ids:
            col.update(ids=ids, metadatas=metas)
            done += len(ids)
    return done


def _service_listening(host: str = "127.0.0.1", port: int = 8000) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="read-only report (required unless --apply)")
    ap.add_argument("--apply", action="store_true", help="persist the stamps (metadata only)")
    ap.add_argument("--i-have-reviewed", action="store_true", help="acknowledge you read the report")
    ap.add_argument("--i-stopped-zoe-data", action="store_true",
                    help="acknowledge zoe-data is stopped (the apply writes the palace in-process)")
    ap.add_argument("--user", help="limit to one user_id")
    ap.add_argument("--json", action="store_true", help="machine-readable output (counts only)")
    ap.add_argument("--palace", default=DEFAULT_PALACE)
    args = ap.parse_args(argv)

    if not args.dry_run and not args.apply:
        print("refused: pass --dry-run (report only) or --apply --i-have-reviewed", file=sys.stderr)
        return 2
    if args.apply:
        if not args.i_have_reviewed:
            print("refused: --apply needs --i-have-reviewed (read the --dry-run report first)", file=sys.stderr)
            return 2
        if not args.i_stopped_zoe_data:
            print("refused: --apply writes the palace in-process; stop zoe-data and pass "
                  "--i-stopped-zoe-data (two writers on one chroma index corrupt it)", file=sys.stderr)
            return 2
        if _service_listening():
            print("refused: something is still listening on 127.0.0.1:8000 (zoe-data running?)", file=sys.stderr)
            return 2
    try:
        report = classify(load_rows(args.palace), args.user)
    except (sqlite3.Error, OSError, ValueError) as exc:
        print(f"memory authority backfill: could not read the palace: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    shown = {k: v for k, v in report.items() if k != "plan"}
    print(json.dumps(shown, indent=2) if args.json else render(report))
    if args.apply:
        from palace_client import open_palace_client

        col = open_palace_client(args.palace).get_collection(DRAWERS)
        print(json.dumps({"stamped": apply_stamps(col, report["plan"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
