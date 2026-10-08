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
    env = bakeoff.hindsight_env(ENV_EXAMPLE, bakeoff.Cfg(bakeoff_dir=Path("/b"), lean=False), "r1")          # run 1 / 2's exact settings (the lean ones are tested below)
    kv = dict(l.split("=", 1) for l in env.splitlines() if "=" in l and not l.startswith("#"))
    assert kv["HINDSIGHT_API_LLM_TRACE_ENABLED"] == "true" and kv["HINDSIGHT_API_EMBEDDINGS_OPENAI_BATCH_SIZE"] == "8"
    assert kv["HINDSIGHT_API_LLM_BASE_URL"] == "http://127.0.0.1:11500/v1" and kv["HINDSIGHT_API_DATABASE_URL"].endswith("127.0.0.1:55432/hindsight")
    assert kv["EGRESS_AUDIT_LOG"] == "/b/egress-r1.log" and kv["PYTHONPATH"] == str(bakeoff.EGRESS_AUDIT_DIR)       # the hook ships in the repo
    assert (bakeoff.EGRESS_AUDIT_DIR / "sitecustomize.py").is_file() and kv["PYTHONDONTWRITEBYTECODE"] == "1"
    with pytest.raises(bakeoff.Refused, match="non-loopback"):
        bakeoff.hindsight_env(ENV_EXAMPLE + "HINDSIGHT_API_WEBHOOK_URL=https://hooks.example.com/x\n", bakeoff.Cfg(bakeoff_dir=Path("/b")), "r1")


def test_the_lean_settings_are_on_by_default_chain_the_egress_hook_and_BAKEOFF_LEAN_0_restores_run_2s_environment():
    """RAM lab 2026-10-06: migration isolation, a 1..2 connection pool, the import trim, docstring-free bytecode and one BLAS thread on the server (measured -44 to -64 MB). The import trim
    must NEVER switch the G0 egress instrument off: it chains the audit hook, and both directories ship a sitecustomize."""
    lean = bakeoff.hindsight_env(ENV_EXAMPLE, bakeoff.Cfg(bakeoff_dir=Path("/b")), "r1")
    kv = dict(l.split("=", 1) for l in lean.splitlines() if "=" in l and not l.startswith("#"))
    assert kv["HINDSIGHT_API_MIGRATION_ISOLATION"] == "true" and kv["HINDSIGHT_API_DB_POOL_MIN_SIZE"] == "1" and kv["HINDSIGHT_API_DB_POOL_MAX_SIZE"] == "2"
    assert kv["PYTHONPATH"] == f"{bakeoff.LEAN_IMPORTS_DIR}:{bakeoff.EGRESS_AUDIT_DIR}" and kv["PYTHONOPTIMIZE"] == "2" and "fastmcp" in kv["ZMB_LEAN_STUBS"]
    assert (bakeoff.LEAN_IMPORTS_DIR / "sitecustomize.py").is_file() and (bakeoff.EGRESS_AUDIT_DIR / "sitecustomize.py").is_file()
    assert "egress_audit" in (bakeoff.LEAN_IMPORTS_DIR / "sitecustomize.py").read_text()                               # it chains the hook
    assert "HINDSIGHT_API_WORKER_ENABLED" not in kv and "LOOP_WATCHDOG" not in lean                                    # the in-process worker (H2 / H0 consolidation) stays ON
    off = bakeoff.hindsight_env(ENV_EXAMPLE, bakeoff.Cfg(bakeoff_dir=Path("/b"), lean=False), "r1")
    okv = dict(l.split("=", 1) for l in off.splitlines() if "=" in l and not l.startswith("#"))
    assert "ZMB_LEAN_STUBS" not in okv and "HINDSIGHT_API_MIGRATION_ISOLATION" not in okv and okv["PYTHONPATH"] == str(bakeoff.EGRESS_AUDIT_DIR)
    import os
    import subprocess
    code = f"import sys; sys.path.insert(0, {str(Path(bakeoff.__file__).parents[1])!r}); from zmb import bakeoff; print(bakeoff.Cfg().lean, bakeoff.Cfg().hm_shared_embedder)"
    for val, want in (("0", "False True"), ("", "True True")):
        env = {k: v for k, v in os.environ.items() if k != "BAKEOFF_LEAN"}
        if val:
            env["BAKEOFF_LEAN"] = val
        assert subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60).stdout.strip() == want


def test_the_shim_unit_gets_the_measured_settings_and_the_model_is_not_switched_by_default(box):
    host = FakeHost(box)
    w = make_window(box, host, measure_fn=lambda win: {"ok": 1})
    assert w.run() == bakeoff.EXIT_OK
    shim = next(c for c in host.joined() if "--unit=zoe-bakeoff-embed" in c)
    assert "--setenv=ZMB_ORT_OPT=all" in shim and "--setenv=MALLOC_ARENA_MAX=1" in shim and "--setenv=ORT_DISABLE_TELEMETRY=1" in shim
    assert "--model" not in shim                                            # run 1 / 2's embedder (bge-small) stays: a changed model would move every H arm's recall
    host2 = FakeHost(box)
    w2 = make_window(box, host2, measure_fn=lambda win: {"ok": 1}, lean=False, shim_model="minilm")
    assert w2.run() == bakeoff.EXIT_OK
    shim2 = next(c for c in host2.joined() if "--unit=zoe-bakeoff-embed" in c)
    assert "ZMB_ORT_OPT" not in shim2 and "MALLOC_ARENA_MAX" not in shim2 and shim2.endswith("--model minilm")


def test_the_hm_driver_is_pointed_at_the_windows_shim_so_one_embedder_serves_both_tiers(box):
    import types
    seen = {}

    class H(FakeHost):
        def run(self, argv, timeout=60.0, mutating=True, env=None):
            if argv[:1] == ["bash"] and "mp_run.sh" in " ".join(argv):
                seen["env"] = env or {}
            return super().run(argv, timeout, mutating, env)
    for shared in (True, False):
        seen.clear()
        host = H(box)
        w = make_window(box, host, measure_fn=lambda win: {"ok": 1}, hm_shared_embedder=shared)
        ctx = types.SimpleNamespace(cfg=w.cfg, host=host, win=w)
        measure.real_hm_runner(ctx, "zmb-v1", 60.0)
        assert ("ZMB_HM_EMBEDDER_URL" in seen["env"]) is shared
        if shared:
            assert seen["env"]["ZMB_HM_EMBEDDER_URL"] == "http://127.0.0.1:11501" and seen["env"]["PYTHONMALLOC"] == "malloc"      # the heap-scrub settings stay


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


@pytest.fixture(autouse=True)
def _private_harness_lock(tmp_path, monkeypatch):
    """The window refuses while ``/tmp/zoe-voice-harness.lock`` is held: a real probe on the dev box (or another session's test lane) must not turn these tests red."""
    monkeypatch.setattr(bakeoff.Window, "HARNESS_LOCK", str(tmp_path / "harness.lock"))


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


#: the arm list before MPA / HMA joined the default: the older planner tests pin the shapes of THIS plan (a changed default list is why they name it)
HM_ERA = ("H1", "H2", "HM", "H0")


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
    assert r"^bash .*/land_voice_pr\.sh" in patterns and set(patterns) == {p for _l, p in bakeoff.Window.BUSY_PATTERNS}       # the ANCHORED patterns, as the land script


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



# ── what counts as "the brain is busy": anchored, and complete (first contact 2026-10-06) ──────────────────────

def _spawn(tmp_path, name, *, interp=True):
    """A real process whose command line is ``python <tmp>/<name>`` (or, for a decoy, only MENTIONS the name)."""
    import subprocess
    script = tmp_path / name
    script.write_text("import time\ntime.sleep(60)\n")
    argv = [sys.executable, str(script)] if interp else ["bash", "-c", "sleep 60", f"watching-{name}"]
    return subprocess.Popen(argv)


@pytest.mark.parametrize("name,label", [("samantha_bar.py", "samantha bar"), ("samantha_bar_conv.py", "samantha bar"),
                                        ("samantha_day_sim.py", "samantha bar"), ("voice_regression_probe.py", "voice regression probe")])
def test_a_running_bar_or_probe_is_seen_by_its_interpreter_and_a_mere_mention_is_not(box, tmp_path, name, label):
    w = make_window(box, bakeoff.Host(lambda _m: None))
    assert w.landing_running() == ""
    decoy = _spawn(tmp_path, name, interp=False)                 # an editor / tail / shell that only names the script
    try:
        assert w.landing_running() == "", "an unanchored pgrep would have waited on a process that is not the bar"
        real = _spawn(tmp_path, name)
        try:
            assert label in w.landing_running()
        finally:
            real.kill()
            real.wait()
    finally:
        decoy.kill()
        decoy.wait()


