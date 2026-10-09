#!/usr/bin/env python3
"""The 12B NIGHT WINDOW: Zoe sleeps for about an hour, a bigger model does the night's thinking, everything is put back.

    night_window.sh --dry-run     the arithmetic for every lever combination, which fits TODAY, the generated 12B command, the plan. Changes nothing.
    night_window.sh               the window (timer 02:50, hard cap 65 min, ends before 03:55)
    night_window.sh --trial       load the 12B only, score the reflection cells (K1-K5) on it and on the 4B at 32k, restore. Manual, about 25 min.
    night_window.sh --restore-only   put everything back (idempotent; reads the marker file the window leaves while it is open)

What a window does (and ALWAYS undoes, on every exit path):

    preflight   refuse: the brain-window lock is held (a landing, the samantha bar, the bake-off), outside the night hours, a nightly timer job is
                running (training / backup / memory export / dreaming), the panel had a voice turn in the last 10 min, the window would overlap the
                04:10-04:52 voice gate, fragmented RAM without passwordless sudo, or even the optimistic RAM prediction cannot fit the smallest 12B
    sleep       stop zoe-data, the router, Kokoro, then the 4B brain (the marker file lists each BEFORE it is stopped); wait for the processes to be
                gone; compact physical memory (sudo -n) and log /proc/buddyinfo
    choose      MEASURE MemAvailable now; pick the best lever set that fits by arithmetic (QAT q4_0 file > Q4_K_M, ctx 32768 > 16384 > 8192, KV q8_0 > q4_0);
                nothing fits -> REFUSE, put everything back (one to two minutes of silence), exit 2
    load        the 12B from the PARKED deep-brain unit's ExecStart text (read, never edited), levers applied, port 11500, health polled, MemAvailable
                >= 1,200 MB required once loaded, one speed probe (prefill / decode tok/s)
    jobs        nightly digest (the body of zoe-data's 03:00 loop), zoe-nightly-dreaming, the night-mind pass: each with a timeout, wall time, model
                tokens (llama-server /metrics) and the MemAvailable low; a job that fails here runs again on the live 4B after the restore
    wake        stop the 12B, compact, start the 4B, Kokoro, the router, zoe-data IN THAT ORDER, polling each one's /health (a retry, then LOUD:
                an ALARM file, exit 4); then the weekly index-compaction trigger (it needs zoe-data) and the 4B fallbacks
    report      ~/.zoe/night-reports/<date>.md and .json

Every external action goes through ``Host`` (the bake-off's seam): the tests replace it with a double and run the REAL step logic.
docs/knowledge/night-window.md is the operator's page.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import bakeoff as bk  # noqa: E402 - the bake-off's Host seam, lock, compaction, buddyinfo, clone generators

Refused, Aborted, Result = bk.Refused, bk.Aborted, bk.Result
EXIT_OK, EXIT_REFUSED, EXIT_ABORTED, EXIT_RESTORE_FAILED = bk.EXIT_OK, bk.EXIT_REFUSED, bk.EXIT_ABORTED, bk.EXIT_RESTORE_FAILED
EXIT_JOBS_FAILED = 5          # the box is back and healthy, but a night job did not finish anywhere (its 4B fallback included)

HOME = os.environ.get("HOME", "/home/zoe")
MIB = 1024 * 1024

# ── the units, in the order they sleep and wake ──────────────────────────────

BRAIN, KOKORO, ROUTER, ZOE_DATA = "llama-server.service", "kokoro-tts.service", "functiongemma-router.service", "zoe-data.service"
#: stop order: the consumers first, the brain last. RESTORE_ORDER is the owner's order: brain first, then Kokoro, router, zoe-data.
STOP_ORDER = (ZOE_DATA, ROUTER, KOKORO, BRAIN)
RESTORE_ORDER = (BRAIN, KOKORO, ROUTER, ZOE_DATA)
#: transient units this tool starts (systemd-run --user); the 12B and the 4B-at-32k both take port 11500, never together
NIGHT_UNITS = {"llm": "zoe-night-12b.service", "clone4": "zoe-night-4b32k.service", "shim": "zoe-night-embed.service"}


def _json_objects(text: str):
    """Every JSON object in ``text``, whether it sits on one line or is indented across many (a log mixes stderr lines with the CLI's stdout object)."""
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            v, end = dec.raw_decode(text, i)
        except ValueError:
            i = text.find("{", i + 1)
            continue
        if isinstance(v, dict):
            yield v
        i = text.find("{", end)


def _json(text: str) -> dict:
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


@dataclasses.dataclass(frozen=True)
class UnitSpec:
    """How to know a unit is REALLY back: its /health answer, never ``systemctl is-active`` (it lies while a model is still loading)."""
    unit: str
    url: str
    ok: Callable[[str], bool]
    wait_s: float
    what: str


def _status_ok(text: str) -> bool:
    return _json(text).get("status") == "ok"


def _kokoro_ok(text: str) -> bool:
    d = _json(text)
    return d.get("pipeline_loaded") is True and d.get("device") == "cuda"     # a CPU Kokoro "works" and chops every reply (voice-pipeline.md)


HEALTH = {
    BRAIN: UnitSpec(BRAIN, "http://127.0.0.1:11434/health", _status_ok, 240.0, "the 4B brain"),
    KOKORO: UnitSpec(KOKORO, "http://127.0.0.1:10201/health", _kokoro_ok, 240.0, "Kokoro (pipeline_loaded, device cuda)"),
    ROUTER: UnitSpec(ROUTER, "http://127.0.0.1:11436/health", _status_ok, 120.0, "the FunctionGemma router"),
    ZOE_DATA: UnitSpec(ZOE_DATA, "http://127.0.0.1:8000/health", _status_ok, 240.0, "zoe-data"),
}


# ── the arithmetic (pure) ────────────────────────────────────────────────────

#: the 12B's KV cache (reduced-SWA): bytes per element by cache type (llama.cpp block formats: q8_0 34 B / 32, q4_0 18 B / 32), elements per token
#: of the global layers and the constant sliding-window part (measured from the GGUF header by the bake-off coordinator, 2026-10-07)
KV_BYTES_PER_ELEM = {"f16": 2.0, "q8_0": 1.0625, "q4_0": 0.5625}
KV_GLOBAL_ELEMS_PER_TOKEN, KV_SWA_ELEMS = 8 * 1024, 189_000_000
COMPUTE_MIB = 600.0                      # compute buffers (the bake-off's figure; the first window measures the real one)
#: the share of a stopped unit's PSS that the dry run counts as freed. 0.8, not 1.0: run 2 measured less headroom after the 4B + Kokoro stopped than their RSS suggests.
#: ONLY the dry run and the refuse-before-stopping check use a prediction; the choice of lever set is made on the MEASURED MemAvailable after the stops.
FREED_FACTOR = 0.8
MODEL_FILES = {"qat": "models/gemma4-12b-qat/gemma-4-12b-it-qat-q4_0.gguf", "q4km": "models/gemma4-12b/gemma-4-12B-it-Q4_K_M.gguf"}
CTX_OPTIONS = (32768, 16384, 8192)
KV_OPTIONS = ("q8_0", "q4_0")
#: preference: the QAT file first (it is TRAINED for q4_0 and is the smaller file; Q4_K_M is only offered by --model or when it is the only file on disk), then KV q8_0
#: (a q4_0 cache costs long-context accuracy; the jobs' prompts are 3-8k tokens, so 16k with q8_0 beats 32k with q4_0), then the larger context


@dataclasses.dataclass(frozen=True)
class Levers:
    model: str          # "qat" | "q4km"
    ctx: int
    kv: str             # "q8_0" | "q4_0"

    def label(self) -> str:
        return f"{self.model} ctx {self.ctx} KV {self.kv}"


def kv_mib(ctx: int, kv: str) -> float:
    return (KV_GLOBAL_ELEMS_PER_TOKEN * ctx + KV_SWA_ELEMS) * KV_BYTES_PER_ELEM[kv] / MIB


def lever_order(models: "tuple[str, ...]" = ("qat", "q4km")) -> "list[Levers]":
    return [Levers(m, c, k) for m in models for k in KV_OPTIONS for c in CTX_OPTIONS]


def need_mib(model_bytes: int, lv: Levers, floor_mib: float, job_reserve_mib: float = 0.0) -> "dict[str, float]":
    """MemAvailable (MiB) the box must show BEFORE the 12B starts: the model file (mlocked) + KV + compute + the floor that must remain once loaded,
    and (``need_jobs``) the resident size of the night job processes that run beside it."""
    model, kv = model_bytes / MIB, kv_mib(lv.ctx, lv.kv)
    load = model + kv + COMPUTE_MIB + floor_mib
    return {"model": model, "kv": kv, "compute": COMPUTE_MIB, "floor": floor_mib, "need_load": load, "job_reserve": job_reserve_mib,
            "need_jobs": load + job_reserve_mib, "need_cuda": model + kv + COMPUTE_MIB}


def evaluate(sizes: "dict[str, int]", avail_mib: float, floor_mib: float, job_reserve_mib: float, margin_mib: float = 0.0,
             models: "tuple[str, ...]" = ("qat", "q4km"), free_mib: "Optional[float]" = None) -> "list[dict[str, Any]]":
    """Every lever combination against ``avail_mib`` (MemAvailable): the table the dry run prints and the choice reads. ``free_mib`` (MemFree, measured after the stops
    and the compaction) is the OTHER half of the test: a Jetson's CUDA allocator takes the model, KV and compute buffers from FREE pages (June 2026: the 12B failed to
    load with 9.2 GB free and loaded at 10.7 GB free), not from page cache that MemAvailable counts; ``None`` = not measurable (the dry run's prediction)."""
    rows = []
    for lv in lever_order(models):
        if lv.model not in sizes:
            continue
        n = need_mib(sizes[lv.model], lv, floor_mib, job_reserve_mib)
        free_ok = True if free_mib is None else free_mib - n["need_cuda"] >= margin_mib
        rows.append({"levers": lv, **n, "avail": avail_mib, "free": free_mib, "margin_load": avail_mib - n["need_load"], "margin_jobs": avail_mib - n["need_jobs"],
                     "margin_free": None if free_mib is None else free_mib - n["need_cuda"],
                     "fits": avail_mib - n["need_jobs"] >= margin_mib and free_ok, "fits_load_only": avail_mib - n["need_load"] >= margin_mib and free_ok})
    return rows


def choose_levers(rows: "list[dict[str, Any]]", pin: "Optional[Levers]" = None, model: str = "auto", ctx: "Optional[int]" = None,
                  kv: "Optional[str]" = None) -> "Optional[dict[str, Any]]":
    """The first row (in preference order) that fits WITH the jobs beside it. ``model auto`` = the QAT file, falling to Q4_K_M only when the QAT file is
    missing (a bigger file never helps a box that is short of RAM). Pins (--model/--ctx/--kv) filter; a pinned set that does not fit is not substituted."""
    for r in rows:
        lv: Levers = r["levers"]
        if model == "auto" and lv.model != ("qat" if any(x["levers"].model == "qat" for x in rows) else "q4km"):
            continue
        if model in ("qat", "q4km") and lv.model != model:
            continue
        if (ctx and lv.ctx != ctx) or (kv and lv.kv != kv):
            continue
        if r["fits"]:
            return r
    return None


def high_order_mib(frag: "dict[int, int]", min_order: int = 9, page_kb: int = 4) -> float:
    """MiB held in FREE blocks of order >= ``min_order`` (order 9 = 2 MB) from /proc/buddyinfo. The 12B's one 6.6 GB CUDA buffer is built from big blocks on a Tegra: on 2026-10-09
    it failed three times with 12.7 GB of MemFree but only 6.1 GB in blocks of 2 MB and up (after the stops and one compaction)."""
    return sum(n * (page_kb << o) for o, n in frag.items() if o >= min_order) / 1024.0


NVMAP_FREE = "/sys/kernel/debug/nvmap/iovmm/free_size"


def parse_nvmap_free_mib(text: str) -> "Optional[float]":
    """``Max allocatable IOVMM memory: N bytes`` (NvMap's own answer to 'how big a CUDA buffer can I still get'), in MiB; None when unreadable."""
    m = re.search(r"Max allocatable IOVMM memory:\s*(\d+)\s*bytes", text or "")
    return int(m.group(1)) / MIB if m else None


def parse_pss_kb(text: str) -> float:
    m = re.search(r"^Pss:\s+(\d+)\s+kB", text or "", re.M)
    return float(m.group(1)) if m else 0.0


def predict_avail(avail_now: float, pss_mib: "dict[str, float]", stop: "tuple[str, ...]", factor: float = FREED_FACTOR) -> float:
    return avail_now + factor * sum(pss_mib.get(u, 0.0) for u in stop)


def parse_metrics(text: str) -> "dict[str, float]":
    """llama-server ``/metrics`` (Prometheus text): the four counters the job accounting reads."""
    out: "dict[str, float]" = {}
    for key in ("prompt_tokens_total", "tokens_predicted_total", "prompt_seconds_total", "tokens_predicted_seconds_total"):
        m = re.search(rf"^llamacpp:{key}\s+([0-9.eE+-]+)", text or "", re.M)
        if m:
            out[key] = float(m.group(1))
    return out


def digest_timeout_scale(decode_tps: "Optional[float]") -> int:
    """memory_digest's model-call timeouts assume about 11.4 tok/s (45 s for a 512-token extraction). Scale them for the measured decode speed with 1.5x slack."""
    if not decode_tps or decode_tps <= 0:
        return 8
    return int(min(20, max(1, math.ceil(1.5 * 11.4 / decode_tps))))


class LoadFailed(Aborted):
    """The 12B never became healthy (cudaMalloc / NvMap, or the process died): the window ends, restores, and counts it (``load_failures.json``)."""


# ── configuration ────────────────────────────────────────────────────────────

def _hhmm(text: str) -> int:
    h, _, m = text.partition(":")
    return int(h) * 60 + int(m or 0)


@dataclasses.dataclass
class NightCfg(bk.Cfg):
    """The bake-off's Cfg (lock path, floor, compaction, panel host, meminfo / buddyinfo paths, ports) with the night window's settings on top."""
    cap_min: float = float(os.environ.get("NIGHT_CAP_MIN", "65"))
    reserve_min: float = 12.0                      # kept back for the unload, the four restarts and the report
    min_avail_mb: float = float(os.environ.get("NIGHT_MIN_AVAIL_MB", "1200"))
    quiet_wait_max_min: float = float(os.environ.get("NIGHT_QUIET_WAIT_MIN", "30"))
    #: the voice gate (04:15 Serena restart, 04:18-04:52 nightly gate) is the only blackout: the 01:45-03:15 passes the BAKE-OFF avoids are exactly what this window is
    blackouts: tuple = ((250, 292),)
    home: str = HOME
    night_dir: Path = Path(os.environ.get("NIGHT_DIR", f"{HOME}/.zoe/night-window"))
    report_dir: Path = Path(os.environ.get("NIGHT_REPORT_DIR", f"{HOME}/.zoe/night-reports"))
    port: int = int(os.environ.get("NIGHT_PORT", "11500"))
    live_unit_text: str = ""
    end_by: int = _hhmm(os.environ.get("NIGHT_END_BY", "03:55"))            # zoe-data must be back before the Sunday 04:00 consolidation loop
    night_only: bool = True                                                  # a manual run in daylight needs --anytime
    night_hours: "tuple[int, int]" = (90, 220)                               # a window may START between 01:30 and 03:40
    stop_zoe_data: bool = True
    model_choice: str = "auto"
    ctx_choice: "Optional[int]" = None
    kv_choice: "Optional[str]" = None
    margin_mib: float = float(os.environ.get("NIGHT_MARGIN_MIB", "0"))
    job_reserve_mib: float = float(os.environ.get("NIGHT_JOB_RESERVE_MIB", "700"))
    cache_ram_mib: int = int(os.environ.get("NIGHT_CACHE_RAM_MIB", "512"))
    binary: "Optional[str]" = os.environ.get("NIGHT_LLAMA_BINARY") or None
    #: ``--fit off`` is NOT added by default: the parked unit does not have it, and the first real load (2026-10-09 01:57, with it) died on cudaMalloc of the 6.6 GB model buffer
    #: with 12.7 GB free while the June 2026 bring-up that worked ran with the default (fit ON). ``--fit-off`` / ``NIGHT_FIT_OFF=1`` re-adds it (tested as a variable).
    fit_off: bool = os.environ.get("NIGHT_FIT_OFF") == "1"
    #: the parked unit passes ``--mlock``: the model file is mapped, locked in RAM AND copied into the CUDA buffer, so loading needs about twice the file size at once. ``--no-mlock`` drops it.
    mlock: bool = os.environ.get("NIGHT_NO_MLOCK") != "1"
    #: ``--n-gpu-layers`` override (None = the parked unit's 99): a partial offload keeps part of the weights in ordinary RAM, so the single CUDA allocation that failed on 2026-10-09
    #: (6,637 MiB; the 4B's 4.2 GB one succeeds) shrinks. Untested: the first thing to try, see docs/knowledge/night-window.md.
    ngl: "Optional[int]" = None
    #: ``GGML_CUDA_ENABLE_UNIFIED_MEMORY=1`` in the 12B's environment: ggml then allocates with cudaMallocManaged, which is not bound by the per-allocation limit that refused the
    #: 12B's single 6,637 MiB weight buffer on 2026-10-09 (the 4B's ~4.4 GB buffer loads) and pages on demand; some speed cost, no quality cost. Default ON on a Jetson
    #: (/etc/nv_tegra_release exists); ``--no-unified`` / ``NIGHT_NO_UNIFIED=1`` turns it off. Only the 12B gets it: the 4B at 32k stays exactly the live brain's configuration.
    unified: bool = os.path.exists("/etc/nv_tegra_release") and os.environ.get("NIGHT_NO_UNIFIED") != "1"
    #: after this many CONSECUTIVE windows (same boot) on which the 12B failed to load, a timer-started window refuses before stopping anything: Zoe is not put to sleep every night for a load that fails
    max_load_failures: int = 2
    retry_load: bool = False
    #: trial only: skip the 4B@32k phase (an exploratory attempt that is expected to fail fast should not also cost two minutes of silence for a baseline already measured)
    skip_4b: bool = False
    busy_units: tuple = ("zoe-training.service", "zoe-backup.service", "zoe-backup-verify.service", "zoe-memory-export.service", "zoe-dreaming.service")
    busy_wait_max_min: float = 20.0
    busy_poll_s: float = 30.0
    jobs: tuple = ("digest", "dreaming", "night_mind")
    night_mind_cmd: str = os.environ.get("NIGHT_MIND_CMD", "")
    #: the night-mind pass's standalone entry point (its contract: the docstring of ``zoe-night-mind.py`` on feat/night-mind-v1-reflection-pass): used by default as soon as the file exists
    night_mind_script: Path = REPO / "scripts" / "maintenance" / "zoe-night-mind.py"
    fallback_4b: bool = True
    py: str = sys.executable
    trial: bool = False
    job_timeout_s: "dict[str, float]" = dataclasses.field(default_factory=lambda: {"digest": 1500.0, "dreaming": 1200.0, "night_mind": 1500.0, "compaction": 900.0})
    metrics_poll_s: float = 5.0
    live_health_port: int = 11434

    @property
    def marker(self) -> Path:
        return self.night_dir / "WINDOW_OPEN"

    @property
    def training_lock(self) -> str:
        return f"{self.home}/training/logs/.training.lock"

    def model_path(self, key: str) -> str:
        return f"{self.home}/{MODEL_FILES[key]}"

    @property
    def blackout_list(self) -> tuple:
        return tuple(self.blackouts)


# ── the host: the bake-off's seam plus a watched long-running command ────────

def meminfo_mb(path: str, name: str) -> float:
    try:
        for line in Path(path).read_text().splitlines():
            if line.startswith(name + ":"):
                return int(line.split()[1]) / 1024.0
    except (OSError, ValueError):
        pass
    return 0.0


class NightHost(bk.Host):
    """Real execution. ``run_watched`` is a long command that is polled (memory floor, hard cap) while it runs and killed on a timeout."""

    def mem_field_mb(self, path: str, name: str) -> float:
        return meminfo_mb(path, name)

    def run_watched(self, argv: "list[str]", timeout: float, env: "Optional[dict]", tick: "Callable[[], None]", log_path: Path, interval: float = 5.0) -> Result:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(log_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)       # a job's output may carry household text: private, never in the report
        try:
            try:
                proc = subprocess.Popen(argv, stdout=fd, stderr=subprocess.STDOUT, env=env, start_new_session=True)
            except OSError as exc:
                return Result(127, str(exc))
        finally:
            os.close(fd)
        t_end = self.mono() + timeout
        try:
            while proc.poll() is None:
                if self.mono() >= t_end:
                    self._kill(proc)
                    return Result(124, f"timeout after {timeout:.0f}s")
                tick()
                self.sleep(interval)
            return Result(proc.returncode, "")
        except BaseException:
            self._kill(proc)
            raise

    @staticmethod
    def _kill(proc: "subprocess.Popen") -> None:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            try:
                proc.wait(timeout=15)
                return
            except subprocess.TimeoutExpired:
                continue


class NightDryHost(bk.DryHost):
    def mem_field_mb(self, path: str, name: str) -> float:
        return meminfo_mb(path, name)

    def run_watched(self, argv, timeout, env, tick, log_path, interval=5.0) -> Result:
        self.log("DRY-RUN would run (watched, timeout %.0fs): %s" % (timeout, shlex.join(argv)))
        return Result(0, "")


# ── the 12B and the 4B-at-32k, generated from unit TEXT ──────────────────────

def _set_flag(argv: "list[str]", flag: str, value: str) -> None:
    if flag in argv:
        argv[argv.index(flag) + 1] = value
    else:
        argv += [flag, value]


def translate_for_new_build(argv: "list[str]") -> "list[str]":
    """The parked unit is written for the old llama.cpp build (b9733: ``--mlock``, ``--chat-template-kwargs``). The live brain's b11194 build renamed both
    (``--load-mode mmap+mlock``, ``--reasoning off``; see llama-server.service). Used ONLY when --binary points at another build."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--mlock":
            out += ["--load-mode", "mmap+mlock"]
        elif argv[i] == "--chat-template-kwargs":
            out += ["--reasoning", "off"]
            i += 1
        else:
            out.append(argv[i])
        i += 1
    return out


def llm_spec(parked_text: str, cfg: NightCfg, lv: Levers) -> "dict[str, Any]":
    """The 12B command: the parked deep-brain unit's ExecStart (``bakeoff.deep_clone_command``: loopback, the port, ``--ctx-size``, ``--parallel 1``, no vision flags) with
    ONLY these further changes, each logged: the chosen model file, the KV cache types, ``--cache-ram`` capped (the llama.cpp default of 8 GiB is an OOM hazard on 15.6 GB
    of unified memory), ``--metrics`` ensured (the job accounting reads it), MemorySwapMax=0, and ``--fit off`` only when asked for (``cfg.fit_off``)."""
    spec = bk.deep_clone_command(parked_text, cfg.home, cfg.port, lv.ctx)
    argv = list(spec["argv"])
    _set_flag(argv, "--model", cfg.model_path(lv.model))
    _set_flag(argv, "--cache-type-k", lv.kv)
    _set_flag(argv, "--cache-type-v", lv.kv)
    _set_flag(argv, "--cache-ram", str(cfg.cache_ram_mib))
    if cfg.fit_off:
        _set_flag(argv, "--fit", "off")
    if not cfg.mlock:
        argv = [a for a in argv if a != "--mlock"]
    if cfg.ngl is not None:
        _set_flag(argv, "--n-gpu-layers", str(cfg.ngl))
    if "--metrics" not in argv:
        argv.append("--metrics")
    if cfg.binary:
        argv[0] = cfg.binary
        if "b9733" not in cfg.binary and "build-jetson-new" not in cfg.binary:
            argv = [argv[0]] + translate_for_new_build(argv[1:])
        spec["env"] = {**spec["env"], "LD_LIBRARY_PATH": str(Path(cfg.binary).parent)}
    props = {**spec["props"], "MemorySwapMax": "0"}
    if cfg.unified:
        spec["env"] = {**spec["env"], "GGML_CUDA_ENABLE_UNIFIED_MEMORY": "1"}
    return {**spec, "argv": argv, "props": props, "model": os.path.basename(cfg.model_path(lv.model)), "diff": argv_diff(spec["live_argv"], argv)}


def argv_flags(argv: "list[str]") -> "dict[str, str]":
    """``--flag value`` / bare ``--flag`` -> {flag: value}; the binary and positional values are skipped."""
    out: "dict[str, str]" = {}
    for i, a in enumerate(argv):
        if a.startswith("--"):
            nxt = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else ""
            out[a] = nxt
    return out


def argv_diff(before: "list[str]", after: "list[str]") -> "list[str]":
    """What a generated command changed against the unit text it came from: the audit line the log and the report print."""
    b, a = argv_flags(before), argv_flags(after)
    out = [f"{k} {b[k]} -> {a[k]}" for k in a if k in b and a[k] != b[k]]
    out += [f"+ {k} {a[k]}".rstrip() for k in a if k not in b]
    out += [f"- {k} {b[k]}".rstrip() for k in b if k not in a]
    if before and after and before[0] != after[0]:
        out.insert(0, f"binary {before[0]} -> {after[0]}")
    return out


def clone4_spec(live_unit_text: str, cfg: NightCfg) -> "dict[str, Any]":
    """The live 4B at ``--ctx-size 32768`` on port 11500, generated from ``systemctl --user cat llama-server.service`` (the bake-off's reflection clone)."""
    return bk.clone_command(live_unit_text, cfg.home, cfg.port, 32768)


# ── the jobs ─────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class JobSpec:
    name: str
    argv: "list[str]"
    timeout_s: float
    fallback_4b: bool = False         # runs again on the live 4B after the restore when it did not finish on the 12B
    needs_loop_miss: bool = False     # the digest: only when zoe-data's own 03:00 loop will not run it tonight
    skip_reason: str = ""


def build_jobs(cfg: NightCfg, ctx_tokens: int = 16384, decode_tps: "Optional[float]" = None, prefill_tps: "Optional[float]" = None) -> "list[JobSpec]":
    """The night's jobs, in order: the digest first (reflection and dreaming read what it stores: ``zoe-nightly-dreaming`` documents its phase 1 as 'after fact
    extraction'), the dreaming cycle, then the night-mind pass. ``night_mind``: ``--night-mind-cmd`` / ``NIGHT_MIND_CMD`` wins (``{model_url}`` is the 12B's base URL,
    ``{model_url_v1}`` the same with /v1, ``{ctx_tokens}`` the served context, ``{decode_tps}`` / ``{prefill_tps}`` the measured decode / prefill speeds); otherwise ``zoe-night-mind.py --all-members`` when
    that file exists (its branch is not merged at the time of writing); otherwise it is reported as skipped, never faked."""
    t = cfg.job_timeout_s
    base = f"http://127.0.0.1:{cfg.port}"
    jobs = {
        "digest": JobSpec("digest", [cfg.py, str(REPO / "scripts" / "night" / "jobs" / "night_digest.py")], t["digest"], fallback_4b=True, needs_loop_miss=True),
        "dreaming": JobSpec("dreaming", [cfg.py, str(REPO / "scripts" / "maintenance" / "zoe-nightly-dreaming.py"), "--skip-compaction"], t["dreaming"], fallback_4b=True),
    }
    tps = f"{decode_tps:.2f}" if decode_tps else "8.0"
    pps = f"{prefill_tps:.1f}" if prefill_tps else "650.0"                 # the 4B's rate: only when the probe gave none
    if cfg.night_mind_cmd.strip():
        cmd = (cfg.night_mind_cmd.replace("{model_url_v1}", base + "/v1").replace("{model_url}", base).replace("{ctx_tokens}", str(ctx_tokens)).replace("{decode_tps}", tps).replace("{prefill_tps}", pps))
        jobs["night_mind"] = JobSpec("night_mind", shlex.split(cmd), t["night_mind"])
    elif cfg.night_mind_script.exists():
        argv = [cfg.py, str(cfg.night_mind_script), "--model-url", base + "/v1", "--ctx-tokens", str(ctx_tokens), "--all-members"] + (["--decode-tok-s", tps] if decode_tps else []) + (["--prefill-tok-s", pps] if prefill_tps else [])
        jobs["night_mind"] = JobSpec("night_mind", argv, t["night_mind"])
    else:
        jobs["night_mind"] = JobSpec("night_mind", [], t["night_mind"], skip_reason=f"{cfg.night_mind_script.name} not found (night-mind v1 is not merged); set --night-mind-cmd / NIGHT_MIND_CMD to wire another entry point")
    return [jobs[j] for j in cfg.jobs if j in jobs]


def reflect_summary(res: dict) -> dict:
    """What a ``mpa_window.py --arm ZMA --reflect-only`` JSON says, reduced to the trial table (verdict per K cell, items, wall, calls, prompt peak)."""
    r = (res or {}).get("reflect") or {}
    cells = [{"id": c.get("id"), "verdict": c.get("verdict"), "items": (c.get("evidence") or {}).get("items")} for c in r.get("k_cells") or []]
    graded = [c for c in cells if c["verdict"] in ("PASS", "FAIL", "ERROR")]
    ip = sum(int(c["items"][0]) for c in cells if isinstance(c["items"], (list, tuple)) and len(c["items"]) == 2)
    iN = sum(int(c["items"][1]) for c in cells if isinstance(c["items"], (list, tuple)) and len(c["items"]) == 2)
    why = res.get("error") or res.get("skipped") or res.get("aborted")
    return {"cells": cells, "pass": sum(1 for c in graded if c["verdict"] == "PASS"), "graded": len(graded), "items": [ip, iN], "wall_s": r.get("wall_s"),
            "model_calls": r.get("model_calls"), "prompt_tokens_max": r.get("prompt_tokens_max"), "error": str(why) if why and not r else ""}


SPEED_LINES = 60


def speed_prompt() -> str:
    """A fixed ~1.5k-token synthetic prompt (invented names) for the one speed probe: prefill and decode tok/s of whatever model just loaded."""
    notes = "\n".join(f"Note {i}: Aldo watered the ferns in Bergvik on day {i} and Tove practised the cello after tea." for i in range(1, SPEED_LINES + 1))
    return notes + "\nIn one sentence: who practised the cello, and what did Aldo water?"


# ── the window ───────────────────────────────────────────────────────────────

class NightWindow(bk.Window):
    """The bake-off's ``Window`` (lock, compaction, buddyinfo, quiet checks, blackout maths) with the night window's own preflight, stops, lever choice, jobs and restore."""

    def __init__(self, cfg: NightCfg, host: bk.Host, log: "Callable[[str], None]", *, dry: bool = False, run_id: str = "", mode: str = "night"):
        super().__init__(cfg, host, log, dry=dry, run_id=run_id)
        self.cfg: NightCfg = cfg
        self.mode = mode
        self.cap_min = cfg.cap_min
        self.floor_armed = False
        self.stopped: "list[str]" = []                 # units this window stopped and has not brought back yet (stop order)
        self.stop_set: "tuple[str, ...]" = ()
        self.t_down: "dict[str, float]" = {}
        self.stop_epoch: "dict[str, float]" = {}
        self.up_s: "dict[str, float]" = {}
        self.parked_text = ""
        self.sizes: "dict[str, int]" = {}
        self.levers: "Optional[Levers]" = None
        self.dry_refusals: "list[str]" = []
        self.jobs: "list[dict[str, Any]]" = []
        self.speed: "dict[str, Any]" = {}
        self.trial_results: "dict[str, Any]" = {}
        self.rec: "dict[str, Any]" = {"run_id": self.run_id, "mode": mode, "events": [], "arith": {}, "restore": {}, "notes": []}
        self.outcome = "not started"
        self.restore_ok: "Optional[bool]" = None
        self.started_iso = dt.datetime.fromtimestamp(host.now()).isoformat(timespec="seconds")
        self.report_paths: "list[Path]" = []
        self.marker_owner_alive = False
        self.fallback_allowed = True                   # False after a refusal for the HOUR: a manual run in daylight must not start the night's jobs

    # ── bookkeeping ──
    def event(self, msg: str) -> None:
        self.log(msg)
        ev = self.rec["events"]
        if len(ev) < 400:
            ev.append({"t_min": round(self.elapsed_min(), 1), "msg": msg[:400]})

    def time_left_s(self) -> float:
        return max(0.0, (self.cap_min - self.cfg.reserve_min - self.elapsed_min()) * 60.0)

    def refuse(self, why: str) -> None:
        """A failed precondition: a refusal in a real run (nothing changed yet), a log line in the dry run (it goes on to print the plan)."""
        if self.dry:
            self.dry_refusals.append(why)
            self.log("DRY-RUN: a real run started now would be REFUSED: " + why)
            return
        raise Refused(why)

    def guard(self) -> None:
        if self.abort_flag:
            raise Aborted(self.abort_flag)
        if self.dry:
            return
        if self.elapsed_min() >= self.cap_min:
            raise Aborted(f"hard cap {self.cap_min:.0f} min reached")
        if self.floor_armed:
            m = self.mem()
            if m < self.cfg.min_avail_mb:
                raise Aborted(f"MemAvailable {m:.0f} MB < {self.cfg.min_avail_mb:.0f} MB floor")

    def write_marker(self) -> None:
        """The restore-only safety net: written BEFORE each stop, so a window killed half way can still be undone (the shell wrapper reads it)."""
        if self.dry:
            return
        self.cfg.night_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.marker.write_text(json.dumps({"pid": os.getpid(), "run_id": self.run_id, "mode": self.mode, "started": self.started_iso,
                                               "stopped": list(self.stopped), "transients": sorted(NIGHT_UNITS.values())}))

    # ── time ──
    def compute_cap(self) -> float:
        now = dt.datetime.fromtimestamp(self.host.now())
        to_end = self.cfg.end_by - (now.hour * 60 + now.minute + now.second / 60.0)
        if to_end <= 0:
            to_end += 1440.0
        return min(self.cfg.cap_min, to_end)

    def blackout_conflict(self) -> str:
        """The voice-gate window this run (now .. now + cap) would overlap. Trial runs (and a zoe-data kept up) also avoid 03:00, when zoe-data's digest loop fires."""
        now = dt.datetime.fromtimestamp(self.host.now())
        start = now.hour * 60 + now.minute
        end = start + self.cap_min
        spans = list(self.cfg.blackout_list)
        if self.cfg.trial and self.cfg.stop_zoe_data:
            spans.append((179, 181))               # a trial has no digest job: zoe-data down across 03:00 would silently skip that night's digest
        if not self.cfg.stop_zoe_data:
            spans += [(179, 181)] + ([(239, 241)] if now.weekday() == 6 else [])    # zoe-data stays up: its 03:00 digest and Sunday 04:00 consolidation would hit a dead brain
        for a, b in spans:
            for shift in (0, 1440):
                if start < b + shift and end > a + shift:
                    return f"a {self.cap_min:.0f}-minute window from {now:%H:%M} would overlap {a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d} ({'the voice gate' if (a, b) in self.cfg.blackout_list else 'a zoe-data maintenance loop'})"
        return ""

    # ── units ──
    def unit_state(self, unit: str) -> str:
        return self.host.run(["systemctl", "--user", "is-active", unit], mutating=False).out.strip()

    def unit_held_mib(self, unit: str) -> float:
        """What the unit's cgroup holds (``memory.current``: anon + page cache + kernel + the pinned model), the number that comes back when it stops. Falls back to the
        summed PSS of its processes. Measured 2026-10-09: the brain's cgroup held 6,046 MiB while its smaps PSS was 2,835 (the model's pinned pages are in neither RSS nor PSS)."""
        cg = self.host.run(["systemctl", "--user", "show", "-p", "ControlGroup", "--value", unit], mutating=False).out.strip()
        cur = self.host.read(f"/sys/fs/cgroup{cg}/memory.current").strip() if cg else ""
        if cur.isdigit():
            return round(int(cur) / MIB, 1)
        procs = self.host.read(f"/sys/fs/cgroup{cg}/cgroup.procs") if cg else ""
        return round(sum(parse_pss_kb(self.host.read(f"/proc/{int(x)}/smaps_rollup")) for x in procs.split() if x.isdigit()) / 1024.0, 1)

    def other_consumers(self, held: "dict[str, float]", avail: float) -> "tuple[float, list[str]]":
        """Memory held by everything the window does NOT stop: MemTotal - MemAvailable - the stop set's cgroups; and the largest resident programs (name and RSS only, never arguments)."""
        total = 0.0
        for line in self.host.read(self.cfg.meminfo).splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) / 1024.0
        rest = max(0.0, total - avail - sum(held.values())) if total else 0.0
        by: "dict[str, float]" = {}
        for line in self.host.run(["ps", "-eo", "rss=,comm="], mutating=False).out.splitlines():
            kb, _, comm = line.strip().partition(" ")
            if kb.isdigit() and comm.strip():
                by[comm.strip()] = by.get(comm.strip(), 0.0) + int(kb) / 1024.0
        top = [f"{c} {v:.0f}" for c, v in sorted(by.items(), key=lambda kv: -kv[1])[:8]]
        return rest, top

    def busy_now(self) -> str:
        """A nightly timer job is running (they share the DB, the palace and, for training, the brain), or LoRA training holds its lock."""
        for u in self.cfg.busy_units:
            if self.unit_state(u) in ("active", "activating", "reloading"):
                return f"{u} is running"
        raw = self.host.read(self.cfg.training_lock).strip()
        if raw.isdigit() and self.host.exists(f"/proc/{raw}"):
            return f"the nightly training cycle holds {self.cfg.training_lock} (pid {raw})"
        return ""

    def wait_until_idle(self) -> str:
        deadline = self.host.mono() + self.cfg.busy_wait_max_min * 60.0
        while True:
            why = self.busy_now()
            if not why:
                return ""
            if self.dry:
                self.log(f"DRY-RUN: would WAIT, polling every {self.cfg.busy_poll_s:.0f}s: {why}")
                return why
            if self.host.mono() >= deadline:
                raise Refused(f"still busy after {self.cfg.busy_wait_max_min:.0f} min: {why}")
            self.log(f"waiting: {why}")
            self.host.sleep(self.cfg.busy_poll_s)

    def poll_health(self, url: str, ok: "Callable[[str], bool]", timeout_s: float, unit: "Optional[str]" = None, guard: bool = True) -> bool:
        """True once ``ok(body)`` holds at ``url``; False on the timeout or when ``unit`` has died (checked every third poll). Never raises except through ``guard``."""
        if self.dry:
            self.log(f"DRY-RUN: would poll {url} (up to {timeout_s:.0f}s)")
            return True
        t_end, n = self.host.mono() + timeout_s, 0
        while True:
            if guard:
                self.guard()
            r = self.host.run(["curl", "-sf", "-m", "3", url], mutating=False)
            if r.rc == 0 and ok(r.out):
                return True
            n += 1
            if unit and n % 3 == 0 and self.unit_state(unit) in ("failed", "inactive"):
                self.log(f"{unit} is {self.unit_state(unit)} while waiting for {url}")
                return False
            if self.host.mono() >= t_end:
                return False
            self.host.sleep(2.0)

    # ── preflight ──
    def preflight(self) -> None:
        cfg, host = self.cfg, self.host
        now = dt.datetime.fromtimestamp(host.now())
        nm = now.hour * 60 + now.minute
        if cfg.night_only and not (cfg.night_hours[0] <= nm <= cfg.night_hours[1]):
            self.fallback_allowed = False
            self.refuse(f"it is {now:%H:%M}: a night window starts between 01:30 and 03:40 (a manual run at another hour needs --anytime: Zoe would go silent while the household is awake)")
        self.cap_min = self.compute_cap()
        need_min = min(30.0 if cfg.trial else 35.0, cfg.cap_min)         # an operator's own smaller --cap-min is not the end-by squeeze this check is about
        if self.cap_min < need_min:
            self.refuse(f"only {self.cap_min:.0f} min remain before {cfg.end_by // 60:02d}:{cfg.end_by % 60:02d} (zoe-data must be back before the 04:00 Sunday loop and the 04:10 voice gate); a window needs {need_min:.0f}")
        clash = self.blackout_conflict()
        if clash:
            self.refuse(clash)
        if not self.probe_lock():
            self.refuse(f"{cfg.lock_path} is held: a landing, the samantha bar, the bake-off or another window has the brain. Nothing was stopped.")
        self.parked_text = host.read(cfg.deep_unit)
        if not self.parked_text.strip():
            self.refuse(f"the parked 12B unit {cfg.deep_unit} is missing or empty: nothing to generate the 12B command from")
        else:
            spec = bk.deep_clone_command(self.parked_text, cfg.home, cfg.port, CTX_OPTIONS[0])        # a unit that is not the deep-brain unit is a Refused
            binary = cfg.binary or spec["binary"]
            if not host.exists(binary):
                self.refuse(f"the llama-server binary {binary} does not exist")
        self.sizes = {k: s for k in MODEL_FILES for s in [host.file_size(cfg.model_path(k))] if s}
        if not self.sizes:
            self.refuse("no 12B model file on disk: " + ", ".join(cfg.model_path(k) for k in MODEL_FILES))
        if cfg.model_choice in MODEL_FILES and cfg.model_choice not in self.sizes:
            self.refuse(f"--model {cfg.model_choice}: {cfg.model_path(cfg.model_choice)} does not exist")
        if cfg.trial:
            for need in (cfg.hs_python, Path(bk.REPO) / "scripts/perf/zmb/mpa_window.py", cfg.bakeoff_dir / "mempalace-venv" / "bin" / "mempalace-mcp"):
                if not host.exists(str(need)):
                    self.refuse(f"the trial needs {need} (the bake-off install): not found")
        if not cfg.trial and not cfg.retry_load and self.load_failures() >= cfg.max_load_failures:
            self.fallback_allowed = True
            self.refuse(f"the 12B failed to load on the last {self.load_failures()} windows on this boot (see {self.failures_path}); not putting Zoe to sleep for another try. "
                        "Fix the load (docs/knowledge/night-window.md 'when the 12B will not load'), then run once with --retry-load (or --trial); a reboot also resets this")
        active = {u: self.unit_state(u) == "active" for u in STOP_ORDER}
        if not active[BRAIN]:
            self.refuse(f"{BRAIN} is not active: there is no live brain to take over (and a dead brain at night is for the operator). Nothing was started.")
        self.stop_set = tuple(u for u in STOP_ORDER if active[u] and (u != ZOE_DATA or cfg.stop_zoe_data))
        for u in STOP_ORDER:
            if not active[u]:
                self.log(f"{u} is not active: left alone (it is not started afterwards either)")
        m = self.mem()
        frag0 = self.log_frag("at preflight")
        have_sudo = self.sudo_ok()
        if not self.contiguous_ok(frag=frag0) and not have_sudo:
            self.refuse("physical memory is fragmented and passwordless sudo is not available, so the window cannot compact it: the 12B would die on cudaMalloc / NvMap error 12. "
                        f"Run, then start again: {bk.operator_compact_command(cfg.drop_caches)}")
        waiting = self.wait_until_idle()
        waiting = self.wait_until_quiet() or waiting
        self.pre_stop_gate(m)
        if not self.dry:
            self.take_lock()
            self.cap_min = self.compute_cap()
            for recheck in (self.blackout_conflict(), self.busy_now(), self.busy_reason()):
                if recheck:
                    raise Refused(f"not clear after taking the lock: {recheck}")
        self.event(f"preflight ok: lock {'free (dry-run: not taken)' if self.dry else 'held'}, MemAvailable {m:.0f} MB, cap {self.cap_min:.0f} min, "
                   f"will stop {', '.join(self.stop_set)}" + (f"; NOT idle right now ({waiting})" if waiting else ""))

    def pre_stop_gate(self, avail_now: float) -> None:
        """Refuse BEFORE anything is stopped when even the optimistic prediction (every stopped unit frees ALL its PSS) cannot hold the smallest lever set."""
        held = {u: self.unit_held_mib(u) for u in self.stop_set}
        self.rec["pre_stop"] = {"avail_mib": round(avail_now, 0), "held_mib": held, "predicted_after_stops_mib": round(predict_avail(avail_now, held, self.stop_set)),}
        if not any(held.values()):
            self.log("pre-stop prediction skipped: the stop set's memory is unreadable here; the measurement after the stops decides")
            return
        pss = held
        rows = evaluate(self.sizes, predict_avail(avail_now, held, self.stop_set, 1.0), self.cfg.min_avail_mb, self.cfg.job_reserve_mib, self.cfg.margin_mib)
        best = max(rows, key=lambda r: r["margin_jobs"]) if rows else None
        if best and not best["fits"]:
            self.refuse(f"even if every stopped unit freed ALL of its memory ({sum(pss.values()):.0f} MiB), MemAvailable would be {best['avail']:.0f} MiB against {best['need_jobs']:.0f} MiB for the smallest "
                        f"12B ({best['levers'].label()}): margin {best['margin_jobs']:+.0f} MiB. Nothing was stopped.")

    # ── sleep ──
    def stop_one(self, unit: str) -> None:
        if unit not in self.stopped:
            self.stopped.append(unit)
        self.write_marker()                                  # the marker lists the unit BEFORE it is stopped
        self.event(f"sleep: stopping {unit}")
        r = self.host.run(["systemctl", "--user", "stop", unit], timeout=90)
        self.t_down[unit] = self.host.mono()
        self.stop_epoch[unit] = self.host.now()
        if r.rc != 0:
            raise Aborted(f"could not stop {unit}: {r.out.strip()[:200]}")

    def mem_free(self) -> float:
        return self.host.mem_field_mb(self.cfg.meminfo, "MemFree")

    def nvmap_free_mib(self) -> "Optional[float]":
        """NvMap's largest allocatable block (read-only, ``sudo -n cat`` of a debugfs file); None without sudo or on a kernel that has no such file."""
        if not self.sudo_ok():
            return None
        return parse_nvmap_free_mib(self.host.run(["sudo", "-n", "cat", NVMAP_FREE], mutating=False).out)

    def sleep_units(self) -> "tuple[float, float]":
        """Stop the units, wait for the processes to be gone and compact; returns (MemAvailable, MemFree) in MiB. Compaction is repeated (up to 4 times, as the June 2026 12B bring-up
        script did) until MemFree holds the smallest lever set's CUDA allocation: a 12B that finds 9.2 GB 'available' but fragmented / cached dies on cudaMalloc / NvMap error 12."""
        self.event("sleep: Zoe goes quiet (voice, Telegram brain replies and the app are down until the restore); stop order " + " > ".join(self.stop_set))
        for u in self.stop_set:
            self.stop_one(u)
        self.log_frag("after the stops")
        self.before_model_start("before the 12B start", BRAIN)
        floor_cuda = min((need_mib(sz, lv, 0.0)["need_cuda"] for lv in lever_order(tuple(self.sizes)) for sz in [self.sizes[lv.model]]), default=0.0)
        for i in range(1, 7):
            big = high_order_mib(self.fragmentation(), self.cfg.contig_order)
            self.rec.setdefault("frag_mib", []).append(round(big))
            if (self.mem_free() >= floor_cuda and big >= floor_cuda) or not self.sudo_ok():
                break
            self.event(f"MemFree {self.mem_free():.0f} MiB, {big:.0f} MiB in free blocks of 2 MB and up; the smallest 12B wants {floor_cuda:.0f} of each: compacting again ({i}/6)")
            self.host.sleep(3.0)
            self.compact_memory(f"retry {i} before the 12B start")
        nv = self.nvmap_free_mib()
        self.rec["nvmap_max_alloc_mib"] = None if nv is None else round(nv)
        if nv is not None:
            self.event(f"NvMap's largest allocatable IOVMM block: {nv:.0f} MiB (the smallest 12B asks for one buffer of {floor_cuda - 600 - kv_mib(16384, 'q4_0'):.0f} MiB)"
                       + ("" if nv >= floor_cuda - COMPUTE_MIB else " - LESS than the model buffer: the load is expected to fail on cudaMalloc"))
        first = self.mem()
        self.host.sleep(2.0)
        return min(first, self.mem()), self.mem_free()                 # two samples 2 s apart: the lower MemAvailable

    # ── choose + load ──
    def table_lines(self, rows: "list[dict[str, Any]]", chosen: "Optional[Levers]") -> "list[str]":
        out = [f"{'levers':<26}{'model':>7}{'KV':>6}{'compute':>8}{'floor':>6}{'NEED load':>10}{'+jobs':>7}{'avail':>8}{'margin':>8}  fits"
               + ("" if not rows or rows[0].get("free") is None else f"  (MemFree {rows[0]['free']:.0f}, CUDA needs listed per row)")]
        for r in rows:
            lv: Levers = r["levers"]
            mark = "CHOSEN" if chosen == lv else ("fits" if r["fits"] else ("load only" if r["fits_load_only"] else "NO"))
            out.append(f"{lv.label():<26}{r['model']:>7.0f}{r['kv']:>6.0f}{r['compute']:>8.0f}{r['floor']:>6.0f}{r['need_load']:>10.0f}{r['need_jobs']:>7.0f}{r['avail']:>8.0f}{r['margin_jobs']:>+8.0f}  {mark}")
        return out

    def choose(self, avail: float, free: "Optional[float]" = None) -> "dict[str, Any]":
        cfg = self.cfg
        rows = evaluate(self.sizes, avail, cfg.min_avail_mb, cfg.job_reserve_mib, cfg.margin_mib, free_mib=free)
        pick = choose_levers(rows, model=cfg.model_choice, ctx=cfg.ctx_choice, kv=cfg.kv_choice)
        pre = self.rec.get("pre_stop") or {}
        self.rec["arith"] = {"avail_measured_mib": round(avail, 0), "free_measured_mib": None if free is None else round(free, 0),
                             "predicted_after_stops_mib": pre.get("predicted_after_stops_mib"), "avail_before_mib": pre.get("avail_mib"), "floor_mib": cfg.min_avail_mb, "job_reserve_mib": cfg.job_reserve_mib,
                             "rows": [{"levers": r["levers"].label(), "need_load": round(r["need_load"]), "need_jobs": round(r["need_jobs"]), "margin_jobs": round(r["margin_jobs"]),
                                       "fits": r["fits"]} for r in rows], "chosen": pick["levers"].label() if pick else None}
        for line in self.table_lines(rows, pick["levers"] if pick else None):
            self.log("  " + line)
        if not pick:
            best = max((r for r in rows if r["levers"].model == (cfg.model_choice if cfg.model_choice in MODEL_FILES else "qat")), key=lambda r: r["margin_jobs"], default=None)
            raise Refused(f"no lever set fits: MemAvailable {avail:.0f} MiB and MemFree {0 if free is None else free:.0f} MiB after the stops and the compaction; the closest set is short by "
                          f"{-(min(best['margin_jobs'], best['margin_free'] if best['margin_free'] is not None else best['margin_jobs']) if best else 0):.0f} MiB. Everything is being put back.")
        self.levers = pick["levers"]
        self.event(f"levers: {self.levers.label()} (model {pick['model']:.0f} + KV {pick['kv']:.0f} + compute {pick['compute']:.0f} + floor {pick['floor']:.0f} "
                   f"+ jobs {pick['job_reserve']:.0f} = {pick['need_jobs']:.0f} MiB needed; {avail:.0f} MiB available, margin {pick['margin_jobs']:+.0f}; "
                   f"MemFree {0 if free is None else free:.0f} against {pick['need_cuda']:.0f} for the CUDA buffers)")
        return pick

    def start_transient(self, key: str, spec: "dict[str, Any]") -> None:
        r = self.host.run(bk.systemd_run(NIGHT_UNITS[key], spec["argv"], env=spec.get("env"), props=spec.get("props")))
        if r.rc != 0:
            raise Aborted(f"could not start {NIGHT_UNITS[key]}: {r.out.strip()[:200]}")
        self.started.append(NIGHT_UNITS[key])

    def stop_transient(self, key: str) -> None:
        self.host.run(["systemctl", "--user", "stop", NIGHT_UNITS[key]], timeout=90)
        self.host.run(["systemctl", "--user", "reset-failed", NIGHT_UNITS[key]])

    def probe_speed(self, label: str) -> "dict[str, Any]":
        """One fixed ~1.5k-token completion: prefill and decode tok/s of the model that just loaded (llama-server's own ``timings``)."""
        if self.dry:
            return {}
        payload = json.dumps({"model": "local", "messages": [{"role": "user", "content": speed_prompt()}], "max_tokens": 96, "temperature": 0, "stream": False})
        # Watched, not a bare blocking call: the memory floor and the hard cap are checked every poll, and the request is bounded by the time left before the restore
        # reserve (a stuck or RAM-hungry first request must never keep Zoe's brain down past the cap).
        left = self.time_left_s()
        if left < 60:
            self.event(f"speed {label}: probe skipped - no time left ({left:.0f}s before the restore reserve)")
            return {}
        budget = min(900.0, left)
        log_path = self.cfg.night_dir / "logs" / f"{self.run_id}-speed-{label.replace('@', '-')}.log"
        if log_path.exists():
            log_path.unlink()
        r = self.host.run_watched(["curl", "-sf", "-m", f"{budget:.0f}", "-H", "Content-Type: application/json", "-d", payload, f"http://127.0.0.1:{self.cfg.port}/v1/chat/completions"],
                                  budget + 10.0, None, self.guard, log_path, self.cfg.metrics_poll_s)
        try:
            body = self.host.read(str(log_path))
        finally:
            log_path.unlink(missing_ok=True)                    # the probe's reply is synthetic text, but nothing needs to keep it
        d = _json(body)
        t, u = d.get("timings") or {}, d.get("usage") or {}
        out = {"label": label, "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
               "prefill_tps": round(float(t["prompt_per_second"]), 1) if t.get("prompt_per_second") else None,
               "decode_tps": round(float(t["predicted_per_second"]), 2) if t.get("predicted_per_second") else None, "ok": r.rc == 0}
        self.event(f"speed {label}: prefill {out['prefill_tps']} tok/s, decode {out['decode_tps']} tok/s ({out['prompt_tokens']} prompt tokens)")
        return out

    # ── 12B load failures: diagnose, count, stop retrying every night ──
    @property
    def failures_path(self) -> Path:
        return self.cfg.night_dir / "load_failures.json"

    def boot_id(self) -> str:
        return self.host.read("/proc/sys/kernel/random/boot_id").strip()

    def load_failures(self) -> int:
        """Consecutive windows, on THIS boot, on which the 12B failed to load (0 after a success or a reboot)."""
        d = _json(self.host.read(str(self.failures_path)))
        return int(d.get("count", 0)) if d.get("boot_id") == self.boot_id() and self.boot_id() else 0

    def record_load(self, ok: bool, why: str = "") -> None:
        if self.dry:
            return
        try:
            self.cfg.night_dir.mkdir(parents=True, exist_ok=True)
            n = 0 if ok else self.load_failures() + 1
            self.failures_path.write_text(json.dumps({"count": n, "boot_id": self.boot_id(), "last": self.started_iso, "why": why[:300]}))
        except OSError as exc:
            self.log(f"could not record the load result: {exc}")

    def diagnose_load_failure(self) -> str:
        """What the 12B said before it died (the allocation line, never the prompt): the lines of its journal that name an allocation or a load error, plus the RAM state."""
        out = self.host.run(["journalctl", "--user", "-u", NIGHT_UNITS["llm"], "-n", "80", "--no-pager", "-o", "cat"], mutating=False).out
        keep = [ln.strip()[:220] for ln in out.splitlines() if re.search(r"cudaMalloc|NvMap|allocating|out of memory|error loading|failed to load|unknown argument|invalid argument", ln)]
        frag = self.fragmentation()
        nv = self.nvmap_free_mib()
        state = (f"MemFree {self.mem_free():.0f} MiB, MemAvailable {self.mem():.0f} MiB, {high_order_mib(frag, self.cfg.contig_order):.0f} MiB in free blocks of 2 MB and up"
                 + ("" if nv is None else f", NvMap largest allocatable block {nv:.0f} MiB"))
        for ln in keep[-5:]:
            self.event("12B said: " + ln)
        self.event("RAM when it failed: " + state)
        self.rec["load_diagnosis"] = {"lines": keep[-5:], "ram": state}
        return ("cuda_alloc" if any("cudaMalloc" in ln for ln in keep) else "died") + ": " + (keep[-1] if keep else "no error line in its journal")

    def load_12b(self, pick: "dict[str, Any]") -> None:
        cfg = self.cfg
        spec = llm_spec(self.parked_text, cfg, pick["levers"])
        self.event("12B command (generated from the parked unit's ExecStart, which is read and never edited): " + shlex.join(spec["argv"]))
        self.event("12B differs from the parked ExecStart in: " + "; ".join(spec["diff"]))
        self.event(f"12B environment: GGML_CUDA_ENABLE_UNIFIED_MEMORY={'1 (set: cudaMallocManaged)' if 'GGML_CUDA_ENABLE_UNIFIED_MEMORY' in spec['env'] else 'NOT set (plain cudaMalloc)'}; levers ngl={cfg.ngl or 'parked 99'}, mlock={cfg.mlock}")
        self.rec["arith"]["unified_memory"] = "GGML_CUDA_ENABLE_UNIFIED_MEMORY" in spec["env"]
        self.rec["arith"]["load_started_s"] = self.host.mono()
        self.start_transient("llm", spec)
        url = f"http://127.0.0.1:{cfg.port}/health"
        if not self.poll_health(url, _status_ok, 480.0, unit=NIGHT_UNITS["llm"]):
            why = self.diagnose_load_failure()
            self.record_load(False, why)
            raise LoadFailed(f"the 12B did not load ({why}); see `journalctl --user -u {NIGHT_UNITS['llm']}` and docs/knowledge/night-window.md 'when the 12B will not load'")
        self.record_load(True)
        self.floor_armed = True
        m = self.mem()
        self.rec["arith"]["load_s"] = round(self.host.mono() - self.rec["arith"].pop("load_started_s", self.host.mono()), 1)
        self.rec["arith"]["avail_after_load_mib"] = round(m, 0)
        self.event(f"12B healthy; MemAvailable {m:.0f} MiB (floor {cfg.min_avail_mb:.0f})")
        self.guard()
        props = _json(self.host.run(["curl", "-sf", "-m", "5", f"http://127.0.0.1:{cfg.port}/props"], mutating=False).out)
        n_ctx = ((props.get("default_generation_settings") or {}).get("n_ctx")) or props.get("n_ctx")
        self.rec["arith"]["served_ctx"] = n_ctx
        if n_ctx and int(n_ctx) < pick["levers"].ctx:
            raise Aborted(f"the 12B serves --ctx-size {n_ctx}, below the {pick['levers'].ctx} the arithmetic was done for")
        self.speed = self.probe_speed("12B")
        self.rec["speed"] = {"12B": self.speed}

    def unload_llm(self) -> None:
        self.floor_armed = False
        for key in ("llm", "clone4"):
            self.stop_transient(key)
        self.wait_pid_gone(NIGHT_UNITS["llm"])
        self.wait_pid_gone(NIGHT_UNITS["clone4"])

    # ── jobs ──
    def job_env(self, backend_port: int, model_name: str, scale: int) -> dict:
        """The job's environment: this process's (the unit's EnvironmentFile; never printed or logged) plus the model it must talk to."""
        env = dict(os.environ)
        env.update({"GEMMA_SERVER_URL": f"http://127.0.0.1:{backend_port}/v1", "ZOE_DIGEST_LLM_TIMEOUT_SCALE": str(scale), "MALLOC_ARENA_MAX": "2", "PYTHONDONTWRITEBYTECODE": "1"})
        if model_name:
            env["MEMORY_DIGEST_MODEL"] = model_name
        return env

    def metrics(self, port: int) -> "dict[str, float]":
        return parse_metrics(self.host.run(["curl", "-sf", "-m", "5", f"http://127.0.0.1:{port}/metrics"], mutating=False).out)

    def exec_job(self, job: JobSpec, backend: str, port: int, model_name: str, scale: int) -> "dict[str, Any]":
        rec: "dict[str, Any]" = {"name": job.name, "backend": backend, "status": "not run", "rc": None, "wall_s": None, "tokens_prompt": None, "tokens_predicted": None,
                                 "mem_low_mib": None, "notes": []}
        self.jobs.append(rec)
        if job.skip_reason:
            rec.update(status="skipped", notes=[job.skip_reason])
            self.event(f"job {job.name}: skipped - {job.skip_reason}")
            return rec
        left = self.time_left_s() if backend == "12B" else (self.cfg.blackout_list[0][0] - self.minutes_now()) * 60.0     # a 4B fallback must end before the voice gate
        if left < 120:
            rec.update(status="no time", notes=[f"no time left ({left:.0f}s before the restore reserve)"])      # NOT "skipped": a requested job that never ran is unfinished work (exit 5 unless the 4B covered it)
            self.event(f"job {job.name}: skipped - no time left")
            return rec
        timeout = min(job.timeout_s, left)
        m0 = self.metrics(port)
        low = [self.mem()]
        rec["mem_at_start_mib"] = round(low[0], 0)
        log_path = self.cfg.night_dir / "logs" / f"{self.run_id}-{job.name}-{backend}.log"

        def tick() -> None:
            low[0] = min(low[0], self.mem())
            if backend == "12B":
                self.guard()

        self.event(f"job {job.name} on the {backend}: {os.path.basename(job.argv[1]) if len(job.argv) > 1 else job.argv[0]} (timeout {timeout:.0f}s)")
        t0 = self.host.mono()
        try:
            res = self.host.run_watched(job.argv, timeout, self.job_env(port, model_name, scale), tick, log_path, self.cfg.metrics_poll_s)
        except Aborted as exc:
            rec.update(status="killed", notes=[str(exc)], wall_s=round(self.host.mono() - t0, 1), mem_low_mib=round(low[0], 0))
            raise
        rec["wall_s"], rec["rc"], rec["mem_low_mib"] = round(self.host.mono() - t0, 1), res.rc, round(min(low[0], self.mem()), 0)
        m1 = self.metrics(port)
        if m0 and m1:
            rec["tokens_prompt"] = int(m1.get("prompt_tokens_total", 0) - m0.get("prompt_tokens_total", 0))
            rec["tokens_predicted"] = int(m1.get("tokens_predicted_total", 0) - m0.get("tokens_predicted_total", 0))
        rec["status"] = "ok" if res.rc == 0 else "timeout" if res.rc == 124 else "degraded" if res.rc == 3 else f"failed(rc {res.rc})"
        rec["notes"] = [ln[:300] for ln in self.host.read(str(log_path)).splitlines() if ln.startswith("NIGHT_JOB ")][-3:]
        self.event(f"job {job.name} {rec['status']} on the {backend}: {rec['wall_s']}s, {rec['tokens_prompt']} prompt / {rec['tokens_predicted']} generated tokens, MemAvailable low {rec['mem_low_mib']} MiB")
        return rec

    def run_jobs(self) -> None:
        model_name = os.path.basename(self.cfg.model_path(self.levers.model)) if self.levers else ""
        scale = digest_timeout_scale(self.speed.get("decode_tps"))
        self.event(f"model-call timeouts scaled x{scale} for the 12B's measured decode speed ({self.speed.get('decode_tps')} tok/s)")
        for job in build_jobs(self.cfg, self.levers.ctx if self.levers else 16384, self.speed.get("decode_tps"), self.speed.get("prefill_tps")):
            self.guard()
            self.exec_job(job, "12B", self.cfg.port, model_name, scale)

    # ── the trial ──
    def start_shim(self) -> None:
        cfg = self.cfg
        shim = [str(cfg.hs_python), str(bk.REPO / "scripts/perf/zmb/embed_shim.py"), "--serve", "--port", str(cfg.shim_port), "--model", "minilm"]
        env = {"HF_HUB_OFFLINE": "1", "ORT_DISABLE_TELEMETRY": "1", "ZMB_ORT_OPT": "all", "MALLOC_ARENA_MAX": "1"}
        self.start_transient("shim", {"argv": shim, "env": env, "props": {"MemoryMax": "300M", "MemorySwapMax": "0"}})
        if not self.poll_health(f"http://127.0.0.1:{cfg.shim_port}/health", lambda t: "minilm" in t.lower(), 90.0, unit=NIGHT_UNITS["shim"], guard=False):
            raise Aborted("the MiniLM embeddings shim did not become healthy (ZMA refuses any other embedder)")

    def trial_phase(self, label: str, timeout_s: float) -> "dict[str, Any]":
        cfg = self.cfg
        out = cfg.night_dir / f"trial-{self.run_id}-{label.replace('@', '-')}.json"
        argv = [cfg.py, str(bk.REPO / "scripts/perf/zmb/mpa_window.py"), "--arm", "ZMA", "--reflect-only", "--out", str(out), "--clone-url", f"http://127.0.0.1:{cfg.port}",
                "--seed", "zmb-v1", "--box-s", "300", "--ctx", str(self.levers.ctx if label == "12B" and self.levers else 32768), "--model-name", label]
        env = {**os.environ, "MALLOC_PERTURB_": "85", "PYTHONMALLOC": "malloc", "ORT_DISABLE_TELEMETRY": "1", "ZMB_HM_EMBEDDER_URL": f"http://127.0.0.1:{cfg.shim_port}"}
        m0, t0, low = self.metrics(cfg.port), self.host.mono(), [self.mem()]

        def tick() -> None:
            low[0] = min(low[0], self.mem())
            self.guard()

        self.event(f"trial {label}: the ZMA reflection pass + K1-K5 on the bench's synthetic household (seed zmb-v1), timeout {timeout_s:.0f}s")
        res = self.host.run_watched(argv, timeout_s, env, tick, cfg.night_dir / "logs" / f"{self.run_id}-trial-{label.replace('@', '-')}.log", cfg.metrics_poll_s)
        raw = self.host.read(str(out))
        summ = reflect_summary(json.loads(raw)) if raw.strip() else {"cells": [], "pass": 0, "graded": 0, "items": [0, 0], "error": f"the driver wrote no result (rc {res.rc})"}
        m1 = self.metrics(cfg.port)
        summ.update({"label": label, "driver_rc": res.rc, "wall_total_s": round(self.host.mono() - t0, 1), "mem_low_mib": round(min(low[0], self.mem()), 0),
                     "tokens_prompt": int(m1.get("prompt_tokens_total", 0) - m0.get("prompt_tokens_total", 0)) if m0 and m1 else None,
                     "tokens_predicted": int(m1.get("tokens_predicted_total", 0) - m0.get("tokens_predicted_total", 0)) if m0 and m1 else None})
        summ["night_mind_cells"] = self.night_mind_cells(label)
        self.event(f"trial {label}: K cells {summ['pass']}/{summ['graded']} pass, items {summ['items']}, {summ['wall_total_s']}s, {summ['tokens_prompt']} prompt / {summ['tokens_predicted']} generated tokens"
                   + (f", {summ['error']}" if summ.get("error") else ""))
        return summ

    def night_mind_cells(self, label: str) -> "dict[str, Any]":
        """When the night-mind entry point exists: its own cell scoring (``--cells``: K1-K12, lab stores, no member touched) against the model on :11500; its one-JSON-object stdout is read from the job log."""
        cfg = self.cfg
        if not cfg.night_mind_script.exists():
            return {}
        log = cfg.night_dir / "logs" / f"{self.run_id}-trial-nm-{label.replace('@', '-')}.log"
        ctx = self.levers.ctx if label == "12B" and self.levers else 32768
        argv = [cfg.py, str(cfg.night_mind_script), "--model-url", f"http://127.0.0.1:{cfg.port}/v1", "--ctx-tokens", str(ctx), "--cells", "--model-name", label]
        if label == "12B" and self.speed.get("decode_tps"):    # the cells' calls get the same measured-rate budget as the member pass (a 12B at 3.6 tok/s is not the 4B at 60)
            argv += ["--decode-tok-s", f"{self.speed['decode_tps']:.2f}"]
        if label == "12B" and self.speed.get("prefill_tps"):
            argv += ["--prefill-tok-s", f"{self.speed['prefill_tps']:.1f}"]
        res = self.host.run_watched(argv, 420.0, {**os.environ, "ZOE_HARNESS": "1"}, lambda: self.guard(), log, cfg.metrics_poll_s)
        for d in reversed(list(_json_objects(self.host.read(str(log))))):          # the whole object, compact or indented: the CLI's stdout is not guaranteed one line
            if isinstance(d.get("cells"), dict) and d["cells"]:
                return {k: v for k, v in d["cells"].items() if isinstance(v, (str, int))}
        return {"error": f"no cells object in the output (rc {res.rc})"}

    def trial_timeout_12b(self) -> float:
        """720 s at the 4B-like speed the phase was sized for; longer in proportion when the measured decode is slower (2026-10-09: 5.4 tok/s with 24 layers on the GPU timed out at 720 s),
        never beyond what the cap leaves after reserving the restore and the 4B phase."""
        tps = self.speed.get("decode_tps") or 11.0
        want = 720.0 * max(1.0, 11.0 / tps)
        room = (self.cap_min - self.cfg.reserve_min) * 60.0 - self.elapsed_min() * 60.0 - 300.0
        return max(300.0, min(want, room))

    def run_trial(self, pick: "dict[str, Any]") -> None:
        """12B first; a 12B that does not load is recorded and the 4B at 32k is STILL measured (the baseline is worth having), then the failure is raised so the exit code says so."""
        self.start_shim()
        failed: "Optional[LoadFailed]" = None
        try:
            self.load_12b(pick)
            self.trial_results["12B"] = self.trial_phase("12B", self.trial_timeout_12b())
        except LoadFailed as exc:
            failed = exc
            self.trial_results["12B"] = {"label": "12B", "error": str(exc), "cells": [], "pass": 0, "graded": 0, "items": [0, 0], "load_failed": True}
        if self.cfg.skip_4b:
            self.rec["trial"] = self.trial_results
            if failed:
                raise failed
            self.raise_if_empty_trial()
            return
        self.event("trial: unloading the 12B, loading the live 4B at --ctx-size 32768 on the same port")
        self.unload_llm()
        self.before_model_start("before the 4B@32k clone", NIGHT_UNITS["llm"])
        spec = clone4_spec(self.cfg.live_unit_text, self.cfg)
        self.event("4B@32k command (generated from `systemctl --user cat llama-server.service`): " + shlex.join(spec["argv"]))
        self.start_transient("clone4", spec)
        if not self.poll_health(f"http://127.0.0.1:{self.cfg.port}/health", _status_ok, 240.0, unit=NIGHT_UNITS["clone4"], guard=False):
            raise Aborted("the 4B at 32k was not healthy")
        self.floor_armed = True
        self.rec.setdefault("speed", {})["4B@32k"] = self.probe_speed("4B@32k")
        self.trial_results["4B@32k"] = self.trial_phase("4B@32k", 420.0)
        self.rec["trial"] = self.trial_results
        if failed:
            raise failed
        self.raise_if_empty_trial()

    def raise_if_empty_trial(self) -> None:
        empty = [k for k, t in self.trial_results.items() if t.get("error")]
        if empty:                                              # the box is fine, the measurement is not: say so in the outcome and the exit code (the restore still runs)
            raise Aborted("trial phase(s) without a result: " + ", ".join(f"{k} ({self.trial_results[k]['error'][:80]})" for k in empty))

    # ── the minutes helper ──
    def minutes_now(self) -> float:
        now = dt.datetime.fromtimestamp(self.host.now())
        m = now.hour * 60 + now.minute + now.second / 60.0
        return m if m <= 600 else m - 1440        # a late-evening clock reads as 'before' the early-morning end_by

    # ── wake ──
    def compact_before_brain(self) -> None:
        """Before the 4B starts again: the 12B that just died may have left RAM fragmented, and a brain that fails on NvMap error 12 is the worst morning. Never raises."""
        try:
            self.log_frag("before restarting the brain")
            if self.sudo_ok():
                self.wait_pid_gone(NIGHT_UNITS["llm"])
                self.compact_memory("before restarting the brain")
            elif not self.contiguous_ok():
                self.event(f"RESTORE ALARM: RAM is fragmented and passwordless sudo is not available: {BRAIN} is likely to fail with cudaMalloc out of memory / NvMap error 12. "
                           f"Run: {bk.operator_compact_command(self.cfg.drop_caches)} and then: systemctl --user start {BRAIN}")
        except Exception as exc:  # noqa: BLE001 - restore never raises
            self.event(f"RESTORE ALARM: compaction before the brain start errored ({type(exc).__name__}: {exc})")

    def bring_back(self, unit: str) -> "dict[str, Any]":
        """Start ``unit`` and require its OWN health answer. One retry (a restart, with a fresh compaction for the brain); then the failure is reported, not hidden."""
        spec = HEALTH[unit]
        info: "dict[str, Any]" = {"ok": False, "attempts": 0, "what": spec.what}
        for attempt, verb in ((1, "start"), (2, "restart")):
            info["attempts"] = attempt
            if attempt == 2:
                self.event(f"RESTORE: {unit} not healthy after {spec.wait_s:.0f}s - retrying with a restart")
                if unit == BRAIN:
                    self.compact_before_brain()
            self.host.run(["systemctl", "--user", verb, unit], timeout=spec.wait_s + 60)
            if self.poll_health(spec.url, spec.ok, spec.wait_s, unit=unit, guard=False):
                info["ok"] = True
                info["down_s"] = round(self.host.mono() - self.t_down[unit], 0) if unit in self.t_down else None
                self.up_s[unit] = self.host.now()
                break
        self.event(f"RESTORE: {unit} {'HEALTHY' if info['ok'] else 'NOT HEALTHY'} ({spec.what}; {info['attempts']} attempt(s)"
                   + (f"; down {info['down_s']:.0f}s" if info.get("down_s") is not None else "") + ")")
        return info

    def load_marker(self) -> bool:
        """Read the marker a window leaves while it is open. ``self.marker_owner_alive`` is True when the process that wrote it is still a running night_window: a second
        instance (a refused start, the shell trap) must not 'restore' units the first one is about to wake itself (2026-10-09: two restores raced and one raised a false ALARM)."""
        raw = self.host.read(str(self.cfg.marker))
        if not raw.strip():
            return False
        data = _json(raw)
        pid = data.get("pid")
        self.marker_owner_alive = bool(isinstance(pid, int) and pid != os.getpid() and self.host.exists(f"/proc/{pid}") and "night_window" in self.host.read(f"/proc/{pid}/cmdline"))
        self.stopped = [u for u in RESTORE_ORDER if u in (data.get("stopped") or [])]
        self.opened = True
        return True

    def alarm(self, failed: "list[str]") -> None:
        lines = ["*** NIGHT WINDOW RESTORE FAILED ***", f"run {self.run_id}: still down or unhealthy: {', '.join(failed)}", "",
                 "Put them back, in this order, then check each /health:",
                 *[f"  systemctl --user start {u}    # {HEALTH[u].url}" for u in RESTORE_ORDER if u in failed],
                 "or re-run:  scripts/night/night_window.sh --restore-only", "",
                 f"If the brain fails on cudaMalloc / NvMap error 12:  {bk.operator_compact_command(self.cfg.drop_caches)}"]
        for ln in lines:
            self.log(ln)
        if not self.dry:
            try:
                self.cfg.report_dir.mkdir(parents=True, exist_ok=True)
                (self.cfg.report_dir / "ALARM").write_text("\n".join(lines) + "\n")
            except OSError as exc:
                self.log(f"could not write the ALARM file: {exc}")

    def restore(self) -> bool:
        """Put everything back. Idempotent; wakes ONLY what this window stopped, and only the units that were active before; never raises. Order: brain, Kokoro, router, zoe-data."""
        cfg = self.cfg
        self.floor_armed = False
        try:
            self.event("RESTORE: stopping the 12B / 4B@32k clone / shim, then waking: " + (" > ".join(u for u in RESTORE_ORDER if u in self.stopped) or "(nothing was stopped)"))
            for key in ("llm", "clone4", "shim"):
                self.stop_transient(key)
            failed: "list[str]" = []
            for unit in RESTORE_ORDER:
                if unit not in self.stopped:
                    continue
                if unit == BRAIN:
                    self.compact_before_brain()
                info = self.bring_back(unit)
                self.rec["restore"][unit] = info
                if info["ok"]:
                    self.stopped.remove(unit)
                    self.write_marker()
                else:
                    failed.append(unit)
            if failed:
                self.restore_status = "NOT HEALTHY: " + ", ".join(failed)
                self.alarm(failed)
                self.restore_ok = False
                return False
            if not self.dry:
                for stale in (cfg.marker, cfg.report_dir / "ALARM"):
                    if stale.exists():
                        stale.unlink()
            self.restore_status = "everything back and healthy"
            self.restore_ok = True
            self.event("RESTORE: " + self.restore_status)
            return True
        except Exception as exc:  # noqa: BLE001 - restore never raises
            self.event(f"RESTORE FAILED with {type(exc).__name__}: {exc}")
            self.restore_ok = False
            self.alarm(list(self.stopped) or [BRAIN])
            return False

    def digest_loop_missed(self) -> bool:
        """True when zoe-data was down across 03:00 (so its in-process digest loop sleeps until tomorrow): this window's digest, or its fallback, is then the night's only one."""
        t0 = self.stop_epoch.get(ZOE_DATA)
        if t0 is None:
            return False
        d0 = dt.datetime.fromtimestamp(t0)
        nxt = d0.replace(hour=3, minute=0, second=0, microsecond=0)
        if nxt <= d0:
            nxt += dt.timedelta(days=1)
        return nxt.timestamp() <= self.up_s.get(ZOE_DATA, self.host.now())

    def after_restore(self) -> None:
        """With the box awake again: the weekly index-compaction trigger (it asks zoe-data) and the 4B fallback for every job that did not finish on the 12B."""
        cfg = self.cfg
        if self.mode != "night" or self.dry or not self.restore_ok:
            return
        brain_ok = self.poll_health(HEALTH[BRAIN].url, HEALTH[BRAIN].ok, 5.0, guard=False)
        planned = build_jobs(cfg)
        ran_full_dreaming = False
        for job in planned:
            if not job.fallback_4b or not cfg.fallback_4b:
                continue
            done = any(r["name"] == job.name and r["backend"] == "12B" and r["status"] == "ok" for r in self.jobs)
            if done:
                continue
            if job.needs_loop_miss and not self.digest_loop_missed():
                self.rec["notes"].append(f"{job.name}: not done on the 12B; zoe-data's own 03:00 loop will run it (zoe-data was up across 03:00)")
                continue
            if not brain_ok:
                self.rec["notes"].append(f"{job.name}: not done on the 12B and the 4B brain is not answering: no fallback")
                continue
            argv = [a for a in job.argv if a != "--skip-compaction"]            # zoe-data is up: the full dreaming script, compaction trigger included
            ran_full_dreaming = ran_full_dreaming or job.name == "dreaming"
            self.event(f"fallback: {job.name} did not finish on the 12B; running it on the live 4B")
            self.exec_job(dataclasses.replace(job, argv=argv), "4B", cfg.live_health_port, "", 1)
        dreamt = any(r["name"] == "dreaming" and r["backend"] == "12B" and r["status"] == "ok" for r in self.jobs)
        if dreamt and not ran_full_dreaming:
            comp = JobSpec("index_compaction", [cfg.py, str(REPO / "scripts" / "maintenance" / "zoe-nightly-dreaming.py"), "--only-compaction"], cfg.job_timeout_s["compaction"])
            self.exec_job(comp, "4B", cfg.live_health_port, "", 1)

    def fallback_clearance(self) -> str:
        """A refused window may still run the night's jobs on the live 4B, but ONLY with the same guarantees as a real window: the brain-window lock is ours (a landing, the
        samantha bar, the bake-off or another window may hold it), the nightly timer jobs / LoRA training are not running, and the panel is quiet. '' when clear, else why not."""
        if self.lock_fd is None:
            try:
                self.take_lock()
            except Refused as exc:
                return f"the brain-window lock is held by someone else ({exc})"
        return self.busy_now() or self.busy_reason()

    # ── the run ──
    def run(self) -> int:
        if self.dry:
            return self.run_dry()
        rc = EXIT_OK
        try:
            self.preflight()
            if self.cfg.trial:
                self.cfg.live_unit_text = self.host.run(["systemctl", "--user", "cat", BRAIN], mutating=False).out
            self.opened = True
            self.write_marker()
            avail, free = self.sleep_units()
            pick = self.choose(avail, free)
            if self.cfg.trial:
                self.run_trial(pick)
            else:
                self.load_12b(pick)
                self.guard()
                self.run_jobs()
            self.outcome = "ok"
        except Refused as exc:
            self.event(f"REFUSED: {exc}")
            self.outcome, rc = f"refused: {exc}", EXIT_REFUSED
        except Aborted as exc:
            self.event(f"ABORTED: {exc}")
            self.outcome, rc = f"aborted: {exc}", EXIT_ABORTED
        except BaseException as exc:  # noqa: BLE001 - ANY failure still restores
            self.event("FAILED: " + "".join(traceback.format_exception_only(type(exc), exc)).strip())
            self.log(traceback.format_exc()[-1500:])
            self.outcome, rc = f"failed: {type(exc).__name__}: {exc}", EXIT_ABORTED
        finally:
            restored = True
            if self.opened or self.stopped:
                restored = self.restore()
            elif self.cfg.fallback_4b and self.mode == "night" and self.fallback_allowed:
                why = self.fallback_clearance()
                if why:
                    self.event(f"no 4B fallback jobs: {why}")
                    self.rec["notes"].append(f"no 4B fallback jobs: {why}")
                else:
                    self.restore_ok = True                          # nothing was stopped, the lock is ours and the box is idle: the fallbacks below may run
            if not restored:
                rc = EXIT_RESTORE_FAILED
            try:
                self.after_restore()
            except Exception as exc:  # noqa: BLE001
                self.event(f"after-restore step failed: {type(exc).__name__}: {exc}")
            if rc == EXIT_OK and any(j["status"] not in ("ok", "skipped") and not (j["backend"] == "12B" and self.fallback_covered(j)) for j in self.jobs):
                rc = EXIT_JOBS_FAILED
            self.release_lock()
            self.write_report(rc)
        return rc

    def run_dry(self) -> int:
        """Reads for real (units, memory, files, the panel), prints the plan and the arithmetic, executes nothing that changes anything."""
        try:
            self.preflight()
            self.dry_plan()
            return EXIT_OK
        except Refused as exc:
            self.log(f"DRY-RUN: a real run started now would be REFUSED: {exc}")
            return EXIT_REFUSED

    def fallback_covered(self, job_rec: "dict[str, Any]") -> bool:
        """A 12B job that failed is not a failure of the night when its 4B fallback finished."""
        return any(r["name"] == job_rec["name"] and r["backend"] == "4B" and r["status"] == "ok" for r in self.jobs)

    # ── the report ──
    def report_lines(self, rc: int) -> "list[str]":
        r = self.rec
        L = [f"# Night window {self.started_iso[:10]} ({self.mode})", "",
             f"**Outcome: {self.outcome}** · exit {rc} · started {self.started_iso[11:]} · {self.elapsed_min():.1f} min of a {self.cap_min:.0f}-minute cap · restore: {getattr(self, 'restore_status', 'not needed')}", ""]
        a = r.get("arith") or {}
        if a.get("chosen"):
            L += ["## Levers (chosen by arithmetic on the MEASURED MemAvailable after the stops)", "",
                  f"- chosen: **{a['chosen']}**; MemAvailable measured {a.get('avail_measured_mib')} MiB; after the 12B loaded {a.get('avail_after_load_mib')} MiB (floor {a.get('floor_mib'):.0f}); served ctx {a.get('served_ctx')}"]
            L += ["- " + (f"{x['levers']}: need {x['need_jobs']} (+{x['need_jobs'] - x['need_load']} for the jobs), margin {x['margin_jobs']:+d}, {'fits' if x['fits'] else 'NO'}") for x in a["rows"][:8]]
            L.append("")
        elif a:
            L += ["## Levers", "", f"- nothing fit at {a.get('avail_measured_mib')} MiB; see the table in the log", ""]
        if r.get("speed"):
            L += ["## Speed (one fixed ~1.5k-token probe)", ""] + [f"- {k}: prefill {v.get('prefill_tps')} tok/s, decode {v.get('decode_tps')} tok/s" for k, v in r["speed"].items()] + [""]
        if self.jobs:
            L += ["## Jobs", "", "| job | on | status | wall | prompt tok | generated tok | MemAvailable low | note |", "|---|---|---|---|---|---|---|---|"]
            for j in self.jobs:
                L.append(f"| {j['name']} | {j['backend']} | {j['status']} | {j['wall_s']} s | {j['tokens_prompt']} | {j['tokens_predicted']} | {j['mem_low_mib']} MiB | {'; '.join(j['notes'])[:160]} |")
            L.append("")
        if r.get("trial"):
            L += ["## Trial: K cells, 12B vs the 4B at 32k (ZMA reflection pass, bench household `zmb-v1`)", "", "| cell | 12B | 4B@32k |", "|---|---|---|"]
            t12, t4 = r["trial"].get("12B") or {}, r["trial"].get("4B@32k") or {}
            ids = [c["id"] for c in (t12.get("cells") or [])] + [c["id"] for c in (t4.get("cells") or []) if c["id"] not in [x["id"] for x in (t12.get("cells") or [])]]
            v = lambda t, i: next((c["verdict"] for c in (t.get("cells") or []) if c["id"] == i), "-")  # noqa: E731
            L += [f"| {i} | {v(t12, i)} | {v(t4, i)} |" for i in ids]
            nm12, nm4 = t12.get("night_mind_cells") or {}, t4.get("night_mind_cells") or {}
            if nm12 or nm4:
                L += ["", "| night-mind cell | 12B | 4B@32k |", "|---|---|---|"] + [f"| {k} | {nm12.get(k, '-')} | {nm4.get(k, '-')} |" for k in sorted(set(nm12) | set(nm4))] + [""]
            for lab, t in (("12B", t12), ("4B@32k", t4)):
                L.append(f"| **{lab}** | cells {t.get('pass')}/{t.get('graded')}, items {t.get('items')}, {t.get('wall_total_s')} s, {t.get('tokens_prompt')} prompt / {t.get('tokens_predicted')} generated tokens, MemAvailable low {t.get('mem_low_mib')} MiB, model calls {t.get('model_calls')}"
                         + (f", ERROR {t['error']}" if t.get("error") else "") + " | |")
            L.append("")
        if r.get("restore"):
            L += ["## Wake", "", "| unit | healthy | attempts | down |", "|---|---|---|---|"] + [f"| {u} | {i['ok']} | {i['attempts']} | {i.get('down_s')} s |" for u, i in r["restore"].items()] + [""]
        if r.get("notes"):
            L += ["## Notes", ""] + [f"- {n}" for n in r["notes"]] + [""]
        L += ["## Timeline (last 30 events)", ""] + [f"- +{e['t_min']} min: {e['msg']}" for e in r["events"][-30:]]
        return L

    def write_report(self, rc: int) -> None:
        if self.dry:
            return
        try:
            self.cfg.report_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.cfg.report_dir, 0o700)
            base = self.cfg.report_dir / (self.started_iso[:10] + ("-trial" if self.cfg.trial else ""))
            if base.with_suffix(".md").exists():
                base = Path(str(base) + "-" + self.started_iso[11:16].replace(":", ""))
            md, js = Path(str(base) + ".md"), Path(str(base) + ".json")
            md.write_text("\n".join(self.report_lines(rc)) + "\n")
            js.write_text(json.dumps({**self.rec, "outcome": self.outcome, "exit": rc, "jobs": self.jobs, "elapsed_min": round(self.elapsed_min(), 1), "cap_min": self.cap_min,
                                      "restore_status": getattr(self, "restore_status", ""), "started": self.started_iso}, indent=1, default=str))
            for p in (md, js):
                os.chmod(p, 0o600)
            self.report_paths = [md, js]
            self.log(f"report: {md} and {js.name}")
        except OSError as exc:
            self.log(f"could not write the report: {exc}")

    # ── the dry run ──
    def dry_plan(self) -> None:
        cfg = self.cfg
        avail = self.mem()
        pss = {u: self.unit_held_mib(u) for u in STOP_ORDER}
        rest, top = self.other_consumers(pss, avail)
        self.log("")
        self.log(f"THE ARITHMETIC, TODAY ({dt.datetime.fromtimestamp(self.host.now()):%Y-%m-%d %H:%M}), in MiB")
        self.log(f"  MemAvailable now {avail:.0f}; held by the units a window stops (cgroup memory.current): " + ", ".join(f"{u.removesuffix('.service')} {v:.0f}" for u, v in pss.items()))
        self.log(f"  everything ELSE holds about {rest:.0f} MiB right now (kernel, pinned pages, agents, tests); largest resident programs, name and MiB: " + ", ".join(top))
        self.log(f"  predicted MemAvailable after the stops = now + {FREED_FACTOR:.1f} x what the stopped units hold (the real run MEASURES it after the stops and chooses on that); "
                 f"floor once loaded {cfg.min_avail_mb:.0f}; night jobs beside the 12B {cfg.job_reserve_mib:.0f}; KV = (8192 x ctx + 189e6) x bytes/elem; file sizes from disk")
        sets = [("stop all (default)", tuple(u for u in STOP_ORDER if u in self.stop_set or u == ZOE_DATA)),
                ("keep zoe-data", tuple(u for u in STOP_ORDER if u != ZOE_DATA)),
                ("keep zoe-data + router", tuple(u for u in STOP_ORDER if u not in (ZOE_DATA, ROUTER)))]
        default_pick = None
        for name, stop in sets:
            pred = predict_avail(avail, pss, stop)
            rows = evaluate(self.sizes, pred, cfg.min_avail_mb, cfg.job_reserve_mib, cfg.margin_mib)
            pick = choose_levers(rows, model=cfg.model_choice, ctx=cfg.ctx_choice, kv=cfg.kv_choice)
            self.log("")
            self.log(f"  stop set: {name} -> {', '.join(u.removesuffix('.service') for u in stop)}; predicted MemAvailable {pred:.0f}")
            for line in self.table_lines(rows, pick["levers"] if pick else None):
                self.log("    " + line)
            if name.startswith("stop all"):
                default_pick = pick
        self.log("")
        if default_pick:
            lv = default_pick["levers"]
            self.log(f"FITS TODAY (predicted, default stop set): {lv.label()}, margin {default_pick['margin_jobs']:+.0f} MiB with the jobs beside it")
            try:
                spec = llm_spec(self.parked_text, cfg, lv)
                self.log("the 12B command, generated from the parked unit (never edited): " + shlex.join(spec["argv"]))
                self.log("  differs from the parked ExecStart in: " + "; ".join(spec["diff"]))
                self.log("  transient unit properties: " + ", ".join(f"{k}={v}" for k, v in spec["props"].items()))
            except Refused as exc:
                self.log(f"could not generate the 12B command: {exc}")
        else:
            self.log("DOES NOT FIT TODAY (predicted, default stop set): no lever set clears the floor with the jobs beside it; a real run would stop, measure, refuse and restore")
        self.log("")
        self.log(f"PLAN: cap {self.cap_min:.0f} min, must end by {cfg.end_by // 60:02d}:{cfg.end_by % 60:02d}; sleep {' > '.join(self.stop_set)}; wake {' > '.join(u for u in RESTORE_ORDER if u in self.stop_set)}")
        for u in RESTORE_ORDER:
            if u in self.stop_set:
                self.log(f"  wake {u}: poll {HEALTH[u].url} up to {HEALTH[u].wait_s:.0f}s ({HEALTH[u].what}); one retry; then an ALARM file and exit {EXIT_RESTORE_FAILED}")
        if cfg.trial:
            self.log("  trial: 12B K1-K5 (ZMA reflection pass, shim on :%d), unload, 4B at --ctx-size 32768, same cells, restore" % cfg.shim_port)
        else:
            for job in build_jobs(cfg, default_pick["levers"].ctx if default_pick else 16384):
                self.log(f"  job {job.name}: " + (shlex.join(job.argv) if job.argv else f"SKIPPED ({job.skip_reason})") + f" [timeout {job.timeout_s:.0f}s; 4B fallback {'yes' if job.fallback_4b and cfg.fallback_4b else 'no'}]")
            self.log("  every job gets GEMMA_SERVER_URL=http://127.0.0.1:%d/v1, ZOE_DIGEST_LLM_TIMEOUT_SCALE=<from the measured decode speed>, MALLOC_ARENA_MAX=2 (nothing else is added; the environment is never printed)" % cfg.port)
        self.log("DRY-RUN: nothing was changed." + (f" A real run started now would be REFUSED ({len(self.dry_refusals)} reason(s) above)." if self.dry_refusals else ""))


