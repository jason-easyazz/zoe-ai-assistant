"""The 4B brain's cold-start helper (``scripts/maintenance/brain_cold_start.sh``) and the drop-in that wires it in.

Born of 2026-10-10: after a brain stop the ``llama-server`` unit failed to start for 14.5 min with ``NvMapMemAllocInternalTagged error 12`` at ~6 GB MemAvailable (Tegra NvMap does not
reclaim page cache; the GGUF load fills it faster than the big weight buffer lands). What recovered it was dropping the cache every ~0.3 s WHILE the server loaded.

Nothing here touches the real machine: ``sudo`` is a fake on PATH that only records its arguments (so ``/proc/sys/vm/drop_caches`` is never written) and the brain's ``/health`` is a real
HTTP server on a loopback port that the test flips from 503 to 200. The properties:

    the helper returns at once, exits 0 and leaves a detached loop        the loop drops repeatedly while /health is not ok
    the loop stops when /health answers                                   the loop stops at the hard bound even if /health never does
    no passwordless sudo -> one log line, no loop, exit 0                 a broken sudo mid-loop ends the loop, not the unit
    every failure shape exits 0 (the unit must never fail on this step)   a second start supersedes the first loop (only one at a time)
    a caller that CAPTURES our output is not held until the loop ends     the drop-in wires the helper without touching the unit's ExecStart
"""
from __future__ import annotations

import http.server
import os
import shutil
import signal
import socket
import stat
import sys
import subprocess
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts" / "maintenance" / "brain_cold_start.sh"
UNIT_DIR = REPO / "scripts" / "setup" / "systemd"
TEMPLATE = UNIT_DIR / "llama-server.service"
DROP_IN = UNIT_DIR / "llama-server.service.d" / "50-cold-start.conf"

needs_tools = pytest.mark.skipif(not all(shutil.which(t) for t in ("bash", "curl", "timeout", "awk", "setsid")), reason="needs bash, curl, timeout, awk, setsid")

FAKE_SUDO = """#!/bin/bash
echo "$*" >> "$FAKE_SUDO_LOG"
if [ "${FAKE_SUDO_OK:-1}" != "1" ]; then exit 1; fi
case "$*" in
  "-n true") exit 0 ;;
  *drop_caches*) [ "${FAKE_SUDO_DROP_FAILS:-0}" = "1" ] && exit 1 ;;
esac
exit 0
"""


# A stand-in for `systemd-run --user --no-block ...`: records its arguments and runs the command as a separate, detached process (as a transient unit would, in its
# own cgroup, with only the --setenv environment added). The REAL systemd-run must never run from a test: it would start a unit on the live user manager.
FAKE_SYSTEMD_RUN = """#!/bin/bash
echo "$*" >> "$FAKE_SYSTEMD_RUN_LOG"
[ "${FAKE_SYSTEMD_RUN_FAIL:-0}" = "1" ] && exit 1
envs=()
while [ $# -gt 0 ]; do
  case "$1" in
    --setenv=*) envs+=("${1#--setenv=}"); shift ;;
    -p) shift 2 ;;
    -*) shift ;;
    *) break ;;
  esac
done
setsid env "${envs[@]}" "$@" </dev/null >/dev/null 2>&1 &
exit 0
"""


