"""The 12B night window (``scripts/night/night_window.py``): the arithmetic, the lever choice, the preflight refusals, the sleep / wake order, the loud failure, the dry run.

Nothing here starts a service, a model or a network connection. Every external command goes through ``Host`` (the bake-off's seam); ``FakeHost`` answers them and the tests run the
REAL step logic, red-before-green on the promises that matter:

    arithmetic = the bake-off's constants       lever choice = the best set that fits       nothing fits -> REFUSE and put everything back
    lock / hour / busy unit / fragmentation -> refuse before anything is stopped
    stop order zoe-data > router > kokoro > brain; wake order brain > kokoro > router > zoe-data, each polled on its OWN /health
    a unit that stays down -> retry, then LOUD (exit 4, an ALARM file); any exception -> the restore still runs
    a job that fails on the 12B runs again on the 4B; the dry run changes nothing.
"""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))
sys.path.insert(0, str(REPO / "scripts" / "night"))

import night_window as nw  # noqa: E402
from zmb import bakeoff as bk  # noqa: E402

HOME = nw.HOME                    # the code derives every path from this HOME (the runner's is /home/runner); never a literal
PARKED = textwrap.dedent("""\
    [Unit]
    Description=Gemma 4 12B-QAT deep-brain (PARKED)
    OnFailure=llama-server-e2b-fallback.service

    [Service]
    Type=simple
    LimitMEMLOCK=infinity
    Environment=LD_LIBRARY_PATH=/home/zoe/llama.cpp/build-jetson-new/bin
    ExecStart=/home/zoe/llama.cpp/build-jetson-new/bin/llama-server \\
      --model /home/zoe/models/gemma4-12b-qat/gemma-4-12b-it-qat-q4_0.gguf \\
      --mmproj /home/zoe/models/gemma4-12b-qat/mmproj-gemma-4-12b-it-qat-q4_0.gguf \\
      --host 0.0.0.0 \\
      --port 11434 \\
      --ctx-size 4096 \\
      --n-gpu-layers 99 \\
      --parallel 1 \\
      --batch-size 512 \\
      --ubatch-size 128 \\
      --cont-batching \\
      --cache-type-k q8_0 \\
      --cache-type-v q8_0 \\
      --flash-attn on \\
      --no-mmproj-offload \\
      --temp 0.7 \\
      --top-k 64 \\
      --top-p 0.95 \\
      --jinja \\
      --chat-template-kwargs '{"enable_thinking":false}' \\
      --mlock \\
      --metrics
    ExecStartPost=/bin/bash -c 'curl -sf http://127.0.0.1:11434/health'
    Restart=on-failure
    """)
LIVE_UNIT = textwrap.dedent("""\
    [Service]
    LimitMEMLOCK=infinity
    MemorySwapMax=0
    MemoryLow=6G
    Environment=LD_LIBRARY_PATH=%h/llama.cpp-b11194/build-jetson/bin
    ExecStart=%h/llama.cpp-b11194/build-jetson/bin/llama-server \\
      --model %h/models/gemma4-e4b-qat/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf \\
      --spec-type draft-mtp \\
      --host 127.0.0.1 \\
      --port 11434 \\
      --ctx-size 8192 \\
      --parallel 1 \\
      --metrics
    """)
QAT = 6_975_877_728
Q4KM = 7_381_382_048
HEALTHY_BUDDY = "Node 0, zone   Normal  60000  30000  15000   7000   3000   1000    300    100     30    320     37    400    600\n"
FRAGMENTED_BUDDY = "Node 0, zone   Normal  60000  30000  15000   7000   3000   1000    300    100     30      0      0      0      0\n"
HELD = {nw.ZOE_DATA: 1400.0, nw.ROUTER: 270.0, nw.KOKORO: 2300.0, nw.BRAIN: 6000.0}
HEALTH_URL = {"11434": nw.BRAIN, "10201": nw.KOKORO, "11436": nw.ROUTER, "8000": nw.ZOE_DATA}


def at(h: int, m: int = 0, day: int = 9) -> float:
    return time.mktime((2026, 10, day, h, m, 0, 0, 0, -1))


class FakeHost(nw.NightHost):
    """Answers every command the window issues. State: which units are active, which are healthy, whether the 12B / the clone is up, the memory arithmetic."""

    def __init__(self, tmp: Path, *, start=None, base=1200.0, freed=1.0, sudo=True, active=None, unhealthy=(), job_rc=None, job_s=600.0, fail_run=(), models=None, busy=(),
                 panel_busy=False, llm_cost=7500.0, free_gap=0.0, gap_heal=0.0, llm_dies=False, boot_id="boot-1", nvmap_mib=12000.0, trial_no_output=False, probe_hang_drop=0.0):
        self.t = start if start is not None else at(2, 50)
        self.cmds: "list[tuple[list[str], bool]]" = []
        self.tmp = tmp
        self.active = {u: True for u in nw.STOP_ORDER} if active is None else dict(active)
        self.busy = set(busy)
        self.unhealthy = set(unhealthy)
        self.base, self.freed, self.sudo, self.llm_cost = base, freed, sudo, llm_cost
        self.llm_up = self.clone_up = self.shim_up = False
        self.job_rc = dict(job_rc or {})
        self.job_s = job_s
        self.fail_run = tuple(fail_run)
        self.files: "dict[str, str]" = {}
        self.sizes = {f"{HOME}/models/gemma4-12b-qat/gemma-4-12b-it-qat-q4_0.gguf": QAT, f"{HOME}/models/gemma4-12b/gemma-4-12B-it-Q4_K_M.gguf": Q4KM} if models is None else models
        self.present: "set[str]" = {"/home/zoe/llama.cpp/build-jetson-new/bin/llama-server"}
        self.jobs_run: "list[dict]" = []
        self.watched_timeouts: "list[float]" = []
        self.job_mem_drop = 0.0
        self.tokens = 0
        self.buddy = HEALTHY_BUDDY
        self.panel_busy = panel_busy
        self.log = lambda _m: None
        self.dry = False
        self.llm_dies, self.boot_id, self.nvmap_mib = llm_dies, boot_id, nvmap_mib
        self.trial_no_output, self.probe_hang_drop = trial_no_output, probe_hang_drop
        self.free_gap, self.gap_heal = free_gap, gap_heal          # MemFree = MemAvailable - gap (page cache that is 'available' but not FREE); each compaction heals gap_heal of it

    def mem_field_mb(self, path, name):
        return self.mem_available_mb(path) - (self.free_gap if name == "MemFree" else 0.0)

    # time
    def now(self): return self.t
    def mono(self): return self.t
    def sleep(self, s): self.t += s

    # files
    def read(self, path):
        if path in self.files:
            return self.files[path]
        if path.endswith("/memory.current"):
            unit = path.split("/cg/")[1].split("/")[0]
            return str(int(HELD.get(unit, 0) * nw.MIB))
        if path.endswith("buddyinfo"):
            return self.buddy
        if path.endswith("boot_id"):
            return self.boot_id + "\n"
        try:
            return Path(path).read_text()                       # files the double's own run_watched wrote (job logs, the trial driver's JSON)
        except OSError:
            return ""

    def exists(self, path): return path in self.present or path in self.files or path in self.sizes
    def file_size(self, path): return self.sizes.get(path)

    def stopped_held(self) -> float:
        return sum(HELD[u] * self.freed for u in nw.STOP_ORDER if not self.active.get(u, False))

    def mem_available_mb(self, path):
        m = self.base + self.stopped_held()
        if self.llm_up or self.clone_up:
            m -= self.llm_cost
        return m - (self.job_mem_drop if self.job_mem_drop and getattr(self, "in_job", False) else 0.0)

    def joined(self): return [" ".join(a) for a, _m in self.cmds]
    def mutating(self): return [" ".join(a) for a, m in self.cmds if m]
    def idx(self, needle, start=0):
        j = self.joined()
        return next(i for i in range(start, len(j)) if needle in j[i])
    def count(self, needle): return sum(1 for c in self.joined() if needle in c)

    def run(self, argv, timeout=60.0, mutating=True, env=None):
        self.cmds.append((list(argv), mutating))
        line = " ".join(argv)
        if self.dry and mutating:                                  # the DryHost: prints, never executes
            return bk.Result(0, "")
        if any(f in line for f in self.fail_run):
            return bk.Result(1, "injected failure")
        prog = argv[0]
        if prog == "sudo":
            if argv[1:] == ["-n", "true"]:
                return bk.Result(0 if self.sudo else 1, "")
            if argv[1:3] == ["-n", "cat"] and argv[3].endswith("iovmm/free_size"):
                return bk.Result(0, f"Max allocatable IOVMM memory: {int(self.nvmap_mib * nw.MIB)} bytes\n")
            if "compact_memory" in line:
                self.buddy = HEALTHY_BUDDY
                self.free_gap = max(0.0, self.free_gap - self.gap_heal)
            return bk.Result(0 if self.sudo else 1, "")
        if prog == "systemctl":
            sub, unit = argv[2], (argv[-1] if len(argv) > 3 else "")
            if sub == "is-active":
                if unit in self.busy:
                    return bk.Result(0, "active\n")
                if unit == nw.NIGHT_UNITS["llm"]:
                    return bk.Result(0, "active\n" if self.llm_up else "failed\n")
                return bk.Result(0, "active\n" if self.active.get(unit, False) else "inactive\n")
            if sub == "show":
                return bk.Result(0, f"/cg/{unit}\n" if "ControlGroup" in line else "MainPID=0\n")
            if sub == "cat":
                return bk.Result(0, LIVE_UNIT)
            if sub in ("start", "restart") and unit in nw.HEALTH:
                self.active[unit] = True
            if sub == "stop":
                if unit in nw.HEALTH:
                    self.active[unit] = False
                if unit == nw.NIGHT_UNITS["llm"]:
                    self.llm_up = False
                if unit == nw.NIGHT_UNITS["clone4"]:
                    self.clone_up = False
                if unit == nw.NIGHT_UNITS["shim"]:
                    self.shim_up = False
            return bk.Result(0, "")
        if prog == "systemd-run":
            if "--unit=zoe-night-12b" in line:
                self.llm_up = not self.llm_dies
            if "--unit=zoe-night-4b32k" in line:
                self.clone_up = True
            if "--unit=zoe-night-embed" in line:
                self.shim_up = True
            return bk.Result(0, "")
        if prog == "curl":
            url = argv[-1]
            m = re.search(r":(\d+)(/.*)$", url)
            port, path = (m.group(1), m.group(2)) if m else ("", "")
            if path == "/health" and port in HEALTH_URL:
                unit = HEALTH_URL[port]
                if not self.active.get(unit) or unit in self.unhealthy:
                    return bk.Result(7, "")
                return bk.Result(0, {nw.KOKORO: '{"status":"ok","voice":"af_sky","device":"cuda","pipeline_loaded":true}'}.get(unit, '{"status":"ok"}'))
            if path == "/health" and port == "11500":
                return bk.Result(0, '{"status":"ok"}') if (self.llm_up or self.clone_up) else bk.Result(7, "")
            if path == "/health" and port == "11501":
                return bk.Result(0, '{"status":"ok","model":"all-MiniLM-L6-v2"}') if self.shim_up else bk.Result(7, "")
            if path == "/props":
                return bk.Result(0, json.dumps({"default_generation_settings": {"n_ctx": int(self.served_ctx())}}))
            if path == "/metrics":
                return bk.Result(0, f"llamacpp:prompt_tokens_total {self.tokens * 3}\nllamacpp:tokens_predicted_total {self.tokens}\n")
            if path == "/v1/chat/completions":
                return bk.Result(0, json.dumps({"usage": {"prompt_tokens": 1500, "completion_tokens": 96}, "timings": {"prompt_per_second": 120.5, "predicted_per_second": 4.2}}))
            return bk.Result(7, "")
        if prog == "ssh":
            return bk.Result(0, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.t - (30 if self.panel_busy else 99999))) + "\n")
        if prog == "journalctl":
            return bk.Result(0, "NvMapMemAllocInternalTagged: 1075072515 error 12\n0.12.9 E ggml_backend_cuda_buffer_type_alloc_buffer: allocating 6637.69 MiB on device 0: cudaMalloc failed: out of memory\n"
                                "0.13.3 E llama_model_load: error loading model: unable to allocate CUDA0 buffer\nsome prompt text that must not be copied\n")
        if prog == "pgrep":
            return bk.Result(1, "")
        if prog == "ps":
            return bk.Result(0, "6000 llama-server\n1400 python\n")
        return bk.Result(0, "")

    def served_ctx(self):
        for a, _m in reversed(self.cmds):
            if a[0] == "systemd-run" and "--ctx-size" in a:
                return a[a.index("--ctx-size") + 1]
        return 32768

    def run_watched(self, argv, timeout, env, tick, log_path, interval=5.0):
        if argv[0] == "curl":                                       # the speed probe: a watched curl whose reply lands in the log file
            self.cmds.append((list(argv), False))
            self.watched_timeouts.append(timeout)
            if self.probe_hang_drop:
                self.base -= self.probe_hang_drop                   # a stuck / RAM-hungry first request: memory falls while it blocks
            tick()
            res = self.run(argv, mutating=False)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(res.out)
            return bk.Result(res.rc, "")
        name = os.path.basename(argv[1]) if len(argv) > 1 else argv[0]
        self.cmds.append((list(argv), True))
        self.in_job = True
        try:
            tick()
        finally:
            self.in_job = False
        self.jobs_run.append({"name": name, "argv": list(argv), "env": dict(env or {}), "t": self.t, "timeout": timeout})
        self.t += self.job_s
        self.tokens += 1000
        rc = self.job_rc.get(name, self.job_rc.get("*", 0))
        if isinstance(rc, list):                                    # successive answers (the 12B run, then the 4B fallback); the last repeats
            rc = rc.pop(0) if len(rc) > 1 else rc[0]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"NIGHT_JOB {name} ok\nsecret household text that must stay out of the report\n")
        if "--out" in argv and not self.trial_no_output:            # the trial driver
            label = argv[argv.index("--model-name") + 1]
            Path(argv[argv.index("--out") + 1]).write_text(json.dumps({"reflect": {"k_cells": [
                {"id": "K1.a", "verdict": "PASS", "evidence": {"items": [4, 4]}}, {"id": "K2.a", "verdict": "FAIL" if label == "4B@32k" else "PASS", "evidence": {"items": [2, 4]}}],
                "wall_s": 100.0, "model_calls": 30, "prompt_tokens_max": 3900}}))
        return bk.Result(rc, "")