# ── logging and the CLI ──────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the arithmetic for every lever combination, which fits today, the generated 12B command and the plan; change nothing")
    ap.add_argument("--trial", action="store_true", help="load the 12B only; score K1-K5 on it and on the 4B at 32k; restore (manual, any hour)")
    ap.add_argument("--restore-only", action="store_true", help="put back whatever the marker file says a window stopped (idempotent)")
    ap.add_argument("--anytime", action="store_true", help="allow a start outside 01:30-03:40 (manual runs)")
    ap.add_argument("--keep-zoe-data", action="store_true", help="do NOT stop zoe-data (needs ~2 GB of other headroom; refused if the window would cross 03:00 / the Sunday 04:00 loop)")
    ap.add_argument("--model", choices=("auto", "qat", "q4km"), default="auto", help="auto = the QAT q4_0 file (smaller and trained for q4_0)")
    ap.add_argument("--ctx", type=int, choices=CTX_OPTIONS, default=None, help="pin --ctx-size (default: the largest that fits)")
    ap.add_argument("--kv", choices=KV_OPTIONS, default=None, help="pin the KV cache type (default: q8_0 if it fits)")
    ap.add_argument("--cap-min", type=float, default=None, help="hard cap in minutes (default 65; never past --end-by)")
    ap.add_argument("--end-by", default=None, help="HH:MM local; the window must be over (zoe-data back) by then (default 03:55)")
    ap.add_argument("--jobs", default=None, help="comma list, in order (default digest,dreaming,night_mind)")
    ap.add_argument("--night-mind-cmd", default=None, help="the night-mind pass command line; {model_url} / {model_url_v1} are the 12B's base URL")
    ap.add_argument("--no-fallback", action="store_true", help="do not run a job on the live 4B after the restore when it did not finish on the 12B")
    ap.add_argument("--binary", default=None, help="llama-server binary other than the parked unit's (flags are translated for the b11194 build)")
    ap.add_argument("--fit-off", action="store_true", help="add --fit off to the 12B command (not the default: see NightCfg.fit_off)")
    ap.add_argument("--no-mlock", action="store_true", help="drop --mlock from the 12B command (the locked mmap of the file plus the CUDA copy is about 2x the file at load)")
    ap.add_argument("--ngl", type=int, default=None, help="--n-gpu-layers for the 12B (default: the parked unit's 99); fewer layers = a smaller single CUDA allocation, the rest stays in RAM")
    ap.add_argument("--no-unified", action="store_true", help="do NOT set GGML_CUDA_ENABLE_UNIFIED_MEMORY=1 for the 12B (default ON on a Jetson: cudaMallocManaged avoids the per-allocation limit)")
    ap.add_argument("--skip-4b", action="store_true", help="trial only: do not run the 4B@32k phase (exploratory attempts)")
    ap.add_argument("--retry-load", action="store_true", help="ignore the 'the 12B failed to load on the last N windows' guard")
    ap.add_argument("--job-reserve-mib", type=float, default=None, help="resident size budgeted for the job processes beside the 12B (default 700)")
    ap.add_argument("--margin-mib", type=float, default=None, help="extra margin demanded by the lever choice (default 0)")
    ap.add_argument("--report-dir", type=Path, default=None)
    ap.add_argument("--night-dir", type=Path, default=None)
    return ap