class Health:
    """A loopback /health: 503 (llama-server's answer while the model loads) until ``ok`` is set, then 200."""

    def __init__(self):
        outer = self
        self.ok = False

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                code = 200 if outer.ok else 503
                body = b'{"status":"ok"}' if outer.ok else b'{"error":{"message":"Loading model"}}'
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/health"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Rig:
    def __init__(self, tmp: Path, health: Health):
        self.tmp, self.health = tmp, health
        self.bin = tmp / "bin"
        self.bin.mkdir()
        sudo = self.bin / "sudo"
        sudo.write_text(FAKE_SUDO)
        sudo.chmod(sudo.stat().st_mode | stat.S_IXUSR)
        srun = self.bin / "systemd-run"
        srun.write_text(FAKE_SYSTEMD_RUN)
        srun.chmod(srun.stat().st_mode | stat.S_IXUSR)
        self.srun_log = tmp / "systemd-run.log"
        self.srun_log.write_text("")
        self.sudo_log = tmp / "sudo.log"
        self.sudo_log.write_text("")
        self.state = tmp / "state"
        self.state.mkdir()
        self.env = {**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_SUDO_LOG": str(self.sudo_log), "FAKE_SYSTEMD_RUN_LOG": str(self.srun_log), "HOME": str(tmp),
                    "BRAIN_COLD_START_STATE_DIR": str(self.state), "BRAIN_COLD_START_HEALTH_URL": health.url,
                    "BRAIN_COLD_START_MAX_S": "30", "BRAIN_COLD_START_INTERVAL_S": "0.05"}
        for k in ("BRAIN_COLD_START_DISABLE", "XDG_RUNTIME_DIR"):
            self.env.pop(k, None)

    def run(self, *args, timeout=15, **env) -> subprocess.CompletedProcess:
        e = {**self.env, **{k: str(v) for k, v in env.items()}}
        return subprocess.run(["bash", str(HELPER), *args], env=e, capture_output=True, text=True, timeout=timeout)

    def drops(self) -> int:
        return sum(1 for ln in self.sudo_log.read_text().splitlines() if "drop_caches" in ln)

    def loop_log(self) -> str:
        p = self.state / "loop.log"
        return p.read_text() if p.exists() else ""

    def pid(self) -> int | None:
        p = self.state / "loop.pid"
        try:
            return int(p.read_text().split()[0])
        except (OSError, ValueError, IndexError):
            return None

    def loops(self) -> list[int]:
        """Every live loop process that belongs to THIS rig (its state dir is in the process environment), whoever started it."""
        out = []
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                cmd = (d / "cmdline").read_bytes().split(b"\0")
                env = (d / "environ").read_bytes()
            except OSError:
                continue
            if b"--loop" in cmd and any(c.endswith(b"brain_cold_start.sh") for c in cmd) and f"BRAIN_COLD_START_STATE_DIR={self.state}".encode() in env and alive(int(d.name)):
                out.append(int(d.name))
        return out

    def kill_loop(self):
        for pid in {*self.loops(), self.pid() or 0}:
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                        # a zombie (not yet reaped) is not a running loop
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def wait_for(pred, timeout=8.0, step=0.03) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


def loopback_skip_reason(ci: str | None, bind=None) -> str | None:
    """tests/AGENTS.md: a test that fails only because the sandbox has no loopback skips WITH the reason, probed at test time, and NEVER under ``CI`` (GitHub sets it on every
    runner, the self-hosted Jetson included), where a dead loopback must fail closed. Same shape as ``loopback_required`` in test_serena_bridge_proxy.py. Returns the skip reason,
    or None to run (always None under CI, without probing)."""
    if ci:
        return None
    try:
        if bind is not None:
            bind()
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        return f"loopback unavailable ({exc}) - no-network sandbox; bring `lo` up (tests/AGENTS.md recipe) to run these real-socket tests"
    return None


@pytest.fixture
def rig(tmp_path):
    reason = loopback_skip_reason(os.environ.get("CI"))
    if reason:
        pytest.skip(reason)
    h = Health()
    r = Rig(tmp_path, h)
    yield r
    r.kill_loop()
    h.close()


# ── the loop ─────────────────────────────────────────────────────────────────

@needs_tools
def test_the_helper_returns_at_once_after_one_synchronous_drop_and_leaves_a_detached_loop(rig):
    t0 = time.monotonic()
    p = rig.run()
    assert p.returncode == 0 and time.monotonic() - t0 < 5
    assert rig.drops() >= 1                                    # the drop BEFORE the server starts happened before we returned
    assert "page cache dropped before the start" in p.stdout and "transient unit" in p.stdout
    assert wait_for(lambda: alive(rig.pid()))                  # the loop outlives the Pre step that spawned it
    assert wait_for(lambda: rig.drops() >= 4)                  # ... and keeps dropping


@needs_tools
def test_the_loop_stops_when_health_answers_and_stops_dropping(rig):
    rig.run()
    assert wait_for(lambda: rig.drops() >= 3)
    pid = rig.pid()
    rig.health.ok = True
    assert wait_for(lambda: "stopped: brain healthy" in rig.loop_log())
    assert wait_for(lambda: not alive(pid))
    settled = rig.drops()
    time.sleep(0.5)
    assert rig.drops() == settled                              # no more drops once the model is up
    assert not (rig.state / "loop.pid").exists()               # it cleans its own pidfile


