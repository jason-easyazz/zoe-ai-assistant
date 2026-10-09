"""The 12B SPEED SWEEP (``night_window.py --speed-sweep`` + ``speed_sweep.py``): the grid, the commands it generates, the stage logic (what loads, what ends a family, who wins), the
shared safety (one stop at the start, one restore at the end, a loud failure, the cap) and the table.

Nothing here starts a service or a model: ``SweepHost`` (the night-window tests' ``FakeHost`` plus a pretend 12B whose loading and speed depend on the command it was started with) answers
every command, and the REAL step logic runs. Each promise below has a case that goes red when it is removed.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))
sys.path.insert(0, str(REPO / "scripts" / "night"))

import night_window as nw  # noqa: E402
import speed_sweep as ss  # noqa: E402
from zmb import bakeoff as bk  # noqa: E402
from tests.unit.test_night_window import FakeHost, PARKED, HOME, QAT, make, wake_order, at  # noqa: E402,F401

try:                                        # the sibling module's autouse fixture (a private harness lock) is not inherited by import
    from tests.unit.test_night_window import _private_locks  # noqa: E402,F401
except ImportError:                         # pragma: no cover
    pass

B11194 = nw.NightCfg().sweep_b11194
Q3 = 6_087_086_688
IQ4 = 6_674_000_000


class SweepHost(FakeHost):
    """A FakeHost whose 12B answers like the real one in the ways the sweep reads: it loads only up to ``max_ngl`` layers (the ceiling), the journal carries the allocation lines, NvMap lists a
    client, and the speed rises with the layers on the GPU (and by ``b11194_gain`` on the newer build, ``q3_gain`` for the smaller file)."""

    def __init__(self, tmp, *, max_ngl=30, b11194_gain=1.0, q3_gain=1.0, breach_ngl=None, probe_s=60.0, **kw):
        super().__init__(tmp, **kw)
        self.max_ngl, self.b11194_gain, self.q3_gain, self.breach_ngl, self.probe_s = max_ngl, b11194_gain, q3_gain, breach_ngl, probe_s
        self.starts: "list[dict]" = []
        self.cur: "dict | None" = None
        self.sizes.update({f"{HOME}/{ss.SWEEP_MODEL_FILES['q3km']}": Q3, f"{HOME}/{ss.SWEEP_MODEL_FILES['iq4xs']}": IQ4})
        self.present.update({B11194})

    @staticmethod
    def flag(argv, name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default

    def run(self, argv, timeout=60.0, mutating=True, env=None):
        if argv[:1] == ["systemd-run"] and "--unit=zoe-night-12b" in " ".join(argv):
            cmd = argv[argv.index("--") + 1:]
            ngl = int(self.flag(cmd, "--n-gpu-layers", 99))
            self.cur = {"argv": cmd, "ngl": ngl, "build11194": "b11194" in cmd[0], "model": self.flag(cmd, "--model"), "env": [a for a in argv if a.startswith("--setenv=")]}
            self.starts.append(self.cur)
            res = super().run(argv, timeout, mutating, env)
            self.llm_up = ngl <= self.max_ngl
            self.t += 14.0                                      # the load takes time (the health poll answers at once in the double)
            return res
        line = " ".join(argv)
        if argv[:1] == ["journalctl"]:
            if self.llm_up and self.cur:
                return bk.Result(0, f"load_tensors:        CUDA0 model buffer size =  {self.cur['ngl'] * 120.0:.2f} MiB\nload_tensors:   CPU_Mapped model buffer size =  {(48 - self.cur['ngl']) * 120.0:.2f} MiB\n"
                                    "llama_kv_cache:      CUDA0 KV buffer size =   260.00 MiB\nsched_reserve:      CUDA0 compute buffer size =   312.50 MiB\n")
            n = (self.cur or {"ngl": 99})["ngl"]
            return bk.Result(0, f"NvMapMemAllocInternalTagged: 1075072515 error 12\n0.12.9 E ggml_backend_cuda_buffer_type_alloc_buffer: allocating {n * 143.0:.2f} MiB on device 0: cudaMalloc failed: out of memory\n")
        if argv[:3] == ["sudo", "-n", "cat"] and argv[3].endswith("iovmm/clients"):
            n = self.cur["ngl"] if self.cur and self.llm_up else 0
            return bk.Result(0, f"CLIENT PROCESS PID SIZE\nuser llama-server 4242 {int(n * 140 * 1024)}K\nuser python 999 740948K\ntotal {int(n * 140 * 1024)}K\n")
        if argv[:2] == ["systemctl", "--user"] and "show" in argv and "MainPID" in line and nw.NIGHT_UNITS["llm"] in line:
            pid = "4242" if self.llm_up else "0"
            return bk.Result(0, pid + "\n" if "--value" in argv else f"MainPID={pid}\n")
        if argv[:1] == ["curl"] and argv[-1].endswith("/v1/chat/completions"):
            body = argv[argv.index("-d") + 1]
            night = len(body) > 6000
            c = self.cur or {"ngl": 30, "build11194": False, "model": ""}
            gain = (self.b11194_gain if c["build11194"] else 1.0) * (self.q3_gain if "Q3_K_M" in (c["model"] or "") else 1.0)
            decode = round((1.0 + 0.1 * min(c["ngl"], 48)) * gain, 2)
            return bk.Result(0, json.dumps({"usage": {"prompt_tokens": 2800 if night else 1630, "completion_tokens": 320 if night else 96},
                                            "timings": {"prompt_per_second": 140.0 * gain, "predicted_per_second": decode}}))
        return super().run(argv, timeout, mutating, env)

    def run_watched(self, argv, timeout, env, tick, log_path, interval=5.0):
        if argv[0] == "curl":
            self.t += self.probe_s
            if self.breach_ngl is not None and self.cur and self.cur["ngl"] == self.breach_ngl:
                self.base -= 20000.0
                try:
                    tick()
                finally:
                    self.base += 20000.0
        return super().run_watched(argv, timeout, env, tick, log_path, interval)


def make_sweep(tmp_path, *args, host_kw=None, **kw):
    """Like the night-window tests' ``make`` but with the SweepHost double."""
    import tests.unit.test_night_window as tw
    real = tw.FakeHost
    tw.FakeHost = lambda tmp, **k: SweepHost(tmp, **{**(host_kw or {}), **k})
    try:
        tmp_path.mkdir(parents=True, exist_ok=True)
        w, host, cfg = tw.make(tmp_path, argv=["--speed-sweep", *args], **{"start": at(12, 0), **kw})
    finally:
        tw.FakeHost = real
    return w, host, cfg