def make(tmp_path, *, cfg_kw=None, argv=(), **host_kw):
    args = nw.build_parser().parse_args(list(argv))
    cfg_kw = {"night_mind_script": tmp_path / "absent" / "zoe-night-mind.py", **(cfg_kw or {})}      # the real entry point lives on another branch: tests never depend on whether it is merged
    cfg = nw.NightCfg(lock_path=str(tmp_path / "brain.lock"), night_dir=tmp_path / "night", report_dir=tmp_path / "reports", buddyinfo=str(tmp_path / "buddyinfo"), meminfo=str(tmp_path / "meminfo"),
                      blackouts=((250, 292),), contig_min_blocks=64, health_wait_s=20.0, quiet_wait_max_min=2.0, quiet_poll_s=10.0, busy_wait_max_min=2.0, busy_poll_s=10.0,
                      bakeoff_dir=tmp_path / "bakeoff", **cfg_kw)
    cfg = nw.configure(args, cfg)
    cfg.bakeoff_dir = tmp_path / "bakeoff"
    host = FakeHost(tmp_path, **host_kw)
    host.files[cfg.deep_unit] = PARKED
    host.files[str(tmp_path / "meminfo")] = "MemTotal: 16000000 kB\n"
    if cfg.trial:
        for p in (cfg.hs_python, REPO / "scripts/perf/zmb/mpa_window.py", cfg.bakeoff_dir / "mempalace-venv" / "bin" / "mempalace-mcp"):
            host.present.add(str(p))
    host.dry = args.dry_run
    w = nw.NightWindow(cfg, host, host.log, dry=args.dry_run, run_id="t1", mode="trial" if cfg.trial else "night")
    return w, host, cfg


@pytest.fixture(autouse=True)
def _private_locks(tmp_path, monkeypatch):
    monkeypatch.setattr(bk.Window, "HARNESS_LOCK", str(tmp_path / "harness.lock"))


def wake_order(host):
    return [u for u in nw.RESTORE_ORDER if any(f"--user start {u}" in c for c in host.joined())]


# ── the arithmetic ───────────────────────────────────────────────────────────

def test_the_constants_are_the_bakeoffs_so_the_two_tools_cannot_disagree():
    from zmb import bakeoff_measure as m
    assert nw.KV_GLOBAL_ELEMS_PER_TOKEN == m.REFLECT_12B_KV_GLOBAL_ELEMS_PER_TOKEN and nw.KV_SWA_ELEMS == m.REFLECT_12B_KV_SWA_ELEMS
    assert nw.KV_BYTES_PER_ELEM["q8_0"] == m.REFLECT_KV_BYTES_PER_ELEM and nw.COMPUTE_MIB == m.REFLECT_COMPUTE_MB
    assert nw.kv_mib(32768, "q8_0") * nw.MIB / 1e6 == pytest.approx(m.kv_est_mb(32768), rel=1e-9)        # 486 MB decimal = 464 MiB


def test_need_is_model_plus_kv_plus_compute_plus_floor_in_mib_not_the_bakeoffs_mixed_units():
    n = nw.need_mib(QAT, nw.Levers("qat", 32768, "q8_0"), 1200.0, 0.0)
    assert n["model"] == pytest.approx(6652.8, abs=0.1) and n["kv"] == pytest.approx(463.6, abs=0.1)
    assert n["need_load"] == pytest.approx(8916.4, abs=0.2)
    assert n["need_load"] < 9262 - 300            # run 2 divided the file by 1e6 but compared it with MemAvailable in MiB: its -1,259 was really about -936
    assert nw.need_mib(QAT, nw.Levers("qat", 32768, "q8_0"), 1200.0, 700.0)["need_jobs"] == pytest.approx(n["need_load"] + 700)
    assert nw.kv_mib(16384, "q8_0") < nw.kv_mib(32768, "q8_0") and nw.kv_mib(32768, "q4_0") < nw.kv_mib(32768, "q8_0")
    assert nw.CTX_OPTIONS == (32768, 16384, 8192) and nw.kv_mib(8192, "q8_0") < nw.kv_mib(16384, "q8_0") and nw.kv_mib(8192, "q4_0") == pytest.approx(137.4, abs=0.1)
    assert nw.need_mib(QAT, nw.Levers("qat", 8192, "q4_0"), 1200.0, 700.0)["need_jobs"] == pytest.approx(9290.1, abs=0.2)     # 8192 is a lever the table and the budget accept


@pytest.mark.parametrize("avail,want", [
    (12000, "qat ctx 32768 KV q8_0"),            # everything fits: the biggest context at the better cache type (needs 9,616 with the jobs beside it)
    (9550, "qat ctx 16384 KV q8_0"),             # 9,480: the context is given up before the cache type is
    (9450, "qat ctx 8192 KV q8_0"),              # 9,412: 8k at the better cache type beats 32k at the worse one (the proven 8k trial set)
    (9400, "qat ctx 32768 KV q4_0"),             # 9,398
    (9350, "qat ctx 16384 KV q4_0"),             # 9,326
    (9300, "qat ctx 8192 KV q4_0"),              # 9,290: the smallest set
    (9250, None),                                # nothing
])
def test_the_best_lever_set_that_fits_is_chosen(avail, want):
    pick = nw.choose_levers(nw.evaluate({"qat": QAT, "q4km": Q4KM}, avail, 1200.0, 700.0))
    assert (pick["levers"].label() if pick else None) == want


def test_nothing_fits_returns_none_and_pins_are_never_substituted():
    sizes = {"qat": QAT, "q4km": Q4KM}
    assert nw.choose_levers(nw.evaluate(sizes, 8000, 1200.0, 700.0)) is None
    rows = nw.evaluate(sizes, 9500, 1200.0, 700.0)
    assert nw.choose_levers(rows, ctx=32768, kv="q8_0") is None            # 32k at q8_0 needs 9,616: the pin is not quietly relaxed
    assert nw.choose_levers(rows, kv="q4_0")["levers"].ctx == 32768
    assert nw.choose_levers(nw.evaluate(sizes, 20000, 1200.0, 700.0), model="q4km")["levers"].model == "q4km"


