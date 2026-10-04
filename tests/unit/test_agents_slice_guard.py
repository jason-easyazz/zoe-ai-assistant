"""Pins PR 1 of "agent sessions off the box" (docs/research/agent-sessions-off-box-2026-10-04.md).

The incident class (every RAM incident on this box since July is an agent-tooling incident) was
never "a component had no cap" - each got one - it was "nothing bounds the SUM, or how many
engineering processes run at once". The guard has four parts and each can silently rot into a
decoration, so each is exercised, not grepped:

* the slice carries all THREE memory keys (a throttle band or a swap escape is "not a cap");
* the launcher and the bridge unit take the session lease IN THE EXEC PATH and name the slice -
  a SessionStart hook cannot refuse a session, so the enforcement point is the launcher;
* the sampler's "outside the slice" arithmetic is a whole-path-COMPONENT match, container
  processes are not counted as outside, and nothing but counts leaves the machine;
* negative controls (record, section 5): break the guard and the matching test must go red.

The Serena drop-in and the codebase-memory wrapper are pinned in test_agent_mcp_memory_bounds.py.
"""

from __future__ import annotations

import ast
import fcntl
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
UNIT_DIR = ROOT / "scripts" / "setup" / "systemd"
SLICE_FILE = UNIT_DIR / "zoe-agents.slice"
BRIDGE_UNIT = UNIT_DIR / "zoe-claude-bridge.service"
BRIDGE_EXEC = ROOT / "scripts" / "agents" / "zoe-claude-bridge-exec"
LAUNCHER = ROOT / "scripts" / "agents" / "zoe-agent"
SAMPLER = ROOT / "scripts" / "maintenance" / "zoe_agents_sampler.py"
SAMPLER_SERVICE = UNIT_DIR / "zoe-agents-sampler.service"
SAMPLER_TIMER = UNIT_DIR / "zoe-agents-sampler.timer"
HARNESS_LOCK = "zoe-voice-harness.lock"

needs_flock = pytest.mark.skipif(shutil.which("flock") is None, reason="util-linux flock not installed")

_spec = importlib.util.spec_from_file_location("zoe_agents_sampler", SAMPLER)
sampler = importlib.util.module_from_spec(_spec)
sys.modules["zoe_agents_sampler"] = sampler  # `from __future__ import annotations` needs it registered
_spec.loader.exec_module(sampler)


