"""ZMB bake-off: the owner's one command (``scripts/perf/zmb/bakeoff_window.sh`` -> ``bakeoff.py`` / ``bakeoff_measure.py`` / ``bakeoff_gates.py``).

Nothing here starts a service, a container, a brain or a network connection. Every external command (``systemctl``, ``systemd-run``,
``docker``, ``ssh``, ``pgrep``, ``curl``) goes through ``Host``; the tests replace it with ``FakeHost`` (the same trick the deploy tests use with
a shimmed ``systemctl``) and run the REAL step logic, red-before-green on the four promises:

    lock held -> refuses        panel not quiet -> waits        MemAvailable low -> aborts and restores        any failure -> restore runs

plus: the Gemma clone command is GENERATED from the live unit text (never hand-written), the measurement end to end over the fake Hindsight, and
the pre-registered decision rule's thresholds are pinned (no threshold may move after a run has been seen).
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import artifact, bakeoff, bakeoff_gates as gates, bakeoff_measure as measure, spec  # noqa: E402
from zmb.arms.fake_hindsight import FakeHindsight  # noqa: E402
from zmb.arms.hindsight import HindsightArm, HindsightClient  # noqa: E402
from zmb.world import make_world  # noqa: E402

LIVE_UNIT = textwrap.dedent("""\
    # /home/zoe/.config/systemd/user/llama-server.service
    [Service]
    LimitMEMLOCK=infinity
    MemorySwapMax=0
    MemoryLow=6G
    CPUWeight=400
    IOWeight=400
    Environment=LD_LIBRARY_PATH=%h/llama.cpp-b11194/build-jetson/bin
    ExecStart=%h/llama.cpp-b11194/build-jetson/bin/llama-server \\
      --model %h/models/gemma4-e4b-qat/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf \\
      --spec-type draft-mtp \\
      --host 127.0.0.1 \\
      --port 11434 \\
      --ctx-size 8192 \\
      --parallel 2 \\
      --cache-ram 2048 \\
      --metrics
    ExecStartPost=/bin/bash -c 'curl -sf http://127.0.0.1:11434/health'

    # /home/zoe/.config/systemd/user.control/llama-server.service.d/50-MemoryLow.conf
    [Service]
    MemoryLow=6442450944

    # /home/zoe/.config/systemd/user/llama-server.service.d/80-swa-full.conf
    [Service]
    ExecStart=
    ExecStart=%h/llama.cpp-b11194/build-jetson/bin/llama-server \\
      --model %h/models/gemma4-e4b-qat/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf \\
      --spec-type draft-mtp \\
      --host 127.0.0.1 \\
      --port 11434 \\
      --ctx-size 8192 \\
      --parallel 1 \\
      --cache-ram 1024 \\
      --swa-full \\
      --metrics
    """)
HOME = "/home/zoe"
ENV_EXAMPLE = textwrap.dedent("""\
    # example
    HINDSIGHT_API_HOST=127.0.0.1
    HINDSIGHT_API_PORT=18888
    HINDSIGHT_API_LLM_BASE_URL=http://127.0.0.1:11500/v1
    HINDSIGHT_API_LLM_TRACE_ENABLED=false
    HINDSIGHT_API_EMBEDDINGS_OPENAI_BASE_URL=http://127.0.0.1:11501/v1
    HINDSIGHT_API_EMBEDDINGS_OPENAI_BATCH_SIZE=16
    # HINDSIGHT_API_WEBHOOK_URL=https://commented.example.com/ignored
    """)


# ── the clone is generated, never hand-written ───────────────────────────────

def test_the_clone_is_the_live_unit_with_only_the_port_changed():
    c = bakeoff.clone_command(LIVE_UNIT, HOME, 11500)
    live, argv = c["live_argv"], c["argv"]
    assert argv[0] == f"{HOME}/llama.cpp-b11194/build-jetson/bin/llama-server"                # %h expanded
    assert c["diff"] == [("11434", "11500")]                                                    # the drop-in's ExecStart (parallel 1) won; ONLY the port differs
    assert argv[argv.index("--parallel") + 1] == "1" and "--swa-full" in argv and argv[argv.index("--cache-ram") + 1] == "1024"
    assert [a for a in live if a not in argv] == ["11434"] and c["model"] == "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"
    assert c["env"] == {"LD_LIBRARY_PATH": f"{HOME}/llama.cpp-b11194/build-jetson/bin"}
    assert c["props"] == {"LimitMEMLOCK": "infinity", "MemorySwapMax": "0", "MemoryLow": "6442450944", "CPUWeight": "400", "IOWeight": "400"}


def test_a_live_unit_with_two_slots_is_cloned_with_one():
    base = LIVE_UNIT.split("# /home/zoe/.config/systemd/user.control")[0]               # no drop-in: --parallel 2 in the live ExecStart
    c = bakeoff.clone_command(base, HOME, 11500)
    assert c["live_argv"][c["live_argv"].index("--parallel") + 1] == "2"
    assert c["argv"][c["argv"].index("--parallel") + 1] == "1" and ("2", "1") in c["diff"]


def test_a_parallel_flag_is_added_when_the_unit_has_none():
    c = bakeoff.clone_command(LIVE_UNIT.replace("--parallel 1", "").replace("--parallel 2", ""), HOME, 11500)
    assert c["argv"][-2:] == ["--parallel", "1"]


def test_the_clone_refuses_a_unit_that_is_not_the_brain_or_is_not_loopback():
    with pytest.raises(bakeoff.Refused, match="loopback"):
        bakeoff.clone_command(LIVE_UNIT.replace("--host 127.0.0.1", "--host 0.0.0.0"), HOME, 11500)
    with pytest.raises(bakeoff.Refused, match="--model"):
        bakeoff.clone_command("[Service]\nExecStart=/bin/sleep 5 --host 127.0.0.1\n", HOME, 11500)
    with pytest.raises(bakeoff.Refused, match="no ExecStart"):
        bakeoff.clone_command("[Service]\nMemoryLow=6G\n", HOME, 11500)


def test_systemd_run_wraps_the_clone_in_a_named_transient_unit():
    cmd = bakeoff.systemd_run(bakeoff.UNITS["clone"], ["/bin/x", "--port", "11500"], env={"A": "1"}, props={"MemorySwapMax": "0"})
    assert cmd[:3] == ["systemd-run", "--user", "--unit=zoe-bakeoff-gemma"] and "--collect" in cmd
    assert "--property=MemorySwapMax=0" in cmd and "--setenv=A=1" in cmd and cmd[cmd.index("--") + 1:] == ["/bin/x", "--port", "11500"]


def test_the_hindsight_environment_is_loopback_only_and_has_the_run_overrides():
    env = bakeoff.hindsight_env(ENV_EXAMPLE, bakeoff.Cfg(bakeoff_dir=Path("/b")), "r1")
    kv = dict(l.split("=", 1) for l in env.splitlines() if "=" in l and not l.startswith("#"))
    assert kv["HINDSIGHT_API_LLM_TRACE_ENABLED"] == "true" and kv["HINDSIGHT_API_EMBEDDINGS_OPENAI_BATCH_SIZE"] == "8"
    assert kv["HINDSIGHT_API_LLM_BASE_URL"] == "http://127.0.0.1:11500/v1" and kv["HINDSIGHT_API_DATABASE_URL"].endswith("127.0.0.1:55432/hindsight")
    assert kv["EGRESS_AUDIT_LOG"] == "/b/egress-r1.log" and kv["PYTHONPATH"] == str(bakeoff.EGRESS_AUDIT_DIR)       # the hook ships in the repo
    assert (bakeoff.EGRESS_AUDIT_DIR / "sitecustomize.py").is_file() and kv["PYTHONDONTWRITEBYTECODE"] == "1"
    with pytest.raises(bakeoff.Refused, match="non-loopback"):
        bakeoff.hindsight_env(ENV_EXAMPLE + "HINDSIGHT_API_WEBHOOK_URL=https://hooks.example.com/x\n", bakeoff.Cfg(bakeoff_dir=Path("/b")), "r1")


def test_the_generated_environment_has_no_inline_comments_systemd_would_read_as_part_of_the_value():
    example = ENV_EXAMPLE + 'HINDSIGHT_API_WORKERS=1                  # one worker: the clone has one slot\nHINDSIGHT_API_X="a # b"   # quoted hash stays\n'
    env = bakeoff.hindsight_env(example, bakeoff.Cfg(bakeoff_dir=Path("/b")), "r1")
    kv = dict(l.split("=", 1) for l in env.splitlines() if "=" in l and not l.startswith("#"))
    assert kv["HINDSIGHT_API_WORKERS"] == "1" and kv["HINDSIGHT_API_X"] == '"a # b"'
    assert all(" #" not in l for l in env.splitlines() if "=" in l and not l.startswith("#") and '"a # b"' not in l)
    assert bakeoff.strip_inline_comment("# whole line stays") == "# whole line stays" and bakeoff.strip_inline_comment("") == ""


def test_small_parsers():
    assert bakeoff.parse_panel_age("2026-10-06 06:00:00\n", time.mktime((2026, 10, 6, 6, 10, 0, 0, 0, -1))) == pytest.approx(600, abs=1)
    assert bakeoff.parse_panel_age("", 0.0) is None and bakeoff.parse_panel_age("garbage", 0.0) is None
    assert measure.parse_pss_kb("Rss: 5 kB\nPss: 2048 kB\n") == 2048.0 and measure.parse_pss_kb("") == 0.0
    assert measure.parse_metrics_seconds("llamacpp:prompt_seconds_total 10.5\nllamacpp:tokens_predicted_seconds_total 4.5\nx 1\n") == 15.0
    assert measure.parse_metrics_seconds("nothing") is None and measure.parse_mib("12.5MiB / 256MiB") == 12.5
    assert measure.probe_sentences(8) == measure.probe_sentences(8)                                     # deterministic, invented
    fam = [type("C", (), {"id": i})() for i in ("A1.a", "A1.b", "A1.c", "F2.x", "F2.y", "H1.z")]
    assert [c.id for c in measure.interleave(fam)] == ["A1.a", "F2.x", "H1.z", "A1.b", "F2.y", "A1.c"]    # a time box samples every family first


# ── the host double: the same trick the deploy tests use ─────────────────────

class FakeHost(bakeoff.Host):
    def __init__(self, tmp: Path, *, panel_busy_for=0.0, landing_for=0.0, mem=None, live_ok=True, fail=(), hook_live=True):
        self.t = 1_800_000_000.0
        self.t0 = self.t
        self.cmds: "list[tuple[list[str], bool]]" = []
        self.slept: "list[float]" = []
        self.panel_busy_until = self.t + panel_busy_for
        self.landing_until = self.t + landing_for
        self.mem = mem or (lambda h: 3000.0)
        self.live_ok, self.fail = live_ok, set(fail)
        self.hook_live = hook_live
        self.live_active = True
        self.tmp = tmp
        self.metrics_calls = 0
        self.log = lambda _m: None

    def joined(self) -> "list[str]":
        return [" ".join(a) for a, _m in self.cmds]

    def mutating_cmds(self) -> "list[str]":
        return [" ".join(a) for a, m in self.cmds if m]

    def index(self, needle: str) -> int:
        j = self.joined()
        return next(i for i, c in enumerate(j) if needle in c)

    def run(self, argv, timeout=60.0, mutating=True, env=None):
        self.cmds.append((list(argv), mutating))
        line = " ".join(argv)
        if any(f in line for f in self.fail):
            return bakeoff.Result(1, "injected failure")
        prog = argv[0]
        if prog == "systemctl":
            sub = argv[2]
            if sub == "cat":
                return bakeoff.Result(0, LIVE_UNIT)
            if sub == "is-active":
                return bakeoff.Result(0, "active\n" if self.live_active else "inactive\n")
            if sub == "show":
                return bakeoff.Result(0, "/system.slice/zoe-bakeoff.service\n" if "ControlGroup" in line else "")
            if sub == "start" and argv[3] == "llama-server.service":
                self.live_active = True
            if sub == "stop" and argv[3] == "llama-server.service":
                self.live_active = False
            return bakeoff.Result(0, "")
        if prog == "ssh":
            age = max(0.0, self.t - (self.panel_busy_until - 599.0)) if self.t < self.panel_busy_until else 10_000.0
            return bakeoff.Result(0, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.t - min(age, 10_000.0))) + "\n")
        if prog == "pgrep":
            return bakeoff.Result(0 if self.t < self.landing_until and "land_voice_pr" in line else 1, "")
        if prog == "curl":
            if ":11434/health" in line:
                return bakeoff.Result(0, "ok") if self.live_ok and self.live_active else bakeoff.Result(7, "")
            if "/metrics" in line:
                self.metrics_calls += 1
                return bakeoff.Result(0, f"llamacpp:prompt_seconds_total {self.metrics_calls * 6.0}\nllamacpp:tokens_predicted_seconds_total {self.metrics_calls * 4.0}\n")
            return bakeoff.Result(0, '{"status":"ok"}')
        if prog == "docker":
            return bakeoff.Result(0, "50MiB / 256MiB" if "stats" in argv else "")
        if prog == "git":
            return bakeoff.Result(0, "abc1234\n")
        return bakeoff.Result(0, "")

    def sleep(self, s):
        self.slept.append(s)
        self.t += s

    def now(self):
        return self.t

    def mono(self):
        return self.t

    def mem_available_mb(self, path):
        return self.mem(self)

    def read(self, path):
        if path.endswith("cgroup.procs"):
            return "100\n"
        if path.endswith("smaps_rollup"):
            return "Rss: 1 kB\nPss: 204800 kB\n"
        if "/egress-" in path and self.hook_live:           # the server's egress hook, as a healthy one writes it
            return "12:00:00 pid=1 ok hook-loaded uvloop=blocked\n12:00:01 pid=1 ok connect ('127.0.0.1', 55432)\n"
        return ""


@pytest.fixture
def box(tmp_path):
    b = tmp_path / "bakeoff"
    (b / "hindsight-venv" / "bin").mkdir(parents=True)
    for f in ("hindsight-venv/bin/hindsight-api", "scratch-postgres.compose.yml"):
        (b / f).write_text("x")
    py = b / "hindsight-venv" / "bin" / "python"                   # stands in for the venv python: `embed_shim.py --find` succeeds
    py.write_text("#!/bin/bash\necho '{\"onnx\": \"/x/model.onnx\"}'\n")
    py.chmod(0o755)
    (b / "hindsight.env.example").write_text(ENV_EXAMPLE)
    return b


def make_window(box, host, *, measure_fn=None, dry=False, **cfg_kw):
    cfg_kw.setdefault("blackouts", ())
    cfg = bakeoff.Cfg(bakeoff_dir=box, lock_path=str(box / "lock"), quiet_poll_s=60.0, health_wait_s=20.0, **cfg_kw)
    logs: "list[str]" = []
    w = bakeoff.Window(cfg, host, logs.append, dry=dry, run_id="t1", measure_fn=measure_fn)
    w.logs = logs
    return w


def restored(host) -> bool:
    j = host.joined()
    return (any("stop zoe-bakeoff-hindsight.service" in c for c in j) and any("stop zoe-bakeoff-gemma.service" in c for c in j)
            and any("stop zoe-bakeoff-embed.service" in c for c in j) and any("compose" in c and c.endswith("down") for c in j)
            and any(c == "systemctl --user start llama-server.service" for c in j))


# ── the four promises ────────────────────────────────────────────────────────

def test_a_held_lock_refuses_and_starts_nothing(box):
    host = FakeHost(box)
    fd = os.open(str(box / "lock"), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)                      # a landing / the bar owns the brain
    try:
        w = make_window(box, host)
        assert w.run() == bakeoff.EXIT_REFUSED
    finally:
        os.close(fd)
    assert host.mutating_cmds() == [] and not (box / "WINDOW_OPEN").exists()
    assert any("held" in l for l in w.logs)
    assert not any(a[0] in ("ssh", "pgrep") for a, _m in host.cmds)         # refused at once: it did not wait for a quiet panel first


def test_a_lock_taken_while_waiting_for_quiet_is_still_refused(box, monkeypatch):
    host = FakeHost(box, panel_busy_for=300)
    w = make_window(box, host)
    real_sleep = host.sleep
    holder: "list[int]" = []

    def sleep_then_steal(s):
        real_sleep(s)
        if not holder:                                                     # another process takes the lock mid-wait
            fd = os.open(str(box / "lock"), os.O_CREAT | os.O_RDWR)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            holder.append(fd)
    host.sleep = sleep_then_steal
    try:
        assert w.run() == bakeoff.EXIT_REFUSED
    finally:
        for fd in holder:
            os.close(fd)
    assert host.mutating_cmds() == []


def test_the_panel_not_quiet_waits_then_proceeds(box):
    host = FakeHost(box, panel_busy_for=400)                               # a voice turn 1 s ago: busy for ~7 more polls of 60 s
    w = make_window(box, host, measure_fn=lambda win: {"ok": 1})
    assert w.run() == bakeoff.EXIT_OK
    assert sum(host.slept) >= 360 and any("waiting" in l and "panel" in l for l in w.logs)
    assert restored(host)
    last_panel_poll = max(i for i, (a, _m) in enumerate(host.cmds) if a[0] == "ssh")
    first_change = min(i for i, (_a, m) in enumerate(host.cmds) if m)
    assert first_change > last_panel_poll                                    # nothing was started until the panel had been quiet


def test_a_landing_or_the_bar_running_waits(box):
    host = FakeHost(box, landing_for=200)
    w = make_window(box, host, measure_fn=lambda win: {})
    assert w.run() == bakeoff.EXIT_OK and any("voice-PR landing" in l for l in w.logs) and sum(host.slept) >= 180
    patterns = [a[-1] for a, _m in host.cmds if a[0] == "pgrep"]
    assert r"^bash .*/land_voice_pr\.sh" in patterns and r"samantha_bar\.py|samantha_day_sim\.py" in patterns       # the anchored patterns, as the land script


def test_never_quiet_is_a_refusal_not_an_endless_wait(box):
    host = FakeHost(box, panel_busy_for=10 ** 7)
    w = make_window(box, host, quiet_wait_max_min=10)
    assert w.run() == bakeoff.EXIT_REFUSED and host.mutating_cmds() == []
    assert any("still not quiet" in l for l in w.logs)


def test_low_memory_at_the_start_refuses_before_anything_is_started(box):
    host = FakeHost(box, mem=lambda h: 900.0)
    assert make_window(box, host).run() == bakeoff.EXIT_REFUSED and host.mutating_cmds() == []


def test_low_memory_mid_window_aborts_and_restores(box):
    host = FakeHost(box, mem=lambda h: 900.0 if any("--unit=zoe-bakeoff-gemma" in c for c in h.joined()) else 3000.0)
    called: "list[int]" = []
    w = make_window(box, host, measure_fn=lambda win: called.append(1) or {})
    assert w.run() == bakeoff.EXIT_ABORTED
    assert not called and restored(host) and any("floor" in l for l in w.logs)


def test_the_sampler_flag_and_the_hard_cap_abort_and_restore(box):
    host = FakeHost(box)

    def flagged(win):
        win.abort_flag = "MemAvailable 800 MB < 1200 MB floor (sampler)"
        win.guard()
    assert make_window(box, host, measure_fn=flagged).run() == bakeoff.EXIT_ABORTED and restored(host)
    host2 = FakeHost(box)

    def overrun(win):
        host2.t += 91 * 60
        win.guard()
    assert make_window(box, host2, measure_fn=overrun).run() == bakeoff.EXIT_ABORTED and restored(host2)


@pytest.mark.parametrize("where", ["measure", "gemma_start", "hindsight_start", "shim_start", "postgres", "stop_live"])
def test_any_failure_still_runs_the_restore(box, where):
    fail = {"gemma_start": ("--unit=zoe-bakeoff-gemma",), "hindsight_start": ("--unit=zoe-bakeoff-hindsight",), "shim_start": ("--unit=zoe-bakeoff-embed",),
            "postgres": ("compose -f",), "stop_live": ("stop llama-server.service",)}.get(where, ())
    if where == "postgres":
        fail = ("up -d",)
    host = FakeHost(box, fail=fail)

    def boom(win):
        raise RuntimeError("the measurement blew up")
    w = make_window(box, host, measure_fn=boom if where == "measure" else (lambda win: {}))
    assert w.run() == bakeoff.EXIT_ABORTED
    assert restored(host), f"restore did not run after a failure in {where}"
    assert not (box / "WINDOW_OPEN").exists()                              # a clean restore removes the marker


def test_a_failed_restore_is_loud_and_leaves_the_marker(box):
    host = FakeHost(box)
    host.live_ok = False                                                   # the live brain never comes back healthy
    w = make_window(box, host, measure_fn=lambda win: {})
    assert w.run() == bakeoff.EXIT_RESTORE_FAILED
    assert (box / "WINDOW_OPEN").exists() and any("LIVE BRAIN NOT HEALTHY" in l for l in w.logs)


def test_a_window_that_would_run_into_a_maintenance_window_is_refused(box):
    import datetime as dt
    host = FakeHost(box)
    start = dt.datetime.fromtimestamp(host.t)
    minute = start.hour * 60 + start.minute
    clash = ((minute + 30) % 1440, (minute + 40) % 1440)                       # a maintenance window starting 30 min from now
    if clash[0] < clash[1]:
        w = make_window(box, host, blackouts=(clash,))
        assert w.run() == bakeoff.EXIT_REFUSED and host.mutating_cmds() == []
        assert any("maintenance window" in l for l in w.logs)
    far = ((minute + 400) % 1440, (minute + 410) % 1440)                       # 6+ hours away: fine
    if far[0] < far[1]:
        assert make_window(box, FakeHost(box), blackouts=(far,), measure_fn=lambda win: {}).run() == bakeoff.EXIT_OK


def test_no_embedding_model_on_disk_is_refused_before_the_brain_is_touched(box):
    host = FakeHost(box, fail=("embed_shim.py --find",))
    w = make_window(box, host)
    assert w.run() == bakeoff.EXIT_REFUSED and host.mutating_cmds() == []
    assert any("no embedding model" in l for l in w.logs)


def test_no_live_brain_to_take_over_is_a_refusal(box):
    host = FakeHost(box)
    host.live_active = False
    assert make_window(box, host).run() == bakeoff.EXIT_REFUSED and host.mutating_cmds() == []


# ── the happy path: order, and what restore may touch ────────────────────────

def test_the_window_steps_run_in_order_and_the_lock_is_released(box):
    host = FakeHost(box)
    seen: "list[str]" = []
    w = make_window(box, host, measure_fn=lambda win: seen.append("measure") or {"ok": True})
    assert w.run() == bakeoff.EXIT_OK and seen == ["measure"]
    order = [host.index(n) for n in ("compose -f", "--unit=zoe-bakeoff-embed", "stop llama-server.service", "--unit=zoe-bakeoff-gemma",
                                     "--unit=zoe-bakeoff-hindsight")]
    assert order == sorted(order)                                          # postgres, shim, STOP live, clone, hindsight
    j = host.joined()
    assert j.index("systemctl --user start llama-server.service") > host.index("--unit=zoe-bakeoff-hindsight")
    assert w.probe_lock() and not (box / "WINDOW_OPEN").exists() and w.restore_status.startswith("live brain healthy")
    clone = next(c for c in j if "--unit=zoe-bakeoff-gemma" in c)
    assert "--port 11500" in clone and "--parallel 1" in clone and "--swa-full" in clone and "MemorySwapMax=0" in clone


def test_restore_only_touches_what_it_started_and_is_idempotent(box):
    host = FakeHost(box)
    w = make_window(box, host)
    assert w.restore() and w.restore()
    stops = {c.split()[-1] for c in host.joined() if c.startswith("systemctl --user stop")}
    assert stops == set(bakeoff.UNITS.values())                            # never llama-server, never zoe-data, never anything else
    assert not any(c.startswith("systemctl --user stop llama-server") for c in host.joined())
    assert all("zoe-bakeoff" in c or "scratch-postgres" in c for c in host.joined() if "compose" in c or "reset-failed" in c)


# ── dry run and the shell wrapper, with every service command shimmed ────────

def _shims(tmp_path: Path, box: Path) -> "tuple[Path, Path, dict]":
    shim = tmp_path / "shims"
    shim.mkdir()
    log = tmp_path / "shim.log"
    unit = tmp_path / "unit.txt"
    unit.write_text(LIVE_UNIT)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16000000 kB\nMemAvailable: 3500000 kB\n")
    scripts = {
        "systemctl": 'echo "systemctl $*" >> "$SHIM_LOG"\ncase "$2" in cat) cat "$SHIM_UNIT";; is-active) echo active;; show) echo "";; esac\nexit 0',
        "systemd-run": 'echo "systemd-run $*" >> "$SHIM_LOG"; exit 0',
        "docker": 'echo "docker $*" >> "$SHIM_LOG"; exit 0',
        "curl": 'echo "curl $*" >> "$SHIM_LOG"; echo ok; exit 0',
        "ssh": 'echo "2020-01-01 00:00:00"; exit 0',
        "pgrep": 'exit 1',
        "git": 'echo abc1234; exit 0',
        "ss": 'exit 0',
    }
    for name, body in scripts.items():
        p = shim / name
        p.write_text("#!/bin/bash\n" + body + "\n")
        p.chmod(0o755)
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", "SHIM_LOG": str(log), "SHIM_UNIT": str(unit), "BAKEOFF_DIR": str(box),
           "BAKEOFF_LOCK": str(tmp_path / "lock"), "BAKEOFF_MEMINFO": str(meminfo), "BAKEOFF_PY": sys.executable, "HOME": HOME,
           "BAKEOFF_HEALTH_WAIT_S": "10", "BAKEOFF_BLACKOUTS": "none"}
    return shim, log, env


def test_the_wrapper_dry_run_prints_the_plan_and_the_clone_and_changes_nothing(box, tmp_path):
    _shim, log, env = _shims(tmp_path, box)
    r = subprocess.run(["bash", str(REPO / "scripts/perf/zmb/bakeoff_window.sh"), "--dry-run"], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "clone command (generated from `systemctl --user cat llama-server.service`)" in out and "--port 11500" in out and "--parallel 1" in out
    assert "DRY-RUN would run: systemctl --user stop llama-server.service" in out and "PLAN (the measurement phases" in out
    calls = log.read_text() if log.exists() else ""
    for forbidden in ("systemctl --user stop", "systemctl --user start", "systemd-run", "docker compose", "docker exec"):
        assert forbidden not in calls, f"the dry run executed: {forbidden}"
    assert not (box / "WINDOW_OPEN").exists() and not list(box.glob("hindsight-*.env")) and not list(box.glob("run-*"))


def test_the_wrapper_restores_on_the_way_out_when_a_marker_is_left_behind(box, tmp_path):
    """The last safety net: a driver killed hard leaves WINDOW_OPEN; the wrapper's EXIT trap then runs --restore-only."""
    _shim, log, env = _shims(tmp_path, box)
    (box / "WINDOW_OPEN").write_text(json.dumps({"pid": 1}))
    r = subprocess.run(["bash", str(REPO / "scripts/perf/zmb/bakeoff_window.sh"), "--dry-run"], capture_output=True, text=True, env=env, timeout=120)
    assert "restoring the live brain" in r.stderr
    calls = log.read_text()
    assert "systemctl --user start llama-server.service" in calls and "docker compose" in calls and "zoe-bakeoff-gemma.service" in calls
    assert not (box / "WINDOW_OPEN").exists()