def test_the_qat_file_is_preferred_and_q4km_only_when_it_is_the_only_file():
    big = nw.choose_levers(nw.evaluate({"qat": QAT, "q4km": Q4KM}, 30000, 1200.0, 700.0))
    assert big["levers"].model == "qat"                                      # a bigger file never helps a box that is short of RAM
    only = nw.choose_levers(nw.evaluate({"q4km": Q4KM}, 30000, 1200.0, 700.0))
    assert only["levers"].model == "q4km"


def test_prediction_and_timeout_scale():
    assert nw.predict_avail(1000, {"a": 1000.0, "b": 500.0}, ("a",), 0.8) == pytest.approx(1800)
    assert nw.predict_avail(1000, {"a": 1000.0}, ("zz",)) == 1000
    assert nw.digest_timeout_scale(None) == 8 and nw.digest_timeout_scale(11.4) == 2 and nw.digest_timeout_scale(4.2) == 5 and nw.digest_timeout_scale(0.1) == 20
    assert nw.parse_metrics("llamacpp:prompt_tokens_total 12\nllamacpp:tokens_predicted_total 3.5\n") == {"prompt_tokens_total": 12.0, "tokens_predicted_total": 3.5}


# ── the 12B command is generated from the parked unit, never hand-written ────

def test_the_12b_command_is_the_parked_execstart_with_only_the_listed_changes(tmp_path):
    cfg = nw.NightCfg()
    spec = nw.llm_spec(PARKED, cfg, nw.Levers("qat", 32768, "q8_0"))
    a = spec["argv"]
    assert a[0] == "/home/zoe/llama.cpp/build-jetson-new/bin/llama-server"                          # the parked binary, verbatim
    val = lambda f: a[a.index(f) + 1]  # noqa: E731
    assert val("--model") == f"{HOME}/models/gemma4-12b-qat/gemma-4-12b-it-qat-q4_0.gguf" and val("--host") == "127.0.0.1" and val("--port") == "11500"
    assert val("--ctx-size") == "32768" and val("--parallel") == "1" and val("--cache-ram") == "512" and "--fit" not in a
    assert val("--cache-type-k") == "q8_0" == val("--cache-type-v")
    assert "--mmproj" not in a and "--no-mmproj-offload" not in a and "--mlock" in a and "--metrics" in a and "--jinja" in a
    assert spec["props"]["MemorySwapMax"] == "0" and spec["props"]["LimitMEMLOCK"] == "infinity"
    assert any("--host 0.0.0.0 -> 127.0.0.1" in d for d in spec["diff"]) and any(d.startswith("+ --cache-ram") for d in spec["diff"])
    assert nw.llm_spec(PARKED, nw.NightCfg(fit_off=True), nw.Levers("qat", 32768, "q8_0"))["argv"].count("--fit") == 1
    kv4 = nw.llm_spec(PARKED, cfg, nw.Levers("q4km", 16384, "q4_0"))["argv"]
    assert kv4[kv4.index("--model") + 1].endswith("gemma-4-12B-it-Q4_K_M.gguf") and kv4[kv4.index("--cache-type-v") + 1] == "q4_0" and kv4[kv4.index("--ctx-size") + 1] == "16384"


def test_another_build_gets_its_renamed_flags_and_the_parked_text_is_never_written(tmp_path):
    cfg = nw.NightCfg(binary="/home/zoe/llama.cpp-b11194/build-jetson/bin/llama-server")
    a = nw.llm_spec(PARKED, cfg, nw.Levers("qat", 32768, "q8_0"))["argv"]
    assert a[0].endswith("b11194/build-jetson/bin/llama-server") and "--mlock" not in a and "--chat-template-kwargs" not in a
    assert a[a.index("--load-mode") + 1] == "mmap+mlock" and a[a.index("--reasoning") + 1] == "off"
    parked = tmp_path / "parked.service.disabled"
    parked.write_text(PARKED)
    before = parked.stat().st_mtime_ns
    nw.llm_spec(parked.read_text(), nw.NightCfg(), nw.Levers("qat", 32768, "q8_0"))
    assert parked.read_text() == PARKED and parked.stat().st_mtime_ns == before
    with pytest.raises(bk.Refused, match="--model"):
        nw.llm_spec("[Service]\nExecStart=/bin/sleep 5\n", nw.NightCfg(), nw.Levers("qat", 32768, "q8_0"))


# ── the preflight refuses before anything is stopped ─────────────────────────

def stopped_nothing(host):
    return not any(" stop " in f" {c} " and "systemctl" in c for c in host.mutating()) and not any(c.startswith("systemd-run") for c in host.joined())


def test_the_brain_window_lock_held_refuses_and_stops_nothing(tmp_path):
    w, host, cfg = make(tmp_path)
    fd = os.open(cfg.lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert w.run() == nw.EXIT_REFUSED
    finally:
        os.close(fd)
    assert stopped_nothing(host) and "is held" in w.outcome and not cfg.marker.exists()


def test_a_manual_run_in_daylight_is_refused_and_does_not_start_the_nights_jobs(tmp_path):
    w, host, _ = make(tmp_path, start=at(14, 0))
    assert w.run() == nw.EXIT_REFUSED and "--anytime" in w.outcome
    assert stopped_nothing(host) and host.jobs_run == []                    # no fallback either: the hour was the refusal
    w2, host2, _ = make(tmp_path, argv=["--anytime", "--dry-run"], start=at(14, 0))
    assert w2.run() == nw.EXIT_OK


def test_a_late_start_is_refused_when_too_little_time_remains_before_the_end(tmp_path):
    w, host, cfg = make(tmp_path, start=at(3, 30))
    assert w.run() == nw.EXIT_REFUSED and "min remain before 03:55" in w.outcome and stopped_nothing(host)
    w2, _, _ = make(tmp_path, start=at(2, 50))
    assert w2.compute_cap() == 65.0 and make(tmp_path, start=at(3, 0))[0].compute_cap() == 55.0


def test_the_end_cannot_be_configured_into_the_voice_gate():
    with pytest.raises(bk.Refused, match="voice gate"):
        nw.configure(nw.build_parser().parse_args(["--end-by", "04:20"]))
    with pytest.raises(bk.Refused, match="unknown job"):
        nw.configure(nw.build_parser().parse_args(["--jobs", "digest,nope"]))


def test_a_running_nightly_timer_job_refuses_after_waiting_and_so_does_a_training_lock(tmp_path):
    w, host, _ = make(tmp_path, busy=("zoe-backup.service",))
    assert w.run() == nw.EXIT_REFUSED and "zoe-backup.service is running" in w.outcome and stopped_nothing(host)
    assert sum(host.t - at(2, 50) for _ in [0]) >= 120                      # it WAITED (busy_wait_max_min=2) before refusing
    w2, host2, cfg = make(tmp_path)
    host2.files[cfg.training_lock] = str(os.getpid())
    host2.present.add(f"/proc/{os.getpid()}")
    assert w2.run() == nw.EXIT_REFUSED and "training" in w2.outcome and stopped_nothing(host2)


def test_a_busy_panel_is_waited_out_then_refused(tmp_path):
    w, host, _ = make(tmp_path, panel_busy=True)
    assert w.run() == nw.EXIT_REFUSED and "voice turn" in w.outcome and stopped_nothing(host)


def test_fragmented_ram_without_sudo_is_refused_before_anything_stops_and_with_sudo_is_compacted(tmp_path):
    w, host, cfg = make(tmp_path, sudo=False)
    host.buddy = FRAGMENTED_BUDDY
    assert w.run() == nw.EXIT_REFUSED and "compact_memory" in w.outcome and stopped_nothing(host)
    w2, host2, cfg2 = make(tmp_path, sudo=True)
    host2.buddy = FRAGMENTED_BUDDY
    assert w2.run() == nw.EXIT_OK
    assert host2.idx("compact_memory") < host2.idx("systemd-run")           # compacted before the 12B started


def test_a_12b_needs_free_pages_not_just_available_ones_so_compaction_is_retried_then_refused(tmp_path):
    w, host, _ = make(tmp_path, free_gap=5000.0, gap_heal=1000.0)          # 'available' fits but only 6.2 GB is FREE; each compaction frees another GB
    assert w.run() == nw.EXIT_OK, w.outcome
    assert host.count("compact_memory") == 3 and w.rec["arith"]["free_measured_mib"] >= 7426 and any("compacting again" in e["msg"] for e in w.rec["events"])
    w2, host2, _ = make(tmp_path, free_gap=6000.0, gap_heal=0.0)           # compaction cannot make them free: the June 2026 failure (9.2 GB free, cudaMalloc failed)
    assert w2.run() == nw.EXIT_REFUSED and "MemFree" in w2.outcome
    assert not any(c.startswith("systemd-run") for c in host2.joined()) and wake_order(host2) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]
    rows = nw.evaluate({"qat": QAT}, 20000, 1200.0, 700.0, free_mib=7000.0)
    assert not any(r["fits"] for r in rows) and all(r["fits_load_only"] is False for r in rows) and nw.evaluate({"qat": QAT}, 20000, 1200.0, 700.0)[0]["fits"]


def test_the_12b_also_needs_big_free_blocks_so_compaction_is_repeated_and_the_numbers_are_recorded(tmp_path):
    """2026-10-09: three loads died on cudaMalloc with 12.7 GB MemFree but 6.1 GB in free blocks of 2 MB and up. The window repeats the compaction (sync, drop caches, compact) until the big-block
    total holds the smallest 12B, logs the figure each time and records it in the report; it still tries the load afterwards (the threshold is a hypothesis the first success calibrates)."""
    w, host, _ = make(tmp_path, sudo=True)
    host.compaction_heals_buddy = [FRAGMENTED_BUDDY, FRAGMENTED_BUDDY, HEALTHY_BUDDY]
    real = host.run

    def run(argv, timeout=60.0, mutating=True, env=None):
        r = real(argv, timeout, mutating, env)
        if argv[:1] == ["sudo"] and "compact_memory" in " ".join(argv) and host.compaction_heals_buddy:
            host.buddy = host.compaction_heals_buddy.pop(0)
        return r
    host.run = run
    assert w.run() == nw.EXIT_OK, w.outcome
    assert w.rec["frag_mib"][0] < 7426 <= w.rec["frag_mib"][-1] and sum("compact_memory" in c for c in host.joined()[:host.idx("systemd-run")]) == 3
    assert nw.high_order_mib(nw.bk.parse_buddyinfo(HEALTHY_BUDDY)) == pytest.approx((320 * 2 + 37 * 4 + 400 * 8 + 600 * 16) * 1.0)
    assert nw.high_order_mib({8: 10_000, 9: 1}) == 2.0 and nw.high_order_mib({}) == 0.0