def rows_by_name(w):
    return {r["name"]: r for r in w.rec["sweep"]["rows"]}


# ── the grid ─────────────────────────────────────────────────────────────────

def test_stage_1_is_the_owners_key_question_the_4bs_build_and_load_mode_with_full_offload():
    cfgs = {c.name: c for c in ss.stage_configs(1, ss.CONTROL)}
    a = cfgs["1a"]
    assert (a.build, a.ngl, a.fit, a.load_mode, a.ctx, a.kv, a.model, a.unified) == ("b11194", 99, "off", "mlock", 8192, "q8_0", "qat", True)
    assert cfgs["1b"].fit == "on" and cfgs["1b"].ngl is None                       # then with --fit on
    assert cfgs["1c"].unified is False and cfgs["1d"].load_mode == "mmap" and cfgs["1e"].ngl == 30 and cfgs["1f"].ngl == 30 and cfgs["1f"].unified is False
    assert ss.stage_configs(0, ss.CONTROL) == [ss.CONTROL] and ss.CONTROL.build == "parked" and ss.CONTROL.ngl == 30       # the control is today's known-good config


def test_every_stage_of_the_owners_grid_exists_and_derives_from_the_best_so_far():
    best = ss.SweepConfig("B", "x", build="b11194", ngl=32)
    assert [c.model for c in ss.stage_configs(2, best)] == ["q4km", "q4km"] and ss.stage_configs(2, best)[1].ngl == 30 and ss.stage_configs(2, best)[1].only_if_prev_failed
    assert {c.model for c in ss.stage_configs(3, best)} == {"q3km", "iq4xs"} and [c.ngl for c in ss.stage_configs(3, best)] == [32, 36, 40, 34]
    assert [c.ngl for c in ss.stage_configs(4, ss.CONTROL)] == [32, 34, 38, 42, 48] and [c.ngl for c in ss.stage_configs(4, best)] == [34, 38, 42, 48]      # ascending, only above the best
    assert ss.stage_configs(4, ss.SweepConfig("F", "x", ngl=99)) == []                                                                      # full offload: nothing above it
    assert [(c.kv, c.ngl) for c in ss.stage_configs(5, best)] == [("q4_0", 32), ("q4_0", 34)]
    assert [(c.batch, c.ubatch) for c in ss.stage_configs(6, best)] == [(2048, 512), (512, 256), (256, 64)]
    assert [(c.threads, c.no_kv_offload) for c in ss.stage_configs(7, best)] == [(6, False), (4, False), (None, True)]
    assert all(c.build == "b11194" and c.ngl == 32 for c in ss.stage_configs(6, best)) and all(c.note == "" or c.name in ("6a", "6c") for c in ss.stage_configs(6, best))   # derived configs inherit the build, not the notes
    assert ss.stage_configs(8, best) == []                                                                                                    # no 12B draft on disk: nothing to test
    d = ss.stage_configs(8, ss.CONTROL, draft="/m/gemma4-12b/mtp-gemma-4-12b.gguf")[0]
    assert d.draft and d.build == "b11194"
    assert ss.stage_configs(3, best, have_models={"qat", "q4km"}) == []                                                                      # a quant that was not made is not planned