def test_restore_only_through_the_wrapper(box, tmp_path):
    _shim, log, env = _shims(tmp_path, box)
    r = subprocess.run(["bash", str(REPO / "scripts/perf/zmb/bakeoff_window.sh"), "--restore-only"], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0 and "live brain healthy" in r.stdout
    assert "systemctl --user start llama-server.service" in log.read_text()


# ── the decision rule: thresholds pinned, verdict logic ──────────────────────

def test_the_rule_thresholds_are_the_pre_registered_ones():
    """docs/research/memory-system-decision-2026-10-05.md section 6.1. If you are editing these after seeing a run, stop."""
    r = gates.RULE
    assert (r["steady_rss_mb"], r["burst_rss_mb"], r["mem_available_floor_mb"]) == (600.0, 900.0, 1200.0)
    assert (r["json_valid_min"], r["json_calls_min"], r["slot_tokens"]) == (0.95, 100, 8192)
    assert (r["recall_p95_ms"], r["layer_lines_max"], r["delete_lines_min"], r["memory_lines_total"], r["seeds_required"]) == (600.0, 1000, 5000, 17923, 3)
    assert r["slot_s_per_turn"] == 2.6


def good_measure(**over):
    m = {"rss": {"steady_mb": 400.0, "burst_mb": 700.0}, "mem_available_floor_mb": 1500.0, "nonloopback_connects": 0,
         "json": {"calls": 104, "valid": 104, "source": "t"}, "prompt_fits": True, "prompt_detail": "x", "recall": {"n": 50, "p95_ms": 300.0},
         "slot_s_per_turn": 0.5, "forgetting": {"t0": {"checked": 2, "resurrected": 0}, "t6": {"checked": 2, "resurrected": 0}},
         "layer_lines": 400, "deletable_lines": 9000, "deletable_basis": "est"}
    m.update(over)
    return m


def seed_run(axes=None, hard=(), skipped=0, ok=True):
    return {"axes": axes if axes is not None else {"extraction": {"pass": 10, "n": 10, "skip": 0, "cells": 10}}, "hard_violations": list(hard),
            "hard_skipped": skipped, "instrument": {"ok": ok}}


def three(**kw):
    return {f"s{i}": seed_run(**kw) for i in range(3)}


def test_a_clean_arm_passes_the_built_gates():
    a = gates.evaluate_arm("H1", three(), good_measure())
    assert a["verdict"] == "PASSES_BUILT_GATES" and set(a["gate_states"].values()) == {"PASS"}


@pytest.mark.parametrize("over,gate", [
    ({"nonloopback_connects": 1}, "G0"), ({"rss": {"steady_mb": 601.0, "burst_mb": 700.0}}, "G0"), ({"rss": {"steady_mb": 400.0, "burst_mb": 901.0}}, "G0"),
    ({"mem_available_floor_mb": 1199.0}, "G0"), ({"json": {"calls": 120, "valid": 113, "source": "t"}}, "G0"), ({"prompt_fits": False}, "G0"),
    ({"recall": {"n": 50, "p95_ms": 601.0}}, "G1"), ({"slot_s_per_turn": 2.7}, "G1"),
    ({"forgetting": {"t0": {"checked": 2, "resurrected": 0}, "t6": {"checked": 2, "resurrected": 1}}}, "G2"),
    ({"layer_lines": 1001}, "G3"), ({"deletable_lines": 4999}, "G3")])
def test_one_miss_on_any_gate_item_makes_the_arm_not_adoptable(over, gate):
    a = gates.evaluate_arm("H1", three(), good_measure(**over))
    assert a["verdict"] == "NOT_ADOPTABLE" and a["gate_states"][gate] == gates.FAIL


def test_a_hard_violation_on_any_seed_fails_g2():
    runs = three()
    runs["s1"]["hard_violations"] = ["A1.digest.home"]
    a = gates.evaluate_arm("H1", runs, good_measure())
    assert a["verdict"] == "NOT_ADOPTABLE" and "A1.digest.home" in a["gates"]["G2"]["hard_cells_zero_violations"]["measured"]


def test_not_measured_is_never_a_pass():
    for missing in ("rss", "nonloopback_connects", "json", "recall", "slot_s_per_turn", "forgetting", "layer_lines", "mem_available_floor_mb", "prompt_fits"):
        m = good_measure()
        m[missing] = None if missing not in ("rss", "json", "recall", "forgetting") else {}
        a = gates.evaluate_arm("H1", three(), m)
        assert a["verdict"] == "INCOMPLETE", missing
    assert gates.evaluate_arm("H1", three(skipped=2), good_measure())["verdict"] == "NOT_ADOPTABLE"       # hard cells that never ran are not a clean bill
    assert gates.evaluate_arm("H1", {"s0": seed_run(), "s1": seed_run()}, good_measure())["verdict"] == "INCOMPLETE"   # two seeds of three
    assert gates.evaluate_arm("H1", three(ok=False), good_measure())["verdict"] == "INCOMPLETE"          # a control that stayed green voids the run
    assert gates.evaluate_arm("H1", {}, good_measure())["verdict"] == "INCOMPLETE"
    assert gates.evaluate_arm("H1", three(), good_measure(json={"calls": 99, "valid": 99, "source": "t"}))["gate_states"]["G0"] == gates.NA   # <100 calls


def axes(p, n, skip=0):
    return {"extraction": {"pass": p, "n": n, "skip": skip, "cells": n}, "abstention": {"pass": p, "n": n, "skip": 0, "cells": n}}


def test_the_winner_clause_beats_z0_beyond_the_wilson_interval_or_ties_to_z0():
    z0 = gates.aggregate_axes({"s": {"axes": axes(60, 90)}})
    strong = {n: gates.evaluate_arm(n, three(axes=axes(30, 30)), good_measure()) for n in ("H1", "H2")}
    d = gates.decide(strong, z0)
    assert d["verdict"] == "ADOPT_CANDIDATE" and d["winner"] == "H1"                               # both pass: H1 (verbatim) over H2, per the rule
    assert d["caveat"].startswith("Advisory") and "NOT FINAL" not in d["caveat"] and "not built" not in d["text"]
    assert gates.WIN_AXES == {"B": "extraction", "C": "temporal", "D": "recall", "E": "abstention"} and "poisoning" in gates.HARD_AXES
    assert not hasattr(gates, "UNBUILT")
    tie = {"H1": gates.evaluate_arm("H1", three(axes=axes(20, 30)), good_measure())}                # 60/90 vs 60/90: inside the interval
    d2 = gates.decide(tie, z0)
    assert d2["verdict"] == "KEEP_Z0" and "tie goes to Z0" in d2["text"] and d2["adoptable"] == ["H1"]
    only_one = {"H1": gates.evaluate_arm("H1", three(axes={"extraction": {"pass": 30, "n": 30, "skip": 0, "cells": 30}, "abstention": {"pass": 20, "n": 30, "skip": 0, "cells": 30}}),
                                         good_measure())}
    assert gates.decide(only_one, z0)["verdict"] == "KEEP_Z0"                                       # needs TWO of B/C/D/E
    worse = {"H1": gates.evaluate_arm("H1", three(axes={"extraction": {"pass": 30, "n": 30, "skip": 0, "cells": 30}, "abstention": {"pass": 5, "n": 30, "skip": 0, "cells": 30}}), good_measure())}
    assert gates.decide(worse, z0)["verdict"] == "KEEP_Z0"


def test_no_adoptable_arm_keeps_z0_and_says_which_are_incomplete():
    bad = {"H0": gates.evaluate_arm("H0", three(hard=["A1.digest.home"]), good_measure()), "H1": gates.evaluate_arm("H1", {}, good_measure())}
    d = gates.decide(bad, {})
    assert d["verdict"] == "KEEP_Z0" and d["winner"] is None and "H1 incomplete" in d["text"]


def test_h0_can_never_win_it_is_the_measurement_of_what_is_native():
    z0 = gates.aggregate_axes({"s": {"axes": axes(10, 90)}})
    d = gates.decide({"H0": gates.evaluate_arm("H0", three(axes=axes(30, 30)), good_measure())}, z0)
    assert d["verdict"] == "KEEP_Z0" and d["winner"] is None


# ── the measurement end to end over the fake Hindsight ───────────────────────

SUBSET = ("A1.digest.home", "A1.mcp.pet", "A1.digest.name", "A2.incident.home", "A5.digest.home", "F1.sweep_keeps_the_rest", "F2.late_writer.digest",
          "F3.after_tombstone_ttl", "H1.digest", "H2.edit.digest", "E1.question_is_not_a_fact", "G3.consent.guest", "G3.consent.minor", "B2.pet_not_child")


def e2e_window(box, tmp_path, monkeypatch, *, box_min=None, egress=True):
    cells = [c for c in spec.load_cells() if c.id in SUBSET]
    assert len(cells) == len(SUBSET)
    monkeypatch.setattr(spec, "load_cells", lambda directory=None: cells)
    monkeypatch.setattr(measure, "VALIDITY_CALLS", 104)
    monkeypatch.setattr(measure, "LATENCY_FACTS", 4)
    if box_min:
        monkeypatch.setattr(measure, "BOX_MIN", box_min)
    host = FakeHost(box)
    host.log = lambda _m: None
    w = make_window(box, host, docs_dir=tmp_path / "docs", sample_s=0.01)
    fake = FakeHindsight()
    w.fake = fake
    w.arm_factory = lambda v, **kw: HindsightArm(v, transport=fake, settle_poll_s=0, **kw)
    w.client_factory = lambda: HindsightClient("http://127.0.0.1:18888", transport=fake)
    if egress:
        (box / "egress-t1.log").write_text("12:00:00 pid=1 ok connect ('127.0.0.1', 5432)\n12:00:01 pid=1 ok connect ('127.0.0.1', 11500)\n")
    return w, host


def test_measure_end_to_end_writes_the_artifact_and_the_verdict_and_never_adopts_incompletely(box, tmp_path, monkeypatch):
    w, host = e2e_window(box, tmp_path, monkeypatch)
    payload = measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    md = Path(art["docs_path"]).read_text()
    assert Path(art["docs_path"]).name == "bakeoff-run-t1.md" and art["run_id"] == "t1"
    arms = art["arms"]
    assert arms["H1"]["verdict"] == "PASSES_BUILT_GATES" and arms["H1"]["seeds_done"] == 3, arms["H1"]["gates"]
    assert arms["H0"]["verdict"] == "NOT_ADOPTABLE"                                                  # no Zoe layer: hard violations + a resurrection
    assert art["decision"]["verdict"] in ("KEEP_Z0", "ADOPT_CANDIDATE") and art["decision"]["caveat"].startswith("Advisory")
    assert arms["H0"]["gates"]["G2"]["hard_cells_zero_violations"]["state"] == gates.FAIL
    assert arms["H0"]["gates"]["G2"]["forget_t+6min_no_resurrection"]["state"] == gates.FAIL          # the replay resurrects her natively
    assert arms["H1"]["gates"]["G2"]["forget_t+6min_no_resurrection"]["state"] == gates.PASS
    assert arms["H1"]["gates"]["G0"]["extraction_json_validity"]["measured"].startswith("104/104")
    assert arms["H1"]["gates"]["G1"]["slot_seconds_per_turn"]["measured"] == "0.083 s"                  # (10 s of /metrics delta) / (12 retains x 10 turns)
    assert arms["H1"]["gates"]["G0"]["steady_rss"]["measured"] == "250 MB"                           # 200 MB PSS + 50 MB container, sampled
    assert art["measure"]["H1"]["arm_controls"] == "3/3"                                              # the adapter's own negative controls went red
    assert "| G0 |" in md and "| G2 |" in md and "Wilson" in md and "## H1:" in md and "## H0:" in md and art["decision"]["verdict"] in md
    assert payload["run_id"] == "t1" and "forgetting" in art["measure"]["H1"]
    world = make_world("zmb-v1")
    assert artifact.household_strings_in(art, world.all_strings()) == [] and artifact.household_strings_in({"md": md}, world.all_strings()) == []
    # the measurement never touches a service: only reads (show / curl / docker stats / git)
    assert not any(c.startswith(("systemctl --user stop", "systemctl --user start", "systemd-run")) for c in host.mutating_cmds() if "llama-server" in c)


def test_without_an_egress_log_zero_connects_is_not_claimed(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch, egress=False)
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    g0 = art["arms"]["H1"]["gates"]["G0"]["zero_nonloopback_connects"]
    assert g0["state"] == gates.NA and art["arms"]["H1"]["verdict"] == "INCOMPLETE"                    # a hook that wrote nothing proves nothing


def test_an_empty_egress_log_is_not_zero_connects_either(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch, egress=False)
    (box / "egress-t1.log").write_text("")
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    assert art["arms"]["H1"]["gates"]["G0"]["zero_nonloopback_connects"]["state"] == gates.NA


def test_a_violation_in_the_egress_log_fails_g0(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch)
    (box / "egress-t1.log").write_text("12:00:00 pid=1 VIOLATION connect ('93.184.216.34', 443)\n")
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    assert art["arms"]["H1"]["gates"]["G0"]["zero_nonloopback_connects"]["state"] == gates.FAIL and art["arms"]["H1"]["verdict"] == "NOT_ADOPTABLE"


def test_a_time_box_that_is_too_small_skips_cells_and_the_arm_is_not_adoptable(box, tmp_path, monkeypatch):
    w, host = e2e_window(box, tmp_path, monkeypatch)
    cells = [c for c in spec.load_cells()]
    by_id = {c.id: c for c in cells}
    ctx = measure.Ctx(w, None)
    ctx.arm_controls_ok = True
    ctx.measure["H1"]["arm_controls"] = "3/3"
    real, ticks = host.mono, {"n": 0}

    def ticking():
        ticks["n"] += 1
        return real() + ticks["n"] * 1.0                       # a second per look at the clock: a 12 s box runs out after a few cells
    host.mono = ticking
    measure.run_arm_seed(ctx, "H1", "zmb-v1", 12.0, cells, by_id, lambda seed: {"ok": True, "lab_controls_red": "9/9"})
    run = ctx.seed_runs["H1"]["zmb-v1"]
    assert 0 < run["cells_ran"] < run["cells_selected"]
    assert any(c["verdict"] == "SKIP" and "time box" in c["reason"] for c in run["cells"]) and run["hard_skipped"] > 0
    assert gates.evaluate_arm("H1", {s: run for s in ("a", "b", "c")}, good_measure())["verdict"] == "NOT_ADOPTABLE"   # skipped hard cells are no clean bill


def test_an_unreachable_hindsight_aborts_the_measurement_and_still_reports(box, tmp_path, monkeypatch):
    w, host = e2e_window(box, tmp_path, monkeypatch)
    w.fake.down = True
    with pytest.raises(bakeoff.Aborted):
        measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    assert art["meta"]["aborted"] and all(a["verdict"] == "INCOMPLETE" for a in art["arms"].values())
    assert art["decision"]["verdict"] == "KEEP_Z0"


def test_dry_plan_prints_the_schedule(box):
    host = FakeHost(box)
    w = make_window(box, host, dry=True)
    measure.dry_plan(w)
    out = "\n".join(w.logs)
    assert "PLAN" in out and "H1 seed 1" in out and "forgetting probes" in out and "hard cap 90" in out and "RESTORE (always" in out



# ── egress: counted per window and per phase, "0 over N observed", and a missing log is never a zero ───────────────

import datetime as _dt  # noqa: E402


def _egress_lines(ref, spec):
    """``spec`` = [(seconds from ref, 'ok' or 'VIOLATION', what)] -> hook-log text (time of day only, like the real hook)."""
    out = ["{} pid=7 ok hook-loaded uvloop=blocked".format(_dt.datetime.fromtimestamp(ref - 8).strftime("%H:%M:%S"))]
    for off, tag, what in spec:
        out.append("{} pid=7 {} {}".format(_dt.datetime.fromtimestamp(ref + off).strftime("%H:%M:%S"), tag, what))
    return "\n".join(out) + "\n"


def _marks(ref):
    return [(ref, "lab"), (ref + 60, "H1:zmb-v1"), (ref + 120, "H2:latency"), (ref + 180, "validity:concise")]


GOOD = [(-5, "ok", "connect ('127.0.0.1', 55432)"), (10, "ok", "getaddrinfo localhost"), (70, "ok", "connect ('127.0.0.1', 11500)"),
        (80, "ok", "connect ('127.0.0.1', 55432)"), (130, "ok", "connect ('::1', 11500, 0, 0)"), (200, "ok", "connect unix:/run/x.sock")]


def test_a_clean_hook_log_says_zero_over_n_observed_and_splits_them_by_phase(tmp_path):
    ref = 1_800_000_000.0
    log = tmp_path / "egress-t1.log"
    log.write_text(_egress_lines(ref, GOOD))
    got = measure.egress_summary(log, _marks(ref), ref, 0)
    assert got["connects"] == 0 and got["observed"] == 6
    assert got["detail"].startswith("0 non-loopback connects over 6 observed (")
    assert got["by_arm"] == {"setup": {"observed": 1, "violations": 0}, "shared": {"observed": 2, "violations": 0},
                             "H1": {"observed": 2, "violations": 0}, "H2": {"observed": 1, "violations": 0}}
    g0 = gates.gate_g0({"nonloopback_connects": got["connects"], "egress": got})["zero_nonloopback_connects"]
    assert g0["state"] == gates.PASS and g0["measured"].startswith("0 non-loopback connects over 6 observed")


def test_a_violation_is_counted_against_the_phase_that_made_it_and_the_gate_fails(tmp_path):
    ref = 1_800_000_000.0
    log = tmp_path / "egress-t1.log"
    log.write_text(_egress_lines(ref, GOOD + [(135, "VIOLATION", "getaddrinfo control.example.invalid")]))
    got = measure.egress_summary(log, _marks(ref), ref, 1)                 # +1: the ss sampler also saw one non-loopback peer
    assert got["connects"] == 2 and got["by_arm"]["H2"] == {"observed": 2, "violations": 1}
    assert "H2 2 (1 non-loopback)" in got["detail"] and "control.example.invalid" in got["detail"] and "ss sampler 1 peers" in got["detail"]
    g0 = gates.gate_g0({"nonloopback_connects": got["connects"], "egress": got})["zero_nonloopback_connects"]
    assert g0["state"] == gates.FAIL and g0["measured"].startswith("2 non-loopback connects over 7 observed")


def test_a_missing_or_blind_hook_log_is_not_measured_never_a_zero(tmp_path):
    ref = 1_800_000_000.0
    missing = measure.egress_summary(tmp_path / "nope.log", [], ref, 0)
    empty = tmp_path / "empty.log"
    empty.write_text("")
    loaded_only = tmp_path / "loaded.log"
    loaded_only.write_text("12:00:00 pid=7 ok hook-loaded uvloop=blocked\n")
    for got in (missing, measure.egress_summary(empty, [], ref, 0), measure.egress_summary(loaded_only, [], ref, 0)):
        assert got["connects"] is None and got["detail"].startswith("not measured")
        g0 = gates.gate_g0({"nonloopback_connects": got["connects"], "egress": got})["zero_nonloopback_connects"]
        assert g0["state"] == gates.NA                                    # NA, and an arm with an NA item is INCOMPLETE, never a pass
    assert measure.count_violations(tmp_path / "nope.log") is None and measure.count_violations(loaded_only) is None


def test_phases_are_attributed_across_midnight(tmp_path):
    ref = _dt.datetime(2026, 10, 6, 23, 59, 30).timestamp()
    log = tmp_path / "egress-t1.log"
    log.write_text(_egress_lines(ref, [(5, "ok", "connect ('127.0.0.1', 1)"), (50, "ok", "connect ('127.0.0.1', 2)"), (125, "ok", "connect ('127.0.0.1', 3)")]))
    got = measure.egress_summary(log, [(ref, "lab"), (ref + 30, "H1:zmb-v1"), (ref + 100, "H0:latency")], ref, 0)
    assert got["by_arm"] == {"shared": {"observed": 1, "violations": 0}, "H1": {"observed": 1, "violations": 0}, "H0": {"observed": 1, "violations": 0}}


def test_the_hook_in_the_repo_logs_connects_and_blocks_uvloop(tmp_path):
    """The hook is run for real in a subprocess: it must be live (hook-loaded), see a loopback connect and a non-loopback lookup, and make
    ``import uvloop`` fail (run 1's hook was blind because uvloop connects in C, bypassing the socket audit event)."""
    log = tmp_path / "hook.log"
    code = (
        "import socket, sys\n"
        "try:\n    socket.create_connection(('127.0.0.1', 1), timeout=1)\nexcept OSError:\n    pass\n"
        "try:\n    socket.getaddrinfo('control.example.invalid', 80)\nexcept OSError:\n    pass\n"
        "print('uvloop-entry', sys.modules.get('uvloop', 'absent'))\n")
    env = {"PATH": os.environ.get("PATH", ""), "EGRESS_AUDIT_LOG": str(log), "PYTHONPATH": str(bakeoff.EGRESS_AUDIT_DIR)}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and "uvloop-entry None" in r.stdout, r.stdout + r.stderr     # None in sys.modules: the import raises ImportError
    parsed = measure.parse_egress(log.read_text())
    assert parsed and parsed["hook_loaded"] and parsed["observed"] >= 2
    assert [e["what"] for e in parsed["entries"] if e["violation"]] == ["control.example.invalid"]
    assert any(e["kind"] == "connect" and not e["violation"] for e in parsed["entries"])


def test_a_dead_egress_hook_aborts_early_and_puts_the_brain_back(box):
    host = FakeHost(box, hook_live=False)
    w = make_window(box, host, measure_fn=lambda win: {"ok": True})
    assert w.run() == bakeoff.EXIT_ABORTED
    assert any("egress hook is not live" in m for m in w.logs) and restored(host)


def test_a_live_egress_hook_is_logged_and_the_window_goes_on(box):
    host = FakeHost(box)
    w = make_window(box, host, measure_fn=lambda win: {"ok": True})
    assert w.run() == bakeoff.EXIT_OK and any("egress hook live" in m for m in w.logs)


# ── the phase budget: three H1 seeds inside the cap, H2 and H0 one seed each ──────────────────────────────────────

def test_the_budget_fits_three_h1_seeds_inside_the_hard_cap(box):
    cfg = bakeoff.Cfg(bakeoff_dir=box)
    b = measure.plan_budget(cfg)
    assert b.seeds == {"H1": 3, "H2": 1, "H0": 1} and b.arms == ("H1", "H2", "H0")
    assert b.total_min() <= b.avail_min <= cfg.cap_min - cfg.reserve_min - measure.TAIL_MIN              # the hard cap is kept, with the tail
    assert b.cells_in_box("H1") >= b.runnable > 100                     # one H1 seed box holds every runnable store cell at run 1's rate
    assert b.box_min["H2"] >= 2.0 and b.box_min["H0"] >= 1.0 and b.store_cells > b.runnable
    assert 3 * b.box_min["H1"] > b.box_min["H2"] + b.box_min["H0"]      # H1 is the preferred arm: the biggest share of the time


def test_a_lower_arm_only_gets_what_is_left_behind_the_work_queued_for_it(box):
    b = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box))
    assert b.seed_box_s("H1", 3600.0) == b.box_min["H1"] * 60.0 and b.seed_box_s("H1", 120.0) == 120.0     # H1: its ceiling, never over the time left
    assert b.fixed_min("H0") == 0.0                     # H0's latency / slot are "only if time remains": its cells outrank its timings
    queued_h2 = (b.fixed_min("H2") + measure.PHASE_MIN["validity"]["concise"] + 2.0) * 60.0       # its own phases + H0's 2 min seed box
    assert b.seed_box_s("H2", queued_h2 - 1.0) == 0.0                                  # nothing left behind its own queue: no box, no starving H0
    assert 0 < b.seed_box_s("H2", 3000.0) <= 2 * b.box_min["H2"] * 60.0
    assert b.seed_box_s("H2", 6000.0) == 2 * b.box_min["H2"] * 60.0                    # an early H1 hands its slack on, up to twice the plan
    assert b.seed_box_s("H0", 100.0 * 60) <= 2 * b.box_min["H0"] * 60.0


