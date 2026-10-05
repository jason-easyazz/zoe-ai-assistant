#!/usr/bin/env python3
"""Restore the 20 owner memories lost in July 2026 from the 2026-07-05 export. NEVER touches text on screen.

Background (docs/knowledge/memory-loss-audit-2026-10-05.md, section 5): 20 of the owner's 27 rows in
``~/.zoe-backups/jason-mem-20260705-184122.json`` (their own Telegram statements of 07-04/05) were removed
from the palace before 07-13 with no audit record. The owner approved restoring them.

How it restores (and why it is safe):
  * Reads the export read-only and the palace through sqlite ``mode=ro`` (dry run never imports chromadb).
  * Selects EXACTLY the 20 row ids in ``LOST_IDS`` (a fixed list, not "whatever is missing"); every one must be
    in the export and belong to the owner.
  * Refuses any row that already exists in the palace — by original id, or by exact text for the same user in
    ANY status (a superseded/archived copy is a deliberate replacement, not a gap). That is also what makes a
    second ``--apply`` restore 0.
  * Writes ONLY through ``MemoryService.ingest`` (dedup, PII scrub, audit row) — never a raw collection write.
    ``source="operator_restore"``, ``status=approved``, original type/confidence/session/user-turn/entity,
    ``captured_at`` = the original capture instant (so "when did I tell you" still says July), and the
    provenance as ``candidate_origin=july_export_restore`` + ``candidate_restored_from_*`` (original id,
    source, session, turn, added_at, export filename) + ``candidate_authority_class=user_stated``. One extra
    ``restore`` audit row per restored memory (actor ``operator_restore``, reason names the export file).
  * ``--apply`` additionally needs ``--i-have-reviewed`` and ``--i-stopped-zoe-data`` and REFUSES while the
    zoe-data unit is active or 127.0.0.1:8000 accepts connections.

Usage (dry run is the default and prints shapes only — id suffix, date, length, sha10, checks):
  python3 scripts/maintenance/restore_memories_from_export.py
Apply (operator, zoe-data stopped, the zoe-data py312 venv so chromadb/onnx match the service):
  systemctl --user stop zoe-data
  ~/.zoe/venvs/zoe-data-py312/bin/python scripts/maintenance/restore_memories_from_export.py \\
      --apply --i-have-reviewed --i-stopped-zoe-data
  systemctl --user start zoe-data && until curl -sf http://127.0.0.1:8000/readyz; do sleep 5; done
Verify: re-run the dry run — every row now reads ``exists_by_text`` and "would restore: 0".
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "services" / "zoe-data"))
import memory_ledger_audit as mla  # noqa: E402  (read-only sqlite helpers shared with the audit tool)

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


def gate_result(text: str) -> str:
    """What the CURRENT write-quality gate says (informational: a restore is the owner's explicit call)."""
    try:
        from memory_quality import is_storable_fact
        ok, why = is_storable_fact(text)
        return "ok" if ok else f"would_reject:{why}"
    except Exception:  # noqa: BLE001
        return "gate_unavailable"


def build_plan(export: dict[str, dict], drawers: list[dict]) -> list[dict]:
    """One entry per LOST_IDS id: ``status`` is ``restorable`` or a ``refused_*`` reason. No text is
    returned except under ``_row`` (used by --apply only, never printed)."""
    missing = [i for i in LOST_IDS if i not in export]
    if missing:
        raise SystemExit(f"restore: {len(missing)} expected id(s) are not in the export (wrong file?): "
                         + ", ".join(m[-8:] for m in missing))
    live_ids = {d["eid"] for d in drawers}
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
        if str(meta.get("user_id") or "") != OWNER or not text.strip():
            entry["status"] = "refused_not_owner_or_empty"
        elif rid in live_ids:
            entry["status"] = "refused_exists_by_id"
        elif (OWNER, text.strip()) in by_text:
            entry["status"] = f"refused_exists_by_text:{by_text[(OWNER, text.strip())]}"
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


def service_up() -> str | None:
    """Why zoe-data looks up ('' / None when it does not) — the apply refuses while it is."""
    try:
        r = subprocess.run(["systemctl", "--user", "is-active", "zoe-data"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip() == "active":
            return "the zoe-data unit is active"
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=1):
            return "127.0.0.1:8000 is accepting connections"
    except OSError:
        return None


async def apply_plan(plan: list[dict], export_name: str, palace: str, *, skip_gate_rejects: bool = False,
                     svc=None) -> dict:
    if svc is None:
        from memory_service import MemoryService
        svc = MemoryService(data_dir=os.path.expanduser(palace))
    done = refused = 0
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
        ref = await svc.ingest(
            text, user_id=OWNER, source=ACTOR,
            session_id=meta.get("session_id") or None, user_turn_id=meta.get("user_turn_id") or None,
            memory_type=str(meta.get("memory_type") or row.get("type") or "fact"),
            confidence=float(meta.get("confidence") or 0.7), status="approved",
            entity_type=meta.get("entity_type") or None, entity_id=meta.get("entity_id") or None,
            metadata=extra, captured_at=meta.get("added_at"),
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
        p["status"] = "restored"
        done += 1
    return {"restored": done, "refused": refused}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", default=DEFAULT_EXPORT)
    ap.add_argument("--palace", default=DEFAULT_PALACE)
    ap.add_argument("--dry-run", action="store_true", help="default; print the plan, write nothing")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--i-have-reviewed", action="store_true")
    ap.add_argument("--i-stopped-zoe-data", action="store_true")
    ap.add_argument("--skip-gate-rejects", action="store_true",
                    help="do not restore rows the CURRENT write-quality gate would reject (default: restore all 20)")
    args = ap.parse_args(argv)

    export = load_export(args.export)
    plan = build_plan(export, read_palace(args.palace))
    name = os.path.basename(os.path.expanduser(args.export))
    print(render(plan, name, args.palace))
    if not args.apply:
        print("DRY RUN: nothing was written. To apply: --apply --i-have-reviewed --i-stopped-zoe-data "
              "(zoe-data stopped).")
        return 0
    if not (args.i_have_reviewed and args.i_stopped_zoe_data):
        print("refusing --apply without --i-have-reviewed and --i-stopped-zoe-data", file=sys.stderr)
        return 2
    up = service_up()
    if up:
        print(f"refusing --apply: {up}. Stop zoe-data first (systemctl --user stop zoe-data).", file=sys.stderr)
        return 3
    result = asyncio.run(apply_plan(plan, name, args.palace, skip_gate_rejects=args.skip_gate_rejects))
    after = read_palace(args.palace)
    n_in_palace = sum(1 for d in after if d.get("candidate_origin") == ORIGIN)
    print(f"APPLIED: restored={result['restored']} refused={result['refused']} "
          f"rows with origin={ORIGIN} now in the palace: {n_in_palace}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