def test_missing_files_inactive_brain_and_an_impossible_fit_are_refused_before_anything_stops(tmp_path):
    w, host, _ = make(tmp_path, models={})
    assert w.run() == nw.EXIT_REFUSED and "no 12B model file" in w.outcome and stopped_nothing(host)
    w, host, _ = make(tmp_path, active={nw.BRAIN: False, nw.KOKORO: True, nw.ROUTER: True, nw.ZOE_DATA: True})
    assert w.run() == nw.EXIT_REFUSED and "no live brain" in w.outcome and stopped_nothing(host)
    w, host, _ = make(tmp_path, base=-5000.0)                               # even every stopped unit freeing ALL it holds cannot fit the smallest set
    assert w.run() == nw.EXIT_REFUSED and "freed ALL" in w.outcome and stopped_nothing(host)
    w, host, _ = make(tmp_path)
    host.present.clear()                                                    # the llama-server binary is gone
    assert w.run() == nw.EXIT_REFUSED and "binary" in w.outcome and stopped_nothing(host)


# ── the window: sleep, load, jobs, wake ──────────────────────────────────────

def test_a_full_night_sleeps_in_order_runs_the_jobs_on_the_12b_and_wakes_in_the_owners_order(tmp_path):
    w, host, cfg = make(tmp_path)
    assert w.run() == nw.EXIT_OK, w.outcome
    stops = [u for u in nw.STOP_ORDER if any(f"--user stop {u}" in c for c in host.joined())]
    assert stops == [nw.ZOE_DATA, nw.ROUTER, nw.KOKORO, nw.BRAIN]
    order = [host.idx(f"--user stop {u}") for u in stops]
    assert order == sorted(order)
    run12 = host.idx("systemd-run")
    assert order[-1] < host.idx("compact_memory", order[-1]) < run12        # the brain is gone and RAM is compacted BEFORE the 12B starts
    cmd = next(c for c in host.joined() if c.startswith("systemd-run"))
    assert "--unit=zoe-night-12b" in cmd and "--port 11500" in cmd and "--ctx-size 32768" in cmd and "--parallel 1" in cmd and "--cache-type-k q8_0" in cmd and "--host 127.0.0.1" in cmd
    assert "gemma-4-12b-it-qat-q4_0.gguf" in cmd and "--mmproj" not in cmd and "--property=MemorySwapMax=0" in cmd
    # the jobs: digest, dreaming, (night_mind skipped: not wired), in that order, each pointed at the 12B
    names = [j["name"] for j in host.jobs_run]
    assert names == ["night_digest.py", "zoe-nightly-dreaming.py", "zoe-nightly-dreaming.py"]        # the last is the compaction trigger, after the wake
    for j in host.jobs_run[:2]:
        assert j["env"]["GEMMA_SERVER_URL"] == "http://127.0.0.1:11500/v1" and j["env"]["ZOE_DIGEST_LLM_TIMEOUT_SCALE"] == "5"          # 1.5 x 11.4 / 4.2 tok/s
        assert j["env"]["MEMORY_DIGEST_MODEL"].endswith("q4_0.gguf")
    assert "--skip-compaction" in host.jobs_run[1]["argv"]
    # the 12B is unloaded, then the wake order is brain, kokoro, router, zoe-data
    assert host.idx("--user stop zoe-night-12b") < host.idx(f"--user start {nw.BRAIN}")
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]
    starts = [host.idx(f"--user start {u}") for u in nw.RESTORE_ORDER]
    assert starts == sorted(starts)
    # after zoe-data is back: the weekly compaction trigger (it needs zoe-data) runs, and nothing else
    assert host.jobs_run[-1]["argv"][-1] == "--only-compaction" and host.jobs_run[-1]["t"] > host.t - 1000 and host.idx(f"--user start {nw.ZOE_DATA}") < host.idx("--only-compaction")
    assert not cfg.marker.exists() and not (cfg.report_dir / "ALARM").exists() and w.lock_fd is None


def test_the_report_is_written_private_with_counts_and_no_job_output(tmp_path):
    w, host, cfg = make(tmp_path)
    assert w.run() == nw.EXIT_OK
    md = next(cfg.report_dir.glob("*.md"))
    js = json.loads(md.with_suffix(".json").read_text())
    text = md.read_text()
    assert "secret household text" not in text and "secret household text" not in md.with_suffix(".json").read_text()
    assert "NIGHT_JOB night_digest.py ok" in md.with_suffix(".json").read_text()          # the counts line IS carried
    assert js["outcome"] == "ok" and js["exit"] == 0 and js["arith"]["chosen"] == "qat ctx 32768 KV q8_0" and js["speed"]["12B"]["decode_tps"] == 4.2
    assert {r["name"] for r in js["jobs"]} >= {"digest", "dreaming", "night_mind"} and any(r["status"] == "skipped" for r in js["jobs"] if r["name"] == "night_mind")
    assert all(r["tokens_predicted"] == 1000 for r in js["jobs"] if r["status"] == "ok" and r["backend"] == "12B")
    assert oct(md.stat().st_mode & 0o777) == "0o600" and oct(cfg.report_dir.stat().st_mode & 0o777) == "0o700"
    assert "Night window 2026-10-09" in text and "qat ctx 32768 KV q8_0" in text and "| digest | 12B | ok |" in text


def test_only_the_units_that_were_active_before_are_stopped_and_woken(tmp_path):
    w, host, _ = make(tmp_path, active={nw.BRAIN: True, nw.KOKORO: True, nw.ROUTER: False, nw.ZOE_DATA: True})
    assert w.run() == nw.EXIT_OK
    assert not any(nw.ROUTER in c and (" stop " in c or " start " in c) for c in host.joined())
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ZOE_DATA]


def test_keep_zoe_data_leaves_it_up_and_is_refused_across_its_03_00_loop(tmp_path):
    w, host, _ = make(tmp_path, argv=["--keep-zoe-data"], start=at(2, 10))
    assert w.run() == nw.EXIT_REFUSED and "maintenance loop" in w.outcome and stopped_nothing(host)
    w, host, _ = make(tmp_path, argv=["--keep-zoe-data", "--cap-min", "40"], start=at(2, 10))
    assert w.run() == nw.EXIT_OK and not any(nw.ZOE_DATA in c and " stop " in c for c in host.joined())


def test_nothing_fits_after_the_stops_refuses_and_puts_everything_back_without_starting_the_12b(tmp_path):
    w, host, cfg = make(tmp_path, freed=0.5)                                # the units hold twice what they give back: the prediction passes, the measurement does not
    assert w.run() == nw.EXIT_REFUSED and "no lever set fits" in w.outcome
    assert not any(c.startswith("systemd-run") for c in host.joined())
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA] and not cfg.marker.exists()


def test_a_refused_window_still_runs_the_dreaming_on_the_4b_because_the_timer_it_replaced_is_disabled(tmp_path):
    w, host, _ = make(tmp_path, freed=0.5)
    assert w.run() == nw.EXIT_REFUSED
    ran = {j["name"]: j for j in host.jobs_run}
    assert "zoe-nightly-dreaming.py" in ran and ran["zoe-nightly-dreaming.py"]["env"]["GEMMA_SERVER_URL"] == "http://127.0.0.1:11434/v1"
    assert "--skip-compaction" not in ran["zoe-nightly-dreaming.py"]["argv"]                # zoe-data is up: the full script, compaction trigger included
    assert "night_digest.py" not in ran or ran["night_digest.py"]["env"]["GEMMA_SERVER_URL"].endswith(":11434/v1")


def test_a_job_that_fails_on_the_12b_runs_again_on_the_4b_and_the_digest_only_when_the_03_00_loop_was_missed(tmp_path):
    w, host, _ = make(tmp_path, start=at(2, 50), job_rc={"night_digest.py": [1, 0]}, job_s=1500.0)           # down across 03:00: zoe-data's own loop will not run tonight
    rc = w.run()
    runs = [(j["name"], j["env"]["GEMMA_SERVER_URL"]) for j in host.jobs_run]
    assert ("night_digest.py", "http://127.0.0.1:11500/v1") in runs and ("night_digest.py", "http://127.0.0.1:11434/v1") in runs
    assert rc == nw.EXIT_OK                                                                              # covered by the 4B fallback: the night is not a failure
    w2, host2, _ = make(tmp_path, start=at(2, 5), job_rc={"night_digest.py": 1}, job_s=60.0, argv=["--anytime"])
    w2.run()
    assert [j["env"]["GEMMA_SERVER_URL"] for j in host2.jobs_run if j["name"] == "night_digest.py"] == ["http://127.0.0.1:11500/v1"]
    assert any("will run it" in n for n in w2.rec["notes"])                                              # restored before 03:00: zoe-data's loop still fires tonight


def test_a_job_that_fails_everywhere_makes_the_exit_code_5_but_the_box_is_back(tmp_path):
    w, host, _ = make(tmp_path, job_rc={"zoe-nightly-dreaming.py": 1})
    assert w.run() == nw.EXIT_JOBS_FAILED
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]


def test_no_fallback_flag_and_the_night_mind_command_are_honoured(tmp_path):
    w, host, _ = make(tmp_path, argv=["--no-fallback", "--night-mind-cmd", "python /x/night_mind.py --model-url {model_url_v1} --user demo"], job_rc={"night_digest.py": 1})
    assert w.run() == nw.EXIT_JOBS_FAILED
    nm = next(j for j in host.jobs_run if j["name"] == "night_mind.py")
    assert nm["argv"][1:] == ["/x/night_mind.py", "--model-url", "http://127.0.0.1:11500/v1", "--user", "demo"]
    assert [j["env"]["GEMMA_SERVER_URL"] for j in host.jobs_run if j["name"] == "night_digest.py"] == ["http://127.0.0.1:11500/v1"]