def configure(args: argparse.Namespace, cfg: "Optional[NightCfg]" = None) -> NightCfg:
    cfg = cfg or NightCfg()
    cfg.trial = bool(args.trial)
    cfg.night_only = not (args.anytime or args.trial)
    cfg.stop_zoe_data = not args.keep_zoe_data
    cfg.model_choice, cfg.ctx_choice, cfg.kv_choice = args.model, args.ctx, args.kv
    if args.cap_min:
        cfg.cap_min = args.cap_min
    elif cfg.trial:
        cfg.cap_min = min(cfg.cap_min, 40.0)
    if args.end_by:
        cfg.end_by = _hhmm(args.end_by)
    if cfg.end_by > cfg.blackouts[0][0]:
        raise Refused(f"--end-by {cfg.end_by // 60:02d}:{cfg.end_by % 60:02d} is inside the voice gate ({cfg.blackouts[0][0] // 60:02d}:{cfg.blackouts[0][0] % 60:02d}-)")
    if args.jobs is not None:
        cfg.jobs = tuple(j.strip() for j in args.jobs.split(",") if j.strip())
    if cfg.trial:
        cfg.jobs = ()
    if args.night_mind_cmd is not None:
        cfg.night_mind_cmd = args.night_mind_cmd
    cfg.fallback_4b = not args.no_fallback and not cfg.trial
    cfg.binary = args.binary or cfg.binary
    cfg.fit_off = cfg.fit_off or args.fit_off
    cfg.mlock = cfg.mlock and not args.no_mlock
    cfg.ngl = args.ngl if args.ngl is not None else cfg.ngl
    cfg.retry_load = cfg.retry_load or args.retry_load
    cfg.unified = cfg.unified and not args.no_unified
    cfg.skip_4b = args.skip_4b
    if args.job_reserve_mib is not None:
        cfg.job_reserve_mib = args.job_reserve_mib
    if args.margin_mib is not None:
        cfg.margin_mib = args.margin_mib
    if args.report_dir:
        cfg.report_dir = args.report_dir
    if args.night_dir:
        cfg.night_dir = args.night_dir
    unknown = [j for j in cfg.jobs if j not in ("digest", "dreaming", "night_mind")]
    if unknown:
        raise Refused(f"unknown job(s) {', '.join(unknown)} (digest, dreaming, night_mind)")
    return cfg


