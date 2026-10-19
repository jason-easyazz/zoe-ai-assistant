"""`zoe-vault-browser`: the login browser, run as its own uid (credential-vault.md 5.1 and 6.1).

It listens on a Unix socket (mode 0660, group shared only by the broker's two users). Each accepted connection gets a
FRESH headless Chromium with a throwaway profile, driven over `--remote-debugging-pipe`, and this process bridges the
connection 1:1 onto that pipe. There is no TCP DevTools port, no second client, and the browser is killed and its
profile deleted the moment the connection closes. One browser at a time (the RAM rule, 6.1): a second connection
waits in the accept queue until the first is done.
"""

from __future__ import annotations

import fcntl
import os
import selectors
import shutil
import signal
import socket
import tempfile
import time

from .peer import peer_uid

CHROMIUM_FLAGS = [
    "--headless=new",
    "--remote-debugging-pipe",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--site-per-process",
    "--disable-extensions",
    "--disable-sync",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-features=Translate,OptimizationHints,MediaRouter,AutofillServerCommunication,PasswordManagerOnboarding",
    "--password-store=basic",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-pings",
]


class Launcher:
    def __init__(self, *, socket_path: str, chromium: str, profile_root: str, allowed_uids: set[int],
                 extra_flags: list[str] | None = None, log=None) -> None:
        self.socket_path = socket_path
        self.chromium = chromium
        self.profile_root = profile_root
        self.allowed_uids = set(allowed_uids)
        self.extra_flags = list(extra_flags or [])
        self.log = log or (lambda msg: None)
        self.sessions = 0

    def bind(self) -> socket.socket:
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old = os.umask(0o117)
        try:
            srv.bind(self.socket_path)
        finally:
            os.umask(old)
        os.chmod(self.socket_path, 0o660)
        srv.listen(4)
        return srv

    def serve_forever(self, *, group_gid: int | None = None) -> None:
        srv = self.bind()
        if group_gid is not None:
            os.chown(self.socket_path, -1, group_gid)
        self.log("listening")
        while True:
            conn, _ = srv.accept()
            try:
                self.handle(conn)
            except Exception as exc:  # one bad session must not take the launcher down
                self.log(f"session error: {type(exc).__name__}")
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def peer_allowed(self, conn: socket.socket) -> bool:
        return peer_uid(conn) in self.allowed_uids

    def handle(self, conn: socket.socket) -> None:
        if not self.peer_allowed(conn):
            self.log("refused a peer")
            return
        self.sessions += 1
        profile = tempfile.mkdtemp(prefix="p-", dir=self.profile_root)
        to_chrome_r, to_chrome_w = os.pipe()
        from_chrome_r, from_chrome_w = os.pipe()
        # Chromium reads DevTools commands from fd 3 and writes to fd 4. Lift both child ends above 10 first so the
        # two dup2 actions cannot clobber each other; every other fd here is close-on-exec.
        a = fcntl.fcntl(to_chrome_r, fcntl.F_DUPFD_CLOEXEC, 10)
        b = fcntl.fcntl(from_chrome_w, fcntl.F_DUPFD_CLOEXEC, 10)
        argv = [self.chromium, *CHROMIUM_FLAGS, *self.extra_flags, f"--user-data-dir={profile}", "about:blank"]
        actions = [
            (os.POSIX_SPAWN_OPEN, 0, "/dev/null", os.O_RDONLY, 0),
            (os.POSIX_SPAWN_OPEN, 1, "/dev/null", os.O_WRONLY, 0),
            (os.POSIX_SPAWN_OPEN, 2, "/dev/null", os.O_WRONLY, 0),
            (os.POSIX_SPAWN_DUP2, a, 3),
            (os.POSIX_SPAWN_DUP2, b, 4),
        ]
        try:
            # TMPDIR inside the profile: with --disable-dev-shm-usage Chromium keeps its shared memory there, so the
            # profile wipe below takes it too (a killed browser otherwise leaves it behind on a tmpfs)
            tmp = os.path.join(profile, "tmp")
            os.mkdir(tmp, 0o700)
            pid = os.posix_spawn(self.chromium, argv, {**_child_env(), "TMPDIR": tmp}, file_actions=actions,
                                 setsid=True)
        finally:
            for fd in (a, b, to_chrome_r, from_chrome_w):
                os.close(fd)
        started = time.monotonic()
        try:
            self._bridge(conn, to_chrome_w, from_chrome_r, pid)
        finally:
            for fd in (to_chrome_w, from_chrome_r):
                try:
                    os.close(fd)
                except OSError:
                    pass
            _kill_group(pid)
            _kill_strays()
            shutil.rmtree(profile, ignore_errors=True)
            self.log(f"session done in {time.monotonic() - started:.1f}s")

    @staticmethod
    def _bridge(conn: socket.socket, to_chrome: int, from_chrome: int, pid: int) -> None:
        sel = selectors.DefaultSelector()
        sel.register(conn, selectors.EVENT_READ, "client")
        sel.register(from_chrome, selectors.EVENT_READ, "chrome")
        while _alive(pid):
            for key, _ in sel.select(timeout=1.0):
                if key.data == "client":
                    data = conn.recv(1 << 16)
                    if not data:
                        return
                    _write_all(to_chrome, data)
                else:
                    data = os.read(from_chrome, 1 << 16)
                    if not data:
                        return
                    conn.sendall(data)


def _kill_strays() -> None:
    """This uid runs nothing but the launcher and its browser, so between sessions every other process it owns is a
    leftover (a renderer or zygote that left the process group) and is killed."""
    me, uid = os.getpid(), os.getuid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            if os.stat(f"/proc/{entry}").st_uid == uid:
                os.kill(int(entry), signal.SIGKILL)
        except OSError:
            pass


def _child_env() -> dict:
    keep = ("PATH", "HOME", "LANG", "TZ", "XDG_RUNTIME_DIR")
    return {k: os.environ[k] for k in keep if k in os.environ}


def _alive(pid: int) -> bool:
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return False
    return done == 0


def _kill_group(pid: int) -> None:
    for sig, wait_s in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 5.0)):
        if not _alive(pid):
            break
        try:
            os.killpg(pid, sig)
        except OSError:
            pass
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline and _alive(pid):
            time.sleep(0.05)
    # the renderer and GPU children share the session; make sure none of them outlives the browser
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]