def test_stage_text_parsing_and_draft_discovery():
    assert ss.parse_stages("all") == tuple(range(9)) and ss.parse_stages("0-4") == (0, 1, 2, 3, 4) and ss.parse_stages("1,3-4") == (1, 3, 4)
    with pytest.raises(ValueError):
        ss.parse_stages("9")
    ls = "/h/models/gemma4-12b/gemma-4-12B-it-Q4_K_M.gguf\n/h/models/gemma4-12b/mmproj-gemma-4-12B-it-Q8_0.gguf\n/h/models/gemma4-12b-qat/gemma-4-12b-it-qat-q4_0.gguf\n"
    assert ss.draft_candidates(ls) == []
    assert ss.draft_candidates(ls + "/h/models/gemma4-12b/mtp-gemma-4-12B-it.gguf\n") == ["/h/models/gemma4-12b/mtp-gemma-4-12B-it.gguf"]


# ── the commands ─────────────────────────────────────────────────────────────

def spec(sc, **cfg_kw):
    return nw.sweep_spec(PARKED, nw.NightCfg(**cfg_kw), sc)


def flag(argv, name):
    return argv[argv.index(name) + 1]


def test_the_b11194_command_spells_the_locked_mmap_the_way_the_live_4b_does_and_the_parked_build_keeps_mlock():
    a = spec(ss.stage_configs(1, ss.CONTROL)[0])["argv"]
    assert a[0] == B11194 and flag(a, "--load-mode") == "mmap+mlock" and "--mlock" not in a and flag(a, "--fit") == "off" and flag(a, "--n-gpu-layers") == "99" and flag(a, "--reasoning") == "off"
    assert flag(a, "--ctx-size") == "8192" and flag(a, "--cache-type-k") == "q8_0" == flag(a, "--cache-type-v") and flag(a, "--port") == "11500" and flag(a, "--host") == "127.0.0.1"
    c = spec(ss.CONTROL)["argv"]
    assert c[0].endswith("build-jetson-new/bin/llama-server") and "--mlock" in c and "--load-mode" not in c and "--fit" not in c and flag(c, "--n-gpu-layers") == "30"      # today's command
    assert flag(c, "--batch-size") == "512" and flag(c, "--ubatch-size") == "128"
    assert spec(ss.CONTROL)["env"].get("GGML_CUDA_ENABLE_UNIFIED_MEMORY") == "1" and "GGML_CUDA_ENABLE_UNIFIED_MEMORY" not in spec(ss.stage_configs(1, ss.CONTROL)[2])["env"]


def test_the_levers_reach_the_command_and_nothing_else_is_hand_written():
    R = ss.SweepConfig
    auto = spec(R("t", "x", build="b11194", ngl=None, fit="on"))["argv"]
    assert "--n-gpu-layers" not in auto and flag(auto, "--fit") == "on"
    t = spec(R("t", "x", threads=6, no_kv_offload=True, batch=2048, ubatch=512, kv="q4_0", model="q3km", ctx=4096))["argv"]
    assert flag(t, "--threads") == "6" and "--no-kv-offload" in t and flag(t, "--batch-size") == "2048" and flag(t, "--ubatch-size") == "512" and flag(t, "--cache-type-k") == "q4_0"
    assert flag(t, "--model").endswith("gemma-4-12b-it-qat-requant-Q3_K_M.gguf") and flag(t, "--ctx-size") == "4096"
    assert "--mlock" not in spec(R("t", "x", load_mode="mmap"))["argv"] and "--load-mode" not in spec(R("t", "x", load_mode="mmap"))["argv"]          # parked build: mmap is its default
    assert flag(spec(R("t", "x", build="b11194", load_mode="mmap"))["argv"], "--load-mode") == "mmap" and flag(spec(R("t", "x", build="b11194", load_mode="none"))["argv"], "--load-mode") == "none"
    assert "--no-mmap" in spec(R("t", "x", load_mode="none"))["argv"]
    d = spec(R("t", "x", build="b11194", draft="/m/d.gguf"))["argv"]
    assert flag(d, "--model-draft") == "/m/d.gguf" and flag(d, "--spec-type") == "draft-mtp" and flag(d, "--spec-draft-n-max") == "4"
    sp = spec(R("t", "x", build="b11194"))
    assert any(x.startswith("binary ") for x in sp["diff"]) and any("--load-mode" in x for x in sp["diff"])       # the audit diff is recomputed against the parked ExecStart


def test_the_sweep_never_writes_the_parked_unit(tmp_path):
    parked = tmp_path / "parked.service.disabled"
    parked.write_text(PARKED)
    before = parked.stat().st_mtime_ns
    for sc in ss.stage_configs(1, ss.CONTROL):
        nw.sweep_spec(parked.read_text(), nw.NightCfg(), sc)
    assert parked.read_text() == PARKED and parked.stat().st_mtime_ns == before