def test_memory_below_the_floor_after_the_12b_loads_aborts_and_restores(tmp_path):
    w, host, _ = make(tmp_path, llm_cost=10500.0)                           # leaves 670 MiB: under the 1,200 floor
    assert w.run() == nw.EXIT_ABORTED and "floor" in w.outcome
    assert not host.jobs_run or host.jobs_run[0]["name"] != "night_digest.py"
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]


def test_a_job_that_pushes_memory_under_the_floor_is_killed_aborts_and_restores(tmp_path):
    w, host, _ = make(tmp_path)
    host.job_mem_drop = 9000.0
    assert w.run() == nw.EXIT_ABORTED and "floor" in w.outcome
    assert any(j["status"] == "killed" for j in w.jobs)
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]


def test_the_hard_cap_keeps_a_late_job_from_starting_and_the_wake_still_happens(tmp_path):
    w, host, _ = make(tmp_path, job_s=52 * 60.0)
    w.run()
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]
    on12 = [j for j in w.jobs if j["backend"] == "12B"]
    assert on12[0]["status"] == "ok" and on12[1]["status"] == "no time" and "no time left" in on12[1]["notes"][0]          # 52 min used: dreaming could not start inside the cap
    for j in host.jobs_run:                                                                                            # and every 4B fallback is bounded by the voice gate
        if j["env"]["GEMMA_SERVER_URL"].endswith(":11434/v1"):
            assert j["timeout"] <= (250 - (time.localtime(j["t"]).tm_hour * 60 + time.localtime(j["t"]).tm_min)) * 60 + 60


def test_a_requested_job_that_never_ran_for_lack_of_time_is_not_a_success(tmp_path):
    """Greptile 1929: 'no time left' shared the status of the intentionally absent night-mind script, and the final check accepted both: --no-fallback + a slow digest exited 0 with dreaming unrun."""
    w, host, _ = make(tmp_path, argv=["--no-fallback"], job_s=52 * 60.0)
    assert w.run() == nw.EXIT_JOBS_FAILED
    assert [j["status"] for j in w.jobs if j["backend"] == "12B"][:2] == ["ok", "no time"]
    w2, _, _ = make(tmp_path, job_s=60.0)
    assert w2.run() == nw.EXIT_OK and any(j["name"] == "night_mind" and j["status"] == "skipped" for j in w2.jobs)        # the absent optional script stays a plain skip


def test_a_refused_window_runs_no_fallback_job_without_the_lock_or_while_the_box_is_busy(tmp_path):
    """Greptile 1929: a refusal (lock held, nightly job / training running) still set restore_ok and ran dreaming on the live 4B WITHOUT owning the lock or idle checks."""
    w, host, cfg = make(tmp_path)
    fd = os.open(cfg.lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert w.run() == nw.EXIT_REFUSED
    finally:
        os.close(fd)
    assert host.jobs_run == [] and stopped_nothing(host) and any("lock is held" in n for n in w.rec["notes"])
    (tmp_path / "b").mkdir()
    w2, host2, cfg2 = make(tmp_path / "b")
    host2.files[cfg2.training_lock] = str(os.getpid())                                      # LoRA training holds its lock (this pid is alive)
    host2.present.add(f"/proc/{os.getpid()}")
    assert w2.run() == nw.EXIT_REFUSED and host2.jobs_run == [] and w2.lock_fd is None
    (tmp_path / "c").mkdir()
    w3, host3, _ = make(tmp_path / "c", panel_busy=True)
    w3.cfg.max_load_failures = 0                                                            # the load-failure breaker refuses; the panel is mid-conversation: no fallback either
    assert w3.run() == nw.EXIT_REFUSED and host3.jobs_run == []
    (tmp_path / "d").mkdir()
    w4, host4, _ = make(tmp_path / "d")
    w4.cfg.max_load_failures = 0                                                            # same refusal, idle box, lock free: the 4B fallback is still wanted, and it runs under the lock
    assert w4.run() == nw.EXIT_REFUSED and {j["name"] for j in host4.jobs_run} >= {"zoe-nightly-dreaming.py"} and w4.lock_fd is None


def test_the_speed_probe_is_watched_so_a_stuck_first_request_cannot_keep_the_brain_down(tmp_path):
    """Greptile 1929: probe_speed blocked in Host.run for up to 900 s with no memory-floor or cap check."""
    w, host, _ = make(tmp_path, probe_hang_drop=9000.0)
    assert w.run() == nw.EXIT_ABORTED and "floor" in w.outcome
    assert host.watched_timeouts and wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA] and not any(j["env"]["GEMMA_SERVER_URL"].endswith(":11500/v1") for j in host.jobs_run)
    (tmp_path / "b").mkdir()
    w2, host2, _ = make(tmp_path / "b")
    w2.cap_min = 40.0
    host2.t += (40 - w2.cfg.reserve_min) * 60.0 - 600.0                                      # 600 s left before the restore reserve
    assert w2.probe_speed("12B")["decode_tps"] == 4.2
    curl = next(a for a, _m in host2.cmds if a[0] == "curl" and a[-1].endswith("/v1/chat/completions"))
    assert int(curl[curl.index("-m") + 1]) == 600 and host2.watched_timeouts[0] == pytest.approx(610.0)        # bounded by the time left, not a flat 900 s
    (tmp_path / "c").mkdir()
    w3, host3, _ = make(tmp_path / "c")
    w3.cap_min = 40.0
    host3.t += 40 * 60.0 - 30                                                                # nothing left before the restore reserve: no probe at all
    assert w3.probe_speed("12B") == {} and not host3.watched_timeouts


def test_any_exception_still_restores_everything(tmp_path):
    w, host, cfg = make(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("the job runner exploded")
    host.run_watched = boom
    assert w.run() == nw.EXIT_ABORTED and "exploded" in w.outcome
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA] and not cfg.marker.exists()
    assert host.idx("--user stop zoe-night-12b") < host.idx(f"--user start {nw.BRAIN}")


def test_the_12b_failing_to_start_aborts_and_restores(tmp_path):
    w, host, _ = make(tmp_path, fail_run=("systemd-run",))
    assert w.run() == nw.EXIT_ABORTED and "could not start" in w.outcome
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]


def test_the_marker_lists_each_unit_before_it_is_stopped(tmp_path):
    w, host, cfg = make(tmp_path)
    seen = []
    real = host.run

    def spy(argv, timeout=60.0, mutating=True, env=None):
        if argv[:3] == ["systemctl", "--user", "stop"] and argv[-1] in nw.HEALTH:
            seen.append((argv[-1], json.loads(cfg.marker.read_text())["stopped"]))
        return real(argv, timeout, mutating, env)
    host.run = spy
    w.run()
    assert [(u, u in lst) for u, lst in seen] == [(u, True) for u in nw.STOP_ORDER]


# ── the 12B that will not load ───────────────────────────────────────────────

def test_a_12b_that_dies_loading_is_diagnosed_restored_and_the_jobs_fall_back_to_the_4b(tmp_path):
    w, host, cfg = make(tmp_path, llm_dies=True)
    assert w.run() == nw.EXIT_ABORTED and "did not load" in w.outcome and "cuda_alloc" in w.outcome
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA] and not (cfg.report_dir / "ALARM").exists()
    diag = w.rec["load_diagnosis"]
    assert any("cudaMalloc failed" in ln for ln in diag["lines"]) and "free blocks of 2 MB" in diag["ram"] and "prompt text" not in json.dumps(w.rec)
    ran = [(j["name"], j["env"]["GEMMA_SERVER_URL"][-9:]) for j in host.jobs_run]
    assert ("zoe-nightly-dreaming.py", ":11434/v1") in ran                                  # the night's dreaming still happens, on the 4B, after the wake
    assert json.loads(w.failures_path.read_text())["count"] == 1


def test_after_two_failed_loads_on_a_boot_a_timer_run_refuses_before_stopping_anything_until_retry_or_reboot(tmp_path):
    for night in (1, 2):
        w, host, cfg = make(tmp_path, llm_dies=True)
        assert w.run() == nw.EXIT_ABORTED, night
    w3, host3, _ = make(tmp_path)
    assert w3.run() == nw.EXIT_REFUSED and "failed to load on the last 2 windows" in w3.outcome and stopped_nothing(host3)
    assert any(j["name"] == "zoe-nightly-dreaming.py" for j in host3.jobs_run)               # refused, but the dreaming it replaced still runs on the 4B
    w4, host4, _ = make(tmp_path, argv=["--retry-load"])
    assert w4.run() == nw.EXIT_OK and json.loads(w4.failures_path.read_text())["count"] == 0   # a success resets the count
    for _ in (1, 2):
        make(tmp_path, llm_dies=True)[0].run()
    w5, host5, _ = make(tmp_path, boot_id="boot-2")
    assert w5.run() == nw.EXIT_OK                                                             # a reboot resets it too


def test_the_trial_still_measures_the_4b_when_the_12b_will_not_load_and_says_so(tmp_path):
    w, host, cfg = make(tmp_path, argv=["--trial"], start=at(3, 5), llm_dies=True)
    assert w.run() == nw.EXIT_ABORTED and "did not load" in w.outcome
    t = w.rec["trial"]
    assert t["12B"]["load_failed"] and t["4B@32k"]["pass"] == 1 and any("zoe-night-4b32k" in c for c in host.joined() if c.startswith("systemd-run"))
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]
    assert "did not load" in next(cfg.report_dir.glob("*-trial*.md")).read_text()


def test_nvmaps_own_largest_block_is_read_logged_and_recorded_without_blocking_the_attempt(tmp_path):
    assert nw.parse_nvmap_free_mib("Max allocatable IOVMM memory: 2339717120 bytes\n") == pytest.approx(2231.3, abs=0.1) and nw.parse_nvmap_free_mib("") is None
    w, host, _ = make(tmp_path, nvmap_mib=6100.0)                           # 2026-10-09 hypothesis: a buffer larger than NvMap's largest block cannot be allocated
    assert w.run() == nw.EXIT_OK                                            # still tried (it is a hypothesis, not a gate) and the figure is in the report
    assert w.rec["nvmap_max_alloc_mib"] == 6100 and any("LESS than the model buffer" in e["msg"] for e in w.rec["events"])
    w2, host2, _ = make(tmp_path, nvmap_mib=12000.0, sudo=False)
    assert w2.run() == nw.EXIT_OK and w2.rec["nvmap_max_alloc_mib"] is None
    w3, _, _ = make(tmp_path, llm_dies=True)
    w3.run()
    assert "NvMap largest allocatable block 12000 MiB" in w3.rec["load_diagnosis"]["ram"]