def test_the_voice_harness_lock_held_means_a_probe_is_using_the_brain(box, tmp_path, monkeypatch):
    lock = tmp_path / "harness.lock"
    monkeypatch.setattr(bakeoff.Window, "HARNESS_LOCK", str(lock))
    w = make_window(box, bakeoff.Host(lambda _m: None))
    assert w.landing_running() == ""                              # no file: nobody ever took it
    fd = os.open(str(lock), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert "voice harness" in w.landing_running()
    finally:
        os.close(fd)
    assert w.landing_running() == ""


# ── the test hook: a window against the LIVE brain (default OFF) ─────────────────────────────────────────────

def test_the_skip_brain_stop_hook_is_off_by_default_and_documented():
    assert bakeoff.Cfg().skip_brain_stop is False and bakeoff.Cfg().smoke_cells == 0
    assert "BAKEOFF_SKIP_BRAIN_STOP" in Path(bakeoff.__file__).read_text() and "BAKEOFF_SKIP_BRAIN_STOP" in (REPO / "docs/knowledge/bakeoff-howto.md").read_text()


def test_with_the_hook_the_live_brain_is_never_stopped_or_started_no_clone_runs_and_hindsight_talks_to_the_live_port(box):
    host = FakeHost(box)
    w = make_window(box, host, measure_fn=lambda win: {"ok": 1}, skip_brain_stop=True)
    assert w.run() == bakeoff.EXIT_OK
    j = host.joined()
    assert not any("stop llama-server" in c or "start llama-server" in c for c in j)
    assert not any("zoe-bakeoff-gemma" in c for c in j)
    assert any("--unit=zoe-bakeoff-hindsight" in c for c in j) and any("--unit=zoe-bakeoff-embed" in c for c in j)
    env = (box / "hindsight-t1.env").read_text()
    assert "HINDSIGHT_API_LLM_BASE_URL=http://127.0.0.1:11434/v1" in env
    assert any("SKIPPED" in l for l in w.logs) and any("never stopped" in l for l in w.logs)
    assert any("compose" in c and c.endswith("down") for c in j) and any("stop zoe-bakeoff-hindsight.service" in c for c in j)
    assert not (box / "WINDOW_OPEN").exists() and w.restore_status.startswith("live brain healthy")


def test_without_the_hook_the_brain_is_stopped_and_a_clone_runs_on_its_own_port(box):
    host = FakeHost(box)
    w = make_window(box, host, measure_fn=lambda win: {})
    assert w.run() == bakeoff.EXIT_OK and any("stop llama-server" in c for c in host.joined()) and any("--unit=zoe-bakeoff-gemma" in c for c in host.joined())
    assert "HINDSIGHT_API_LLM_BASE_URL=http://127.0.0.1:11500/v1" in (box / "hindsight-t1.env").read_text()


def test_with_the_hook_a_voice_turn_that_starts_during_the_window_ends_it_and_the_restore_runs(box):
    host = FakeHost(box)
    state = {"n": 0}

    def measure_fn(win):
        host.t += 120.0                                          # two minutes into the window ...
        host.panel_busy_until = host.t + 599.0 - 100.0           # ... the panel wakes: its last turn was ~100 s ago, newer than the window
        win.guard()
        state["n"] += 1
        return {}
    w = make_window(box, host, measure_fn=measure_fn, skip_brain_stop=True)
    host.panel_busy_until = 0
    assert w.run() == bakeoff.EXIT_ABORTED and state["n"] == 0
    assert any("voice turn started" in l for l in w.logs) and any("stop zoe-bakeoff-hindsight.service" in c for c in host.joined())
    assert not any("stop llama-server" in c for c in host.joined())


def test_a_smoke_window_against_the_live_brain_runs_one_seed_marks_the_report_and_never_touches_the_brain(box, tmp_path, monkeypatch):
    w, host = e2e_window(box, tmp_path, monkeypatch, extra=("F5.forgotten_text_not_on_disk",), pg=True)
    w.cfg.skip_brain_stop, w.cfg.smoke_cells = True, 6
    w.opened = True
    monkeypatch.setattr(measure, "FORGET_WAIT_S", 0.0)
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    md = Path(art["docs_path"]).read_text()
    assert art["arms"]["H1"]["seeds_done"] == 1 and all(r["cells_selected"] == 6 for r in art["seed_runs"]["H1"].values())
    assert "TEST-HOOK RUN, NOT A BAKE-OFF RESULT" in md and "BAKEOFF_SKIP_BRAIN_STOP=1" in art["meta"]["test_hook"] and "BAKEOFF_SMOKE_CELLS=6" in art["meta"]["test_hook"]
    assert art["arms"]["H1"]["gates"]["G0"]["extraction_json_validity"]["state"] == gates.NA        # 10 calls: below the 100-call minimum, so not a measurement
    assert not any(c.startswith(("systemctl --user stop llama", "systemctl --user start llama")) for c in host.joined())


def test_a_smoke_run_picks_cells_across_the_axes_and_always_includes_the_physical_erase_cell():
    store = [c for c in spec.load_cells() if c.tier == "store"]
    chosen = measure.pick_smoke(store, 10)
    ids = [c.id for c in chosen]
    assert len(ids) == 10 and len(set(ids)) == 10 and "F5.forgotten_text_not_on_disk" in ids
    assert len({i[0] for i in ids}) >= 8, ids                    # authority, extraction, temporal, recall, abstention, forgetting, ...


def test_the_validity_count_ignores_the_trace_rows_of_an_earlier_windows_bank_of_the_same_name(box, tmp_path, monkeypatch):
    """A bank delete does not touch ``llm_requests``: a finished run's rows for ``zmb-probe-verbatim`` are still there when the next window counts."""
    w, _host = e2e_window(box, tmp_path, monkeypatch)
    w.fake.llm_requests.extend({"status": "error", "operation": "retain", "started_at": "2020-01-01T00:00:00+00:00"} for _ in range(10))
    ctx = measure.Ctx(w, None)
    ctx.measure = {v: {} for v in ("H0", "H1", "H2")}
    measure.phase_validity(ctx, "verbatim", ("H1",), 600.0)
    assert ctx.measure["H1"]["json"] == {"calls": 104, "valid": 104, "source": ctx.measure["H1"]["json"]["source"]}
    assert "104/104 success" in ctx.measure["H1"]["json"]["source"]


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


def ax_c(p, n):
    return {"pass": p, "n": n, "skip": 0, "cells": n, "items": {"pass": 0, "n": 0}, "failing": []}


def ax_i(p, n, failing=()):
    """A capability axis measured in ITEMS (20 sentences, 20 questions, the observations judged): one cell, n items."""
    return {"pass": 1 if p == n else 0, "n": 1, "skip": 0, "cells": 1, "items": {"pass": p, "n": n}, "failing": list(failing)}


def cap(temporal=(10, 20), recall=(10, 20), exact=(10, 20), reflection=None, hops=None, **extra):
    out = {"temporal": ax_c(*temporal), "recall": ax_c(*recall), "exact_words": ax_i(*exact), **extra}
    if reflection:
        out["reflection"] = ax_i(*reflection)
    if hops:
        out["multi_hop"] = ax_i(*hops)
    return out


def test_the_winner_clause_is_decided_on_the_capability_axes_and_a_tie_goes_to_the_maintained_candidate():
    """Owner direction 2026-10-07: G0-G3 are floors; the contest is exact words / reflection / long-range recall / protocol + temporal and recall AT DISTANCE.
    Two wins beyond the Wilson interval (worse on none) wins; fewer wins and no axis where it is worse is a capability tie, which goes to the maintained
    candidate (H1 before H2); authority / forgetting / extraction / abstention scores no longer break ties."""
    assert gates.WIN_AXES == {"C": "temporal", "D": "recall_distance", "J": "exact_words", "K": "reflection", "L": "multi_hop", "M": "protocol_brain"}
    assert gates.FLOOR_AXES == {"B": "extraction", "E": "abstention"} and "poisoning" in gates.HARD_AXES and gates.MAINTAINED == ("H1", "H2")
    assert (gates.RULE["capability_wins_min"], gates.RULE["capability_axes_with_data_min"], gates.RULE["observation_precision_min"]) == (2, 3, 0.95)
    z0 = gates.aggregate_axes({"s": {"axes": cap()}})
    # a win: exact words 20 / 20 vs Z0's 10 / 20 and multi-hop 20 / 20 vs 5 / 20 (items), worse on none
    strong = {n: gates.evaluate_arm(n, three(axes=cap(exact=(20, 20), hops=(20, 20))), good_measure()) for n in ("H1", "H2")}
    z0w = gates.aggregate_axes({"s": {"axes": cap(hops=(5, 20))}})
    d = gates.decide(strong, z0w)
    assert d["verdict"] == "ADOPT_CANDIDATE" and d["winner"] == "H1" and "exact_words" not in d["text"] and "J" in d["text"] and "L" in d["text"]
    assert d["caveat"].startswith("Advisory") and "floors" in d["caveat"]
    # a tie on capability, with data on at least three axes: the maintained candidate
    tie = {"H1": gates.evaluate_arm("H1", three(axes=cap()), good_measure())}
    d2 = gates.decide(tie, z0)
    assert d2["verdict"] == "ADOPT_ON_TIE" and d2["winner"] == "H1" and "maintained candidate" in d2["text"] and d2["adoptable"] == ["H1"]
    # store hygiene does not break a tie: an arm far WORSE than Z0 on extraction and abstention (the floors' axes) still takes a capability tie
    hyg = {"H1": gates.evaluate_arm("H1", three(axes={**cap(), "extraction": ax_c(0, 30), "abstention": ax_c(0, 30)}), good_measure())}
    z0h = gates.aggregate_axes({"s": {"axes": {**cap(), "extraction": ax_c(30, 30), "abstention": ax_c(30, 30)}}})
    dh = gates.decide(hyg, z0h)
    assert dh["verdict"] == "ADOPT_ON_TIE" and dh["floors"]["H1"]["B"]["worse"] is True and "B" not in dh["compare"]["H1"]
    # too little capability evidence is not a tie
    thin = {"H1": gates.evaluate_arm("H1", three(axes={"temporal": ax_c(10, 20), "exact_words": ax_i(10, 20)}), good_measure())}
    assert gates.decide(thin, z0)["verdict"] == "KEEP_Z0" and "too thin" in gates.decide(thin, z0)["text"]
    # worse beyond the interval on one capability axis: not adoptable, however many it wins
    worse = {"H1": gates.evaluate_arm("H1", three(axes=cap(temporal=(1, 20), exact=(20, 20), hops=(20, 20))), good_measure())}
    dw = gates.decide(worse, z0w)
    assert dw["verdict"] == "KEEP_Z0" and "WORSE" in dw["text"]
    # a floor still fails the arm whatever it wins
    bad = {"H1": gates.evaluate_arm("H1", three(axes=cap(exact=(20, 20), hops=(20, 20)), hard=["A1.digest.home"]), good_measure())}
    assert gates.decide(bad, z0w)["verdict"] == "KEEP_Z0"


def _k1_row(verdict="FAIL", stage="write", decidable=20, false=2, reason=""):
    """A K1 cell row as ``runner._row`` makes it, with the evidence shape ``scorers_cap.score_observations`` writes."""
    if verdict == "ERROR":
        ev = {}
    elif decidable < 3:
        ev = {"probes": [{"observations_judged": {"observations": decidable, "true": decidable - false, "false": false, "neutral": 0, "decidable": decidable,
                                                   "reason": "too few decidable observations"}, "items": [decidable - false, decidable]}]}
    else:
        ev = {"probes": [{"observations_judged": {"n": decidable, "hits": decidable - false, "false": false, "decidable": decidable}, "items": [decidable - false, decidable]}]}
    return {"id": "K1.observations_are_true", "verdict": verdict, "stage": stage, "evidence": ev, "reason": reason}


def _h2_with_k1(*rows, failing=("K1.observations_are_true",)):
    runs = three(axes=cap(exact=(20, 20), hops=(20, 20), reflection=(20, 20)))
    runs["s0"]["k1"] = [gates.k1_evidence(r) for r in rows]
    a = gates.evaluate_arm("H2", runs, good_measure())
    a["axes"]["reflection"]["failing"] = list(failing)             # K1 is in the axis' failing list whatever the reason it failed
    return a


def test_fabricated_observations_veto_an_arm_whatever_else_it_wins():
    z0 = gates.aggregate_axes({"s": {"axes": cap(hops=(5, 20))}})
    # a MEASURED false rate above the limit (2 of 20 = 10%) over enough decidable observations: vetoed
    liar = {"H2": _h2_with_k1(_k1_row(decidable=20, false=2))}
    st = gates.observation_status(liar["H2"]["axes"])
    assert st["status"] == "vetoed" and st["evidence"][0]["decidable"] == 20 and st["evidence"][0]["false_rate"] == 0.1 and st["evidence"][0]["seed"] == "s0"
    d = gates.decide(liar, z0)
    assert gates.observation_veto(liar["H2"]["axes"]) and d["verdict"] == "KEEP_Z0" and "H2" in d["vetoed"] and "VETOED" in d["text"] and "2 false of 20" in d["text"]
    # exactly three decidable is enough to measure; one false of three is 33%
    assert gates.observation_veto(_h2_with_k1(_k1_row(decidable=3, false=1))["axes"])
    honest = {"H2": gates.evaluate_arm("H2", three(axes=cap(exact=(20, 20), hops=(20, 20), reflection=(20, 20))), good_measure())}
    assert not gates.observation_veto(honest["H2"]["axes"]) and gates.decide(honest, z0)["verdict"] == "ADOPT_CANDIDATE"
    assert gates.observation_status(honest["H2"]["axes"])["status"] == "not_run"


def test_a_k1_that_did_not_measure_is_reported_as_insufficient_or_error_and_never_vetoes():
    """K1 also fails at stage ``read`` (fewer than three decidable observations) and an ERROR cell lands in the axis' failing list: neither says the layer fabricates."""
    z0 = gates.aggregate_axes({"s": {"axes": cap(hops=(5, 20))}})
    # precision 100% over two decidable observations: the cell FAILs at read, the evidence is insufficient, the arm is not vetoed
    thin = {"H2": _h2_with_k1(_k1_row(verdict="FAIL", stage="read", decidable=2, false=0))}
    st = gates.observation_status(thin["H2"]["axes"])
    assert st["status"] == "insufficient" and st["evidence"][0]["false_rate"] == 0.0 and "too few decidable" in st["reason"]
    d = gates.decide(thin, z0)
    assert not gates.observation_veto(thin["H2"]["axes"]) and d["vetoed"] == [] and d["verdict"] == "ADOPT_CANDIDATE"
    assert d["observations"]["H2"]["status"] == "insufficient" and "NOT established" in d["text"] and "VETOED" not in d["text"]
    # zero observations at all (an empty layer): also insufficient, never fabrication
    assert gates.observation_status(_h2_with_k1(_k1_row(verdict="FAIL", stage="read", decidable=0, false=0))["axes"])["status"] == "insufficient"
    # an ERROR cell is a measurement error
    err = {"H2": _h2_with_k1(_k1_row(verdict="ERROR", stage="", reason="RuntimeError: the idle pass did not run"))}
    assert gates.observation_status(err["H2"]["axes"])["status"] == "measurement_error" and not gates.observation_veto(err["H2"]["axes"])
    assert gates.decide(err, z0)["vetoed"] == []
    # an old record that has K1 in failing and no counts cannot be read as fabrication either
    old = gates.evaluate_arm("H2", three(axes=cap(exact=(20, 20), hops=(20, 20), reflection=(20, 20))), good_measure())
    old["axes"]["reflection"]["failing"] = ["K1.observations_are_true"]
    assert gates.observation_status(old["axes"])["status"] == "insufficient" and not gates.observation_veto(old["axes"])
    # a clean K1 reads clean
    assert gates.observation_status(_h2_with_k1(_k1_row(verdict="PASS", stage="", decidable=20, false=0), failing=())["axes"])["status"] == "clean"
    # the evidence of a seed that measured fabrication still vetoes beside a thin one
    mixed = _h2_with_k1(_k1_row(verdict="FAIL", stage="read", decidable=2, false=0), _k1_row(decidable=20, false=4))
    assert gates.observation_veto(mixed["axes"])


def test_the_derived_axes_are_built_from_the_per_cell_verdicts():
    """D = the recall cells AT DISTANCE (D2 / D3 / D4; D1's 30 turns is near); M = the brain-tier protocol cells only (the lab half never decides)."""
    cells = [{"id": "D1.hit5_after_30_filler", "verdict": "FAIL", "sanity": False}, {"id": "D2.hit5_after_100_filler", "verdict": "PASS", "sanity": False},
             {"id": "D3.hit5_after_300_filler", "verdict": "PASS", "sanity": False}, {"id": "D4.hit5_paraphrase_after_100_filler", "verdict": "FAIL", "sanity": False},
             {"id": "M1.answered_when_recall_fired", "verdict": "PASS", "sanity": False}, {"id": "M4.fire_when_needed.zoe", "verdict": "SKIP", "sanity": False}]
    a = gates.aggregate_axes({"s": {"axes": {"recall": ax_c(2, 4)}, "cells": cells}})
    assert (a["recall_distance"]["pass"], a["recall_distance"]["n"]) == (2, 3) and a["recall_distance"]["failing"] == ["D4.hit5_paraphrase_after_100_filler"]
    assert "protocol_brain" not in a                                                              # M4 cells all SKIPped (no brain): no data, so M never counts
    ran = gates.aggregate_axes({"s": {"axes": {}, "cells": cells[:5] + [{"id": "M4.fire_when_needed.zoe", "verdict": "PASS", "sanity": False}]}})
    assert (ran["protocol_brain"]["pass"], ran["protocol_brain"]["n"]) == (1, 1)
    c = gates.compare_axes(gates.aggregate_axes({"s": {"axes": {"recall": ax_c(20, 20)}}}), gates.aggregate_axes({"s": {"axes": {"recall": ax_c(4, 20)}}}))                       # an old record with no per-cell verdicts: D falls back to the recall axis
    assert c["D"]["axis"] == "recall_distance" and c["D"]["beats"]


def test_items_are_the_unit_of_the_item_axes_and_cells_are_not():
    """Two cells of 0 / 2 vs 2 / 2 cannot separate; 20 of 20 items vs 0 of 20 can. That is why J / K / L pool items."""
    arm = {"exact_words": ax_i(20, 20)}
    z0 = {"exact_words": ax_i(0, 20)}
    agg = lambda x: gates.aggregate_axes({"s": {"axes": x}})  # noqa: E731
    c = gates.compare_axes(agg(arm), agg(z0))["J"]
    assert c["unit"] == "items" and c["beats"] and c["arm"][:2] == [20, 20] and c["z0"][:2] == [0, 20]
    cells_only = gates.compare_axes(agg({"exact_words": ax_c(2, 2)}), agg({"exact_words": ax_c(0, 2)}))["J"]
    assert cells_only["unit"] == "cells" and not cells_only["beats"]                              # two cells say nothing beyond the interval


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


def canned_hm(cells):
    """What ``hm_window.py`` would hand back, produced by the HM arm over the DOUBLES (no library, no server): the HM cells + the generic cells of one seed."""
    from zmb import hm_cells, runner
    from zmb.arms.hm import FakeDistilledTier, HMArm
    from zmb.arms.mempalace_verbatim import InMemoryVerbatimStore, MemPalaceVerbatimArm

    def run(ctx, seed, box_s):
        arm = HMArm(distilled=FakeDistilledTier(), verbatim=MemPalaceVerbatimArm(store=InMemoryVerbatimStore()))
        rows = runner.run_cells(cells, make_world(seed), arm, log=lambda m: None)
        return {"library": "mempalace 3.10.0 (double)", "hm_cells": hm_cells.run_all("double", controls="real-tier"),
                "generic": {"seed": seed, "rows": rows, "cells_ran": sum(1 for r in rows if r["verdict"] != "SKIP"), "cells_selected": len(rows), "duration_s": 1.0},
                "driver": {"pss_before_mb": 60.0, "pss_after_mb": 80.0, "peak_rss_mb": 100.0, "verbatim_added_mb": 20.0, "peak_added_mb": 40.0}}
    return run


def e2e_window(box, tmp_path, monkeypatch, *, box_min=None, egress=True, extra=(), pg=False, host=None, **cfg_kw):
    cells = [c for c in spec.load_cells() if c.id in SUBSET + tuple(extra)]
    assert len(cells) == len(SUBSET) + len(extra)
    monkeypatch.setattr(spec, "load_cells", lambda directory=None: cells)
    from zmb import lab_driver
    monkeypatch.setattr(lab_driver, "embedder_available", lambda: False)       # Z0e: no real Chroma + MiniLM in this lane (its own tests below)
    monkeypatch.setattr(measure, "VALIDITY_CALLS", 104)
    monkeypatch.setattr(measure, "LATENCY_FACTS", 4)
    if box_min:
        monkeypatch.setattr(measure, "BOX_MIN", box_min)
    host = host or FakeHost(box)
    host.log = lambda _m: None
    w = make_window(box, host, docs_dir=tmp_path / "docs", sample_s=0.01, **cfg_kw)
    if pg:                                   # the stack the window really has: Hindsight writing to a scratch Postgres the arm can read and scrub
        from zmb.arms.fake_postgres import FakePostgres
        store = FakePostgres()
        fake = FakeHindsight(pg=store)
        w.arm_factory = lambda v, **kw: HindsightArm(v, transport=fake, settle_poll_s=0, pg=store, **kw)
    else:
        fake = FakeHindsight()
        w.arm_factory = lambda v, **kw: HindsightArm(v, transport=fake, settle_poll_s=0, **kw)
    w.fake = fake
    w.hm_runner = canned_hm(cells)
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
    cfg = bakeoff.Cfg(bakeoff_dir=box, arms=HM_ERA)
    b = measure.plan_budget(cfg)
    assert b.seeds == {"H1": 3, "H2": 1, "H0": 1, "HM": 1} and b.arms == ("H1", "H2", "HM", "H0")        # HM: one seed box; H1 keeps three
    assert b.total_min() <= b.avail_min <= cfg.cap_min - cfg.reserve_min - measure.TAIL_MIN              # the hard cap is kept, with the tail
    assert b.cells_in_box("H1") >= b.runnable > 100                     # one H1 seed box holds every runnable store cell at run 1's rate
    assert b.box_min["H2"] >= 2.0 and b.box_min["H0"] >= 1.0 and b.box_min["HM"] >= 1.0
    assert b.store_cells == b.runnable > b.runnable_h0 > b.runnable_hm > 100     # H1 / H2 run every store cell now; H0 has no Zoe layer; HM runs what clock / identities / idle_pass / verbatim / reader can
    assert 3 * b.box_min["H1"] > b.box_min["H2"] + b.box_min["H0"] + b.box_min["HM"]      # H1 is the preferred arm: the biggest share of the time
    assert b.box_min["H2"] >= b.box_min["HM"] >= b.box_min["H0"]                          # HM is a candidate: ahead of the native baseline


def test_a_run_of_h1_h2_h0_without_hm_keeps_the_pre_hm_budget_shape(box):
    b = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box, arms=("H1", "H2", "H0")))
    assert b.arms == ("H1", "H2", "H0") and "HM" not in b.seeds and b.total_min() <= b.avail_min


def test_hm_fits_the_90_minute_cap_next_to_h1_x3_h2_and_h0_and_the_plan_says_what_it_costs(box):
    """The owner's question (2026-10-06): can HM ride along? Yes, as ONE seed box: the HM cells (about 4 min measured on the real tiers) plus a store-cell box."""
    cfg = bakeoff.Cfg(bakeoff_dir=box, arms=HM_ERA)
    b = measure.plan_budget(cfg)
    without = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box, arms=("H1", "H2", "H0")))
    assert b.total_min() <= b.avail_min and b.box_min["H1"] == without.box_min["H1"]       # H1's three full seeds are untouched
    assert b.box_min["H2"] < without.box_min["H2"] or b.box_min["H0"] < without.box_min["H0"]    # what HM costs: slack taken from H2 / H0's boxes, nothing from H1


def test_a_lower_arm_only_gets_what_is_left_behind_the_work_queued_for_it(box):
    b = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box, arms=HM_ERA, reflect_ctx=0))
    assert b.seed_box_s("H1", 3600.0, first=False) == b.box_min["H1"] * 60.0 and b.seed_box_s("H1", 120.0) == 120.0     # H1: its ceiling, never over the time left
    assert b.seed_box_s("H1", 3600.0) == (b.box_min["H1"] + b.extra_min["H1"]) * 60.0                  # seed 1 also holds the capability cells; seeds 2 and 3 do not
    assert b.fixed_min("H0") == 0.0 and b.fixed_min("H2") == 0.0     # H0's and H2's latency / slot are "only if time remains": their cells outrank their timings (CUT for the capability axes)
    queued_h2 = (b.fixed_min("H2") + 2.0) * 60.0       # its own phases (none budgeted) + H0's 2 min seed box
    assert b.seed_box_s("H2", queued_h2 - 1.0) == 0.0                                  # nothing left behind its own queue: no box, no starving H0
    assert 0 < b.seed_box_s("H2", 3000.0) <= 2 * (b.box_min["H2"] + b.extra_min["H2"]) * 60.0
    assert b.seed_box_s("H2", 6000.0) == 2 * (b.box_min["H2"] + b.extra_min["H2"]) * 60.0     # an early H1 hands its slack on, up to twice the plan
    assert b.seed_box_s("H0", 100.0 * 60) <= 2 * (b.box_min["H0"] + b.extra_min["H0"]) * 60.0


def test_h1_runs_first_and_complete_then_h2_then_h0(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch)
    calls, real = [], measure.run_arm_seed
    monkeypatch.setattr(measure, "run_arm_seed", lambda ctx, v, seed, *a, **k: (calls.append((v, seed)), real(ctx, v, seed, *a, **k))[1])
    measure.measure(w)
    assert [v for v, _s in calls] == ["H1", "H1", "H1", "H2", "H0"]                   # HM's seed box is run by its own driver (hm_window.py), in between: see below
    assert len({s for v, s in calls if v == "H1"}) == 3 and {s for v, s in calls if v != "H1"} == {calls[0][1]}