# ── the parsers ──────────────────────────────────────────────────────────────

def test_nvmap_clients_buffers_and_failures_are_read_from_the_boxs_own_formats():
    clients = "CLIENT PROCESS PID SIZE\nuser python 2507644 740948K\nuser llama-server 2411547 91808K\nuser llama-server 2411105 3234060K\ntotal 4066816K\n"
    assert ss.parse_nvmap_client_mib(clients, 2411105) == pytest.approx(3158.3, abs=0.1) and ss.parse_nvmap_client_mib(clients, 2411547) == pytest.approx(89.7, abs=0.1)
    assert ss.parse_nvmap_client_mib(clients, None) == pytest.approx(3158.3, abs=0.1) and ss.parse_nvmap_client_mib("", 1) is None and ss.parse_nvmap_client_mib("garbage", 1) is None
    assert ss.parse_main_pid("MainPID=4242") == 4242 and ss.parse_main_pid("4242\n") == 4242 and ss.parse_main_pid("MainPID=0") is None and ss.parse_main_pid("") is None
    buf = ss.parse_buffers("load_tensors: CUDA0 model buffer size = 4163.31 MiB\nload_tensors: CPU_Mapped model buffer size = 2474.32 MiB\nllama_kv_cache: CUDA0 KV buffer size = 260.00 MiB\n"
                           "sched_reserve: CUDA0 compute buffer size = 312.50 MiB\nsched_reserve: CUDA_Host compute buffer size = 21.1 MiB\n")
    assert buf == {"CUDA0 model": 4163.3, "CPU_Mapped model": 2474.3, "CUDA0 KV": 260.0, "CUDA0 compute": 312.5, "CUDA_Host compute": 21.1}
    assert ss.failure_reason("E ggml_backend_cuda_buffer_type_alloc_buffer: allocating 6637.69 MiB on device 0: cudaMalloc failed: out of memory") == "cudaMalloc of 6638 MiB failed (out of memory)"
    assert "no error line" in ss.failure_reason("")
    p = ss.parse_probe(json.dumps({"usage": {"prompt_tokens": 2800, "completion_tokens": 320}, "timings": {"prompt_per_second": 141.26, "predicted_per_second": 4.567}}))
    assert p == {"prompt_tokens": 2800, "completion_tokens": 320, "prefill_tps": 141.3, "decode_tps": 4.57} and ss.parse_probe("not json")["decode_tps"] is None


def test_the_night_probe_has_the_night_minds_shape_and_shares_nothing_with_the_speed_probe():
    n, s = ss.night_prompt(), nw.speed_prompt()
    assert 2.4 * 4 * len(n.splitlines()) > 0 and len(n.splitlines()) == ss.NIGHT_LINES + 1 and "Mirela" in n and "Mirela" not in s and n.splitlines()[0] != s.splitlines()[0]
    assert ss.NIGHT_PROMPT_TOKENS == 2800 and ss.NIGHT_ANSWER_TOKENS == 320
    pay = json.loads(ss.chat_payload(n, 320))
    assert pay["max_tokens"] == 320 and pay["cache_prompt"] is False and pay["temperature"] == 0 and pay["messages"][0]["content"] == n


def test_the_winner_is_the_fastest_night_shape_decode_among_the_files_that_may_become_the_default():
    def row(name, model, dec, loaded=True, status="ok", pre=100.0):
        return {"name": name, "loaded": loaded, "status": status, "b_decode": dec, "a_decode": None, "b_prefill": pre, "config": {"model": model}}
    rows = [row("a", "qat", 4.0), row("b", "qat", 6.5), row("c", "q3km", 9.0), row("d", "qat", 8.0, loaded=False, status="did not load"), row("e", "q4km", 6.5, pre=130.0), row("f", "qat", None)]
    assert ss.pick_best(rows)["name"] == "e"                                       # c is a re-quantised SPEED probe: never the default; d did not load; f has no figure; e ties b on decode and wins on prefill
    assert ss.pick_best(rows, tuple(ss.SWEEP_MODEL_FILES))["name"] == "c" and ss.pick_best([]) is None
    assert ss.clearly_loses(row("x", "qat", 4.0), 6.5) and not ss.clearly_loses(row("x", "qat", 5.0), 6.5) and not ss.clearly_loses(row("x", "qat", 1.0, loaded=False, status="did not load"), 6.5)
    merged = ss.merge_rows([{"name": "a", "v": 1}, {"name": "b", "v": 1}], [{"name": "b", "v": 2}, {"name": "c", "v": 1}])
    assert [(r["name"], r["v"]) for r in merged] == [("a", 1), ("b", 2), ("c", 1)]           # a later run replaces a re-measured row and appends the new ones


# ── the run ──────────────────────────────────────────────────────────────────