def test_unified_memory_is_an_env_lever_on_the_12b_only_default_on_for_a_jetson(tmp_path, monkeypatch):
    """2026-10-09: the 12B's one 6,637 MiB weight buffer is refused by cudaMalloc with 4-5 GB spare; GGML_CUDA_ENABLE_UNIFIED_MEMORY=1 makes ggml use cudaMallocManaged."""
    lv = nw.Levers("qat", 16384, "q8_0")
    on = nw.llm_spec(PARKED, nw.NightCfg(unified=True), lv)
    off = nw.llm_spec(PARKED, nw.NightCfg(unified=False), lv)
    assert on["env"]["GGML_CUDA_ENABLE_UNIFIED_MEMORY"] == "1" and "GGML_CUDA_ENABLE_UNIFIED_MEMORY" not in off["env"]
    assert on["env"]["LD_LIBRARY_PATH"] == off["env"]["LD_LIBRARY_PATH"]                       # nothing else about the environment changes
    assert "--batch-size" in on["argv"] and on["argv"][on["argv"].index("--ubatch-size") + 1] == "128"       # June's small compute buffer stays
    assert "GGML_CUDA_ENABLE_UNIFIED_MEMORY" not in nw.clone4_spec(LIVE_UNIT, nw.NightCfg(unified=True))["env"]   # the 4B at 32k is exactly the live brain's setup
    assert nw.configure(nw.build_parser().parse_args(["--no-unified"]), nw.NightCfg(unified=True)).unified is False
    assert nw.configure(nw.build_parser().parse_args([]), nw.NightCfg(unified=True)).unified is True
    w, host, _ = make(tmp_path, cfg_kw={"unified": True})
    assert w.run() == nw.EXIT_OK
    assert "--setenv=GGML_CUDA_ENABLE_UNIFIED_MEMORY=1" in next(c for c in host.joined() if "zoe-night-12b" in c and c.startswith("systemd-run"))
    assert w.rec["arith"]["unified_memory"] is True and w.rec["arith"]["load_s"] >= 0 and any("UNIFIED_MEMORY=1 (set" in e["msg"] for e in w.rec["events"])
    w2, host2, _ = make(tmp_path, argv=["--no-unified"], cfg_kw={"unified": True})
    w2.run()
    assert "UNIFIED_MEMORY" not in next(c for c in host2.joined() if "zoe-night-12b" in c and c.startswith("systemd-run")) and w2.rec["arith"]["unified_memory"] is False


def test_ngl_nomlock_and_fit_are_levers_that_change_only_their_flag():
    base = nw.llm_spec(PARKED, nw.NightCfg(), nw.Levers("qat", 32768, "q8_0"))["argv"]
    ngl = nw.llm_spec(PARKED, nw.NightCfg(ngl=28), nw.Levers("qat", 32768, "q8_0"))["argv"]
    assert ngl[ngl.index("--n-gpu-layers") + 1] == "28" and base[base.index("--n-gpu-layers") + 1] == "99"
    assert "--mlock" not in nw.llm_spec(PARKED, nw.NightCfg(mlock=False), nw.Levers("qat", 32768, "q8_0"))["argv"] and "--mlock" in base
    args = nw.build_parser().parse_args(["--ngl", "30", "--no-mlock", "--fit-off", "--retry-load"])
    cfg = nw.configure(args, nw.NightCfg())
    assert (cfg.ngl, cfg.mlock, cfg.fit_off, cfg.retry_load) == (30, False, True, True)


def test_the_night_mind_entry_point_is_used_as_soon_as_it_exists_with_the_served_context_and_speed(tmp_path):
    script = tmp_path / "zoe-night-mind.py"
    script.write_text("# stand-in")
    w, host, _ = make(tmp_path, cfg_kw={"night_mind_script": script})
    assert w.run() == nw.EXIT_OK, w.outcome
    nm = next(j for j in host.jobs_run if j["name"] == "zoe-night-mind.py")
    assert nm["argv"][2:] == ["--model-url", "http://127.0.0.1:11500/v1", "--ctx-tokens", "32768", "--all-members", "--decode-tok-s", "4.20", "--prefill-tok-s", "120.5"]     # both measured rates
    assert [j["name"] for j in host.jobs_run][:3] == ["night_digest.py", "zoe-nightly-dreaming.py", "zoe-night-mind.py"]
    assert not any(r["name"] == "night_mind" and r["backend"] == "4B" for r in w.jobs)                  # no 4B fallback for the new pass
    custom = nw.build_jobs(nw.NightCfg(night_mind_cmd="python /x/nm.py --u {model_url} --c {ctx_tokens} --t {decode_tps} --p {prefill_tps}", night_mind_script=tmp_path / "no.py"), 16384, 11.5, 136.0)
    assert custom[-1].argv == ["python", "/x/nm.py", "--u", "http://127.0.0.1:11500", "--c", "16384", "--t", "11.50", "--p", "136.0"]
    script2 = tmp_path / "nm2.py"
    script2.write_text("# stand-in")
    only_decode = nw.build_jobs(nw.NightCfg(night_mind_script=script2), 16384, 3.62)[-1].argv
    assert "--decode-tok-s" in only_decode and "--prefill-tok-s" not in only_decode                 # no probe result for prefill: the pass keeps its own default
    assert nw.build_jobs(nw.NightCfg(night_mind_script=tmp_path / "no.py"))[-1].skip_reason.startswith("no.py not found")


def test_the_trial_also_scores_the_night_mind_cells_when_the_entry_point_exists(tmp_path):
    _trial_cells(tmp_path, pretty=True)


def test_the_cells_object_is_read_from_the_compact_one_line_stdout_too(tmp_path):
    _trial_cells(tmp_path, pretty=False)


def _trial_cells(tmp_path, *, pretty):
    script = tmp_path / "zoe-night-mind.py"
    script.write_text("# stand-in")
    w, host, cfg = make(tmp_path, argv=["--trial"], start=at(3, 5), cfg_kw={"night_mind_script": script})
    real = host.run_watched
    cells_argv: list = []

    def rw(argv, timeout, env, tick, log_path, interval=5.0):
        if "--cells" in argv:
            cells_argv.append(list(argv))
            host.cmds.append((list(argv), True))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            obj = {"status": "ok", "model": "12B", "members": [], "totals": {"calls": 9}, "cells": {"K1": "PASS", "K6": "FAIL", "pass": 1, "fail": 1, "k1": {"judged": 4}}}
            # the real log: stderr lines (some with braces) around the CLI's stdout object - indented over many lines (the old CLI) or one compact line (the new one)
            log_path.write_text("NIGHT_MIND user=- status={ok}\nloading {model}\n" + (json.dumps(obj, indent=2, sort_keys=True) if pretty else json.dumps(obj)) + "\ndone {}\n")
            return bk.Result(0, "")
        return real(argv, timeout, env, tick, log_path, interval)
    host.run_watched = rw
    assert w.run() == nw.EXIT_OK, w.outcome
    assert w.rec["trial"]["12B"]["night_mind_cells"]["K6"] == "FAIL"
    twelve = cells_argv[0]                                                                       # the 12B phase: the cells get the measured rates (they used the 4B's, 2026-10-09)
    assert twelve[twelve.index("--decode-tok-s") + 1] == "4.20" and twelve[twelve.index("--prefill-tok-s") + 1] == "120.5" and twelve[twelve.index("--model-name") + 1] == "12B"
    assert all("--decode-tok-s" not in a for a in cells_argv[1:])                                # the 4B@32k phase is not sized by the 12B's speed
    assert "| K6 | FAIL | FAIL |" in next(cfg.report_dir.glob("*-trial*.md")).read_text()


# ── loud failure ─────────────────────────────────────────────────────────────

def test_a_unit_that_stays_down_is_retried_then_reported_loudly_and_the_others_are_still_woken(tmp_path):
    w, host, cfg = make(tmp_path, unhealthy=(nw.KOKORO,))
    assert w.run() == nw.EXIT_RESTORE_FAILED
    assert host.count(f"--user restart {nw.KOKORO}") == 1                     # one retry, with a restart
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]  # router and zoe-data were still woken after Kokoro failed
    alarm = (cfg.report_dir / "ALARM").read_text()
    assert "RESTORE FAILED" in alarm and nw.KOKORO in alarm and "--restore-only" in alarm
    assert nw.BRAIN not in alarm.split("still down or unhealthy:")[1].splitlines()[0]
    assert cfg.marker.exists() and json.loads(cfg.marker.read_text())["stopped"] == [nw.KOKORO]          # kept, so --restore-only retries exactly that unit
    assert "NOT HEALTHY" in w.restore_status and w.rec["restore"][nw.KOKORO]["ok"] is False and w.rec["restore"][nw.ROUTER]["ok"] is True


def test_a_cpu_kokoro_is_not_healthy_and_a_brain_that_never_answers_is_reported(tmp_path):
    assert nw.HEALTH[nw.KOKORO].ok('{"status":"ok","device":"cpu","pipeline_loaded":true}') is False
    assert nw.HEALTH[nw.KOKORO].ok('{"status":"ok","device":"cuda","pipeline_loaded":false}') is False
    assert nw.HEALTH[nw.KOKORO].ok('{"status":"ok","device":"cuda","pipeline_loaded":true}') is True
    assert nw.HEALTH[nw.ZOE_DATA].ok('{"status":"degraded"}') is False and nw.HEALTH[nw.BRAIN].ok("not json") is False
    w, host, cfg = make(tmp_path, unhealthy=(nw.BRAIN,))
    assert w.run() == nw.EXIT_RESTORE_FAILED and nw.BRAIN in (cfg.report_dir / "ALARM").read_text()
    assert host.count(f"--user restart {nw.BRAIN}") == 1 and w.jobs and all(j["backend"] != "4B" or j["status"] != "ok" for j in w.jobs)        # no 4B fallback against a dead brain


