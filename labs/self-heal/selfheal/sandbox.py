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
import os
import shutil
import signal
import site
import subprocess
import sys
import tempfile
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

    def env(self, priv_tmp: str) -> dict:
        env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
        env.update(PATH=binaries.TRUSTED_PATH, HOME="/tmp", TMPDIR="/tmp", PYTHONDONTWRITEBYTECODE="1")
        if self.backend != "bwrap":
            env.update(HOME=priv_tmp, TMPDIR=priv_tmp)
        if site.ENABLE_USER_SITE and os.path.isdir(site.getusersitepackages()):
            env["PYTHONUSERBASE"] = site.getuserbase()   # the user-site packages are re-bound read-only
        return env

    def argv(self, cmd: list, wt: str, priv_tmp: str) -> list:
        c, wt = self.cfg, os.path.realpath(wt)
        # prlimit runs INSIDE the sandbox, so by its real path (a symlink under /tmp would be hidden)
        limits = [os.path.realpath(binaries.which("prlimit")), f"--as={c.sandbox_mem_mb << 20}",
                  f"--nproc={c.sandbox_procs}",
                  f"--fsize={FILE_MB << 20}", "--core=0", "--"]
        a = [binaries.which("bwrap"), "--unshare-all", "--die-with-parent", "--new-session",
             "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
        masked = ["/tmp"]   # covered by the private /tmp bound below
        for d in self.hidden():
            d = os.path.realpath(d) if d else ""
            if d and os.path.isdir(d) and d != wt and not any(d == m or d.startswith(m + "/") for m in masked):
                a += ["--tmpfs", d]
                masked.append(d)
        a += ["--bind", priv_tmp, "/tmp"]
        for d in _ro_dirs():
            if any(d.startswith(m + "/") for m in masked):
                a += ["--ro-bind", d, d]
        a += ["--bind", wt, wt]
        for d in [*masked[1:], "/dev"]:
            a += ["--remount-ro", d]
        return [*a, "--chdir", wt, "--", *limits, *cmd]

    def run(self, cmd: list, wt: str, timeout: int, lock: list = ()) -> tuple[int | None, str]:
        """(rc, stdout) or (None, '') on timeout. The whole process group is killed on timeout."""
        if self.backend == "none" and self.cfg.dispatch != "dry":
            raise Refused("sandbox_unavailable", self.why)
        priv = tempfile.mkdtemp(prefix="selfheal-sbx-")
        try:
            argv = [*lock, *(self.argv(cmd, wt, priv) if self.backend == "bwrap" else cmd)]
            if argv and argv[0] == "flock":
                argv[0] = binaries.which("flock")
            p = subprocess.Popen(argv, cwd=wt, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, env=self.env(priv), start_new_session=True)
            try:
                out, _ = p.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.communicate()
                return None, ""
            return p.returncode, out
        finally:
            shutil.rmtree(priv, ignore_errors=True)
