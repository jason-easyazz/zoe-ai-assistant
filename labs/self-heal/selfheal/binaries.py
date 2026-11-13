"""Every system and engine binary the loop runs is resolved ONCE, to an absolute path, from a fixed
trusted PATH (never the inherited one), checked, and recorded in the audit log.

A path is refused when the file (or its real path) is writable by anyone but its owner, is owned by
someone other than root or the running user, or lives under the heal worktrees, the state dir or a
temp dir. An unresolved name never falls back to the inherited PATH: running it raises.
"""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

TRUSTED_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
TEMP_ROOTS = ("/tmp", "/var/tmp", "/dev/shm", tempfile.gettempdir())
NAMES = ("git", "gh", "systemctl", "true", "false", "flock", "bwrap", "prlimit", "pgrep", "bash")
RESOLVED: dict = {}   # name -> absolute path, set by resolve_all() at Runner start


class UnsafeBinary(ValueError):
    def __init__(self, name: str, why: str):
        super().__init__(f"{name}:{why}")
        self.name, self.why = name, why


def trusted_path() -> str:
    return TRUSTED_PATH


def _under(p: Path, roots) -> bool:
    return any(p == Path(r).resolve() or Path(r).resolve() in p.parents for r in roots if r)


def check(name: str, path: str, cfg=None) -> str:
    if not os.path.isabs(path):
        raise UnsafeBinary(name, "not_absolute")
    real = Path(path).resolve()
    if not (real.is_file() and os.access(real, os.X_OK)):
        raise UnsafeBinary(name, "not_executable")
    for p in {Path(path), real}:
        st = p.lstat() if p.is_symlink() else p.stat()
        if not p.is_symlink() and st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise UnsafeBinary(name, "group_or_world_writable")
        if st.st_uid not in (0, os.getuid()):
            raise UnsafeBinary(name, "foreign_owner")
    roots = list(TEMP_ROOTS) + ([cfg.worktree_root, cfg.state_dir] if cfg is not None else [])
    if _under(Path(os.path.abspath(path)), roots) or _under(real, roots):
        raise UnsafeBinary(name, "under_worktree_state_or_temp")
    return os.path.abspath(path)


def find(name: str, cfg=None) -> str | None:
    """Absolute + checked path for `name` (or an absolute configured path), else None."""
    if os.path.isabs(name):
        return check(os.path.basename(name), name, cfg) if os.path.exists(name) else None
    for d in trusted_path().split(":"):
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return check(name, cand, cfg)
    return None


def resolve_all(cfg) -> dict:
    """Resolve the system binaries and both engine CLIs. Raises UnsafeBinary on a refused path."""
    RESOLVED.clear()
    for name in NAMES:
        p = find(name, cfg)
        if p:
            RESOLVED[name] = p
    for name, configured in (("codex", cfg.codex_bin), ("claude", cfg.claude_bin)):
        p = find(configured, cfg)
        if p:
            RESOLVED[name] = p
    return dict(RESOLVED)


def which(name: str, cfg=None) -> str:
    """The resolved absolute path; resolves on demand when the runner has not. Never the inherited PATH."""
    p = RESOLVED.get(name) or find(name, cfg)
    if not p:
        raise FileNotFoundError(f"{name} not found on the trusted PATH")
    return p