def test_restore_only_wakes_exactly_what_the_marker_lists_in_the_owners_order(tmp_path):
    w, host, cfg = make(tmp_path, argv=["--restore-only"], active={u: False for u in nw.STOP_ORDER})
    cfg.night_dir.mkdir(parents=True, exist_ok=True)
    host.files[str(cfg.marker)] = json.dumps({"pid": 1, "run_id": "x", "stopped": [nw.ZOE_DATA, nw.KOKORO, nw.BRAIN]})
    assert w.load_marker() and w.restore() is True
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ZOE_DATA]            # the router was not in the marker: not started
    w2, host2, _ = make(tmp_path, argv=["--restore-only"])
    assert not w2.load_marker() and w2.restore() is True and wake_order(host2) == []


def test_a_restore_only_takes_the_brain_lock_first_so_two_recoveries_never_overlap(tmp_path):
    """Greptile 1929: --restore-only read the marker and restored without the lock; two recoveries (or a recovery and a new window) could overlap."""
    w, host, cfg = make(tmp_path, argv=["--restore-only"], active={u: False for u in nw.STOP_ORDER})
    host.files[str(cfg.marker)] = json.dumps({"pid": 1, "run_id": "x", "stopped": [nw.BRAIN]})
    fd = os.open(cfg.lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)                                           # another recovery / a window is running
    try:
        assert nw.main(["--restore-only", "--night-dir", str(cfg.night_dir)], host_factory=lambda lg: host, cfg=cfg) == nw.EXIT_REFUSED
    finally:
        os.close(fd)
    assert wake_order(host) == []                                                           # it woke nothing
    assert nw.main(["--restore-only", "--night-dir", str(cfg.night_dir)], host_factory=lambda lg: host, cfg=cfg) == nw.EXIT_OK and wake_order(host) == [nw.BRAIN]
    assert w.probe_lock()                                                                   # and it released the lock afterwards


def test_a_restore_only_never_races_a_window_that_is_still_running(tmp_path):
    """2026-10-09: a second start was refused (the lock was held), its shell trap ran --restore-only on the FIRST window's marker, and the two restores raced into a false ALARM."""
    w, host, cfg = make(tmp_path, argv=["--restore-only"], active={u: False for u in nw.STOP_ORDER})
    host.files[str(cfg.marker)] = json.dumps({"pid": 4242, "run_id": "x", "stopped": [nw.BRAIN]})
    host.present.add("/proc/4242")
    host.files["/proc/4242/cmdline"] = "python\0/x/night_window.py\0--trial"
    assert w.load_marker() and w.marker_owner_alive
    rc = nw.main(["--restore-only", "--night-dir", str(cfg.night_dir)], host_factory=lambda lg: host, cfg=cfg)
    assert rc == nw.EXIT_OK and wake_order(host) == []                          # it did nothing
    host.files["/proc/4242/cmdline"] = "some other program"                      # a recycled pid after a reboot is not the window
    w2, _, _ = make(tmp_path, argv=["--restore-only"])
    w2.host = host
    assert w2.load_marker() and not w2.marker_owner_alive and w2.restore() and wake_order(host) == [nw.BRAIN]


# ── the dry run ──────────────────────────────────────────────────────────────

def test_the_dry_run_prints_the_table_says_what_fits_and_changes_nothing(tmp_path):
    lines = []
    w, host, cfg = make(tmp_path, argv=["--dry-run"], base=3000.0)
    w.log = lines.append
    assert w.run() == nw.EXIT_OK
    out = "\n".join(lines)
    assert [c for c in host.mutating() if not c.startswith(("sudo -n true",))] == [] or all(m is False for _a, m in host.cmds if _a[0] in ("systemd-run", "docker"))
    assert w.lock_fd is None and not cfg.marker.exists() and not cfg.report_dir.exists()
    for lv in ("qat ctx 32768 KV q8_0", "qat ctx 16384 KV q4_0", "q4km ctx 32768 KV q8_0"):
        assert lv in out
    assert "stop set: stop all (default)" in out and "stop set: keep zoe-data" in out and "keep zoe-data + router" in out
    assert "FITS TODAY (predicted, default stop set): qat ctx 32768 KV q8_0" in out and "the 12B command, generated from the parked unit" in out
    assert "wake llama-server.service" in out and "job digest" in out and "DRY-RUN: nothing was changed" in out


def test_the_dry_run_says_when_it_does_not_fit_and_why_a_real_run_would_be_refused(tmp_path):
    lines = []
    w, host, _ = make(tmp_path, argv=["--dry-run"], base=-1500.0, start=at(14, 0))
    w.log = lines.append
    w.run()
    out = "\n".join(lines)
    assert "DOES NOT FIT TODAY" in out and "would be REFUSED" in out and "--anytime" in out
    assert [c for c in host.cmds if c[1] and c[0][0] in ("systemd-run", "docker")] == []


def test_the_real_dry_run_command_line_changes_nothing_and_prints_the_table():
    """The real CLI against the real box (read-only commands only): exits 0 and prints the arithmetic. Skipped where the operator's files do not exist (CI)."""
    if not Path(HOME, ".config/systemd/user/llama-server-12b-deepbrain.service.disabled").exists():
        pytest.skip("not the Zoe host")
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME")}
    r = subprocess.run([sys.executable, str(REPO / "scripts/night/night_window.py"), "--dry-run", "--anytime"], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode in (0, 2), r.stderr[-500:]
    assert "THE ARITHMETIC" in r.stdout and "qat ctx 32768 KV q8_0" in r.stdout


# ── the trial ────────────────────────────────────────────────────────────────

def test_the_trial_scores_the_12b_then_the_4b_at_32k_and_restores(tmp_path):
    w, host, cfg = make(tmp_path, argv=["--trial"], start=at(3, 5))
    assert w.run() == nw.EXIT_OK, w.outcome
    assert cfg.jobs == () and not cfg.fallback_4b
    systemd = [c for c in host.joined() if c.startswith("systemd-run")]
    assert any("zoe-night-embed" in c and "--model minilm" in c for c in systemd)              # ZMA refuses any embedder but MiniLM
    assert any("zoe-night-12b" in c for c in systemd)
    clone = next(c for c in systemd if "zoe-night-4b32k" in c)
    assert "--ctx-size 32768" in clone and "--port 11500" in clone and "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf" in clone and "--parallel 1" in clone
    assert host.idx("--user stop zoe-night-12b") < host.idx("zoe-night-4b32k")                 # one model at a time on :11500
    t = w.rec["trial"]
    assert t["12B"]["pass"] == 2 and t["4B@32k"]["pass"] == 1 and t["12B"]["items"] == [6, 8] and t["12B"]["tokens_predicted"] == 1000
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]
    md = next(cfg.report_dir.glob("*-trial*.md")).read_text()
    assert "| K2.a | PASS | FAIL |" in md and "Trial: K cells, 12B vs the 4B at 32k" in md


def test_a_trial_phase_without_a_result_is_not_an_ok_outcome_and_the_12b_timeout_follows_its_speed(tmp_path):
    w, host, cfg = make(tmp_path, argv=["--trial"], start=at(3, 5))
    real = host.run_watched

    def rw(argv, timeout, env, tick, log_path, interval=5.0):
        if "--out" in argv and argv[argv.index("--model-name") + 1] == "12B":
            host.cmds.append((list(argv), True))
            host.t += 720
            return bk.Result(124, "")                                              # the driver timed out and wrote nothing
        return real(argv, timeout, env, tick, log_path, interval)
    host.run_watched = rw
    assert w.run() == nw.EXIT_ABORTED and "without a result" in w.outcome and "12B" in w.outcome
    assert w.rec["trial"]["4B@32k"]["pass"] == 1 and wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]
    w2, _, _ = make(tmp_path, argv=["--trial"], start=at(3, 5))
    w2.cap_min = 65.0
    w2.speed = {"decode_tps": 5.42}
    assert w2.trial_timeout_12b() == pytest.approx(720 * 11 / 5.42, rel=1e-6)
    w2.speed = {"decode_tps": 40.0}
    assert w2.trial_timeout_12b() == 720.0
    w2.speed = {"decode_tps": 1.0}
    assert w2.trial_timeout_12b() <= (w2.cap_min - w2.cfg.reserve_min) * 60 - 300


def test_skip_4b_ends_an_exploratory_trial_after_the_12b_phase_and_still_restores(tmp_path):
    w, host, _ = make(tmp_path, argv=["--trial", "--skip-4b"], start=at(3, 5), llm_dies=True)
    assert w.run() == nw.EXIT_ABORTED and "did not load" in w.outcome
    assert not any("zoe-night-4b32k" in c for c in host.joined() if c.startswith("systemd-run")) and wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]
    w2, host2, _ = make(tmp_path, argv=["--trial", "--skip-4b"], start=at(3, 5))
    assert w2.run() == nw.EXIT_OK and "4B@32k" not in w2.rec["trial"] and w2.rec["trial"]["12B"]["pass"] == 2
    (tmp_path / "e").mkdir()
    w3, host3, _ = make(tmp_path / "e", argv=["--trial", "--skip-4b"], start=at(3, 5), trial_no_output=True)        # Greptile 1929: the driver wrote nothing; the early return reported ok
    assert w3.run() == nw.EXIT_ABORTED and "without a result" in w3.outcome and wake_order(host3) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]


def test_a_trial_is_refused_across_03_00_because_it_has_no_digest_job_to_cover_the_skipped_loop(tmp_path):
    w, host, _ = make(tmp_path, argv=["--trial"], start=at(2, 50))
    assert w.run() == nw.EXIT_REFUSED and "maintenance loop" in w.outcome and stopped_nothing(host)


def test_the_reflect_summary_reads_the_zma_drivers_json():
    s = nw.reflect_summary({"reflect": {"k_cells": [{"id": "K1", "verdict": "PASS", "evidence": {"items": [3, 4]}}, {"id": "K2", "verdict": "SKIP"}], "wall_s": 9.5}})
    assert s["pass"] == 1 and s["graded"] == 1 and s["items"] == [3, 4] and s["wall_s"] == 9.5
    assert nw.reflect_summary({"error": "boom"})["error"] == "boom"


# ── the shipped files ────────────────────────────────────────────────────────

SYSTEMD = REPO / "scripts" / "night" / "systemd"


