"""The sandbox every repo-code gate runs in (pytest lanes, structure validator, py_compile, the
verify re-run, python health checks, the detector): ENGINE-WRITTEN code never runs as the service
user with full access.

Backend `bwrap` (bubblewrap, unprivileged user namespaces; the strongest that works on this box,
see README "Sandbox"): new user / pid / net / ipc / uts namespaces (no network, loopback included),
the whole filesystem READ-ONLY, then empty read-only views over /home, the real home, /root, /run
(user bus, docker socket, agent sockets), /mnt, /media, /var/tmp, the live checkout, the state dir
and the heal repo; a private on-disk /tmp; the interpreter's own library dirs re-bound read-only;
the heal worktree the ONLY writable path. Inside: rlimits (address space, processes, file size,
core) via prlimit, a clean allow-listed environment. A timeout kills the whole process group; the
pid namespace dies with it, so a daemonised grandchild cannot survive.

Backend `none`: REFUSES to run anything unless dispatch is `dry` (fail closed, reason recorded).
There is no knob for writable paths or network: the config can only lower the limits.
"""
from __future__ import annotations

import functools
import tempfile
import xml.etree.ElementTree as ET
import os
import shutil
import signal
import site
import subprocess
import sys
from pathlib import Path

from . import binaries
from .config import HARD_LIVE_CHECKOUT
from .safety import Refused

HIDE = ("/home", "/root", "/run", "/mnt", "/media", "/srv", "/var/tmp", "/data")
FILE_MB = 512
ENV_KEEP = ("LANG", "LC_ALL", "TERM")