@needs_tools
def test_negative_control_without_health_the_loop_keeps_dropping(rig):
    """The counting harness above must be able to see a loop that is NOT stopping: here /health never answers and the count keeps climbing, loop alive."""
    rig.run()
    assert wait_for(lambda: rig.drops() >= 3)
    before = rig.drops()
    assert wait_for(lambda: rig.drops() >= before + 5)
    assert alive(rig.pid()) and "stopped" not in rig.loop_log()


@needs_tools
def test_the_loop_stops_at_the_hard_bound_when_health_never_answers(rig):
    p = rig.run(BRAIN_COLD_START_MAX_S="1")
    assert p.returncode == 0
    assert wait_for(lambda: rig.pid() is not None)
    pid = rig.pid()
    assert wait_for(lambda: "bound reached" in rig.loop_log(), timeout=10)
    assert wait_for(lambda: not alive(pid))
    n = rig.drops()
    assert 3 <= n < 200                                        # ~1 s at 50 ms; bounded, not runaway
    time.sleep(0.4)
    assert rig.drops() == n


@needs_tools
def test_health_already_ok_means_nothing_to_protect(rig):
    rig.health.ok = True
    p = rig.run()
    assert p.returncode == 0 and "skipped" in p.stdout and rig.drops() == 0 and rig.pid() is None


@needs_tools
def test_a_second_start_supersedes_the_first_loop(rig):
    rig.run()
    assert wait_for(lambda: alive(rig.pid()))
    first = rig.pid()
    p = rig.run()
    assert p.returncode == 0 and "superseded" in p.stdout
    assert wait_for(lambda: not alive(first))
    assert wait_for(lambda: rig.pid() not in (None, first) and alive(rig.pid()))
    assert "stopped: superseded" in rig.loop_log()


@needs_tools
def test_a_caller_that_captures_output_is_not_held_until_the_loop_ends(rig):
    """night_window.py runs the helper through subprocess with captured pipes. A loop that inherited those pipes would hold the caller for the whole 30 s bound; the loop's own
    output goes to its log file instead. (``rig.run`` captures stdout and stderr, with a 15 s timeout, while the 30 s loop is still running.)"""
    t0 = time.monotonic()
    p = rig.run(timeout=10)
    assert p.returncode == 0 and time.monotonic() - t0 < 5
    assert alive(rig.pid()) or wait_for(lambda: alive(rig.pid()))


# ── the loop lives OUTSIDE the Pre step's cgroup (review: systemd kills everything an ExecStartPre= forked) ──

@needs_tools
def test_the_loop_is_started_as_its_own_transient_unit_not_as_a_child_of_the_pre_step(rig):
    """systemd.service(5): "All processes forked off by processes invoked via ExecStartPre= will be killed before the next service process is run"; setsid does not leave the
    cgroup. So the loop must be handed to systemd-run (a transient unit, its own cgroup) with a systemd-enforced RuntimeMaxSec beside the script's bound."""
    p = rig.run()
    assert p.returncode == 0 and "transient unit" in p.stdout
    call = rig.srun_log.read_text().strip().splitlines()
    assert len(call) == 1
    line = call[0]
    for want in ("--user", "--no-block", "--collect", "--unit=brain-cold-start-loop-", "RuntimeMaxSec=61",           # the rig's 30 s bound + 31 s slack f"--setenv=BRAIN_COLD_START_STATE_DIR={rig.state}",
                 f"--setenv=BRAIN_COLD_START_HEALTH_URL={rig.health.url}", f"/bin/bash {HELPER} --loop"):
        assert want in line, (want, line)
    assert wait_for(lambda: alive(rig.pid()))                  # and the loop the fake unit ran is the one dropping the cache
    assert wait_for(lambda: rig.drops() >= 4)
    assert rig.pid() in rig.loops()


@needs_tools
def test_if_systemd_run_fails_the_loop_falls_back_to_a_setsid_child(rig):
    p = rig.run(FAKE_SYSTEMD_RUN_FAIL="1")
    assert p.returncode == 0 and "falling back" in p.stdout and "setsid" in p.stdout
    assert wait_for(lambda: alive(rig.pid())) and wait_for(lambda: rig.drops() >= 4)


