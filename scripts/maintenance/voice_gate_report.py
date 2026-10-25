#!/usr/bin/env python3
"""Print the voice gate's last results for zoe_ground_truth.sh, one lane per line.

The probe's shared result file is overwritten by every run (nightly on main AND
each PR-landing probe), so a PR pass used to mask a failed nightly. Lanes:
  * nightly main : voice_regression_last_main.json, else the newest main-lane
                   record in the trend file (labelled), else "no record".
  * PR/other     : the shared file when it is not a main run, else the newest
                   non-main record in the trend file.
Read-only; never raises (an unreadable file is skipped)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _is_main(rec: dict[str, Any], live: str) -> bool:
    if rec.get("self_test"):
        return False
    rev = rec.get("revision")
    sd = rev.get("service_dir") if isinstance(rev, dict) else None
    if not sd:
        return False
    try:
        return Path(sd).resolve() == Path(live).resolve()
    except OSError:
        return False


def _load(path: Path) -> dict[str, Any] | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _trend(path: Path, live: str, want_main: bool) -> dict[str, Any] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines[-500:]):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and not rec.get("self_test") and _is_main(rec, live) == want_main:
            return rec
    return None


def _fmt(label: str, rec: dict[str, Any], note: str = "") -> str:
    ts = rec.get("timestamp") or rec.get("created_at") or "unknown time"
    reason = rec.get("reason") or ""
    return f"voice gate ({label}, {ts}{note}): {rec.get('status')}" + (f" - {reason}" if reason else "")


def report(cache_dir: Path, live: str) -> list[str]:
    shared = _load(cache_dir / "voice_regression_last.json")
    main = _load(cache_dir / "voice_regression_last_main.json")
    trend_path = cache_dir / "voice_regression_trend.jsonl"
    out = []
    if main:
        out.append(_fmt("nightly main", main))
    else:
        t = _trend(trend_path, live, True)
        if t:
            out.append(_fmt("nightly main", t, ", from trend"))
        elif shared and _is_main(shared, live):
            out.append(_fmt("nightly main", shared, ", from shared file"))
        else:
            out.append("voice gate (nightly main): no record")
    if shared and not _is_main(shared, live):
        out.append(_fmt("latest PR probe", shared))
    elif not shared:
        t = _trend(trend_path, live, False)
        if t:
            out.append(_fmt("latest PR probe", t, ", from trend"))
    return out


def main() -> int:
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / ".cache" / "zoe"
    live = os.environ.get("ZOE_VOICE_MAIN_CHECKOUT") or str(Path.home() / "assistant")
    for line in report(cache, live):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