def test_h1_runs_first_and_complete_then_h2_then_h0(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch)
    calls, real = [], measure.run_arm_seed
    monkeypatch.setattr(measure, "run_arm_seed", lambda ctx, v, seed, *a, **k: (calls.append((v, seed)), real(ctx, v, seed, *a, **k))[1])
    measure.measure(w)
    assert [v for v, _s in calls] == ["H1", "H1", "H1", "H2", "H0"]
    assert len({s for v, s in calls if v == "H1"}) == 3 and {s for v, s in calls if v != "H1"} == {calls[0][1]}


def test_the_dry_plan_prints_the_per_arm_cell_budget_and_the_seed_counts(box):
    host = FakeHost(box)
    w = make_window(box, host, dry=True)
    measure.dry_plan(w)
    line = next(m for m in w.logs if m.startswith("per-arm cell budget"))
    assert "H1 3 seeds x " in line and "H2 1 seed x " in line and "H0 1 seed x " in line and "all 3 seeds complete inside the cap" in line
    assert "SKIP by capability" in line
    out = "\n".join(w.logs)
    assert out.index("H1 seed 1") < out.index("H1 seed 3") < out.index("H2 seed 1") < out.index("H0 seed 1")      # execution order: H1 first and complete
    assert "H2 seed 2" not in out and "H0 seed 2" not in out and "slack +" in out