def test_the_service_template_survives_a_long_night_and_always_restores():
    text = (SYSTEMD / "zoe-night-window.service").read_text()
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "Type=oneshot" in body and re.search(r"TimeoutStartSec=(\d+)", body) and int(re.search(r"TimeoutStartSec=(\d+)", body).group(1)) >= 65 * 60 + 600
    assert "ExecStopPost=%h/assistant/scripts/night/night_window.sh --restore-only" in body and "ExecStart=%h/assistant/scripts/night/night_window.sh\n" in body + "\n"
    assert "EnvironmentFile=%h/assistant/services/zoe-data/.env" in body and "[Install]" not in body              # never enabled by itself: the timer is the entry
    assert not re.search(r"^(After|Requires|Wants|BindsTo)=.*(zoe-data|llama-server)", body, re.M)               # it STOPS those units: it must not depend on them


def test_the_timer_template_never_fires_in_the_daytime_and_starts_after_the_other_nightly_jobs():
    text = (SYSTEMD / "zoe-night-window.timer").read_text()
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    assert "OnCalendar=*-*-* 02:50:00" in body and "Persistent=false" in body and "Persistent=true" not in body
    h, m = re.search(r"OnCalendar=\*-\*-\* (\d+):(\d+)", body).groups()
    assert (int(h) * 60 + int(m)) > 2 * 60 + 40 + 5                       # after the 02:40 (+5 min) memory export
    latest = int(h) * 60 + int(m) + 2                                      # RandomizedDelaySec=120
    assert nw.NightCfg().end_by - latest >= 35 + nw.NightCfg().reserve_min   # even the latest start leaves a usable window before 03:55 (the cap shrinks to fit)


def test_the_installer_adds_two_units_absorbs_dreaming_and_the_uninstaller_undoes_it(tmp_path):
    shim = tmp_path / "systemctl"
    shim.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        echo "systemctl $*" >> {tmp_path}/calls
        case "$*" in *"is-enabled zoe-dreaming.timer"*) echo enabled;; *"is-enabled"*) echo disabled; exit 1;; esac
        exit 0
        """))
    shim.chmod(0o755)
    env = {**os.environ, "NIGHT_SYSTEMCTL": str(shim), "NIGHT_UNIT_DIR": str(tmp_path / "units"), "NIGHT_DIR": str(tmp_path / "nd"), "HOME": str(tmp_path)}
    script = str(REPO / "scripts/night/install_night_window.sh")
    dry = subprocess.run(["bash", script, "--dry-run"], capture_output=True, text=True, env=env, timeout=60)
    assert dry.returncode == 0 and "DRY-RUN: systemctl --user disable --now zoe-dreaming.timer" in dry.stdout and not (tmp_path / "units").exists() and not (tmp_path / "nd" / "INSTALLED").exists()
    inst = subprocess.run(["bash", script], capture_output=True, text=True, env=env, timeout=60)
    assert inst.returncode == 0, inst.stderr
    assert sorted(p.name for p in (tmp_path / "units").iterdir()) == ["zoe-night-window.service", "zoe-night-window.timer"]
    calls = (tmp_path / "calls").read_text()
    assert "disable --now zoe-dreaming.timer" in calls and "enable --now zoe-night-window.timer" in calls and calls.index("daemon-reload") < calls.index("enable --now zoe-night-window")
    assert "dreaming_timer_was=enabled" in (tmp_path / "nd" / "INSTALLED").read_text()
    un = subprocess.run(["bash", script, "--uninstall"], capture_output=True, text=True, env=env, timeout=60)
    assert un.returncode == 0 and list((tmp_path / "units").iterdir()) == [] and not (tmp_path / "nd" / "INSTALLED").exists()
    assert "enable --now zoe-dreaming.timer" in (tmp_path / "calls").read_text().split("disable --now zoe-night-window.timer")[1]


def _stateful_systemctl(tmp_path, fail_enable_window=False):
    """A systemctl double that remembers zoe-dreaming.timer's state in a file (enable/disable change it), so a sequence of installs can be played."""
    state = tmp_path / "dreaming.state"
    state.write_text("enabled\n")
    shim = tmp_path / "systemctl"
    shim.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        echo "systemctl $*" >> {tmp_path}/calls
        case "$*" in
          *"is-enabled zoe-dreaming.timer"*) cat {state};;
          *"disable --now zoe-dreaming.timer"*) echo disabled > {state};;
          *"enable --now zoe-dreaming.timer"*) echo enabled > {state};;
          *"enable --now zoe-night-window.timer"*) {"exit 1" if fail_enable_window else "exit 0"};;
          *"is-enabled"*) echo disabled; exit 1;;
        esac
        exit 0
        """))
    shim.chmod(0o755)
    env = {**os.environ, "NIGHT_SYSTEMCTL": str(shim), "NIGHT_UNIT_DIR": str(tmp_path / "units"), "NIGHT_DIR": str(tmp_path / "nd"), "HOME": str(tmp_path)}
    return env, state


def test_reinstalling_keeps_the_original_dreaming_state_so_uninstall_still_restores_it(tmp_path):
    """Greptile 1929: the second install saw the timer already disabled and recorded dreaming_timer_was=disabled; uninstall then left neither nightly schedule running."""
    env, state = _stateful_systemctl(tmp_path)
    script = str(REPO / "scripts/night/install_night_window.sh")
    for _ in range(2):
        assert subprocess.run(["bash", script], capture_output=True, text=True, env=env, timeout=60).returncode == 0
    assert state.read_text().strip() == "disabled" and "dreaming_timer_was=enabled" in (tmp_path / "nd" / "INSTALLED").read_text()
    assert subprocess.run(["bash", script, "--uninstall"], capture_output=True, text=True, env=env, timeout=60).returncode == 0
    assert state.read_text().strip() == "enabled"                                              # the dreaming timer is back


def test_a_failed_install_rolls_dreaming_back_and_leaves_a_record_for_uninstall(tmp_path):
    """Greptile 1929: disable-dreaming ran before the record was saved; a failing enable of the window timer then lost the night schedule with no way to restore it."""
    env, state = _stateful_systemctl(tmp_path, fail_enable_window=True)
    script = str(REPO / "scripts/night/install_night_window.sh")
    r = subprocess.run(["bash", script], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode != 0 and state.read_text().strip() == "enabled"                        # rolled back: dreaming still runs tonight
    assert "dreaming_timer_was=enabled" in (tmp_path / "nd" / "INSTALLED").read_text()         # and the record was saved before anything was flipped


def test_the_wrapper_is_shell_clean_and_has_the_restore_net():
    for f in ("night_window.sh", "install_night_window.sh"):
        assert subprocess.run(["bash", "-n", str(REPO / "scripts/night" / f)]).returncode == 0
    sh = (REPO / "scripts/night/night_window.sh").read_text()
    assert "trap restore EXIT" in sh and "--restore-only" in sh and "WINDOW_OPEN" in sh and os.access(REPO / "scripts/night/night_window.sh", os.X_OK)


# ── the pieces it relies on elsewhere ────────────────────────────────────────

def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_dreaming_script_can_skip_or_only_run_the_compaction_trigger(monkeypatch, capsys):
    import asyncio
    import types
    m = _load(REPO / "scripts/maintenance/zoe-nightly-dreaming.py", "zoe_nightly_dreaming_night")
    assert m.parse_args([]).skip_compaction is False and m.parse_args(["--skip-compaction"]).skip_compaction and m.parse_args(["--only-compaction"]).only_compaction
    with pytest.raises(SystemExit):
        m.parse_args(["--skip-compaction", "--only-compaction"])
    calls = []
    monkeypatch.setattr(m, "weekly_index_compaction", lambda *a, **k: calls.append("compaction") or 0)
    assert asyncio.run(m.main(only_compaction=True)) == 0 and calls == ["compaction"]
    assert "compaction trigger complete" in capsys.readouterr().out

    class Ctx:
        async def __aenter__(self): return object()
        async def __aexit__(self, *a): return False

    async def noop(*a, **k): return None

    async def dream(db): return [{"user_id": "demo"}]
    fake = types.SimpleNamespace(init_pool=noop, close_pool=noop, get_db_ctx=lambda: Ctx())
    monkeypatch.setitem(sys.modules, "db_pool", fake)
    monkeypatch.setattr(m, "memory_quality_snapshot", lambda: {"ok": 1})
    monkeypatch.setattr(m, "run_dreaming", dream)
    monkeypatch.setattr(m, "run_music_digest", dream)
    calls.clear()
    assert asyncio.run(m.main(skip_compaction=True)) == 0 and calls == []                    # zoe-data is down: the trigger would silently return 0 ("index health unavailable")
    assert "skipped (--skip-compaction" in capsys.readouterr().out
    assert asyncio.run(m.main()) == 0 and calls == ["compaction"]                             # the default is unchanged: the trigger runs


def test_the_digest_runner_copies_the_loops_body_and_reports_counts_only():
    import asyncio
    d = _load(REPO / "scripts/night/jobs/night_digest.py", "night_digest_job")
    order = []

    async def run_pass():
        order.append("pass")
        return {"results": [{"user_id": "demo"}], "input_seen": True}

    def record(results, input_seen=None):
        order.append("record")
        return {"users": 1, "effect_count": 4, "verdict": "productive", "attempted": 1, "skipped": 0, "errors": 0}

    async def evo():
        order.append("notice")
        return {"count": 2, "text": "a household sentence", "ok": True}

    async def measure():
        order.append("measure")
        raise RuntimeError("non-fatal, like the loop")
    rc, out = asyncio.run(d.run_digest_body(run_pass=run_pass, record=record, evolution=evo, measure=measure))
    assert rc == 0 and order == ["pass", "record", "notice", "measure"]
    assert out["evolution_notice"] == {"count": 2, "ok": True} and out["evolution_measure"] == {"error": "RuntimeError"} and "household" not in json.dumps(out)
    rc2, _ = asyncio.run(d.run_digest_body(run_pass=run_pass, record=lambda r, input_seen=None: {"verdict": "extractor_errors"}, evolution=evo, measure=evo))
    assert rc2 == 3                                                                               # the 12B could not serve the pass: the window runs it again on the 4B
    assert d.scalars({"a": 1, "b": "text", "c": None, "d": True}) == {"a": 1, "c": None, "d": True}
