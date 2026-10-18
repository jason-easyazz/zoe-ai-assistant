#!/usr/bin/env python3
"""Which INSTALLED user units have a tracked template, which are retired-but-still-on-the-box, which are neither.
READ-ONLY: parses file names and unit files, never calls systemctl, never writes (architecture deep dive B9).

``unit_drift_check.py`` answers "did the installed copy of a TRACKED unit get the template's changes?". This answers
the question before it: "is every unit on the box tracked at all?". 2026-10-18 the box ran zoe-backup, zoe-health,
zoe-merge-queue and zoe-opensrc-refresh from files that existed nowhere in the repo, and carried ~20 retired units.

Classes, per ``*.service`` / ``*.timer`` in ``~/.config/systemd/user``:
  tracked   - a file of the same name exists under ``scripts/`` (any template directory)
  retired   - listed in RETIRED below with the reason: still installed, removal pending (owner step)
  UNTRACKED - neither: drift. Add a template, or add it to RETIRED with a reason.
Plus a count of ``.bak*`` / ``.disabled`` cruft and, for the latency-critical units, the template-vs-live drift
count from ``unit_drift_check``.

    python3 scripts/maintenance/unit_inventory.py            # table
    python3 scripts/maintenance/unit_inventory.py --summary  # one line (zoe_ground_truth.sh)
    python3 scripts/maintenance/unit_inventory.py --json

Exit 0 always for --summary (a probe line must never break the caller); otherwise 1 when anything is UNTRACKED.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_DIR = Path.home() / ".config" / "systemd" / "user"

# Installed on the box, retired, removal pending. Families in #2110's cleanup_dead_units.py are listed for the
# window before it lands; the rest are retired here (2026-10-18) and go through the same owner step.
RETIRED: dict[str, str] = {
    "hermes-agent.service": "Hermes retired 2026-07-22 (B3 / cleanup_dead_units.py)",
    "openclaw-gateway.service": "OpenClaw retired 2026-07-22 (B3 / cleanup_dead_units.py)",
    "flue-zoe-brain.service": "Flue 1.x brain, superseded by flue-zoe-brain-2x 2026-08-10",
    "zoe-dreaming.service": "the night window runs dreaming itself",
    "zoe-dreaming.timer": "the night window runs dreaming itself",
    "zoe-keepwarm.service": "warmed the retired Hermes agent pool",
    "zoe-keepwarm.timer": "warmed the retired Hermes agent pool",
    "bonsai-server.service": "Bonsai side model, retired",
    "zoe-purge-presence.service": "disabled; purges the legacy SQLite zoe.db (production is Postgres)",
    "zoe-purge-presence.timer": "disabled; purges the legacy SQLite zoe.db (production is Postgres)",
    "llama-server-e2b-fallback.service": "E2B fallback for the retired 12B daytime lane; binds 0.0.0.0:11434",
    "zoe-12b-postboot.service": "one-shot 12B boot validation, self-disabled; the 12B runs only in the night window",
    "zoe-brain-boot-check.service": "one-shot 12B carveout check, self-disabled",
}


def template_names(repo: Path) -> set[str]:
    out = set()
    for ext in ("*.service", "*.timer", "*.slice"):
        out |= {p.name for p in (repo / "scripts").rglob(ext) if p.is_file()}
    return out


def inventory(live_dir: Path, repo: Path) -> dict:
    names = template_names(repo)
    rows = []
    for p in sorted(list(live_dir.glob("*.service")) + list(live_dir.glob("*.timer"))):
        if not p.is_file():
            continue
        cls = "tracked" if p.name in names else ("retired" if p.name in RETIRED else "UNTRACKED")
        rows.append({"unit": p.name, "class": cls, "why": RETIRED.get(p.name, "")})
    cruft = sorted(p.name for p in live_dir.iterdir() if ".bak" in p.name or p.name.endswith(".disabled"))
    return {"units": rows, "cruft": cruft}


def drift_count(live_dir: Path, repo: Path) -> int | None:
    try:
        sys.path.insert(0, str(repo / "scripts" / "maintenance"))
        import unit_drift_check as udc  # noqa: PLC0415
        findings, _ = udc.check(udc.DEFAULT_UNITS, repo / "scripts" / "setup" / "systemd", live_dir,
                                str(Path.home()))
        return sum(1 for f in findings if f.kind in ("missing", "differs"))
    except Exception:  # noqa: BLE001 - a probe line degrades, it never raises
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--live-dir", type=Path, default=LIVE_DIR)
    ap.add_argument("--repo", type=Path, default=REPO_ROOT)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--summary", action="store_true")
    mode.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    if not ns.live_dir.is_dir():
        print(f"units: no {ns.live_dir} (off-host)")
        return 0 if ns.summary else 2
    inv = inventory(ns.live_dir, ns.repo)
    count = {c: [r["unit"] for r in inv["units"] if r["class"] == c] for c in ("tracked", "retired", "UNTRACKED")}
    if ns.json:
        print(json.dumps({**inv, "drift": drift_count(ns.live_dir, ns.repo)}, indent=2))
    elif ns.summary:
        d = drift_count(ns.live_dir, ns.repo)
        line = (f"units: {len(inv['units'])} installed = {len(count['tracked'])} tracked + "
                f"{len(count['retired'])} retired-pending-removal + {len(count['UNTRACKED'])} UNTRACKED"
                f"{' (' + ', '.join(count['UNTRACKED']) + ')' if count['UNTRACKED'] else ''}; "
                f"{len(inv['cruft'])} .bak/.disabled files; template drift: "
                f"{'?' if d is None else d} differs/missing (unit_drift_check.py)")
        print(line)
        return 0
    else:
        for r in inv["units"]:
            print(f"{r['class']:10} {r['unit']:40} {r['why']}")
        print(f"\ncruft ({len(inv['cruft'])}): {' '.join(inv['cruft'])}")
    return 1 if count["UNTRACKED"] else 0


if __name__ == "__main__":
    sys.exit(main())
