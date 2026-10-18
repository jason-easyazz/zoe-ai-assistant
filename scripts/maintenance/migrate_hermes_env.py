#!/usr/bin/env python3
"""Owner-run, no-print migration: ``~/.hermes/.env`` -> each live unit's OWN env file.

WHY. Hermes is retired and ``~/.hermes`` was deleted (2026-10-18) EXCEPT ``~/.hermes/.env``,
because several live user units still load it (``EnvironmentFile=-%h/.hermes/.env``) and
some secrets exist ONLY there. Deleting that file as it stands could silently break the
voice-regression probe and any child process that needs a provider key. This script moves the
names a unit really needs into that unit's own env file so the Hermes line (and then the file)
can go. It is the repo side of architecture-deep-dive action A6.

CONTRACT.
  * NEVER prints, logs or echoes a value. Output is NAMES, unit names and target PATHS only.
    (Whether a shadowed value DIFFERS is reported as a yes/no, computed from digests.)
  * ``--dry-run`` is the default; ``--apply`` writes. Re-running is a no-op (idempotent).
  * Never overwrites: a name the target already defines (any value) is kept as-is.
  * Before touching an existing target it writes a backup into
    ``~/.zoe/backups/migrate-hermes-env-<ts>/`` (a private dir - NOT a ``.env.bak`` beside the
    live file, which is the sprawl the deep dive flagged) and keeps the target's file mode.
    The BACKUP is always mode 600 inside a 0700 dir, deliberately never looser than the source (a
    secret copy must not be wider than its original). A target created here is mode 600. The
    hourly zoe-log-rotate job deletes these backups after 14 days (local_text_logs).
  * "Only from hermes" is computed per unit against everything else the unit gets: its other
    ``EnvironmentFile=`` lines, inline ``Environment=`` lines (unit + ``.d/*.conf`` drop-ins) and
    names already planned for the same target earlier in the run.
  * A name the unit's code never reads is SKIPPED, decided by looking for the name as a whole
    identifier in the unit's code (a unit running from the repo also covers ``scripts/`` and
    ``tools/``, which it spawns as children). When the code cannot be located, or an SDK reads
    the name from the environment without naming it (provider API keys, in python units), the
    answer is COPY (default to copying when unsure). Hermes-only knobs (browser/terminal/MOA/
    IMAGE/VISION debug, GATEWAY_ALLOW_ALL_USERS, API_SERVER_*) are DROPPED only when that
    check also finds nothing. ``--copy-unread`` copies the skipped names too.
  * ``REVOKED_NAMES`` (``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_HOME_CHANNEL``) are never copied,
    not even with ``--copy-unread``: the token is revoked (Telegram ``getMe`` -> 401); the live
    bot is ``labs/flue-zoe-telegram-2x`` and zoe-data notifies through its loopback
    ``/internal/notify`` route.
  * A name whose code reads a DIFFERENT, longer name is skipped with a note - it is a rename,
    and whether to set the new name is the owner's call, not this script's.
  * A name present in BOTH files with a DIFFERENT value is reported (``ATTENTION``): systemd
    loaded hermes last, so hermes' value won until now. It is NOT copied (never overwrite).

TARGET PER UNIT. ``zoe-data`` -> ``services/zoe-data/.env`` (its own file, mode 600);
``zoe-voice-regression`` loads that same file. Any other unit gets a private
``~/.zoe/env/<unit>.env`` (mode 600) - NOT the repo-root ``.env`` (664 on the box: the last
place for a token) - and the report names the ``EnvironmentFile=`` line to add to that unit.
A unit that needs nothing copied needs no new line: just drop the hermes one.

Usage (owner):
  python3 scripts/maintenance/migrate_hermes_env.py              # dry-run: names + targets
  python3 scripts/maintenance/migrate_hermes_env.py --apply      # write
  ... --unit zoe-health.service                                  # restrict to a unit
Live runbook: docs/knowledge/open-problems.md ("hermes .env retirement").
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional

_NAME_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

# Units that are retired (their removal is scripts/maintenance/cleanup_dead_units.py): never a migration target.
RETIRED_UNITS = frozenset({"hermes-agent.service", "zoe-keepwarm.service"})

# Dead whatever the code says, and why. NEVER copied: a revoked secret in a live env file only hides a 401 behind a name that looks
# configured. Coordinator note 2026-10-18 (A9, #2108): TELEGRAM_BOT_TOKEN in ~/.hermes/.env is revoked (getMe -> 401); the working
# token lives in labs/flue-zoe-telegram-2x/.env and zoe-data reaches members via the bot's loopback /internal/notify.
REVOKED_NAMES = {
    "TELEGRAM_BOT_TOKEN": "revoked (Telegram getMe returns 401); the live bot token is in labs/flue-zoe-telegram-2x/.env and "
                          "zoe-data notifies through the bot's /internal/notify",
    "TELEGRAM_HOME_CHANNEL": "belonged to the revoked Hermes bot; the live bot owns its chat ids",
}

# Hermes-only knobs: dropped when the unit's code does not read them.
DEAD_NAME_PATTERNS = tuple(re.compile(p) for p in (
    r"^BROWSERBASE_", r"^BROWSER_(INACTIVITY|SESSION)_TIMEOUT$", r"^TERMINAL_", r"^MOA_TOOLS_DEBUG$",
    r"^IMAGE_TOOLS_DEBUG$", r"^VISION_TOOLS_DEBUG$", r"^WEB_TOOLS_DEBUG$", r"^GATEWAY_ALLOW_ALL_USERS$",
    r"^API_SERVER_", r"^HERMES_",
))

# Read by a client library straight from the environment, so the name never appears in the unit's own code: copy.
IMPLICIT_SDK_NAMES = frozenset({
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY",
    "CUSTOM_API_KEY", "GITHUB_TOKEN", "GH_TOKEN",
})

# unit -> own env file (relative paths are under the repo root); everything else gets ~/.zoe/env/<unit>.env
KNOWN_TARGETS = {
    "zoe-data.service": "services/zoe-data/.env",
    "zoe-voice-regression.service": "services/zoe-data/.env",
}

_CODE_EXTS = (".py", ".sh", ".js", ".mjs", ".ts", ".toml", ".yml", ".yaml", ".service", ".conf", ".env.example")
_SKIP_DIRS = frozenset({".git", "node_modules", ".claude", ".worktrees", "worktrees", "__pycache__", "tests", "test",
                        "docs", "backups", "labs", ".venv", "venv", "dist"})
_MAX_GREP_BYTES = 2_000_000


@dataclasses.dataclass
class EnvFile:
    path: Path
    values: dict[str, str]          # name -> raw right-hand side (NEVER print)
    lines: dict[str, str]           # name -> the verbatim line to copy (NEVER print)


@dataclasses.dataclass
class UnitPlan:
    unit: str
    target: Optional[Path] = None
    copy: list[str] = dataclasses.field(default_factory=list)
    skipped_unused: list[str] = dataclasses.field(default_factory=list)
    dropped_dead: list[str] = dataclasses.field(default_factory=list)
    dropped_revoked: list[str] = dataclasses.field(default_factory=list)
    shadowed_same: list[str] = dataclasses.field(default_factory=list)
    shadowed_differs: list[str] = dataclasses.field(default_factory=list)
    skipped_empty: list[str] = dataclasses.field(default_factory=list)
    hints: dict[str, list[str]] = dataclasses.field(default_factory=dict)   # skipped name -> names the code reads instead
    needs_envfile_line: Optional[str] = None


def parse_env(path: Path) -> EnvFile:
    values: dict[str, str] = {}
    lines: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return EnvFile(path, values, lines)
    for raw in text.splitlines():
        m = _NAME_RE.match(raw)
        if not m or raw.lstrip().startswith("#"):
            continue
        name = m.group(1)
        values[name] = m.group(2).strip()
        lines[name] = raw.rstrip("\n")
    return EnvFile(path, values, lines)


def _digest(value: str) -> str:
    # compare only; digests never leave this process
    return hashlib.sha256(value.strip().strip("'\"").encode()).hexdigest()


def _expand(token: str, home: Path) -> str:
    return token.replace("%h", str(home)).replace("$HOME", str(home))


@dataclasses.dataclass
class UnitInfo:
    unit: str
    path: Path
    env_files: list[Path]                # EnvironmentFile= targets, in load order, '-' stripped, %h expanded
    inline_names: set[str]               # names set by Environment= (unit + drop-ins)
    exec_paths: list[Path]               # absolute paths named by ExecStart=
    workdir: Optional[Path]


def read_unit(path: Path, home: Path) -> UnitInfo:
    texts = [path.read_text(encoding="utf-8", errors="replace")]
    dropin = path.with_name(path.name + ".d")
    if dropin.is_dir():
        for conf in sorted(dropin.glob("*.conf")):
            texts.append(conf.read_text(encoding="utf-8", errors="replace"))
    env_files: list[Path] = []
    inline: set[str] = set()
    execs: list[Path] = []
    workdir: Optional[Path] = None
    for text in texts:
        for ln in text.splitlines():
            s = ln.strip()
            if not s or s.startswith(("#", ";")):
                continue
            if s.startswith("EnvironmentFile="):
                v = s.split("=", 1)[1].strip().lstrip("-")
                if v:
                    env_files.append(Path(_expand(v, home)))
            elif s.startswith("Environment="):
                for m in re.finditer(r"""(?:^|\s)["']?([A-Za-z_][A-Za-z0-9_]*)=""", s.split("=", 1)[1]):
                    inline.add(m.group(1))
            elif s.startswith("WorkingDirectory="):
                workdir = Path(_expand(s.split("=", 1)[1].strip().lstrip("-"), home))
            elif s.startswith(("ExecStart=", "ExecStartPre=")):
                for tok in s.split("=", 1)[1].split():
                    tok = _expand(tok.lstrip("-@+!"), home)
                    if tok.startswith("/"):
                        execs.append(Path(tok))
    return UnitInfo(path.name, path, env_files, inline, execs, workdir)