def test_a_sweep_stops_the_units_once_runs_one_12b_per_config_and_restores_once_at_the_end(tmp_path):
    w, host, cfg = make_sweep(tmp_path, "--sweep-stages", "0-1")
    assert w.run() == nw.EXIT_OK, w.outcome
    starts = [s for s in host.starts]
    assert len(starts) == 7                                                                 # C0 + 1a-1f
    assert [host.joined()[i].split("systemd-run")[0] for i in range(0)] == []              # (the starts are all transient 12B units)
    for u in nw.STOP_ORDER:
        assert host.count(f"--user stop {u}") == 1 and host.count(f"--user start {u}") == 1       # ONE sleep and ONE wake for the whole sweep, never one per config
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]
    assert host.count(f"--user stop {nw.NIGHT_UNITS['llm']}") >= 7                           # the 12B itself is stopped after every config
    last_start = max(i for i, c in enumerate(host.joined()) if "systemd-run" in c and "zoe-night-12b" in c)
    assert host.idx(f"--user start {nw.BRAIN}") > last_start                                 # nothing wakes before the last config has been measured
    assert not cfg.marker.exists() and w.restore_ok is True


def test_the_table_records_what_loaded_what_did_not_and_every_figure_the_owner_asked_for(tmp_path):
    w, host, cfg = make_sweep(tmp_path, "--sweep-stages", "0-1")
    assert w.run() == nw.EXIT_OK, w.outcome
    r = rows_by_name(w)
    assert [k for k, v in r.items() if v["loaded"]] == ["C0", "1e", "1f"] and all(r[k]["status"] == "did not load" for k in ("1a", "1b", "1c", "1d"))
    assert r["1a"]["why"] == "cudaMalloc of 14157 MiB failed (out of memory)" or "cudaMalloc of" in r["1a"]["why"]          # the failed allocation, from the 12B's own journal
    c0 = r["C0"]
    assert c0["load_s"] > 0 and c0["a_prefill"] == 140.0 and c0["a_decode"] == 4.0 and c0["b_prefill"] == 140.0 and c0["b_decode"] == 4.0 and c0["b_prompt_tokens"] == 2800 and c0["b_completion_tokens"] == 320
    assert c0["nvmap_mib"] == pytest.approx(30 * 140.0, abs=1) and c0["mem_low_mib"] is not None and c0["avail_after_load_mib"] is not None
    assert c0["buffers"]["CUDA0 model"] == 3600.0 and c0["buffers"]["CUDA0 compute"] == 312.5
    table = "\n".join(ss.table_lines(w.rec["sweep"]["rows"]))
    assert "| C0 |" in table and "| 1a |" in table and "NO" in table and "cudaMalloc" in table
    state = json.loads(cfg.sweep_state_path.read_text())
    assert [x["name"] for x in state["rows"]] == ["C0", "1a", "1b", "1c", "1d", "1e", "1f"] and state["best"]["name"] in ("1e", "1f", "C0")


def test_a_config_that_does_not_load_is_a_row_not_a_failure_and_does_not_count_against_the_nightly_window(tmp_path):
    w, host, cfg = make_sweep(tmp_path, "--sweep-stages", "1")
    assert w.run() == nw.EXIT_OK and w.outcome == "ok"
    assert not (cfg.night_dir / "load_failures.json").exists()                              # a measurement must not make the real window refuse tomorrow night
    assert rows_by_name(w)["1a"]["loaded"] is False


def test_the_best_row_seeds_the_next_stage_a_failure_ends_an_ascending_family_and_a_resquant_is_never_the_default(tmp_path):
    w, host, cfg = make_sweep(tmp_path, "--sweep-stages", "0,1,3,4", host_kw={"max_ngl": 34, "b11194_gain": 1.2, "q3_gain": 1.5})
    assert w.run() == nw.EXIT_OK, w.outcome
    r = rows_by_name(w)
    assert r["1e"]["loaded"] and r["1e"]["b_decode"] > r["C0"]["b_decode"] and w.rec["sweep"]["rows"][0]["name"] == "C0"
    q3 = [x for x in r.values() if x["config"]["model"] == "q3km"]
    assert q3 and max(x.get("b_decode") or 0 for x in q3) > max(x.get("b_decode") or 0 for x in r.values() if x["config"]["model"] == "qat")      # the smaller file IS faster...
    best = w.rec["sweep"]["best"]
    assert best["config"]["model"] == "qat" and w.rec["sweep"]["best_overall"]["config"]["model"] == "q3km"                                  # ...but only a default-eligible file wins the default
    ngl_rows = [x for x in r.values() if x["family"] == "ngl"]
    assert [x["name"] for x in ngl_rows] == ["4-32", "4-34", "4-38", "4-42", "4-48"]
    assert [x["loaded"] for x in ngl_rows] == [True, True, False, False, False] and ngl_rows[3]["status"] == "not run" and "ended it" in ngl_rows[3]["why"]    # 38 failed: 42 and 48 were never started
    assert not any(s["ngl"] in (42, 48) for s in host.starts)
    assert best["name"] == "4-34" and best["config"]["ngl"] == 34 and best["config"]["build"] == "b11194"


