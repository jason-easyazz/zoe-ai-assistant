#!/usr/bin/env python3
"""Count approved rows the conflict pass retired for a DIFFERENT person's / attribute's fact. READ-ONLY.

Why: bake-off verification X1 / X2 (docs/research/bakeoff-setup-verification-2026-10-06.md): before
``memory_supersede`` keyed on the named person and the attribute, the nightly implicit-conflict pass read
"User's friend Dana lives in Hobart" and "User's friend Leo lives in Perth" as one subject with two homes and
retired the older, and a friend's "moved to X" could retire that friend's job. The retired rows are KEPT
(``status=superseded`` + ``superseded_by_id``, never deleted), so the operator can see how many there are and
restore them. This script says how many, with the SAME matcher the fix uses (it imports
``services/zoe-data/memory_supersede``: one definition of "same subject" for the pass and for the audit).

Safety contract (pinned by tests/unit/test_memory_supersede_collateral_audit.py):
  * The palace is opened with ``sqlite3 mode=ro``. No chromadb import, no write, no VACUUM.
  * Output is COUNTS (and, with ``--plan-restore``, row ids in a local file). No memory text is printed,
    written or logged; per-row output is a short sha only.
  * Nothing is restored here. A superseded row keeps its text, so restoring is the operator's step: set it
    back to ``approved`` and clear ``superseded_by_id`` / ``invalid_at`` / ``expired_at`` on it and its
    ``supersedes_id`` on the successor (there is no review decision for it today: ``approve|reject|archive|edit``).

Buckets, per superseded row whose successor is present:
  other_person     the successor is about a different subject (owner, relation or named person) than the
                   retired row, and the retired row is about someone other than the plain user (X1)
  other_attribute  the same subject, but the two facts state different attributes (home / job / ...: X2)
  same_subject     a real change of the same thing (not collateral)
Each is split by who retired it: the implicit-conflict pass (``actor=implicit_supersede`` in the audit lane)
or any other writer.

Usage:
  python3 scripts/maintenance/memory_supersede_collateral_audit.py            # human summary
  python3 scripts/maintenance/memory_supersede_collateral_audit.py --json
  python3 scripts/maintenance/memory_supersede_collateral_audit.py --owner jason --plan-restore /tmp/restore.json
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DOC_KEY = "chroma:document"
IMPLICIT_ACTOR = "implicit_supersede"


def _load_ledger():
    """The sibling read-only sqlite reader (one definition of how the palace file is opened)."""
    spec = importlib.util.spec_from_file_location("memory_ledger_audit", os.path.join(HERE, "memory_ledger_audit.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("memory_ledger_audit", mod)
    spec.loader.exec_module(mod)
    return mod


def _matcher(repo: str):
    """``memory_supersede`` from the checkout under audit: the fix's own matcher, pure stdlib at import."""
    zd = os.path.join(repo, "services", "zoe-data")
    if zd not in sys.path:
        sys.path.insert(0, zd)
    import memory_supersede

    if not hasattr(memory_supersede, "same_subject"):
        raise SystemExit("memory supersede collateral audit: this checkout predates the named-person fix "
                         "(memory_supersede.same_subject is missing); run it from a checkout that has it")
    return memory_supersede


def h(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:10]


def _covers(ms, new_text: str, old_text: str) -> bool:
    """Does the successor still name everyone the retired row was about (a RICHER restatement: "Emily" -> "Emily,
    the user's wife and friend of ...")? Same owner, the old relations a subset, every old name matched."""
    (no, nr, nn), (oo, orr, on) = ms.subject_key(new_text), ms.subject_key(old_text)
    if no != oo or not orr <= nr:
        return False
    return all(any(ms.names_compatible([x], [y]) for y in nn) for x in on)


def classify(ms, old_text: str, new_text: str) -> str | None:
    """other_person | other_attribute | same_subject for one retired row. A retired row that is the plain
    user's own fact about nobody else, or one the successor merely ENRICHES, is never collateral."""
    owner, rels, names = ms.subject_key(old_text)
    third_party = owner != "user" or bool(rels) or bool(names)
    if not ms.same_subject(new_text, old_text):
        if _covers(ms, new_text, old_text):
            return "same_subject"
        return "other_person" if third_party or ms.subject_key(new_text)[2] else "same_subject"
    if not ms.same_attribute(new_text, old_text):
        return "other_attribute"
    return "same_subject"


