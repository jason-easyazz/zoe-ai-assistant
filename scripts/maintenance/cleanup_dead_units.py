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
  3. no ``<unit>.wants/`` / ``.requires/`` / ``.upholds/`` link directory (``default.target.wants`` etc.) still links to it;
  4. a dependency from a family that STAYS (blocked, active, or not selected with ``--only``) blocks it too, to a fixpoint: only
     dependencies from families confirmed removable in this run are ignored.
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


def _logical_lines(text: str) -> list[str]:
    """systemd logical lines: a trailing backslash continues the directive on the next physical line
    (``Requires=network.target \\`` + ``hermes-agent.service`` is ONE directive naming both units).
    Comment lines inside a continuation are skipped, as systemd does."""
    out: list[str] = []
    cur = ""
    for raw in text.splitlines():
        s = raw.strip()
        if cur and s.startswith(("#", ";")):
            continue
        if raw.rstrip().endswith("\\"):
            cur += raw.rstrip()[:-1] + " "
            continue
        out.append(cur + raw)
        cur = ""
    if cur:
        out.append(cur)
    return out


def _directive_refs(text: str, unit_names: set[str]) -> list[str]:
    hits = []
    for ln in _logical_lines(text):
        s = ln.strip()
        if not s or s.startswith(("#", ";")) or "=" not in s:
            continue
        key, _, val = s.partition("=")
        if key.strip() in DEP_DIRECTIVES and any(re.search(rf"(?<![\w.-]){re.escape(n)}(?![\w.-])", val) for n in unit_names):
            hits.append(key.strip())
    return hits


DEP_DIR_SUFFIXES = (".wants", ".requires", ".upholds")


def external_references(units_dir: Path, family: str, gone: set[str]) -> list[str]:
    """Everything that stays on the box and depends on this family: unit files and drop-ins naming it in a dependency directive,
    and ``<unit>.wants/`` / ``.requires/`` / ``.upholds/`` link directories holding a link to it. ``gone`` is the set of file names
    being removed in this run: files of a family that is confirmed removable (and a dependency directory OWNED by a removed unit) do
    not count, because they leave with it. A kept family - blocked, active, or not selected by ``--only`` - keeps counting."""
    names = set(FAMILIES[family][0])
    out: list[str] = []
    for p in sorted(units_dir.iterdir()):
        if p.name in gone or ".bak" in p.name or p.name.endswith(".disabled"):
            continue
        if p.is_dir() and p.name.endswith(DEP_DIR_SUFFIXES):
            owner = p.name.rsplit(".", 1)[0]
            if owner in gone:
                continue
            out += [f"{p.name}/{l.name} (link)" for l in p.iterdir() if l.name in names]
            continue
        if p.is_dir() and p.name.endswith(".d"):
            if p.name[:-2] in gone:
                continue
            files = sorted(p.glob("*.conf"))
        else:
            files = [p] if p.is_file() else []
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


def assess_all(units_dir: Path, families: list[str], state: StateFn, repo_root: Optional[Path] = None) -> list[dict]:
    """State checks per family, then dependencies to a fixpoint: a family starts removable when its own units are disabled and
    inactive; a dependency from a file that STAYS (a blocked family, or one outside ``families``) blocks it, and blocking it makes
    ITS files stay too, which may block a third. Only dependencies from families confirmed removable in this run are ignored."""
    results = []
    for family in families:
        files = family_files(units_dir, family)
        blockers: list[str] = []
        states = {}
        for n in FAMILIES[family][0]:
            # ask systemd about EVERY family member, local file or not: a timer loaded/installed from another user-unit directory
            # still fires the service we would move away
            enabled, active = state(n)
            states[n] = (enabled, active)
            if enabled not in _DEAD_STATES_ENABLED:
                blockers.append(f"{n} is {enabled!r} (enabled)")
            if active in _LIVE_ACTIVE:
                blockers.append(f"{n} is {active}")
        results.append({"family": family, "files": files, "states": states, "blockers": blockers,
                        "mentions": repo_mentions(repo_root, family)})
    while True:
        gone = {p.name for r in results if r["files"] and not r["blockers"] for p in r["files"]}
        changed = False
        for r in results:
            if not r["files"] or r["blockers"]:
                continue
            refs = external_references(units_dir, r["family"], gone)
            if refs:
                r["blockers"] += [f"referenced by {x}" for x in refs]
                changed = True
        if not changed:
            return results