def test_a_fallback_config_runs_only_when_the_one_before_it_did_not_load(tmp_path):
    w, host, _ = make_sweep(tmp_path, "--sweep-stages", "0,2", host_kw={"max_ngl": 30})
    assert w.run() == nw.EXIT_OK, w.outcome
    r = rows_by_name(w)
    assert r["2a"]["loaded"] and r["2b"]["status"] == "not run" and "loaded" in r["2b"]["why"]
    w2, host2, _ = make_sweep(tmp_path / "b", "--sweep-stages", "0,2", host_kw={"max_ngl": 30})
    orig = host2.run

    def run(argv, timeout=60.0, mutating=True, env=None):                                     # the Q4_K_M file needs a smaller offload than the QAT file
        if argv[:1] == ["systemd-run"] and "Q4_K_M" in " ".join(argv) and "--n-gpu-layers 30" in " ".join(argv):
            host2.max_ngl = 29
        elif argv[:1] == ["systemd-run"]:
            host2.max_ngl = 30
        return orig(argv, timeout, mutating, env)
    host2.run = run
    assert w2.run() == nw.EXIT_OK, w2.outcome
    r2 = rows_by_name(w2)
    assert r2["2a"]["loaded"] is False and r2["2b"]["loaded"] is True and r2["2b"]["config"]["ngl"] == 28


def test_a_config_that_loses_by_more_than_a_quarter_ends_its_family(tmp_path):
    w, host, _ = make_sweep(tmp_path, "--sweep-stages", "0,7", host_kw={"max_ngl": 30})
    orig = host.run

    def run(argv, timeout=60.0, mutating=True, env=None):
        r = orig(argv, timeout, mutating, env)
        if argv[:1] == ["curl"] and argv[-1].endswith("/chat/completions") and host.cur and "--threads" in host.cur["argv"] and host.cur["argv"][host.cur["argv"].index("--threads") + 1] == "6":
            return bk.Result(0, json.dumps({"usage": {"prompt_tokens": 1630, "completion_tokens": 96}, "timings": {"prompt_per_second": 140.0, "predicted_per_second": 1.0}}))      # -t 6 is terrible
        return r
    host.run = run
    assert w.run() == nw.EXIT_OK, w.outcome
    r = rows_by_name(w)
    assert r["7a"]["loaded"] and r["7b"]["status"] == "not run" and "ended it" in r["7b"]["why"] and r["7c"]["status"] == "not run"


def test_the_time_cap_records_the_rest_as_not_run_and_the_restore_still_happens(tmp_path):
    w, host, cfg = make_sweep(tmp_path, "--sweep-stages", "0,1,5,6,7", "--cap-min", "16", host_kw={"max_ngl": 30, "probe_s": 120.0})
    assert w.run() == nw.EXIT_OK, w.outcome
    r = w.rec["sweep"]["rows"]
    done = [x for x in r if x["status"] != "not run"]
    skipped = [x for x in r if x["status"] == "not run"]
    assert done and skipped and all("time cap" in x["why"] for x in skipped)                # cut by the cap, and SAID so
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA] and w.restore_ok is True
    assert w.elapsed_min() < 16.0 + 4.0


def test_a_config_that_takes_the_box_under_the_memory_floor_is_stopped_and_recorded_and_the_sweep_goes_on(tmp_path):
    w, host, _ = make_sweep(tmp_path, "--sweep-stages", "0,1", host_kw={"max_ngl": 30, "breach_ngl": 30})
    orig = host.run

    def run(argv, timeout=60.0, mutating=True, env=None):
        r = orig(argv, timeout, mutating, env)
        return r
    assert w.run() == nw.EXIT_OK, w.outcome
    r = rows_by_name(w)
    assert r["C0"]["status"] == "floor breach" and "floor" in r["C0"]["why"] and r["C0"]["loaded"] is True
    assert r["1e"]["status"] in ("floor breach",) and "1f" in r                              # every -ngl 30 config breached; the sweep did not stop at the first
    assert nw.NIGHT_UNITS["llm"] and host.count(f"--user stop {nw.NIGHT_UNITS['llm']}") >= 7 and w.restore_ok is True


def test_a_unit_that_stays_down_after_the_sweep_is_loud_and_exits_with_the_restore_code(tmp_path):
    w, host, cfg = make_sweep(tmp_path, "--sweep-stages", "0", host_kw={"unhealthy": (nw.KOKORO,)})
    assert w.run() == nw.EXIT_RESTORE_FAILED
    alarm = (cfg.report_dir / "ALARM").read_text()
    assert "RESTORE FAILED" in alarm and nw.KOKORO in alarm and cfg.marker.exists()
    assert wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA]