# ── why hard cells did not run, the winner-clause axes, and the top of the record ──────────────────────────────────

def test_skips_are_split_into_time_box_and_capability_and_the_gate_says_which():
    rows = [{"verdict": "SKIP", "reason": "time box reached"}, {"verdict": "SKIP", "reason": "time box reached"},
            {"verdict": "SKIP", "reason": "arm H1 lacks capability: disk"}, {"verdict": "SKIP", "reason": "arm H1 lacks capability: edges"},
            {"verdict": "SKIP", "reason": "cannot reach Hindsight at x"}]
    why = measure.skip_breakdown(rows)
    assert why == {"time box": 2, "capability: disk": 1, "capability: edges": 1, "unreachable": 1}
    g2 = gates.gate_g2({"zmb-v1": {"hard_skipped": 5, "hard_skipped_why": why, "hard_violations": []}}, {})["hard_cells_all_ran"]
    assert g2["state"] == gates.FAIL and "5 skipped (2 time box, 1 capability: disk, 1 capability: edges, 1 unreachable)" in g2["measured"]
    assert gates.gate_g2({"s": {"hard_skipped": 0, "hard_violations": []}}, {})["hard_cells_all_ran"]["state"] == gates.PASS


def test_the_winner_clause_reads_the_temporal_and_recall_axes_the_axes_pr_built():
    assert gates.WIN_AXES == {"B": "extraction", "C": "temporal", "D": "recall", "E": "abstention"}
    def ax(p, n):
        lo, hi = gates.wilson(p, n)
        return {"pass": p, "n": n, "wilson95": [round(lo, 4), round(hi, 4)]}
    z0 = {"extraction": ax(20, 20), "temporal": ax(10, 20), "recall": ax(4, 20), "abstention": ax(10, 10)}
    arm = {"extraction": ax(20, 20), "temporal": ax(20, 20), "recall": ax(20, 20), "abstention": ax(10, 10)}
    c = gates.compare_axes(arm, z0)
    assert c["C"]["axis"] == "temporal" and c["C"]["beats"] and c["D"]["axis"] == "recall" and c["D"]["beats"]       # two beats: the rule's win
    assert not c["B"]["beats"] and not c["E"]["beats"] and not any(v["worse"] for v in c.values())
    assert gates.compare_axes({}, z0)["C"]["note"] == "no data"                                                       # an arm that ran none: no data, not a tie