def test_the_dry_plan_prints_the_per_arm_cell_budget_and_the_seed_counts(box):
    host = FakeHost(box)
    w = make_window(box, host, dry=True, arms=HM_ERA)
    measure.dry_plan(w)
    line = next(m for m in w.logs if m.startswith("per-arm cell budget"))
    assert "H1 3 seeds x " in line and "H2 1 seed x " in line and "H0 1 seed x " in line and "HM 1 seed x " in line and "all 3 seeds complete inside the cap" in line
    assert "SKIP by capability" in line
    out = "\n".join(w.logs)
    assert out.index("H1 seed 1") < out.index("H1 seed 3") < out.index("H2 seed 1") < out.index("HM seed 1") < out.index("H0 seed 1")      # execution order: H1 first and complete
    assert "Z0e (real Chroma + MiniLM)" in out and "HM cells on the REAL tiers" in out
    assert "H2 seed 2" not in out and "H0 seed 2" not in out and "slack +" in out


# ── HM and Z0e in the window (first contact 2026-10-06) ──────────────────────────────────────────────────────────

def test_the_window_measures_hm_on_one_seed_and_the_record_has_its_own_line_gates_and_winner_column(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch)
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    md = Path(art["docs_path"]).read_text()
    hm = art["arms"]["HM"]
    assert hm["seeds_done"] == 1 and hm["seeds"]["state"] == gates.NA and hm["verdict"] in ("INCOMPLETE", "NOT_ADOPTABLE")      # one seed by design: measured for the comparison, never adopted
    assert set(hm["gates"]) == {"G0", "G1", "G2", "G3", "HM"} and "hm_G1a_second_lookup_adds" in hm["gates"]["HM"] and "hm_G3a_replaces_zoe_datas_palace" in hm["gates"]["HM"]
    assert hm["gates"]["HM"]["hm_G3a_replaces_zoe_datas_palace"]["state"] == gates.NA        # a design review, never a pass the window can give
    assert hm["gates"]["G0"]["steady_rss"]["measured"].endswith("MB") and art["measure"]["HM"]["rss"]["note"].startswith("the servers' PSS during the HM phase")
    assert art["measure"]["HM"]["forgetting"]["t0"]["resurrected"] == 0 and art["measure"]["HM"]["forgetting"]["t6"]["resurrected"] == 0
    assert "## HM:" in md and "| Letter | Axis | Z0 | H1 | H2 | HM | MPA | HMA | ZMA | H0 |" in md
    assert any("HM runs ONE seed by design" in n for n in art["notes"]) and "HM" in art["compare"]
    assert art["decision"]["verdict"] in ("KEEP_Z0", "ADOPT_CANDIDATE") and art["decision"]["winner"] != "HM"


def test_an_hm_driver_that_did_not_run_is_reported_not_passed(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch)
    w.hm_runner = lambda ctx, seed, box_s: {"skipped": "the real MemPalace library is not importable in this interpreter"}
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    assert art["arms"]["HM"]["seeds_done"] == 0 and art["arms"]["HM"]["verdict"] == "INCOMPLETE" and art["arms"]["HM"]["gates"]["HM"]["hm_cells_ran"]["state"] == gates.NA
    assert any("HM did not run" in n for n in art["notes"])


def test_an_hm_driver_stopped_by_a_voice_turn_aborts_the_window_and_the_rest_is_still_reported(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch)
    w.hm_runner = lambda ctx, seed, box_s: {"aborted": "a voice turn happened at 2026-10-06 20:00:00 after the window started: stopping (the live brain is shared)"}
    with pytest.raises(bakeoff.Aborted, match="voice turn"):
        measure.measure(w)
    assert json.loads((box / "run-t1.json").read_text())["meta"]["aborted"].startswith("Aborted: a voice turn")


def test_hm_gates_read_the_real_tiers_numbers_and_the_second_lookup_budget_is_pre_registered():
    assert (gates.RULE["hm_voice_p95_ms"], gates.RULE["hm_second_lookup_ms"], gates.RULE["hm_verbatim_p95_ms"]) == (600.0, 25.0, 100.0)
    cells = [{"id": "HM-L1.latency.voice-lane", "verdict": "PASS", "evidence": {"p95_ms": 0.01}},
             {"id": "HM-L2.latency.two-lookups", "verdict": "FAIL", "evidence": {"added_ms": 29.77, "p95_verbatim_alone_ms": 72.8}}]
    g = gates.gate_hm({"hm_cells": {"summary": {"pass": 1, "graded": 2, "fail": ["HM-L2.latency.two-lookups"], "skipped": [], "controls_checked": 5, "not_instrumented": []}, "cells": cells}})
    assert g["hm_G1a_second_lookup_adds"]["state"] == gates.FAIL and g["hm_G1a_voice_lane_p95"]["state"] == gates.PASS and g["hm_G1a_verbatim_query_p95"]["state"] == gates.PASS
    assert g["hm_cells_zero_violations"]["state"] == gates.FAIL and g["hm_G2a_forget_both_tiers_t0_t6"]["state"] == gates.NA        # F1 / F2 did not run: not a pass


def test_the_recall_axis_is_compared_with_z0e_when_it_ran_and_with_z0_when_it_did_not():
    arm = {"recall": {"pass": 20, "n": 20, "skipped": 0, "wilson95": [0.84, 1.0]}}
    z0 = {"recall": {"pass": 4, "n": 4, "skipped": 0, "wilson95": [0.51, 1.0]}}
    z0e = {"recall": {"pass": 12, "n": 12, "skipped": 0, "wilson95": [0.76, 1.0]}}
    assert gates.compare_axes(arm, z0)["D"]["baseline"] == "Z0" and gates.compare_axes(arm, z0, z0e)["D"]["baseline"] == "Z0e"
    assert gates.compare_axes(arm, z0, z0e)["D"]["z0"][1] == 12                       # the baseline's own counts, not the bag-of-words'
    assert gates.compare_floors(arm, z0)["B"]["baseline"] == "Z0" and gates.compare_axes(arm, z0, z0e)["C"]["baseline"] == "Z0"      # only D and L move: the rest stay on the lab's Z0
    bad = {"recall": {"pass": 0, "n": 20, "skipped": 0, "wilson95": [0.0, 0.16]}}
    assert gates.compare_axes(bad, z0, z0e)["D"]["worse"] is True


def test_z0e_runs_on_the_recall_cells_and_gets_its_own_column_and_the_d_baseline(box, tmp_path, monkeypatch):
    from zmb import arms as armsmod
    from zmb.arms import z0 as z0mod
    w, _host = e2e_window(box, tmp_path, monkeypatch, extra=("D1.hit5_after_30_filler",))
    real = armsmod.make_arm
    monkeypatch.setattr(armsmod, "make_arm", lambda name: z0mod.Z0Arm(name="Z0e") if name == "Z0e" else real(name))      # the lab's Z0 stands in for the embedder-backed one
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    md = Path(art["docs_path"]).read_text()
    assert set(art["z0e"]) == set(art["z0"]) and all("recall" in a for a in art["z0e"].values())          # three seeds, the recall axis
    assert "| Axis | Z0 | Z0e (real retrieval) | Z0-off" in md and "Z0e (the D baseline)" in md and "multi_hop" in md
    assert art["compare"]["H1"]["D"]["baseline"] == "Z0e"


def test_without_chroma_and_the_model_z0e_is_a_skip_and_the_record_says_the_d_baseline_is_bag_of_words(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch, extra=("D1.hit5_after_30_filler",))
    measure.measure(w)                                                                          # e2e_window made the embedder unavailable
    art = json.loads((box / "run-t1.json").read_text())
    assert art["z0e"] == {} and any("Z0e did not run" in n for n in art["notes"]) and art["compare"]["H1"]["D"]["baseline"] == "Z0"


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
    assert gates.WIN_AXES["C"] == "temporal" and gates.WIN_AXES["D"] == "recall_distance"
    def ax(p, n):
        lo, hi = gates.wilson(p, n)
        return {"pass": p, "n": n, "wilson95": [round(lo, 4), round(hi, 4)]}
    z0 = {"extraction": ax(20, 20), "temporal": ax(10, 20), "recall": ax(4, 20), "abstention": ax(10, 10)}
    arm = {"extraction": ax(20, 20), "temporal": ax(20, 20), "recall": ax(20, 20), "abstention": ax(10, 10)}
    c = gates.compare_axes(arm, z0)
    assert c["C"]["axis"] == "temporal" and c["C"]["beats"] and c["D"]["axis"] == "recall_distance" and c["D"]["beats"]       # two beats: the rule's win
    assert not any(v["worse"] for v in c.values()) and "B" not in c and "E" not in c
    fl = gates.compare_floors(arm, z0)
    assert not fl["B"]["beats"] and not fl["E"]["beats"]                                                             # the floors are reported, they decide nothing
    assert gates.compare_axes({}, z0)["C"]["note"] == "no data"                                                       # an arm that ran none: no data, not a tie


def test_the_run_record_opens_with_the_verdict_the_clause_per_axis_and_the_plain_answer(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch)
    measure.measure(w)
    md = Path(json.loads((box / "run-t1.json").read_text())["docs_path"]).read_text()
    head = md.split("## Run")[0]
    assert "**Verdict by the pre-registered rule:" in head and "### Winner clause per capability axis" in head and "### Is it better than ours?" in head
    assert "| C | temporal |" in head and "| D | recall_distance" in head and "| J | exact_words" in head and "| K | reflection" in head
    assert "| L | multi_hop" in head and "| M | protocol_brain" in head and "| B | extraction |" in head and "| E | abstention |" in head
    assert "FLOORS" in head and "ties on capability go to the maintained candidate" in head and "no longer break ties" in head and "Honest caveats:" in head
    assert head.index("Verdict by") < head.index("Winner clause per capability axis") < head.index("Floors reported beside the contest") < head.index("Is it better than ours?")


def test_the_plain_answer_says_yes_only_when_the_rule_says_so():
    ax = {"extraction": {"pass": 18, "n": 18, "wilson95": [0.82, 1.0], "skipped": 0}}
    arm = {"verdict": "PASSES_BUILT_GATES", "seeds_done": 3, "axes": ax, "gates": {"G0": {"x": {"state": gates.PASS}}}}
    won = {"verdict": "ADOPT_CANDIDATE", "winner": "H1", "compare": {"H1": {"J": {"axis": "exact_words", "built": True, "beats": True, "worse": False},
                                                                        "L": {"axis": "multi_hop", "built": True, "beats": True, "worse": False}}}}
    assert gates._better_line({"H1": arm}, won).startswith("Yes, on what was measured: H1 passes every floor")
    tied = {"verdict": "ADOPT_ON_TIE", "winner": "H1", "compare": {"H1": {"C": {"axis": "temporal", "built": True, "beats": False, "worse": False, "arm": [9, 9, [0.7, 1.0]]},
                                                                         "K": {"axis": "reflection", "built": True, "beats": False, "worse": False, "note": "no data"}}}}
    line = gates._better_line({"H1": arm}, tied)
    assert line.startswith("Not shown to be better, not shown to be worse: H1 passes every floor") and "maintained candidate" in line and "no data for K reflection" in line
    tie = {"verdict": "KEEP_Z0", "compare": {"H1": {"C": {"axis": "temporal", "built": True, "beats": False, "worse": False, "arm": [18, 18, [0.82, 1.0]]},
                                                  "J": {"axis": "exact_words", "built": True, "beats": False, "worse": False, "note": "no data"}}}}
    line = gates._better_line({"H1": {**arm, "verdict": "NOT_ADOPTABLE", "gates": {"G2": {"hard_cells_all_ran": {"state": gates.FAIL}}}}}, tie)
    assert line.startswith("No evidence that H1 is better than Z0") and "ties Z0 on C temporal" in line and "no data for J exact_words" in line
    assert "keep Z0" in line and "G2 hard_cells_all_ran" in line


# ── the H arms run the graph-edge, conflict-pass and physical-erase cells (the structural skips of run 1 are gone) ────────────────

STRUCTURAL = ("A8.inferred_cannot_close_user_edge", "A8.refused_edge_is_held_not_lost", "A8.user_change_closes_edge_keeps_history",
              "F5.forgotten_text_not_on_disk", "F6.hard_delete_not_on_disk", "C1.update_typed", "C1.old_fact_invalidated_not_deleted")


def test_the_scratch_postgres_container_name_is_one_name_in_both_places():
    from zmb.arms import pg_store
    assert pg_store.PG_CONTAINER == bakeoff.PG_CONTAINER == "zoe-bakeoff-pg"


def test_the_windows_arms_get_a_handle_on_the_scratch_postgres(box):
    w = make_window(box, FakeHost(box))
    measure.make_factories(w)
    for v in ("H0", "H1", "H2"):
        arm = w.arm_factory(v)
        assert arm.pg is not None and arm.pg.container == bakeoff.PG_CONTAINER and "disk" in arm.capabilities, v
        arm.close()
    assert "edges" in w.arm_factory("H1").capabilities and "edges" not in w.arm_factory("H0").capabilities


def test_the_dry_plan_counts_what_each_kind_of_arm_can_run(box):
    w = make_window(box, FakeHost(box), dry=True)
    measure.dry_plan(w)
    line = next(m for m in w.logs if m.startswith("per-arm cell budget"))
    b = measure.plan_budget(w.cfg)
    assert b.runnable == b.store_cells and b.runnable_h0 < b.runnable
    assert f"0 of {b.store_cells} ordinary store cells SKIP by capability on H1 / H2" in line
    assert f"{b.store_cells - b.runnable_h0} on H0 (no Zoe layer: conflict_pass / edges)" in line
    assert f"/{b.runnable_h0} runnable cells each" in line and f"/{b.runnable} runnable cells each" in line


CAP_IDS = ("J1.exact_sentence_after_100_filler", "J2.when_did_i_say_it", "K1.observations_are_true", "K2.thread_recall", "K4.invalidated_fact_not_restated",
           "L1.two_facts_after_100_filler", "M1.answered_when_recall_fired")


def test_the_capability_cells_run_on_seed_one_only_and_each_arm_runs_the_ones_it_is_the_evidence_for(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch, extra=CAP_IDS)
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    h1 = art["seed_runs"]["H1"]
    seeds = list(h1)
    ids = lambda run: {c["id"]: c for c in run["cells"]}  # noqa: E731
    held = [k for k in h1 if k != "zmb-v1"]                                                                               # the artifact sorts its keys: seed 1 is the baseline seed
    first, later = ids(h1["zmb-v1"]), ids(h1[held[0]])
    assert set(CAP_IDS) <= set(first) and not set(CAP_IDS) & set(later) and not set(CAP_IDS) & set(ids(h1[held[1]]))        # seed 1 only
    assert first["J1.exact_sentence_after_100_filler"]["verdict"] == "FAIL"                                               # H1 keeps the extractor's facts, not the raw turn
    assert first["K1.observations_are_true"]["verdict"] == "SKIP" and "observations" in first["K1.observations_are_true"]["reason"]     # no observation layer: a capability skip, not a cut
    h2 = ids(art["seed_runs"]["H2"]["zmb-v1"])
    assert h2["J1.exact_sentence_after_100_filler"]["verdict"] == "SKIP" and "planner cut" in h2["J1.exact_sentence_after_100_filler"]["reason"]    # the plan's cut, said so
    assert h2["K1.observations_are_true"]["verdict"] != "SKIP" and h2["K2.thread_recall"]["verdict"] != "SKIP"                        # H2 runs the observation layer's cells
    assert art["arms"]["H1"]["axes"]["exact_words"]["items"]["n"] == 40 and art["arms"]["H2"]["axes"]["reflection"]["n"] >= 3
    why = art["arms"]["H2"]["gates"]["G2"]["hard_cells_all_ran"]["measured"]
    assert "planner cut" not in why or "hard" in why                                                                    # the cut cells are not hard cells: they cannot keep a floor red
    md = Path(art["docs_path"]).read_text()
    assert "### Winner clause per capability axis" in md and "| J | exact_words" in md and "FLOORS" in md and "no longer break ties" in md
    assert set(art["compare"]["H1"]) == {"C", "D", "J", "K", "L", "M"} and set(art["decision"]["floors"]["H1"]) == {"B", "E"}
    assert art["decision"]["verdict"] in ("KEEP_Z0", "ADOPT_CANDIDATE", "ADOPT_ON_TIE")


def test_the_baseline_runs_the_capability_cells_on_the_same_seed_as_the_candidates_and_only_that_one(monkeypatch):
    """The candidates run J/K/L/M on seed 1 only; Z0 / Z0-off / Z0e pooled over three households would be compared with ONE, so they get seed 1 only too."""
    import types
    from zmb import runner
    ran = []
    monkeypatch.setattr(runner, "control_pass", lambda *a, **k: {})
    monkeypatch.setattr(runner, "instrument_block", lambda *a, **k: {"ok": True, "lab_controls_red": "0/0"})
    monkeypatch.setattr(runner, "run_cells", lambda cells, world, arm: (ran.append([c.axis for c in cells]), [])[1])
    ctx = types.SimpleNamespace(z0={}, z0_off={}, z0e={}, notes=[], log=lambda *a, **k: None)
    store = [c for c in spec.load_cells() if c.tier == "store"]
    assert {c.axis for c in store} & set(measure.CAP_AXES)
    measure.phase_z0(ctx, ("s1", "s2", "s3"), store, {c.id: c for c in store})
    per_seed = [any(a in measure.CAP_AXES for a in axes) for axes in ran]
    assert len(ran) == 3 * 3 and per_seed == [True, True, True, False, False, False, False, False, False]      # (Z0, Z0-off, Z0e) x (s1, s2, s3)


