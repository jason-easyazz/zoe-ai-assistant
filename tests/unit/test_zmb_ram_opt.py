"""ZMB RAM lab (``scripts/perf/zmb/ram_opt.py``, ``stub_embed.py``, ``lean_imports/sitecustomize.py``): the pure parts. Slim-lane safe: no server, no container, no model.

The lab itself (it starts a scratch Postgres, the shim and a Hindsight server) is exercised by running it; what is pinned here is what its numbers rest on: the memory-floor
admission and watchdog arithmetic, the steady / burst summary (the window's own rule), the hit@5 judge, the Postgres command (loopback port, memory AND swap capped), the stub
embedder's protocol, and the import stubs (they must chain the egress hook, never switch it off).
"""
from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import ram_opt, stub_embed  # noqa: E402


def test_percentile_and_hit_at_k_are_what_the_report_means_by_them():
    assert ram_opt.percentile([], 0.5) == 0.0
    assert ram_opt.percentile([10, 20, 30, 40], 0.5) == 25.0
    assert ram_opt.percentile(list(range(1, 101)), 0.95) == pytest.approx(95.05)
    rows = [{"text": "other"}] * 4 + [{"text": "User's friend Aldo lives in Bergvik."}, {"text": "Bergvik again"}]
    assert ram_opt.hit_at_k(rows, "bergvik", 5) is True               # the fifth row is inside the top five
    assert ram_opt.hit_at_k(rows[:4] + rows[5:], "bergvik", 4) is False and ram_opt.hit_at_k([{"text": "x"}] * 5 + rows[4:], "bergvik", 5) is False   # the sixth is not
    assert ram_opt.hit_at_k([], "x") is False


def test_summary_is_the_windows_rule_median_is_steady_and_max_is_burst_over_the_named_phases():
    s = [{"label": "ingest", "rss_mb": 400.0, "pids": 3}, {"label": "warm", "rss_mb": 410.0, "pids": 3}, {"label": "latency", "rss_mb": 420.0, "pids": 3},
         {"label": "storm", "rss_mb": 470.0, "pids": 3}, {"label": "idle0", "rss_mb": 900.0, "pids": 3}, {"label": "post", "rss_mb": 430.0, "pids": 0}]
    r = ram_opt.summarise(s, ("ingest", "warm", "latency", "storm"))
    assert r == {"steady_mb": 415.0, "burst_mb": 470.0, "samples": 4}                     # idle0 (start-up) is not a workload sample; a sample with no process is not a sample
    assert ram_opt.summarise(s, ("nothing",)) == {"steady_mb": None, "burst_mb": None, "samples": 0}


def test_the_postgres_command_is_loopback_only_and_capped_in_memory_and_swap(tmp_path):
    cfg = ram_opt.Config(name="t", pg_flags=("shared_buffers=16MB", "jit=off"), pg_mem="128m")
    cmd = ram_opt.pg_command(cfg, tmp_path)
    assert cmd[:3] == ["docker", "run", "-d"] and "127.0.0.1:55433:5432" in cmd                # never 0.0.0.0, never the window's :55432
    assert cmd[cmd.index("--memory") + 1] == "128m" and cmd[cmd.index("--memory-swap") + 1] == "128m"
    assert cmd[cmd.index("--name") + 1] != "zoe-bakeoff-pg"                                      # the window's container is never touched
    tail = cmd[cmd.index("postgres"):]
    assert tail == ["postgres", "-c", "shared_buffers=16MB", "-c", "jit=off"]


def test_admission_waits_for_room_and_refuses_when_the_box_never_has_it():
    t = {"now": 0.0}
    seq = iter([1600.0, 1700.0, 2100.0, 2100.0, 1800.0, 2100.0] + [2100.0] * 40)
    log = []

    def sleep(s):
        t["now"] += s

    ok = ram_opt.wait_for_room(400.0, 60.0, log.append, floor_mb=1500.0, hold_s=5.0, read=lambda: next(seq), sleep=sleep, clock=lambda: t["now"])
    assert ok is True and t["now"] >= 5.0 + 5.0                                          # a dip back under floor + need restarts the hold: it needed 5 consecutive good seconds
    t["now"] = 0.0
    assert ram_opt.wait_for_room(400.0, 10.0, log.append, floor_mb=1500.0, hold_s=3.0, read=lambda: 1800.0, sleep=sleep, clock=lambda: t["now"]) is False


def test_the_footprint_estimate_counts_the_real_shim_and_not_the_stub():
    assert ram_opt.footprint_estimate_mb(ram_opt.Config(name="a")) > ram_opt.footprint_estimate_mb(ram_opt.Config(name="b", shim_kind="stub")) + 100