def audit(drawers: list[dict], audit_rows: "list[dict] | None", ms, *, owner: str | None = None) -> dict:
    by_id = {r["eid"]: r for r in drawers}
    # audit_rows None = no audit lane (a drawers export): who retired a row is then unknown, never guessed
    implicit_ids = {a.get("mempalace_id") for a in audit_rows or []
                    if a.get("action") == "supersede" and a.get("actor") == IMPLICIT_ACTOR}
    per_user: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    rows: list[dict] = []
    for r in drawers:
        uid = str(r.get("user_id") or r.get("wing") or "")
        if owner and uid != owner:
            continue
        status = r.get("status")
        text = str(r.get(DOC_KEY) or "")
        if status == "approved":
            owner_k, rels, names = ms.subject_key(text)
            if owner_k != "user" or rels or names:
                per_user[uid]["approved_about_other_people"] += 1
            continue
        if status != "superseded":
            continue
        per_user[uid]["superseded_total"] += 1
        succ = by_id.get(str(r.get("superseded_by_id") or ""))
        if succ is None:
            per_user[uid]["successor_missing"] += 1
            continue
        kind = classify(ms, text, str(succ.get(DOC_KEY) or ""))
        if kind is None:
            continue
        via = "unaudited" if audit_rows is None else "implicit_pass" if r["eid"] in implicit_ids else "other_writer"
        per_user[uid][f"{kind}.{via}"] += 1
        if kind != "same_subject":
            rows.append({"id": r["eid"], "user_id": uid, "bucket": kind, "via": via, "text_sha": h(text)})
    total = collections.Counter()
    for c in per_user.values():
        total.update(c)
    return {
        "owner_filter": owner,
        "totals": dict(sorted(total.items())),
        "by_user": {u: dict(sorted(c.items())) for u, c in sorted(per_user.items())},
        "restorable_rows": len(rows),
        "restorable_via_implicit_pass": sum(1 for x in rows if x["via"] == "implicit_pass"),
        "rows": rows,
    }


def render(report: dict) -> str:
    t = report["totals"]
    lines = [f"Superseded rows: {t.get('superseded_total', 0)} (successor missing: {t.get('successor_missing', 0)}); "
             f"approved rows about someone other than the user: {t.get('approved_about_other_people', 0)}."]
    for kind in ("other_person", "other_attribute", "same_subject"):
        lines.append(f"  {kind:16s} implicit-pass={t.get(kind + '.implicit_pass', 0):4d}  "
                     f"other-writer={t.get(kind + '.other_writer', 0):4d}  unaudited={t.get(kind + '.unaudited', 0):4d}")
    if any(k.endswith(".unaudited") for k in t):
        lines.append("  (drawers export: no audit lane, so who retired a row is unknown)  "
                     + "  ".join(f"{k}={v}" for k, v in sorted(t.items()) if k.endswith(".unaudited")))
    lines.append(f"Restorable (retired for a different person's or attribute's fact): {report['restorable_rows']} "
                 f"({report['restorable_via_implicit_pass']} by the implicit-conflict pass).")
    for u, c in report["by_user"].items():
        n = sum(v for k, v in c.items() if k.split(".")[0] in ("other_person", "other_attribute"))
        if n:
            lines.append(f"  user={u}: {n} restorable")
    lines.append("DRY RUN: nothing was written. --plan-restore FILE writes the id list for the operator.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ledger = _load_ledger()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=ledger.DEFAULT_DB)
    ap.add_argument("--export", metavar="FILE", help="read a drawers export JSON (a backup copy under "
                    "~/.zoe/palace-backups/) instead of the palace; it has no audit lane")
    ap.add_argument("--owner", default=None, help="only this user id (default: every user)")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--plan-restore", metavar="FILE", help="write the restorable row ids as a JSON plan (no execution here)")
    args = ap.parse_args(argv)

    ms = _matcher(args.repo)
    if args.export:
        with open(os.path.expanduser(args.export), encoding="utf-8") as fh:
            exp = json.load(fh)
        drawers = [{"eid": i, DOC_KEY: d, **(m or {})} for i, d, m in zip(exp["ids"], exp["documents"], exp["metadatas"])]
        audit_rows = None
    else:
        conn = ledger.connect_ro(args.db)
        try:
            drawers = ledger.load_collection(conn, ledger.DRAWERS)
            audit_rows = ledger.load_collection(conn, ledger.AUDIT)
        finally:
            conn.close()
    report = audit(drawers, audit_rows, ms, owner=args.owner)
    if args.plan_restore:
        with open(args.plan_restore, "w", encoding="utf-8") as fh:
            json.dump({"how_to_apply": "operator step: restore each row to status=approved and clear superseded_by_id / "
                                       "invalid_at / expired_at (and the successor's supersedes_id); never raw delete",
                       "rows": [{k: x[k] for k in ("id", "user_id", "bucket", "via")} for x in report["rows"]]},
                      fh, indent=2)
    printable = {k: v for k, v in report.items() if k != "rows"}
    print(json.dumps(printable, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