def test_the_dry_plan_states_what_it_cut_to_fit_the_capability_axes_under_the_cap(box):
    w = make_window(box, FakeHost(box), dry=True, arms=HM_ERA)
    measure.dry_plan(w)
    out = "\n".join(w.logs)
    b = measure.plan_budget(w.cfg)
    assert b.total_min() <= b.avail_min and b.avail_min <= w.cfg.cap_min - w.cfg.reserve_min - measure.TAIL_MIN               # inside the 90 minute cap with the restore reserve kept
    assert "CUT to fit the capability axes under the cap" in out and "H2: exact_words, multi_hop, protocol cut" in out and "H0: exact_words, multi_hop, protocol cut" in out
    assert "H1: nothing cut" in out and "capability cells run on seed 1 only" in out and "(-17.7 min)" in out
    for arm in ("H1", "H2", "HM", "H0"):
        assert f"{arm} seed 1: capability cells (" in out, arm
    assert "H1 seed 1: capability cells (exact_words, multi_hop, protocol)" in out and "H2 seed 1: capability cells (reflection)" in out
    assert "only if time remains; ~10 min, not budgeted" in out                                                           # the concise validity phase
    assert b.extra_min["H1"] > b.extra_min["H2"] > 0 and b.extra_min["HM"] == measure.HM_CAP_MIN
    assert measure.cap_extra_min("H1") == round(sum(measure.CAP_RETAINS[x] for x in measure.CAP_PLANNED["H1"]) * measure.S_PER_RETAIN["H1"] / 60.0 * 2) / 2.0
    assert "Z0e (real Chroma + MiniLM) on the 4 recall (D) + 3 long-range (L) cells" in out


def test_the_planner_does_not_count_the_capability_cells_in_the_box_run_1_measured(box):
    b = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box, arms=HM_ERA))
    from zmb import cells as cellmod
    cap_cells = [c for c in spec.load_cells() if c.tier == "store" and c.axis in measure.CAP_AXES]
    assert cap_cells and b.store_cells == len([c for c in spec.load_cells() if c.tier == "store"]) - len(cap_cells)
    assert set(measure.CAP_AXES) == {"exact_words", "reflection", "multi_hop", "protocol"} and set(measure.CAP_CUT) == {"H0", "H1", "H2", "HM", "MPA", "HMA", "ZMA"}
    assert all(set(measure.CAP_CUT[a]) | set(measure.CAP_PLANNED[a]) <= set(measure.CAP_AXES) for a in measure.CAP_CUT)


def test_measure_over_the_full_stack_runs_the_hard_edge_and_disk_cells_on_h1_and_h0_stays_red_by_design(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch, extra=STRUCTURAL, pg=True)
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    for v in ("H1", "H2"):
        g2 = art["arms"][v]["gates"]["G2"]
        assert g2["hard_cells_all_ran"]["state"] == gates.PASS and g2["hard_cells_zero_violations"]["state"] == gates.PASS, (v, g2)
    h0 = art["arms"]["H0"]["gates"]["G2"]
    assert h0["hard_cells_all_ran"]["state"] == gates.FAIL and "capability: edges" in h0["hard_cells_all_ran"]["measured"]   # A8 x2: no layer, no graph
    h0_bad = art["seed_runs"]["H0"]["zmb-v1"]["hard_violations"]
    assert "F5.forgotten_text_not_on_disk" in h0_bad and "F6.hard_delete_not_on_disk" in h0_bad                        # native Hindsight leaves the text on disk
    assert not [b for v in ("H1", "H2") for s in art["seed_runs"][v].values() for b in s["hard_violations"] if b.startswith(("F5", "F6", "A8"))]


def test_without_a_scratch_postgres_the_disk_cells_skip_by_capability_and_keep_the_hard_gate_red(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch, extra=STRUCTURAL, pg=False)
    measure.measure(w)
    g2 = json.loads((box / "run-t1.json").read_text())["arms"]["H1"]["gates"]["G2"]["hard_cells_all_ran"]
    assert g2["state"] == gates.FAIL and "capability: disk" in g2["measured"] and "capability: edges" not in g2["measured"]


def test_the_forget_probes_names_are_outside_every_world_pool_so_they_cannot_read_as_a_disk_cells_residue():
    from zmb import world
    pools = {n.lower() for p in (world._FEMALE, world._MALE, world._NEUTRAL, world._PETS, world._INTRUDERS, world._SURNAMES, world._HOMES) for n in p}
    mine = {measure.ForgetProbe.NAME, measure.ForgetProbe.KEEP, measure.ForgetProbe.PLACE}
    assert len(mine) == 3 and not ({n.lower() for n in mine} & pools)      # its bank lives the whole window in the cluster F5 / F6 scan


def test_a_window_clears_the_banks_an_earlier_window_left_before_its_own_probes_start(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch, extra=STRUCTURAL, pg=True)
    for stale in ("zmb-h1-demo_bar_deadbeef", "zmb-h0-demo_bar_cafef00d"):
        w.fake.banks[stale] = {"config": {}, "docs": {}, "units": [], "name": stale}
    w.fake.banks["someone-elses-bank"] = {"config": {}, "docs": {}, "units": [], "name": "x"}
    measure.measure(w)
    assert "zmb-h1-demo_bar_deadbeef" not in w.fake.banks and "zmb-h0-demo_bar_cafef00d" not in w.fake.banks
    assert "someone-elses-bank" in w.fake.banks                                                          # only this tool's own prefix is touched
    assert any("cleared 2 stale zmb- bank(s)" in m for m in w.logs)


def test_the_stale_bank_sweep_never_stops_the_window(box, tmp_path, monkeypatch):
    w, _host = e2e_window(box, tmp_path, monkeypatch)
    ctx = measure.Ctx(w, None)
    w.fake.down = True
    assert measure.sweep_stale_banks(ctx) == 0 and any("sweep skipped" in m for m in ctx.win.logs)


# ── egress: the second instrument (first contact 2026-10-06 caught the embeddings shim uploading to Microsoft) ─────

def _sampler_over(box, ss_lines, pgrep_out=""):
    host = FakeHost(box)
    base = host.run

    def run(argv, timeout=60.0, mutating=True, env=None):
        if argv[0] == "ss":
            return bakeoff.Result(0, ss_lines)
        if argv[0] == "pgrep" and any("hm_window" in a for a in argv):
            return bakeoff.Result(0 if pgrep_out else 1, pgrep_out)
        return base(argv, timeout, mutating, env)
    host.run = run
    w = make_window(box, host)
    s = measure.Sampler(w)
    s.snapshot()
    return s


def test_the_ss_sampler_flags_a_real_remote_peer_of_the_servers_and_not_the_ipv4_mapped_loopback_spelling(box):
    s = _sampler_over(box, '0 0 [::ffff:127.0.0.1]:41112 [::ffff:127.0.0.1]:55432 users:(("python",pid=100,fd=7))\n'
                           '0 85 192.168.1.218:57136 20.42.73.31:443 users:(("python",pid=100,fd=10))\n'
                           '0 0 192.168.1.218:1 8.8.8.8:443 users:(("other",pid=999,fd=3))\n')
    assert s.nonloopback == {"20.42.73.31"}                      # the shim's telemetry upload; a stranger's socket and the mapped loopback are not counted


def test_the_hm_driver_is_a_process_of_the_window_so_its_sockets_are_watched_too(box):
    s = _sampler_over(box, '0 0 192.168.1.218:5 1.2.3.4:443 users:(("python",pid=555,fd=9))\n', pgrep_out="555\n")
    assert s.nonloopback == {"1.2.3.4"}
    assert _sampler_over(box, '0 0 192.168.1.218:5 1.2.3.4:443 users:(("python",pid=555,fd=9))\n').nonloopback == set()        # no driver running: not ours


def test_every_process_the_window_starts_forces_onnxruntimes_telemetry_off():
    """onnxruntime >= 1.30 ships Microsoft's 1DS SDK and uploads over HTTPS unless ORT_DISABLE_TELEMETRY=1 is set before it initialises (measured: the shim
    connected to Azure addresses every ~2 s). Python's audit hook cannot see a connect made from C, so the setting is pinned at every place a process starts."""
    for name in ("bakeoff.py", "bakeoff_window.sh", "hm_window.py", "bakeoff_measure.py", "embed_shim.py", "lab_driver.py", "arms/mempalace_verbatim.py"):
        assert "ORT_DISABLE_TELEMETRY" in (REPO / "scripts/perf/zmb" / name).read_text(), name
    assert "ORT_DISABLE_TELEMETRY=1" in bakeoff.hindsight_env(ENV_EXAMPLE, bakeoff.Cfg(), "t")


# ══ MPA / HMA: the agent operates MemPalace (2026-10-07). Planner, gates, driver fold, report ═══════════════════════════════════════════════════════════

import math as _math  # noqa: E402


def test_the_default_arm_list_and_the_execution_order_include_mpa_and_hma():
    assert bakeoff.Cfg().arms == ("H1", "H2", "HM", "MPA", "HMA", "ZMA", "H0") == measure.ARM_ORDER
    assert measure.SEEDS_PER_ARM["MPA"] == 1 and measure.SEEDS_PER_ARM["HMA"] == 1 and measure.SEEDS_PER_ARM["ZMA"] == 1 and measure.SEEDS_PER_ARM["H1"] == 3
    assert gates.ARM_NAMES[-3:] == ("MPA", "HMA", "ZMA") and gates.MAINTAINED == ("H1", "H2") and set(gates.ONE_SEED_ARMS) == {"HM", "MPA", "HMA", "ZMA"}
    assert measure.CAP_PLANNED["MPA"] == ("exact_words", "reflection", "multi_hop", "protocol") == measure.CAP_PLANNED["HMA"]
    assert measure.CAP_CUT["MPA"] == () and measure.cap_extra_min("MPA") == 0.0 and measure.cap_extra_min("HMA") == 0.0
    assert measure.PHASE_MIN["latency"]["MPA"] == 0.0 and measure.PHASE_MIN["slot"]["HMA"] == 0.0


def test_the_mpa_cells_minutes_are_computed_from_model_calls_in_one_constants_block(monkeypatch):
    assert measure.MPA_CALLS == {"protocol": 85, "exact_words": 32, "multi_hop": 36, "reflection": 86, "behaviour": 50, "closet": 12} and measure.MPA_S_PER_CALL == 3.0
    assert measure.HMA_CALLS == {**measure.MPA_CALLS, "observations": 60}
    assert measure.mpa_cells_min() == _math.ceil(301 * 3.0 / 60.0 * 2) / 2.0 == measure.PHASE_MIN["mpa_cells"]       # round UP to the half minute
    assert measure.hma_cells_min() == _math.ceil((361 * 3.0 + measure.CONSOLIDATE_S) / 60.0 * 2) / 2.0 == measure.PHASE_MIN["hma_cells"]
    b = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=Path("/x")))
    assert b.fixed_min("MPA") == measure.mpa_cells_min() and b.fixed_min("HMA") == measure.hma_cells_min()
    monkeypatch.setattr(measure, "MPA_S_PER_CALL", 6.0)                                                          # tune the one number: the minutes follow
    assert measure.mpa_cells_min() == _math.ceil(301 * 6.0 / 60.0 * 2) / 2.0 and b.fixed_min("MPA") == measure.mpa_cells_min()


def test_the_dry_plan_prints_the_mpa_and_hma_rows_the_constants_and_what_each_costs_and_cut(box):
    w = make_window(box, FakeHost(box), dry=True)
    measure.dry_plan(w)
    out = "\n".join(w.logs)
    assert "MPA cells on the clone brain: protocol 85, exact_words 32, multi_hop 36, reflection 86, behaviour 50, closet 12 = 301 model calls x 3 s" in out
    assert "HMA cells on the clone brain:" in out and "observations 60" in out and "Hindsight consolidation" in out
    assert "MPA seed 1 (" in out and "HMA seed 1 (" in out and "store-tier cells" in out
    assert out.index("HM seed 1") < out.index("MPA cells on the clone brain") < out.index("MPA seed 1") < out.index("HMA cells on the clone brain") < out.index("HMA seed 1") < out.index("H0 seed 1")
    assert "MPA_CALLS = {" in out and "MPA_S_PER_CALL = 3 s" in out and "HMA_CALLS = {" in out
    assert "MPA runs L0 only (no 100/300 filler: each filler turn is a brain call)" in out
    assert "WHAT MPA COSTS AND WHAT WAS CUT: MPA adds " in out and "WHAT HMA COSTS AND WHAT WAS CUT: HMA adds " in out
    assert "the cut order when the plan is over is H0's box, then HM's, then H2's, never H1's seeds" in out
    assert "MPA recall latency" not in out and "MPA brain-slot" not in out                      # no latency / slot phases: their numbers come from their own cells


def test_the_cost_sentence_names_exactly_the_boxes_that_shrank_and_h1_is_untouched(box):
    cfg = bakeoff.Cfg(bakeoff_dir=box)
    store = [c for c in spec.load_cells() if c.tier == "store"]
    with_b = measure.plan_budget(cfg, store)
    without = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box, arms=("H1", "H2", "HM", "HMA", "H0")), store)
    assert with_b.box_min["H1"] == without.box_min["H1"] and with_b.seeds["H1"] == 3 and with_b.extra_min["H1"] == without.extra_min["H1"]       # H1 keeps 3 seeds and its box
    changed = {a for a in without.arms if with_b.box_min[a] != without.box_min[a]}
    assert "H1" not in changed
    for a in without.arms:                                                                       # everything that is not a lower arm's BOX is identical with and without MPA
        assert with_b.fixed_min(a) == without.fixed_min(a) and with_b.extra_min[a] == without.extra_min[a], a
    sentence = measure.cost_sentence(cfg, "MPA", store, with_b)
    for a in changed:
        assert f"{a} box {without.box_min[a]:g} -> {with_b.box_min[a]:g} min" in sentence
    assert ("no other box shrank" in sentence) == (not changed) and "H1's box unchanged" in sentence


def test_when_the_arms_ask_for_more_than_the_cap_holds_the_plan_says_so_and_the_boxes_sit_on_the_floor(box):
    """With the constants as first written (402 + 462 calls x 3 s) the 90-minute cap cannot hold H1 x3 + HM + MPA + HMA: the plan prints the deficit and what would fit."""
    w = make_window(box, FakeHost(box), dry=True)
    measure.dry_plan(w)
    out = "\n".join(w.logs)
    b = measure.plan_budget(w.cfg)
    assert b.slack_min() < 0 and "DOES NOT FIT" in out and f"over by {-b.slack_min():.1f}" in out
    assert all(b.box_min[a] == measure.BOX_FLOOR_MIN for a in b.arms if a != "H1") and b.seeds["H1"] == 3
    head = measure.driver_call_headroom(b)
    assert head is not None and f"~{head} MPA + HMA + ZMA brain calls" in out and head < sum(measure.MPA_CALLS.values()) + sum(measure.HMA_CALLS.values()) + sum(measure.ZMA_CALLS.values())


def test_the_plan_with_mpa_and_hma_fits_a_cap_that_holds_it_and_h1_keeps_three_seeds_and_its_box(box):
    """At 90 minutes even ZERO MPA / HMA brain calls leave almost no headroom (H1's three ceilings + the capability cells are 42.5 of the 77.5 available minutes): the constants as
    first written fit a longer cap, and the plan then shows FITS with H1's three seeds and box untouched."""
    cfg90 = bakeoff.Cfg(bakeoff_dir=box)
    assert measure.driver_call_headroom(measure.plan_budget(cfg90)) < 100                         # the 90-minute headroom, in calls: next to nothing
    w = make_window(box, FakeHost(box), dry=True, cap_min=170.0)
    b = measure.plan_budget(w.cfg)
    assert b.slack_min() >= 0 and b.total_min() <= b.avail_min <= w.cfg.cap_min - w.cfg.reserve_min - measure.TAIL_MIN
    assert b.seeds["H1"] == 3 and b.arms == ("H1", "H2", "HM", "MPA", "HMA", "ZMA", "H0") and all(b.box_min[a] >= measure.BOX_FLOOR_MIN for a in b.arms)
    assert b.box_min["H1"] == measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box, arms=HM_ERA, cap_min=170.0)).box_min["H1"]
    measure.dry_plan(w)
    assert any(m.startswith("FITS: slack +") for m in w.logs) and not any("DOES NOT FIT" in m for m in w.logs)


def test_the_weights_shed_h0_first_then_hm_then_h2_and_never_h1(box):
    w = measure.LOWER_WEIGHTS
    assert w["H0"] < w["MPA"] <= w["HM"] < w["H2"] and w["HMA"] == w["MPA"]
    b6 = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box, arms=("H1", "H2", "HM", "MPA", "HMA", "H0"), cap_min=240.0))     # a long cap: spare minutes split by the weights
    assert b6.box_min["H2"] >= b6.box_min["HM"] >= b6.box_min["MPA"] >= b6.box_min["H0"] and b6.box_min["H2"] > b6.box_min["H0"]


def test_an_mpa_arm_runs_the_ordinary_cells_it_declares_capabilities_for(box):
    from zmb.arms.mempalace_agent import MemPalaceAgentArm
    b = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box))
    from zmb import cells as cellmod
    ordinary = [c for c in spec.load_cells() if c.tier == "store" and c.axis not in measure.CAP_AXES]
    assert b.runnable_mpa == sum(1 for c in ordinary if cellmod.required_capabilities(c) <= set(MemPalaceAgentArm.capabilities)) and 0 < b.runnable_mpa <= b.runnable
    assert b.runnable_for("MPA") == b.runnable_mpa and b.cells_in_box("MPA") == int(b.box_min["MPA"] * 60.0 / measure.S_PER_CELL["MPA"])


# ── the gate items ────────────────────────────────────────────────────────────