@functools.lru_cache(maxsize=None)
def best_backend() -> tuple[str, str]:
    """('bwrap', 'ok') when an unprivileged bwrap with every namespace starts here, else ('none', why)."""
    try:
        bw = binaries.find("bwrap")
    except binaries.UnsafeBinary as e:
        return "none", f"bwrap_unsafe:{e.why}"
    if not bw:
        return "none", "bwrap_not_installed"
    try:
        r = subprocess.run([bw, "--unshare-all", "--die-with-parent", "--ro-bind", "/", "/", "--proc", "/proc",
                            "--", "/bin/true"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return "none", f"bwrap_probe_{type(e).__name__}"
    return ("bwrap", "ok") if r.returncode == 0 else ("none", "user_namespaces_unavailable")


def _ro_dirs() -> list:
    """The interpreter's library dirs (stdlib, venv, site-packages), re-bound read-only."""
    out = {os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix),
           os.path.dirname(os.path.realpath(sys.executable))}
    out.update(os.path.realpath(p) for p in sys.path
               if p and ("site-packages" in p or "dist-packages" in p) and os.path.isdir(p))
    return sorted(out)


class Sandbox:
    def __init__(self, cfg):
        self.cfg = cfg
        best, why = best_backend()
        self.backend, self.why = (best, why) if cfg.sandbox in ("auto", best) else ("none", f"{cfg.sandbox}_unavailable")

    def hidden(self) -> list:
        c = self.cfg
        return [*HIDE, str(Path.home()), HARD_LIVE_CHECKOUT, c.live_checkout, c.state_dir, c.repo]

    def env(self, tmp: str) -> dict:
        env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
        env.update(PATH=binaries.TRUSTED_PATH, HOME=tmp, TMPDIR=tmp, PYTHONDONTWRITEBYTECODE="1")
        if site.ENABLE_USER_SITE and os.path.isdir(site.getusersitepackages()):
            env["PYTHONUSERBASE"] = site.getuserbase()   # the user-site packages are re-bound read-only
        return env

    def argv(self, cmd: list, wt: str, rw=None, *, tree_ro: bool = False, ro=()) -> list:
        c, wt = self.cfg, os.path.realpath(wt)
        # prlimit runs INSIDE the sandbox, so by its real path (a symlink under /tmp would be hidden)
        limits = [os.path.realpath(binaries.which("prlimit")), f"--as={c.sandbox_mem_mb << 20}",
                  f"--nproc={c.sandbox_procs}",
                  f"--fsize={FILE_MB << 20}", "--core=0", "--"]
        a = [binaries.which("bwrap"), "--unshare-all", "--die-with-parent", "--new-session",
             "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
        # bwrap 0.6.1 has no `--size`, so a writable tmpfs would be RAM with no cap: /tmp is an EMPTY
        # READ-ONLY view instead, and TMPDIR / HOME point at a scratch dir INSIDE the worktree, whose
        # growth is capped (`sandbox_disk_mb`, checked after the run) and which is deleted after it
        a += ["--tmpfs", "/tmp"]
        masked = ["/tmp"]
        for d in self.hidden():
            d = os.path.realpath(d) if d else ""
            if d and os.path.isdir(d) and d != wt and not any(d == m or d.startswith(m + "/") for m in masked):
                a += ["--tmpfs", d]
                masked.append(d)
        for d in _ro_dirs():
            if any(d.startswith(m + "/") for m in masked):
                a += ["--ro-bind", d, d]
        a += ["--ro-bind" if tree_ro else "--bind", wt, wt]
        for d in ([rw] if isinstance(rw, str) else list(rw or ())):   # runner-owned dirs (report, scratch)
            a += ["--bind", d, d]
        for d in ro:   # runner-owned, read-only inside (the supervisor's node runner)
            a += ["--ro-bind", d, d]
        for d in [*masked, "/dev"]:
            a += ["--remount-ro", d]
        return [*a, "--chdir", wt, "--", *limits, *cmd]

    def run(self, cmd: list, wt: str, timeout: int, lock: list = (), rw=None, *, tree_ro: bool = False,
            ro=(), scratch: str | None = None, stdin: str | None = None, pass_fds=()) -> tuple[int | None, str]:
        """(rc, stdout), or (None, 'timeout' | 'worktree_quota'). On timeout the whole process group
        is killed; a worktree that GREW by more than `sandbox_disk_mb` fails the step. With `scratch`
        (the supervisor's per-node runs) HOME / TMPDIR are that runner-owned dir, it is what the growth
        cap measures, and `tree_ro` makes the tree itself read-only inside."""
        if self.backend == "none" and self.cfg.dispatch != "dry":
            raise Refused("sandbox_unavailable", self.why)
        own = scratch is None
        tmp = os.path.join(os.path.realpath(wt), TMP_NAME) if own else scratch
        measured = wt if own else scratch
        rws = [rw] if isinstance(rw, str) else list(rw or ())
        before = tree_bytes(measured)
        os.makedirs(tmp, exist_ok=True)
        try:
            argv = [*lock, *(self.argv(cmd, wt, rws + ([] if own else [scratch]), tree_ro=tree_ro, ro=ro)
                             if self.backend == "bwrap" else cmd)]
            if argv and argv[0] == "flock":
                argv[0] = binaries.which("flock")
            p = subprocess.Popen(argv, cwd=wt, stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=self.env(tmp),
                                 start_new_session=True, pass_fds=tuple(pass_fds))
            try:
                out, _ = p.communicate(input=stdin, timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.communicate()
                return None, "timeout"
            if tree_bytes(measured) - before > self.cfg.sandbox_disk_mb << 20:
                return None, "worktree_quota"
            return p.returncode, out
        finally:
            if own:
                shutil.rmtree(tmp, ignore_errors=True)

    def run_report(self, cmd: list, wt: str, timeout: int, lock: list = ()) -> tuple[int | None, str, str | None]:
        """Run a pytest `cmd` with its JUnit report in a FRESH runner-owned dir OUTSIDE the worktree
        (a file the engine planted in the worktree is never read). The XML is returned only when the
        report exists, is newer than a marker written just before the run, and parses; else None,
        which every caller treats as FAILED (exit 0 with no fresh report is a failure)."""
        d = tempfile.mkdtemp(prefix="selfheal-report-")
        junit, marker = os.path.join(d, "report.xml"), os.path.join(d, ".start")
        try:
            if os.path.lexists(junit):
                os.unlink(junit)
            Path(marker).write_text("")
            rc, out = self.run([*cmd, f"--junitxml={junit}"], wt, timeout, lock, rw=d)
            if rc is None or not os.path.isfile(junit) or os.path.islink(junit):
                return rc, out, None
            if os.stat(junit).st_mtime_ns < os.stat(marker).st_mtime_ns:
                return rc, out, None   # stale
            xml = Path(junit).read_text(errors="replace")
            try:
                ET.fromstring(xml)
            except ET.ParseError:
                return rc, out, None
            return rc, out, xml
        finally:
            shutil.rmtree(d, ignore_errors=True)


TMP_NAME = ".selfheal-tmp"


def tree_bytes(root: str) -> int:
    """Apparent size of every file under `root` (symlinks not followed)."""
    total = 0
    for d, _dirs, files in os.walk(root):
        for f in files:
            try:
                total += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return total
