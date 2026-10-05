#!/usr/bin/env python3
"""Restore the 20 owner memories lost in July 2026 from the 2026-07-05 export. NEVER touches text on screen.

Background (docs/knowledge/memory-loss-audit-2026-10-05.md, section 5): 20 of the owner's 27 rows in
``~/.zoe-backups/jason-mem-20260705-184122.json`` (their own Telegram statements of 07-04/05) were removed
from the palace before 07-13 with no audit record. The owner approved restoring them.

How it restores (and why it is safe):
  * Reads the export read-only and the palace through sqlite ``mode=ro`` (dry run never imports chromadb).
  * Selects EXACTLY the 20 row ids in ``LOST_IDS`` (a fixed list, not "whatever is missing"); every one must be
    in the export and belong to the owner.
  * Refuses any row that already exists in the palace — by original id, by ``candidate_restored_from_id`` (a
    restored row carries the ORIGINAL id there; its own id differs), or by exact text for the same user in
    ANY status (a superseded/archived copy is a deliberate replacement, not a gap). That is also what makes a
    second ``--apply`` restore 0.
  * Refuses a row whose ``meta.added_at`` is missing/unparseable (``undated``, unless ``--allow-undated``, which
    stores it with ``candidate_restored_undated=True``) or more than 5 minutes in the future (``future_date``).
  * Refuses the WHOLE run when the owner has a completed ``delete_user`` (right-to-be-forgotten) audit row
    newer than the newest row in the export, unless ``--after-forget``: a restore must not resurrect
    forgotten rows.
  * Writes ONLY through ``MemoryService.ingest`` (dedup, PII scrub, audit row) — never a raw collection write.
    ``source="operator_restore"``, ``status=approved``, original type/confidence/session/user-turn/entity,
    ``captured_at`` = the original capture instant (so "when did I tell you" still says July), and the
    provenance as ``candidate_origin=july_export_restore`` + ``candidate_restored_from_*`` (original id,
    source, session, turn, added_at, export filename) + ``candidate_authority_class=user_stated``. One extra
    ``restore`` audit row per restored memory (actor ``operator_restore``, reason names the export file).
  * ``--apply`` additionally needs ``--i-have-reviewed`` and ``--i-stopped-zoe-data`` and fails CLOSED on the
    service check: it proceeds only when the zoe-data unit reports ``inactive`` or ``failed`` AND
    127.0.0.1:8000 refuses connections. Any other or unknown state (activating, deactivating, no user bus,
    no systemctl) refuses.

Usage (dry run is the default and prints shapes only — id suffix, date, length, sha10, checks):
  python3 scripts/maintenance/restore_memories_from_export.py
Apply (operator, zoe-data stopped, the zoe-data py312 venv so chromadb/onnx match the service):
  systemctl --user stop zoe-data
  ~/.zoe/venvs/zoe-data-py312/bin/python scripts/maintenance/restore_memories_from_export.py \\
      --apply --i-have-reviewed --i-stopped-zoe-data
  systemctl --user start zoe-data && until curl -sf http://127.0.0.1:8000/readyz; do sleep 5; done
Verify: re-run the dry run — every row now reads ``exists_by_origin_id`` and "would restore: 0".
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "services" / "zoe-data"))
import memory_ledger_audit as mla  # noqa: E402  (read-only sqlite helpers shared with the audit tool)
from memory_captured_at import FUTURE, parse_captured_at, parse_iso_utc  # noqa: E402  (pure stdlib)

DEFAULT_EXPORT = "~/.zoe-backups/jason-mem-20260705-184122.json"
DEFAULT_PALACE = "~/.mempalace"
OWNER = "jason"
ACTOR = "operator_restore"
ORIGIN = "july_export_restore"

# The 20 rows (owner, status approved at 2026-07-05 18:41 local) that the 2026-10-05 audit found in this
# export and in no later palace, with no archive/edit/delete record. Row ids are text-derived hashes.
LOST_IDS = (
    "zoe_jason_42bf26514480e4c58264bfbd", "zoe_jason_624350d67cdbab2afd2f1968",
    "zoe_jason_a48ceeaa2f390acb172a6b45", "zoe_jason_74362ef013e27cea78d92425",
    "zoe_jason_80d2f10d0a6f432d76aadad9", "zoe_jason_4b9422c8cc3a086e985c3fa6",
    "zoe_jason_0a4de150061dfa43cd6d6da9", "zoe_jason_d8584319a75ca05eeb8d521c",
    "zoe_jason_ac32dc6b1d7aafb220f74861", "zoe_jason_662373976fd0e6573b471145",
    "zoe_jason_9a071452ca7d584ca0da813b", "zoe_jason_625cfbb4b41cbf6154dc4a7c",
    "zoe_jason_ab21b4b5a068fd4c2d76a764", "zoe_jason_2349323423988c45b2a4e799",
    "zoe_jason_d9f1dadde57d07321f31ff38", "zoe_jason_b8f7841e2d3ee24fd7d51beb",
    "zoe_jason_613ded6f48d2f82ced7f8dd2", "zoe_jason_756fd51ab6ab418fd29d38f0",
    "zoe_jason_113d2d8d6472fcb0e0c52f7f", "zoe_jason_0a7f636c8cc6394bb47cea2c",
)


def sha10(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:10]


def load_export(path: str) -> dict[str, dict]:
    import json
    with open(os.path.expanduser(path), encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise SystemExit(f"restore: {path} is not a list-of-rows export")
    return {str(r.get("id")): r for r in data if isinstance(r, dict)}


def read_palace(palace: str) -> list[dict]:
    db = os.path.join(os.path.expanduser(palace), "chroma.sqlite3")
    conn = mla.connect_ro(db)
    try:
        return mla.load_collection(conn, mla.DRAWERS)
    finally:
        conn.close()


def read_audit(palace: str) -> list[dict]:
    """The palace's audit collection (read-only; empty when the collection does not exist yet)."""
    db = os.path.join(os.path.expanduser(palace), "chroma.sqlite3")
    conn = mla.connect_ro(db)
    try:
        return mla.load_collection(conn, mla.AUDIT)
    finally:
        conn.close()


