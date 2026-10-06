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
  * Nothing is restored unless ``--apply-restore`` is given (see below); the default is a dry run.

Restore (``--apply-restore``; there is no review decision for it: ``approve|reject|archive|edit``): each
restorable row goes back to ``approved`` through ``MemoryService.restore_superseded`` (the same row-write and
audit path ``MemoryService.review`` uses, so the history is kept and the ledger shows ``restore_collateral``):
``invalid_at`` / ``expired_at`` / ``superseded_by_id`` cleared, the ORIGINAL ``valid_from`` kept (a new interval,
open-ended from where the row began; ``memory_temporal.restore_fields``), the embedding rebuilt if the index lost
it, and the successor's ``supersedes_id`` (when it points at the restored row) cleared and noted. The successor
itself is NOT touched otherwise: it is a true fact about the other person. A row whose exact text is already
approved for that user is not a loss and is not restored. Guards, as ``restore_memories_from_export.py``:
``--owner`` (one user per run), ``--i-have-reviewed``, ``--i-stopped-zoe-data`` and a fail-CLOSED service check
(the zoe-data unit must report ``inactive``/``failed`` AND 127.0.0.1:8000 must refuse connections); the live-store
guard (``live_store_guard``) stays armed for any process that is not the service. A second run restores 0. Output
is counts only.

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
Restore (operator; zoe-data stopped; the zoe-data py312 venv so chromadb/onnx match the service):
  systemctl --user stop zoe-data
  ~/.zoe/venvs/zoe-data-py312/bin/python scripts/maintenance/memory_supersede_collateral_audit.py \\
      --owner jason --apply-restore --i-have-reviewed --i-stopped-zoe-data
  systemctl --user start zoe-data && until curl -sf http://127.0.0.1:8000/readyz; do sleep 5; done
Verify: re-run the dry run for the owner - "Restorable ...: 0".
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DOC_KEY = "chroma:document"
IMPLICIT_ACTOR = "implicit_supersede"
RESTORE_ACTOR = "operator_restore"
RESTORE_ACTION = "restore_collateral"


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


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


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
    # a retired row whose exact text is approved again (the person said it again) lost nothing: not restorable
    live_text = {(str(r.get("user_id") or r.get("wing") or ""), _norm(str(r.get(DOC_KEY) or "")))
                 for r in drawers if r.get("status") == "approved"}
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
            if (uid, _norm(text)) in live_text:
                per_user[uid]["collateral_text_already_approved"] += 1
                continue
            rows.append({"id": r["eid"], "user_id": uid, "bucket": kind, "via": via, "text_sha": h(text),
                         "successor_id": succ["eid"]})
    total = collections.Counter()
    for c in per_user.values():
        total.update(c)
    return {
        "owner_filter": owner,
        "totals": dict(sorted(total.items())),
        "by_user": {u: dict(sorted(c.items())) for u, c in sorted(per_user.items())},
        "restorable_rows": len(rows),
        "restorable_via_implicit_pass": sum(1 for x in rows if x["via"] == "implicit_pass"),
        "restorable_by_user": dict(sorted(collections.Counter(x["user_id"] for x in rows).items())),
        "rows": rows,
    }


def render(report: dict, *, dry_run: bool = True) -> str:
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
    if t.get("collateral_text_already_approved"):
        lines.append(f"  not restorable, the same text is approved again: {t['collateral_text_already_approved']}")
    for u, n in report["restorable_by_user"].items():
        lines.append(f"  user={u}: {n} restorable")
    if dry_run:
        lines.append("DRY RUN: nothing was written. --plan-restore FILE writes the id list for the operator; "
                     "--apply-restore restores them (zoe-data stopped).")
    return "\n".join(lines)