@needs_tools
def test_the_setsid_path_can_be_forced_and_never_calls_systemd_run(rig):
    p = rig.run(BRAIN_COLD_START_NO_SYSTEMD_RUN="1")
    assert p.returncode == 0 and "setsid" in p.stdout and rig.srun_log.read_text() == ""
    assert wait_for(lambda: alive(rig.pid()))


# ── the bound is a real bound (review: a huge interval or a backward clock must not outlive it) ──

@needs_tools
def test_a_huge_interval_cannot_outlive_the_bound(rig):
    t0 = time.monotonic()
    rig.run(BRAIN_COLD_START_MAX_S="1", BRAIN_COLD_START_INTERVAL_S="3600")
    assert wait_for(lambda: rig.pid() is not None)
    pid = rig.pid()
    assert wait_for(lambda: "bound reached" in rig.loop_log(), timeout=8)
    assert wait_for(lambda: not alive(pid))
    assert time.monotonic() - t0 < 8                           # not 3600 s: every wait is capped to the time that remains


@needs_tools
def test_a_calendar_clock_that_runs_backward_cannot_stretch_the_bound(rig):
    """The deadline is measured on /proc/uptime, not `date`: a `date` that goes backward (an NTP step) would make elapsed negative forever."""
    fake_date = rig.bin / "date"
    fake_date.write_text('#!/bin/bash\nn=$(cat "$0.n" 2>/dev/null || echo 9000000000000000000)\necho $((n - 1000000000)) > "$0.n"\necho $n\n')
    fake_date.chmod(fake_date.stat().st_mode | stat.S_IXUSR)
    rig.run(BRAIN_COLD_START_MAX_S="1")
    assert wait_for(lambda: rig.pid() is not None)
    pid = rig.pid()
    assert wait_for(lambda: "bound reached" in rig.loop_log(), timeout=8)
    assert wait_for(lambda: not alive(pid))


# ── overlapping starts (review: supersede + launch must be one locked step) ──

@needs_tools
def test_a_start_that_cannot_get_the_launch_lock_does_nothing_and_exits_zero(rig):
    import fcntl
    lock = rig.state / "launch.lock"
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)                         # another start is mid-launch
        t0 = time.monotonic()
        p = rig.run(BRAIN_COLD_START_LOCK_WAIT_S="0.3")
        assert p.returncode == 0 and "launch lock" in p.stdout and time.monotonic() - t0 < 5
        assert rig.pid() is None and rig.loops() == [] and rig.srun_log.read_text() == ""
    p = rig.run()                                              # lock released: the next start arms a loop normally
    assert p.returncode == 0 and wait_for(lambda: alive(rig.pid()))