def forgotten_since_export(audit: list[dict], export: dict[str, dict]) -> int:
    """How many completed ``delete_user`` rows for the owner are newer than the export's newest row.

    The export's own time is not recorded in it, so its newest ``meta.added_at`` stands in (a deletion after
    that could have removed rows the export still lists). Fails closed: when no row of the export has a
    parseable ``added_at`` there is nothing to compare against, so every completed deletion counts. An audit row
    with an unreadable timestamp also counts."""
    stamps = [parse_iso_utc((r.get("meta") or {}).get("added_at")) for r in export.values()]
    newest = max((t for t in stamps if t is not None), default=None)
    n = 0
    for a in audit:
        if a.get("action") != "delete_user_done" or str(a.get("user_id") or "") != OWNER:
            continue
        when = parse_iso_utc(a.get("timestamp"))
        if newest is None or when is None or when > newest:
            n += 1
    return n


def gate_result(text: str) -> str:
    """What the CURRENT write-quality gate says (informational: a restore is the owner's explicit call)."""
    try:
        from memory_quality import is_storable_fact
        ok, why = is_storable_fact(text)
        return "ok" if ok else f"would_reject:{why}"
    except Exception:  # noqa: BLE001
        return "gate_unavailable"


def build_plan(export: dict[str, dict], drawers: list[dict], *, allow_undated: bool = False,
               now=None) -> list[dict]:
    """One entry per LOST_IDS id: ``status`` is ``restorable`` or a ``refused_*`` reason. No text is
    returned except under ``_row`` (used by --apply only, never printed). ``now`` is for tests."""
    missing = [i for i in LOST_IDS if i not in export]
    if missing:
        raise SystemExit(f"restore: {len(missing)} expected id(s) are not in the export (wrong file?): "
                         + ", ".join(m[-8:] for m in missing))
    live_ids = {d["eid"] for d in drawers}
    # A restored row's own id differs from the original (source is part of the id hash), but it records the
    # original id here: that is the re-run check that survives the text being edited afterwards.
    restored_from = {str(d["candidate_restored_from_id"]): str(d.get("status") or "?")
                     for d in drawers if d.get("candidate_restored_from_id")}
    by_text: dict[tuple[str, str], str] = {}
    for d in drawers:
        by_text.setdefault((str(d.get("user_id") or d.get("wing") or ""), str(d.get(mla.DOC_KEY) or "").strip()),
                           str(d.get("status") or "?"))
    plan = []
    for rid in LOST_IDS:
        row = export[rid]
        meta = row.get("meta") or {}
        text = str(row.get("text") or "")
        entry = {"id": rid, "suffix": rid[-8:], "added_at": str(meta.get("added_at") or ""),
                 "length": len(text), "sha10": sha10(text), "gate": gate_result(text), "_row": row}
        captured, why = parse_captured_at(meta.get("added_at"), now=now)
        entry["undated"] = captured is None and why != FUTURE
        entry["captured_at"] = captured.isoformat() + "Z" if captured else None
        if str(meta.get("user_id") or "") != OWNER or not text.strip():
            entry["status"] = "refused_not_owner_or_empty"
        elif rid in live_ids:
            entry["status"] = "refused_exists_by_id"
        elif rid in restored_from:
            entry["status"] = f"refused_exists_by_origin_id:{restored_from[rid]}"
        elif (OWNER, text.strip()) in by_text:
            entry["status"] = f"refused_exists_by_text:{by_text[(OWNER, text.strip())]}"
        elif why == FUTURE:
            entry["status"] = "refused_future_date"
        elif entry["undated"] and not allow_undated:
            entry["status"] = "refused_undated"
        else:
            entry["status"] = "restorable"
        plan.append(entry)
    return plan