def main(argv: "Optional[list[str]]" = None, host_factory: "Optional[Callable[[Callable], bk.Host]]" = None, cfg: "Optional[NightCfg]" = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = configure(args, cfg)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    mode = "restore" if args.restore_only else "trial" if cfg.trial else "night"
    if not args.dry_run:
        cfg.night_dir.mkdir(parents=True, exist_ok=True)
    log = bk.Logger(None if args.dry_run else cfg.night_dir / f"{'restore' if args.restore_only else 'run'}-{stamp}.log")
    make_host = host_factory or (lambda lg: NightDryHost(lg) if args.dry_run else NightHost(lg))
    host = make_host(log)
    w = NightWindow(cfg, host, log, dry=args.dry_run, run_id=stamp, mode=mode)
    if args.restore_only:
        if not args.dry_run:
            try:
                w.take_lock()                  # BEFORE the marker is read, and held through the restore: two recoveries (or a recovery and a new window) must never overlap
            except Refused as exc:
                log(f"restore-only: {exc}; a window (or another recovery) holds the brain lock and owns the wake-up, so this one does nothing")
                return EXIT_REFUSED
        try:
            return restore_only(w, log)
        finally:
            w.release_lock()
    log(("DRY-RUN " if args.dry_run else "") + f"night window {stamp} ({mode}): cap {cfg.cap_min:.0f} min, end by {cfg.end_by // 60:02d}:{cfg.end_by % 60:02d}, "
        f"jobs {','.join(cfg.jobs) or '-'}, zoe-data {'stopped' if cfg.stop_zoe_data else 'kept up'}")
    return w.run()


def restore_only(w: NightWindow, log: "Callable[[str], None]") -> int:
    if not w.load_marker():
        log("restore-only: no marker file, so this tool has stopped nothing; stopping any leftover night units only")
    elif w.marker_owner_alive:
        log("restore-only: the window that wrote the marker is still running and will wake everything itself; doing nothing")
        return EXIT_OK
    return EXIT_OK if w.restore() else EXIT_RESTORE_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