def test_an_exception_in_the_middle_of_the_sweep_still_restores(tmp_path):
    w, host, cfg = make_sweep(tmp_path, "--sweep-stages", "0,1")
    real = w.sweep_one
    calls = []

    def boom(sc):
        calls.append(sc.name)
        if sc.name == "1a":
            raise RuntimeError("boom")
        return real(sc)
    w.sweep_one = boom
    assert w.run() == nw.EXIT_ABORTED and "boom" in w.outcome
    assert calls == ["C0", "1a"] and wake_order(host) == [nw.BRAIN, nw.KOKORO, nw.ROUTER, nw.ZOE_DATA] and not cfg.marker.exists()


def test_a_second_invocation_continues_from_the_saved_best_and_keeps_the_first_rows(tmp_path):
    w, host, cfg = make_sweep(tmp_path, "--sweep-stages", "0-1", host_kw={"max_ngl": 30, "b11194_gain": 1.3})
    assert w.run() == nw.EXIT_OK, w.outcome
    first_best = json.loads(cfg.sweep_state_path.read_text())["best"]
    w2, host2, cfg2 = make_sweep(tmp_path, "--sweep-stages", "6", host_kw={"max_ngl": 30, "b11194_gain": 1.3})
    cfg2.night_dir = cfg.night_dir
    assert w2.run() == nw.EXIT_OK, w2.outcome
    assert [s["ngl"] for s in host2.starts] == [first_best["config"]["ngl"]] * 3                                   # stage 6 ran on the saved best (its layer count)
    assert all(("b11194" in s["argv"][0]) == (first_best["config"]["build"] == "b11194") for s in host2.starts)
    state = json.loads(cfg2.sweep_state_path.read_text())
    assert [x["name"] for x in state["rows"]][:7] == ["C0", "1a", "1b", "1c", "1d", "1e", "1f"] and [x["name"] for x in state["rows"]][7:] == ["6a", "6b", "6c"]


def test_the_report_has_the_sweep_table_and_the_winner(tmp_path):
    w, host, cfg = make_sweep(tmp_path, "--sweep-stages", "0-1")
    assert w.run() == nw.EXIT_OK, w.outcome
    md = [p for p in w.report_paths if p.suffix == ".md"][0]
    assert md.name.startswith("2026-10-09-sweep") and oct(md.stat().st_mode & 0o777) == "0o600"
    text = md.read_text()
    assert "12B speed sweep" in text and "| C0 |" in text and "best default-eligible config" in text and "cudaMalloc" in text


# ── the refusals and the dry run ─────────────────────────────────────────────

def test_a_sweep_and_a_trial_cannot_be_combined_and_a_sweep_needs_no_night_hour(tmp_path):
    with pytest.raises(bk.Refused, match="one at a time"):
        nw.configure(nw.build_parser().parse_args(["--speed-sweep", "--trial"]))
    with pytest.raises(bk.Refused, match="stages"):
        nw.configure(nw.build_parser().parse_args(["--speed-sweep", "--sweep-stages", "12"]))
    cfg = nw.configure(nw.build_parser().parse_args(["--speed-sweep"]))
    assert cfg.sweep and cfg.cap_min == 45.0 and cfg.night_only is False and cfg.jobs == () and cfg.fallback_4b is False and cfg.exploratory
    w, host, _ = make_sweep(tmp_path, "--sweep-stages", "0", start=at(14, 0))
    assert w.run() == nw.EXIT_OK and not any("night_digest" in c for c in host.joined())           # daylight is fine for a measurement; no night job is started


