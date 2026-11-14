#!/usr/bin/env python3
"""Find (and, on the operator's say-so, forget) memory rows MINTED FROM A FORGET COMMAND's own turn. READ-ONLY by default.

Why (QA round 10a, 2026-11-14; record docs/knowledge/forget-reach.md "Forget commands are never mined"): a TOPIC forget
("forget that I told you about my brother") writes no forget-ledger entry, and its saved chat turn was not off the record, so
idle consolidation and the nightly digest re-read the command itself and filed a row ABOUT the forgotten topic ("the user told
Zoe about their brother"). The fix (``turn_forget.is_forget_instruction``) stops new ones; this finds the rows minted before it.

A row is LINKED to a forget command (and only linked rows are ever cleaned) when its own provenance says so:
  excerpt_is_command  the row's ``source_excerpt`` (the user's words it rests on) is itself a forget instruction
  excerpt_in_command  the excerpt is part of one of the member's forget-command turns and of none of their other turns read
  turn_id             the row's ``user_turn_id`` is the content id (``memory_authority.turn_evidence``) of a forget-command turn
The detector is the fix's own (``turn_forget.is_forget_instruction``: ``forget_topic.parse`` + ``turn_forget.is_forget_turn`` +
the entity-forget pattern), so "I always forget my keys" is never a command.
A row with NO linkage (no excerpt, no turn id: idle consolidation / digest rows often have neither) cannot be proven: the
best conservative match is reported as WINDOW - a row from the idle / digest writers, in a session that holds a forget-command
turn, added after it within ``--window-hours``. Window rows are REPORT-ONLY: counted and listed for review, never erased (they
can be ordinary memories written after a command).

Safety contract (pinned by services/zoe-data/tests/test_forget_command_rows_audit.py):
  * The palace is opened with sqlite ``mode=ro``; chat turns are one SELECT through psql: the WHOLE history by default (rows are
    durable), ``--since YYYY-MM-DD`` to narrow it; the dates actually read are printed. No write, no chromadb import.
  * Output is COUNTS per member INDEX (``member-1`` + a short hash of the id, never a name) and per writer. No row text, no
    turn text. Row ids (with their owner id) go only to the private ``--ids-out`` file (mode 0600).
  * Cleanup (``--erase-from FILE``) is a DRY RUN unless ``--confirm``; ``--confirm`` also needs ``--i-stopped-zoe-data`` and a
    service that is provably down. It uses the owner-forget path: ``MemoryService.review(archive)`` then
    ``MemoryService.erase_rows`` (content-free intent row, the rows and their audit trail, the physical residue erase), for rows
    The plan file is a HINT, never an authority: before anything is erased the link is RECOMPUTED from the palace and the
    transcripts by the same three rules, and a planned row not linked NOW to a forget command of that same owner is refused
    (counted; its id only to ``--refused-out``, 0600) and left untouched. Window rows are never erased. Never a raw delete.

Usage (operator, on the box):
  python3 scripts/maintenance/forget_command_rows_audit.py --ids-out ~/.zoe/forget-command-rows.json
  # review the counts, then (zoe-data stopped; the zoe-data py312 venv so chromadb matches the service):
  systemctl --user stop zoe-data
  ~/.zoe/venvs/zoe-data-py312/bin/python scripts/maintenance/forget_command_rows_audit.py \\
      --erase-from ~/.zoe/forget-command-rows.json                 # dry run: counts only
  ~/.zoe/venvs/zoe-data-py312/bin/python scripts/maintenance/forget_command_rows_audit.py \\
      --erase-from ~/.zoe/forget-command-rows.json --confirm --i-stopped-zoe-data
  systemctl --user start zoe-data && until curl -sf http://127.0.0.1:8000/readyz; do sleep 5; done
Verify: re-run the audit - "linked: 0".

Exit codes: 0 = report printed / dry run / cleanup complete; 2 = refused or unreadable; 5 = cleanup left rows behind.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as _dt
import hashlib
import json
import os
import shlex
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))

DEFAULT_PALACE = "~/.mempalace"
DEFAULT_PSQL = "docker exec zoe-database psql -U zoe -d zoe"
DRAWERS = "mempalace_drawers"
WINDOW_SOURCES = frozenset({"idle_consolidation", "digest", "turn_digest", "synthesis"})
GONE = frozenset({"archived", "rejected", "deleted"})
ACTOR = "forget_command_cleanup"
REASON = "minted from a forget command's own turn (QA round 10a); forgotten through the owner-forget path"
# a superset prefilter (every verb the detectors accept); the detector itself decides
_PREFILTER = r"(forget|delete|remove|erase|scrap|never ?mind|remember|keep|sav|stor)"


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def member_key(user_id: str) -> str:
    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:8]


def turn_content_id(user_id: str, text: str) -> str:
    """The ``ut-`` content id ``memory_authority.turn_evidence`` gives a model-written row whose evidence is this text."""
    import memory_authority

    return "ut-" + hashlib.sha1(f"{user_id}|{memory_authority._squash(text).lower()}".encode("utf-8")).hexdigest()[:16]


def load_rows(palace: str) -> list[dict[str, Any]]:
    """Every drawers row ``{id, user_id, text, meta}`` (any status). sqlite ``mode=ro``."""
    db = Path(palace).expanduser() / "chroma.sqlite3"
    if not db.exists():
        raise OSError(f"no such palace database: {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        segs = [r[0] for r in con.execute(
            "SELECT s.id FROM segments s JOIN collections c ON c.id = s.collection WHERE c.name = ? AND s.scope = 'METADATA'",
            (DRAWERS,))]
        rows: list[dict[str, Any]] = []
        for seg in segs:
            for rid, eid in con.execute("SELECT id, embedding_id FROM embeddings WHERE segment_id = ?", (seg,)):
                meta: dict[str, Any] = {}
                for k, s, i, f in con.execute(
                        "SELECT key, string_value, int_value, float_value FROM embedding_metadata WHERE id = ?", (rid,)):
                    meta[k] = s if s is not None else (i if i is not None else f)
                text = str(meta.pop("chroma:document", "") or "")
                rows.append({"id": str(eid), "user_id": str(meta.get("user_id") or meta.get("wing") or ""), "text": text,
                             "meta": meta})
        return rows
    finally:
        con.close()


def turns_sql(since: Optional[str] = None) -> str:
    """The one SELECT: every user turn that could be a forget instruction, the WHOLE history unless ``since`` (an ISO date,
    validated: anything else raises ValueError, so nothing but a date reaches the SQL)."""
    when = f"and created_at::timestamptz >= '{_dt.date.fromisoformat(since).isoformat()}'::date " if since else ""
    return ("select coalesce(json_agg(json_build_object('id', id, 'session_id', session_id, 'at', created_at::text, "
            "'meta', metadata, 'content', content)), '[]') from chat_messages where role = 'user' "
            f"{when}and content ~* '{_PREFILTER}'")


def load_turns(psql_cmd: str, since: Optional[str] = None) -> list[dict[str, Any]]:
    """The candidate user turns (``turns_sql``), JSON out, never printed."""
    sql = turns_sql(since)
    out = subprocess.run([*shlex.split(psql_cmd), "-Atc", sql], capture_output=True, text=True, timeout=120, check=True).stdout
    turns = []
    for t in json.loads(out.strip() or "[]"):
        meta = t.get("meta")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {}
        turns.append({"id": str(t.get("id")), "session_id": str(t.get("session_id") or ""), "at": str(t.get("at") or ""),
                      "user_id": str((meta or {}).get("user_id") or ""), "content": str(t.get("content") or "")})
    return turns


def _ts(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        s = str(value or "").strip().replace("Z", "+00:00").replace(" ", "T", 1)
        d = _dt.datetime.fromisoformat(s)
        return (d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)).timestamp()
    except ValueError:
        return None


def audit(rows: Iterable[dict], turns: Iterable[dict], *, window_hours: float = 48.0) -> dict[str, Any]:
    """Pure: ``{linked, window, counts}``. ``linked`` / ``window`` are ``{id, user_id, source, how}`` (never text)."""
    import turn_forget

    commands: dict[str, list[dict]] = collections.defaultdict(list)
    others: dict[str, list[str]] = collections.defaultdict(list)
    for t in turns:
        uid = t.get("user_id") or ""
        if not uid:
            continue
        if turn_forget.is_forget_instruction(t.get("content") or ""):
            commands[uid].append({**t, "norm": _norm(t["content"]), "cid": turn_content_id(uid, t["content"]), "ts": _ts(t.get("at"))})
        else:
            others[uid].append(_norm(t.get("content") or ""))
    linked, window = [], []
    for r in rows:
        meta, uid = r.get("meta") or {}, r.get("user_id") or ""
        if str(meta.get("status") or "approved").lower() in GONE or not commands.get(uid):
            continue
        src = str(meta.get("source") or meta.get("added_by") or "-")
        excerpt = _norm(meta.get("source_excerpt") or "")
        how = ""
        if excerpt and turn_forget.is_forget_instruction(excerpt):
            how = "excerpt_is_command"
        elif len(excerpt) >= 8 and any(excerpt in c["norm"] for c in commands[uid]) \
                and not any(excerpt in o for o in others.get(uid, ())):
            how = "excerpt_in_command"
        elif str(meta.get("user_turn_id") or "") in {c["cid"] for c in commands[uid]}:
            how = "turn_id"
        if how:
            linked.append({"id": r["id"], "user_id": uid, "source": src, "how": how})
            continue
        if excerpt or str(meta.get("user_turn_id") or "").startswith("ut-") or src not in WINDOW_SOURCES:
            continue        # it rests on some other turn of the member's: not this audit's
        added = _ts(meta.get("added_ts") if meta.get("added_ts") is not None else meta.get("added_at"))
        sess = str(meta.get("session_id") or "")
        for c in commands[uid]:
            if sess and sess == c["session_id"] and added is not None and c["ts"] is not None \
                    and 0 <= added - c["ts"] <= window_hours * 3600:
                window.append({"id": r["id"], "user_id": uid, "source": src, "how": "session_window"})
                break
    index = {u: f"member-{i + 1}" for i, u in enumerate(sorted({x["user_id"] for x in linked + window}))}
    per_member: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for bucket, items in (("linked", linked), ("window", window)):
        for x in items:
            per_member[f"{index[x['user_id']]} ({member_key(x['user_id'])})"][f"{bucket}.{x['source']}"] += 1
    return {
        "linked": linked, "window": window,
        "counts": {
            "command_turns": sum(len(v) for v in commands.values()),
            "linked": len(linked), "window": len(window),
            "linked_by_how": dict(sorted(collections.Counter(x["how"] for x in linked).items())),
            "by_member": {k: dict(sorted(v.items())) for k, v in sorted(per_member.items())},
        },
    }


def turn_dates(turns: Iterable[dict]) -> dict[str, str]:
    """The first and last DATE of the turns read (dates only, never a time or a word)."""
    days = sorted(str(t.get("at") or "")[:10] for t in turns if str(t.get("at") or "")[:10])
    return {"from": days[0], "to": days[-1]} if days else {"from": "-", "to": "-"}


def _write_private(path: str, obj: Any) -> None:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
    os.chmod(str(p), 0o600)


def write_ids(path: str, report: dict) -> None:
    _write_private(path, {"linked": report["linked"], "window": report["window"]})


def plan_cleanup(rows: list[dict], turns: list[dict], plan: dict, *,
                 window_hours: float = 48.0) -> tuple[dict[str, list[str]], list[str]]:
    """``({owner: [ids]}, refused_ids)``. The plan is a HINT: a planned ``linked`` row is kept only when the audit RECOMPUTED here from
    ``rows`` and ``turns`` links it NOW to a forget command of the SAME owner the plan names (which also means it still exists and
    is not gone); every other planned id - an edited plan, a row that changed - is refused. ``window`` entries are never erased. Pure."""
    verified = {(x["id"], x["user_id"]) for x in audit(rows, turns, window_hours=window_hours)["linked"]}
    out: dict[str, list[str]] = collections.defaultdict(list)
    refused: list[str] = []
    for x in list(plan.get("linked") or []):
        key = (str(x.get("id")), str(x.get("user_id")))
        if key in verified and key[0] not in out[key[1]]:
            out[key[1]].append(key[0])
        elif key not in verified:
            refused.append(key[0])
    return {u: v for u, v in out.items() if v}, refused


async def cleanup(todo: dict[str, list[str]], *, svc) -> dict[str, dict[str, int]]:
    """The owner-forget path, per owner: archive each row (``review``), then ``erase_rows`` the archived ones. Counts only."""
    done: dict[str, dict[str, int]] = {}
    for owner, ids in sorted(todo.items()):
        archived = []
        for rid in ids:
            try:
                ref = await svc.review(rid, decision="archive", actor=ACTOR, note="forget_command_cleanup")
            except Exception:  # noqa: BLE001 - never erased on top of a failed archive
                ref = None
            if ref is not None:
                archived.append(rid)
        res = await svc.erase_rows(owner, archived, actor=ACTOR, reason=REASON) if archived else {"rows_removed": 0}
        done[member_key(owner)] = {"asked": len(ids), "archived": len(archived), "rows_removed": int((res or {}).get("rows_removed") or 0)}
    return done


def _service_up() -> Optional[str]:
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=1.0):
            return ":8000 is listening"
    except OSError:
        pass
    try:
        state = subprocess.run(["systemctl", "--user", "is-active", "zoe-data"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "the zoe-data unit state is unknown"
    return None if state in ("inactive", "failed") else f"zoe-data is {state or 'unknown'}"


def _member_counts(todo: dict[str, list[str]]) -> dict[str, int]:
    return {member_key(u): len(v) for u, v in sorted(todo.items())}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--palace", default=DEFAULT_PALACE)
    ap.add_argument("--psql-cmd", default=DEFAULT_PSQL)
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="read user turns from this date (default: the whole history)")
    ap.add_argument("--window-hours", type=float, default=48.0)
    ap.add_argument("--ids-out", metavar="FILE", help="write the row ids (and owners) here, mode 0600")
    ap.add_argument("--erase-from", metavar="FILE", help="cleanup: the --ids-out file of an audit run (dry run unless --confirm)")
    ap.add_argument("--refused-out", metavar="FILE", help="cleanup: the refused planned ids go here, mode 0600")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--i-stopped-zoe-data", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.erase_from:
        if args.confirm:
            if not args.i_stopped_zoe_data:
                print("refused: --confirm writes the palace in-process; stop zoe-data and pass --i-stopped-zoe-data", file=sys.stderr)
                return 2
            up = _service_up()
            if up:
                print(f"refused: {up}; stop zoe-data first", file=sys.stderr)
                return 2
        try:
            plan = json.loads(Path(args.erase_from).expanduser().read_text(encoding="utf-8"))
            rows = load_rows(args.palace)
            turns = load_turns(args.psql_cmd, args.since)       # the link is RECOMPUTED: the plan is a hint
        except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
            print(f"refused: could not read the plan, the palace or the turns ({type(exc).__name__})", file=sys.stderr)
            return 2
        todo, refused = plan_cleanup(rows, turns, plan, window_hours=args.window_hours)
        if refused and args.refused_out:
            _write_private(args.refused_out, {"refused": refused})
        print(json.dumps({"turns_read": turn_dates(turns), "before": _member_counts(todo), "total": sum(len(v) for v in todo.values()),
                          "refused_not_linked_now": len(refused), "window_report_only": len(plan.get("window") or [])}))
        if not args.confirm:
            print("DRY RUN: nothing was changed. Add --confirm --i-stopped-zoe-data (zoe-data stopped) to forget the verified rows.")
            return 0
        from memory_service import MemoryService

        done = asyncio.run(cleanup(todo, svc=MemoryService(data_dir=str(Path(args.palace).expanduser()))))
        left, _ = plan_cleanup(load_rows(args.palace), turns, plan, window_hours=args.window_hours)
        print(json.dumps({"done": done, "after": _member_counts(left), "remaining": sum(len(v) for v in left.values())}))
        return 5 if left else 0
    try:
        rows = load_rows(args.palace)
        turns = load_turns(args.psql_cmd, args.since)
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"refused: could not read the palace or the turns ({type(exc).__name__})", file=sys.stderr)
        return 2
    report = audit(rows, turns, window_hours=args.window_hours)
    if args.ids_out:
        write_ids(args.ids_out, report)
    c = report["counts"]
    if args.json:
        print(json.dumps({**c, "turns_read": turn_dates(turns), "since": args.since}, indent=2))
    else:
        d = turn_dates(turns)
        print(f"turns read: {d['from']} .. {d['to']}{' (since ' + args.since + ')' if args.since else ' (whole history)'}")
        print(f"forget-command turns read: {c['command_turns']}; rows minted from one: linked={c['linked']} "
              f"{c['linked_by_how']} window(unproven)={c['window']}")
        for m, counts in c["by_member"].items():
            print(f"  {m}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        if not args.ids_out:
            print("(no --ids-out: row ids not written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
