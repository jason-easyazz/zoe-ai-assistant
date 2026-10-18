#!/usr/bin/env python3
"""Owner-run: remove the user-systemd units of retired installs. DRY RUN by default.

Architecture deep dive B9 (2026-10-18). The box still carries unit files for installs that are gone: Hermes
(``hermes-agent``), OpenClaw (``openclaw-gateway``), the Flue 1.x brain (``flue-zoe-brain``, superseded by ``flue-zoe-brain-2x``),
the old nightly dreaming timer (``zoe-dreaming``; the night window runs dreaming itself), the keep-warm pair (``zoe-keepwarm``,
which warmed the Hermes agent pool) and the Bonsai side model (``bonsai-server``). Each is disabled and inactive, and each is a
trap: ``zoe-dreaming.service`` and ``zoe-keepwarm.service`` write to ``~/training/logs`` and load ``~/.hermes/.env``, both gone.

A family is removed only when EVERY check passes; otherwise it is reported with the reason and left alone:
  1. every unit of the family is not enabled (``disabled`` / ``static`` / ``masked`` / not found) and not active/activating;
  2. no OTHER live unit file names it in a dependency directive (Requires/Wants/BindsTo/PartOf/Upholds/OnFailure/OnSuccess/
     Conflicts/Before/After/Unit/Also), comments ignored;
  3. nothing in ``<default|timers|sockets>.target.wants`` etc. still links to it.
Tracked repo code that merely MENTIONS a unit name (a health check, an installer's rollback) is listed for information: those
references degrade gracefully when the unit is absent and are not blockers.

``--apply`` MOVES the files (not deletes) to ``~/.zoe/backups/dead-units-<ts>/``, then ``daemon-reload`` and ``reset-failed``. It
never touches a unit outside the table below, ``flue-zoe-brain-2x*``, or any ``llama-server`` backup.

    python3 scripts/maintenance/cleanup_dead_units.py            # dry run
    python3 scripts/maintenance/cleanup_dead_units.py --apply
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

#: family -> (unit names, extra file globs inside the units dir). Globs are matched against file/dir NAMES in the units dir.
FAMILIES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "hermes-agent": (("hermes-agent.service",), ("hermes-agent.service.d", "hermes-agent.service.bak-*")),
    "openclaw-gateway": (("openclaw-gateway.service",), ("openclaw-gateway.service.bak-*",)),
    "flue-zoe-brain": (("flue-zoe-brain.service",), ("flue-zoe-brain.service.d",)),
    "zoe-dreaming": (("zoe-dreaming.service", "zoe-dreaming.timer"), ("zoe-dreaming.service.d",)),
    "zoe-keepwarm": (("zoe-keepwarm.service", "zoe-keepwarm.timer"), ()),
    "bonsai-server": (("bonsai-server.service",), ()),
}

DEP_DIRECTIVES = ("Requires", "Requisite", "Wants", "BindsTo", "PartOf", "Upholds", "OnFailure", "OnSuccess", "Conflicts",
                  "Before", "After", "Unit", "Also")
_DEAD_STATES_ENABLED = {"disabled", "static", "masked", "masked-runtime", "not-found", "indirect", "generated", "transient", ""}
_LIVE_ACTIVE = {"active", "activating", "reloading", "deactivating"}

StateFn = Callable[[str], tuple[str, str]]


def systemctl_state(unit: str) -> tuple[str, str]:
    """``(is-enabled, is-active)`` for a user unit, read-only."""
    def one(verb: str) -> str:
        r = subprocess.run(["systemctl", "--user", verb, unit], capture_output=True, text=True, timeout=15)
        text = (r.stdout or r.stderr).strip().splitlines()
        return text[0].strip() if text else ""
    return one("is-enabled"), one("is-active")


def family_files(units_dir: Path, family: str) -> list[Path]:
    names, globs = FAMILIES[family]
    found: list[Path] = []
    for n in names:
        p = units_dir / n
        if p.exists() or p.is_symlink():
            found.append(p)
    for g in globs:
        found += sorted(units_dir.glob(g))
    return found


def _directive_refs(text: str, unit_names: set[str]) -> list[str]:
    hits = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith(("#", ";")) or "=" not in s:
            continue
        key, _, val = s.partition("=")
        if key.strip() in DEP_DIRECTIVES and any(re.search(rf"(?<![\w.-]){re.escape(n)}(?![\w.-])", val) for n in unit_names):
            hits.append(key.strip())
    return hits


def external_references(units_dir: Path, family: str) -> list[str]:
    """Live unit files (and drop-ins) outside the retired set that name this family in a dependency directive, plus ``*.wants`` links."""
    names = set(FAMILIES[family][0])
    # files of EVERY retired family are skipped: they are all dead, and one dead unit ordering itself After= another is not a live dependency
    own = {p.name for fam in FAMILIES for p in family_files(units_dir, fam)}
    out: list[str] = []
    for p in sorted(units_dir.iterdir()):
        if p.name in own or ".bak" in p.name or p.name.endswith(".disabled"):
            continue
        files = [p] if p.is_file() else (sorted(p.glob("*.conf")) if p.is_dir() and p.name.endswith(".d") else [])
        if p.is_dir() and p.name.endswith(".wants"):
            out += [f"{p.name}/{l.name} (link)" for l in p.iterdir() if l.name in names]
            continue
        if p.is_dir() and p.name.endswith(".d") and p.name[:-2] in own:
            continue
        for f in files:
            try:
                refs = _directive_refs(f.read_text(encoding="utf-8", errors="replace"), names)
            except OSError:
                continue
            out += [f"{f.relative_to(units_dir)} ({r}=)" for r in refs]
    return out


def repo_mentions(repo_root: Optional[Path], family: str) -> list[str]:
    """Tracked code (scripts/, tools/, services/) that mentions a unit name - information only."""
    if not repo_root:
        return []
    names = FAMILIES[family][0]
    pat = re.compile("|".join(re.escape(n) for n in names))
    own_templates = {f"scripts/setup/systemd/{n}" for n in names}
    out: list[str] = []
    for top in ("scripts", "tools", "services"):
        base = repo_root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in {"node_modules", "__pycache__", "tests", ".git", "venv"}]
            for fn in filenames:
                if not fn.endswith((".py", ".sh", ".service", ".timer", ".js", ".ts", ".yml")):
                    continue
                path = Path(dirpath) / fn
                rel = str(path.relative_to(repo_root))
                if rel in own_templates or Path(__file__).resolve() == path.resolve():
                    continue
                try:
                    n = sum(1 for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if pat.search(ln))
                except OSError:
                    continue
                if n:
                    out.append(f"{rel} ({n})")
    return out


def assess(units_dir: Path, family: str, state: StateFn, repo_root: Optional[Path] = None) -> dict:
    files = family_files(units_dir, family)
    blockers: list[str] = []
    states = {}
    for n in FAMILIES[family][0]:
        if not (units_dir / n).exists():
            continue
        enabled, active = state(n)
        states[n] = (enabled, active)
        if enabled not in _DEAD_STATES_ENABLED:
            blockers.append(f"{n} is {enabled!r} (enabled)")
        if active in _LIVE_ACTIVE:
            blockers.append(f"{n} is {active}")
    refs = external_references(units_dir, family)
    blockers += [f"referenced by {r}" for r in refs]
    return {"family": family, "files": files, "states": states, "blockers": blockers,
            "mentions": repo_mentions(repo_root, family)}


def apply_removal(units_dir: Path, results: list[dict], backup_root: Path, now: Optional[float] = None,
                  ctl: Optional[Callable[[list[str]], int]] = None) -> list[str]:
    out: list[str] = []
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    ctl = ctl or (lambda argv: subprocess.run(["systemctl", "--user", *argv], timeout=60).returncode)
    moved_units: list[str] = []
    bdir = backup_root / f"dead-units-{ts}"
    for r in results:
        if r["blockers"] or not r["files"]:
            continue
        bdir.mkdir(parents=True, exist_ok=True)
        os.chmod(bdir, 0o700)
        for f in r["files"]:
            dest = bdir / f.name
            shutil.move(str(f), str(dest))
            out.append(f"  moved {f.name} -> {dest}")
            if f.name.endswith((".service", ".timer")):
                moved_units.append(f.name)
    if moved_units:
        ctl(["daemon-reload"])
        for u in moved_units:
            ctl(["reset-failed", u])
        out.append(f"  daemon-reload + reset-failed for {len(moved_units)} unit(s)")
        out.append(f"  restore with:  mv {bdir}/<file> {units_dir}/ && systemctl --user daemon-reload")
    return out


def main(argv: Optional[list[str]] = None, *, state: StateFn = systemctl_state,
         ctl: Optional[Callable[[list[str]], int]] = None) -> int:
    home = Path(os.environ.get("HOME", str(Path.home())))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(default) report what would be removed")
    mode.add_argument("--apply", action="store_true", help="move the unit files to a backup dir and reload systemd")
    ap.add_argument("--units-dir", type=Path, default=home / ".config" / "systemd" / "user")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--backup-root", type=Path, default=home / ".zoe" / "backups")
    ap.add_argument("--only", action="append", choices=sorted(FAMILIES), help="restrict to a family (repeatable)")
    ns = ap.parse_args(argv)
    if not ns.units_dir.is_dir():
        print(f"no such units dir: {ns.units_dir}", file=sys.stderr)
        return 1
    results = [assess(ns.units_dir, f, state, ns.repo_root) for f in (ns.only or FAMILIES)]
    print(f"mode: {'APPLY' if ns.apply else 'DRY-RUN (default; pass --apply to move the files)'}   units dir: {ns.units_dir}")
    for r in results:
        if not r["files"]:
            print(f"{r['family']}: already gone")
            continue
        verdict = "BLOCKED" if r["blockers"] else "removable"
        print(f"{r['family']}: {verdict}")
        for n, (en, ac) in r["states"].items():
            print(f"    {n}: enabled={en or '?'} active={ac or '?'}")
        for f in r["files"]:
            print(f"    file: {f.name}")
        for b in r["blockers"]:
            print(f"    BLOCKER: {b}")
        if r["mentions"]:
            print("    tracked code that mentions it (information; degrades gracefully, not a blocker): " + ", ".join(r["mentions"]))
    if ns.apply:
        for line in apply_removal(ns.units_dir, results, ns.backup_root, ctl=ctl):
            print(line)
    else:
        print("\nNothing was changed.")
    return 1 if any(r["blockers"] for r in results) and ns.apply else 0


if __name__ == "__main__":
    sys.exit(main())