def _restrict(path: Path) -> None:
    """Owner-only, but still usable: regular files 0600, directories 0700 (a drop-in ``.d`` dir needs its execute bit, or the
    hourly ``prune_adhoc_backups`` rmtree cannot traverse it and a secret-bearing backup never expires)."""
    if path.is_dir() and not path.is_symlink():
        os.chmod(path, 0o700)
        for sub in path.rglob("*"):
            if sub.is_symlink():
                continue
            os.chmod(sub, 0o700 if sub.is_dir() else 0o600)
    else:
        os.chmod(path, 0o600)


def apply_removal(units_dir: Path, results: list[dict], backup_root: Path, now: Optional[float] = None,
                  ctl: Optional[Callable[[list[str]], int]] = None) -> tuple[list[str], bool]:
    """Move the removable families' files away, then reload systemd. Returns ``(report lines, ok)``; ``ok`` is False when
    ``daemon-reload`` failed (the manager would keep the removed definitions in memory)."""
    out: list[str] = []
    ok = True
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
            _restrict(dest)                             # a unit file can carry Environment= secrets: files 0600, dirs 0700
            out.append(f"  moved {f.name} -> {dest}")
            if f.name.endswith((".service", ".timer")):
                moved_units.append(f.name)
    if moved_units:
        if ctl(["daemon-reload"]) != 0:
            ok = False
            out.append("  FAILED: systemctl --user daemon-reload returned non-zero; the manager may still hold the removed units")
        else:
            for u in moved_units:
                ctl(["reset-failed", u])
            out.append(f"  daemon-reload + reset-failed for {len(moved_units)} unit(s)")
        out.append(f"  restore with:  mv {bdir}/<file> {units_dir}/ && systemctl --user daemon-reload")
    return out, ok


def _pruned_root(home: Path) -> Path:
    """The dir the hourly job prunes (ZOE_ADHOC_BACKUP_DIR from env / saved settings, else <home>/.zoe/backups)."""
    import importlib.util

    mod = sys.modules.get("rotate_service_logs")
    if mod is None:
        spec = importlib.util.spec_from_file_location("rotate_service_logs", Path(__file__).resolve().parent / "rotate_service_logs.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
    return mod.pruned_backup_root(home)


def _warn_if_unpruned(backup_root: Path, home: Path) -> None:
    pruned = _pruned_root(home)
    try:
        backup_root.resolve().relative_to(pruned.resolve())
    except ValueError:
        print(f"WARNING: --backup-root {backup_root} is outside {pruned}, which the hourly job prunes: the backup there will NOT "
              f"expire automatically - delete it by hand.", file=sys.stderr)


def main(argv: Optional[list[str]] = None, *, state: StateFn = systemctl_state,
         ctl: Optional[Callable[[list[str]], int]] = None) -> int:
    home = Path(os.environ.get("HOME", str(Path.home())))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(default) report what would be removed")
    mode.add_argument("--apply", action="store_true", help="move the unit files to a backup dir and reload systemd")
    ap.add_argument("--units-dir", type=Path, default=home / ".config" / "systemd" / "user")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--backup-root", type=Path, default=None,
                    help="default: the directory the hourly job prunes (ZOE_ADHOC_BACKUP_DIR, else ~/.zoe/backups)")
    ap.add_argument("--only", action="append", choices=sorted(FAMILIES), help="restrict to a family (repeatable)")
    ns = ap.parse_args(argv)
    explicit_backup = ns.backup_root is not None
    ns.backup_root = ns.backup_root if explicit_backup else _pruned_root(home)
    if ns.apply and explicit_backup:
        _warn_if_unpruned(ns.backup_root, home)
    if not ns.units_dir.is_dir():
        print(f"no such units dir: {ns.units_dir}", file=sys.stderr)
        return 1
    results = assess_all(ns.units_dir, list(ns.only or FAMILIES), state, ns.repo_root)
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
    reloaded = True
    if ns.apply:
        lines, reloaded = apply_removal(ns.units_dir, results, ns.backup_root, ctl=ctl)
        for line in lines:
            print(line)
    else:
        print("\nNothing was changed.")
    return 1 if ns.apply and (not reloaded or any(r["blockers"] for r in results)) else 0


if __name__ == "__main__":
    sys.exit(main())
