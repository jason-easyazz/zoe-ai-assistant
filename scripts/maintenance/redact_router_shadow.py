#!/usr/bin/env python3
"""Owner-run: strip utterance text from the EXISTING router shadow log (and intent-miss log). DRY RUN by default; prints COUNTS only.

The log (``services/zoe-data/data/router_head_shadow.jsonl`` + rotated ``.N`` segments) keeps, since 2026-07-14, the household's raw
utterances in a ``utt_text`` field beside the 12-hex hash (``ZOE_ROUTER_SHADOW_TEXT=1``). From now on the hourly log-rotation job
ages that text out after ``ZOE_TEXT_LOG_RETENTION_DAYS`` (default 30); this script does the same ONCE, on demand, for what is
already there, and can go further.

  --days N      strip text older than N days (default: the retention window, 30)
  --all         strip the text from EVERY record, whatever its age (the "forget the whole corpus" option)
  --log NAME    router_shadow | intent_misses (repeatable; default both)

A stripped record keeps its hash and routing features, so the self-training miner still reads it and re-joins the words through
``chat_messages`` (labs/router-selftrain, BOOTSTRAP); only turns that never reached ``chat_messages`` lose their text. If you want to
mine the full backlog first, run ``labs/router-selftrain/mine_candidates.py`` BEFORE ``--apply`` (or set
``ZOE_TEXT_LOG_RETENTION_DAYS=keep`` for the hourly job meanwhile).

SAFETY
  * dry run is the default; nothing is written without ``--apply``;
  * ``--apply`` first copies every file it will touch into ``~/.zoe/backups/router-shadow-<ts>/`` (dir 0700, files 0600). THAT
    BACKUP STILL HOLDS THE TEXT: check the result, then delete it (``--drop-backup`` does it for you once the check passes, or
    ``rm -r`` the printed path). A forgotten backup is the very leak this removes, so the hourly zoe-log-rotate job DELETES
    ``router-shadow-*`` backups older than 14 days (``local_text_logs.prune_adhoc_backups``) and goes red if it cannot;
  * a row that is not a JSON object (a torn append, a hand-edit) cannot be judged and may hold words: it is DROPPED, counted in
    "selected", and the post-check fails if any remains;
  * after the rewrite it re-reads every file and checks that the record count is unchanged and that no record selected for
    stripping still carries text; a failed check keeps the backup and exits 1;
  * file modes are kept; rewrites are atomic and re-append lines a writer added meanwhile;
  * the text is never printed.

    python3 scripts/maintenance/redact_router_shadow.py                    # dry run
    python3 scripts/maintenance/redact_router_shadow.py --apply            # window = retention (30 days)
    python3 scripts/maintenance/redact_router_shadow.py --apply --all --drop-backup
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

_MOD = Path(__file__).resolve().parents[2] / "services" / "zoe-data" / "local_text_logs.py"


def load_ltl():
    spec = importlib.util.spec_from_file_location("local_text_logs", _MOD)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_saved_settings() -> None:
    """Fill ZOE_TEXT_LOG_RETENTION_DAYS / ZOE_ROUTER_HEAD_LOG / ZOE_INTENT_MISS_LOG from the zoe-data env files exactly as the hourly
    ``rotate_service_logs.py`` does (the settings live there, not in an owner's shell): otherwise a bare ``--apply`` would strip text
    the owner chose to keep, or leave a custom log untouched. A variable already in the environment wins."""
    mod = sys.modules.get("rotate_service_logs")
    if mod is None:
        spec = importlib.util.spec_from_file_location("rotate_service_logs", Path(__file__).resolve().parent / "rotate_service_logs.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    mod.apply_env_files()


def _records(seg: Path) -> list[dict]:
    out = []
    for line in seg.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _malformed(seg: Path) -> int:
    """Non-blank rows that are not a JSON object. The sweep and every erasure DROP them (fail closed), so they count as selected."""
    n = 0
    for line in seg.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            ok = isinstance(json.loads(line), dict)
        except ValueError:
            ok = False
        n += 0 if ok else 1
    return n


def _selected(ltl, log, rec: dict, cutoff: Optional[float], everything: bool) -> bool:
    return bool(isinstance(rec.get(log.text_field), str) and rec.get(log.text_field)
                and (everything or (cutoff is not None and ltl._ts(rec, log) < cutoff)))


def plan(ltl, logs, cutoff: Optional[float], everything: bool) -> list[tuple]:
    """``[(log, segment, total records, records selected)]`` - counts only. A malformed row is dropped by every pass, so it is
    selected too."""
    rows = []
    for log in logs:
        for seg in ltl.segments(log):
            recs = _records(seg)
            rows.append((log, seg, len(recs), sum(_selected(ltl, log, r, cutoff, everything) for r in recs) + _malformed(seg)))
    return rows


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(default) report counts, change nothing")
    mode.add_argument("--apply", action="store_true", help="back up, then strip")
    ap.add_argument("--days", type=float, default=None, help="strip text older than this many days (default: the retention window)")
    ap.add_argument("--all", action="store_true", help="strip the text from every record, whatever its age")
    ap.add_argument("--log", action="append", choices=["router_shadow", "intent_misses"], help="restrict to a log (repeatable)")
    ap.add_argument("--backup-root", type=Path, default=Path(os.environ.get("HOME", str(Path.home()))) / ".zoe" / "backups")
    ap.add_argument("--drop-backup", action="store_true", help="delete the backup once the post-check passes")
    ns = ap.parse_args(argv)

    load_saved_settings()
    ltl = load_ltl()
    logs = [l for l in ltl.LOGS if not ns.log or l.name in ns.log]
    days = ns.days if ns.days is not None else ltl.retention_days()
    if days is None and not ns.all:
        print("ZOE_TEXT_LOG_RETENTION_DAYS=keep and no --days/--all given: nothing selected.")
        return 0
    now = time.time()
    cutoff = None if ns.all else now - float(days) * 86400.0
    rows = plan(ltl, logs, cutoff, ns.all)
    scope = "EVERY record" if ns.all else f"records older than {days:g} days"
    print(f"mode: {'APPLY' if ns.apply else 'DRY-RUN (default; pass --apply to write)'}   scope: {scope}")
    if not rows:
        print("no log files found.")
        return 0
    total_sel = 0
    for log, seg, total, sel in rows:
        total_sel += sel
        verb = "text " + ("dropped with its record" if log.expire == "drop" else "stripped")
        print(f"  {log.name:14s} {seg}: {total} records, {sel} selected ({verb})")
    print(f"  total selected: {total_sel}")
    if not ns.apply:
        print("\nNothing was written. The text is never printed by this tool.")
        return 0
    if total_sel == 0:
        print("nothing to strip.")
        return 0

    bdir = ns.backup_root / f"router-shadow-{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}"
    bdir.mkdir(parents=True, exist_ok=True)
    os.chmod(bdir, 0o700)
    for i, (_log, seg, _t, sel) in enumerate(rows):
        if sel:
            dest = bdir / f"{i:02d}-{seg.name}"
            shutil.copy2(seg, dest)
            os.chmod(dest, 0o600)
    print(f"backup (STILL HOLDS THE TEXT): {bdir}")

    failed = 0
    for log in logs:
        before = {seg: _records(seg) for seg in ltl.segments(log)}
        kw = {"everything": True} if ns.all else {"older_than": cutoff}
        try:
            ltl._apply(log, **kw)                      # one pass over EVERY segment under the log's write lock (the router's own flock)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAILED {log.name}: {type(exc).__name__}: {exc}")
            continue
        for seg, recs in before.items():
            after = _records(seg) if seg.exists() else []
            still = sum(_selected(ltl, log, r, cutoff, ns.all) for r in after)
            bad = _malformed(seg) if seg.exists() else 0
            kept = len(after) if log.expire == "strip" else None
            if still or bad or (kept is not None and kept < len(recs)):
                failed += 1
                print(f"  CHECK FAILED {seg}: {still} selected records still carry text, {bad} malformed rows remain, "
                      f"{len(recs)} -> {len(after)} records")
            else:
                print(f"  ok {seg}: {len(recs)} -> {len(after)} records, 0 selected remain")
    if failed:
        print(f"{failed} file(s) failed the post-check: the backup is KEPT at {bdir}")
        return 1
    if ns.drop_backup:
        # The backup holds the RAW text: a deletion that did not happen must not read as done. No ignore_errors, and "deleted" is
        # printed only once the path is verified gone.
        try:
            shutil.rmtree(bdir)
        except OSError as exc:
            print(f"ERROR: could not delete the backup ({type(exc).__name__}): it may STILL HOLD the raw utterance text at {bdir} "
                  f"- delete it by hand:  rm -r {bdir}", file=sys.stderr)
            return 1
        if os.path.lexists(bdir):
            print(f"ERROR: the backup is STILL PRESENT and holds the raw utterance text at {bdir} - delete it by hand:  "
                  f"rm -r {bdir}", file=sys.stderr)
            return 1
        print("backup deleted (post-check passed; verified gone).")
    else:
        print(f"Post-check passed. Delete the backup when satisfied:  rm -r {bdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