def render(plan: list[dict], export_name: str, palace: str) -> str:
    n_ok = sum(1 for p in plan if p["status"] == "restorable")
    lines = [f"export={export_name} palace={palace} owner={OWNER}: {len(plan)} selected, "
             f"would restore: {n_ok}, refused: {len(plan) - n_ok}"]
    for p in plan:
        lines.append(f"  {p['suffix']} added={p['added_at'][:19]} len={p['length']:3d} sha10={p['sha10']} "
                     f"gate={p['gate']} -> {p['status']}")
    return "\n".join(lines)


def service_up() -> Optional[str]:
    """Why zoe-data may be running - or None only when it is PROVABLY down. The apply refuses on any reason.

    Fails CLOSED. The unit must report exactly ``inactive`` or ``failed`` (``activating`` / ``deactivating`` /
    ``reloading`` / ``active`` all mean a process may hold Chroma open and the port may not be bound yet), and
    127.0.0.1:8000 must refuse connections. A systemctl that cannot answer (no user bus under cron/sudo, not
    installed, timeout) is "unknown", which is also a refusal."""
    reasons = []
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", "zoe-data"], capture_output=True, text=True, timeout=10)
        state = (r.stdout or "").strip()
        if state not in ("inactive", "failed"):
            reasons.append("the zoe-data unit state is " + repr(state) if state else
                           "the zoe-data unit state cannot be read (systemctl --user gave no answer)")
    except (OSError, subprocess.SubprocessError):
        reasons.append("the zoe-data unit state cannot be read (systemctl --user failed)")
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=1):
            reasons.append("127.0.0.1:8000 is accepting connections")
    except OSError:
        pass
    return "; ".join(reasons) if reasons else None


def _same_instant(a, b) -> bool:
    x, y = parse_iso_utc(a), parse_iso_utc(b)
    return x is not None and y is not None and x == y


