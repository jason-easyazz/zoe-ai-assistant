"""A test run must not write under the real home directory or into the checkout - the guard behind the conftest pins.

Why: the service keeps state under ``~/.zoe`` and beside its own modules, and a test that reaches a real handler writes
there too. Each one was found by accident, after the damage (225 test drawers in the household palace, six test panel
ids in the live warm-speaker memo, five entries in the live forget-epoch file) and fixed by pinning ONE more path in a
conftest. This turns the class into a failing test: the three lanes' conftests install it, and any test that writes
under the home directory outside the allowlist below fails, naming the path.

How: ``sys.addaudithook`` sees every ``open`` for writing, ``mkdir``, ``rename``, ``remove``, ``rmdir``, ``symlink``,
``link``, ``truncate`` and ``sqlite3.connect`` made by THIS process, on any thread. It records the ones that land under
the real home directory (read from the password database and ``$HOME`` once, when the guard is installed, so a test that
points ``HOME`` at a temporary directory is not affected) or under the checkout the lane passes to ``install``, and are
not allowlisted; an autouse fixture in the lane's conftest (``fixture_body``) fails the test that was running. Deterministic (no clock, no sampling) and cheap (one tuple lookup per audited event; measured ~4% on the
zoe-data lane with a far chattier recording hook).

Limits, stated so nobody trusts it for more than it does: it does not see a write made by a CHILD process (a script a
test runs), and an audit hook cannot be removed, so it stays for the life of the pytest process (it only records).

Pure stdlib. Loaded by path from each lane's conftest (``install()`` is idempotent across those loads).
"""
from __future__ import annotations

import os
import pwd
import sys
import tempfile
import threading

_STATE_ATTR = "_zoe_home_write_guard_state"          # on ``sys``: one state however many times this file is loaded
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
# audit event -> ((index of a path the call WRITES, index of its dir_fd or None), ...). Shapes measured on CPython 3.10-3.13:
# os.mkdir(path, mode, dir_fd) / os.remove(path, dir_fd) / os.rename(src, dst, src_dir_fd, dst_dir_fd) /
# os.symlink(src, dst, dir_fd) / os.link(src, dst, src_dir_fd, dst_dir_fd); an absent dir_fd is -1.
_WRITES = {
    "os.mkdir": ((0, 2),), "os.remove": ((0, 1),), "os.rmdir": ((0, 1),), "os.truncate": ((0, None),),
    "os.rename": ((0, 2), (1, 3)), "os.symlink": ((1, 2),), "os.link": ((1, 3),), "sqlite3.connect": ((0, None),),
}

# Path PARTS that are never a leak wherever they sit: interpreter and pytest caches.
ALLOWED_PARTS = ("__pycache__", ".pytest_cache")
# Under the home directory (paths relative to it), each with its reason. Keep these short: a new entry is a decision.
ALLOWED_TREES = (
    ".cache",            # tool caches (pip, uv, fontconfig, the zoe-auth test venv): not household state ...
)
# ... except Zoe's own files there, which are instruments' records (the voice probe's self-test trend), not caches.
REFUSED_UNDER_HOME = (".cache/zoe",)
# PENDING PR 2496 (it gives forget_session's epoch file a per-test store). Delete these entries when it lands.
ALLOWED_EXACT = (".zoe", ".zoe/forget_session_epochs.json")
ALLOWED_PREFIXES = (".zoe/.forget_session.",)         # that module's own atomic-write temp files


def _real_homes() -> tuple[str, ...]:
    homes = []
    for cand in (os.environ.get("HOME"), _passwd_home()):
        if cand:
            real = os.path.realpath(cand)
            if real not in homes and real != "/":
                homes.append(real)
    return tuple(homes)


def _passwd_home() -> str | None:
    try:
        return pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:          # a uid with no passwd entry (some containers)
        return None


def _temp_roots() -> tuple[str, ...]:
    """Places that are throwaway by definition even when they sit under the home directory (a CI runner's temp does)."""
    roots = []
    for cand in (tempfile.gettempdir(), os.environ.get("RUNNER_TEMP"), os.environ.get("PYTEST_DEBUG_TEMPROOT")):
        if cand:
            real = os.path.realpath(cand)
            if real not in roots:
                roots.append(real)
    ledger = os.environ.get("PYTEST_SHARD_LEDGER")       # tools/ci/pytest_shard_ledger.py writes its ledger where it is told
    if ledger:
        roots.append(os.path.dirname(os.path.realpath(ledger)))
    return tuple(roots)


class _State:
    def __init__(self) -> None:
        self.homes = _real_homes()
        self.checkouts: list[str] = []                  # repo roots the lanes registered (``install(checkout=...)``)
        self.temp_roots = _temp_roots()
        self.extra_allowed: list[str] = []              # absolute prefixes a lane adds (see ``allow``)
        self.violations: list[tuple[str, str, str]] = []   # (event, path, thread name)
        self.checked = 0
        self.lock = threading.Lock()
        self.enabled = True                             # the mechanism tests switch recording off for a negative control