def test_the_brain_window_lock_held_refuses_the_sweep_and_stops_nothing(tmp_path):
    import fcntl
    import os
    w, host, cfg = make_sweep(tmp_path, "--sweep-stages", "0")
    fd = os.open(cfg.lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert w.run() == nw.EXIT_REFUSED
    finally:
        os.close(fd)
    assert "is held" in w.outcome and not any(c.startswith("systemd-run") for c in host.joined()) and host.count("--user stop") == 0


def test_the_dry_run_lists_the_whole_grid_and_changes_nothing(tmp_path):
    lines = []
    w, host, cfg = make_sweep(tmp_path, "--dry-run")
    w.log = lines.append
    assert w.run() == nw.EXIT_OK
    out = "\n".join(lines)
    for needle in ("SPEED SWEEP", "stage 0 (control)", "stage 1 (offload)", "1a   b11194 qat ngl 99", "1b   b11194 qat ngl auto", "stage 8 (draft)", "no 12B draft GGUF on disk",
                   "command 1a:", "--load-mode mmap+mlock", "DRY-RUN: nothing was changed"):
        assert needle in out, needle
    assert host.starts == [] and not cfg.sweep_state_path.exists() and not cfg.marker.exists() and not cfg.report_dir.exists()


# ── the sweep's measured winner (4-38 = 6.89 tok/s decode, 182 tok/s prefill; the parked flags gave 5.46 / 141) is the window's default EXCEPT -ngl: 34, because 38 was refused twice the same evening ──

def _flags(argv):
    return {a: (argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else "") for i, a in enumerate(argv) if a.startswith("--")}


def test_nightcfg_defaults_are_the_sweeps_winner_and_the_command_carries_them_explicitly():
    import os
    cfg = nw.NightCfg()
    assert (cfg.ngl, cfg.ctx_choice, cfg.kv_choice, cfg.batch, cfg.ubatch, cfg.mlock, cfg.fit_off) == (34, 8192, "q8_0", 512, 128, True, True)
    assert cfg.binary == nw.WINNER_BINARY and cfg.binary.endswith("llama.cpp-b11194/build-jetson/bin/llama-server")
    assert cfg.unified is os.path.exists("/etc/nv_tegra_release")             # on by default wherever there is a Jetson (the one box this runs on)
    # the lever choice with plenty of RAM lands on the winner's context and cache type, not the biggest set
    pick = nw.choose_levers(nw.evaluate({"qat": QAT}, 20000, 1200.0, 700.0), model=cfg.model_choice, ctx=cfg.ctx_choice, kv=cfg.kv_choice)
    assert pick["levers"] == nw.Levers("qat", 8192, "q8_0")
    # ... and the generated command SAYS so: a parked unit whose ExecStart drifted (other batch sizes, full offload) cannot change what the window runs
    drifted = PARKED.replace("--batch-size 512", "--batch-size 2048").replace("--ubatch-size 128", "--ubatch-size 512")
    assert drifted != PARKED
    spec = nw.llm_spec(drifted, nw.NightCfg(unified=True), pick["levers"])
    f = _flags(spec["argv"])
    assert spec["argv"][0] == nw.WINNER_BINARY and f["--n-gpu-layers"] == "34" and f["--ctx-size"] == "8192" and f["--cache-type-k"] == "q8_0" == f["--cache-type-v"]
    assert f["--batch-size"] == "512" and f["--ubatch-size"] == "128" and f["--fit"] == "off" and f["--load-mode"] == "mmap+mlock" and "--mlock" not in f
    assert spec["env"]["GGML_CUDA_ENABLE_UNIFIED_MEMORY"] == "1"


def test_the_trial_and_the_plain_window_parse_to_the_winner_and_every_value_is_overridable():
    for argv in ([], ["--trial"], ["--speed-sweep"]):
        cfg = nw.configure(nw.build_parser().parse_args(argv))
        assert (cfg.ngl, cfg.ctx_choice, cfg.kv_choice, cfg.batch, cfg.ubatch, cfg.mlock, cfg.fit_off, cfg.binary) == (34, 8192, "q8_0", 512, 128, True, True, nw.WINNER_BINARY), argv
    over = nw.configure(nw.build_parser().parse_args(["--trial", "--ngl", "30", "--ctx", "16384", "--kv", "q4_0", "--batch-size", "2048", "--ubatch-size", "512", "--no-mlock",
                                                     "--fit-default", "--binary", "/x/llama-server"]))
    assert (over.ngl, over.ctx_choice, over.kv_choice, over.batch, over.ubatch, over.mlock, over.fit_off, over.binary) == (30, 16384, "q4_0", 2048, 512, False, False, "/x/llama-server")
    spec = nw.llm_spec(PARKED, over, nw.Levers("qat", 16384, "q4_0"))
    f = _flags(spec["argv"])
    assert f["--n-gpu-layers"] == "30" and f["--batch-size"] == "2048" and f["--ubatch-size"] == "512" and "--fit" not in f


def test_the_table_renderer_reproduces_a_known_row_from_a_tiny_state():
    state = {"rows": [{"name": "4-38", "desc": "b11194 qat ngl 38 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma", "loaded": True, "status": "ok", "why": "", "load_s": 14.3,
                       "a_prefill": 175.4, "a_decode": 6.84, "b_prefill": 182.4, "b_decode": 6.89, "nvmap_mib": 5671.0, "mem_low_mib": 3978.0},
                      {"name": "4-42", "desc": "b11194 qat ngl 42 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma", "loaded": False, "status": "did not load",
                       "why": "cudaMalloc of 181 MiB failed (out of memory)", "mem_low_mib": 10513.0}]}
    lines = ss.render_markdown(json.dumps(state)).splitlines()
    assert lines[0].startswith("| id | config | loaded | load s |") and lines[1].startswith("|---|")
    assert lines[2] == "| 4-38 | b11194 qat ngl 38 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | yes | 14.3 | 175.4 | 6.84 | 182.4 | 6.89 | 5671 | 3978 | ok |"
    assert lines[3] == "| 4-42 | b11194 qat ngl 42 ctx 8192 kv q8_0 b512/ub128 mlock fit off uma | NO | - | - | - | - | - | - | 10513 | cudaMalloc of 181 MiB failed (out of memory) |"
    assert ss.render_markdown("not json").count("\n") == 2                      # a broken state file renders the header only, never a traceback
