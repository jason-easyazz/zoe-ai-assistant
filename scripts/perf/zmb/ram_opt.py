#!/usr/bin/env python3
"""RAM / latency optimisation lab for the bake-off's Hindsight stack (2026-10-06). Measures ONE configuration per run, in the scratch lab only.

What a run does (every process it starts is stopped on every exit path):

    scratch Postgres (its own container ``zoe-ropt-pg`` :55433, its own data directory)  ->  embeddings shim (unit ``zoe-ropt-embed`` :11511)
    ->  hindsight-api (unit ``zoe-ropt-hindsight`` :18889, the window's environment + this config's overrides, egress audit hook ON)
    ->  workload: N retains (verbatim = through the LIVE brain's LLM under the brain lock and the panel-quiet check; ``chunks`` = no LLM, the
        screening mode), 200 recalls (warm-up), 50 timed recalls (p50/p95 + hit@5 on the 20 needles), a concurrent recall storm (the burst).

The number it reports is the window's own: PSS summed over the hindsight and shim units' cgroups + ``docker stats`` of the Postgres container
(``bakeoff_measure.Sampler``), median = steady, max = burst. A watchdog stops everything when MemAvailable drops under the floor (1500 MB).
It never touches the live services, the live store, the live Postgres or the brain except for the LLM calls of a verbatim retain.

    python scripts/perf/zmb/ram_opt.py --config base --mode verbatim --retains 200
    python scripts/perf/zmb/ram_opt.py --list
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import json
import os
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import needles  # noqa: E402
from zmb.arms.hindsight import BANK_CONFIG, HindsightClient, HindsightError  # noqa: E402
from zmb.bakeoff import EGRESS_AUDIT_DIR, Cfg, Host, Window, hindsight_env, systemd_run  # noqa: E402
from zmb.bakeoff_measure import parse_mib, parse_pss_kb  # noqa: E402

BAKE = Path(os.environ.get("BAKEOFF_DIR", "/home/zoe/.zoe/bakeoff-2026-10"))
LAB = BAKE / "ram-opt"
PG_NAME, PG_PORT, SHIM_PORT, HS_PORT = "zoe-ropt-pg", 55433, 11511, 18889
U_HS, U_SHIM = "zoe-ropt-hindsight.service", "zoe-ropt-embed.service"
PG_IMAGE = "pgvector/pgvector:pg17"
FLOOR_MB = float(os.environ.get("ROPT_FLOOR_MB", "1500"))        # the owner's limit for this task: MemAvailable never under 1.5 GB
SEED = "ropt-v1"
USER = "demo_ropt_00000000"

#: the scratch Postgres exactly as ``scratch-postgres.compose.yml`` ran it for run 1 / 2 (the baseline)
PG_BASE_FLAGS = ("shared_buffers=64MB", "max_connections=40", "work_mem=4MB", "maintenance_work_mem=32MB", "effective_cache_size=128MB")


@dataclasses.dataclass(frozen=True)
class Config:
    """One configuration of the stack. Everything not named here is the window's default (the baseline)."""
    name: str
    note: str = ""
    hs_env: "tuple[tuple[str, str], ...]" = ()                 # extra / overriding Hindsight environment
    hs_props: "tuple[tuple[str, str], ...]" = (("MemoryMax", "1536M"), ("MemorySwapMax", "0"))
    hs_argv: "tuple[str, ...]" = ()                            # extra hindsight-api arguments
    shim_env: "tuple[tuple[str, str], ...]" = ()
    shim_args: "tuple[str, ...]" = ()
    shim_kind: str = "real"                                    # "real" = embed_shim.py (bge-small), "stub" = stub_embed.py (footprint runs only)
    shim_props: "tuple[tuple[str, str], ...]" = (("MemoryMax", "300M"), ("MemorySwapMax", "0"))
    pg_flags: "tuple[str, ...]" = PG_BASE_FLAGS
    pg_mem: str = "256m"
    pg_shm: str = "64m"
    pythonpath_extra: str = ""                                  # prepended to the server's PYTHONPATH (a sitecustomize that trims imports)


def pg_command(cfg: Config, data_dir: Path) -> "list[str]":
    """The ``docker run`` that starts the scratch Postgres for ``cfg`` (loopback port only, memory + swap capped)."""
    cmd = ["docker", "run", "-d", "--name", PG_NAME, "--memory", cfg.pg_mem, "--memory-swap", cfg.pg_mem, "--shm-size", cfg.pg_shm,
           "-p", f"127.0.0.1:{PG_PORT}:5432", "-v", f"{data_dir}:/var/lib/postgresql/data",
           "-e", "POSTGRES_USER=hindsight", "-e", "POSTGRES_PASSWORD=hindsight", "-e", "POSTGRES_DB=hindsight", PG_IMAGE, "postgres"]
    for f in cfg.pg_flags:
        cmd += ["-c", f]
    return cmd