def _state() -> _State | None:
    return getattr(sys, _STATE_ATTR, None)


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def classify(path: str, state: _State) -> bool:
    """True when ``path`` (absolute, normalised) is a write the guard refuses."""
    home = next((h for h in state.homes if _under(path, h)), None)
    checkout = next((c for c in state.checkouts if _under(path, c)), None)
    if home is None and checkout is None:
        return False
    if path == home:                                     # touching the directory entry itself (mkdir -p ~) is not a leak
        return False
    # A temp root exempts only what is INSIDE the guarded root it sits in (a CI runner's temp is under its home). A guarded
    # root that itself sits under the system temp dir (a scratch checkout in /tmp) is not exempted by that.
    for root in (home, checkout):
        if root and any(temp != root and _under(temp, root) and _under(path, temp) for temp in state.temp_roots):
            return False
    if any(path.startswith(prefix) for prefix in state.extra_allowed):
        return False
    if any(p in ALLOWED_PARTS for p in path.split(os.sep)) or path.endswith(".pyc"):
        return False
    if checkout is not None:                             # the checkout is not a place for a test's files, wherever it sits
        return True
    rel = os.path.relpath(path, home)
    if any(rel == refused or rel.startswith(refused + "/") for refused in REFUSED_UNDER_HOME):
        return True
    if rel in ALLOWED_EXACT or rel.startswith(ALLOWED_PREFIXES):
        return False
    return not any(rel == tree or rel.startswith(tree + "/") for tree in ALLOWED_TREES)


def _resolve(path, dir_fd=None) -> str | None:
    try:
        if isinstance(path, int):
            return None                                  # an already-open descriptor: its open() was seen
        path = os.fsdecode(path)
        if not path or path == ":memory:" or path.startswith("file:"):
            return None                                  # sqlite in-memory / URI databases
        if not os.path.isabs(path) and isinstance(dir_fd, int):
            base = os.readlink(f"/proc/self/fd/{dir_fd}")      # shutil.rmtree & co. work relative to a directory fd
            path = os.path.join(base, path)
        return os.path.abspath(path)
    except (OSError, TypeError, ValueError):
        return None


def _record(state: _State, event: str, path, dir_fd=None) -> None:
    resolved = _resolve(path, dir_fd)
    if resolved is None or not classify(resolved, state):
        return
    with state.lock:
        state.violations.append((event, resolved, threading.current_thread().name))


def _hook(event: str, args) -> None:
    if event != "open" and event not in _WRITES:
        return
    state = _state()
    if state is None or not state.enabled:
        return
    try:
        if event == "open":
            path, mode, flags = args
            writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and bool(flags & _WRITE_FLAGS))
            if writes:
                _record(state, "open for writing", path)
            return
        for path_i, fd_i in _WRITES[event]:
            if path_i < len(args):
                fd = args[fd_i] if fd_i is not None and fd_i < len(args) else None
                _record(state, event, args[path_i], fd if isinstance(fd, int) and fd >= 0 else None)
    except Exception:                                    # noqa: BLE001 - an audit hook must never break the audited call
        return


def install(checkout: str | None = None) -> bool:
    """Start recording (idempotent); ``checkout`` adds a repo root to guard. Returns whether the guard is on."""
    state = _state()
    if state is None:
        state = _State()
        setattr(sys, _STATE_ATTR, state)
        if state.enabled:
            sys.addaudithook(_hook)
    if checkout:
        real = os.path.realpath(checkout)
        if real not in state.checkouts:
            state.checkouts.append(real)
    return state.enabled


def allow(prefix: str) -> None:
    """Allow writes to paths that START WITH this absolute prefix, for the rest of the process (a lane's conftest decision)."""
    state = _state()
    if state is not None:
        state.extra_allowed.append(os.path.abspath(prefix))


def take() -> list[tuple[str, str, str]]:
    """The violations recorded since the last call (each is reported once)."""
    state = _state()
    if state is None:
        return []
    with state.lock:
        new = state.violations[state.checked:]
        state.checked = len(state.violations)
    return new


def describe(violations, when: str) -> str:
    seen, lines = set(), []
    for event, path, thread in violations:
        if (event, path) not in seen:
            seen.add((event, path))
            lines.append(f"  - {event}: {path}" + ("" if thread == "MainThread" else f"   [thread {thread}]"))
    return (
        f"this test run wrote under the real home directory or the checkout {when}:\n" + "\n".join(lines) + "\n"
        "A test must keep its files under tmp_path. If product code chose the path, pin it for the suite: an env knob or a "
        "monkeypatched module path in the lane's conftest (services/zoe-data/tests/conftest.py is the pattern), never a "
        "new entry in tools/ci/home_write_guard.py ALLOWED_TREES unless the path is a cache."
    )


def fixture_body(enforce: bool = True):
    """The body of each lane's autouse fixture (``yield from`` it). A write made before the test started (at import or
    collection, or by a thread an earlier test left running) fails the first enforced test after it, and says so."""
    import pytest

    before = take()
    if before and enforce:
        pytest.fail(describe(before, "BEFORE this test started (at import/collection, or from a thread an earlier test "
                                     "left running)"), pytrace=False)
    yield
    during = take()
    if during and enforce:
        pytest.fail(describe(during, "during this test"), pytrace=False)