def test_a_run_never_asks_for_more_than_200_retains(capsys):
    assert ram_opt.main(["--retains", "201"]) == 2
    assert "refusing more than 200 retains" in capsys.readouterr().err


def test_every_configuration_is_loopback_and_keeps_the_memory_caps():
    assert "base" in ram_opt.CONFIGS and len(ram_opt.CONFIGS) >= 5
    for c in ram_opt.CONFIGS.values():
        assert dict(c.shim_props).get("MemorySwapMax") == "0" or "MemoryMax" not in dict(c.shim_props)
        assert dict(c.hs_props).get("MemorySwapMax") == "0"
        assert c.pg_mem.endswith("m") and int(c.pg_mem[:-1]) <= 256


def test_the_stub_embedder_speaks_the_protocol_and_is_deterministic():
    v1, v2 = stub_embed.embed("Where does Aldo live"), stub_embed.embed("where does aldo live")
    assert len(v1) == stub_embed.DIM == 384 and v1 == v2
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-6
    b = base64.b64encode(struct.pack("<384f", *v1)).decode()
    assert struct.unpack("<384f", base64.b64decode(b))[0] == pytest.approx(v1[0], abs=1e-6)


def test_the_lean_import_stubs_chain_the_egress_hook_and_stay_off_unless_named(tmp_path):
    """sitecustomize is found ONCE on sys.path: a lean directory in front of the audit directory must still load the audit hook (else the G0 egress gate silently reads 'not measured'),
    and with no ZMB_LEAN_STUBS it must import nothing different."""
    log = tmp_path / "egress.log"
    env = {**os.environ, "PYTHONPATH": str(REPO / "scripts/perf/zmb/lean_imports"), "EGRESS_AUDIT_LOG": str(log), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("ZMB_LEAN_STUBS", None)
    r = subprocess.run([sys.executable, "-c", "import sys; print('uvloop' in sys.modules and sys.modules['uvloop'] is None)"], env=env, capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "True" and "hook-loaded" in log.read_text()                 # the audit hook ran (it blocks uvloop and logs hook-loaded)
    env["ZMB_LEAN_STUBS"] = "fakeheavy"
    code = ("import fakeheavy, fakeheavy.sub.deeper as d, sys\n"
            "class A(fakeheavy.Base): pass\n"
            "print(type(fakeheavy.thing()).__name__, d.x.y is not None, 'fakeheavy' in sys.modules)")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[1:] == ["True", "True"]
    r = subprocess.run([sys.executable, "-c", "import fakeheavy3"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "ModuleNotFoundError" in r.stderr                          # only the NAMED packages are stubbed


def test_result_row_reads_a_result_without_crashing_on_a_partial_run():
    assert "BREACH" in ram_opt.result_row({"config": "x", "mode": "chunks", "breach": "MemAvailable 1400 MB < 1500 MB floor"})
    row = ram_opt.result_row({"config": "x", "mode": "chunks", "rss": {"window_style": {"steady_mb": 400.0, "burst_mb": 450.0}},
                              "workload": {"hit_at_5": {"direct": "20/20", "paraphrase": "19/20"}, "recall": {"p50_ms": 10.0, "p95_ms": 20.0}}})
    assert "steady 400.0 burst 450.0" in row and "20/20 19/20" in row and json.dumps(row)


def test_a_stack_that_fails_to_come_up_is_still_stopped_and_the_result_is_still_written(monkeypatch, tmp_path):
    """Found in the lab (2026-10-07): a server that never became healthy (MemoryMax too low) raised inside ``Lab.start`` BEFORE the sampler thread existed; the cleanup then
    called ``join`` on a thread that was never started, raised, and skipped ``lab.stop()``: the Postgres container and the units were left running and the next run found the name taken."""
    calls = []
    monkeypatch.setattr(ram_opt, "LAB", tmp_path)
    monkeypatch.setattr(ram_opt, "wait_for_room", lambda *a, **k: True)
    monkeypatch.setattr(ram_opt.Lab, "start", lambda self, parts=("pg", "shim", "hs"): (_ for _ in ()).throw(RuntimeError("hindsight never became healthy")))
    monkeypatch.setattr(ram_opt.Lab, "stop", lambda self: calls.append("stop"))
    res = ram_opt.run_config(ram_opt.CONFIGS["s-base"], "chunks", 1, 1, "t", log=lambda m: None)
    assert "never became healthy" in res["error"] and calls.count("stop") >= 1
    assert list(tmp_path.glob("ropt-s-base-chunkst-*.json")), "the result file is written even when the stack never came up"
    calls.clear()
    res = ram_opt.run_shim_probe(ram_opt.CONFIGS["h-shim-base"], "t")
    assert res["error"] and "stop" in calls