MPA_IDS = ("M4.fire_when_needed.mempalace5", "M4.quiet_when_not_needed.mempalace5", "M4.cite_precision.mempalace5", "M4.idk_when_silent.mempalace5",
           "MPA-T1.schema", "MPA-B2.supersede", "MPA-F1.forget.t0", "MPA-F2.forget.t6min", "MPA-A1.authority", "MPA-I1.guest", "MPA-C1.closet", "MPA-G0.rss")


def mpa_m(verdicts=None, **over):
    """A measure dict in which every MPA item PASSES; ``verdicts`` overrides cell verdicts by id."""
    verdicts = verdicts or {}
    cells = [{"id": i, "verdict": verdicts.get(i, "PASS"), "evidence": {}} for i in MPA_IDS]
    m = {"mpa_cells": {"summary": {"pass": len(cells), "graded": len(cells), "fail": [], "sanity_fail": [], "skipped": [], "not_instrumented": [], "controls_checked": 6,
                                   "controls_mode": "real", "targets_failing": [], "reflective_tier": "real"}, "cells": cells},
         "brain": {"model_calls": 300, "prompt_tokens_max": 3000, "model_s_total": 900.0, "tool_calls": 120, "tool_calls_valid": 118, "searched_before_answer": [18, 20],
                   "supersede": {"correct": 9, "n": 10, "wrong": 1}},
         "mpa_driver": {"server_rss_steady_mb": 120.0, "server_rss_peak_mb": 180.0, "servers": 3, "pss_before_mb": 60.0, "peak_rss_mb": 100.0},
         "prompt_fits": True, "prompt_detail": "max prompt 3000 + 2048 < 8192", "mpa_glue_lines": 800}
    m.update(over)
    return m


MPA_ITEMS = {"mpa_cells_zero_violations", "mpa_cells_all_ran", "mpa_controls_red", "mpa_G0_server_rss", "mpa_G1_tool_call_validity", "mpa_G1_search_before_answer",
             "mpa_G1_supersede_correct", "mpa_G1_prompt_fits", "mpa_G2_floors", "mpa_G3_glue_lines"}


def test_the_mpa_thresholds_are_pre_registered():
    r = gates.RULE
    assert (r["mpa_tool_validity_min"], r["mpa_tool_calls_min"], r["mpa_supersede_min"], r["mpa_supersede_wrong_max"], r["mpa_supersede_n_min"]) == (0.95, 30, 0.80, 2, 10)
    assert "pre-registered 2026-10-07 before any MPA run" in (REPO / "scripts/perf/zmb/bakeoff_gates.py").read_text()
    assert (r["steady_rss_mb"], r["burst_rss_mb"], r["layer_lines_max"]) == (600.0, 900.0, 1000)          # MPA reuses the G0 / G3 numbers


def test_a_clean_mpa_measurement_passes_every_item_and_a_missing_driver_is_not_a_pass():
    g = gates.gate_mpa(mpa_m())
    assert set(g) == MPA_ITEMS and all(v["state"] == gates.PASS for v in g.values()), {k: v for k, v in g.items() if v["state"] != gates.PASS}
    assert "3 server(s)" in g["mpa_G0_server_rss"]["measured"]                                       # information: one MemPalace server per palace
    none = gates.gate_mpa({})
    assert set(none) == {"mpa_cells_ran"} and none["mpa_cells_ran"]["state"] == gates.NA


def test_mpa_cells_red_skipped_and_controls():
    red = mpa_m(verdicts={"MPA-F1.forget.t0": "FAIL"})
    red["mpa_cells"]["summary"]["fail"] = ["MPA-F1.forget.t0"]
    g = gates.gate_mpa(red)
    assert g["mpa_cells_zero_violations"]["state"] == gates.FAIL and "MPA-F1.forget.t0" in g["mpa_cells_zero_violations"]["measured"] and g["mpa_G2_floors"]["state"] == gates.FAIL
    sk = mpa_m(verdicts={"MPA-A1.authority": "SKIP"})
    sk["mpa_cells"]["summary"]["skipped"] = ["MPA-A1.authority"]
    g = gates.gate_mpa(sk)
    assert g["mpa_cells_all_ran"]["state"] == gates.FAIL and g["mpa_G2_floors"]["state"] == gates.NA                        # a skipped floor is not a pass either
    ni = mpa_m()
    ni["mpa_cells"]["summary"]["not_instrumented"] = ["MPA-F1.forget.t0"]
    assert gates.gate_mpa(ni)["mpa_controls_red"]["state"] == gates.FAIL
    nc = mpa_m()
    nc["mpa_cells"]["summary"]["controls_checked"] = 0
    assert gates.gate_mpa(nc)["mpa_controls_red"]["state"] == gates.NA


@pytest.mark.parametrize("steady,peak,state", [(600.0, 900.0, gates.PASS), (600.1, 900.0, gates.FAIL), (100.0, 900.1, gates.FAIL), (None, 100.0, gates.NA), (100.0, None, gates.NA)])
def test_mpa_g0_server_rss(steady, peak, state):
    m = mpa_m(mpa_driver={"server_rss_steady_mb": steady, "server_rss_peak_mb": peak, "servers": 2})
    assert gates.gate_mpa(m)["mpa_G0_server_rss"]["state"] == state


@pytest.mark.parametrize("calls,valid,state", [(100, 94, gates.FAIL), (100, 95, gates.PASS), (29, 29, gates.NA), (30, 30, gates.PASS), (30, 28, gates.FAIL), (None, None, gates.NA)])
def test_mpa_g1_tool_call_validity(calls, valid, state):
    m = mpa_m()
    m["brain"].update({"tool_calls": calls, "tool_calls_valid": valid})
    g = gates.gate_mpa(m)["mpa_G1_tool_call_validity"]
    assert g["state"] == state and (calls != 29 or "too few calls to judge" in g["measured"])


@pytest.mark.parametrize("verdict,state", [("PASS", gates.PASS), ("FAIL", gates.FAIL), ("SKIP", gates.NA), (None, gates.NA)])
def test_mpa_g1_search_before_answer_reads_the_protocol_cells_verdict(verdict, state):
    m = mpa_m(verdicts={"M4.fire_when_needed.mempalace5": verdict} if verdict else {})
    if verdict is None:
        m["mpa_cells"]["cells"] = [c for c in m["mpa_cells"]["cells"] if not c["id"].startswith("M4.fire")]
    assert gates.gate_mpa(m)["mpa_G1_search_before_answer"]["state"] == state


@pytest.mark.parametrize("correct,n,wrong,state", [(8, 10, 2, gates.PASS), (7, 10, 3, gates.FAIL), (9, 10, 3, gates.FAIL), (9, 9, 0, gates.NA), (None, None, 0, gates.NA)])
def test_mpa_g1_supersede(correct, n, wrong, state):
    m = mpa_m()
    m["brain"]["supersede"] = {"correct": correct, "n": n, "wrong": wrong} if n is not None else {}
    assert gates.gate_mpa(m)["mpa_G1_supersede_correct"]["state"] == state


def test_mpa_prompt_fit_floors_and_glue_lines():
    assert gates.gate_mpa(mpa_m(prompt_fits=False))["mpa_G1_prompt_fits"]["state"] == gates.FAIL and gates.gate_mpa(mpa_m(prompt_fits=None))["mpa_G1_prompt_fits"]["state"] == gates.NA
    for fam in ("MPA-F", "MPA-A", "MPA-I"):
        ids = [i for i in MPA_IDS if i.startswith(fam)]
        assert gates.gate_mpa(mpa_m(verdicts={ids[0]: "FAIL"}))["mpa_G2_floors"]["state"] == gates.FAIL, fam
        assert gates.gate_mpa(mpa_m(verdicts={ids[0]: "SKIP"}))["mpa_G2_floors"]["state"] == gates.NA, fam
    assert gates.gate_mpa(mpa_m(mpa_glue_lines=1000))["mpa_G3_glue_lines"]["state"] == gates.PASS and gates.gate_mpa(mpa_m(mpa_glue_lines=1001))["mpa_G3_glue_lines"]["state"] == gates.FAIL
    assert gates.gate_mpa(mpa_m(mpa_glue_lines=None))["mpa_G3_glue_lines"]["state"] == gates.NA
    assert measure.mpa_glue_lines() >= 0 and measure.hma_glue_lines() >= measure.mpa_glue_lines()


def hma_m(verdicts=None, **over):
    m = mpa_m(verdicts, rss={"steady_mb": 520.0, "burst_mb": 700.0}, hma_rss_parts={"stack_steady_mb": 400.0, "mempalace_steady_mb": 120.0})
    m["mpa_cells"]["cells"].append({"id": "MPA-K1.closet_summaries_true", "verdict": "PASS", "evidence": {"precision": 0.96}})
    m.update(over)
    return m


def test_hma_adds_the_observation_veto_the_total_ram_and_the_two_tier_forget():
    g = gates.gate_hma(hma_m())
    assert set(g) == MPA_ITEMS | {"hma_K_reflection", "hma_G0_total_rss", "hma_G2b_two_tier_forget"} and all(v["state"] == gates.PASS for v in g.values())
    assert "HM-like stack 400 MB + MemPalace servers 120 MB" in g["hma_G0_total_rss"]["measured"]
    low = hma_m()
    low["mpa_cells"]["cells"][-1]["evidence"]["precision"] = 0.94
    assert gates.gate_hma(low)["hma_K_reflection"]["state"] == gates.FAIL
    low["mpa_cells"]["cells"] = [c for c in low["mpa_cells"]["cells"] if not c["id"].startswith("MPA-K")]
    assert gates.gate_hma(low)["hma_K_reflection"]["state"] == gates.NA
    low["k1_rows"] = [{"id": "K1.observations_are_true", "verdict": "FAIL"}]                       # the generic K1 row is the fallback
    assert gates.gate_hma(low)["hma_K_reflection"]["state"] == gates.FAIL
    assert gates.gate_hma(hma_m(rss={"steady_mb": 601.0, "burst_mb": 700.0}))["hma_G0_total_rss"]["state"] == gates.FAIL
    assert gates.gate_hma(hma_m(rss={}))["hma_G0_total_rss"]["state"] == gates.NA
    f = hma_m(verdicts={"MPA-F2.forget.t6min": "FAIL"})
    assert gates.gate_hma(f)["hma_G2b_two_tier_forget"]["state"] == gates.FAIL
    one = hma_m()
    one["mpa_cells"]["cells"] = [c for c in one["mpa_cells"]["cells"] if c["id"] != "MPA-F2.forget.t6min"]
    assert gates.gate_hma(one)["hma_G2b_two_tier_forget"]["state"] == gates.NA                     # one tier measured is not both tiers
    assert set(gates.gate_hma({})) == {"mpa_cells_ran"}


def test_a_stand_in_reflective_tier_never_certifies_an_hma_gate():
    """Which tier produced the evidence is recorded; only the window's real Hindsight can pass an HMA item."""
    real = gates.gate_hma(hma_m())
    assert any(v["state"] == gates.PASS for v in real.values()) and not any("not certified" in v["measured"] for v in real.values())
    for tier in ("fake", None):
        m = hma_m()
        if tier is None:
            m["mpa_cells"]["summary"].pop("reflective_tier")
        else:
            m["mpa_cells"]["summary"]["reflective_tier"] = tier
        g = gates.gate_hma(m)
        assert not any(v["state"] == gates.PASS for v in g.values()) and g["hma_G2b_two_tier_forget"]["state"] == gates.NA and "not certified" in g["hma_G2b_two_tier_forget"]["measured"]
    f = hma_m(verdicts={"MPA-F2.forget.t6min": "FAIL"})
    f["mpa_cells"]["summary"]["reflective_tier"] = "fake"
    assert gates.gate_hma(f)["hma_G2b_two_tier_forget"]["state"] == gates.FAIL                       # a red stays red whatever tier produced it


def test_evaluate_arm_adds_the_mpa_and_hma_blocks_and_one_seed_is_incomplete_even_when_every_gate_passes():
    m = good_measure(**mpa_m())
    a = gates.evaluate_arm("MPA", {"zmb-v1": seed_run()}, m)
    assert set(a["gates"]) == {"G0", "G1", "G2", "G3", "MPA"} and all(s == gates.PASS for s in a["gate_states"].values()), a["gate_states"]
    assert a["verdict"] == "INCOMPLETE" and a["seeds"]["state"] == gates.NA                         # every gate PASSES; ONE seed by design: never adoptable
    h = gates.evaluate_arm("HMA", {"zmb-v1": seed_run()}, good_measure(**hma_m()))
    assert "HMA" in h["gates"] and h["verdict"] == "INCOMPLETE" and all(s == gates.PASS for s in h["gate_states"].values())
    three_seeds = gates.evaluate_arm("MPA", three(), m)
    assert three_seeds["verdict"] == "PASSES_BUILT_GATES"                                           # the verdict logic is the existing one: only the seed count holds it back
    red = mpa_m(verdicts={"MPA-F1.forget.t0": "FAIL"})
    red["mpa_cells"]["summary"]["fail"] = ["MPA-F1.forget.t0"]
    assert gates.evaluate_arm("MPA", {"zmb-v1": seed_run()}, good_measure(**red))["verdict"] == "NOT_ADOPTABLE"


def test_decide_never_lets_a_one_seed_arm_win_and_mpa_beats_the_maintained_candidate_or_nothing():
    z0 = gates.aggregate_axes({"s": {"axes": cap(hops=(5, 20))}})
    strong = cap(exact=(20, 20), hops=(20, 20))
    h1 = gates.evaluate_arm("H1", three(axes=cap(hops=(5, 20))), good_measure())               # H1 ties Z0
    mpa = gates.evaluate_arm("MPA", three(axes=strong), good_measure(**mpa_m()))                # a hypothetical MPA with three clean seeds and two wins
    assert mpa["verdict"] == "PASSES_BUILT_GATES"
    d = gates.decide({"H1": h1, "MPA": mpa}, z0)
    assert d["winner"] == "H1" and d["verdict"] in ("ADOPT_ON_TIE", "KEEP_Z0") and "MPA" not in d["adoptable"]
    d2 = gates.decide({"MPA": mpa, "HMA": gates.evaluate_arm("HMA", three(axes=strong), good_measure(**hma_m()))}, z0)
    assert d2["winner"] is None and d2["verdict"] == "KEEP_Z0" and d2["adoptable"] == []              # the maintained candidates did not pass: nothing wins by default
    assert d["compare"]["MPA"]["J"]["beats"] and d["compare"]["MPA"]["L"]["beats"]                  # the report still shows what MPA beat, for the owner


# ── the driver run: the argv, the fold, the report ────────────────────────────

def _subset():
    return [c for c in spec.load_cells() if c.id in SUBSET]


def canned_mpa(cells, *, arm="MPA", verdicts=None, **over):
    """What ``mpa_window.py`` would hand back (the generic rows come from the HM doubles; the brain cells and counters are canned)."""
    base = canned_hm(cells)

    def run(ctx, seed, box_s):
        gen = base(ctx, seed, box_s)["generic"]
        cs = [{"id": i, "verdict": (verdicts or {}).get(i, "PASS"), "evidence": {}, "title": f"title of {i}"} for i in MPA_IDS]
        res = {"started": "t", "finished": "t", "library": "mempalace 3.10.0", "model": "gemma-4-E4B", "generic": gen,
               "mpa_cells": {"summary": {"pass": len(cs), "graded": len(cs), "fail": [], "sanity_fail": [], "skipped": [], "not_instrumented": [], "controls_checked": 6,
                                         "controls_mode": "real", "targets_failing": [], "reflective_tier": "real"}, "cells": cs},
               "brain": {"model_calls": 300, "prompt_tokens_max": 3000, "model_s_total": 900.0, "tool_calls": 120, "tool_calls_valid": 118,
                         "searched_before_answer": [18, 20], "supersede": {"correct": 9, "n": 10, "wrong": 1}},
               "driver": {"server_rss_steady_mb": 120.0, "server_rss_peak_mb": 180.0, "pss_before_mb": 60.0, "peak_rss_mb": 100.0, "servers": 3, "server_samples": 40, "tool_ms": {"search": {"n": 5, "p50": 4.0, "p95": 9.0}}},
               "forget_probe": {"t0": {"checked": 2, "resurrected": 0, "kept_others": 1, "how": "store export + recall packet naming her"},
                                "t6": {"checked": 2, "resurrected": 0, "kept_others": 1, "how": "store export + recall packet naming her", "waited_s": 361.0}}}
        if arm == "HMA":
            res["hindsight"] = {"observations": 12, "model_calls": 60}
        res.update(over)
        return res
    return run


def test_without_a_real_delayed_probe_the_t6_forgetting_item_is_na_never_a_pass_from_the_immediate_refile_cell(box, tmp_path, monkeypatch):
    """MPA-F2 is an immediate refile test (no clock, no wait): it must not stand in for the t+6 min result."""
    w, _h = e2e_window(box, tmp_path, monkeypatch, arms=("H1", "MPA"))
    base = canned_mpa(_subset())

    def no_probe(ctx, seed, box_s):
        res = base(ctx, seed, box_s)
        res["forget_probe"] = {"unmeasured": "start failed: x"}
        return res
    w.mpa_runner = no_probe
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    mm = art["measure"]["MPA"]
    assert "t6" not in mm["forgetting"] and mm["forgetting"]["t0"]["resurrected"] == 0
    assert art["arms"]["MPA"]["gates"]["G2"]["forget_t+6min_no_resurrection"]["state"] == gates.NA and any("t+6 min forgetting probe is unmeasured" in n for n in art["notes"])