def _is_hermes_env(p: Path, hermes_env: Path) -> bool:
    try:
        return p.resolve() == hermes_env.resolve() or str(p).endswith("/.hermes/.env")
    except OSError:
        return str(p) == str(hermes_env)


def code_roots(info: UnitInfo, repo_root: Path) -> list[Path]:
    """Where a unit's code lives, for the 'does it read this name' check. Empty = unknown (so: copy).

    A unit running out of the repo also gets ``scripts/`` and ``tools/``: the service spawns those as child
    processes, and a child inherits the unit's environment."""
    roots: list[Path] = []
    for p in info.exec_paths:
        if p.is_file() and p.suffix in (".py", ".sh", ".js", ".mjs", ".ts"):
            roots.append(p)
    if info.workdir and info.workdir.is_dir():
        roots.append(info.workdir)
        try:
            inside_repo = info.workdir.resolve().is_relative_to(repo_root.resolve())
        except OSError:
            inside_repo = False
        if inside_repo:
            roots += [d for d in (repo_root / "scripts", repo_root / "tools") if d.is_dir()]
    return roots


def _iter_code_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(_CODE_EXTS):
                yield Path(dirpath) / fn


_TOKEN_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
_TOKEN_CACHE: dict[Path, frozenset[str]] = {}


def code_tokens(root: Path) -> frozenset[str]:
    """Every identifier-shaped token in the code under ``root`` (one walk per root per run). An env name is 'read' when it
    appears as a WHOLE token: ``TELEGRAM_BOT_TOKEN`` is not found inside ``ZOE_TELEGRAM_BOT_TOKEN``."""
    if root not in _TOKEN_CACHE:
        found: set[str] = set()
        for f in _iter_code_files(root):
            try:
                if f.stat().st_size > _MAX_GREP_BYTES:
                    continue
                found.update(t.decode() for t in _TOKEN_RE.findall(f.read_bytes()) if len(t) >= 4)
            except OSError:
                continue
        _TOKEN_CACHE[root] = frozenset(found)
    return _TOKEN_CACHE[root]