def _section(text: str, name: str) -> dict[str, str]:
    """Key=value pairs of one [Section] of a unit file, comments stripped."""
    out: dict[str, str] = {}
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"\[(\w+)\]", line)
        if m:
            cur = m.group(1)
            continue
        if cur == name and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _code(text: str) -> str:
    """Executable lines only - comments (which legitimately name the harness lock) removed."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


# ------------------------------------------------------------------------------ the slice


def _slice_problems(text: str) -> list[str]:
    s = _section(text, "Slice")
    bad = []
    for key in ("MemoryHigh", "MemoryMax", "MemorySwapMax", "CPUWeight", "IOWeight"):
        if key not in s:
            bad.append(f"{key} missing")
    if s.get("MemoryHigh") != s.get("MemoryMax"):
        bad.append("MemoryHigh != MemoryMax (throttle band: memory.high never OOM-kills)")
    if s.get("MemorySwapMax") != "0":
        bad.append("MemorySwapMax must be 0 (a cap that can swap just relocates the leak)")
    return bad


def test_slice_carries_all_three_memory_keys_and_the_record_values():
    s = _section(SLICE_FILE.read_text(), "Slice")
    assert _slice_problems(SLICE_FILE.read_text()) == []
    assert s["MemoryMax"] == "3G", "the record's aggregate bound is 3G (3G + 1.5G container = 4.5G worst case)"
    assert int(s["CPUWeight"]) < 100 and int(s["IOWeight"]) < 100, (
        "dev tooling must lose scheduling fights against the voice stack (weights 300-400)"
    )


@pytest.mark.parametrize(
    "mutate,expect",
    [
        (lambda t: t.replace("MemorySwapMax=0", "MemorySwapMax=2G"), "MemorySwapMax must be 0"),
        (lambda t: t.replace("MemoryHigh=3G", "MemoryHigh=2G"), "MemoryHigh != MemoryMax"),
        (lambda t: re.sub(r"^MemorySwapMax=0\n", "", t, flags=re.M), "MemorySwapMax missing"),
    ],
)
def test_slice_checker_catches_each_broken_key(mutate, expect):
    """Negative control: the checker above must go red on each way a 'cap' stops being one."""
    text = SLICE_FILE.read_text()
    broken = mutate(text)
    assert broken != text, "negative control did not alter the slice"
    assert any(expect in p for p in _slice_problems(broken))


# --------------------------------------------------------------- the launcher, run for real


def _fake_bin(tmp_path: Path) -> Path:
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    sr = b / "systemd-run"
    sr.write_text(
        "#!/usr/bin/env bash\n"
        '[ "${@: -1}" = true ] && exit 0\n'  # the availability probe ends in `true`
        'printf "%s\\n" "$@" > "$FAKE_RECORD"\n'
        'touch "$FAKE_STARTED"\n'
        '[ "${FAKE_HOLD:-0}" = 1 ] && exec sleep 60\n'
        "exit 0\n"
    )
    for name in ("claude", "codex"):
        (b / name).write_text("#!/usr/bin/env bash\nexit 0\n")
        (b / name).chmod(0o755)
    sr.chmod(0o755)
    return b


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    return {
        "PATH": f"{_fake_bin(tmp_path)}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "XDG_RUNTIME_DIR": str(run),
        "FAKE_RECORD": str(tmp_path / "systemd-run.args"),
        "FAKE_STARTED": str(tmp_path / "started"),
        **extra,
    }


def _hold_a_session(tmp_path: Path, script: Path = LAUNCHER) -> subprocess.Popen:
    """Start a session through the launcher and wait until it is really running (lease held)."""
    started = tmp_path / "started"
    started.unlink(missing_ok=True)
    p = subprocess.Popen(
        ["bash", str(script), "claude"], env=_env(tmp_path, FAKE_HOLD="1"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    deadline = time.time() + 15
    while not started.exists():
        assert time.time() < deadline and p.poll() is None, "first session never started"
        time.sleep(0.05)
    return p


def _launch(tmp_path: Path, *args: str, script: Path = LAUNCHER) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script), *args], env=_env(tmp_path), capture_output=True, text=True, timeout=30
    )


def _recorded(tmp_path: Path) -> list[str]:
    return (tmp_path / "systemd-run.args").read_text().splitlines()


@needs_flock
def test_launcher_enters_the_slice_and_passes_args_through(tmp_path):
    r = _launch(tmp_path, "codex", "exec", "--force", "-p", "hello")
    assert r.returncode == 0, r.stderr
    args = _recorded(tmp_path)
    assert {"--user", "--scope", "--slice=zoe-agents.slice"} <= set(args)
    sep = args.index("--")
    assert args[sep + 1].endswith("/codex")
    assert args[sep + 2 :] == ["exec", "--force", "-p", "hello"], (
        "everything after the CLI name belongs to the CLI - including a literal --force"
    )


@needs_flock
def test_second_session_is_refused_with_the_holders_pid_and_exit_75(tmp_path):
    holder = _hold_a_session(tmp_path)
    try:
        r = _launch(tmp_path, "claude")
        assert r.returncode == 75, (r.returncode, r.stderr)
        assert "another engineering session holds the lease" in r.stderr
        assert f"pid {holder.pid}" in r.stderr and "since" in r.stderr
    finally:
        holder.kill()
        holder.wait(timeout=10)
    # the lease dies with the session: no stale lock, no manual cleanup
    assert _launch(tmp_path, "claude").returncode == 0


@needs_flock
def test_force_is_the_only_way_past_the_lease_and_still_enters_the_slice(tmp_path):
    holder = _hold_a_session(tmp_path)
    try:
        r = _launch(tmp_path, "--force", "claude", "-p", "x")
        assert r.returncode == 0, r.stderr
        assert "WITHOUT the session lease" in r.stderr
        assert "--slice=zoe-agents.slice" in _recorded(tmp_path)
    finally:
        holder.kill()
        holder.wait(timeout=10)


@needs_flock
def test_negative_control_launcher_without_the_flock_does_not_refuse(tmp_path):
    """Break the guard: with the lease taken out of the exec path a second session starts."""
    src = LAUNCHER.read_text()
    broken_src = src.replace("flock -n -E 75 9", "true")
    assert broken_src != src, "negative control did not alter the launcher"
    broken = tmp_path / "zoe-agent-broken"
    broken.write_text(broken_src)
    holder = _hold_a_session(tmp_path, script=broken)
    try:
        r = _launch(tmp_path, "claude", script=broken)
        assert r.returncode != 75, "the mutated launcher still refused - the lease test proves nothing"
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_launcher_lease_is_not_the_brain_window_lock():
    for path in (LAUNCHER, BRIDGE_EXEC, BRIDGE_UNIT):
        assert HARNESS_LOCK not in _code(path.read_text()), (
            f"{path.name} must not take {HARNESS_LOCK}: an idle open session would block the nightly "
            "replay gate and every voice-PR landing"
        )


def test_launcher_is_executable_and_names_the_slice_and_lock():
    assert LAUNCHER.stat().st_mode & 0o111 and BRIDGE_EXEC.stat().st_mode & 0o111
    body = _code(LAUNCHER.read_text())
    assert "flock -n -E 75" in body
    assert 'SLICE="zoe-agents.slice"' in body and '--slice="$SLICE"' in body
    assert "zoe-agent-session.lock" in body
    subprocess.run(["bash", "-n", str(LAUNCHER)], check=True)
    subprocess.run(["bash", "-n", str(BRIDGE_EXEC)], check=True)


# ----------------------------------------------------------------------- the bridge unit


def test_bridge_unit_takes_the_same_lease_in_execstart_and_names_the_slice():
    svc = _section(BRIDGE_UNIT.read_text(), "Service")
    assert svc["Slice"] == "zoe-agents.slice"
    exec_start = svc["ExecStart"]
    assert re.match(r"/usr/bin/flock -n -E 75 %t/zoe-agent-session\.lock \S+/zoe-claude-bridge-exec$", exec_start), (
        "the lease must wrap the bridge in ExecStart itself (not ExecCondition/ExecStartPre, which "
        f"return before the bridge runs and release nothing): {exec_start}"
    )
    # same lock file as the launcher: %t is the user runtime dir the launcher locks in
    assert "zoe-agent-session.lock" in LAUNCHER.read_text()
    assert svc["RestartPreventExitStatus"].split()[0] == "75", "a refused lease must not be retried in a loop"
    assert exec_start.endswith("scripts/agents/zoe-claude-bridge-exec") and BRIDGE_EXEC.exists()


def test_bridge_unit_ships_inert():
    assert not re.search(r"^\[Install\]", BRIDGE_UNIT.read_text(), re.M), (
        "the bridge unit is stage-gated ([unverified] the app attaches to it); it must not be enable-able yet"
    )


def _bridge_env(tmp_path: Path, srv: Path, run: Path, proc: Path | None = None) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_RUNTIME_DIR": str(run),
        "AGENT_BRIDGE_SRV_ROOT": str(srv), "AGENT_BRIDGE_RUN_DIR": str(tmp_path / "bridge-run"),
        "AGENT_BRIDGE_PROC_ROOT": str(proc or tmp_path / "no-proc"), "OUT": str(tmp_path / "out"),
    }


def _release(srv: Path, name: str, *, file_age: float, dir_age: float) -> Path:
    d = srv / name
    d.mkdir(parents=True)
    s = d / "server"
    s.write_text(f'#!/usr/bin/env bash\necho "{name} $@" > "$OUT"\n')
    s.chmod(0o755)
    now = time.time()
    os.utime(s, (now - file_age, now - file_age))
    os.utime(d, (now - dir_age, now - dir_age))  # set LAST: writing the file would bump it
    return s


def test_bridge_exec_picks_the_release_by_server_file_mtime_not_directory_mtime(tmp_path):
    """Directory mtime moves only on create/remove: on the live box srv/89cb…/ has dir mtime 09-30
    while its `server` was rewritten 10-04. Here the OLDER-dir release has the NEWER server file."""
    srv, run = tmp_path / "srv", tmp_path / "run"
    run.mkdir()
    _release(srv, "newdir-oldfile", file_age=5000, dir_age=10)
    _release(srv, "olddir-newfile", file_age=10, dir_age=5000)
    r = subprocess.run(["bash", str(BRIDGE_EXEC)], env=_bridge_env(tmp_path, srv, run), check=True,
                       capture_output=True, text=True, timeout=30)
    out = (tmp_path / "out").read_text()
    assert out.startswith("olddir-newfile --serve --socket ") and "--token-file" in out, out
    assert "release olddir-newfile chosen by newest server file mtime" in r.stderr
    assert (tmp_path / "bridge-run" / "token").stat().st_mode & 0o077 == 0
    assert "cli=claude-bridge" in (run / "zoe-agent-session.holder").read_text()


def test_negative_control_bridge_exec_keyed_on_directory_mtime_picks_wrong(tmp_path):
    """Break the fix (dir-mtime criterion) and the same fixture must pick the other release."""
    src = BRIDGE_EXEC.read_text()
    broken_src = src.replace('if [ -z "$bin" ] || [ "$f" -nt "$bin" ]; then', 'if [ -z "$bin" ] || [ "$(dirname "$f")" -nt "$(dirname "$bin")" ]; then')
    assert broken_src != src, "negative control did not alter the helper"
    broken = tmp_path / "bridge-broken"
    broken.write_text(broken_src)
    srv, run = tmp_path / "srv", tmp_path / "run"
    run.mkdir()
    _release(srv, "newdir-oldfile", file_age=5000, dir_age=10)
    _release(srv, "olddir-newfile", file_age=10, dir_age=5000)
    subprocess.run(["bash", str(broken)], env=_bridge_env(tmp_path, srv, run), check=True, timeout=30)
    assert (tmp_path / "out").read_text().startswith("newdir-oldfile"), "mutant did not reproduce the bug"


def test_bridge_exec_prefers_the_release_a_running_serve_process_executes(tmp_path):
    """After an app rollback the live `--serve` runs an OLDER release than the newest file on disk."""
    srv, run, proc = tmp_path / "srv", tmp_path / "run", tmp_path / "proc"
    run.mkdir()
    old = _release(srv, "running-old", file_age=9000, dir_age=9000)
    _release(srv, "newer-on-disk", file_age=10, dir_age=10)
    for pid, cmd in (("11", "unrelated --serve"), ("12", f"{old} --serve --socket x")):
        (proc / pid).mkdir(parents=True)
        (proc / pid / "cmdline").write_bytes(cmd.replace(" ", "\0").encode() + b"\0")
    (proc / "12" / "exe").symlink_to(old)
    (proc / "11" / "exe").symlink_to("/usr/bin/sleep")
    r = subprocess.run(["bash", str(BRIDGE_EXEC)], env=_bridge_env(tmp_path, srv, run, proc), check=True,
                       capture_output=True, text=True, timeout=30)
    assert (tmp_path / "out").read_text().startswith("running-old --serve")
    assert "release running-old chosen by running --serve process 12" in r.stderr


@needs_flock
def test_bridge_unit_exec_path_fails_with_75_while_a_session_holds_the_lease(tmp_path):
    """The unit's ExecStart, run for real against a held lease (control 1: the unit must fail to start)."""
    run = tmp_path / "run"
    run.mkdir()
    lock = run / "zoe-agent-session.lock"
    srv = tmp_path / "srv" / "x"
    srv.mkdir(parents=True)
    (srv / "server").write_text("#!/usr/bin/env bash\nexit 0\n")
    (srv / "server").chmod(0o755)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_RUNTIME_DIR": str(run),
           "AGENT_BRIDGE_SRV_ROOT": str(tmp_path / "srv"), "AGENT_BRIDGE_RUN_DIR": str(tmp_path / "r")}
    cmd = ["/usr/bin/flock", "-n", "-E", "75", str(lock), str(BRIDGE_EXEC)]
    with open(lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert subprocess.run(cmd, env=env, timeout=30).returncode == 75
    assert subprocess.run(cmd, env=env, timeout=30).returncode == 0  # released -> starts


# ------------------------------------------------------------------------ the sampler


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _cg(root: Path, rel: str, current: int, swap: int = 0, **extra: str) -> None:
    d = root / rel.strip("/")
    _w(d / "memory.current", f"{current}\n")
    _w(d / "memory.swap.current", f"{swap}\n")
    for k, v in extra.items():
        _w(d / k.replace("_", "."), v)


U = "user.slice/user-1000.slice/user@1000.service"
IN = f"/{U}/zoe-agents.slice/serena-mcp.service"
OUT_APP = f"/{U}/app.slice/run-r1.scope"
OUT_SESSION = "/user.slice/user-1000.slice/session-72.scope"
CONTAINER = "/system.slice/docker-0123abcd.scope"
SECRET = "sk-test-NOT-A-REAL-TOKEN"


def _proc(proc: Path, pid: int, argv: list[str], cgroup: str) -> None:
    d = proc / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (d / "cgroup").write_text(f"0::{cgroup}\n")


def _tree(tmp_path: Path, *, serena_cg: str, jedi_cg: str, cbm_cg: str) -> dict:
    cg, proc = tmp_path / "cgroup", tmp_path / "proc"
    proc.mkdir(exist_ok=True)
    s = f"{U}/zoe-agents.slice"
    _cg(cg, s, 1_500_000_000, 1000, memory_max="3221225472\n", memory_swap_max="0\n",
        memory_events="high 0\nmax 2\noom 1\noom_kill 1\n")
    _cg(cg, f"{s}/serena-mcp.service", 400_000_000)
    _cg(cg, f"{s}/run-rA.scope", 90_000_000, 5000)
    _cg(cg, "system.slice/docker-0123abcd.scope", 648_000_000, 147_000_000)
    _w(tmp_path / "meminfo", "MemTotal: 16000000 kB\nMemAvailable: 2400000 kB\nSwapTotal: 50000000 kB\nSwapFree: 48600000 kB\n")
    _proc(proc, 100, ["/home/zoe/.local/share/uv/tools/serena-agent/bin/python3", "/home/zoe/.local/bin/serena",
                      "start-mcp-server", "--project", "/home/zoe/assistant"], serena_cg)
    _proc(proc, 101, ["/home/zoe/.local/share/uv/tools/jedi-language-server/bin/python", "/home/zoe/.local/bin/jedi-language-server"], jedi_cg)
    _proc(proc, 102, ["/home/zoe/.local/bin/codebase-memory-mcp"], cbm_cg)
    # the engineering CLI itself: argv is huge and mentions every tool name (and a secret)
    _proc(proc, 200, ["/home/zoe/.claude/remote/ccd-cli/2.1.286", "--allowedTools", "mcp__serena,codebase-memory-mcp",
                      "--api-key", SECRET], OUT_SESSION)
    # container processes share the host uid: reported, never "outside"
    _proc(proc, 300, ["node", "/usr/local/bin/codex", "app-server"], CONTAINER)
    _proc(proc, 301, ["/root/.local/share/uv/tools/omnigent/bin/python", "/root/.local/bin/omnigent", "server", "--port", "6767"], CONTAINER)
    # decoys that must NOT classify: a shell whose later args mention a tool; an unrelated process
    _proc(proc, 400, ["bash", "-c", "echo serena codex claude codebase-memory-mcp"], OUT_SESSION)
    _proc(proc, 401, ["/usr/bin/python3", "/home/zoe/assistant/scripts/maintenance/zoe_agents_sampler.py"], OUT_SESSION)
    return dict(cgroup_root=cg, proc_root=proc, meminfo_path=tmp_path / "meminfo",
                runtime_dir=tmp_path / "run", proc_locks=tmp_path / "locks", uid=1000, now=0)


def _record(tmp_path: Path, **cgs: str) -> dict:
    return sampler.build_record(**_tree(tmp_path, **cgs))


def test_sampler_parses_a_fake_cgroup_tree(tmp_path):
    r = _record(tmp_path, serena_cg=IN, jedi_cg=IN, cbm_cg=f"/{U}/zoe-agents.slice/run-rA.scope")
    assert r["mem_available_kb"] == 2_400_000 and r["swap_used_kb"] == 1_400_000
    sl = r["slice"]
    assert sl["present"] and sl["memory_current"] == 1_500_000_000 and sl["swap_current"] == 1000
    assert sl["memory_max"] == 3221225472 and sl["swap_max"] == 0
    assert sl["events"]["oom_kill"] == 1 and sl["events"]["max"] == 2
    assert {m["name"]: m["memory_current"] for m in sl["members"]} == {
        "serena-mcp.service": 400_000_000, "run-rA.scope": 90_000_000}
    assert r["omnigent"] == {"found": True, "memory_current": 648_000_000, "swap_current": 147_000_000}
    p = r["procs"]
    assert p["serena"] == {"total": 1, "in_slice": 1, "outside_slice": 0, "container": 0}
    assert p["codebase-memory-mcp"]["in_slice"] == 1
    assert p["ccd-cli"] == {"total": 1, "in_slice": 0, "outside_slice": 1, "container": 0}
    assert p["codex"] == {"total": 0, "in_slice": 0, "outside_slice": 0, "container": 1}
    assert r["ts"] == "1970-01-01T00:00:00Z"
    assert sl["state"] == "capped" and r["in_slice_bounded"] is True
    assert sl["cpu_weight"] is None and sl["io_weight"] is None  # absent files are null, not a crash


def test_implicit_slice_is_reported_distinctly_and_is_not_bounded(tmp_path):
    """`systemd-run --slice=zoe-agents.slice` with no unit file creates a slice with NO limits
    (codebase_memory_capped.sh does this from merge day): `present` is true, memory.max reads `max`,
    and codebase-memory counts as in_slice. The baseline week must not read that as a bound."""
    t = _tree(tmp_path, serena_cg=OUT_APP, jedi_cg=OUT_APP, cbm_cg=f"/{U}/zoe-agents.slice/run-rA.scope")
    sdir = t["cgroup_root"] / U / "zoe-agents.slice"
    (sdir / "memory.max").write_text("max\n")
    (sdir / "memory.swap.max").write_text("max\n")
    r = sampler.build_record(**t)
    assert r["slice"]["present"] is True and r["slice"]["memory_max"] is None
    assert r["slice"]["state"] == "implicit slice (no memory.max)"
    assert r["procs"]["codebase-memory-mcp"]["in_slice"] == 1, "structurally in the implicit slice"
    assert r["in_slice_bounded"] is False, "...but that is parenting, not a bound"
    # negative control: a real memory.max flips the same tree to capped/bounded
    (sdir / "memory.max").write_text("3221225472\n")
    r2 = sampler.build_record(**t)
    assert r2["slice"]["state"] == "capped" and r2["in_slice_bounded"] is True


def test_slice_weights_are_read_back_when_the_kernel_exposes_them(tmp_path):
    t = _tree(tmp_path, serena_cg=IN, jedi_cg=IN, cbm_cg=IN)
    sdir = t["cgroup_root"] / U / "zoe-agents.slice"
    (sdir / "cpu.weight").write_text("50\n")
    (sdir / "io.weight").write_text("default 50\n")
    sl = sampler.build_record(**t)["slice"]
    assert sl["cpu_weight"] == 50 and sl["io_weight"] == 50


def test_negative_control_4_outside_the_slice_goes_red_then_green(tmp_path):
    """Record control 4: before the drop-in and the wrapper change serena, jedi and codebase-memory sit
    in app.slice - the count must read RED (3 outside + the ccd-cli session); after, only the CLI is left."""
    before = _record(tmp_path, serena_cg=f"/{U}/app.slice/serena-mcp.service",
                     jedi_cg=f"/{U}/app.slice/serena-mcp.service", cbm_cg=OUT_APP)
    outside = {k: v["outside_slice"] for k, v in before["procs"].items()}
    assert outside["serena"] == outside["jedi-language-server"] == outside["codebase-memory-mcp"] == 1
    assert before["slice"]["state"] == "capped"  # fixture slice is capped; the processes are what sit outside
    assert before["outside_slice_total"] == 4  # + the ccd-cli session, which only the launcher/bridge unit can fix

    after = _record(tmp_path, serena_cg=IN, jedi_cg=IN, cbm_cg=f"/{U}/zoe-agents.slice/run-rA.scope")
    assert after["procs"]["serena"]["outside_slice"] == 0
    assert after["procs"]["jedi-language-server"]["outside_slice"] == 0
    assert after["procs"]["codebase-memory-mcp"]["outside_slice"] == 0
    assert after["outside_slice_total"] == 1


@pytest.mark.parametrize("path,expected", [
    (f"/{U}/zoe-agents.slice/serena-mcp.service", True),
    (f"/{U}/zoe-agents.slice", True),
    (f"/{U}/zoe-agents.slice.bak/serena-mcp.service", False),   # substring, not a component
    (f"/{U}/not-zoe-agents.slice/x.scope", False),
    (f"/{U}/app.slice/zoe-agents.slice-note.scope", False),
    ("/user.slice/user-1000.slice/session-72.scope", False),
    (None, False),
])
def test_in_slice_is_a_whole_component_match(path, expected):
    assert sampler.in_slice(path) is expected


def test_classifier_matches_argv_head_not_substrings():
    c = sampler.classify
    assert c(["python3", "/home/zoe/.local/bin/serena", "start-mcp-server"]) == "serena"
    assert c(["/home/zoe/.local/bin/codebase-memory-mcp"]) == "codebase-memory-mcp"
    assert c(["/home/zoe/.claude/remote/ccd-cli/2.1.286", "--x", "serena"]) == "ccd-cli"
    assert c(["node", "/usr/local/bin/codex", "app-server"]) == "codex"
    assert c(["bash", "-c", "serena codex claude"]) is None
    assert c(["/usr/bin/vim", "serena"]) is None
    assert c(["shairport-sync", "codex"]) is None  # `sh…` prefix must not make it an interpreter
    assert c([]) is None


def test_container_processes_are_counted_beside_not_outside(tmp_path):
    r = _record(tmp_path, serena_cg=IN, jedi_cg=IN, cbm_cg=IN)
    assert r["procs"]["codex"]["container"] == 1 and r["procs"]["codex"]["outside_slice"] == 0
    assert r["outside_slice_total"] == 1  # only the ccd-cli session; the container is separate


def test_sampler_record_carries_no_command_lines_or_paths(tmp_path):
    r = _record(tmp_path, serena_cg=IN, jedi_cg=IN, cbm_cg=IN)
    blob = json.dumps(r)
    for leak in (SECRET, "--api-key", "/home/zoe", "start-mcp-server", "allowedTools", "session-72"):
        assert leak not in blob, f"sampler leaked {leak!r}"


def test_sampler_never_crashes_on_a_missing_tree(tmp_path):
    r = sampler.build_record(cgroup_root=tmp_path / "nope", proc_root=tmp_path / "nope2",
                             meminfo_path=tmp_path / "nope3", runtime_dir=tmp_path / "nope4",
                             proc_locks=tmp_path / "nope5", uid=1000, now=0)
    assert r["slice"] == {"present": False, "state": "absent"} and r["omnigent"] == {"found": False}
    assert r["mem_available_kb"] is None and r["outside_slice_total"] == 0


def test_lease_report_reads_the_lock_holder_from_proc_locks(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "zoe-agent-session.lock").write_text("")
    ino = (run / "zoe-agent-session.lock").stat().st_ino
    _w(tmp_path / "locks", f"1: FLOCK  ADVISORY  WRITE 4242 08:02:{ino} 0 EOF\n2: POSIX  ADVISORY  WRITE 1 08:02:99 0 EOF\n")
    _w(run / "zoe-agent-session.holder", f"pid={os.getpid()}\nsince=1700000000\ncli=claude\n")
    rep = sampler.lease_report(run, tmp_path / "locks")
    assert rep["held"] is True and rep["lock_pid"] == 4242
    assert rep["holder_file"] == {"pid": os.getpid(), "since": 1700000000, "cli": "claude", "alive": True}
    # an unrelated lock on another inode is not the lease
    _w(tmp_path / "locks", "1: FLOCK  ADVISORY  WRITE 4242 08:02:1 0 EOF\n")
    assert sampler.lease_report(run, tmp_path / "locks")["held"] is False


def test_sampler_appends_private_jsonl_and_rotates(tmp_path, capsys):
    out = tmp_path / "logs" / "agents-sampler.jsonl"
    t = _tree(tmp_path, serena_cg=IN, jedi_cg=IN, cbm_cg=IN)
    argv = ["--out", str(out), "--cgroup-root", str(t["cgroup_root"]), "--proc-root", str(t["proc_root"]),
            "--meminfo", str(t["meminfo_path"]), "--runtime-dir", str(t["runtime_dir"]),
            "--proc-locks", str(t["proc_locks"]), "--uid", "1000"]
    assert sampler.main(argv) == 0 and sampler.main(argv) == 0
    lines = out.read_text().splitlines()
    assert len(lines) == 2 and all(json.loads(l)["slice"]["present"] for l in lines)
    assert out.stat().st_mode & 0o077 == 0
    out.write_text("x" * (sampler.ROTATE_BYTES + 1))
    sampler.main(argv)
    assert out.with_name(out.name + ".1").exists() and len(out.read_text().splitlines()) == 1
    assert sampler.main(["--stdout", *argv[2:]]) == 0
    assert json.loads(capsys.readouterr().out)["procs"]["serena"]["in_slice"] == 1


def test_sampler_is_stdlib_only_and_reads_no_zoe_flags():
    tree = ast.parse(SAMPLER.read_text())
    mods = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)
    assert "ZOE_" not in SAMPLER.read_text(), "operator tooling: no ZOE_* flag readers (keeps flag-inventory untouched)"


def test_sampler_units_run_the_script_every_five_minutes():
    assert SAMPLER.exists()
    svc = _section(SAMPLER_SERVICE.read_text(), "Service")
    assert svc["Type"] == "oneshot" and svc["ExecStart"].endswith("scripts/maintenance/zoe_agents_sampler.py")
    assert svc["MemorySwapMax"] == "0"
    tm = _section(SAMPLER_TIMER.read_text(), "Timer")
    assert tm["OnUnitActiveSec"] == "5min"


def test_delegation_template_is_tracked_with_the_controllers_the_slice_weights_need():
    """CPUWeight/IOWeight in a user slice are inert unless the user manager is delegated cpu+io; the
    root drop-in that does it was untracked on the live host. The template must carry them, and the
    slice header must name the dependency."""
    conf = ROOT / "scripts" / "setup" / "systemd" / "system" / "user@.service.d" / "delegate.conf"
    delegate = _section(conf.read_text(), "Service")["Delegate"].split()
    assert {"memory", "cpu", "io"} <= set(delegate), delegate
    header = SLICE_FILE.read_text()
    assert "delegate.conf" in header and "Delegate=pids memory cpu io" in header
    assert "io.weight" in header, "the slice must say IOWeight needs a block scheduler that exposes io.weight"