def test_the_driver_runs_the_generic_forget_probe_at_a_real_t6_and_it_goes_red_with_the_protections_off():
    sys.path.insert(0, str(REPO / "scripts" / "perf"))
    from zmb import mpa_cells as mc, mpa_window
    for off, red in (((), False), (("ledger_write_check", "tool_floor", "distiller_skip"), True)):
        d = mpa_window.DelayedForget("MPA", lambda off=off: mc.lab_arm("MPA", off))
        assert not d.error and d.probe.t6 is None
        d.poll()
        assert d.probe.t6 is None                                                   # not due: the wall clock has not run six minutes
        d.probe.t_forget -= 361.0                                                    # six minutes of wall clock later
        out = d.finish(lambda: None)
        assert out["t6"]["waited_s"] >= 361.0 and (out["t6"]["resurrected"] > 0) is red and out["t0"]["resurrected"] == 0
    d = mpa_window.DelayedForget("MPA", lambda: (_ for _ in ()).throw(RuntimeError("no palace")))
    assert "start failed" in d.error and d.finish(lambda: None) == {"unmeasured": d.error}                     # a probe that cannot run is unmeasured, never a pass


def test_the_mpa_server_rss_gate_reads_na_when_the_sampler_saw_no_server():
    from zmb import mpa_window
    s = mpa_window.ServerSampler()
    assert s.summary()["server_rss_steady_mb"] is None and s.summary()["server_rss_peak_mb"] is None and s.summary()["server_samples"] == 0
    for fn, extra in ((gates.gate_mpa, {}), (gates.gate_zma, {"pss_added_mb": 90.0})):
        m = mpa_m()
        m["mpa_driver"].update({"server_rss_steady_mb": 0.0, "server_rss_peak_mb": 0.0, "server_samples": 0, **extra})
        g = fn(m)
        assert g["mpa_G0_server_rss" if fn is gates.gate_mpa else "zma_G0_total_rss"]["state"] == gates.NA, fn.__name__
        m["mpa_driver"].update({"server_rss_steady_mb": 120.0, "server_rss_peak_mb": 180.0, "server_samples": 40})
        assert fn(m)["mpa_G0_server_rss" if fn is gates.gate_mpa else "zma_G0_total_rss"]["state"] == gates.PASS


def test_the_mpa_driver_is_run_like_hm_through_mp_run_sh_with_the_clone_brains_url_and_the_shared_embedder(box):
    import types
    seen = {}

    class H(FakeHost):
        def run(self, argv, timeout=60.0, mutating=True, env=None):
            if argv[:1] == ["bash"] and "mp_run.sh" in " ".join(argv):
                seen.update(argv=list(argv), env=env or {}, timeout=timeout)
            return super().run(argv, timeout, mutating, env)
    host = H(box)
    w = make_window(box, host, measure_fn=lambda win: {"ok": 1})
    ctx = types.SimpleNamespace(cfg=w.cfg, host=host, win=w)
    r = measure.real_mpa_runner(ctx, "zmb-v1", 120.0)
    a = seen["argv"]
    assert a[2].endswith("scripts/perf/zmb/mpa_window.py") and a[a.index("--out") + 1].endswith("mpa-t1.json") and a[a.index("--clone-url") + 1] == "http://127.0.0.1:11500"
    assert a[a.index("--seed") + 1] == "zmb-v1" and a[a.index("--box-s") + 1] == "120" and "--arm" not in a and "--hindsight-url" not in a and "--smoke" not in a
    assert seen["env"]["ZMB_HM_EMBEDDER_URL"] == "http://127.0.0.1:11501" and seen["env"]["PYTHONMALLOC"] == "malloc" and seen["env"]["MALLOC_PERTURB_"] == "85"
    assert seen["timeout"] == 120.0 + measure.PHASE_MIN["mpa_cells"] * 60.0 * 2 + 180.0 and "no result" in r["error"]            # nothing wrote the file: an error, not a pass
    seen.clear()
    measure.real_hma_runner(ctx, "zmb-v1", 120.0)
    a = seen["argv"]
    assert a[a.index("--arm") + 1] == "HMA" and a[a.index("--hindsight-url") + 1] == "http://127.0.0.1:18888" and a[a.index("--out") + 1].endswith("hma-t1.json")
    assert seen["timeout"] == 120.0 + measure.PHASE_MIN["hma_cells"] * 60.0 * 2 + 180.0
    w2 = make_window(box, H(box), measure_fn=lambda win: {"ok": 1}, smoke_cells=7, skip_brain_stop=True)
    seen.clear()
    measure.real_mpa_runner(types.SimpleNamespace(cfg=w2.cfg, host=w2.host, win=w2), "zmb-v1", 60.0)
    a = seen["argv"]
    assert a[a.index("--clone-url") + 1] == "http://127.0.0.1:11434" and a[a.index("--smoke") + 1] == "7" and "--quiet-since" in a          # the live port under the test hook


def test_phase_mpa_folds_the_generic_rows_the_protocol_cells_the_brain_and_the_forgetting(box, tmp_path, monkeypatch):
    w, _h = e2e_window(box, tmp_path, monkeypatch, arms=("H1", "MPA"))
    w.mpa_runner = canned_mpa(_subset())
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    md = Path(art["docs_path"]).read_text()
    a = art["arms"]["MPA"]
    assert a["seeds_done"] == 1 and a["verdict"] in ("INCOMPLETE", "NOT_ADOPTABLE") and set(a["gates"]) == {"G0", "G1", "G2", "G3", "MPA"}      # one seed by design: never adopted
    run = art["seed_runs"]["MPA"]["zmb-v1"]
    assert run["cells_ran"] > 0 and run["cells_selected"] == len(SUBSET) and not any(c["id"].startswith("MPA-") for c in run["cells"])
    m4 = [c["id"] for c in run["cells"] if c["id"].startswith("M4.")]
    assert len(m4) == 4 and a["axes"]["protocol_brain"]["n"] == 4 and a["axes"]["protocol_brain"]["pass"] == 4          # the four brain-tier cells feed the derived axis M
    mm = art["measure"]["MPA"]
    assert all("title" not in c for c in mm["mpa_cells"]["cells"]) and mm["brain"]["tool_calls"] == 120 and mm["mpa_driver"]["servers"] == 3
    assert mm["prompt_in_tokens_max"] == 3000 and mm["prompt_fits"] is True and a["gates"]["G0"]["prompt_fits_slot"]["state"] == gates.PASS
    assert mm["forgetting"]["t0"]["resurrected"] == 0 and mm["forgetting"]["t6"]["resurrected"] == 0 and mm["arm_controls"] == "6/6" and "real 361.0 s" in mm["forgetting"]["t6"]["how"]
    assert mm["rss"]["steady_mb"] == round(mm["rss"]["steady_mb"], 1) and "MemPalace servers' own RSS" in mm["rss"]["note"] and mm["rss"]["steady_mb"] >= 120.0
    assert mm["layer_lines"] == mm["mpa_glue_lines"] == measure.mpa_glue_lines()
    assert a["gates"]["MPA"]["mpa_G1_tool_call_validity"]["state"] == gates.PASS and a["gates"]["MPA"]["mpa_G2_floors"]["state"] == gates.PASS
    assert "## MPA:" in md and "| Letter | Axis | Z0 | H1 | MPA |" in md
    assert any("MPA / HMA run ONE seed by design" in n and "CLONE BRAIN" in n and "no 100 / 300 filler" in n for n in art["notes"])
    assert art["decision"]["winner"] != "MPA" and "MPA" in art["compare"]


def test_phase_hma_reports_the_total_rss_the_two_tier_forget_and_the_k1_rows(box, tmp_path, monkeypatch):
    w, _h = e2e_window(box, tmp_path, monkeypatch, extra=("K1.observations_are_true",), arms=("H1", "H2", "HMA"))
    w.hma_runner = canned_mpa([c for c in spec.load_cells() if c.id in SUBSET + ("K1.observations_are_true",)], arm="HMA")
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    h = art["arms"]["HMA"]
    assert set(h["gates"]) == {"G0", "G1", "G2", "G3", "HMA"} and h["verdict"] in ("INCOMPLETE", "NOT_ADOPTABLE") and h["seeds_done"] == 1
    mm = art["measure"]["HMA"]
    assert mm["hindsight"] == {"observations": 12, "model_calls": 60} and mm["hma_rss_parts"]["mempalace_steady_mb"] == 120.0 and mm["k1_rows"]
    assert "HM-like stack" in h["gates"]["HMA"]["hma_G0_total_rss"]["measured"] and h["gates"]["HMA"]["hma_G2b_two_tier_forget"]["state"] == gates.PASS
    assert h["gates"]["HMA"]["hma_K_reflection"]["state"] == gates.NA and "K1.observations_are_true" in h["gates"]["HMA"]["hma_K_reflection"]["measured"]       # the generic K1 row (a SKIP on the double): a skip is not a pass
    assert measure.mpa_glue_lines() <= mm["layer_lines"]


@pytest.mark.parametrize("res,note", [({"skipped": "the real MemPalace library is not importable"}, "MPA did not run"), ({"error": "mpa_window.py produced no result (rc=1)"}, "MPA did not run")])
def test_an_mpa_driver_that_did_not_run_is_reported_not_passed(box, tmp_path, monkeypatch, res, note):
    w, _h = e2e_window(box, tmp_path, monkeypatch, arms=("H1", "MPA"))
    w.mpa_runner = lambda ctx, seed, box_s: res
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    assert art["arms"]["MPA"]["seeds_done"] == 0 and art["arms"]["MPA"]["verdict"] == "INCOMPLETE" and art["arms"]["MPA"]["gates"]["MPA"]["mpa_cells_ran"]["state"] == gates.NA
    assert any(note in n for n in art["notes"])


def test_an_mpa_driver_stopped_by_a_voice_turn_aborts_the_window_and_a_partial_result_is_kept(box, tmp_path, monkeypatch):
    w, _h = e2e_window(box, tmp_path, monkeypatch, arms=("H1", "MPA"))
    w.mpa_runner = lambda ctx, seed, box_s: {"aborted": "a voice turn happened after the window started: stopping (the live brain is shared)"}
    with pytest.raises(bakeoff.Aborted, match="voice turn"):
        measure.measure(w)
    assert json.loads((box / "run-t1.json").read_text())["meta"]["aborted"].startswith("Aborted: a voice turn")
    w2, _h2 = e2e_window(box, tmp_path, monkeypatch, arms=("H1", "MPA"))                         # stopped EARLY but with cells: folded, with a note
    full = canned_mpa(_subset(), error="the brain stopped answering")
    w2.mpa_runner = full
    measure.measure(w2)
    art = json.loads((box / "run-t1.json").read_text())
    assert art["arms"]["MPA"]["seeds_done"] == 1 and any("MPA stopped early: the brain stopped answering" in n for n in art["notes"])


def test_the_sampler_watches_the_mpa_driver_too_and_the_phases_know_the_new_arms(box):
    s = _sampler_over(box, '0 0 192.168.1.218:5 1.2.3.4:443 users:(("python",pid=777,fd=9))\n', pgrep_out="777\n")
    assert s.nonloopback == {"1.2.3.4"} and "mpa_window" in (REPO / "scripts/perf/zmb/bakeoff_measure.py").read_text()
    assert measure.phase_arm("MPA:zmb-v1") == "MPA" and measure.phase_arm("HMA:zmb-v1") == "HMA" and measure.phase_arm("reflect:12B@32k") == "shared"


def test_mpa_and_hma_have_no_forget_probe_validity_or_latency_phase_of_their_own(box, tmp_path, monkeypatch):
    w, _h = e2e_window(box, tmp_path, monkeypatch)
    w.mpa_runner = canned_mpa(_subset())
    w.hma_runner = canned_mpa(_subset(), arm="HMA")
    seen = []
    real = measure.phase_latency
    monkeypatch.setattr(measure, "phase_latency", lambda ctx, v: (seen.append(v), real(ctx, v))[1])
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    assert "MPA" not in seen and "HMA" not in seen and "HM" not in seen
    assert art["measure"]["MPA"]["forgetting"] != {} and art["measure"]["MPA"].get("json") is None                    # forgetting from MPA-F cells; no extraction JSON phase for the verbatim arm


# ══ the REFLECTION phase: K only, against a restarted clone (variants, never contest entrants) ═══════════════════════════════════════════════════════

PARKED_12B = textwrap.dedent("""\
    # /home/zoe/.config/systemd/user/llama-server-12b-deepbrain.service.disabled
    [Service]
    LimitMEMLOCK=infinity
    MemorySwapMax=0
    MemoryLow=8G
    Environment=LD_LIBRARY_PATH=/home/zoe/llama.cpp/build-jetson-new/bin
    ExecStart=/home/zoe/llama.cpp/build-jetson-new/bin/llama-server \\
      --model /home/zoe/models/gemma4-12b-qat/gemma-4-12b-it-qat-q4_0.gguf \\
      --mmproj /home/zoe/models/gemma4-12b-qat/mmproj.gguf \\
      --no-mmproj-offload \\
      --host 0.0.0.0 \\
      --port 11434 \\
      --ctx-size 8192 \\
      --parallel 2 \\
      --cache-type-k q8_0 \\
      --cache-type-v q8_0 \\
      --flash-attn on \\
      --jinja \\
      --chat-template-kwargs '{"enable_thinking":false}' \\
      --mlock \\
      --n-gpu-layers 99 \\
      --metrics
    """)
DEEP = "/x/llama-server-12b-deepbrain.service.disabled"
BIN12 = "/home/zoe/llama.cpp/build-jetson-new/bin/llama-server"
MODEL12 = "/home/zoe/models/gemma4-12b-qat/gemma-4-12b-it-qat-q4_0.gguf"


class ReflectHost(FakeHost):
    """The fake host plus the parked 12B unit's text, a set of files that exist, and a MemAvailable that drops while a BIG model is the last one started."""

    def __init__(self, tmp, *, parked=PARKED_12B, exist=(BIN12, MODEL12), low_when=None, **kw):
        kw.setdefault("mem", lambda h: 20000.0)                                                      # room for the 12B pair's preflight unless a test says otherwise
        super().__init__(tmp, **kw)
        self.parked, self.exist, self.low_when = parked, set(exist), low_when
        if low_when:
            self.mem = lambda h: 500.0 if h.big_is_up() else 3000.0

    def starts(self):
        return [c for c in self.joined() if c.startswith("systemd-run")]

    def big_is_up(self):
        st = self.starts()
        return bool(st) and self.low_when in st[-1]

    def read(self, path):
        return self.parked if path == DEEP else super().read(path)

    def exists(self, path):
        return path in self.exist

    def file_size(self, path):
        return 6_975_877_728 if path == MODEL12 else None


def reflect_env(box, tmp_path, monkeypatch, host, *, arms=("H1", "H2", "HMA"), **cfg_kw):
    w, _h = e2e_window(box, tmp_path, monkeypatch, extra=("K1.observations_are_true", "K2.thread_recall"), host=host, arms=arms, deep_unit=DEEP, **cfg_kw)
    w.unit_text = LIVE_UNIT
    w.hma_reflect_runner = lambda ctx, seed, box_s, pair: {"reflect": {"k_cells": [{"id": "MPA-K1.closet", "verdict": "PASS", "evidence": {"precision": 0.97, "items": [18, 20]}},
                                                                                  {"id": "MPA-K2.thread", "verdict": "PASS", "evidence": {"items": [4, 5]}}],
                                                                        "wall_s": 120.0, "model_calls": 70, "tool_calls": 50, "tool_calls_valid": 49, "prompt_tokens_max": 20000,
                                                                        "model": "m", "closet": {"processed": 5, "failed": 0, "tokens": 9000}}}
    cells = spec.load_cells()
    ctx = measure.Ctx(w, None)
    ctx.arm_controls_ok = True
    return w, ctx, [c for c in cells if c.tier == "store"], {c.id: c for c in cells}


INST = lambda seed: {"ok": True, "lab_controls_red": "9/9"}  # noqa: E731


def test_the_12b_clone_is_generated_from_the_parked_unit_with_only_the_documented_overrides():
    spec12 = bakeoff.deep_clone_command(PARKED_12B, HOME, 11500, 32768)
    live = bakeoff.parse_unit(PARKED_12B, HOME)["argv"]
    argv = spec12["argv"]
    assert argv[0] == BIN12 and spec12["binary"] == BIN12 and spec12["model_path"] == MODEL12 and spec12["model"] == "gemma-4-12b-it-qat-q4_0.gguf"
    assert "--mmproj" not in argv and "--no-mmproj-offload" not in argv and MODEL12.replace("gemma-4-12b-it-qat-q4_0.gguf", "mmproj.gguf") not in argv
    for flag, value in (("--host", "127.0.0.1"), ("--port", "11500"), ("--ctx-size", "32768"), ("--parallel", "1")):
        assert argv[argv.index(flag) + 1] == value
    rest = [t for t in live if t not in ("--no-mmproj-offload",)]
    i = rest.index("--mmproj")
    del rest[i:i + 2]                                                                              # the only removals
    for flag, value in (("--host", "127.0.0.1"), ("--port", "11500"), ("--ctx-size", "32768"), ("--parallel", "1")):
        rest[rest.index(flag) + 1] = value
    assert argv == rest                                                                            # and NOTHING else changed: cache types, flash-attn, jinja, template kwargs, mlock, gpu layers
    for kept in ("--cache-type-k", "--cache-type-v", "--flash-attn", "--jinja", "--chat-template-kwargs", "--mlock", "--n-gpu-layers", "--metrics"):
        assert kept in argv
    assert spec12["env"] == {"LD_LIBRARY_PATH": "/home/zoe/llama.cpp/build-jetson-new/bin"} and spec12["props"]["MemorySwapMax"] == "0"
    with pytest.raises(bakeoff.Refused):
        bakeoff.deep_clone_command("[Service]\nExecStart=/bin/llama-server --port 1\n", HOME, 11500, 32768)          # no --model: refused, never guessed


