"""pytest plugin: prove a test run is OFFLINE-hermetic, and say what it cost.

Three ways in, one guard:

* ``OFFLINE_GUARD=block|record`` with ``-p offline_guard`` (``tools/ci`` on ``PYTHONPATH``): every test is
  guarded. This is how ``tools/ci/offline_tier.py`` measures and verifies.
* ``--offline-tier`` (``python -m pytest -p tools.ci.offline_guard --offline-tier <lane dir>``): collect ONLY the
  files of the manifest ``tools/ci/offline_tier.json`` and guard them. A plain pytest argv, for callers that may
  run nothing else (the self-heal lab's ``offline_lane_cmd``).
* neither: the two lane conftests register this module, and only the manifest's files are guarded. That is the
  pin: in the required ``ci_safe`` lanes a tier test that reaches for the network fails by name.

A ``sys.addaudithook`` hook sees every socket call of THIS process, whatever library makes it:

* ``connect`` / ``sendto`` / ``sendmsg`` to a non-loopback IP address, and name resolution of anything that is
  not a loopback literal or ``localhost``, are VIOLATIONS. In ``block`` mode the call fails the way it does on a
  box with no network (``ENETUNREACH`` / ``EAI_AGAIN``), so a run on a connected dev box behaves like a sold
  unit; the test that made the call then FAILS at teardown even if the code under test swallowed the error.
* every program the process starts (``subprocess.Popen``, ``os.exec*``, ``os.posix_spawn``, ``os.system``) is
  recorded by name: that is the list of external binaries a test needs.

Not covered: sockets opened by a CHILD process (the hook is per interpreter). ``offline_tier.py`` therefore
also runs the tests inside an empty network namespace when the kernel allows it, where a child's call fails too.

On Python 3.12+ it also records which ``services/zoe-data`` source files had a function or class body
EXECUTED (``sys.monitoring``, each code object reported once, then disabled: no measurable overhead). Module
top-level code does not count: importing a module is not exercising it.

``OFFLINE_GUARD_REPORT=<path>`` writes one JSON report at session end. Pure stdlib.
"""
from __future__ import annotations

import errno
import ipaddress
import json
import os
import resource
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

import pytest