def percentile(xs: "list[float]", q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def hit_at_k(rows: "list[dict]", answer: str, k: int = 5) -> bool:
    """Is the needle's answer token in the text of the first ``k`` results? (The answer exists nowhere else: a pure string match.)"""
    return any(answer.lower() in str(r.get("text") or "").lower() for r in rows[:k])


def summarise(samples: "list[dict]", phases: "tuple[str, ...]") -> "dict[str, Any]":
    """steady = median, burst = max over the samples whose label starts with one of ``phases`` (the window's rule)."""
    xs = [s["rss_mb"] for s in samples if s["label"].startswith(phases) and s.get("pids")]
    return {"steady_mb": round(statistics.median(xs), 1) if xs else None, "burst_mb": round(max(xs), 1) if xs else None, "samples": len(xs)}


def parts_median(samples: "list[dict]", phases: "tuple[str, ...]") -> "dict[str, float]":
    out: "dict[str, list[float]]" = {}
    for s in samples:
        if s["label"].startswith(phases) and s.get("pids"):
            for k in ("hs_mb", "hs_anon_mb", "hs_file_mb", "shim_mb", "pg_mb", "pg_anon_mb", "pg_file_mb"):
                out.setdefault(k, []).append(s[k])
    return {k: round(statistics.median(v), 1) for k, v in out.items()}


def workload_items(n: int) -> "list[dict]":
    """``n`` retain items: the 20 needles (taught first, the way the owner teaches them) then stored filler (preferences and near-miss facts)."""
    nd = needles.corpus(SEED)
    items = [{"content": x.fact, "kind": "needle"} for x in nd]
    filler = [c for c in needles.chatter(SEED, 900) if c["kind"] != "chatter"]
    items += [{"content": c["text"], "kind": c["kind"]} for c in filler]
    return items[:n]


def queries(n_extra: int = 160) -> "list[tuple[str, Optional[str]]]":
    """(query, answer-or-None): the 40 needle questions (direct + paraphrase) first, then near-miss subject questions."""
    nd = needles.corpus(SEED)
    qs: "list[tuple[str, Optional[str]]]" = [(x.direct, x.answer) for x in nd] + [(x.paraphrase, x.answer) for x in nd]
    for name in needles.distractor_names(SEED)[:n_extra]:
        qs.append((f"where does {name} live", None))
    return qs


class Lab:
    """Starts and stops the scratch stack for one configuration. Nothing here outlives ``stop()``."""

    def __init__(self, cfg: Config, run_id: str, log: "Callable[[str], None]"):
        self.c, self.run_id, self.log = cfg, run_id, log
        self.host = Host(log)
        self.data_dir = LAB / f"pgdata-{run_id}"
        bcfg = Cfg(hs_port=HS_PORT, shim_port=SHIM_PORT, pg_port=PG_PORT, skip_brain_stop=True)
        for knob in ("lean", "hm_shared_embedder"):         # the window's own optimisations: the lab sets every one of them explicitly per Config, so a baseline stays a baseline
            if hasattr(bcfg, knob):
                setattr(bcfg, knob, False)
        self.bcfg = bcfg
        self.win = Window(bcfg, self.host, log, run_id=run_id)
        self.started: "list[str]" = []

    def env_text(self) -> str:
        example = (BAKE / "hindsight.env.example").read_text()
        text = hindsight_env(example, self.bcfg, self.run_id)
        extra = dict(self.c.hs_env)
        lines, seen = [], set()
        for line in text.splitlines():
            k = line.split("=", 1)[0].strip()
            if k in extra and not line.lstrip().startswith("#"):
                lines.append(f"{k}={extra[k]}")
                seen.add(k)
            else:
                lines.append(line)
        lines += [f"{k}={v}" for k, v in extra.items() if k not in seen]
        if self.c.pythonpath_extra:
            lines = [f"PYTHONPATH={self.c.pythonpath_extra}:{EGRESS_AUDIT_DIR}" if ln.startswith("PYTHONPATH=") else ln for ln in lines]
        return "\n".join(lines) + "\n"

    def run(self, argv: "list[str]", timeout: float = 120.0) -> Any:
        return self.host.run(argv, timeout=timeout)

    def start(self, parts: "tuple[str, ...]" = ("pg", "shim", "hs")) -> None:
        LAB.mkdir(parents=True, exist_ok=True)
        for u in (U_HS, U_SHIM):                    # leftovers of a killed run (this lab's own unit names; one lab run at a time, see lab_lock)
            self.run(["systemctl", "--user", "stop", u], timeout=60)
            self.run(["systemctl", "--user", "reset-failed", u], timeout=20)
        if "pg" in parts:
            self.start_pg()
        if "shim" in parts:
            self.start_shim()
        if "hs" in parts:
            self.start_hs()

    def start_pg(self) -> None:
        self.run(["docker", "rm", "-f", PG_NAME], timeout=60)          # only this lab's own container name: a leftover of a killed run must not block the next one
        self.data_dir.mkdir(parents=True, exist_ok=True)
        r = self.run(pg_command(self.c, self.data_dir))
        if r.rc != 0:
            raise RuntimeError("postgres did not start: " + r.out[-300:])
        self.started.append("pg")
        for _ in range(40):
            if self.run(["docker", "exec", PG_NAME, "pg_isready", "-U", "hindsight"]).rc == 0:
                break
            time.sleep(1.5)
        else:
            raise RuntimeError("postgres never became ready")

    def start_shim(self) -> None:
        shim_env ={"HF_HUB_OFFLINE": "1", "ORT_DISABLE_TELEMETRY": "1", **dict(self.c.shim_env)}
        if self.c.shim_kind == "stub":
            argv = ["/usr/bin/python3", str(REPO / "scripts/perf/zmb/stub_embed.py"), "--serve", "--port", str(SHIM_PORT)]
        else:
            argv = [str(self.bcfg.hs_python), str(REPO / "scripts/perf/zmb/embed_shim.py"), "--serve", "--port", str(SHIM_PORT), *self.c.shim_args]
        r = self.run(systemd_run(U_SHIM, argv, env=shim_env, props=dict(self.c.shim_props)))
        if r.rc != 0:
            raise RuntimeError("shim did not start: " + r.out[-300:])
        self.started.append("shim")
        self.wait(f"http://127.0.0.1:{SHIM_PORT}/health", 60)

    def start_hs(self) -> None:
        env_path =LAB / f"hindsight-{self.run_id}.env"
        env_path.write_text(self.env_text())
        props = {"EnvironmentFile": str(env_path), **dict(self.c.hs_props)}
        r = self.run(systemd_run(U_HS, [str(self.bcfg.hs_bin), *self.c.hs_argv], env={"HOME": str(BAKE / "hs-home")}, props=props))
        if r.rc != 0:
            raise RuntimeError("hindsight did not start: " + r.out[-300:])
        self.started.append("hs")
        self.wait(f"http://127.0.0.1:{HS_PORT}/health", 240)

    def wait(self, url: str, timeout_s: float) -> None:
        t_end = time.monotonic() + timeout_s
        while time.monotonic() < t_end:
            if self.run(["curl", "-sf", "-m", "3", url], timeout=10).rc == 0:
                return
            time.sleep(1.5)
        raise RuntimeError(f"{url} not healthy after {timeout_s:.0f}s")

    def stop(self) -> None:
        for u in (U_HS, U_SHIM):
            self.run(["systemctl", "--user", "stop", u], timeout=60)
            self.run(["systemctl", "--user", "reset-failed", u], timeout=20)
        self.run(["docker", "rm", "-f", PG_NAME], timeout=60)
        # the data directory belongs to the container's postgres user: remove it with the same image, never with sudo
        if self.data_dir.exists():                 # mount the PARENT and remove the directory itself: an empty directory owned by the container user would otherwise stay behind
            self.run(["docker", "run", "--rm", "--entrypoint", "sh", "-v", f"{self.data_dir.parent}:/l", PG_IMAGE, "-c", f"rm -rf /l/{self.data_dir.name}"], timeout=60)
            shutil.rmtree(self.data_dir, ignore_errors=True)
        self.started.clear()

    def unit_pids(self, unit: str) -> "list[int]":
        cg = self.host.run(["systemctl", "--user", "show", "-p", "ControlGroup", "--value", unit], mutating=False).out.strip()
        procs = self.host.read(f"/sys/fs/cgroup{cg}/cgroup.procs") if cg else ""
        return [int(x) for x in procs.split() if x.isdigit()]


def pss_split_kb(smaps_rollup: str) -> "tuple[float, float]":
    """(anonymous, file-backed) PSS in kB from a ``smaps_rollup`` text: heap and thread arenas vs mapped libraries / bytecode."""
    a = f = 0.0
    for line in (smaps_rollup or "").splitlines():
        if line.startswith("Pss_Anon:"):
            a = float(line.split()[1])
        elif line.startswith("Pss_File:"):
            f = float(line.split()[1])
    return a, f


def proc_kb(pid: int, key: str) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(key + ":"):
                return float(line.split()[1])
    except OSError:
        pass
    return 0.0


class Sampler(threading.Thread):
    """PSS of the hindsight and shim units, ``docker stats`` of the scratch Postgres, MemAvailable; the watchdog for the memory floor."""
    daemon = True

    def __init__(self, lab: Lab, on_breach: "Callable[[str], None]"):
        super().__init__(name="ropt-sampler")
        self.lab, self.label, self.samples, self.on_breach = lab, "startup", [], on_breach
        self.stop_evt = threading.Event()
        self.pg_mb, self.n, self.floor = 0.0, 0, 1e9
        self.pg_split: "dict[str, float]" = {"anon": 0.0, "file": 0.0, "shmem": 0.0}
        self.pg_scope = ""
        self.hwm: "dict[str, float]" = {}

    def pg_cgroup(self) -> None:
        """The container's cgroup split (v2 ``memory.stat``): anonymous memory (the backends) against page cache (data files, WAL): ``docker stats`` counts both."""
        h = self.lab.host
        if not self.pg_scope:
            cid = h.run(["docker", "inspect", "-f", "{{.Id}}", PG_NAME], mutating=False).out.strip()
            self.pg_scope = f"/sys/fs/cgroup/system.slice/docker-{cid}.scope" if len(cid) == 64 else ""
        if not self.pg_scope:
            return
        for line in h.read(self.pg_scope + "/memory.stat").splitlines():
            k, _, v = line.partition(" ")
            if k in self.pg_split:
                self.pg_split[k] = int(v) / 1048576.0

    def sample(self) -> "dict[str, Any]":
        h = self.lab.host
        hs_pids, shim_pids = self.lab.unit_pids(U_HS), self.lab.unit_pids(U_SHIM)
        hs_rolls = [h.read(f"/proc/{p}/smaps_rollup") for p in hs_pids]
        hs = sum(parse_pss_kb(t) for t in hs_rolls) / 1024.0
        split = [pss_split_kb(t) for t in hs_rolls]
        hs_anon, hs_file = sum(a for a, _ in split) / 1024.0, sum(f for _, f in split) / 1024.0
        shim = sum(parse_pss_kb(h.read(f"/proc/{p}/smaps_rollup")) for p in shim_pids) / 1024.0
        rss = sum(proc_kb(p, "VmRSS") for p in hs_pids + shim_pids) / 1024.0
        for tag, pids in (("hs", hs_pids), ("shim", shim_pids)):
            self.hwm[tag] = max(self.hwm.get(tag, 0.0), sum(proc_kb(p, "VmHWM") for p in pids) / 1024.0)
        if self.n % 3 == 0:
            r = h.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", PG_NAME], timeout=20, mutating=False)
            self.pg_mb = parse_mib(r.out.split("/")[0]) if r.rc == 0 else self.pg_mb
            self.pg_cgroup()
        self.n += 1
        avail = h.mem_available_mb("/proc/meminfo")
        self.floor = min(self.floor, avail)
        return {"t": time.monotonic(), "label": self.label, "mem_available_mb": avail, "hs_mb": round(hs, 1), "hs_anon_mb": round(hs_anon, 1), "hs_file_mb": round(hs_file, 1), "shim_mb": round(shim, 1),
                "pg_mb": round(self.pg_mb, 1), "pg_anon_mb": round(self.pg_split["anon"], 1), "pg_file_mb": round(self.pg_split["file"], 1), "rss_mb": round(hs + shim + self.pg_mb, 1), "rss_sum_mb": round(rss + self.pg_mb, 1),
                "pids": len(hs_pids) + len(shim_pids)}

    def run(self) -> None:
        while not self.stop_evt.is_set():
            try:
                s = self.sample()
                self.samples.append(s)
                if s["mem_available_mb"] < FLOOR_MB:
                    self.on_breach(f"MemAvailable {s['mem_available_mb']:.0f} MB < {FLOOR_MB:.0f} MB floor")
            except Exception:  # noqa: BLE001 - a failed sample must never kill the run
                pass
            self.stop_evt.wait(1.0)


class Brain:
    """The live brain is shared with the household: the LLM calls of a verbatim retain run only under the lock, with the panel quiet."""

    def __init__(self, win: Window, log: "Callable[[str], None]"):
        self.win, self.log = win, log

    def take(self) -> None:
        self.win.take_lock()
        why = self.win.busy_reason()
        if why:
            self.win.release_lock()
            raise RuntimeError("the brain is busy: " + why)

    def check(self) -> None:
        why = self.win.busy_reason()
        if why:
            raise RuntimeError("the brain became busy: " + why)

    def release(self) -> None:
        self.win.release_lock()


def functional_checks(url: str, brain: "Optional[Brain]", mode: str = "verbatim") -> "dict[str, Any]":
    """What the lean configuration must not break: the REAL adapter's H1 flow (ingest through the Zoe gate, recall, forget, stats, bank delete) and H2's consolidation (the in-process
    worker must still run) on the server under test. A handful of retains (the verbatim ones go to the live brain)."""
    from zmb.arms.base import Turn
    from zmb.arms.hindsight import HindsightArm, HindsightClient
    from zmb.arms import hindsight as hs_mod
    out: "dict[str, Any]" = {"mode": mode}
    uid = "demo_bar_0000f001"
    saved = dict(hs_mod.BANK_CONFIG["H1"])
    if mode == "chunks":                                   # no model call: the Zoe gate, the forget, the cascade and the bank delete are exercised; H2's consolidation needs the model
        hs_mod.BANK_CONFIG["H1"]["retain_extraction_mode"] = "chunks"
    arm = HindsightArm("H1", base_url=url)
    try:
        arm.reset(uid)
        rep = arm.ingest([Turn("User's friend Priya lives in Perth.", "owner_taught"), Turn("User's friend Ravi lives in Cork.", "owner_taught"),
                          Turn("My dentist is Dr Okonkwo.", "owner_taught"), Turn("I live in Perth", "owner_taught")])
        out["h1_written"] = rep.written
        out["h1_recall_finds"] = any("Perth" in r["text"] for r in arm.recall("where does Priya live", 5))
        if brain is not None:
            brain.check()
        arm.forget("Priya")
        out["h1_forgot"] = not any("Priya" in r["text"] for r in arm.recall("where does Priya live", 5))
        out["h1_rows"] = len(arm.stats()["rows"])
    finally:
        arm.close()
        hs_mod.BANK_CONFIG["H1"].clear()
        hs_mod.BANK_CONFIG["H1"].update(saved)
    out["h1_bank_deleted"] = not HindsightClient(url).list_banks(f"zmb-h1-{uid}")
    if mode == "chunks":
        out["h2"] = "not run: consolidation needs the model (a verbatim run covers it)"
        out["ok"] = bool(out.get("h1_written") and out.get("h1_recall_finds") and out.get("h1_forgot") and out.get("h1_bank_deleted"))
        return out
    arm2 = HindsightArm("H2", base_url=url, settle_timeout_s=120.0)
    try:
        arm2.reset(uid)
        if brain is not None:
            brain.check()
        arm2.ingest([Turn("My dentist is Dr Okonkwo.", "owner_taught"), Turn("I live in Perth", "owner_taught"), Turn("I moved to Hobart last week", "owner_taught")])
        rows = arm2.stats()["rows"]
        out["h2_rows"] = len(rows)
        out["h2_consolidated"] = any("observation" in str(r.get("memory_type", "")) or "observation" in str(r.get("origin", "")) for r in rows)
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        out["h2_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    finally:
        arm2.close()
    out["ok"] = bool(out.get("h1_written") and out.get("h1_recall_finds") and out.get("h1_forgot") and out.get("h1_bank_deleted") and not out.get("h2_error"))
    return out


def run_workload(lab: Lab, smp: Sampler, mode: str, retains: int, recalls: int, brain: "Optional[Brain]", log: "Callable[[str], None]",
                 functional: bool = False) -> "dict[str, Any]":
    client = HindsightClient(f"http://127.0.0.1:{HS_PORT}")
    out: "dict[str, Any]" = {"mode": mode}
    bank = "zmb-ropt-demo"
    base_cfg = dict(BANK_CONFIG["H1"])
    if mode == "chunks":
        base_cfg["retain_extraction_mode"] = "chunks"
    client.delete_bank(bank)
    client.put_bank(bank)
    client.patch_config(bank, base_cfg)
    smp.label = "idle0"
    time.sleep(6)                                            # the server's own steady state before the first request
    t_start_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    items = workload_items(retains)
    smp.label = "ingest"
    ok = fail = 0
    retain_ms: "list[float]" = []
    for i, it in enumerate(items):
        if mode == "verbatim" and brain is not None and i % 10 == 0:
            brain.check()
        t0 = time.monotonic()
        try:
            client.retain(bank, [{"content": it["content"], "document_id": f"d{i:04d}", "context": "the user is speaking", "tags": [f"user:{USER}"],
                                  "metadata": {"memory_type": it["kind"]}}])
            ok += 1
        except HindsightError as exc:
            fail += 1
            log(f"retain {i} failed: {exc.status} {str(exc)[:120]}")
        retain_ms.append((time.monotonic() - t0) * 1000.0)
    out["retain"] = {"ok": ok, "failed": fail, "p50_ms": round(percentile(retain_ms, 0.5)), "p95_ms": round(percentile(retain_ms, 0.95))}
    if mode == "verbatim":
        try:
            t = client.call("trace", "GET", f"/v1/default/banks/{bank}/llm-requests", params={"operation": "retain", "limit": 500})
            rows = [x for x in (t.get("items") or t.get("requests") or []) if str(x.get("started_at") or "9") >= t_start_iso]
            good = sum(1 for x in rows if str(x.get("status")) == "success")
            out["validity"] = {"trace_rows": len(rows), "success": good, "calls": len(items), "valid": min(ok, good) if len(rows) >= len(items) else ok}
        except (HindsightError, NotImplementedError) as exc:
            out["validity"] = {"error": str(exc)[:100], "valid": ok, "calls": len(items)}
    else:
        out["validity"] = {"valid": ok, "calls": len(items), "note": "chunks mode: no LLM call"}
    units = client.list_units(bank)
    out["units"] = len(units)
    qs = queries()
    smp.label = "warm"
    for i in range(recalls):                                  # 200-recall warm-up (serial, the household's access pattern)
        q = qs[i % len(qs)][0]
        client.recall(bank, q, tags=[f"user:{USER}"])
    smp.label = "latency"
    lat: "list[float]" = []
    hits = {"direct": 0, "paraphrase": 0}
    nd = needles.corpus(SEED)
    timed = [(x.direct, x.answer, "direct") for x in nd] + [(x.paraphrase, x.answer, "paraphrase") for x in nd]
    extra = [(f"where does {n} live", None, "") for n in needles.distractor_names(SEED)[:10]]
    for q, ans, kind in (timed + extra)[:50]:
        t0 = time.monotonic()
        rows = client.recall(bank, q, tags=[f"user:{USER}"])
        lat.append((time.monotonic() - t0) * 1000.0)
        if ans and hit_at_k(rows, ans):
            hits[kind] += 1
    # the 50 above hold all 40 needle questions; the hit counters read them
    out["recall"] = {"n": len(lat), "p50_ms": round(percentile(lat, 0.5), 1), "p95_ms": round(percentile(lat, 0.95), 1), "max_ms": round(max(lat), 1)}
    out["hit_at_5"] = {"direct": f"{hits['direct']}/{len(nd)}", "paraphrase": f"{hits['paraphrase']}/{len(nd)}", "n_needles": len(nd)}
    smp.label = "storm"
    errs: "list[str]" = []

    def worker(k: int) -> None:
        c = HindsightClient(f"http://127.0.0.1:{HS_PORT}")
        for j in range(10):
            try:
                c.recall(bank, qs[(k * 10 + j) % len(qs)][0], tags=[f"user:{USER}"])
            except Exception as exc:  # noqa: BLE001
                errs.append(str(exc)[:80])

    def rworker(k: int) -> None:                       # chunks mode only (no LLM call): writes racing the reads, the case a small connection pool could deadlock on
        c = HindsightClient(f"http://127.0.0.1:{HS_PORT}")
        for j in range(8):
            try:
                c.retain(bank, [{"content": f"Storm note {k}-{j}: the {needles._ADJ[(k + j) % len(needles._ADJ)]} {needles._THING[(k * 3 + j) % len(needles._THING)]} club meets on Thursdays.",
                                 "document_id": f"s{k}-{j}", "context": "the user is speaking", "tags": [f"user:{USER}"]}])
            except Exception as exc:  # noqa: BLE001
                errs.append("retain " + str(exc)[:80])

    ts = [threading.Thread(target=worker, args=(k,)) for k in range(8)]
    n_writers = 4 if mode == "chunks" else 0
    ts += [threading.Thread(target=rworker, args=(k,)) for k in range(n_writers)]
    t0 = time.monotonic()
    [t.start() for t in ts]
    [t.join() for t in ts]
    out["storm"] = {"threads": 8, "calls": 80, "writers": n_writers, "writes": n_writers * 8, "wall_s": round(time.monotonic() - t0, 2), "errors": len(errs)}
    if functional:
        smp.label = "func"
        try:
            out["functional"] = functional_checks(f"http://127.0.0.1:{HS_PORT}", brain, mode)
        except Exception as exc:  # noqa: BLE001 - the workload numbers already measured must survive a failed check
            out["functional"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    smp.label = "post"
    time.sleep(20)
    client.delete_bank(bank)
    return out


@contextlib.contextmanager
def lab_lock():
    """One lab run at a time on this box: two stacks started together would breach the memory floor together. Blocking, FIFO by the kernel's whim."""
    LAB.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LAB / ".lab.lock"), os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def footprint_estimate_mb(cfg: Config) -> float:
    """What the stack is expected to add to the box (MB): the server, the embedder, the Postgres container. The admission check uses it; the watchdog
    is the real guard."""
    return 330.0 + (12.0 if cfg.shim_kind == "stub" else 135.0) + 40.0


def wait_for_room(need_mb: float, max_wait_s: float, log: "Callable[[str], None]", floor_mb: float = FLOOR_MB, hold_s: float = 8.0,
                  read: "Callable[[], float]" = lambda: Host(print).mem_available_mb("/proc/meminfo"),
                  sleep: "Callable[[float], None]" = time.sleep, clock: "Callable[[], float]" = time.monotonic) -> bool:
    """Block until MemAvailable has stayed at or above ``floor + need`` for ``hold_s`` seconds. The box's own MemAvailable swings by 300+ MB
    with nothing of ours running (measured 1,468-2,022 MB in 90 s at idle), so a run that would end under the floor is not started."""
    t_end, since, last_log = clock() + max_wait_s, None, 0.0
    while clock() < t_end:
        m = read()
        if m >= floor_mb + need_mb:
            since = clock() if since is None else since
            if clock() - since >= hold_s:
                return True
        else:
            since = None
        if clock() - last_log > 60:
            log(f"waiting for room: MemAvailable {m:.0f} MB, need {floor_mb + need_mb:.0f} MB")
            last_log = clock()
        sleep(1.0)
    return False


def run_config(cfg: Config, mode: str, retains: int, recalls: int, tag: str = "", log: "Optional[Callable[[str], None]]" = None,
               wait_s: float = 0.0, functional: bool = False) -> "dict[str, Any]":
    run_id = f"ropt-{cfg.name}-{mode}{tag}-{time.strftime('%H%M%S')}"
    LAB.mkdir(parents=True, exist_ok=True)
    logf = open(LAB / f"{run_id}.log", "a")

    def _log(m: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {m}"
        print(line, flush=True)
        if logf:
            logf.write(line + "\n")
            logf.flush()
    log = log or _log
    lab = Lab(cfg, run_id, log)
    result: "dict[str, Any]" = {"config": cfg.name, "note": cfg.note, "mode": mode, "run_id": run_id, "retains": retains, "recalls": recalls,
                                "started": time.strftime("%Y-%m-%d %H:%M:%S"), "floor_mb": FLOOR_MB}
    breach: "list[str]" = []

    def on_breach(msg: str) -> None:
        if not breach:                      # the first breach stops everything at once: never run on under the floor
            breach.append(msg)
            lab.stop()
    smp = Sampler(lab, on_breach)
    brain: "Optional[Brain]" = None
    try:
        need = footprint_estimate_mb(cfg)
        if not wait_for_room(need, wait_s, log):
            raise RuntimeError(f"no room: MemAvailable never held {FLOOR_MB + need:.0f} MB (floor {FLOOR_MB:.0f} + expected footprint {need:.0f}) within {wait_s:.0f}s: not starting")
        result["mem_available_before_mb"] = round(lab.host.mem_available_mb("/proc/meminfo"))
        if mode == "verbatim":
            brain = Brain(lab.win, log)
            brain.take()
        lab.start()
        smp.start()
        time.sleep(8)
        smp.label = "idle0"
        result["workload"] = run_workload(lab, smp, mode, retains, recalls, brain, log, functional)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        log("ERROR " + result["error"])
    finally:
        smp.stop_evt.set()
        if smp.is_alive():                 # the sampler is not started when the stack failed to come up
            smp.join(timeout=5)
        try:
            lab.stop()                       # FIRST, and whatever else fails below: nothing this run started may outlive it
        finally:
            if brain is not None:
                brain.release()
        try:
            samples = smp.samples
            result["breach"] = breach[0] if breach else ""
            result["mem_available_min_mb"] = round(smp.floor) if smp.floor < 1e8 else None
            result["rss"] = {"window_style": summarise(samples, ("ingest", "warm", "latency", "storm")),
                             "steady_after_warmup": summarise(samples, ("latency",)),
                             "idle_start": summarise(samples, ("idle0",)),
                             "post_storm": summarise(samples, ("post",)),
                             "startup_peak_mb": summarise(samples, ("startup", "idle0"))["burst_mb"]}
            result["parts_median_mb"] = parts_median(samples, ("ingest", "warm", "latency", "storm"))
            result["hwm_mb"] = {k: round(v, 1) for k, v in smp.hwm.items()}
            result["sample_count"] = len(samples)
            (LAB / f"{run_id}.samples.json").write_text(json.dumps(samples))
        except Exception as exc:  # noqa: BLE001 - a summary that fails must not hide the run
            result["finalize_error"] = f"{type(exc).__name__}: {exc}"
        result["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (LAB / f"{run_id}.json").write_text(json.dumps(result, indent=1, default=str))
        if logf:
            logf.close()
    return result


def shim_requests(url: str, n_single: int = 300, n_batch: int = 100, n_long: int = 20) -> "dict[str, Any]":
    """The traffic a Hindsight server sends the shim: single short texts (a recall's query), batches of 8 (a retain's chunks) and a few long ones; request latencies."""
    import urllib.request
    nd = needles.corpus(SEED)
    fill = [c["text"] for c in needles.chatter(SEED, 900)]
    texts = [x.fact for x in nd] + [x.paraphrase for x in nd] + fill

    def post(inp: "list[str]") -> float:
        body = json.dumps({"model": "m", "input": inp, "encoding_format": "base64"}).encode()
        t0 = time.monotonic()
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}), timeout=60) as r:
            r.read()
        return (time.monotonic() - t0) * 1000.0

    def stat(xs: "list[float]") -> "dict[str, Any]":
        return {"n": len(xs), "p50_ms": round(percentile(xs, 0.5), 1), "p95_ms": round(percentile(xs, 0.95), 1)}
    single = [post([texts[i % len(texts)]]) for i in range(n_single)]
    batch = [post([texts[(i * 8 + j) % len(texts)] for j in range(8)]) for i in range(n_batch)]
    long_ = [post([" ".join(texts[(i * 20 + j) % len(texts)] for j in range(30))]) for i in range(n_long)]
    return {"single": stat(single), "batch8": stat(batch), "long": stat(long_)}


def run_shim_probe(cfg: Config, tag: str = "", wait_s: float = 0.0) -> "dict[str, Any]":
    """The embeddings shim ALONE under ``cfg`` (its environment, arguments, model): PSS and peak while it serves 420 requests, and the request latencies."""
    run_id = f"ropt-{cfg.name}-shim{tag}-{time.strftime('%H%M%S')}"
    LAB.mkdir(parents=True, exist_ok=True)

    def log(m: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)
    lab = Lab(cfg, run_id, log)
    result: "dict[str, Any]" = {"config": cfg.name, "note": cfg.note, "mode": "shim", "run_id": run_id, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "floor_mb": FLOOR_MB}
    breach: "list[str]" = []

    def on_breach(msg: str) -> None:
        if not breach:
            breach.append(msg)
            lab.stop()
    smp = Sampler(lab, on_breach)
    try:
        if not wait_for_room(160.0, wait_s, log):
            raise RuntimeError("no room: MemAvailable never held the floor plus the shim's footprint")
        lab.start(("shim",))
        smp.start()
        smp.label = "idle0"
        time.sleep(5)
        smp.label = "ingest"
        result["requests"] = shim_requests(f"http://127.0.0.1:{SHIM_PORT}/v1/embeddings")
        smp.label = "post"
        time.sleep(5)
        health = json.loads(lab.host.run(["curl", "-s", "-m", "3", f"http://127.0.0.1:{SHIM_PORT}/health"], mutating=False).out or "{}")
        result["model"] = {k: health.get(k) for k in ("model", "dim")}
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        log("ERROR " + result["error"])
    finally:
        smp.stop_evt.set()
        if smp.is_alive():                 # the sampler is not started when the stack failed to come up
            smp.join(timeout=5)
        result["breach"] = breach[0] if breach else ""
        result["mem_available_min_mb"] = round(smp.floor) if smp.floor < 1e8 else None
        result["shim_pss_mb"] = {"idle": parts_median(smp.samples, ("idle0",)).get("shim_mb"), "serving": parts_median(smp.samples, ("ingest",)).get("shim_mb"),
                                 "post": parts_median(smp.samples, ("post",)).get("shim_mb"),
                                 "max": max([s["shim_mb"] for s in smp.samples if s.get("pids")] or [None])}
        result["hwm_mb"] = {k: round(v, 1) for k, v in smp.hwm.items()}
        lab.stop()
        result["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (LAB / f"{run_id}.json").write_text(json.dumps(result, indent=1))
    return result


MP_RUN = BAKE / "mp_run.sh"
HM_PROBE = REPO / "scripts" / "perf" / "zmb" / "pilot" / "hm_ram_probe.py"


def run_hm_probe(cfg: Config, shared: bool, with_hindsight: bool, tag: str = "", wait_s: float = 0.0, turns: int = 200, queries: int = 200,
                 cells: bool = False) -> "dict[str, Any]":
    """The HM driver (the real MemPalace library behind the real adapter) next to the shim (and, with ``with_hindsight``, the whole Hindsight stack): its own PSS and latencies, in two
    shapes: ``shared=False`` = the library loads its own MiniLM ONNX session (the bench's default), ``shared=True`` = it asks the shim (ZMB_HM_EMBEDDER_URL). The probe runs under the
    scrubbed environment of ``mp_run.sh`` with the scrubbing allocator the HM forget cell needs, exactly like ``hm_window.py``; the floor watchdog kills it too."""
    run_id = f"ropt-{cfg.name}-hm{'cells' if cells else ''}{'shared' if shared else 'local'}{'2t' if with_hindsight else ''}{tag}-{time.strftime('%H%M%S')}"
    LAB.mkdir(parents=True, exist_ok=True)

    def log(m: str) -> None:
        print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)
    lab = Lab(cfg, run_id, log)
    result: "dict[str, Any]" = {"config": cfg.name, "note": cfg.note, "mode": "hm-" + ("cells-" if cells else "") + ("shared" if shared else "local") + ("-2tier" if with_hindsight else ""), "run_id": run_id,
                                "started": time.strftime("%Y-%m-%d %H:%M:%S"), "floor_mb": FLOOR_MB}
    breach: "list[str]" = []
    proc: "list[subprocess.Popen]" = []

    def on_breach(msg: str) -> None:
        if not breach:
            breach.append(msg)
            for pr in proc:
                with contextlib.suppress(Exception):
                    pr.kill()
            lab.stop()
    smp = Sampler(lab, on_breach)
    try:
        need = (footprint_estimate_mb(cfg) if with_hindsight else 150.0 if shared else 0.0) + (300.0 if not shared else 150.0)      # + the driver (measured 256 MB PSS peak with its own session)
        if not wait_for_room(need, wait_s, log):
            raise RuntimeError(f"no room: MemAvailable never held {FLOOR_MB + need:.0f} MB within {wait_s:.0f}s")
        lab.start(("pg", "shim", "hs") if with_hindsight else ("shim",) if shared else ())          # the local-session shape needs no server at all
        smp.start()
        smp.label = "idle0"
        time.sleep(5)
        smp.label = "hm"
        env = {**os.environ, "MALLOC_PERTURB_": "85", "PYTHONMALLOC": "malloc", "ORT_DISABLE_TELEMETRY": "1"}
        if shared:
            env["ZMB_HM_EMBEDDER_URL"] = f"http://127.0.0.1:{SHIM_PORT}"
        if cells:
            argv = ["bash", str(MP_RUN), str(REPO / "scripts/perf/zmb/hm_cells.py"), "--store", "library"]
        else:
            argv = ["bash", str(MP_RUN), str(HM_PROBE), "--turns", str(turns), "--queries", str(queries)]
            if with_hindsight:
                argv += ["--url", f"http://127.0.0.1:{HS_PORT}"]
        pr = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(REPO))
        proc.append(pr)
        peak = 0.0
        timeline: "list[tuple[float, float]]" = []

        def watch_child() -> None:                       # the driver's own PSS peak (the python child of mp_run.sh's exec) and its timeline (every 2 s)
            nonlocal peak
            t0 = time.monotonic()
            while pr.poll() is None:
                cur = parse_pss_kb(lab.host.read(f"/proc/{pr.pid}/smaps_rollup")) / 1024.0
                peak = max(peak, cur)
                if len(timeline) == 0 or time.monotonic() - t0 - timeline[-1][0] >= 2.0:
                    timeline.append((round(time.monotonic() - t0, 1), round(cur, 1)))
                time.sleep(1.0)
        wt = threading.Thread(target=watch_child, daemon=True)
        wt.start()
        out, err = pr.communicate(timeout=1200)
        wt.join(timeout=3)
        result["driver_pss_peak_mb"] = round(peak, 1)
        result["driver_pss_timeline"] = timeline
        if cells:
            lines = [ln.strip() for ln in out.splitlines() if ln.strip().split(" ")[0] in ("PASS", "FAIL", "SKIP", "graded")]
            result["driver"] = {"rc": pr.returncode, "verdicts": [ln for ln in lines if not ln.startswith("graded")], "summary": next((ln for ln in lines if ln.startswith("graded")), "")}
        else:
            line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("{")), "")
            result["driver"] = json.loads(line) if line else {"error": f"no result (rc={pr.returncode}): {(err or out)[-300:]}"}
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        log("ERROR " + result["error"])
    finally:
        smp.stop_evt.set()
        if smp.is_alive():                 # the sampler is not started when the stack failed to come up
            smp.join(timeout=5)
        result["breach"] = breach[0] if breach else ""
        result["mem_available_min_mb"] = round(smp.floor) if smp.floor < 1e8 else None
        result["servers_mb"] = {"steady": summarise(smp.samples, ("hm",)).get("steady_mb"), "burst": summarise(smp.samples, ("hm",)).get("burst_mb"),
                                "parts": parts_median(smp.samples, ("hm",))}
        lab.stop()
        result["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
        (LAB / f"{run_id}.json").write_text(json.dumps(result, indent=1))
    return result


# ── the configurations ────────────────────────────────────────────────────────

#: Postgres for a 256 MB cap, the minimum that still runs Hindsight's schema (shared_buffers 16 MB, 20 connections, no JIT, no parallel workers)
PG_MIN_FLAGS = ("shared_buffers=16MB", "max_connections=20", "work_mem=2MB", "maintenance_work_mem=16MB", "effective_cache_size=64MB", "jit=off",
                "max_worker_processes=2", "max_parallel_workers=0", "max_parallel_workers_per_gather=0", "autovacuum_max_workers=1", "wal_buffers=1MB",
                "huge_pages=off")
#: the packages a loopback / MCP-off / OpenAI-provider-only server never uses (scripts/perf/zmb/lean_imports/sitecustomize.py)
LEAN_STUBS = "fastmcp,mcp,google.genai,google.oauth2,google.auth,google.api_core,opentelemetry.exporter.otlp"
LEAN_DIR = str(REPO / "scripts" / "perf" / "zmb" / "lean_imports")
STUB = {"shim_kind": "stub"}


def _cfgs() -> "dict[str, Config]":
    c: "dict[str, Config]" = {}

    def add(name: str, note: str, hs: "tuple[tuple[str, str], ...]" = (), **kw: Any) -> None:
        c[name] = Config(name=name, note=note, hs_env=hs, **kw)
    # --- full stack, real shim (the window's own shape) ---
    add("base", "the window's stack exactly as run 2 starts it (real shim)")
    # --- footprint screening: stub embedder (the real shim is measured on its own and added), chunks mode ---
    add("s-base", "screening baseline: stub embedder, window defaults", **STUB)
    add("s-pgmin", "Postgres minimal flags only", pg_flags=PG_MIN_FLAGS, pg_mem="128m", **STUB)
    add("s-arena2", "MALLOC_ARENA_MAX=2 on the server", (("MALLOC_ARENA_MAX", "2"),), **STUB)
    add("s-pymalloc", "PYTHONMALLOC=malloc on the server (the heap-scrub setting)", (("PYTHONMALLOC", "malloc"),), **STUB)
    add("s-migiso", "MIGRATION_ISOLATION=true: alembic, sqlalchemy and psycopg2 stay out of the server", (("HINDSIGHT_API_MIGRATION_ISOLATION", "true"),), **STUB)
    add("s-pool2", "asyncpg pool 1..2 instead of 1..8", (("HINDSIGHT_API_DB_POOL_MIN_SIZE", "1"), ("HINDSIGHT_API_DB_POOL_MAX_SIZE", "2")), **STUB)
    add("s-opt2", "PYTHONOPTIMIZE=2 (no docstrings, no asserts)", (("PYTHONOPTIMIZE", "2"),), **STUB)
    add("s-lean", "lab import trim: MCP, Gemini and the OTLP exporter stubbed", (("ZMB_LEAN_STUBS", LEAN_STUBS),), pythonpath_extra=LEAN_DIR, **STUB)
    add("s-quiet", "loop watchdog and the in-process worker off",
        (("HINDSIGHT_API_LOOP_WATCHDOG_ENABLED", "false"), ("HINDSIGHT_API_WORKER_ENABLED", "false")), **STUB)
    add("s-nodbg", "PYTHONNODEBUGRANGES=1 (no per-instruction position tables in code objects)", (("PYTHONNODEBUGRANGES", "1"),), **STUB)
    add("s-thr1", "OPENBLAS_NUM_THREADS=1 and OMP_NUM_THREADS=1", (("OPENBLAS_NUM_THREADS", "1"), ("OMP_NUM_THREADS", "1")), **STUB)
    add("s-conc", "concurrency caps sized for one household: recall 2, connections per recall 2, embedding requests 2, worker slots 2",
        (("HINDSIGHT_API_RECALL_MAX_CONCURRENT", "2"), ("HINDSIGHT_API_RECALL_CONNECTION_BUDGET", "2"), ("HINDSIGHT_API_EMBEDDINGS_MAX_CONCURRENT_REQUESTS", "2"),
         ("HINDSIGHT_API_ADMISSION_RECALL_MAX_IN_FLIGHT", "4"), ("HINDSIGHT_API_WORKER_MAX_SLOTS", "2")), **STUB)
    add("s-ssl", "SSL_CERT_FILE points at a one-certificate bundle (a loopback-only server never verifies a public CA)",
        (("SSL_CERT_FILE", str(LAB / "one-cert.pem")),), **STUB)
    add("s-pg96", "Postgres container capped at 96 MB (page cache is reclaimed instead of kept)", pg_flags=PG_MIN_FLAGS, pg_mem="96m", **STUB)
    # --- combinations of what helped (server side) ---
    safe = (("HINDSIGHT_API_MIGRATION_ISOLATION", "true"), ("HINDSIGHT_API_DB_POOL_MIN_SIZE", "1"), ("HINDSIGHT_API_DB_POOL_MAX_SIZE", "2"),
            ("HINDSIGHT_API_LOOP_WATCHDOG_ENABLED", "false"), ("HINDSIGHT_API_WORKER_ENABLED", "false"), ("PYTHONOPTIMIZE", "2"), ("PYTHONNODEBUGRANGES", "1"),
            ("OPENBLAS_NUM_THREADS", "1"), ("OMP_NUM_THREADS", "1"), ("HINDSIGHT_API_RECALL_MAX_CONCURRENT", "2"), ("HINDSIGHT_API_RECALL_CONNECTION_BUDGET", "2"),
            ("HINDSIGHT_API_EMBEDDINGS_MAX_CONCURRENT_REQUESTS", "2"), ("HINDSIGHT_API_ADMISSION_RECALL_MAX_IN_FLIGHT", "4"), ("HINDSIGHT_API_WORKER_MAX_SLOTS", "2"))
    tier_b_screen = safe + (("ZMB_LEAN_STUBS", LEAN_STUBS),)
    add("c-safe", "every config-only setting that helped (no import stubs, no Postgres change)", safe, **STUB)
    add("c-lean", "c-safe + the import trim", safe + (("ZMB_LEAN_STUBS", LEAN_STUBS),), pythonpath_extra=LEAN_DIR, **STUB)
    add("c-lean-pg", "c-lean + Postgres minimal flags", safe + (("ZMB_LEAN_STUBS", LEAN_STUBS),), pythonpath_extra=LEAN_DIR, pg_flags=PG_MIN_FLAGS, pg_mem="128m", **STUB)
    # --- the floor: how little memory the server survives in (a systemd MemoryMax with MemorySwapMax=0 on its unit; stub embedder, chunks mode) ---
    for mb in (450, 400, 350, 325, 300, 270, 240):
        add(f"m-{mb}", f"c-lean settings under MemoryMax={mb}M + MemorySwapMax=0", tier_b_screen, hs_props=(("MemoryMax", f"{mb}M"), ("MemorySwapMax", "0")),
            pythonpath_extra=LEAN_DIR, pg_flags=PG_MIN_FLAGS, pg_mem="128m", **STUB)
    # --- the real stack (real shim) with what was measured to help; f-a = configuration only, f-b = + the interpreter / import trim ---
    tier_a = (("HINDSIGHT_API_MIGRATION_ISOLATION", "true"), ("HINDSIGHT_API_DB_POOL_MIN_SIZE", "1"), ("HINDSIGHT_API_DB_POOL_MAX_SIZE", "2"))
    shim_best = (("ZMB_ORT_OPT", "all"), ("MALLOC_ARENA_MAX", "1"))
    tier_b = tier_a + (("PYTHONOPTIMIZE", "2"), ("OPENBLAS_NUM_THREADS", "1"), ("OMP_NUM_THREADS", "1"), ("ZMB_LEAN_STUBS", LEAN_STUBS))
    add("s-fb", "f-b with the stub embedder (the footprint run that fits the memory floor; the shim is added from its own measurement)", tier_b, pythonpath_extra=LEAN_DIR,
        pg_flags=PG_MIN_FLAGS, pg_mem="96m", **STUB)
    add("f-a", "real stack, configuration only: migration isolation, pool 1..2, shim full graph optimisation + one malloc arena, Postgres 96 MB cap",
        tier_a, shim_env=shim_best, pg_flags=PG_MIN_FLAGS, pg_mem="96m")
    add("f-b", "f-a + PYTHONOPTIMIZE=2, one BLAS thread and the import trim", tier_b, shim_env=shim_best, pg_flags=PG_MIN_FLAGS, pg_mem="96m", pythonpath_extra=LEAN_DIR)
    add("f-b-minilm", "f-b with the shim serving MiniLM (zoe-data's live embedder) instead of bge-small", tier_b, shim_env=shim_best, shim_args=("--model", "minilm"),
        pg_flags=PG_MIN_FLAGS, pg_mem="96m", pythonpath_extra=LEAN_DIR)
    # --- the embeddings shim on its own (real model; run with --shim) ---
    add("h-shim-base", "the shim as it is (bge-small int8, 2 threads, basic graph optimisation)")
    add("h-shim-t1", "one ONNX thread", shim_env=(("ZMB_ORT_THREADS", "1"),))
    add("h-shim-opt-all", "full graph optimisation", shim_env=(("ZMB_ORT_OPT", "all"),))
    add("h-shim-opt-off", "no graph optimisation", shim_env=(("ZMB_ORT_OPT", "disable"),))
    add("h-shim-arena1", "MALLOC_ARENA_MAX=1 and a low trim threshold", shim_env=(("MALLOC_ARENA_MAX", "1"), ("MALLOC_TRIM_THRESHOLD_", "65536")))
    add("h-shim-minilm", "serve Chroma's MiniLM (zoe-data's live embedder) instead of bge-small", shim_args=("--model", "minilm"))
    add("h-shim-bge", "serve bge-small explicitly", shim_args=("--model", "bge"))
    add("h-shim-a1", "MALLOC_ARENA_MAX=1 only (no trim threshold)", shim_env=(("MALLOC_ARENA_MAX", "1"),))
    add("h-shim-best", "full graph optimisation + MALLOC_ARENA_MAX=1", shim_env=(("ZMB_ORT_OPT", "all"), ("MALLOC_ARENA_MAX", "1")))
    add("h-shim-best-minilm", "full graph optimisation + MALLOC_ARENA_MAX=1, serving MiniLM", shim_env=(("ZMB_ORT_OPT", "all"), ("MALLOC_ARENA_MAX", "1")),
        shim_args=("--model", "minilm"))
    return c


CONFIGS = _cfgs()


def result_row(res: "dict[str, Any]") -> str:
    """One line per result file: the numbers the report tables are built from."""
    w, r = res.get("workload") or {}, res.get("rss") or {}
    ws, pm = r.get("window_style") or {}, res.get("parts_median_mb") or {}
    hit, rec, ret = w.get("hit_at_5") or {}, w.get("recall") or {}, w.get("retain") or {}
    flag = "BREACH" if res.get("breach") else "ERR" if res.get("error") else "ok"
    return (f"{res.get('config', '?'):13s} {res.get('mode', '?'):8s} {flag:6s} steady {ws.get('steady_mb')} burst {ws.get('burst_mb')} post {(r.get('post_storm') or {}).get('steady_mb')} | "
            f"hs {pm.get('hs_mb')} (anon {pm.get('hs_anon_mb')} file {pm.get('hs_file_mb')}) shim {pm.get('shim_mb')} pg {pm.get('pg_mb')} (anon {pm.get('pg_anon_mb')} file {pm.get('pg_file_mb')}) | "
            f"recall p50 {rec.get('p50_ms')} p95 {rec.get('p95_ms')} | hit@5 {hit.get('direct')} {hit.get('paraphrase')} | retain ok {ret.get('ok')} fail {ret.get('failed')} "
            f"units {w.get('units')} valid {(w.get('validity') or {}).get('valid')}/{(w.get('validity') or {}).get('calls')} | avail-min {res.get('mem_available_min_mb')} {res.get('run_id', '')}")


def main(argv: "Optional[list[str]]" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default="base")
    ap.add_argument("--mode", choices=("verbatim", "chunks"), default="chunks")
    ap.add_argument("--retains", type=int, default=200)
    ap.add_argument("--recalls", type=int, default=200)
    ap.add_argument("--tag", default="")
    ap.add_argument("--seed", default="", help="the needle / filler seed (default ropt-v1): the recall-quality comparison between embedders runs several")
    ap.add_argument("--wait-min", type=float, default=0.0, help="wait this long for MemAvailable room before each attempt")
    ap.add_argument("--attempts", type=int, default=1, help="retry after a memory-floor abort (the box's own MemAvailable swings)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--functional", action="store_true", help="after the workload run the real adapter's H1 flow and H2 consolidation on the server (needs the brain: use with --mode verbatim)")
    ap.add_argument("--hm", choices=("local", "shared"), default="", help="run the HM driver probe (the real MemPalace library) next to the shim: own ONNX session or the shared shim")
    ap.add_argument("--hm-cells", action="store_true", help="with --hm: run the 20 HM cells on the real library (not the RAM probe) and report the verdicts")
    ap.add_argument("--hm-two-tier", action="store_true", help="with --hm: also start Hindsight + Postgres and time the REAL two-tier packet lanes")
    ap.add_argument("--shim", action="store_true", help="run the embeddings shim alone under --config (420 requests), not the whole stack")
    ap.add_argument("--table", default="", help="print one line per result file matching this glob (under the lab directory) and exit")
    a = ap.parse_args(argv)
    global SEED
    if a.seed:
        SEED = a.seed
    if a.table:
        for f in sorted(LAB.glob(a.table), key=lambda q: q.stat().st_mtime):
            if f.name.endswith(".json") and ".samples." not in f.name:
                print(result_row(json.loads(f.read_text())))
        return 0
    if a.list:
        for n, c in CONFIGS.items():
            print(f"{n:16s} {c.note}")
        return 0
    if a.config not in CONFIGS:
        print(f"unknown config {a.config!r}; known: {', '.join(CONFIGS)}", file=sys.stderr)
        return 2
    if a.retains > 200:
        print("refusing more than 200 retains per experiment", file=sys.stderr)
        return 2
    res: "dict[str, Any]" = {}
    # a kill must still reach the `finally` that stops the stack (a SIGTERM would otherwise skip it); the previous handler is put back (a test calls main())
    previous = signal.signal(signal.SIGTERM, lambda *_a: sys.exit(143))
    try:
        res = _run_with_lock(a)
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps({k: v for k, v in res.items() if k != "samples"}, indent=1, default=str))
    return 1 if res.get("error") or res.get("breach") else 0


def _run_with_lock(a: "argparse.Namespace") -> "dict[str, Any]":
    res: "dict[str, Any]" = {}
    with lab_lock():
        for n in range(max(1, a.attempts)):
            if a.hm:
                res = run_hm_probe(CONFIGS[a.config], a.hm == "shared", a.hm_two_tier, a.tag, wait_s=a.wait_min * 60.0, cells=a.hm_cells)
            elif a.shim:
                res = run_shim_probe(CONFIGS[a.config], a.tag, wait_s=a.wait_min * 60.0)
            else:
                res = run_config(CONFIGS[a.config], a.mode, a.retains, a.recalls, a.tag, wait_s=a.wait_min * 60.0, functional=a.functional)
            res["attempt"] = n + 1
            if not (res.get("breach") or "no room" in str(res.get("error"))):
                break
            print(f"attempt {n + 1}: {res.get('breach') or res.get('error')}", flush=True)
            time.sleep(20)
    return res


if __name__ == "__main__":
    raise SystemExit(main())