def code_reads_name(name: str, roots: list[Path]) -> bool:
    return any(name in code_tokens(r) for r in roots)


def alias_hints(name: str, roots: list[Path], limit: int = 3) -> list[str]:
    """Longer names the code reads that END in ``name`` (``TELEGRAM_BOT_TOKEN`` -> ``ZOE_TELEGRAM_BOT_TOKEN``): a rename, not a copy."""
    hits: set[str] = set()
    for r in roots:
        hits.update(t for t in code_tokens(r) if t != name and t.endswith("_" + name) and t.isupper())
    return sorted(hits)[:limit]


def is_dead_name(name: str) -> bool:
    return any(p.search(name) for p in DEAD_NAME_PATTERNS)


def resolve_target(unit: str, repo_root: Path, home: Path) -> tuple[Path, Optional[str]]:
    rel = KNOWN_TARGETS.get(unit)
    if rel:
        return repo_root / rel, None
    stem = unit[:-8] if unit.endswith(".service") else unit
    return home / ".zoe" / "env" / f"{stem}.env", f"EnvironmentFile=-%h/.zoe/env/{stem}.env"


def plan_migration(units_dir: Path, hermes_env: Path, repo_root: Path, home: Path,
                   only_units: Optional[set[str]] = None, copy_unread: bool = False) -> list[UnitPlan]:
    _TOKEN_CACHE.clear()
    hermes = parse_env(hermes_env)
    plans: list[UnitPlan] = []
    planned_by_target: dict[Path, set[str]] = {}
    if not units_dir.is_dir():
        return plans
    for upath in sorted(units_dir.glob("*.service")):
        info = read_unit(upath, home)
        if not any(_is_hermes_env(p, hermes_env) for p in info.env_files):
            continue
        if upath.name in RETIRED_UNITS or (only_units and upath.name not in only_units):
            continue
        plan = UnitPlan(unit=upath.name)
        plan.target, plan.needs_envfile_line = resolve_target(upath.name, repo_root, home)
        target_env = parse_env(plan.target)
        # everything else the unit gets: other env files, inline names, names an earlier unit already planned for this target
        other_vals: dict[str, str] = {}
        for p in info.env_files:
            if not _is_hermes_env(p, hermes_env):
                other_vals.update(parse_env(p).values)
        already = set(other_vals) | info.inline_names | set(target_env.values) | planned_by_target.get(plan.target, set())
        roots = code_roots(info, repo_root)
        python_code = any(r.is_dir() or r.suffix == ".py" for r in roots)
        for name in sorted(hermes.values):
            hv = hermes.values[name]
            if name in already:
                have = other_vals.get(name, target_env.values.get(name))
                if have is not None:
                    (plan.shadowed_same if _digest(have) == _digest(hv) else plan.shadowed_differs).append(name)
                continue
            if name in REVOKED_NAMES:
                plan.dropped_revoked.append(name)       # before every other rule, --copy-unread included
            elif not hv.strip().strip("'\""):
                plan.skipped_empty.append(name)
            elif not roots or copy_unread:
                plan.copy.append(name)                  # code not locatable (unsure), or the owner asked for everything
            elif name in IMPLICIT_SDK_NAMES and python_code:
                plan.copy.append(name)                  # an SDK reads it from the environment without naming it
            elif code_reads_name(name, roots):
                plan.copy.append(name)
            elif is_dead_name(name):
                plan.dropped_dead.append(name)
            else:
                plan.skipped_unused.append(name)
                hint = alias_hints(name, roots)
                if hint:
                    plan.hints[name] = hint
        planned_by_target.setdefault(plan.target, set()).update(plan.copy)
        if not plan.copy:
            plan.needs_envfile_line = None              # nothing lands in the target, so the unit needs no new line
        elif plan.needs_envfile_line and plan.target in info.env_files:
            plan.needs_envfile_line = None              # the unit already loads it
        plans.append(plan)
    return plans