def service_up() -> "str | None":
    """Why zoe-data may be running, or None only when it is PROVABLY down: the SAME fail-closed check
    ``restore_memories_from_export.service_up`` makes (one definition: the unit must be ``inactive``/``failed``
    and 127.0.0.1:8000 must refuse connections; any unknown state refuses)."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import restore_memories_from_export as rme

    return rme.service_up()


def _make_service(data_dir: str):
    """The real service on the palace directory (imports chromadb: only the apply path ever does)."""
    zd = os.path.join(REPO, "services", "zoe-data")
    if zd not in sys.path:
        sys.path.insert(0, zd)
    from memory_service import MemoryService

    return MemoryService(data_dir=data_dir)


async def apply_restore(rows: list[dict], owner: str, *, svc) -> dict:
    """Restore each planned row through ``svc.restore_superseded`` (idempotent: it acts only on a row that is still
    superseded by the successor the plan names). Counts only."""
    done = skipped = reindexed = unlinked = 0
    for row in rows:
        if row["user_id"] != owner:
            skipped += 1
            continue
        res = await svc.restore_superseded(
            owner, row["id"], expected_successor_id=row["successor_id"], actor=RESTORE_ACTOR,
            note=f"{RESTORE_ACTION}: {row['bucket']} via {row['via']}")
        if res is None:
            skipped += 1
            continue
        done += 1
        reindexed += bool(res.get("reindexed"))
        unlinked += bool(res.get("unlinked"))
    return {"restored": done, "skipped": skipped, "reindexed": reindexed, "successor_links_cleared": unlinked}


def _read_palace(ledger, db: str) -> tuple[list[dict], list[dict]]:
    conn = ledger.connect_ro(db)
    try:
        return ledger.load_collection(conn, ledger.DRAWERS), ledger.load_collection(conn, ledger.AUDIT)
    finally:
        conn.close()


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
    ap.add_argument("--apply-restore", action="store_true",
                    help="restore the restorable rows of --owner (needs --i-have-reviewed and --i-stopped-zoe-data; "
                         "zoe-data must be stopped)")
    ap.add_argument("--i-have-reviewed", action="store_true")
    ap.add_argument("--i-stopped-zoe-data", action="store_true")
    args = ap.parse_args(argv)
    if args.apply_restore:
        if args.export:
            print("refusing --apply-restore with --export: a drawers export has no audit lane and is not the palace",
                  file=sys.stderr)
            return 2
        if not args.owner:
            print("refusing --apply-restore without --owner: restore one user per run", file=sys.stderr)
            return 2
        if not (args.i_have_reviewed and args.i_stopped_zoe_data):
            print("refusing --apply-restore without --i-have-reviewed and --i-stopped-zoe-data", file=sys.stderr)
            return 2

    ms = _matcher(args.repo)
    if args.export:
        with open(os.path.expanduser(args.export), encoding="utf-8") as fh:
            exp = json.load(fh)
        drawers = [{"eid": i, DOC_KEY: d, **(m or {})} for i, d, m in zip(exp["ids"], exp["documents"], exp["metadatas"])]
        audit_rows = None
    else:
        up = service_up() if args.apply_restore else None
        if up:        # checked BEFORE the snapshot is read: the plan must be made against a store nothing is writing
            print(f"refusing --apply-restore: {up}. Stop zoe-data first (systemctl --user stop zoe-data) and wait "
                  "for it to report inactive.", file=sys.stderr)
            return 3
        drawers, audit_rows = _read_palace(ledger, args.db)
    report = audit(drawers, audit_rows, ms, owner=args.owner)
    if args.plan_restore:
        with open(args.plan_restore, "w", encoding="utf-8") as fh:
            json.dump({"how_to_apply": "operator step: restore each row to status=approved and clear superseded_by_id / "
                                       "invalid_at / expired_at (and the successor's supersedes_id); never raw delete",
                       "rows": [{k: x[k] for k in ("id", "user_id", "bucket", "via")} for x in report["rows"]]},
                      fh, indent=2)
    printable = {k: v for k, v in report.items() if k != "rows"}
    print(json.dumps(printable, indent=2) if args.json else render(report, dry_run=not args.apply_restore))
    if not args.apply_restore:
        return 0
    result = asyncio.run(apply_restore(report["rows"], args.owner,
                                       svc=_make_service(os.path.dirname(os.path.expanduser(args.db)))))
    after_drawers, after_audit = _read_palace(ledger, args.db)
    after = audit(after_drawers, after_audit, ms, owner=args.owner)
    wanted = {r["id"] for r in report["rows"]}
    ledgered = sum(1 for a in after_audit if a.get("action") == RESTORE_ACTION and a.get("actor") == RESTORE_ACTOR
                   and a.get("mempalace_id") in wanted and a.get("user_id") == args.owner)
    approved_now = sum(1 for d in after_drawers if d["eid"] in wanted and d.get("status") == "approved")
    print(f"APPLIED: restored={result['restored']} skipped={result['skipped']} reindexed={result['reindexed']} "
          f"successor_links_cleared={result['successor_links_cleared']} ledger_rows={ledgered} "
          f"approved_now={approved_now} restorable_remaining={after['restorable_rows']}")
    if ledgered < result["restored"] or approved_now < result["restored"]:
        print("WARNING: the palace does not show every restored row approved and ledgered; run the dry run and "
              "check the audit lane before starting zoe-data.", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