ZOE_OFFLINE_GUARD_PLUGIN = True      # how a conftest recognises this plugin under either import name
MODE = os.environ.get("OFFLINE_GUARD", "").strip().lower()
MANIFEST = Path(__file__).resolve().parent / "offline_tier.json"
REPO = Path(__file__).resolve().parents[2]
COVER_ROOT = str(REPO / "services" / "zoe-data") + os.sep
_SEND = {"socket.connect", "socket.sendto", "socket.sendmsg"}
_RESOLVE = {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"}
_SPAWN = {"subprocess.Popen", "os.exec", "os.posix_spawn", "os.system"}


def _as_ip(host):
    """The address a host string IS, without any lookup (``inet_aton`` forms such as ``2130706433`` included), or None."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).split("%", 1)[0].strip("[]")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.inet_aton(host))
        except (OSError, ValueError):
            return None
    return getattr(ip, "ipv4_mapped", None) or ip


def is_loopback(host) -> bool:
    """True for a loopback IP literal, ``localhost``, or no host at all (wildcard / AF_UNIX path)."""
    if host in (None, "", b""):
        return True
    name = (host.decode("ascii", "replace") if isinstance(host, bytes) else str(host)).lower()
    if name == "localhost" or name.endswith(".localhost"):
        return True
    ip = _as_ip(host)
    return ip is not None and (ip.is_loopback or ip.is_unspecified)


class Guard:
    """State of one guarded process. ``current`` is the node id pytest is running (or ``<collection>``)."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.current = "<startup>"
        self.violations: list[dict] = []
        self.spawned: dict[str, set[str]] = {}
        self.pending: list[dict] = []          # violations of the test now running
        self.reported = 0                      # how many of ``pending`` already failed a phase
        self.armed = True
        self.only: frozenset[str] | None = None    # guard only these test files (None = every test)

    def _violate(self, kind: str, target: str) -> None:
        v = {"test": self.current, "kind": kind, "target": target}
        self.violations.append(v)
        self.pending.append(v)

    def hook(self, event: str, args) -> None:
        if not self.armed:
            return
        if event in _SEND:
            sock, addr = args[0], args[-1]
            if getattr(sock, "family", None) not in (socket.AF_INET, socket.AF_INET6):
                return
            if not isinstance(addr, tuple) or not addr or is_loopback(addr[0]):
                return
            udp = getattr(sock, "type", 0) == socket.SOCK_DGRAM
            self._violate("connect-udp" if udp and event == "socket.connect" else "send-udp" if udp else "connect",
                          f"{addr[0]}:{addr[1] if len(addr) > 1 else ''}")
            if self.mode == "block":
                raise OSError(errno.ENETUNREACH, "offline guard: network is unreachable")
        elif event in _RESOLVE:
            host = args[0]
            if is_loopback(host) or _as_ip(host) is not None or host == socket.gethostname():
                return                    # an IP literal needs no lookup: its connect is what gets judged
            self._violate("resolve", str(host))
            if self.mode == "block":
                raise socket.gaierror(socket.EAI_AGAIN, "offline guard: name resolution is unavailable")
        elif event in _SPAWN:
            exe, env = args[0], (args[3] if event == "subprocess.Popen" and len(args) > 3 else None)
            if event == "subprocess.Popen" and not exe and args[1]:
                exe = args[1] if isinstance(args[1], (str, bytes)) else args[1][0]
            self.spawned.setdefault(program_name(exe, env), set()).add(item_file(self.current))


def program_name(exe, env=None) -> str:
    """What a started program IS: a system binary's name, ``repo:<path>`` for a repo script, or ``stub`` for a
    file under the temp dir (a test's own fake)."""
    if isinstance(exe, bytes):
        exe = exe.decode("utf-8", "replace")
    exe = str(exe or "?")
    if os.sep not in exe:
        exe = exe.split()[0] if exe.split() else "?"            # os.system / shell=True command lines
        found = shutil.which(exe, path=env.get("PATH") if isinstance(env, dict) else None)
        if not found:
            return exe
        exe = found
    real = os.path.realpath(exe)
    if real.startswith(str(REPO) + os.sep):
        return "repo:" + os.path.relpath(real, REPO)
    if any(real.startswith(os.path.realpath(t) + os.sep) for t in {tempfile.gettempdir(), "/tmp", "/var/tmp"}):
        return "stub"
    return os.path.basename(exe)


def item_file(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


_guard: Guard | None = None
_executed: set[str] = set()
_t0 = time.monotonic()
_files: dict[str, dict] = {}


def _start_coverage() -> None:
    mon = getattr(sys, "monitoring", None)
    if mon is None:
        return
    tool = mon.COVERAGE_ID
    try:
        mon.use_tool_id(tool, "zoe-offline-guard")
    except ValueError:      # another coverage tool owns the slot: leave it alone
        return

    def on_start(code, _offset, _root=COVER_ROOT, _add=_executed.add, _off=mon.DISABLE):   # bound: runs at exit too
        if code.co_filename.startswith(_root) and code.co_name != "<module>":
            _add(code.co_filename[len(_root):])
        return _off

    mon.register_callback(tool, mon.events.PY_START, on_start)
    mon.set_events(tool, mon.events.PY_START)


def install(mode: str) -> Guard:
    """Arm the guard for this process (idempotent; an audit hook cannot be removed, only disarmed)."""
    global _guard
    if _guard is None:
        _guard = Guard(mode)
        sys.addaudithook(_guard.hook)
        _start_coverage()
    _guard.mode, _guard.armed = mode, True
    return _guard


def tier_files() -> frozenset[str]:
    """Repo-relative test files of the committed tier (empty when there is no manifest)."""
    try:
        lanes = json.loads(MANIFEST.read_text(encoding="utf-8"))["lanes"]
    except (OSError, ValueError, KeyError):
        return frozenset()
    return frozenset(f for files in lanes.values() for f in files)


def _rel(path) -> str:
    try:
        return str(Path(path).resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def pytest_addoption(parser) -> None:
    parser.getgroup("zoe-offline").addoption(
        "--offline-tier", action="store_true",
        help="collect only the files of tools/ci/offline_tier.json and refuse non-loopback network in them")


def pytest_configure(config) -> None:
    select = config.getoption("offline_tier", False)
    if MODE in ("block", "record") or select:
        install(MODE if MODE == "record" else "block")
        # An offline run must not inherit the caller's git config: a signing program or credential helper there
        # reaches for the network (or a hardware key) from every `git commit` a test makes in its temp repo.
        # (The lane conftests pin their own neutral config first; this covers a run outside those two lanes.)
        for key, value in (("GIT_CONFIG_GLOBAL", os.devnull), ("GIT_CONFIG_NOSYSTEM", "1"), ("GIT_TERMINAL_PROMPT", "0")):
            os.environ.setdefault(key, value)
    else:
        only = tier_files()
        if only:
            install("block").only = only
            _guard.armed = False


def pytest_ignore_collect(collection_path, config):
    if config.getoption("offline_tier", False) and collection_path.is_file() \
            and collection_path.name.startswith("test_") and _rel(collection_path) not in tier_files():
        return True
    return None


def pytest_collectstart(collector) -> None:
    if _guard is not None and _guard.only is None:
        _guard.current = f"<collection>::{collector.nodeid}"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if _guard is None:
        return (yield)
    if _guard.only is not None and _rel(item.path) not in _guard.only:
        _guard.pending, _guard.reported = [], 0
        return (yield)                       # not a tier file: this test runs unguarded
    _guard.current, _guard.pending, _guard.reported, _guard.armed = item.nodeid, [], 0, True
    started = time.monotonic()
    try:
        return (yield)
    finally:
        _guard.armed = _guard.only is None
        f = _files.setdefault(item_file(item.nodeid), {"seconds": 0.0, "tests": 0})
        f["seconds"] += time.monotonic() - started
        f["tests"] += 1
        _guard.current = "<between-tests>"


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    rep = yield
    if _guard is not None and _guard.mode == "block" and call.when != "setup" and len(_guard.pending) > _guard.reported and rep.passed:
        _guard.reported = len(_guard.pending)
        seen = sorted({f"{v['kind']} {v['target']}" for v in _guard.pending})
        rep.outcome = "failed"
        rep.longrepr = "offline guard: this test reached for the network: " + ", ".join(seen)
    return rep


def pytest_sessionfinish(session, exitstatus) -> None:
    path = os.environ.get("OFFLINE_GUARD_REPORT")
    if _guard is None or not path:
        return
    _guard.armed = False
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    counts = {k: len(tr.stats.get(k, [])) for k in ("passed", "failed", "error", "skipped", "xfailed")} if tr else {}
    report = {
        "schema": 1,
        "mode": _guard.mode,
        "exitstatus": int(exitstatus),
        "counts": counts,
        "wall_s": round(time.monotonic() - _t0, 3),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "children_peak_rss_mb": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024, 1),
        "files": {k: {"seconds": round(v["seconds"], 3), "tests": v["tests"]} for k, v in sorted(_files.items())},
        "violations": _guard.violations,
        "spawned": {k: sorted(v) for k, v in sorted(_guard.spawned.items())},
        "executed": sorted(_executed),
        "imported": sorted({f[len(COVER_ROOT):] for m in list(sys.modules.values())
                            if (f := getattr(m, "__file__", None) or "").startswith(COVER_ROOT)}),
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