def _atomic_write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        dfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_plans(plans: list[UnitPlan], hermes_env: Path, backup_root: Path, now: Optional[float] = None) -> list[str]:
    """Write every target. Returns report lines (paths and counts only)."""
    out: list[str] = []
    hermes = parse_env(hermes_env)
    stamp = now or time.time()
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(stamp))
    by_target: dict[Path, list[str]] = {}
    for plan in plans:
        if plan.target and plan.copy:
            have = by_target.setdefault(plan.target, [])
            have += [n for n in plan.copy if n not in have]
    for target, names in by_target.items():
        current = parse_env(target)
        names = [n for n in names if n not in current.values]       # never overwrite (and idempotent)
        if not names:
            continue
        existed = target.exists()
        mode = (target.stat().st_mode & 0o777) if existed else 0o600
        if existed:
            bdir = backup_root / f"migrate-hermes-env-{ts}"
            bdir.mkdir(parents=True, exist_ok=True)
            os.chmod(bdir, 0o700)
            bpath = bdir / target.name
            n = 1
            while bpath.exists():
                n += 1
                bpath = bdir / f"{target.name}.{n}"
            shutil.copy2(target, bpath)
            os.chmod(bpath, 0o600)
            out.append(f"  backup: {target} -> {bpath}")
        body = target.read_text(encoding="utf-8", errors="replace") if existed else ""
        if body and not body.endswith("\n"):
            body += "\n"
        body += ("\n# migrated from ~/.hermes/.env by scripts/maintenance/migrate_hermes_env.py "
                 f"({time.strftime('%Y-%m-%d', time.localtime(stamp))})\n")
        body += "\n".join(hermes.lines[n] for n in names) + "\n"
        _atomic_write(target, body, mode)
        out.append(f"  wrote {len(names)} name(s) to {target} (mode {oct(mode)[2:]})")
    return out