@needs_tools
def test_overlapping_starts_leave_exactly_one_loop(rig):
    procs = [subprocess.Popen(["bash", str(HELPER)], env=rig.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(6)]
    assert all(p.wait(timeout=20) == 0 for p in procs)
    assert wait_for(lambda: len(rig.loops()) == 1, timeout=8), rig.loops()
    assert rig.pid() in rig.loops()                            # and the pidfile names the survivor, so the NEXT start can supersede it


# ── degradation: never fails the unit ────────────────────────────────────────

@needs_tools
def test_without_passwordless_sudo_it_logs_once_and_exits_zero_with_no_loop(rig):
    p = rig.run(FAKE_SUDO_OK="0")
    assert p.returncode == 0 and "SKIPPED" in p.stdout and "sudo" in p.stdout
    assert rig.drops() == 0 and rig.pid() is None and rig.loop_log() == ""


@needs_tools
def test_with_no_sudo_binary_at_all_it_still_exits_zero(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    for t in ("bash", "curl", "timeout", "awk", "date", "tr", "head", "grep", "readlink", "mkdir", "logger", "sleep", "rm", "setsid", "dirname"):
        src = shutil.which(t)
        if src:
            (tools / t).symlink_to(src)
    assert not (tools / "sudo").exists()
    env = {"PATH": str(tools), "HOME": str(tmp_path), "BRAIN_COLD_START_STATE_DIR": str(tmp_path / "s")}
    p = subprocess.run([str(tools / "bash"), str(HELPER)], env=env, capture_output=True, text=True, timeout=15)
    assert p.returncode == 0 and "SKIPPED" in p.stdout and not (tmp_path / "s" / "loop.pid").exists()


@needs_tools
def test_a_sudo_that_stops_working_ends_the_loop_not_the_unit(rig):
    p = rig.run(FAKE_SUDO_DROP_FAILS="1")                      # `sudo -n true` works, the drop itself fails every time
    assert p.returncode == 0 and "the first drop_caches failed" in p.stdout
    assert wait_for(lambda: "consecutive drop_caches failures" in rig.loop_log())
    assert wait_for(lambda: not alive(rig.pid()))


@needs_tools
@pytest.mark.parametrize("kw", [
    {"BRAIN_COLD_START_DISABLE": "1"},
    {"FAKE_SUDO_OK": "0"},
    {"BRAIN_COLD_START_HEALTH_URL": "http://127.0.0.1:1/health", "BRAIN_COLD_START_MAX_S": "1"},
    {"BRAIN_COLD_START_STATE_DIR": "/proc/definitely/not/writable"},
    {"BRAIN_COLD_START_MAX_S": "garbage", "BRAIN_COLD_START_INTERVAL_S": "garbage"},
], ids=["disabled", "no-sudo", "dead-health-endpoint", "unwritable-state-dir", "garbage-numbers"])
def test_every_failure_shape_exits_zero(rig, kw):
    assert rig.run(**kw).returncode == 0


@needs_tools
def test_the_internal_loop_mode_also_exits_zero_on_every_path(rig):
    assert rig.run("--loop", BRAIN_COLD_START_MAX_S="0.3").returncode == 0
    rig.health.ok = True
    assert rig.run("--loop").returncode == 0


@needs_tools
def test_the_loop_only_ever_runs_the_single_drop_caches_command(rig):
    rig.run()
    assert wait_for(lambda: rig.drops() >= 3)
    rig.kill_loop()
    lines = [ln for ln in rig.sudo_log.read_text().splitlines() if ln.strip()]
    assert set(lines) <= {"-n true", "-n sh -c echo 1 > /proc/sys/vm/drop_caches"}


# ── no loopback in the sandbox: skip with a reason locally, fail closed under CI (tests/AGENTS.md) ──

def _denied():
    raise PermissionError(1, "Operation not permitted")


def test_a_sandbox_without_loopback_skips_with_the_reason_but_never_under_ci():
    reason = loopback_skip_reason(None, bind=_denied)
    assert reason and "loopback unavailable" in reason and "Operation not permitted" in reason
    assert loopback_skip_reason("1", bind=_denied) is None            # CI: no probe, no skip -> the fixture goes on and the real failure shows
    assert loopback_skip_reason(None, bind=lambda: None) is None      # a working loopback runs the tests


@pytest.mark.skipif(not shutil.which("unshare"), reason="needs unshare")
def test_in_a_real_no_network_namespace_the_rig_tests_skip_locally_and_fail_under_ci():
    """The end-to-end control: `unshare -rn` WITHOUT `ip link set lo up` (the recipe in tests/AGENTS.md, minus the last step) makes the loopback bind fail for real."""
    probe = subprocess.run(["unshare", "-rn", "--map-root-user", sys.executable, "-c", "import socket; s=socket.socket(); s.bind(('127.0.0.1',0))"], capture_output=True, text=True)
    if probe.returncode == 0:
        pytest.skip("this sandbox cannot create a no-loopback network namespace")
    target = f"{__file__}::test_the_helper_returns_at_once_after_one_synchronous_drop_and_leaves_a_detached_loop"
    base = [k for k in os.environ if k != "CI"]
    env = {k: os.environ[k] for k in base}
    cmd = ["unshare", "-rn", "--map-root-user", sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", target]
    local = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120, cwd=REPO)
    assert "1 skipped" in local.stdout and local.returncode == 0, local.stdout[-800:]
    assert "loopback unavailable" in subprocess.run(cmd + ["-rs"], env=env, capture_output=True, text=True, timeout=120, cwd=REPO).stdout
    ci = subprocess.run(cmd, env={**env, "CI": "true"}, capture_output=True, text=True, timeout=120, cwd=REPO)
    assert ci.returncode != 0 and "1 skipped" not in ci.stdout, ci.stdout[-800:]       # under CI the same dead loopback is a FAILURE


# ── the unit wiring ──────────────────────────────────────────────────────────

def _service_lines(path: Path) -> list[str]:
    out, section = [], None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
        elif section == "Service":
            out.append(line)
    return out


def _effective(lines: list[str], key: str) -> list[str]:
    """systemd list semantics: every assignment appends, an empty one resets."""
    vals: list[str] = []
    for ln in lines:
        k, _, v = ln.partition("=")
        if k.strip() == key:
            vals = [] if not v.strip() else vals + [v.strip()]
    return vals


def test_the_helper_is_executable_and_passes_a_syntax_check():
    assert HELPER.is_file() and os.access(HELPER, os.X_OK)
    if shutil.which("bash"):
        assert subprocess.run(["bash", "-n", str(HELPER)], capture_output=True).returncode == 0


def test_the_drop_in_wires_the_helper_without_touching_the_main_command():
    lines = _service_lines(DROP_IN)
    assert _effective(lines, "ExecStart") == []                                    # the drop-in never touches (or resets) the unit's ExecStart
    assert not any(ln.partition("=")[0].strip() == "ExecStart" for ln in lines)
    pre = _effective(lines, "ExecStartPre")
    assert pre == ["-/bin/bash %h/assistant/scripts/maintenance/brain_cold_start.sh"]    # the `-` = the step can never fail the unit
    assert lines.index("ExecStartPre=") < lines.index("ExecStartPre=" + pre[0])    # reset first: template + drop-in never run it twice
    assert not any(ln.partition("=")[0].strip() in ("Nice", "OOMScoreAdjust") for ln in lines)   # a --user unit cannot raise priority; systemd drops it silently


def test_restart_sec_in_the_drop_in_equals_the_templates_so_the_drift_check_stays_quiet():
    drop, tmpl = _service_lines(DROP_IN), _service_lines(TEMPLATE)
    assert _effective(drop, "RestartSec") == _effective(tmpl, "RestartSec") == ["10"]
    assert _effective(tmpl, "Restart") == ["on-failure"]                          # the retry that the loop is re-armed by


def test_the_drop_in_leaves_the_templates_main_command_and_health_probe_alone():
    tmpl = _service_lines(TEMPLATE)
    merged = tmpl + _service_lines(DROP_IN)
    assert _effective(merged, "ExecStart") == _effective(tmpl, "ExecStart") and len(_effective(merged, "ExecStart")) == 1
    assert _effective(merged, "ExecStartPost") == _effective(tmpl, "ExecStartPost")
    assert len(_effective(merged, "ExecStartPre")) == 1                            # exactly one pre step, whatever the template carries


def test_the_drift_check_reports_no_difference_for_the_template_plus_drop_in(tmp_path):
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("unit_drift_check_cs", REPO / "scripts" / "maintenance" / "unit_drift_check.py")
    udc = importlib.util.module_from_spec(spec)
    sys.modules["unit_drift_check_cs"] = udc
    spec.loader.exec_module(udc)
    live = tmp_path / "live"
    (live / "llama-server.service.d").mkdir(parents=True)
    (live / "llama-server.service").write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    drop = DROP_IN.read_text(encoding="utf-8")
    (live / "llama-server.service.d" / "50-cold-start.conf").write_text(drop, encoding="utf-8")
    findings, _ = udc.check(["llama-server"], UNIT_DIR, live, "/home/x")
    assert [f for f in findings if f.kind in ("missing", "differs")] == []         # an expected install is not drift (the new pre step is a live_only note)
    # negative control: a RestartSec that differs from the template IS reported, so the check can see this drop-in at all
    (live / "llama-server.service.d" / "50-cold-start.conf").write_text(drop.replace("RestartSec=10", "RestartSec=15"), encoding="utf-8")
    findings, _ = udc.check(["llama-server"], UNIT_DIR, live, "/home/x")
    assert any(f.kind == "differs" and "RestartSec" in f.key for f in findings), findings