def test_the_run_record_opens_with_the_verdict_the_clause_per_axis_and_the_plain_answer(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch)
    measure.measure(w)
    md = Path(json.loads((box / "run-t1.json").read_text())["docs_path"]).read_text()
    head = md.split("## Run")[0]
    assert "**Verdict by the pre-registered rule:" in head and "### Winner clause per axis" in head and "### Is it better than ours?" in head
    assert "| B | extraction |" in head and "| C | temporal |" in head and "| D | recall |" in head and "| E | abstention |" in head
    assert "A tie goes to Z0" in head and "Honest caveats:" in head
    assert head.index("Verdict by") < head.index("Winner clause per axis") < head.index("Is it better than ours?")


def test_the_plain_answer_says_yes_only_when_the_rule_says_so():
    ax = {"extraction": {"pass": 18, "n": 18, "wilson95": [0.82, 1.0], "skipped": 0}}
    arm = {"verdict": "PASSES_BUILT_GATES", "seeds_done": 3, "axes": ax, "gates": {"G0": {"x": {"state": gates.PASS}}}}
    won = {"verdict": "ADOPT_CANDIDATE", "winner": "H1", "compare": {"H1": {"B": {"axis": "extraction", "built": True, "beats": True, "worse": False},
                                                                        "D": {"axis": "recall", "built": True, "beats": True, "worse": False}}}}
    assert gates._better_line({"H1": arm}, won).startswith("Yes, on what was measured: H1 passes every built gate")
    tie = {"verdict": "KEEP_Z0", "compare": {"H1": {"B": {"axis": "extraction", "built": True, "beats": False, "worse": False, "arm": [18, 18, [0.82, 1.0]]},
                                                  "C": {"axis": "temporal", "built": True, "beats": False, "worse": False, "note": "no data"}}}}
    line = gates._better_line({"H1": {**arm, "verdict": "NOT_ADOPTABLE", "gates": {"G2": {"hard_cells_all_ran": {"state": gates.FAIL}}}}}, tie)
    assert line.startswith("No evidence that H1 is better than Z0") and "ties Z0 on B extraction" in line and "no data for C temporal" in line
    assert "A tie goes to Z0" in line and "G2 hard_cells_all_ran" in line