def render_report(plans: list[UnitPlan], applied: bool) -> str:
    lines: list[str] = []
    verb = "copied" if applied else "would copy"
    if not plans:
        lines.append("No live unit loads the hermes env file; nothing to migrate.")
    for p in plans:
        lines.append(f"{p.unit}  ->  {p.target}")
        lines.append(f"  {verb} ({len(p.copy)}): {' '.join(p.copy) or '-'}")
        for n in p.dropped_revoked:
            lines.append(f"  DROP {n}: {REVOKED_NAMES[n]}")
        if p.dropped_dead:
            lines.append(f"  drop, hermes-only and unread by this unit's code ({len(p.dropped_dead)}): {' '.join(p.dropped_dead)}")
        if p.skipped_unused:
            lines.append(f"  skip, unread by this unit's code ({len(p.skipped_unused)}): {' '.join(p.skipped_unused)}")
            for n, alts in p.hints.items():
                lines.append(f"    note {n}: this unit's code reads {' / '.join(alts)} instead - a different name, NOT aliased "
                             f"here; the owner decides whether to set it")
        if p.skipped_empty:
            lines.append(f"  skip, empty value in hermes ({len(p.skipped_empty)}): {' '.join(p.skipped_empty)}")
        if p.shadowed_same:
            lines.append(f"  already present with the same value ({len(p.shadowed_same)}): {' '.join(p.shadowed_same)}")
        if p.shadowed_differs:
            lines.append(f"  ATTENTION already present with a DIFFERENT value, kept as is; hermes' value used to win "
                         f"({len(p.shadowed_differs)}): {' '.join(p.shadowed_differs)}")
        if p.needs_envfile_line:
            lines.append(f"  unit edit needed: add  {p.needs_envfile_line}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--home", type=Path, default=Path(os.environ.get("HOME", str(Path.home()))),
                    help="the home that %h in the units, ~/.zoe and the default paths below resolve against")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(default) show names and targets only")
    mode.add_argument("--apply", action="store_true", help="write the targets (after a backup)")
    ap.add_argument("--units-dir", type=Path, default=None)
    ap.add_argument("--hermes-env", type=Path, default=None)
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="the LIVE checkout that owns services/zoe-data/.env (default <home>/assistant)")
    ap.add_argument("--backup-root", type=Path, default=None)
    ap.add_argument("--copy-unread", action="store_true",
                    help="also copy names the unit's code does not read (the safe direction if you are unsure)")
    ap.add_argument("--unit", action="append", default=[], help="restrict to this unit (repeatable)")
    ns = ap.parse_args(argv)
    home = ns.home
    ns.units_dir = ns.units_dir or home / ".config/systemd/user"
    ns.hermes_env = ns.hermes_env or home / ".hermes/.env"
    ns.repo_root = ns.repo_root or Path(os.environ.get("ZOE_REPO_ROOT", home / "assistant"))
    ns.backup_root = ns.backup_root or home / ".zoe/backups"
    if not ns.hermes_env.is_file():
        print(f"{ns.hermes_env} does not exist: nothing to migrate (already retired?).")
        return 0
    plans = plan_migration(ns.units_dir, ns.hermes_env, ns.repo_root, home, set(ns.unit) or None, ns.copy_unread)
    print(f"mode: {'APPLY' if ns.apply else 'DRY-RUN (default; pass --apply to write)'}  hermes env: {ns.hermes_env}")
    if ns.apply:
        for line in apply_plans(plans, ns.hermes_env, ns.backup_root):
            print(line)
    print(render_report(plans, applied=ns.apply))
    if not ns.apply:
        print("\nNothing was written. Values are never printed by this tool.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