def test_the_4b_clone_gets_a_big_context_only_in_the_reflection_restart():
    plain = bakeoff.clone_command(LIVE_UNIT, HOME, 11500)
    big = bakeoff.clone_command(LIVE_UNIT, HOME, 11500, 32768)
    assert plain["argv"][plain["argv"].index("--ctx-size") + 1] == "8192" and big["argv"][big["argv"].index("--ctx-size") + 1] == "32768"
    assert [a for a, b in zip(plain["argv"], big["argv"]) if a != b] == ["8192"] and "--swa-full" in big["argv"] and ("8192", "32768") in big["diff"]
    assert bakeoff.Cfg().reflect_ctx == 32768 and bakeoff.Cfg(reflect_ctx=0).reflect_ctx == 0 and "BAKEOFF_REFLECT_CTX" in (REPO / "scripts/perf/zmb/bakeoff.py").read_text()


def test_the_reflection_phase_restarts_the_clone_big_runs_the_four_variants_and_puts_the_live_context_back(box, tmp_path, monkeypatch):
    host = ReflectHost(box)
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host)
    measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    starts = host.starts()
    assert len(starts) == 3 and "--ctx-size 32768" in starts[0] and "--model" in starts[0] and "gemma-4-E4B" in starts[0]
    assert "--ctx-size 32768" in starts[1] and MODEL12 in starts[1] and "--mmproj" not in starts[1] and "--host 127.0.0.1" in starts[1] and "--port 11500" in starts[1]
    assert "--ctx-size 8192" in starts[2] and "32768" not in starts[2]                               # and back to the live context at the end
    assert not any("llama-server.service" in c for c in host.mutating_cmds()), [c for c in host.mutating_cmds() if "llama-server.service" in c]                       # the live unit is never stopped, started or edited by the phase
    assert not any(c.startswith(("systemctl --user edit", "systemctl --user enable", "systemctl --user set-property")) or "deepbrain" in c for c in host.mutating_cmds())
    r = ctx.reflect
    assert set(r["variants"]) == {"H2@32k", "HMA@32k", "H2@12B", "HMA@12B"} and all(p["status"] == "ran" for p in r["pairs"].values())
    assert r["pairs"]["12B@32k"]["model"] == "gemma-4-12b-it-qat-q4_0.gguf" and r["pairs"]["12B@32k"]["clone_pss_mb"] == 200.0 and r["clone_pss_8k_mb"] == 200.0
    v = r["variants"]["HMA@12B"]
    assert (v["tool_calls"], v["tool_calls_valid"], v["prompt_tokens_max"], v["wall_s"]) == (50, 49, 20000, 120.0) and v["closet"]["processed"] == 5 and v["items"] == {"pass": 22, "n": 25}
    assert v["k1_veto"] is False and r["variants"]["H2@32k"]["tool_calls"] is None and r["variants"]["H2@32k"]["k_cells"]


def test_the_k1_precision_veto_applies_to_the_variants(box, tmp_path, monkeypatch):
    host = ReflectHost(box)
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host)
    w.hma_reflect_runner = lambda ctx, seed, box_s, pair: {"reflect": {"k_cells": [{"id": "MPA-K1.closet", "verdict": "PASS", "evidence": {"precision": 0.90}}], "wall_s": 1.0}}
    measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    assert ctx.reflect["variants"]["HMA@32k"]["k1_veto"] is True and ctx.reflect["variants"]["HMA@12B"]["k1_veto"] is True


def test_a_missing_12b_model_skips_the_pair_with_the_reason_and_never_fakes_it(box, tmp_path, monkeypatch):
    host = ReflectHost(box, exist=(BIN12,))
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host)
    measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    p = ctx.reflect["pairs"]["12B@32k"]["status"]
    assert p.startswith("skipped") and "gemma-4-12b-it-qat-q4_0.gguf does not exist" in p and not any(k.endswith("@12B") for k in ctx.reflect["variants"])
    assert not any(MODEL12 in c for c in host.starts()) and "--ctx-size 8192" in host.starts()[-1] and any("12B@32k" in n and "does not exist" in n for n in ctx.notes)
    host2 = ReflectHost(box, parked="")
    _w, ctx2, store, by_id = reflect_env(box, tmp_path, monkeypatch, host2)
    measure.phase_reflect(ctx2, "zmb-v1", store, by_id, INST)
    assert "missing or empty" in ctx2.reflect["pairs"]["12B@32k"]["status"] and set(ctx2.reflect["variants"]) == {"H2@32k", "HMA@32k"}


def test_memavailable_below_the_floor_once_the_model_is_up_stops_the_pair_and_restores_the_clone(box, tmp_path, monkeypatch):
    host = ReflectHost(box, low_when="--ctx-size 32768")
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host)
    measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    r = ctx.reflect
    assert r["pairs"]["4B@32k"]["status"].startswith("stopped: MemAvailable") and r["pairs"]["12B@32k"]["status"].startswith("skipped: an earlier pair stopped") and r["variants"] == {}
    assert "--ctx-size 8192" in host.starts()[-1] and w.abort_flag is None and any("stopped: MemAvailable" in n for n in ctx.notes)
    host2 = ReflectHost(box, low_when="--ctx-size 32768")                                         # the explicit check after the health poll (the guard saw nothing)
    w2, ctx2, store, by_id = reflect_env(box, tmp_path, monkeypatch, host2)
    w2.guard = lambda: None
    measure.phase_reflect(ctx2, "zmb-v1", store, by_id, INST)
    assert ctx2.reflect["pairs"]["4B@32k"]["status"].startswith("stopped: MemAvailable") and ctx2.reflect["variants"] == {} and "--ctx-size 8192" in host2.starts()[-1]


def test_an_abort_in_the_middle_of_the_reflection_phase_restores_the_live_brain_at_its_normal_context(box, monkeypatch):
    host = ReflectHost(box)

    def phase(win):
        ctx = measure.Ctx(win, None)
        monkeypatch.setattr(measure, "reflect_variant_h2", lambda *a, **k: (_ for _ in ()).throw(bakeoff.Aborted("a voice turn started during the reflection phase")))
        measure.phase_reflect(ctx, "zmb-v1", [], {}, INST)
    w = make_window(box, host, measure_fn=phase, arms=("H1", "H2"), deep_unit=DEEP)
    rc = w.run()
    assert rc == bakeoff.EXIT_ABORTED and restored(host), w.logs[-12:]
    j = host.joined()
    big = max(i for i, c in enumerate(j) if c.startswith("systemd-run") and "--ctx-size 32768" in c)
    assert j.index("systemctl --user start llama-server.service") > big and not (box / "WINDOW_OPEN").exists()
    assert j[big + 1:].count("systemctl --user stop zoe-bakeoff-gemma.service") >= 1 and not any("--ctx-size 32768" in c for c in j[big + 1:] if c.startswith("systemd-run"))
    assert "--ctx-size 8192" in next(c for c in j if "--unit=zoe-bakeoff-gemma" in c)             # the window's own clone never had the big context
    assert not any(("systemctl --user edit" in c or "set-property" in c) for c in j)


def test_the_parked_unit_file_is_only_read_never_written_or_started(box, tmp_path, monkeypatch):
    parked = tmp_path / "llama-server-12b-deepbrain.service.disabled"
    parked.write_text(PARKED_12B)
    before = (parked.read_bytes(), parked.stat().st_mtime_ns)

    class H(ReflectHost):
        def read(self, path):
            return parked.read_text() if path == str(parked) else FakeHost.read(self, path)
    host = H(box)
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host)
    w.cfg.deep_unit = str(parked)
    measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    assert (parked.read_bytes(), parked.stat().st_mtime_ns) == before and set(ctx.reflect["variants"]) == {"H2@32k", "HMA@32k", "H2@12B", "HMA@12B"}
    assert not any(parked.name in c for c in host.joined()) and sorted(p.name for p in tmp_path.glob("*.disabled")) == [parked.name]
    src = (REPO / "scripts/perf/zmb/bakeoff_measure.py").read_text() + (REPO / "scripts/perf/zmb/bakeoff.py").read_text()
    assert "write_text(cfg.deep_unit" not in src and "systemctl\", \"--user\", \"enable" not in src


def test_the_phase_is_off_with_ctx_zero_and_with_the_live_brain_hook_and_without_time(box, tmp_path, monkeypatch):
    host = ReflectHost(box)
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host, reflect_ctx=0)
    measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    assert ctx.reflect is None and host.starts() == [] and measure.plan_budget(w.cfg).reflect_min == {}
    host2 = ReflectHost(box)
    w2, ctx2, store, by_id = reflect_env(box, tmp_path, monkeypatch, host2, skip_brain_stop=True)
    measure.phase_reflect(ctx2, "zmb-v1", store, by_id, INST)
    assert "BAKEOFF_SKIP_BRAIN_STOP" in ctx2.reflect["why"] and host2.starts() == []
    host3 = ReflectHost(box)
    w3, ctx3, store, by_id = reflect_env(box, tmp_path, monkeypatch, host3)
    host3.t += 82 * 60                                                                              # almost no time left behind the tail: the pairs are skipped, with the minutes said
    measure.phase_reflect(ctx3, "zmb-v1", store, by_id, INST)
    assert all(p["status"].startswith("skipped") and "min left behind the tail" in p["status"] for p in ctx3.reflect["pairs"].values()) and host3.starts() == []
    host4 = ReflectHost(box)
    w4, ctx4, store, by_id = reflect_env(box, tmp_path, monkeypatch, host4, arms=("H1", "H2", "H0"))
    measure.phase_reflect(ctx4, "zmb-v1", store, by_id, INST)
    assert set(ctx4.reflect["variants"]) == {"H2@32k", "H2@12B"}                                    # only the arms in the window get a variant


def test_the_reflection_minutes_are_computed_optional_and_taken_from_h0_then_hm(box):
    assert measure.REFLECT_CALLS == {"H2@32k": 60, "HMA@32k": 80, "ZMA@32k": 40, "H2@12B": 60, "HMA@12B": 80, "ZMA@12B": 40} and measure.REFLECT_S_PER_CALL == {"4B@32k": 4.0, "12B@32k": 12.0}
    assert measure.REFLECT_LOAD_MIN == {"4B@32k": 1.5, "12B@32k": 3.0}
    assert measure.reflect_pair_min("4B@32k", ("H2", "HMA")) == _math.ceil((140 * 4.0 / 60.0 + 1.5) * 2) / 2.0
    assert measure.reflect_pair_min("4B@32k", ("H2", "HMA", "ZMA")) == _math.ceil((180 * 4.0 / 60.0 + 1.5) * 2) / 2.0 == measure.PHASE_MIN["reflect32k"]
    assert measure.reflect_pair_min("12B@32k", ("H2", "HMA", "ZMA")) == _math.ceil((180 * 12.0 / 60.0 + 3.0) * 2) / 2.0 == measure.PHASE_MIN["reflect12b"]
    assert measure.reflect_pair_min("4B@32k", ("H1", "H0")) == 0.0
    b = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box))
    nob = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box, reflect_ctx=0))
    assert set(b.reflect_min) == {"4B@32k", "12B@32k"} and b.total_min() == nob.total_min()      # optional: never inside the planned total
    h0_plain, h0_cut = nob.seed_box_s("H0", 20 * 60.0), b.seed_box_s("H0", 20 * 60.0)
    assert h0_cut < h0_plain                                                                        # H0's box pays for the reflection reserve first
    hm_plain, hm_cut = nob.seed_box_s("HM", 70 * 60.0), b.seed_box_s("HM", 70 * 60.0)
    assert hm_cut <= hm_plain and hm_cut >= min(hm_plain, measure.BOX_FLOOR_MIN * 60.0)             # then HM's, never below the minute that keeps HM running
    w = make_window(box, FakeHost(box), dry=True)
    measure.dry_plan(w)
    out = "\n".join(w.logs)
    assert "REFLECTION PHASE (optional" in out and f"--ctx-size {w.cfg.reflect_ctx}" in out and "PARKED unit llama-server-12b-deepbrain.service.disabled" in out and "never edited" in out
    assert "reflection phase, 4B@32k" in out and "reflection phase, 12B@32k" in out and "REFLECT_CALLS" in out


def test_the_measure_end_to_end_reports_the_reflection_variants_beside_the_8k_k_numbers_and_they_never_win(box, tmp_path, monkeypatch):
    host = ReflectHost(box)
    w, _ctx, _store, _by = reflect_env(box, tmp_path, monkeypatch, host, arms=("H1", "H2", "HMA"))
    w.hma_runner = canned_mpa([c for c in spec.load_cells() if c.id in SUBSET + ("K1.observations_are_true", "K2.thread_recall")], arm="HMA")
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    md = Path(art["docs_path"]).read_text()
    assert set(art["reflect"]["variants"]) == {"H2@32k", "HMA@32k", "H2@12B", "HMA@12B"} and set(art["arms"]) == {"H1", "H2", "HMA"} and set(art["compare"]) == set(art["arms"])
    assert "## Reflection at a bigger context (variants, NOT contest entrants)" in md and "| H2@12B |" in md and "| HMA@32k |" in md and "8k K of the same arm" in md
    assert art["decision"]["winner"] not in art["reflect"]["variants"] and any("VARIANTS, never contest entrants" in n for n in art["notes"])
    assert "--ctx-size 8192" in host.starts()[-1]                                                   # the report was written after the clone went back to the live context


# ── the 12B pair's RAM preflight and the owner's listed units ─────────────────────────────────────────────────────────────────────────────────────────

def test_the_12b_ram_arithmetic_is_the_header_measured_one():
    assert measure.REFLECT_12B_MODEL_BYTES == 6_975_877_728 and measure.REFLECT_COMPUTE_MB == 600.0
    assert abs(measure.kv_est_mb(32768) - 486.0) < 2.0                                                # global 268M + SWA 189M elements x 1.0625 B
    assert measure.need_mb_12b(None, 32768, 1200.0) == round(6975.877728 + measure.kv_est_mb(32768) + 600.0 + 1200.0, 0)
    assert measure.need_mb_12b(6_500_000_000, 32768, 1200.0) < measure.need_mb_12b(None, 32768, 1200.0)      # the real file size wins when known
    assert measure.kv_est_mb(8192) < measure.kv_est_mb(32768)
    assert bakeoff.Cfg().reflect_stop_units == () and "BAKEOFF_REFLECT_STOP_UNITS" in (REPO / "scripts/perf/zmb/bakeoff.py").read_text()


def test_the_dry_plan_prints_the_12b_preflight_and_the_margin_with_and_without_the_listed_unit(box):
    w = make_window(box, FakeHost(box), dry=True)
    measure.dry_plan(w)
    out = next(m for m in w.logs if m.startswith("12B PREFLIGHT"))
    need = measure.need_mb_12b(None, w.cfg.reflect_ctx, w.cfg.min_avail_mb)
    assert f"need {need:.0f} MB = model 6976 + KV 486 + compute 600 + floor 1200" in out and "expected: skip unless extra headroom" in out
    assert f"with kokoro-tts.service stopped for the 12B pair (BAKEOFF_REFLECT_STOP_UNITS=kokoro-tts.service, default none) the margin = {8400 + 2300 - need:+.0f} MB" in out
    assert "units stopped for the pair: none" in out and "nothing unlisted is ever stopped" in out
    w2 = make_window(box, FakeHost(box), dry=True, reflect_stop_units=("kokoro-tts.service",))
    measure.dry_plan(w2)
    out2 = next(m for m in w2.logs if m.startswith("12B PREFLIGHT"))
    assert "expected to fit" in out2 and "units stopped for the pair: kokoro-tts.service" in out2


def test_the_12b_pair_is_skipped_with_the_arithmetic_when_the_floor_would_be_breached(box, tmp_path, monkeypatch):
    host = ReflectHost(box, mem=lambda h: 8400.0)                                                    # the box with the 4B stopped: under the 12B's need
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host)
    measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    p = ctx.reflect["pairs"]["12B@32k"]
    need = measure.need_mb_12b(6_975_877_728, 32768, 1200.0)
    assert p["status"].startswith("stopped: the floor would be breached") and f"need {need:.0f} MB = model 6976 + KV 486 + compute 600 + floor 1200" in p["status"]
    assert f"= 8400 MB (margin {8400 - need:+.0f} MB)" in p["status"] and "--ctx-size 8192" in host.starts()[-1]
    assert not any(MODEL12 in c for c in host.starts()) and set(ctx.reflect["variants"]) == {"H2@32k", "HMA@32k"} and ctx.reflect["pairs"]["4B@32k"]["status"] == "ran"