async def apply_plan(plan: list[dict], export_name: str, palace: str, *, skip_gate_rejects: bool = False,
                     svc=None) -> dict:
    if svc is None:
        from memory_service import MemoryService
        svc = MemoryService(data_dir=os.path.expanduser(palace))
    done = refused = misdated = 0
    for p in plan:
        if p["status"] != "restorable" or (skip_gate_rejects and p["gate"].startswith("would_reject")):
            refused += 1
            continue
        row = p["_row"]
        meta = row.get("meta") or {}
        text = str(row["text"])
        extra = {
            "origin": ORIGIN, "authority_class": "user_stated",
            "restored_from_export": export_name, "restored_from_id": row["id"],
            "restored_from_source": meta.get("source") or row.get("src"),
            "restored_from_added_at": meta.get("added_at"),
            "restored_from_session_id": meta.get("session_id"),
            "restored_from_user_turn_id": meta.get("user_turn_id"),
        }
        if p.get("undated"):                # --allow-undated: the provenance must say the date is a guess
            extra["restored_undated"] = True
        ref = await svc.ingest(
            text, user_id=OWNER, source=ACTOR,
            session_id=meta.get("session_id") or None, user_turn_id=meta.get("user_turn_id") or None,
            memory_type=str(meta.get("memory_type") or row.get("type") or "fact"),
            confidence=float(meta.get("confidence") or 0.7), status="approved",
            entity_type=meta.get("entity_type") or None, entity_id=meta.get("entity_id") or None,
            metadata=extra, captured_at=p.get("captured_at"),
        )
        if ref is None:                     # PII scrub / durable dedup: nothing was written
            refused += 1
            p["status"] = "refused_by_ingest"
            continue
        await svc._append_audit(   # noqa: SLF001 — the restore record: actor + export file, no text
            mem_id=ref.id, user_id=OWNER, actor=ACTOR, action="restore",
            before={"restored_from_id": row["id"], "export": export_name}, after={"id": ref.id},
            reason=f"{ORIGIN}: {export_name}",
        )
        if p.get("captured_at") and not _same_instant(ref.metadata.get("added_at"), p["captured_at"]):
            misdated += 1                   # ingest ignored captured_at: the row exists but is dated wrongly
            p["status"] = "restored_misdated"
            continue
        p["status"] = "restored"
        done += 1
    result = {"restored": done, "refused": refused}
    if misdated:
        result["misdated"] = misdated
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", default=DEFAULT_EXPORT)
    ap.add_argument("--palace", default=DEFAULT_PALACE)
    ap.add_argument("--dry-run", action="store_true", help="default; print the plan, write nothing")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--i-have-reviewed", action="store_true")
    ap.add_argument("--i-stopped-zoe-data", action="store_true")
    ap.add_argument("--allow-undated", action="store_true",
                    help="restore rows whose export added_at is missing/unparseable, dated now and marked "
                         "candidate_restored_undated=True (default: refuse them)")
    ap.add_argument("--after-forget", action="store_true",
                    help="proceed although the owner has a completed delete_user (forget) newer than the export")
    ap.add_argument("--skip-gate-rejects", action="store_true",
                    help="do not restore rows the CURRENT write-quality gate would reject (default: restore all 20)")
    args = ap.parse_args(argv)

    export = load_export(args.export)
    plan = build_plan(export, read_palace(args.palace), allow_undated=args.allow_undated)
    name = os.path.basename(os.path.expanduser(args.export))
    print(render(plan, name, args.palace))
    forgotten = forgotten_since_export(read_audit(args.palace), export)
    if forgotten:
        print(f"FORGET TOMBSTONE: {forgotten} completed delete_user record(s) for {OWNER} are newer than the "
              "export; restoring could resurrect forgotten rows.")
    if not args.apply:
        print("DRY RUN: nothing was written. To apply: --apply --i-have-reviewed --i-stopped-zoe-data "
              "(zoe-data stopped).")
        return 0
    if not (args.i_have_reviewed and args.i_stopped_zoe_data):
        print("refusing --apply without --i-have-reviewed and --i-stopped-zoe-data", file=sys.stderr)
        return 2
    if forgotten and not args.after_forget:
        print("refusing --apply: the owner was forgotten (delete_user) after this export. If the owner wants "
              "these rows back anyway, re-run with --after-forget.", file=sys.stderr)
        return 4
    up = service_up()
    if up:
        print(f"refusing --apply: {up}. Stop zoe-data first (systemctl --user stop zoe-data) and wait for it "
              "to report inactive.", file=sys.stderr)
        return 3
    result = asyncio.run(apply_plan(plan, name, args.palace, skip_gate_rejects=args.skip_gate_rejects))
    after = read_palace(args.palace)
    n_in_palace = sum(1 for d in after if d.get("candidate_origin") == ORIGIN)
    print(f"APPLIED: restored={result['restored']} refused={result['refused']} "
          f"rows with origin={ORIGIN} now in the palace: {n_in_palace}")
    if result.get("misdated"):
        print(f"WARNING: {result['misdated']} restored row(s) were NOT stored at their original capture instant.",
              file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