def test_a_listed_unit_is_stopped_only_for_the_12b_pair_and_started_again_and_nothing_else_is_stopped(box, tmp_path, monkeypatch):
    def mem(h):
        j = h.joined()
        stopped = [i for i, c in enumerate(j) if c == "systemctl --user stop kokoro-tts.service"]
        started = [i for i, c in enumerate(j) if c == "systemctl --user start kokoro-tts.service"]
        return 8400.0 + (2300.0 if stopped and (not started or started[-1] < stopped[-1]) else 0.0)
    host = ReflectHost(box, mem=mem)
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host, reflect_stop_units=("kokoro-tts.service",))
    measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    j = host.joined()
    stop, start = j.index("systemctl --user stop kokoro-tts.service"), j.index("systemctl --user start kokoro-tts.service")
    twelve = next(i for i, c in enumerate(j) if c.startswith("systemd-run") and MODEL12 in c)
    assert j.count("systemctl --user stop kokoro-tts.service") == 1 and stop < twelve < start                 # only the 12B pair; back right after it
    assert ctx.reflect["pairs"]["12B@32k"]["status"] == "ran" and "margin +" in ctx.reflect["pairs"]["12B@32k"]["preflight"] and w.stopped_extra == []
    stops = {c.split()[-1] for c in j if c.startswith("systemctl --user stop")}
    assert stops <= {"zoe-bakeoff-gemma.service", "kokoro-tts.service"} and not any("llama-server.service" in c for c in host.mutating_cmds())


def test_a_listed_unit_comes_back_on_an_abort_and_on_the_windows_restore(box, tmp_path, monkeypatch):
    host = ReflectHost(box)
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host, reflect_stop_units=("kokoro-tts.service",))
    calls = []

    def boom(*a, **k):
        calls.append(1)
        if len(calls) >= 2:                                                                        # the 12B pair's H2 variant (one call per pair)
            raise bakeoff.Aborted("a voice turn started while the 12B was up")
        return {"k_cells": [], "items": {"pass": 0, "n": 0}}
    monkeypatch.setattr(measure, "reflect_variant_h2", boom)
    with pytest.raises(bakeoff.Aborted, match="voice turn"):
        measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    j = host.joined()
    assert "systemctl --user stop kokoro-tts.service" in j and j.index("systemctl --user start kokoro-tts.service") > j.index("systemctl --user stop kokoro-tts.service") and w.stopped_extra == []
    host2 = ReflectHost(box)                                                                       # a hard kill between stop and start: the window's restore starts it
    w2 = make_window(box, host2, deep_unit=DEEP, reflect_stop_units=("kokoro-tts.service",))
    w2.stop_extra_units(w2.cfg.reflect_stop_units)
    assert w2.stopped_extra == ["kokoro-tts.service"]
    assert w2.restore() and "systemctl --user start kokoro-tts.service" in host2.joined() and w2.stopped_extra == []
    assert w2.stop_extra_units(()) is None and w2.start_extra_units() is True                          # idempotent, nothing listed = nothing stopped


def _kokoro_after_the_12b_is_unloaded(host):
    """Index check on the command stream: the 12B clone start, the next clone start (the unload: back to the 4B at the live context), and the Kokoro start."""
    j = host.joined()
    i12 = next(i for i, c in enumerate(j) if c.startswith("systemd-run") and MODEL12 in c)
    unload = next(i for i, c in enumerate(j) if i > i12 and c.startswith("systemd-run"))
    start = next(i for i, c in enumerate(j) if "systemctl --user start kokoro-tts.service" in c)
    return i12, unload, start, j[unload]


def test_a_stopped_unit_is_started_only_after_the_12b_is_unloaded_on_the_pass_path_and_on_an_abort(box, tmp_path, monkeypatch):
    """Kokoro was stopped because the 12B pair does not fit beside it: starting it while the 12B holds ~7.5 GB can fail or OOM. Put the start back before the unload and this goes red."""
    host = ReflectHost(box)
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host, reflect_stop_units=("kokoro-tts.service",))
    measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    i12, unload, start, cmd = _kokoro_after_the_12b_is_unloaded(host)
    assert i12 < unload < start and "--ctx-size 8192" in cmd and MODEL12 not in cmd and len(host.starts()) == 3 and w.stopped_extra == []        # one unload, not two
    host2 = ReflectHost(box)
    w2, ctx2, store, by_id = reflect_env(box, tmp_path, monkeypatch, host2, reflect_stop_units=("kokoro-tts.service",))
    calls = []

    def boom(*a, **k):
        calls.append(1)
        if len(calls) >= 2:
            raise bakeoff.Aborted("a voice turn started while the 12B was up")
        return {"k_cells": [], "items": {"pass": 0, "n": 0}}
    monkeypatch.setattr(measure, "reflect_variant_h2", boom)
    with pytest.raises(bakeoff.Aborted, match="voice turn"):
        measure.phase_reflect(ctx2, "zmb-v1", store, by_id, INST)
    i12, unload, start, cmd = _kokoro_after_the_12b_is_unloaded(host2)
    assert i12 < unload < start and "--ctx-size 8192" in cmd and w2.stopped_extra == []


def test_a_clone_that_will_not_unload_keeps_the_unit_stopped_until_the_restore_has_stopped_the_clone(box, tmp_path, monkeypatch):
    host = ReflectHost(box)
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host, reflect_stop_units=("kokoro-tts.service",))
    real = w.restart_clone
    n = []

    def flaky(ctx_size):
        n.append(ctx_size)
        if len(n) == 1:                                                                            # the unload after the 12B pair (the first restart_clone call of the phase)
            raise bakeoff.Aborted("the 4B would not come back")
        return real(ctx_size)
    monkeypatch.setattr(w, "restart_clone", flaky)
    w.stop_extra_units(w.cfg.reflect_stop_units)
    measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    assert "systemctl --user start kokoro-tts.service" not in "\n".join(host.joined()) and w.stopped_extra == ["kokoro-tts.service"]
    assert any("was not unloaded" in x for x in ctx.notes)
    assert w.restore()
    j = host.joined()
    stop_clone = max(i for i, c in enumerate(j) if "systemctl --user stop zoe-bakeoff-gemma.service" in c)
    assert stop_clone < host.index("systemctl --user start kokoro-tts.service") and w.stopped_extra == []


def test_a_listed_unit_that_was_not_active_before_the_window_is_left_alone(box):
    """Restore the host to its pre-window state, never past it: an intentionally stopped Kokoro is not started by the window."""
    host = ReflectHost(box)
    w = make_window(box, host, deep_unit=DEEP, reflect_stop_units=("kokoro-tts.service",))
    host.live_active = False                                                                       # is-active answers "inactive"
    w.stop_extra_units(w.cfg.reflect_stop_units)
    assert w.stopped_extra == [] and "systemctl --user stop kokoro-tts.service" not in "\n".join(host.joined())
    assert w.start_extra_units() is True and w.restore() is not None
    assert "systemctl --user start kokoro-tts.service" not in "\n".join(host.joined())


def test_a_unit_that_never_became_active_stays_listed_so_the_restore_retries_it(box):
    host = ReflectHost(box)
    w = make_window(box, host, deep_unit=DEEP, reflect_stop_units=("kokoro-tts.service",))
    w.stop_extra_units(w.cfg.reflect_stop_units)
    host.live_active = False                                                                       # is-active answers "inactive" for everything
    assert w.start_extra_units() is False and w.stopped_extra == ["kokoro-tts.service"]
    host.live_active = True
    assert w.start_extra_units() is True and w.stopped_extra == []


# ══ ZMA: Zoe's live stack + MemPalace (2026-10-07) ═════════════════════════════════════════════════════════════════════════════════════════════════════

def zma_m(**over):
    m = mpa_m(verdicts={})
    m["mpa_cells"]["cells"] += [{"id": "ZMA-F1.forget.store", "verdict": "PASS", "evidence": {}}, {"id": "ZMA-F2.forget.palace", "verdict": "PASS", "evidence": {}}]
    m["mpa_driver"].update({"pss_added_mb": 90.0, "embedder_shared_mb": 85.0})
    m.update({"zma_hard_violations": [], "zma_hard_skipped": 0})
    m.update(over)
    return m


def test_zma_gate_items_the_z0_floors_unchanged_both_stores_forgetting_and_the_total_ram():
    g = gates.gate_zma(zma_m())
    assert set(g) == MPA_ITEMS | {"zma_G2_floors_unchanged", "zma_forget_both_tiers", "zma_G0_total_rss"} and all(v["state"] == gates.PASS for v in g.values())
    assert "servers 120 + Z0 in-process 90" in g["zma_G0_total_rss"]["measured"] and "shared-embedder saving 85 MB" in g["zma_G0_total_rss"]["measured"]
    assert gates.gate_zma(zma_m(zma_hard_violations=["A1.digest.home"]))["zma_G2_floors_unchanged"]["state"] == gates.FAIL
    assert gates.gate_zma(zma_m(zma_hard_skipped=2))["zma_G2_floors_unchanged"]["state"] == gates.FAIL                 # a hard cell that never ran is not a clean bill
    assert gates.gate_zma(zma_m(zma_hard_violations=None))["zma_G2_floors_unchanged"]["state"] == gates.NA
    f = zma_m()
    f["mpa_cells"]["cells"][-1]["verdict"] = "FAIL"
    assert gates.gate_zma(f)["zma_forget_both_tiers"]["state"] == gates.FAIL
    one = zma_m()
    one["mpa_cells"]["cells"].pop()
    assert gates.gate_zma(one)["zma_forget_both_tiers"]["state"] == gates.NA                                          # one store measured is not both
    d = zma_m()
    d["mpa_driver"].update({"server_rss_steady_mb": 300.0, "pss_added_mb": 301.0})
    assert gates.gate_zma(d)["zma_G0_total_rss"]["state"] == gates.FAIL                                              # 601 MB steady
    d2 = zma_m()
    d2["mpa_driver"].pop("pss_added_mb")
    assert gates.gate_zma(d2)["zma_G0_total_rss"]["state"] == gates.NA and set(gates.gate_zma({})) == {"mpa_cells_ran"}
    a = gates.evaluate_arm("ZMA", {"zmb-v1": seed_run()}, good_measure(**zma_m()))
    assert "ZMA" in a["gates"] and a["verdict"] == "INCOMPLETE" and a["seeds"]["state"] == gates.NA                      # one seed by design


def test_zma_runs_in_the_windows_own_interpreter_not_through_mp_run_sh(box):
    import types
    seen = {}

    class H(FakeHost):
        def run(self, argv, timeout=60.0, mutating=True, env=None):
            if "mpa_window.py" in " ".join(argv):
                seen.update(argv=list(argv), env=env or {}, timeout=timeout)
            return super().run(argv, timeout, mutating, env)
    host = H(box)
    w = make_window(box, host, measure_fn=lambda win: {"ok": 1})
    r = measure.real_zma_runner(types.SimpleNamespace(cfg=w.cfg, host=host, win=w), "zmb-v1", 90.0)
    a = seen["argv"]
    assert a[0] == sys.executable and a[1].endswith("scripts/perf/zmb/mpa_window.py") and "mp_run.sh" not in " ".join(a) and a[a.index("--arm") + 1] == "ZMA"
    assert a[a.index("--out") + 1].endswith("zma-t1.json") and a[a.index("--clone-url") + 1] == "http://127.0.0.1:11500" and a[a.index("--box-s") + 1] == "90"
    assert seen["env"]["ZMB_HM_EMBEDDER_URL"] == "http://127.0.0.1:11501" and seen["timeout"] == 90.0 + measure.PHASE_MIN["zma_cells"] * 60.0 * 2 + 180.0 and "no result" in r["error"]
    assert measure.zma_cells_min() == _math.ceil(180 * 3.0 / 60.0 * 2) / 2.0 == measure.PHASE_MIN["zma_cells"] and measure.ZMA_CALLS == {
        "protocol": 60, "exact_words": 20, "multi_hop": 40, "behaviour": 30, "reflection": 30}


def test_zma_gets_a_normal_box_that_holds_all_axes_and_its_cost_is_stated_with_mpa_and_hma(box):
    from zmb.arms.zma import ZMAArm
    from zmb import cells as cellmod
    b = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box))
    store = [c for c in spec.load_cells() if c.tier == "store"]
    assert b.runnable_zma == sum(1 for c in store if cellmod.required_capabilities(c) <= set(ZMAArm.capabilities)) > b.runnable_mpa                  # ALL axes A-M, not the ordinary ones only
    assert b.fixed_min("ZMA") == measure.zma_cells_min() and b.extra_min["ZMA"] == 0.0 and b.seeds["ZMA"] == 1 and b.box_min["ZMA"] >= measure.BOX_FLOOR_MIN
    assert measure.S_PER_CELL["ZMA"] == 6.5 and measure.LOWER_WEIGHTS["ZMA"] == 1.0 and measure.CAP_PLANNED["ZMA"] == measure.CAP_AXES
    w = make_window(box, FakeHost(box), dry=True)
    measure.dry_plan(w)
    out = "\n".join(w.logs)
    assert "ZMA cells on the clone brain: protocol 60, exact_words 20, multi_hop 40, behaviour 30, reflection 30 = 180 model calls x 3 s" in out
    assert "ZMA seed 1 (" in out and "ALL store-tier cells (axes A-M: its write path has no model call)" in out and "--arm ZMA" in out
    assert "WHAT ZMA COSTS AND WHAT WAS CUT: ZMA adds " in out and "WHAT MPA + HMA + ZMA COST TOGETHER AND WHAT WAS CUT: together they add " in out
    run2 = next(m for m in w.logs if m.startswith("RUN-2 ARM LIST: "))
    assert run2.index("H1 (3 seeds") < run2.index("H2 (") < run2.index("HM (") < run2.index("MPA (") < run2.index("HMA (") < run2.index("ZMA (") < run2.index("H0 (")
    assert out.index("HMA seed 1") < out.index("ZMA cells on the clone brain") < out.index("ZMA seed 1") < out.index("H0 seed 1")
    with_b = measure.plan_budget(w.cfg, store)
    without = measure.plan_budget(bakeoff.Cfg(bakeoff_dir=box, arms=tuple(a for a in w.cfg.arms if a not in ("MPA", "HMA", "ZMA"))), store)
    assert with_b.box_min["H1"] == without.box_min["H1"] and with_b.seeds["H1"] == 3                                                                    # H1 untouched by all three
    assert "ZMA@32k" in out and "ZMA@12B" in out


def test_phase_zma_folds_the_rows_the_hard_violations_the_total_rss_and_names_the_embedder(box, tmp_path, monkeypatch):
    w, _h = e2e_window(box, tmp_path, monkeypatch, arms=("H1", "ZMA"))
    w.zma_runner = canned_mpa(_subset(), arm="ZMA", driver={"server_rss_steady_mb": 120.0, "server_rss_peak_mb": 180.0, "servers": 3, "pss_added_mb": 90.0, "embedder_shared_mb": 85.0})
    measure.measure(w)
    art = json.loads((box / "run-t1.json").read_text())
    md = Path(art["docs_path"]).read_text()
    z = art["arms"]["ZMA"]
    assert set(z["gates"]) == {"G0", "G1", "G2", "G3", "ZMA"} and z["seeds_done"] == 1 and z["verdict"] in ("INCOMPLETE", "NOT_ADOPTABLE") and "## ZMA:" in md
    mm = art["measure"]["ZMA"]
    run = art["seed_runs"]["ZMA"]["zmb-v1"]
    assert mm["zma_hard_violations"] == run["hard_violations"] and mm["zma_hard_skipped"] == run["hard_skipped"]
    assert z["gates"]["ZMA"]["zma_G2_floors_unchanged"]["state"] == (gates.FAIL if run["hard_violations"] or run["hard_skipped"] else gates.PASS)
    assert mm["rss"]["steady_mb"] == 210.0 and mm["rss"]["burst_mb"] == 270.0 and "Z0's in-process delta" in mm["rss"]["note"]
    assert z["gates"]["ZMA"]["zma_G0_total_rss"]["state"] == gates.PASS and mm["zma_embedder"]
    assert any("ZMA = Zoe's live stack" in n and "beating the maintained candidate AND Z0e" in n for n in art["notes"])
    assert art["decision"]["winner"] != "ZMA" and "ZMA" in art["compare"] and "ZMA" not in art["decision"]["adoptable"]


def test_the_zma_reflection_variants_run_through_the_same_driver_with_k_only(box, tmp_path, monkeypatch):
    host = ReflectHost(box)
    w, ctx, store, by_id = reflect_env(box, tmp_path, monkeypatch, host, arms=("H1", "H2", "HMA", "ZMA"))
    seen = []
    w.zma_reflect_runner = lambda c, seed, box_s, pair: (seen.append(pair), {"reflect": {"k_cells": [{"id": "MPA-K1.closet", "verdict": "PASS", "evidence": {"precision": 0.96}}],
                                                                                         "wall_s": 5.0, "tool_calls": 30, "tool_calls_valid": 30, "prompt_tokens_max": 9000}})[1]
    measure.phase_reflect(ctx, "zmb-v1", store, by_id, INST)
    assert set(ctx.reflect["variants"]) == {"H2@32k", "HMA@32k", "ZMA@32k", "H2@12B", "HMA@12B", "ZMA@12B"} and seen == ["4B@32k", "12B@32k"]
    assert ctx.reflect["variants"]["ZMA@12B"]["tool_calls_valid"] == 30 and ctx.reflect["variants"]["ZMA@32k"]["k1_veto"] is False
    argv = measure._mpa_argv(ctx, "ZMA", Path("/x/o.json"), "s", 10.0, ("--reflect-only", "--ctx", "32768"))
    assert argv[0] == sys.executable and "--reflect-only" in argv and argv[argv.index("--arm") + 1] == "ZMA"


def test_decide_never_lets_zma_win_either_even_when_it_beats_z0e_on_two_axes():
    z0 = gates.aggregate_axes({"s": {"axes": cap(hops=(5, 20))}})
    z = gates.evaluate_arm("ZMA", three(axes=cap(exact=(20, 20), hops=(20, 20))), good_measure(**zma_m()))
    d = gates.decide({"ZMA": z}, z0)
    assert d["winner"] is None and d["verdict"] == "KEEP_Z0" and d["compare"]["ZMA"]["J"]["beats"]
