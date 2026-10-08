"""What a bake-off window MEASURES (called by ``bakeoff.Window`` once the clone, Hindsight, the shim and the scratch DB are up).

Order of work, by value, because 90 minutes cannot hold everything at full size (the schedule is printed by ``--dry-run``):

    1  Z0 and Z0-off in the lab on every seed (no brain)                   the control, and the negative control
    2  forgetting probes START (t+0 check); the real t+6 min check is run later, between cells
    3  adapter negative controls on the real server (a bypassed gate must write the intruder row)
    4  H1, the preferred arm, FIRST and COMPLETE: seed 1, recall latency (n=50), verbatim extraction validity (>=100 calls), brain-slot
       seconds per retained turn, then seeds 2 and 3 (each seed box is a ceiling sized from run 1's measured seconds per cell)
    5  H2 (one seed: the observation layer's cells, then its latency / concise validity / slot IF TIME REMAINS) and H0 (same) take what H1
       left: they are INCOMPLETE by design (the rule needs three seeds), so they are measured for the comparison, never for adoption

The capability axes (j exact words, k reflection, l long-range recall, m protocol; the owner's contest, 2026-10-07) run on SEED 1 only, and each
arm runs the ones it is the evidence for (``CAP_PLANNED``): that, H2's and H0's latency / slot phases and the concise-validity phase becoming "only
if time remains" are what the plan CUT to fit them under the 90 minute cap (``--dry-run`` prints both).
    6  the t+6 min forgetting verdicts, the report

A cell the time box did not reach is a SKIP with the reason, never a pass; an arm with fewer than three seeds is INCOMPLETE and cannot be
adopted (bakeoff_gates). Counts and labels only go into the artifact: no household text.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from . import bakeoff_gates as gates
from .bakeoff import PG_CONTAINER, UNITS, Aborted

UNITS_CLONE = UNITS["clone"]

#: What run 1 (20261006-0935, ``run-20261006-0935.log``) MEASURED; the planner budgets from these, not from hope.
#: minutes per fixed phase (Z0 lab + forgetting start + adapter controls; per-arm latency / slot; per-mode extraction validity)
PHASE_MIN = {"lab": 2.4, "latency": {"H1": 1.0, "H2": 2.5, "H0": 5.1, "HM": 0.0, "MPA": 0.0, "HMA": 0.0, "ZMA": 0.0},        # MPA / HMA / ZMA: their latency and slot numbers come from their own cells
             "slot": {"H1": 3.1, "H2": 5.2, "H0": 5.2, "HM": 0.0, "MPA": 0.0, "HMA": 0.0, "ZMA": 0.0},
             "validity": {"verbatim": 6.0, "concise": 10.0},
             #: Z0e (real Chroma + MiniLM) on the D cells x 3 seeds, measured 2026-10-06 first contact: 4 cells x ~6.5 s x 3 seeds = 1.3 min
             #: ... plus the two long-range cells (L1 9 s, L2 17 s per seed measured 2026-10-07): 2.7 min
             "z0e": 2.7,
             #: the HM cells on the real tiers (real library + real Hindsight, ``--controls real-tier``): 217 s measured at first contact (2026-10-06), with headroom
             "hm_cells": 5.0}
#: seconds per cell that RAN, seed 1: H1 112 cells in 414 s, H2 55 in 662 s, H0 18 in 362 s
S_PER_CELL = {"H1": 3.7, "H2": 12.0, "H0": 20.0, "HM": 1.3, "MPA": 6.0, "HMA": 8.0,
              "ZMA": 6.5}      # ZMA: Z0e's rate (4 cells x ~6.5 s measured 2026-10-06): its write path has no model call, Chroma + MiniLM are real (a constant to tune)      # HM: 131 cells ran in 171 s on the real tiers at first contact (a verbatim write is no model call; most cells never distil)
#: seeds per arm. H1 is the preferred arm and needs all three (the rule); H2 and H0 get one each and are INCOMPLETE by design
SEEDS_PER_ARM = {"H1": 3, "H2": 1, "H0": 1, "HM": 1, "MPA": 1, "HMA": 1, "ZMA": 1}
ARM_ORDER = ("H1", "H2", "HM", "MPA", "HMA", "ZMA", "H0")  # priority: an earlier arm is finished before a later one starts (HM / MPA / HMA are candidates, H0 only the native baseline)
DRIVER_ARMS = ("HM", "MPA", "HMA", "ZMA")                 # the arms whose one seed box + own cells run in a DRIVER process (hm_window.py / mpa_window.py), not in this interpreter
OPTIONAL_PHASES = ("H2", "H0")                    # the arm's latency + slot run only if time remains, never budgeted: H0 cannot win or complete, and H2 is
                                                  # INCOMPLETE by design (one seed), so their CELLS (what Hindsight does natively; the observation layer) outrank
                                                  # their timings. CUT 2026-10-07 to make room for the capability cells: H2's latency 2.5 + slot 5.2 min
OPTIONAL_VALIDITY = ("concise",)                  # the concise-mode extraction-validity phase (10 min, shared by H2 and H0) runs only if time remains: both arms are
                                                  # INCOMPLETE by design, the verbatim phase (H1, the arm that can be adopted) stays budgeted. CUT 2026-10-07: 10 min

#: the capability axes (the owner's contest). They run on seed 1 only, and an arm runs the ones it is the EVIDENCE for:
#:   H1 / HM  exact words, long-range recall and the protocol's lab half (H1's verbatim write is one model call per fact; HM's is none)
#:   H2 / H0  reflection: the observation layer is what H2 adds over H1 and what H0 does natively; their exact-words / multi-hop / protocol would only be
#:            H1's retrieval again behind a concise rewrite (CUT: ~15 min of H2 time the window does not have)
CAP_AXES = ("exact_words", "reflection", "multi_hop", "protocol")
CAP_PLANNED = {"H1": ("exact_words", "multi_hop", "protocol"), "H2": ("reflection",), "H0": ("reflection",), "HM": ("exact_words", "multi_hop", "protocol"),
               #: MPA / HMA are the evidence for ALL FOUR (MPA: reflection via the closet pass summaries the clone brain makes; HMA: via Hindsight's observations). Their capability
               #: work is INSIDE their own cells phase (``mpa_cells`` / ``hma_cells`` minutes), so ``cap_extra_min`` is 0 for them.
               "MPA": ("exact_words", "reflection", "multi_hop", "protocol"), "HMA": ("exact_words", "reflection", "multi_hop", "protocol"),
               "ZMA": ("exact_words", "reflection", "multi_hop", "protocol")}      # ZMA's store cells run ALL axes on seed 1 inside its own box (no model call on its write path)
#: what an arm DECLARES (it could run it) and the plan drops: H1 and HM have no observation layer, so reflection is a capability skip for them, not a cut
CAP_CUT = {"H1": (), "HM": (), "H2": ("exact_words", "multi_hop", "protocol"), "H0": ("exact_words", "multi_hop", "protocol"), "MPA": (), "HMA": (), "ZMA": ()}
#: retained model calls one seed-1 play makes per axis (counted from the corpora: filler that is stored + the facts taught) and seconds per call
#: (``slot_s_per_turn``: H1 1.52, H2 2.56 at run 1), plus the observation consolidation (measured by nobody yet: 90 s is a guess, the box is a ceiling)
CAP_RETAINS = {"exact_words": 45, "multi_hop": 260, "protocol": 14, "reflection": 37}
S_PER_RETAIN = {"H1": 1.52, "H2": 2.56, "H0": 2.56}
CONSOLIDATE_S = 90.0

# ── THE MPA / HMA / REFLECTION COST CONSTANTS: the ONLY place these numbers live (the dry-run plan prints them; tune them after a smoke run) ──────────────────────
#: brain model calls per cell family for ONE MPA seed: the clone brain operates MemPalace's MCP tools, every turn of every cell is a model call
MPA_CALLS = {"protocol": 85, "exact_words": 32, "multi_hop": 36, "reflection": 86, "behaviour": 50, "closet": 12}      # 301 calls: the coordinator's count from the cells
MPA_S_PER_CALL = 3.0                              # seconds per brain call on the 8k clone (prefill of the protocol text + tool schemas + decode)
#: HMA = MPA + Hindsight's observation consolidation (the reflective tier): the same cells plus this many extra calls (plus ``CONSOLIDATE_S``, as H2)
HMA_CALLS = {**MPA_CALLS, "observations": 60}
#: ZMA = Zoe's live stack + MemPalace: its WRITE path has no model call (the harness files the verbatim chunk, Z0's extractor reads from it), so only its brain-tier cells (the protocol M4,
#: tool validity, search-before-answer, J4 / L4 on the clone) cost calls; its generic store cells run all axes A-M in the seed box
ZMA_CALLS = {"protocol": 60, "exact_words": 20, "multi_hop": 40, "behaviour": 30, "reflection": 30}
#: the REFLECTION PHASE (optional): K work only, per variant, against a restarted clone: ``<arm>@32k`` (the live 4B at ``--ctx-size 32768``) and ``<arm>@12B`` (the parked 12B deep-brain model)
REFLECT_CALLS = {"H2@32k": 60, "HMA@32k": 80, "ZMA@32k": 40, "H2@12B": 60, "HMA@12B": 80, "ZMA@12B": 40}
REFLECT_S_PER_CALL = {"4B@32k": 4.0, "12B@32k": 12.0}      # seconds per call at 32k, per model (placeholders until the first window measures them)
REFLECT_LOAD_MIN = {"4B@32k": 1.5, "12B@32k": 3.0}          # restart + health poll (and the restart back), minutes
#: the 12B pair's RAM arithmetic (measured from the GGUF header by the coordinator, 2026-10-07): the weights are the file's size; KV at q8_0 (1.0625 bytes/element) with llama.cpp's
#: reduced-SWA cache = 8 global layers x 1 kv head x (512+512) elements per token x ctx + 189 M SWA elements; compute / output buffers ~0.6 GB (262k vocab, ubatch 128)
REFLECT_12B_MODEL_BYTES = 6_975_877_728
REFLECT_12B_KV_GLOBAL_ELEMS_PER_TOKEN, REFLECT_12B_KV_SWA_ELEMS, REFLECT_KV_BYTES_PER_ELEM = 8 * 1024, 189_000_000, 1.0625
REFLECT_COMPUTE_MB = 600.0
REFLECT_AFTER_STOP_MB = 8400.0                    # MemAvailable measured on this box with the live 4B stopped (about 1.7 GB with it running), minus nothing: the preflight re-measures it
REFLECT_STOP_UNIT_MB = {"kokoro-tts.service": 2300.0}    # what a listed unit frees (information for the dry plan; the preflight measures the real number)
REFLECT_PAIRS = ("4B@32k", "12B@32k")                        # execution order: the 4B pair, then the 12B pair; each pair = the variants of the arms in the window
REFLECT_VARIANTS = {"4B@32k": ("H2@32k", "HMA@32k", "ZMA@32k"), "12B@32k": ("H2@12B", "HMA@12B", "ZMA@12B")}


def half_up(minutes: float) -> float:
    """Round UP to the half minute (a plan must not under-budget)."""
    return math.ceil(minutes * 2.0 - 1e-9) / 2.0


def kv_est_mb(ctx: int) -> float:
    """The 12B's KV cache at ``ctx`` (q8_0, reduced-SWA cache), in MB (decimal): about 486 at 32768."""
    return (REFLECT_12B_KV_GLOBAL_ELEMS_PER_TOKEN * ctx + REFLECT_12B_KV_SWA_ELEMS) * REFLECT_KV_BYTES_PER_ELEM / 1e6


def need_mb_12b(model_bytes: "Optional[int]", ctx: int, floor_mb: float) -> float:
    """MemAvailable the 12B pair needs once the 4B is stopped: ``model_file_mb + kv_est_mb(ctx) + 600 + cfg.min_avail_mb``."""
    return round((model_bytes or REFLECT_12B_MODEL_BYTES) / 1e6 + kv_est_mb(ctx) + REFLECT_COMPUTE_MB + floor_mb, 0)


def mpa_cells_min() -> float:
    """Minutes of MPA's fixed cells phase, COMPUTED from the model calls (``MPA_CALLS`` x ``MPA_S_PER_CALL``)."""
    return half_up(sum(MPA_CALLS.values()) * MPA_S_PER_CALL / 60.0)


def hma_cells_min() -> float:
    """Minutes of HMA's fixed cells phase: ``HMA_CALLS`` x the MPA seconds per call + Hindsight's consolidation (as H2)."""
    return half_up((sum(HMA_CALLS.values()) * MPA_S_PER_CALL + CONSOLIDATE_S) / 60.0)


def zma_cells_min() -> float:
    """Minutes of ZMA's fixed cells phase (its brain-tier cells), COMPUTED from ``ZMA_CALLS`` x ``MPA_S_PER_CALL``."""
    return half_up(sum(ZMA_CALLS.values()) * MPA_S_PER_CALL / 60.0)


def driver_calls(arm: str) -> dict:
    """The model-call constants of an MPA-driver arm (read at call time, so a test or a tuning edit is followed)."""
    return {"MPA": MPA_CALLS, "HMA": HMA_CALLS, "ZMA": ZMA_CALLS}[arm]


def reflect_pair_min(pair: str, arms: tuple) -> float:
    """Minutes of one reflection pair (``4B@32k`` / ``12B@32k``) for the arms in the window (0.0 when no arm of the pair is in it)."""
    names = [v for v in REFLECT_VARIANTS[pair] if v.split("@")[0] in arms]
    if not names:
        return 0.0
    return half_up(sum(REFLECT_CALLS[v] for v in names) * REFLECT_S_PER_CALL[pair] / 60.0 + REFLECT_LOAD_MIN[pair])


PHASE_MIN["mpa_cells"] = mpa_cells_min()
PHASE_MIN["hma_cells"] = hma_cells_min()
PHASE_MIN["zma_cells"] = zma_cells_min()
PHASE_MIN["reflect32k"] = reflect_pair_min("4B@32k", ("H2", "HMA", "ZMA"))
PHASE_MIN["reflect12b"] = reflect_pair_min("12B@32k", ("H2", "HMA", "ZMA"))
HM_CAP_MIN = 2.0                                  # HM: the verbatim write is no model call; the same corpora took ~100 s on the real tiers in the lab
H1_BOX_MARGIN = 1.25                             # the new cells (D recall, A3, C temporal) are unmeasured on real Hindsight: headroom over run 1's rate
OPEN_MIN, TAIL_MIN = 0.5, 5.0                    # steps 1-6 of the window; the t+6 min wait + report at the end
LATENCY_FACTS, LATENCY_QUERIES = 16, 50
VALIDITY_CALLS = 104
SMOKE_VALIDITY_CALLS, SMOKE_SLOT_RETAINS = 10, 4          # BAKEOFF_SMOKE_CELLS: a handful of brain calls, never a measurement
SLOT_RETAINS, SLOT_TURNS_PER_CHUNK = 12, 10
FORGET_WAIT_S = 360.0
PROBE_USERS = {v: "demo_bar_" + hashlib.sha1(f"zmb-probe-{v}".encode()).hexdigest()[:8] for v in ("H0", "H1", "H2", "MPA", "HMA", "ZMA")}      # the driver arms run theirs inside their driver
HM_DRIVER = Path(__file__).resolve().parent / "hm_window.py"
MPA_DRIVER = Path(__file__).resolve().parent / "mpa_window.py"          # one driver for MPA and (``--arm HMA``) HMA, and for the reflection variants (``--reflect-only``)

_PLACES = ("Hobart", "Lisbon", "Perth", "Bergen", "Ghent", "Cork", "Dunedin", "Tauranga")
_PEOPLE = ("Priya", "Ravi", "Anika", "Teodor", "Ines", "Oskar", "Saoirse", "Tomas")
_THINGS = ("the observatory", "a ferry company", "the botanic garden", "a bakery", "the harbour office", "a bookbinder")


def probe_sentences(n: int) -> "list[str]":
    """Deterministic, invented, one-fact sentences (no household text)."""
    out = []
    for i in range(n):
        p, c, t = _PEOPLE[i % len(_PEOPLE)], _PLACES[(i * 3) % len(_PLACES)], _THINGS[i % len(_THINGS)]
        out.append([f"User's friend {p} lives in {c}.", f"{p} works at {t}.", f"User met {p} in {c} last spring.",
                    f"{p}'s birthday is the {(i % 27) + 1}th of March."][i % 4])
    return out


def interleave(cells: list) -> list:
    """Round-robin over cell families (``A1``, ``A2``, ``F2``, ...) so a time box samples every axis before it deepens any one."""
    fams: "dict[str, list]" = {}
    for c in cells:
        fams.setdefault(c.id.split(".")[0], []).append(c)
    out, i = [], 0
    while any(fams.values()):
        for f in sorted(fams):
            if i < len(fams[f]):
                out.append(fams[f][i])
        i += 1
        if i > max(len(v) for v in fams.values()):
            break
    return out


#: cells a smoke run always includes: the physical-erase cell proves the byte scan sees real Postgres residue on the real stack
SMOKE_PRIORITY = ("F5.forgotten_text_not_on_disk",)


def pick_smoke(cells: list, n: int) -> list:
    """TEST HOOK (``BAKEOFF_SMOKE_CELLS=n``): ``n`` store cells spread over the axes: the priority cells, then one per family letter
    (A B C D ...) round-robin, so a ten-cell smoke touches authority, extraction, temporal, recall, abstention, forgetting ..."""
    chosen = [c for c in cells if c.id in SMOKE_PRIORITY][:n]
    letters: "dict[str, list]" = {}
    for c in cells:
        if c not in chosen:
            letters.setdefault(c.id[0], []).append(c)
    i = 0
    while len(chosen) < n and any(letters.values()):
        for k in sorted(letters):
            if len(chosen) < n and i < len(letters[k]):
                chosen.append(letters[k][i])
        i += 1
        if i > max(len(v) for v in letters.values()):
            break
    return chosen


def seeds_for(stamp: str) -> "tuple[str, str, str]":
    from .world import BASELINE_SEED
    return (BASELINE_SEED, f"zmb-heldout-a-{stamp}", f"zmb-heldout-b-{stamp}")


def parse_pss_kb(smaps_rollup: str) -> float:
    for line in (smaps_rollup or "").splitlines():
        if line.startswith("Pss:"):
            return float(line.split()[1])
    return 0.0


def parse_metrics_seconds(text: str) -> "Optional[float]":
    """Brain-slot seconds from the clone's Prometheus ``/metrics``: prompt (prefill) + predicted (decode) time."""
    tot, seen = 0.0, False
    for line in (text or "").splitlines():
        m = re.match(r"^llamacpp:(prompt_seconds_total|tokens_predicted_seconds_total)\s+([0-9.eE+-]+)", line)
        if m:
            tot += float(m.group(2))
            seen = True
    return tot if seen else None


def parse_mib(s: str) -> float:
    m = re.match(r"\s*([0-9.]+)\s*([KMG]i?B)", s or "")
    if not m:
        return 0.0
    return float(m.group(1)) * {"K": 1 / 1024, "M": 1.0, "G": 1024.0}[m.group(2)[0]]


# ── the sampler: PSS of every candidate PID, MemAvailable, non-loopback connects ─────────────

class Sampler(threading.Thread):
    daemon = True

    def __init__(self, win: Any):
        super().__init__(name="bakeoff-sampler")
        self.win, self.label, self.samples = win, "setup", []
        self.nonloopback: "set[str]" = set()
        self._stop_evt = threading.Event()
        self._pg_mb, self._n = 0.0, 0

    def unit_pids(self, unit: str) -> "list[int]":
        h = self.win.host
        cg = h.run(["systemctl", "--user", "show", "-p", "ControlGroup", "--value", unit], mutating=False).out.strip()
        procs = h.read(f"/sys/fs/cgroup{cg}/cgroup.procs") if cg else ""
        return [int(x) for x in procs.split() if x.isdigit()]

    def snapshot(self) -> "dict[str, Any]":
        h, w = self.win.host, self.win
        pids: "list[int]" = []
        for u in ("hindsight", "shim"):
            pids += self.unit_pids(UNITS[u])
        pids = sorted(set(pids))                      # a PID counted once however many units list it
        rss_mb = sum(parse_pss_kb(h.read(f"/proc/{p}/smaps_rollup")) for p in pids) / 1024.0
        if self._n % 5 == 0:
            r = h.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "zoe-bakeoff-pg"], timeout=20, mutating=False)
            self._pg_mb = parse_mib(r.out.split("/")[0]) if r.rc == 0 else self._pg_mb
        self._n += 1
        ss = h.run(["ss", "-tnpH", "state", "established"], mutating=False).out
        watch = set(pids)
        drv = h.run(["pgrep", "-f", r"^\S*python\S* .*(hm_window|mpa_window)\.py"], mutating=False)       # the HM / MPA drivers are child processes of the window: their sockets count too
        if drv.rc == 0:
            watch |= {int(x) for x in drv.out.split() if x.isdigit()}
        for line in ss.splitlines():
            parts = line.split()
            if len(parts) < 4 or not any(f"pid={p}," in line for p in watch):
                continue
            peer = parts[3].rsplit(":", 1)[0].strip("[]")
            if peer.startswith("::ffff:"):            # an IPv4 peer in its IPv6-mapped spelling is the same address
                peer = peer[7:]
            if not (peer.startswith("127.") or peer == "::1"):
                self.nonloopback.add(peer)
        return {"t": h.mono(), "label": self.label, "mem_available_mb": w.mem(), "rss_mb": round(rss_mb + self._pg_mb, 1), "pids": len(pids)}

    def run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                s = self.snapshot()
                self.samples.append(s)
                if s["mem_available_mb"] < self.win.cfg.min_avail_mb and not self.win.dry:
                    self.win.abort_flag = f"MemAvailable {s['mem_available_mb']:.0f} MB < {self.win.cfg.min_avail_mb:.0f} MB floor (sampler)"
            except Exception:  # noqa: BLE001 - a failed sample must never kill the window
                pass
            self._stop_evt.wait(self.win.cfg.sample_s)

    def stop(self) -> None:
        self._stop_evt.set()

    def summary(self, prefix: "Optional[str]" = None) -> "dict[str, Optional[float]]":
        xs = [s["rss_mb"] for s in self.samples if s["pids"] and (prefix is None or s["label"].startswith(prefix))]
        return {"steady_mb": gates.median(xs), "burst_mb": round(max(xs), 1) if xs else None, "samples": len(xs)}


# ── the forgetting probe: a REAL t+6 min ─────────────────────────────────────

class ForgetProbe:
    """Forget an invented friend, check at t+0, wait a real six minutes (the driver keeps measuring meanwhile), then replay a transcript that
    still names her through the arm's OWN nightly pass and a late model writer, and check again."""
    #: invented names OUTSIDE every ``world.py`` pool: the probe's bank stays alive for the whole window, in the same scratch Postgres the F5 / F6 disk
    #: cells byte-scan, so a name the seeded household also draws (Marisol, Priya, Hobart ...) would read as that cell's residue
    NAME, KEEP, PLACE = "Zephyrine", "Cordelia", "Invercargill"

    def __init__(self, ctx: "Ctx", variant: str):
        self.ctx, self.variant = ctx, variant
        self.arm = ctx.new_arm(variant)
        self.user = PROBE_USERS[variant]
        self.t_forget: "Optional[float]" = None
        self.t0: "Optional[dict]" = None
        self.t6: "Optional[dict]" = None

    def _check(self) -> "dict[str, Any]":
        rows = self.arm.stats()["rows"]
        named = [r for r in rows if r["status"] in ("approved", "pending", "disputed") and self.NAME.lower() in r["text"].lower()]
        hits = [r for r in self.arm.recall(f"tell me about {self.NAME}", 10) if self.NAME.lower() in r["text"].lower()]
        kept = [r for r in rows if r["status"] == "approved" and self.KEEP.lower() in r["text"].lower()]
        return {"checked": 2, "resurrected": (1 if named else 0) + (1 if hits else 0), "kept_others": len(kept),
                "how": "store export + recall packet naming her"}

    def start(self) -> None:
        from .arms.base import Turn
        self.arm.reset(self.user)
        self.arm.ingest([Turn(f"User's friend {self.NAME} lives in {self.PLACE}.", "owner_taught"),
                         Turn(f"{self.NAME}'s birthday is the 3rd of March.", "owner_taught"),
                         Turn(f"User's friend {self.KEEP} works at the observatory.", "owner_taught")])
        self.arm.forget(self.NAME)
        self.t_forget = self.ctx.host.now()
        self.t0 = self._check()

    def due(self) -> bool:
        return self.t6 is None and self.t_forget is not None and self.ctx.host.now() - self.t_forget >= FORGET_WAIT_S

    def finish(self) -> None:
        from .arms.base import Turn
        transcript = f"okay so that was the plan and then {self.NAME} rang about the weekend and we will see about the shopping later on tonight"
        self.arm.run_idle_pass(transcript, [f"User's friend {self.NAME} is visiting at the weekend."])
        self.arm.ingest([Turn(transcript, "system_writer", writer="digest", proposes=(f"{self.NAME}: 3rd of March",), op="say")])
        self.t6 = self._check()
        self.t6["waited_s"] = round(self.ctx.host.now() - (self.t_forget or 0), 1)

    def close(self) -> None:
        self.arm.close()


# ── the context a window's measurement shares ────────────────────────────────

class Ctx:
    def __init__(self, win: Any, sampler: "Optional[Sampler]"):
        self.win, self.host, self.log, self.cfg = win, win.host, win.log, win.cfg
        self.sampler = sampler
        self.seed_runs: "dict[str, dict[str, dict]]" = {}
        self.z0: "dict[str, dict]" = {}
        self.z0_off: "dict[str, dict]" = {}
        self.measure: "dict[str, dict]" = {v: {} for v in ("H0", "H1", "H2", "HM", "MPA", "HMA", "ZMA")}
        self.reflect: "Optional[dict]" = None                  # the reflection phase's record (variants, never contest entrants); None = not attempted
        self.z0e: "dict[str, dict]" = {}
        self.forget: "dict[str, ForgetProbe]" = {}
        self.notes: "list[str]" = []
        self.aborted = ""
        self.validity_done: "set[str]" = set()
        self.marks: "list[tuple[float, str]]" = []      # (epoch, phase label): which phase an egress event fell in
        self.ref_epoch = win.host.now()

    def new_arm(self, variant: str, **kw: Any) -> Any:
        return self.win.arm_factory(variant, **kw)

    def slot_seconds(self) -> "Optional[float]":
        r = self.host.run(["curl", "-sf", "-m", "5", f"http://127.0.0.1:{self.cfg.llm_port}/metrics"], mutating=False)
        return parse_metrics_seconds(r.out) if r.rc == 0 else None

    def label(self, name: str) -> None:
        self.marks.append((self.host.now(), name))
        if self.sampler:
            self.sampler.label = name

    def due_probes(self) -> None:
        for p in self.forget.values():
            if p.due():
                self.log(f"forgetting probe {p.variant}: t+{FORGET_WAIT_S / 60:.0f} min reached, replaying and checking")
                p.finish()


# ── the phases ───────────────────────────────────────────────────────────────

def _strip(rows: "list[dict]") -> "list[dict]":
    return [{k: v for k, v in r.items() if k != "evidence"} for r in rows]


def _k1(rows: "list[dict]") -> "list[dict]":
    """The K1 precision counts of a seed's rows (the observation veto reads these; ``_strip`` drops the evidence they come from)."""
    return [gates.k1_evidence(r) for r in rows if str(r.get("id", "")).startswith("K1.") and r.get("verdict") != "SKIP"]


def phase_z0(ctx: Ctx, seeds: tuple, store: list, by_id: dict) -> None:
    from . import artifact, runner
    from .arms import make_arm
    from .lab_driver import CONTROLS
    from .world import make_world
    for seed in seeds:
        world = make_world(seed)
        # the capability cells run on the FIRST seed only, for every arm (``run_arm_seed``): a baseline pooled over three households would be compared with a candidate's one
        seed_store = store if seed == seeds[0] else [c for c in store if c.axis not in CAP_AXES]
        cp = runner.control_pass(store, world, frozenset(CONTROLS))
        inst = runner.instrument_block(cp, store)
        for label, arm_name, sink in (("Z0", "Z0", ctx.z0), ("Z0-off", "Z0-off", ctx.z0_off)):
            arm = make_arm(arm_name)
            try:
                rows = runner.run_cells(seed_store, world, arm)
            finally:
                arm.close()
            sink[seed] = {"axes": artifact.axis_stats(rows, by_id, inst["ok"]), "hard_violations": artifact.hard_violations(rows, by_id),
                          "instrument": inst, "cells": _strip(rows)}
        ctx.log(f"Z0 seed {seed}: controls red {inst['lab_controls_red']} ok={inst['ok']}")
        phase_z0e(ctx, seed, world, seed_store, by_id, inst)


def phase_z0e(ctx: Ctx, seed: str, world: Any, store: list, by_id: dict, inst: dict) -> None:
    """Z0e = Z0 over a REAL Chroma collection with the service's MiniLM embedder (as live), on the recall (D) cells: the lab's own Z0 ranks by bag-of-words, so
    the D axis compares an embedding arm with a real embedder, not with a word counter. Skipped (with the reason in the notes) where chromadb or the cached
    model is missing: the D baseline then falls back to Z0 and the report says so."""
    from . import artifact, runner
    from .arms import make_arm
    cells = [c for c in store if c.id.startswith(("D", "L"))]          # recall (D) and long-range recall (L): both are retrieval, both need the real embedder
    arm = make_arm("Z0e")
    try:
        rows = runner.run_cells(cells, world, arm)
    finally:
        arm.close()
    ran = sum(1 for r in rows if r["verdict"] != "SKIP")
    if not ran:
        ctx.notes.append("Z0e did not run (no chromadb or no cached MiniLM model): the D axis is compared with the lab's bag-of-words Z0, which is not a retrieval baseline")
        return
    ctx.z0e[seed] = {"axes": artifact.axis_stats(rows, by_id, inst["ok"]), "cells": _strip(rows)}
    ctx.log(f"Z0e seed {seed}: {sum(1 for r in rows if r['verdict'] == 'PASS')}/{ran} recall + long-range cells pass over real Chroma + MiniLM")


def sweep_stale_banks(ctx: Ctx) -> int:
    """Delete the ``zmb-`` banks an earlier (crashed or finished) window left in the scratch store, BEFORE this window's own forgetting probes start
    their banks. They are nobody's data here, and a live bank that holds the name a disk cell (F5 / F6) scans for makes that cell's residue unmeasurable."""
    try:
        client = ctx.win.client_factory()
        stale = client.list_banks("zmb-")
        for bank in stale:
            client.delete_bank(bank)
    except Exception as exc:  # noqa: BLE001 - never stops the window: a stale bank only matters to a disk cell, which then says so itself
        ctx.log(f"stale-bank sweep skipped ({type(exc).__name__}: {str(exc)[:80]})")
        return 0
    ctx.log(f"cleared {len(stale)} stale zmb- bank(s) left in the scratch store by an earlier window")
    return len(stale)


#: arms whose cells run against the window's Hindsight server (their calls share the clone's single slot with whatever Hindsight's worker is doing in the background)
HINDSIGHT_ARMS = ("H0", "H1", "H2", "HM", "HMA")


def check_hindsight_idle(ctx: Ctx, arm: str) -> "Optional[int]":
    """Before an arm's cells: how many operations is Hindsight's worker still holding (pending + processing)? The installed Hindsight (0.10.2) has NO control that pauses consolidation in the
    background (``HINDSIGHT_API_WORKER_CONSOLIDATION_RESERVED_SLOTS`` is a FLOOR, not a ceiling; there is no pause flag; ``enable_auto_consolidation`` is per bank and H2 / HMA already run
    it off and trigger it explicitly), so this DETECTS instead of preventing: a non-zero count is a noted confound on that arm. Never raises."""
    if arm not in HINDSIGHT_ARMS:
        return None
    try:
        n, detail = ctx.win.hindsight_queue()
    except Exception as exc:  # noqa: BLE001 - an instrument for the report, never a reason to stop the window
        ctx.log(f"hindsight job queue before {arm}: unreadable ({type(exc).__name__}: {str(exc)[:80]})")
        return None
    if n:
        ctx.log(f"hindsight job queue before {arm}: {n} operation(s) still queued/running ({detail}) - background work shares the clone's single slot with {arm}'s cells")
        ctx.notes.append(f"{arm} started with {n} operation(s) still queued or running in Hindsight's worker ({detail}): an earlier arm's background consolidation shared the clone's single slot with its cells")
    else:
        ctx.log(f"hindsight job queue before {arm}: empty" if n == 0 else f"hindsight job queue before {arm}: unreadable ({detail})")
    return n


def phase_arm_controls(ctx: Ctx, store: list) -> None:
    """Instrument checks of the ADAPTER on the real server: with a Zoe-layer protection switched off the cell that claims it must go RED."""
    from . import cells as cellmod
    from .world import BASELINE_SEED, make_world
    world = make_world(BASELINE_SEED)
    # the first three are required; the last three need what the window gives the arm (the people graph, the conflict pass, the scratch Postgres): the physical-erase
    # control is the one that proves the byte scan sees REAL Postgres residue (F5 with the scrub OFF must be red on the real stack)
    claims = (("authority", "A1.digest.home"), ("identity", "H1.digest"), ("ledger", "F2.late_writer.digest"),
              ("authority", "A8.inferred_cannot_close_user_edge"), ("supersede", "C1.update_typed"), ("physical_erase", "F5.forgotten_text_not_on_disk"))
    red = counted = 0
    for off, cid in claims:
        cell = next((c for c in store if c.id == cid), None)
        if cell is None:
            continue
        arm = ctx.new_arm("H1", off=frozenset({off}))
        try:
            lacking = cellmod.required_capabilities(cell) - set(arm.capabilities)
            if lacking:
                ctx.log(f"adapter control: H1 with `{off}` OFF on {cid} NOT RUN (the arm lacks {', '.join(sorted(lacking))})")
                continue
            o = cellmod.run_cell(cell.rendered(world), world, arm)
        finally:
            arm.close()
        counted += 1
        red += 1 if o.verdict == "FAIL" else 0
        ctx.log(f"adapter control: H1 with `{off}` OFF on {cid} -> {o.verdict} (must be FAIL)")
    ctx.measure["H1"]["arm_controls"] = f"{red}/{counted}"
    for v in ("H0", "H2"):
        ctx.measure[v]["arm_controls"] = ctx.measure["H1"]["arm_controls"]        # HM has its own: the HM cells' real-tier controls (``phase_hm``)
    ctx.arm_controls_ok = counted >= 3 and red == counted


def skip_breakdown(rows: "list[dict]") -> "dict[str, int]":
    """Why cells were skipped, as counts: ``time box`` (the budget ran out), ``capability: x, y`` (the arm cannot do what the cell needs:
    structural, no amount of time fixes it), ``unreachable``, ``other``."""
    out: "dict[str, int]" = {}
    for r in rows:
        why = str(r.get("reason") or "")
        m = re.search(r"lacks capability: (.+)", why)
        key = ("time box" if "time box" in why else "planner cut" if "planner cut" in why else f"capability: {m.group(1).strip()}" if m else
               "unreachable" if "reach Hindsight" in why else "other")
        out[key] = out.get(key, 0) + 1
    return out


def skip_breakdown(rows: "list[dict]") -> "dict[str, int]":
    """Why cells were skipped, as counts: ``time box`` (the budget ran out), ``capability: x, y`` (the arm cannot do what the cell needs:
    structural, no amount of time fixes it), ``unreachable``, ``other``."""
    out: "dict[str, int]" = {}
    for r in rows:
        why = str(r.get("reason") or "")
        m = re.search(r"lacks capability: (.+)", why)
        key = ("time box" if "time box" in why else "planner cut" if "planner cut" in why else f"capability: {m.group(1).strip()}" if m else
               "unreachable" if "reach Hindsight" in why else "other")
        out[key] = out.get(key, 0) + 1
    return out


def run_arm_seed(ctx: Ctx, variant: str, seed: str, box_s: float, store: list, by_id: dict, instrument_of: "Callable[[str], dict]", first: bool = True) -> None:
    from . import artifact, cells as cellmod, runner
    from .world import make_world
    world = make_world(seed)
    arm = ctx.new_arm(variant)
    ctx.label(f"{variant}:{seed}")
    t_end, rows, unreachable, t0 = ctx.host.mono() + box_s, [], 0, ctx.host.mono()
    try:
        ordered = pick_smoke(store, ctx.cfg.smoke_cells) if ctx.cfg.smoke_cells else interleave(store)
        if not first:                                            # seeds 2 and 3: the capability cells ran on seed 1 (the budget), the ordinary ones are the three-seed rule
            ordered = [c for c in ordered if c.axis not in CAP_AXES]
        for cell in ordered:
            ctx.win.guard()
            ctx.due_probes()
            if cell.axis in CAP_CUT.get(variant, ()):
                o = cellmod.Outcome("SKIP", reason=f"planner cut: {variant} does not run the {cell.axis} cells in this window (H2 / H0 run reflection; the rest is H1's retrieval again)")
            elif ctx.host.mono() >= t_end or unreachable >= 3:
                o = cellmod.Outcome("SKIP", reason="time box reached" if unreachable < 3 else "Hindsight unreachable")
            else:
                o = cellmod.run_cell(cell.rendered(world), world, arm)
                unreachable = unreachable + 1 if (o.verdict == "SKIP" and "cannot reach Hindsight" in o.reason) else 0
            rows.append(runner._row(cell, o))
    finally:
        arm.close()
    if unreachable >= 3:
        raise Aborted(f"Hindsight unreachable during {variant}:{seed}")
    inst = instrument_of(seed)
    hard_skipped = sum(1 for r in rows if r["verdict"] == "SKIP" and artifact.is_hard(by_id[r["id"]]))
    hard_skip_why = skip_breakdown([r for r in rows if r["verdict"] == "SKIP" and artifact.is_hard(by_id[r["id"]])])
    ran = sum(1 for r in rows if r["verdict"] != "SKIP")
    ctx.seed_runs.setdefault(variant, {})[seed] = {
        "axes": artifact.axis_stats(rows, by_id, inst["ok"]), "hard_violations": artifact.hard_violations(rows, by_id),
        "hard_skipped": hard_skipped, "hard_skipped_why": hard_skip_why, "instrument": {"ok": inst["ok"] and getattr(ctx, "arm_controls_ok", False),
                                                      "lab_controls_red": inst["lab_controls_red"], "arm_controls": ctx.measure[variant].get("arm_controls")},
        "cells_ran": ran, "cells_selected": len(rows), "duration_s": round(ctx.host.mono() - t0, 1), "cells": _strip(rows),
        "k1": _k1(rows), "retain": arm.measure()}
    ctx.log(f"{variant} {seed}: {ran}/{len(rows)} cells ran in {ctx.host.mono() - t0:.0f}s; hard violations {len(ctx.seed_runs[variant][seed]['hard_violations'])}"
            f"; hard skipped {hard_skipped}")


def real_hm_runner(ctx: Ctx, seed: str, box_s: float) -> dict:
    """Run ``hm_window.py`` in the bake-off venv (the verbatim tier is the real MemPalace 3.10.0 library, not the interpreter the window runs in) and read its JSON.
    The Hindsight tier talks to this window's server over loopback; the Postgres scrub / scan go through ``docker exec``."""
    cfg, host = ctx.cfg, ctx.host
    out = cfg.bakeoff_dir / f"hm-{ctx.win.run_id}.json"
    argv = ["bash", str(cfg.bakeoff_dir / "mp_run.sh"), str(HM_DRIVER), "--out", str(out), "--url", f"http://127.0.0.1:{cfg.hs_port}", "--seed", seed,
            "--box-s", str(int(box_s)), "--controls", "real-tier"]
    if cfg.smoke_cells:
        argv += ["--smoke", str(cfg.smoke_cells)]
    if cfg.skip_brain_stop:
        argv += ["--quiet-since", str(ctx.win.t0_epoch)]
    env = {**os.environ, "MALLOC_PERTURB_": "85", "PYTHONMALLOC": "malloc", "ORT_DISABLE_TELEMETRY": "1"}          # the scrubbing allocator HM-F6 needs (forgotten text in uninitialised heap)
    if cfg.hm_shared_embedder:        # ONE embedder: the verbatim tier asks the window's shim (the session Hindsight already uses) instead of loading its own MiniLM session
        env["ZMB_HM_EMBEDDER_URL"] = f"http://127.0.0.1:{cfg.shim_port}"
    r = host.run(argv, timeout=box_s + PHASE_MIN["hm_cells"] * 60.0 * 2 + 180.0, env=env)
    text = host.read(str(out))
    if not text:
        return {"error": f"hm_window.py produced no result (rc={r.rc}): {r.out.strip()[-300:]}"}
    return json.loads(text)


def phase_hm(ctx: Ctx, seed: str, box_s: float, store: list, by_id: dict, instrument_of: "Callable[[str], dict]") -> None:
    """The HM arm's one seed box: the HM cells on the real tiers plus the generic store cells for one seed (``hm_window.py``), folded into the same
    structures an H arm's seed fills (axes, hard violations, gates), and the HM-only measurements (wall-clock latency, the verbatim tier's RAM)."""
    from . import artifact
    ctx.label(f"HM:{seed}")
    t0 = ctx.host.mono()
    res = (getattr(ctx.win, "hm_runner", None) or real_hm_runner)(ctx, seed, box_s)
    ctx.win.guard()
    why = res.get("aborted") or res.get("skipped") or res.get("error")
    if why and not res.get("hm_cells"):
        ctx.notes.append(f"HM did not run: {why}")
        ctx.log(f"HM: not run ({why})")
        if res.get("aborted"):
            raise Aborted(str(res["aborted"]))
        return
    if why:
        ctx.notes.append(f"HM stopped early: {why}")
    cells = res.get("hm_cells") or {}
    summ = cells.get("summary") or {}
    m = ctx.measure["HM"]
    cells = {**cells, "cells": [{k: v for k, v in r.items() if k != "title"} for r in cells.get("cells") or []]}       # titles name the household's pool names: counts and ids only
    m["hm_cells"] = cells
    m["hm_driver"] = res.get("driver") or {}
    m["hm_library"] = res.get("library", "")
    m["arm_controls"] = f"{summ.get('controls_checked', 0) - len(summ.get('not_instrumented') or [])}/{summ.get('controls_checked', 0)}"
    by = {r["id"]: r for r in cells.get("cells") or []}
    f1, f2 = by.get("HM-F1.forget.t0"), by.get("HM-F2.forget.t6min")
    m["forgetting"] = {k: {"checked": 2, "resurrected": 0 if c["verdict"] == "PASS" else 1, "kept_others": 1,
                           "how": how} for k, c, how in (
        ("t0", f1, "HM-F1: both tiers, real library + real Hindsight, name / case / possessive / hyphen"),
        ("t6", f2, "HM-F2: replay + the distiller's own re-proposal on a virtual 360 s clock (the ledger is durable: no TTL)")) if c}
    gen = res.get("generic")
    if gen and gen.get("rows"):
        rows = gen["rows"]
        inst = instrument_of(seed)
        controls_ok = bool(summ.get("controls_checked")) and not summ.get("not_instrumented")
        hard_skipped = sum(1 for r in rows if r["verdict"] == "SKIP" and artifact.is_hard(by_id[r["id"]]))
        why_hard = skip_breakdown([r for r in rows if r["verdict"] == "SKIP" and artifact.is_hard(by_id[r["id"]])])
        ctx.seed_runs.setdefault("HM", {})[seed] = {
            "axes": artifact.axis_stats(rows, by_id, inst["ok"]), "hard_violations": artifact.hard_violations(rows, by_id),
            "hard_skipped": hard_skipped, "hard_skipped_why": why_hard,
            "instrument": {"ok": inst["ok"] and controls_ok, "lab_controls_red": inst["lab_controls_red"], "arm_controls": m["arm_controls"]},
            "cells_ran": gen["cells_ran"], "cells_selected": gen["cells_selected"], "duration_s": gen["duration_s"], "cells": _strip(rows), "retain": {}}
        ctx.log(f"HM {seed}: {gen['cells_ran']}/{gen['cells_selected']} generic cells ran in {gen['duration_s']:.0f}s; hard violations "
                f"{len(ctx.seed_runs['HM'][seed]['hard_violations'])}; hard skipped {hard_skipped}")
    ctx.log(f"HM cells on the real tiers: {summ.get('pass')}/{summ.get('graded')} graded pass; red {summ.get('fail')}; targets failing {summ.get('targets_failing')}; "
            f"skipped {summ.get('skipped')}; controls {m['arm_controls']} red; {ctx.host.mono() - t0:.0f}s")


def driver_env(cfg: Any) -> dict:
    """The environment of every driver process (HM, MPA, HMA, the reflection runs): the scrubbing allocator the forgetting cells need, no telemetry, the shared embedder."""
    env = {**os.environ, "MALLOC_PERTURB_": "85", "PYTHONMALLOC": "malloc", "ORT_DISABLE_TELEMETRY": "1"}
    if cfg.hm_shared_embedder:
        env["ZMB_HM_EMBEDDER_URL"] = f"http://127.0.0.1:{cfg.shim_port}"
    return env


def _mpa_argv(ctx: Ctx, arm: str, out: Path, seed: str, box_s: float, extra: "tuple[str, ...]" = ()) -> "list[str]":
    """The CLI of ``mpa_window.py``: the clone brain's URL (the live port under BAKEOFF_SKIP_BRAIN_STOP), the seed, the box; HMA adds ``--arm HMA`` + Hindsight's URL."""
    cfg = ctx.cfg
    head = [sys.executable, str(MPA_DRIVER), "--arm", "ZMA"] if arm == "ZMA" else ["bash", str(cfg.bakeoff_dir / "mp_run.sh"), str(MPA_DRIVER)]     # ZMA: the window's own interpreter, NOT mp_run.sh
    argv = head + ["--out", str(out), "--clone-url", f"http://127.0.0.1:{cfg.llm_port}", "--seed", seed, "--box-s", str(int(box_s))]
    if arm == "HMA":
        argv += ["--arm", "HMA", "--hindsight-url", f"http://127.0.0.1:{cfg.hs_port}"]
    argv += list(extra)
    if cfg.smoke_cells:
        argv += ["--smoke", str(cfg.smoke_cells)]
    if cfg.skip_brain_stop:
        argv += ["--quiet-since", str(ctx.win.t0_epoch)]
    return argv


def _run_mpa_driver(ctx: Ctx, arm: str, out: Path, argv: "list[str]", timeout: float) -> dict:
    r = ctx.host.run(argv, timeout=timeout, env=driver_env(ctx.cfg))
    text = ctx.host.read(str(out))
    if not text:
        return {"error": f"mpa_window.py ({arm}) produced no result (rc={r.rc}): {r.out.strip()[-300:]}"}
    return json.loads(text)


def real_mpa_runner(ctx: Ctx, seed: str, box_s: float) -> dict:
    """Run ``mpa_window.py`` (MPA) in the bake-off venv, through the same ``mp_run.sh`` as HM, and read ``mpa-<run_id>.json``. The clone brain operates MemPalace's tools."""
    out = ctx.cfg.bakeoff_dir / f"mpa-{ctx.win.run_id}.json"
    return _run_mpa_driver(ctx, "MPA", out, _mpa_argv(ctx, "MPA", out, seed, box_s), box_s + PHASE_MIN["mpa_cells"] * 60.0 * 2 + 180.0)


def real_hma_runner(ctx: Ctx, seed: str, box_s: float) -> dict:
    """The same driver with ``--arm HMA``: MPA as the episodic tier + Hindsight (concise + observations) as the reflective tier; reads ``hma-<run_id>.json``."""
    out = ctx.cfg.bakeoff_dir / f"hma-{ctx.win.run_id}.json"
    return _run_mpa_driver(ctx, "HMA", out, _mpa_argv(ctx, "HMA", out, seed, box_s), box_s + PHASE_MIN["hma_cells"] * 60.0 * 2 + 180.0)


def real_zma_runner(ctx: Ctx, seed: str, box_s: float) -> dict:
    """``mpa_window.py --arm ZMA`` run with THIS interpreter (the zoe-data venv: Z0 needs zoe-data's modules; MemPalace is a subprocess the driver starts itself), reads ``zma-<run_id>.json``."""
    out = ctx.cfg.bakeoff_dir / f"zma-{ctx.win.run_id}.json"
    return _run_mpa_driver(ctx, "ZMA", out, _mpa_argv(ctx, "ZMA", out, seed, box_s), box_s + PHASE_MIN["zma_cells"] * 60.0 * 2 + 180.0)


def _brain_rows(cells: "list[dict]") -> "list[dict]":
    """The brain-tier protocol cells (``M4.*``) as seed-run rows, so ``aggregate_axes`` builds the derived axis ``protocol_brain`` (the M letter) from them."""
    return [{"id": c["id"], "axis": "protocol", "tier": "full", "verdict": c["verdict"], "stage": "", "expected": "PASS", "controls": [], "sanity": False,
             "duration_s": 0.0, "brain_turns": 0, "reason": "", "lme_map": None} for c in cells if str(c.get("id", "")).startswith("M4.")]


def _phase_driver_arm(ctx: Ctx, arm: str, seed: str, box_s: float, store: list, by_id: dict, instrument_of: "Callable[[str], dict]") -> None:
    """One seed box of an MPA-driver arm (MPA, HMA): the arm's own cells with the clone brain plus the generic store cells for one seed (``mpa_window.py``), folded into the
    same structures an H arm's seed fills (axes, hard violations, gates), and the arm-only measurements (the brain's tool calls, the MemPalace servers' RAM)."""
    from . import artifact
    ctx.label(f"{arm}:{seed}")
    t0 = ctx.host.mono()
    hook = getattr(ctx.win, f"{arm.lower()}_runner", None)
    res = (hook or {"MPA": real_mpa_runner, "HMA": real_hma_runner, "ZMA": real_zma_runner}[arm])(ctx, seed, box_s)
    ctx.win.guard()
    why = res.get("aborted") or res.get("skipped") or res.get("error")
    if why and not res.get("mpa_cells"):
        ctx.notes.append(f"{arm} did not run: {why}")
        ctx.log(f"{arm}: not run ({why})")
        if res.get("aborted"):
            raise Aborted(str(res["aborted"]))
        return
    if why:
        ctx.notes.append(f"{arm} stopped early: {why}")
    cells = res.get("mpa_cells") or {}
    summ = cells.get("summary") or {}
    m = ctx.measure[arm]
    cells = {**cells, "cells": [{k: v for k, v in r.items() if k != "title"} for r in cells.get("cells") or []]}          # titles name the household's pool names: counts and ids only
    m["mpa_cells"] = cells
    m["mpa_driver"] = res.get("driver") or {}
    m["brain"] = res.get("brain") or {}
    m["mpa_library"], m["mpa_model"] = res.get("library", ""), res.get("model", "")
    if res.get("hindsight"):
        m["hindsight"] = res["hindsight"]
    if arm == "ZMA":
        m["zma_embedder"] = getattr(ctx, "embed_model", "") or "unknown"          # which embedder the shim served, for the record (ZMA states the one it shared)
    pin = (m["brain"] or {}).get("prompt_tokens_max")
    if pin is not None:
        m["prompt_in_tokens_max"] = int(pin)
    m["arm_controls"] = f"{summ.get('controls_checked', 0) - len(summ.get('not_instrumented') or [])}/{summ.get('controls_checked', 0)}"
    by = {r["id"]: r for r in cells["cells"]}
    f1 = next((r for i, r in by.items() if i.startswith("MPA-F1")), None)
    fp = res.get("forget_probe") or {}
    m["forgetting"] = {k: {"checked": 2, "resurrected": 0 if c["verdict"] == "PASS" else 1, "kept_others": 1, "how": how} for k, c, how in (
        ("t0", f1, "MPA-F1: the forget call through the agent's tools, both tiers where there are two"),) if c}
    if fp.get("t0"):
        m["forgetting"]["t0"] = {**fp["t0"], "how": "the driver's probe, t+0: " + fp["t0"].get("how", "")}
    if fp.get("t6"):         # ONLY a real wall-clock t+6 min counts (the same ForgetProbe the H arms get); MPA-F2 is an immediate refile test and stays its own cell
        m["forgetting"]["t6"] = {**fp["t6"], "how": f"the driver's probe after a real {fp['t6'].get('waited_s', '?')} s: " + fp["t6"].get("how", "")}
    else:
        ctx.notes.append(f"{arm}: the t+6 min forgetting probe is unmeasured ({fp.get('unmeasured') or 'the driver reported none'}): the gate item reads NA")
    k1 = [{"id": r["id"], "verdict": r["verdict"]} for r in (res.get("generic") or {}).get("rows") or [] if str(r.get("id", "")).startswith("K1")]
    if k1:
        m["k1_rows"] = k1
    gen = res.get("generic")
    if gen and gen.get("rows"):
        rows = gen["rows"]
        inst = instrument_of(seed)
        controls_ok = bool(summ.get("controls_checked")) and not summ.get("not_instrumented")
        hard_skipped = sum(1 for r in rows if r["verdict"] == "SKIP" and artifact.is_hard(by_id[r["id"]]))
        why_hard = skip_breakdown([r for r in rows if r["verdict"] == "SKIP" and artifact.is_hard(by_id[r["id"]])])
        ctx.seed_runs.setdefault(arm, {})[seed] = {
            "axes": artifact.axis_stats(rows, by_id, inst["ok"]), "hard_violations": artifact.hard_violations(rows, by_id),
            "hard_skipped": hard_skipped, "hard_skipped_why": why_hard,
            "instrument": {"ok": inst["ok"] and controls_ok, "lab_controls_red": inst["lab_controls_red"], "arm_controls": m["arm_controls"]},
            "cells_ran": gen["cells_ran"], "cells_selected": gen["cells_selected"], "duration_s": gen["duration_s"],
            "cells": _strip(rows) + _brain_rows(cells["cells"]), "retain": {}}
        if arm == "ZMA":
            m["zma_hard_violations"], m["zma_hard_skipped"] = ctx.seed_runs[arm][seed]["hard_violations"], hard_skipped
        ctx.log(f"{arm} {seed}: {gen['cells_ran']}/{gen['cells_selected']} generic cells ran in {gen['duration_s']:.0f}s; hard violations "
                f"{len(ctx.seed_runs[arm][seed]['hard_violations'])}; hard skipped {hard_skipped}")
    ctx.log(f"{arm} cells with the clone brain: {summ.get('pass')}/{summ.get('graded')} graded pass; red {summ.get('fail')}; targets failing {summ.get('targets_failing')}; "
            f"skipped {summ.get('skipped')}; controls {m['arm_controls']} red; brain calls {(m['brain'] or {}).get('model_calls')}; {ctx.host.mono() - t0:.0f}s")


def phase_mpa(ctx: Ctx, seed: str, box_s: float, store: list, by_id: dict, instrument_of: "Callable[[str], dict]") -> None:
    _phase_driver_arm(ctx, "MPA", seed, box_s, store, by_id, instrument_of)


def phase_hma(ctx: Ctx, seed: str, box_s: float, store: list, by_id: dict, instrument_of: "Callable[[str], dict]") -> None:
    _phase_driver_arm(ctx, "HMA", seed, box_s, store, by_id, instrument_of)


def phase_zma(ctx: Ctx, seed: str, box_s: float, store: list, by_id: dict, instrument_of: "Callable[[str], dict]") -> None:
    _phase_driver_arm(ctx, "ZMA", seed, box_s, store, by_id, instrument_of)


# ── the REFLECTION phase: K only, against a restarted clone (variants, never contest entrants) ────────────────────────────────

class _PairStop(Exception):
    """One reflection pair is not run (a precondition failed, or MemAvailable fell below the floor once its model was up): the clone goes back to the live context."""


def unit_pss_mb(ctx: Ctx, unit: str) -> float:
    h = ctx.host
    cg = h.run(["systemctl", "--user", "show", "-p", "ControlGroup", "--value", unit], mutating=False).out.strip()
    procs = h.read(f"/sys/fs/cgroup{cg}/cgroup.procs") if cg else ""
    return round(sum(parse_pss_kb(h.read(f"/proc/{int(x)}/smaps_rollup")) for x in procs.split() if x.isdigit()) / 1024.0, 1)


def reflect_spec(ctx: Ctx, pair: str) -> "tuple[Optional[dict], str]":
    """The clone to start for ``pair``, GENERATED (never hand-written): the 4B from the live unit's text with ``--ctx-size``; the 12B from the PARKED unit's text (read, never
    enabled / modified / started). ``(None, reason)`` when a precondition fails: the pair is skipped with the reason, never faked."""
    from .bakeoff import Refused, clone_command, deep_clone_command
    cfg, host, home = ctx.cfg, ctx.host, os.environ.get("HOME", "/home/zoe")
    try:
        if pair == "4B@32k":
            return clone_command(ctx.win.unit_text, home, cfg.clone_port, cfg.reflect_ctx), ""
        text = host.read(cfg.deep_unit)
        if not text.strip():
            return None, f"the parked 12B unit {cfg.deep_unit} is missing or empty"
        spec = deep_clone_command(text, home, cfg.clone_port, cfg.reflect_ctx)
    except Refused as exc:
        return None, str(exc)
    for what, path in (("binary", spec["binary"]), ("model file", spec["model_path"])):
        if not host.exists(path):
            return None, f"the 12B {what} {path} does not exist"
    return spec, ""


def deep_preflight(ctx: Ctx, spec: dict) -> "tuple[bool, str]":
    """BEFORE the 12B starts: stop the 4B clone (this window's own), stop the owner's listed units, measure MemAvailable and compare with the arithmetic
    ``need = model file MB + KV estimate + 600 + floor``. ``(False, arithmetic)`` skips the pair (the caller restarts the 4B at the live context)."""
    win, cfg, host = ctx.win, ctx.cfg, ctx.host
    host.run(["systemctl", "--user", "stop", UNITS_CLONE], timeout=90)
    host.run(["systemctl", "--user", "reset-failed", UNITS_CLONE])
    if cfg.reflect_stop_units:
        win.stop_extra_units(cfg.reflect_stop_units)
    win.log_frag("after the 4B clone and the listed units stopped (before the 12B start)")
    avail = win.mem()
    size = host.file_size(spec["model_path"])
    need = need_mb_12b(size, cfg.reflect_ctx, cfg.min_avail_mb)
    arith = (f"need {need:.0f} MB = model {(size or REFLECT_12B_MODEL_BYTES) / 1e6:.0f} + KV {kv_est_mb(cfg.reflect_ctx):.0f} + compute {REFLECT_COMPUTE_MB:.0f} + floor {cfg.min_avail_mb:.0f}; "
             f"MemAvailable with the 4B stopped" + (f" and {', '.join(cfg.reflect_stop_units)} stopped" if cfg.reflect_stop_units else "") + f" = {avail:.0f} MB (margin {avail - need:+.0f} MB)")
    return avail >= need, arith


def _k1_veto(cells: "list[dict]") -> bool:
    """The observation veto for a variant: any K1 cell red, or an evidence precision below the rule's."""
    for c in cells:
        if str(c.get("id", "")).startswith(("K1", "MPA-K1")):
            prec = (c.get("evidence") or {}).get("precision")
            if c.get("verdict") in ("FAIL", "ERROR") or (prec is not None and float(prec) < gates.RULE["observation_precision_min"]):
                return True
    return False


def reflect_variant_h2(ctx: Ctx, seed: str, store: list, by_id: dict, instrument_of: "Callable[[str], dict]") -> dict:
    """H2 restricted to the reflection cells (K1-K5) on the restarted clone: the observation layer, consolidation and the mental-model refresh run against it."""
    from . import artifact, cells as cellmod, runner
    from .world import make_world
    world, arm, rows, t0 = make_world(seed), ctx.new_arm("H2"), [], ctx.host.mono()
    try:
        for cell in (c for c in store if c.axis == "reflection"):
            ctx.win.guard()
            rows.append(runner._row(cell, cellmod.run_cell(cell.rendered(world), world, arm)))
    finally:
        arm.close()
    st = artifact.axis_stats(rows, by_id, instrument_of(seed)["ok"]).get("reflection") or {}
    return {"k_cells": [{"id": r["id"], "verdict": r["verdict"]} for r in rows], "items": st.get("items") or {"pass": 0, "n": 0},
            "cells": {"pass": st.get("pass", 0), "n": st.get("n", 0)}, "wall_s": round(ctx.host.mono() - t0, 1), "model_calls": None, "tool_calls": None,
            "tool_calls_valid": None, "prompt_tokens_max": None, "k1_veto": _k1_veto(rows)}


def real_reflect_runner(ctx: Ctx, arm: str, seed: str, box_s: float, pair: str) -> dict:
    """``mpa_window.py --arm <HMA|ZMA> --reflect-only`` against the restarted clone (same port): re-runs the closet pass (and Hindsight's consolidation for HMA) and scores K."""
    out = ctx.cfg.bakeoff_dir / f"{arm.lower()}-reflect-{ctx.win.run_id}-{pair.replace('@', '-')}.json"
    argv = _mpa_argv(ctx, arm, out, seed, box_s, ("--reflect-only", "--ctx", str(ctx.cfg.reflect_ctx)))
    return _run_mpa_driver(ctx, arm, out, argv, box_s * 2 + 180.0)


def real_hma_reflect_runner(ctx: Ctx, seed: str, box_s: float, pair: str) -> dict:
    return real_reflect_runner(ctx, "HMA", seed, box_s, pair)


def real_zma_reflect_runner(ctx: Ctx, seed: str, box_s: float, pair: str) -> dict:
    return real_reflect_runner(ctx, "ZMA", seed, box_s, pair)


def reflect_variant_hma(ctx: Ctx, name: str, pair: str, seed: str, box_s: float) -> dict:
    """The reflection variant of an MPA-driver arm (HMA or ZMA: the arm is the name's prefix) through its driver."""
    arm = name.split("@")[0]
    res = (getattr(ctx.win, f"{arm.lower()}_reflect_runner", None) or {"HMA": real_hma_reflect_runner, "ZMA": real_zma_reflect_runner}[arm])(ctx, seed, box_s, pair)
    ctx.win.guard()
    why = res.get("aborted") or res.get("skipped") or res.get("error")
    r = res.get("reflect") or {}
    if why and not r:
        if res.get("aborted"):
            raise Aborted(str(res["aborted"]))
        return {"error": str(why)}
    cells = [{"id": c.get("id"), "verdict": c.get("verdict"), "evidence": c.get("evidence") or {}} for c in r.get("k_cells") or []]
    ip = sum(int((c["evidence"].get("items") or [0, 0])[0]) if isinstance(c["evidence"].get("items"), (list, tuple)) else 0 for c in cells)
    iN = sum(int(c["evidence"]["items"][1]) if isinstance(c["evidence"].get("items"), (list, tuple)) else 0 for c in cells)
    graded = [c for c in cells if c["verdict"] in ("PASS", "FAIL", "ERROR")]
    out = {"k_cells": [{"id": c["id"], "verdict": c["verdict"]} for c in cells], "items": {"pass": ip, "n": iN},
           "cells": {"pass": sum(1 for c in graded if c["verdict"] == "PASS"), "n": len(graded)}, "wall_s": r.get("wall_s"), "model_calls": r.get("model_calls"),
           "tool_calls": r.get("tool_calls"), "tool_calls_valid": r.get("tool_calls_valid"), "prompt_tokens_max": r.get("prompt_tokens_max"),
           "model": r.get("model"), "closet": r.get("closet"), "k1_veto": _k1_veto(cells)}
    if why:
        out["stopped_early"] = str(why)
    return out


def phase_reflect(ctx: Ctx, seed: str, store: list, by_id: dict, instrument_of: "Callable[[str], dict]") -> None:
    """The optional reflection phase. Runs only when the clone is a clone (not under BAKEOFF_SKIP_BRAIN_STOP), ``cfg.reflect_ctx`` > 0, and at least a pair's minutes remain
    behind the tail. For each pair (the 4B at ``reflect_ctx``, then the parked 12B): the clone is regenerated and restarted, its health polled, MemAvailable required >= the
    floor AFTER its model is up (else the pair is stopped and the clone goes back), then H2 restricted to K and HMA ``--reflect-only`` run against it. At the end the clone is
    restarted at the LIVE context. A guard that aborts (hard cap, a voice turn, a dead server) propagates: the window's restore puts the live unit back."""
    win, cfg, host = ctx.win, ctx.cfg, ctx.host
    if cfg.reflect_ctx <= 0:
        return
    if cfg.skip_brain_stop:
        ctx.reflect = {"ctx": cfg.reflect_ctx, "pairs": {}, "variants": {}, "why": "BAKEOFF_SKIP_BRAIN_STOP: there is no clone to restart (the live brain is shared)"}
        return
    pairs = [pr for pr in REFLECT_PAIRS if reflect_pair_min(pr, cfg.arms)]
    if not pairs:
        ctx.reflect = {"ctx": cfg.reflect_ctx, "pairs": {}, "variants": {}, "why": "neither H2 nor HMA is in this window"}
        return
    rec = ctx.reflect = {"ctx": cfg.reflect_ctx, "pairs": {}, "variants": {}, "clone_pss_8k_mb": unit_pss_mb(ctx, UNITS_CLONE)}
    swapped, stopped, at_live = False, False, True            # at_live: the clone runs the 4B at the LIVE context (nothing big is resident)
    for pair in pairs:
        need_s = reflect_pair_min(pair, cfg.arms) * 60.0
        left = win.time_left_s() - TAIL_MIN * 60.0
        info = rec["pairs"][pair] = {"status": "not run"}
        if stopped:
            info["status"] = "skipped: an earlier pair stopped on MemAvailable"
        elif left < need_s:
            info["status"] = f"skipped: {left / 60.0:.1f} min left behind the tail, the pair needs {need_s / 60.0:g}"
        else:
            spec, why = reflect_spec(ctx, pair)
            if spec is None:
                info["status"] = f"skipped: {why}"
            else:
                label = f"reflect:{pair}"
                ctx.label(label)
                swapped = True
                try:
                    if pair == "12B@32k":
                        fits, arith = deep_preflight(ctx, spec)
                        info["preflight"] = arith
                        if not fits:
                            raise _PairStop("the floor would be breached: " + arith)
                    t_load = host.mono()
                    at_live = False                                  # from here the big model may be resident, even if the swap itself raises half way
                    win.swap_clone(spec, pair, max(cfg.health_wait_s * (2.0 if pair == "12B@32k" else 1.0), 240.0))
                    info["health_s"] = round(host.mono() - t_load, 1)
                    info["model"] = spec["model"]
                    if win.mem() < cfg.min_avail_mb:
                        raise _PairStop(f"MemAvailable {win.mem():.0f} MB < {cfg.min_avail_mb:.0f} MB floor once the model was up")
                    info["clone_pss_mb"] = unit_pss_mb(ctx, UNITS_CLONE)
                    for name in REFLECT_VARIANTS[pair]:
                        arm = name.split("@")[0]
                        if arm not in cfg.arms:
                            continue
                        ctx.label(f"{label}:{arm}")
                        box = REFLECT_CALLS[name] * REFLECT_S_PER_CALL[pair] * 2.0
                        v = (reflect_variant_h2(ctx, seed, store, by_id, instrument_of) if arm == "H2" else reflect_variant_hma(ctx, name, pair, seed, box))
                        v.update({"pair": pair, "clone_pss_mb": info.get("clone_pss_mb"), "health_s": info.get("health_s")})
                        rec["variants"][name] = v
                        ctx.log(f"reflection {name}: K {v.get('cells') or v.get('error')}, items {v.get('items')}, {v.get('wall_s')}s, veto {v.get('k1_veto')}")
                    info["status"] = "ran"
                except (_PairStop, Aborted) as exc:
                    if isinstance(exc, Aborted) and not str(exc).startswith("MemAvailable"):
                        raise                                    # the cap, a voice turn, a dead server: the window aborts and its restore puts the live unit back
                    win.abort_flag = None                         # a memory stop on the big model: skip the pair, not the window; the clone goes back below
                    info["status"] = f"stopped: {exc}"
                    stopped = True
                    ctx.notes.append(f"reflection {pair} stopped: {exc}")
                finally:
                    if pair == "12B@32k":
                        # The listed units come back on EVERY exit path (pass, skip, abort) - but only AFTER the 12B is unloaded: Kokoro was stopped because the 12B pair does not
                        # fit beside it (docs/knowledge/zoe-memory-bench.md), so starting it while the 12B holds ~7.5 GB can fail or OOM. If the unload fails, the unit stays in
                        # ``stopped_extra`` and the window's restore (which stops the clone first) starts it.
                        if not at_live:
                            try:
                                win.restart_clone(None)
                                at_live = True
                            except Exception as exc:             # noqa: BLE001 - a finally must not mask the pair's own outcome; restore() retries the unload and the start
                                ctx.notes.append(f"reflection {pair}: the 12B was not unloaded ({exc}); the listed units stay stopped until the window's restore")
                        if at_live:
                            win.start_extra_units()
                    floors = [x["mem_available_mb"] for x in (ctx.sampler.samples if ctx.sampler else []) if x["label"].startswith(label)]
                    info["mem_available_floor_mb"] = round(min(floors), 0) if floors else None
        if info["status"].startswith("skipped"):
            ctx.notes.append(f"reflection {pair}: {info['status']}")
    if swapped and not at_live:
        win.restart_clone(None)                                    # back to the live context: the forgetting probes' t+6 replay and the report still use the clone
    ctx.log("reflection phase: " + "; ".join(f"{k} {v['status']}" for k, v in rec["pairs"].items()))


def phase_latency(ctx: Ctx, variant: str) -> None:
    from .arms.base import Turn
    ctx.label(f"{variant}:latency")
    arm = ctx.new_arm(variant)
    try:
        arm.reset("demo_bar_" + hashlib.sha1(f"lat-{variant}".encode()).hexdigest()[:8])
        arm.ingest([Turn(s, "owner_taught") for s in probe_sentences(LATENCY_FACTS)])
        arm.recall_ms.clear()
        queries = [f"where does {p} live" for p in _PEOPLE] + [f"who works at {t}" for t in _THINGS] + [f"tell me about {p}" for p in _PEOPLE]
        for i in range(LATENCY_QUERIES):
            ctx.win.guard()
            arm.recall(queries[i % len(queries)], 5)
        ms = list(arm.recall_ms)
    finally:
        arm.close()
    from .arms.hindsight import _percentile
    ctx.measure[variant]["recall"] = {"n": len(ms), "p50_ms": _percentile(ms, 0.5), "p95_ms": _percentile(ms, 0.95)}
    ctx.log(f"{variant} recall latency: n={len(ms)} p50 {_percentile(ms, 0.5):.0f} ms p95 {_percentile(ms, 0.95):.0f} ms")


def phase_validity(ctx: Ctx, mode: str, variants: "tuple[str, ...]", box_s: float) -> None:
    """>= 100 retain calls in ``mode`` on a scratch bank; call-level success plus the server's own LLM trace (attempt level)."""
    from .arms.hindsight import BANK_CONFIG, HindsightClient, HindsightError
    ctx.label(f"validity:{mode}")
    client = ctx.win.client_factory()
    bank = f"zmb-probe-{mode}"
    client.delete_bank(bank)
    client.put_bank(bank)
    client.patch_config(bank, {**BANK_CONFIG["H2" if mode == "concise" else "H1"], "enable_observations": False})
    ok = n = 0
    max_in, t_end = 0, ctx.host.mono() + box_s
    t_start_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    for s in probe_sentences(SMOKE_VALIDITY_CALLS if ctx.cfg.smoke_cells else VALIDITY_CALLS):
        ctx.win.guard()
        if ctx.host.mono() >= t_end:
            break
        n += 1
        try:
            r = client.retain(bank, [{"content": s, "document_id": f"v{n:04d}", "context": "the user is speaking"}])
            ok += 1
            max_in = max(max_in, int(((r or {}).get("usage") or {}).get("input_tokens") or 0))
        except HindsightError:
            pass
    trace = ""
    try:
        t = client.call("trace", "GET", f"/v1/default/banks/{bank}/llm-requests", params={"operation": "retain", "limit": 500})
        # the trace outlives its bank (a bank delete does not touch ``llm_requests``): rows of an EARLIER window's bank of the same name are
        # not this window's calls (measured: 23 rows for 20 calls after a 3-call smoke on the same bank name)
        rows = [x for x in (t.get("items") or t.get("requests") or []) if str(x.get("started_at") or "9") >= t_start_iso]
        if rows:
            good = sum(1 for x in rows if str(x.get("status")) == "success")
            trace = f"; LLM trace attempts {good}/{len(rows)} success"
            if len(rows) >= n:        # attempt-level is the stricter number: use it when it covers every call
                ok = min(ok, good)
    except (HindsightError, NotImplementedError):
        trace = "; llm trace unavailable"
    client.delete_bank(bank)
    for v in variants:
        ctx.measure[v]["json"] = {"calls": n, "valid": ok, "source": f"{mode} mode, retain on the 8,192 slot{trace}"}
        ctx.measure[v]["prompt_in_tokens_max"] = max_in
    ctx.log(f"{mode} extraction validity: {ok}/{n}{trace}; max prompt tokens {max_in}")


def phase_slot(ctx: Ctx, variant: str) -> None:
    """Brain-slot seconds per retained TURN under the design (an idle retain of a 10-turn chunk): delta of the clone's /metrics."""
    from .arms.hindsight import BANK_CONFIG
    ctx.label(f"{variant}:slot")
    client = ctx.win.client_factory()
    bank = f"zmb-probe-slot-{variant.lower()}"
    client.delete_bank(bank)
    client.put_bank(bank)
    client.patch_config(bank, {**BANK_CONFIG[variant], "enable_observations": False})
    chunk = " ".join(probe_sentences(SLOT_TURNS_PER_CHUNK))
    before, n = ctx.slot_seconds(), 0
    for i in range(SMOKE_SLOT_RETAINS if ctx.cfg.smoke_cells else SLOT_RETAINS):
        ctx.win.guard()
        client.retain(bank, [{"content": f"{chunk} ({i})", "document_id": f"s{i:03d}", "context": "the user is speaking"}])
        n += 1
    after = ctx.slot_seconds()
    client.delete_bank(bank)
    if before is not None and after is not None and n:
        ctx.measure[variant]["slot_s_per_turn"] = round((after - before) / (n * SLOT_TURNS_PER_CHUNK), 3)
        ctx.measure[variant]["slot_s_per_retain"] = round((after - before) / n, 2)
    ctx.log(f"{variant} brain-slot seconds per turn (idle retain of {SLOT_TURNS_PER_CHUNK}-turn chunks): {ctx.measure[variant].get('slot_s_per_turn')}")


# ── the schedule, the report ─────────────────────────────────────────────────

def cap_extra_min(arm: str) -> float:
    """Minutes the capability cells add to ``arm``'s FIRST seed box (0 for an arm that does not run them)."""
    if arm == "HM":
        return HM_CAP_MIN
    if arm in ("MPA", "HMA", "ZMA"):
        return 0.0                                  # their capability work (J, K, L, M) is INSIDE ``mpa_cells`` / ``hma_cells`` (computed from model calls), not an extra on the seed box
    if arm not in S_PER_RETAIN:
        return 0.0
    sec = sum(CAP_RETAINS[ax] * S_PER_RETAIN[arm] for ax in CAP_PLANNED.get(arm, ()))
    sec += CONSOLIDATE_S if "reflection" in CAP_PLANNED.get(arm, ()) else 0.0
    return round(sec / 60.0 * 2) / 2.0            # to the half minute


@dataclasses.dataclass
class Budget:
    """The phase budget of one window. ``avail_min`` = cap - the restore/report reserve - the tail - the window-open steps. Fixed phases are
    run 1's measured minutes; a seed's box is a CEILING (an arm that finishes early hands its slack to the arms behind it)."""
    avail_min: float
    store_cells: int
    runnable: int                                   # store-tier cells a LAYERED H arm (H1 / H2, with the scratch Postgres) can run (the rest SKIP: missing capability)
    arms: tuple
    seeds: dict                                      # arm -> seeds planned
    box_min: dict                                    # arm -> planned ceiling of ONE seed box, minutes
    runnable_h0: int = -1                            # the same for H0, which has no Zoe layer (no conflict_pass / edges); -1 = not computed
    runnable_hm: int = -1                            # the same for HM: clock / identities / idle_pass / verbatim / reader only
    extra_min: dict = dataclasses.field(default_factory=dict)   # arm -> minutes the capability cells add to its FIRST seed box (seeds 2 and 3 do not run them)
    runnable_mpa: int = -1                           # the same for MPA / HMA (store cells whose required capabilities the arm declares)
    runnable_hma: int = -1
    runnable_zma: int = -1
    reflect_min: dict = dataclasses.field(default_factory=dict)  # reflection pair ("4B@32k" / "12B@32k") -> minutes; OPTIONAL (runs only if time remains), never in ``total_min``

    def runnable_for(self, arm: str) -> int:
        if arm == "HM" and self.runnable_hm >= 0:
            return self.runnable_hm
        if arm == "MPA" and self.runnable_mpa >= 0:
            return self.runnable_mpa
        if arm == "HMA" and self.runnable_hma >= 0:
            return self.runnable_hma
        if arm == "ZMA" and self.runnable_zma >= 0:
            return self.runnable_zma
        return self.runnable_h0 if arm == "H0" and self.runnable_h0 >= 0 else self.runnable

    def fixed_min(self, arm: str) -> float:
        """Budgeted latency + slot minutes for the arm (the extraction-validity phase is per MODE, see ``validity_min``). 0 for an optional arm.
        HM has neither phase (its latency is measured inside its cells, on the real tiers): its fixed time is the HM cells."""
        if arm == "HM":
            return PHASE_MIN["hm_cells"]
        if arm == "MPA":
            return mpa_cells_min()                  # computed from MPA_CALLS x MPA_S_PER_CALL (PHASE_MIN["mpa_cells"] holds the same number at import)
        if arm == "HMA":
            return hma_cells_min()
        if arm == "ZMA":
            return zma_cells_min()
        return 0.0 if arm in OPTIONAL_PHASES else PHASE_MIN["latency"][arm] + PHASE_MIN["slot"][arm]

    def validity_min(self) -> float:
        return ((PHASE_MIN["validity"]["verbatim"] if "H1" in self.arms and "verbatim" not in OPTIONAL_VALIDITY else 0.0)
                + (PHASE_MIN["validity"]["concise"] if any(a != "H1" for a in self.arms) and "concise" not in OPTIONAL_VALIDITY else 0.0))

    def slack_min(self) -> float:
        return self.avail_min - self.total_min()

    def reflect_reserve_s(self) -> float:
        """Seconds the optional reflection phase would like to keep for itself at the end: taken from H0's box first, then HM's (never from H1, H2, MPA or HMA)."""
        return sum(self.reflect_min.values()) * 60.0

    def cells_in_box(self, arm: str) -> int:
        return int(self.box_min[arm] * 60.0 / S_PER_CELL[arm])

    def total_min(self) -> float:
        return (PHASE_MIN["lab"] + PHASE_MIN["z0e"] + self.validity_min()
                + sum(self.fixed_min(a) + self.seeds[a] * self.box_min[a] + self.extra_min.get(a, 0.0) for a in self.arms))

    def seed_box_s(self, arm: str, time_left_s: float, first: bool = True) -> float:
        """The ceiling for the next seed box of ``arm`` in seconds. H1: its planned ceiling. A lower arm: whatever is left behind the
        work still queued for it and for the arms after it (a lower arm may use up to 2x its plan when H1 finished early, never more).
        ``first`` = seed 1, whose box also holds the capability cells."""
        planned = (self.box_min[arm] + (self.extra_min.get(arm, 0.0) if first else 0.0)) * 60.0
        if arm == "H1" or arm not in ARM_ORDER:
            return min(planned, time_left_s)
        later = [a for a in ARM_ORDER[ARM_ORDER.index(arm) + 1:] if a in self.arms]
        concise_here = (arm == "H2" or (arm in ("H0", "HM") and "H2" not in self.arms)) and "concise" not in OPTIONAL_VALIDITY
        queued = (self.fixed_min(arm) + (PHASE_MIN["validity"]["concise"] if concise_here else 0.0)
                  + sum(self.fixed_min(a) + 2.0 for a in later)) * 60.0
        left = max(0.0, time_left_s - queued)
        frac = 2.0 / 3.0 if later else 1.0
        box = min(2.0 * planned, left * frac)
        reserve = self.reflect_reserve_s()
        if reserve and arm == "H0":                                  # the optional reflection phase's minutes come out of H0's box first (it may go to nothing) ...
            box = min(2.0 * planned, max(0.0, left - reserve) * frac)
        elif reserve and arm == "HM" and "H0" in self.arms:          # ... then out of HM's, for what H0's whole box cannot cover, but never below the 1 min floor that keeps HM running
            cut = max(0.0, reserve - (self.box_min.get("H0", 0.0) + self.extra_min.get("H0", 0.0)) * 60.0)
            box = max(min(2.0 * planned, max(0.0, left - cut) * frac), min(box, BOX_FLOOR_MIN * 60.0))
        return box


#: the share of the spare minutes each lower arm's seed box gets when MPA / HMA are in the window. Said plainly: H2 the biggest (its cells are the observation layer's), HM next (a
#: candidate with no model call on its write), MPA and HMA a modest box each (their generic store cells mostly need the brain on every turn, and their own cells phase is fixed
#: apart), H0 the least (the native baseline cannot win). When the plan does not fit, the boxes sit on their floor and the SHEDDING ORDER is H0's, then HM's, then H2's: never H1's
#: three seeds. Without MPA / HMA the older (2.0, 1.0) / (2.0, 1.5, 0.5) weights stand.
LOWER_WEIGHTS = {"H2": 2.0, "HM": 1.5, "MPA": 0.75, "HMA": 0.75, "ZMA": 1.0, "H0": 0.5}      # ZMA: a normal box like HM's (no model call on its write path), a little under
BOX_FLOOR_MIN = 1.0


def _driver_capabilities(arm: str) -> "Optional[set]":
    """The capabilities MPA / HMA declare (None when the arm module cannot be imported here: the planner then counts every ordinary cell as runnable)."""
    try:
        if arm == "MPA":
            from .arms.mempalace_agent import MemPalaceAgentArm
            return set(MemPalaceAgentArm.capabilities)
        if arm == "ZMA":
            try:
                from .arms.zma import ZMAArm
                return set(ZMAArm.capabilities)
            except ImportError:                                   # ZMA = Z0's capabilities + MPA's, until its own module lands
                from .arms.mempalace_agent import MemPalaceAgentArm
                from .arms.z0 import Z0Arm
                return set(Z0Arm.capabilities) | set(MemPalaceAgentArm.capabilities)
        try:
            from .arms.hma import HMAArm
            return set(HMAArm.capabilities)
        except ImportError:                                   # HMA = MPA's tier + Hindsight H2-style: the union until its own module lands
            from .arms.hindsight import HindsightArm
            from .arms.mempalace_agent import MemPalaceAgentArm
            return set(MemPalaceAgentArm.capabilities) | set(HindsightArm.capabilities)
    except Exception:  # noqa: BLE001 - a planner must not die on an arm module that fails to import
        return None


def plan_budget(cfg: Any, store: "Optional[list]" = None, runnable: "Optional[int]" = None) -> Budget:
    """Three seeds of H1 inside the cap, one seed each for the other arms, the hard cap kept. ``store`` = the store-tier cells; the cells an H arm
    can run are those whose required capabilities it declares (the rest SKIP with the reason and cost nothing). When the arms ask for more than the cap holds
    (``Budget.slack_min() < 0``) the lower arms' boxes sit on ``BOX_FLOOR_MIN`` and the dry run says so: the plan never hides a deficit."""
    arms = tuple(a for a in ARM_ORDER if a in cfg.arms)
    if store is None:
        from . import spec
        store = [c for c in spec.load_cells() if c.tier == "store"]
    from . import cells as cellmod
    from .arms.hindsight import HindsightArm
    layered = set(HindsightArm.capabilities)
    ordinary = [c for c in store if c.axis not in CAP_AXES]       # the base box is sized from the cells run 1 measured; the capability cells are budgeted apart
    if runnable is None:
        runnable = sum(1 for c in ordinary if cellmod.required_capabilities(c) <= layered)
    runnable_h0 = sum(1 for c in ordinary if cellmod.required_capabilities(c) <= layered - {"conflict_pass", "edges"})
    from .arms.hm import HMArm
    runnable_hm = sum(1 for c in ordinary if cellmod.required_capabilities(c) <= set(HMArm.capabilities))
    caps = {a: _driver_capabilities(a) for a in ("MPA", "HMA", "ZMA") if a in arms}
    # ZMA's seed box holds ALL store cells (axes A-M: its write path has no model call), the others the ordinary ones (their capability cells run inside their own cells phase)
    runnable_d = {a: (-1 if c is None else sum(1 for x in (store if a == "ZMA" else ordinary) if cellmod.required_capabilities(x) <= c)) for a, c in caps.items()}
    avail = cfg.cap_min - cfg.reserve_min - TAIL_MIN - OPEN_MIN
    seeds = {a: SEEDS_PER_ARM[a] for a in arms}
    h1 = round(max(5.0, runnable * S_PER_CELL["H1"] * H1_BOX_MARGIN / 60.0) * 2) / 2.0          # to the half minute
    box = {"H1": h1} if "H1" in arms else {}
    extra = {a: cap_extra_min(a) for a in arms}
    reflect = ({pr: reflect_pair_min(pr, arms) for pr in REFLECT_PAIRS if reflect_pair_min(pr, arms)}
               if getattr(cfg, "reflect_ctx", 0) > 0 and not cfg.skip_brain_stop else {})
    kw = dict(runnable_mpa=runnable_d.get("MPA", -1), runnable_hma=runnable_d.get("HMA", -1), runnable_zma=runnable_d.get("ZMA", -1), reflect_min=reflect)
    draft = Budget(avail, len(ordinary), runnable, arms, seeds, {a: 0.0 for a in arms}, runnable_h0, runnable_hm, extra, **kw)
    spare = (avail - PHASE_MIN["lab"] - PHASE_MIN["z0e"] - draft.validity_min() - sum(draft.fixed_min(a) for a in arms) - seeds.get("H1", 0) * box.get("H1", 0.0)
             - sum(extra.values()))
    lower = [a for a in arms if a != "H1"]
    if "MPA" in arms or "HMA" in arms or "ZMA" in arms:
        weights = tuple(LOWER_WEIGHTS[a] for a in lower)
    else:
        weights = {2: (2.0, 1.0), 3: (2.0, 1.5, 0.5)}.get(len(lower), (1.0,) * len(lower))      # H2 the biggest share, then HM (a candidate), H0 (the native baseline) the least
    for a, w in zip(lower, weights):
        box[a] = math.floor(max(BOX_FLOOR_MIN, spare * w / sum(weights)) * 2) / 2.0               # rounded DOWN: the plan must fit the cap, not just touch it
    return Budget(avail, len(ordinary), runnable, arms, seeds, {a: box.get(a, 0.0) for a in arms}, runnable_h0, runnable_hm, extra, **kw)


def plan_table(cfg: Any, seeds: tuple, budget: "Optional[Budget]" = None) -> "list[tuple[str, float, str]]":
    """The schedule in EXECUTION order: ``(what, minutes, note)``."""
    b = budget or plan_budget(cfg)
    rows = [("Z0 + Z0-off in the lab on 3 seeds, forgetting probes start (t+0), adapter negative controls", PHASE_MIN["lab"],
             "control, negative control; the real t+6 min check runs later between cells"),
            ("Z0e (real Chroma + MiniLM) on the 4 recall (D) + 3 long-range (L) cells x 3 seeds", PHASE_MIN["z0e"], "the D and L axes' baseline: real retrieval on both engines")]
    concise_done = False
    for a in b.arms:
        if a == "HM":
            rows.append(("HM cells on the REAL tiers (MemPalace 3.10.0 library + Hindsight): controls on the real-tier protections, wall-clock latencies",
                         PHASE_MIN["hm_cells"], "run in the bake-off venv by hm_window.py; HM-F8 scans Hindsight's Postgres"))
        if a in ("MPA", "HMA", "ZMA"):
            calls = driver_calls(a)
            rows.append((f"{a} cells on the clone brain: " + ", ".join(f"{k} {v}" for k, v in calls.items()) + f" = {sum(calls.values())} model calls x {MPA_S_PER_CALL:g} s"
                         + (f" + {CONSOLIDATE_S:g} s Hindsight consolidation" if a == "HMA" else ""), b.fixed_min(a),
                         ("run with the window's own interpreter (--arm ZMA: Z0 needs zoe-data's modules; MemPalace is a subprocess) by mpa_window.py" if a == "ZMA" else "run in the bake-off venv by mpa_window.py" + (" --arm HMA" if a == "HMA" else ""))
                         + ": J / K / L / M and the arm's own floors INSIDE these minutes (no separate capability row); "
                         f"{a} runs L0 only (no 100/300 filler: each filler turn is a brain call)"))
        rows.append((f"{a} seed 1 ({seeds[0]}): " + ("ALL store-tier cells (axes A-M: its write path has no model call)" if a == "ZMA" else "store-tier cells"), b.box_min[a],
                     f"ceiling; ~{b.cells_in_box(a)} of {b.runnable_for(a)} runnable cells at {S_PER_CELL[a]:g} s/cell " + ("(estimate: the brain is called per turn)" if a in ("MPA", "HMA") else "(Z0e's rate)" if a == "ZMA" else "(run 1)")))
        if b.extra_min.get(a):
            rows.append((f"{a} seed 1: capability cells ({', '.join(CAP_PLANNED[a])})", b.extra_min[a],
                         "exact words / reflection / long-range recall / protocol, seed 1 only: " + ("no model call on the verbatim write" if a == "HM"
                                                                                                     else f"~{sum(CAP_RETAINS[x] for x in CAP_PLANNED[a])} retained calls x {S_PER_RETAIN[a]:g} s"
                                                                                                     + (f" + {CONSOLIDATE_S:g} s consolidation" if "reflection" in CAP_PLANNED[a] else ""))))
        opt = a in OPTIONAL_PHASES
        why = f"only if time remains; ~{PHASE_MIN['latency'][a]:g} min at run 1's rate, not budgeted" if opt else "p50/p95 through the shim and Postgres"
        if a not in DRIVER_ARMS:           # HM / MPA / HMA: latency and slot are measured inside their own cells (wall clocks on the real tiers): no separate phase
            rows.append((f"{a} recall latency n=50", 0.0 if opt else PHASE_MIN["latency"][a], why))
        mode = "verbatim" if a == "H1" else "concise"
        if (a == "H1" or not concise_done) and a != "MPA":
            opt_v = mode in OPTIONAL_VALIDITY
            rows.append((f"extraction JSON validity, {mode} (>= 100 retain calls)", 0.0 if opt_v else PHASE_MIN["validity"][mode],
                         (f"only if time remains; ~{PHASE_MIN['validity'][mode]:g} min, not budgeted (CUT for the capability cells; H2 / H0 are INCOMPLETE by design)" if opt_v
                          else "shared by H0 and H2" if mode == "concise" else "")))
            concise_done = concise_done or mode == "concise"
        if a not in DRIVER_ARMS:
            rows.append((f"{a} brain-slot seconds per retained turn", 0.0 if opt else PHASE_MIN["slot"][a],
                         f"only if time remains; ~{PHASE_MIN['slot'][a]:g} min at run 1's rate, not budgeted" if opt else "idle retain of 10-turn chunks"))
        for k in range(2, b.seeds[a] + 1):
            rows.append((f"{a} seed {k} ({seeds[k - 1]}): store-tier cells", b.box_min[a], "ceiling, same box as seed 1 (the capability cells ran on seed 1)"))
    for pr, mins in b.reflect_min.items():
        names = [v for v in REFLECT_VARIANTS[pr] if v.split("@")[0] in b.arms]
        rows.append((f"reflection phase, {pr}: restart the clone with the K work only ({', '.join(names)})", 0.0,
                     f"OPTIONAL, only if >= {mins:g} min remain behind the tail (not budgeted); H0's box is cut first, then HM's, to leave it room"))
    rows.append(("t+6 min forgetting verdicts, report", 0.0, f"inside the {TAIL_MIN:g} min tail, not counted"))
    return rows


def driver_call_headroom(b: Budget) -> "Optional[int]":
    """How many MPA + HMA brain calls the cap would hold at ``MPA_S_PER_CALL`` with every OTHER planned minute as it is (None when neither arm is in the plan)."""
    names = [a for a in ("MPA", "HMA", "ZMA") if a in b.arms]
    if not names:
        return None
    room_min = b.avail_min - (b.total_min() - sum(b.fixed_min(a) for a in names))
    return max(0, int((room_min * 60.0 - (CONSOLIDATE_S if "HMA" in names else 0.0)) / MPA_S_PER_CALL))


def cost_sentence(cfg: Any, arm: str, store: list, with_b: Budget) -> str:
    """``WHAT <arm> COSTS AND WHAT WAS CUT``: plan_budget called with and without ``arm``; the minutes it adds and exactly which other boxes shrank."""
    without_b = plan_budget(dataclasses.replace(cfg, arms=tuple(a for a in cfg.arms if a != arm)), store)
    added = with_b.fixed_min(arm) + with_b.seeds[arm] * with_b.box_min[arm] + with_b.extra_min.get(arm, 0.0)
    calls = driver_calls(arm)
    shrunk = [f"{a} box {without_b.box_min[a]:g} -> {with_b.box_min[a]:g} min ({with_b.box_min[a] - without_b.box_min[a]:+g})" for a in without_b.arms
              if a != "H1" and with_b.box_min.get(a) != without_b.box_min[a]]
    return (f"WHAT {arm} COSTS AND WHAT WAS CUT: {arm} adds {added:.1f} min ({with_b.fixed_min(arm):g} min of cells on the clone brain = {sum(calls.values())} model calls x "
            f"{MPA_S_PER_CALL:g} s" + (f" + {CONSOLIDATE_S:g} s consolidation" if arm == "HMA" else "") + f", plus a {with_b.box_min[arm]:g} min store-cell box); against the plan without {arm}: "
            + ("; ".join(shrunk) if shrunk else ("no other box shrank" + (" (the plan is over the cap either way: the lower boxes were already on their floor without it, see DOES NOT FIT)" if without_b.slack_min() < 0 else "")))
            + f"; H1 keeps {with_b.seeds.get('H1', 0)} seeds x {with_b.box_min.get('H1', 0):g} min "
            f"(H1's box {'unchanged' if with_b.box_min.get('H1') == without_b.box_min.get('H1') else 'CHANGED'}); the cut order when the plan is over is H0's box, then HM's, then H2's, never H1's seeds")


def together_sentence(cfg: Any, names: "list[str]", store: list, with_b: Budget) -> str:
    """``WHAT MPA + HMA + ZMA COST TOGETHER``: plan_budget with and without every new arm at once; the minutes they add and which other boxes shrank."""
    without_b = plan_budget(dataclasses.replace(cfg, arms=tuple(a for a in cfg.arms if a not in names)), store)
    added = sum(with_b.fixed_min(a) + with_b.seeds[a] * with_b.box_min[a] + with_b.extra_min.get(a, 0.0) for a in names)
    shrunk = [f"{a} box {without_b.box_min[a]:g} -> {with_b.box_min[a]:g} min ({with_b.box_min[a] - without_b.box_min[a]:+g})" for a in without_b.arms
              if a != "H1" and with_b.box_min.get(a) != without_b.box_min[a]]
    return (f"WHAT {' + '.join(names)} COST TOGETHER AND WHAT WAS CUT: together they add {added:.1f} min ({', '.join(f'{a} {with_b.fixed_min(a):g} cells + {with_b.box_min[a]:g} box' for a in names)}); "
            f"against the plan without them: " + ("; ".join(shrunk) if shrunk else "no other box shrank") + f"; H1 keeps {with_b.seeds.get('H1', 0)} seeds x {with_b.box_min.get('H1', 0):g} min "
            f"(H1's box {'unchanged' if with_b.box_min.get('H1') == without_b.box_min.get('H1') else 'CHANGED'}); slack {with_b.slack_min():+.1f} min")


def dry_plan(win: Any) -> dict:
    cfg = win.cfg
    seeds = seeds_for(win.run_id)
    from . import spec as specmod
    store = [c for c in specmod.load_cells() if c.tier == "store"]
    b = plan_budget(cfg, store)
    win.log("PLAN (the measurement phases, in execution order; est. minutes from run 1's measured rates; H1 first and complete):")
    total = 0.0
    for name, mins, note in plan_table(cfg, seeds, b):
        total += mins
        win.log(f"  {mins:5.1f}  {name}" + (f"   [{note}]" if note else ""))
    win.log(f"  {total:5.1f}  total planned work; hard cap {cfg.cap_min:.0f} min; {cfg.reserve_min:.0f} min always kept for restore + report; "
            f"{TAIL_MIN:.0f} min tail; {b.avail_min:.1f} min available for the phases above (slack {b.avail_min - b.total_min():+.1f})")
    win.log("per-arm cell budget (cells per seed box at run 1's seconds per cell): " + "; ".join(
        f"{a} {b.seeds[a]} seed{'s' if b.seeds[a] > 1 else ''} x {b.box_min[a]:g} min = ~{b.cells_in_box(a)}/{b.runnable_for(a)} runnable cells each"
        + (" (all 3 seeds complete inside the cap)" if a == "H1" and b.cells_in_box(a) >= b.runnable else "")
        for a in b.arms) + f"; {b.store_cells - b.runnable} of {b.store_cells} ordinary store cells SKIP by capability on H1 / H2 (the Zoe layer runs the conflict pass and "
        f"the people graph, the scratch Postgres gives them the disk cells)" + (f"; {b.store_cells - b.runnable_h0} on H0 (no Zoe layer: conflict_pass / edges)"
                                                                               if "H0" in b.arms and b.runnable_h0 >= 0 else ""))
    win.log("CUT to fit the capability axes under the cap: " + "; ".join(
        f"{a}: {', '.join(CAP_CUT[a]) or 'nothing'} cut" for a in b.arms if a in CAP_CUT)
        + f"; H2 / H0 latency + slot phases and the concise validity phase only if time remains (-{PHASE_MIN['latency']['H2'] + PHASE_MIN['slot']['H2'] + PHASE_MIN['validity']['concise']:g} min);"
        " the capability cells run on seed 1 only")
    new_arms = [a for a in ("MPA", "HMA", "ZMA") if a in b.arms]
    if new_arms:
        win.log(f"MPA / HMA / ZMA brain-call constants (bakeoff_measure.py: the only place they live): MPA_CALLS = {MPA_CALLS} ({sum(MPA_CALLS.values())} calls); HMA_CALLS = {HMA_CALLS} "
                f"({sum(HMA_CALLS.values())} calls); ZMA_CALLS = {ZMA_CALLS} ({sum(ZMA_CALLS.values())} calls); MPA_S_PER_CALL = {MPA_S_PER_CALL:g} s -> mpa_cells {mpa_cells_min():g} min, "
                f"hma_cells {hma_cells_min():g} min, zma_cells {zma_cells_min():g} min")
        for a in new_arms:
            win.log(cost_sentence(cfg, a, store, b))
        if len(new_arms) > 1:
            win.log(together_sentence(cfg, new_arms, store, b))
        win.log("RUN-2 ARM LIST: " + ", ".join(f"{a} ({b.seeds[a]} seed{'s' if b.seeds[a] > 1 else ''}, box {b.box_min[a]:g} min" + (f" + {b.fixed_min(a):g} min cells" if b.fixed_min(a) else "") + ")"
                                                 for a in b.arms) + " (Z0, Z0-off and Z0e always run in the lab)")
        head = driver_call_headroom(b)
        if b.slack_min() < 0:
            need = b.total_min() + cfg.reserve_min + TAIL_MIN + OPEN_MIN
            win.log(f"DOES NOT FIT: the planned work is {b.total_min():.1f} min and the cap leaves {b.avail_min:.1f} (over by {-b.slack_min():.1f}) with every lower arm's box already on its "
                    f"{BOX_FLOOR_MIN:g} min floor. To hold this plan as written the cap would be about {need:.0f} min (BAKEOFF_CAP_MIN / --cap-min); inside {cfg.cap_min:.0f} min the cap holds at most "
                    f"~{head} MPA + HMA + ZMA brain calls at {MPA_S_PER_CALL:g} s (the plan asks for {sum(sum(driver_calls(a).values()) for a in new_arms)}): "
                    "cut MPA_CALLS / HMA_CALLS / ZMA_CALLS or an arm, or raise the cap; at run time an arm that gets less than a minute of box is skipped, H0 first")
        else:
            win.log(f"FITS: slack {b.slack_min():+.1f} min with MPA / HMA in the plan (the cap would hold ~{head} MPA + HMA brain calls at {MPA_S_PER_CALL:g} s)")
    if b.reflect_min:
        size = None
        try:
            from .bakeoff import deep_clone_command
            size = win.host.file_size(deep_clone_command(win.host.read(cfg.deep_unit), os.environ.get("HOME", "/home/zoe"), cfg.clone_port, cfg.reflect_ctx)["model_path"])
        except Exception:  # noqa: BLE001 - the dry plan falls back to the header-measured constant
            size = None
        need = need_mb_12b(size, cfg.reflect_ctx, cfg.min_avail_mb)
        listed = sum(REFLECT_STOP_UNIT_MB.get(u, 0.0) for u in cfg.reflect_stop_units)
        win.log(f"12B PREFLIGHT (before anything is stopped for it): need {need:.0f} MB = model {(size or REFLECT_12B_MODEL_BYTES) / 1e6:.0f} + KV {kv_est_mb(cfg.reflect_ctx):.0f} + compute "
                f"{REFLECT_COMPUTE_MB:.0f} + floor {cfg.min_avail_mb:.0f}; MemAvailable with the 4B stopped is about {REFLECT_AFTER_STOP_MB:.0f} MB, so the margin is "
                f"{REFLECT_AFTER_STOP_MB + listed - need:+.0f} MB: " + ("expected to fit" if REFLECT_AFTER_STOP_MB + listed - need >= 0 else "expected: skip unless extra headroom")
                + f"; with kokoro-tts.service stopped for the 12B pair (BAKEOFF_REFLECT_STOP_UNITS=kokoro-tts.service, default none) the margin = "
                f"{REFLECT_AFTER_STOP_MB + REFLECT_STOP_UNIT_MB['kokoro-tts.service'] - need:+.0f} MB; units stopped for the pair: "
                f"{', '.join(cfg.reflect_stop_units) or 'none'} (started again on every exit path; nothing unlisted is ever stopped); the pair is skipped with this arithmetic when the floor would be breached")
        steps = (f"REFLECTION PHASE (optional: it runs only if >= its minutes remain behind the tail; H0's box is cut first, then HM's, to leave room): after the live-slot phases and "
                 f"before the t+6 min wait, for each pair ({', '.join(f'{k} {v:g} min' for k, v in b.reflect_min.items())}; total {sum(b.reflect_min.values()):g}) the window "
                 f"(1) records the clone's PSS at the live context, (2) stops the clone and starts it from the live unit's text with --ctx-size {cfg.reflect_ctx} (the 12B pair: from the PARKED "
                 f"unit {Path(cfg.deep_unit).name}, read only, its ExecStart verbatim with only --host 127.0.0.1 / --port {cfg.clone_port} / --ctx-size {cfg.reflect_ctx} / --parallel 1 and the "
                 f"vision flags dropped; skipped with the reason if its binary or model file is missing), (3) polls /health (x2 for the 12B load), (4) requires MemAvailable >= {cfg.min_avail_mb:.0f} MB "
                 "once the model is up (else the pair is stopped), (5) runs ONLY the K work for H2@, HMA@ and ZMA@ (variants, never contest entrants), (6) at the end restarts the clone at the LIVE "
                 f"context; on any abort the normal restore stops the clone and starts {cfg.unit} at its own ctx-size, which is never edited")
        win.log(steps)
        win.log("reflection constants: REFLECT_CALLS = " + str(REFLECT_CALLS) + "; REFLECT_S_PER_CALL = " + str(REFLECT_S_PER_CALL) + "; REFLECT_LOAD_MIN = " + str(REFLECT_LOAD_MIN))
    win.log("INSTRUMENT hindsight_consolidation_prompt_tokens_max: read from hindsight-api's journal at the end of the measurement (every exceed_context_size_error's n_prompt_tokens); "
            "recorded in run-<id>.json and the report, with the caveat 'Hindsight consolidation does not fit the 8,192-token live slot' when any consolidation call exceeded the slot")
    win.log("seeds: " + ", ".join(seeds))
    win.log(f"outputs: {cfg.bakeoff_dir}/run-{win.run_id}.log, run-{win.run_id}.json, <docs>/bakeoff-run-{win.run_id}.md")
    win.log("RESTORE (always, on every exit path): stop zoe-bakeoff-hindsight/-gemma/-embed, docker compose down, "
            f"systemctl --user start {cfg.unit}, poll :{cfg.live_port}/health, release the lock")
    return {"dry_run": True}


def make_factories(win: Any) -> None:
    """Real arm / client factories (tests replace them with ones over ``FakeHindsight``)."""
    from .arms.hindsight import HindsightArm, HindsightClient
    from .arms.pg_store import ScratchPostgres
    base = f"http://127.0.0.1:{win.cfg.hs_port}"
    if not hasattr(win, "arm_factory"):         # every arm gets a handle on the window's scratch Postgres: that is what makes the F5 / F6 disk cells runnable
        win.arm_factory = lambda variant, **kw: HindsightArm(variant, base_url=base, pg=ScratchPostgres(PG_CONTAINER), **kw)
    if not hasattr(win, "client_factory"):
        win.client_factory = lambda: HindsightClient(base)


EGRESS_RX = re.compile(r"^(\d\d):(\d\d):(\d\d) pid=(\d+) (ok|VIOLATION) (\S+)(?: (.*))?\Z")


def parse_egress(text: str) -> "Optional[dict]":
    """The hook log as counts, or None when it holds no usable line (a missing or empty log is NOT zero: it is not measured).
    ``observed`` counts connect and DNS events only; the hook-loaded line is liveness. A log with that line but no event is
    observed == 0 and callers read it as not measured too: the server always talks to Postgres, so an instrument that saw nothing was blind."""
    entries, loaded = [], False
    for ln in (text or "").splitlines():
        m = EGRESS_RX.match(ln.strip())
        if not m:
            continue
        tod = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        kind = m.group(6)
        if kind == "hook-loaded":
            loaded = True
            continue
        entries.append({"tod": tod, "violation": m.group(5) == "VIOLATION", "kind": kind, "what": (m.group(7) or "")[:120]})
    if not entries and not loaded:
        return None
    return {"hook_loaded": loaded, "observed": len(entries), "entries": entries}


def count_violations(path: Path) -> "Optional[int]":
    """Non-loopback connects the in-process egress hook logged. None = nothing usable in the log: that is NOT zero, it is not measured."""
    try:
        parsed = parse_egress(path.read_text(errors="ignore"))
    except OSError:
        return None
    if parsed is None or not parsed["observed"]:
        return None
    return sum(1 for e in parsed["entries"] if e["violation"])


def phase_arm(label: str) -> str:
    """The arm a phase label belongs to (``H1:zmb-v1`` -> H1); a phase that serves every arm over the shared server is ``shared``."""
    head = label.split(":", 1)[0]
    return head if head in ("H0", "H1", "H2", "HM", "MPA", "HMA", "ZMA") else "shared"


def attribute_egress(parsed: dict, marks: "list[tuple[float, str]]", ref_epoch: float) -> "dict[str, dict]":
    """Per arm (``shared`` = lab and validity phases, ``setup`` = before the first phase) the observed events and violations, by the
    WALL-CLOCK phase each event fell in (the hook stamps only the time of day; ``marks`` = (epoch, label) of each phase start). All arms
    share one Hindsight process, so this says WHICH phase made a connection, never that another arm was clear of it."""
    ref = dt.datetime.fromtimestamp(ref_epoch)
    ref_s = ref.hour * 3600 + ref.minute * 60 + ref.second
    out: "dict[str, dict]" = {}
    ordered = sorted(marks)
    for e in parsed["entries"]:
        delta = ((e["tod"] - ref_s + 43200) % 86400) - 43200          # the signed distance to the measurement start, across midnight
        t = ref_epoch + delta
        label = "setup"
        for when, name in ordered:
            if when <= t:
                label = name
        arm = "setup" if label == "setup" else phase_arm(label)
        d = out.setdefault(arm, {"observed": 0, "violations": 0})
        d["observed"] += 1
        d["violations"] += 1 if e["violation"] else 0
    return out


def egress_summary(path: Path, marks: "list[tuple[float, str]]", ref_epoch: float, ss_peers: int) -> "dict[str, Any]":
    """What the egress gate reads: ``connects`` (None = not measured), the plain detail line, and the per-arm split."""
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        text = ""
    parsed = parse_egress(text)
    if parsed is None:
        return {"connects": None, "observed": 0, "by_arm": {},
                "detail": f"not measured: no egress hook log at {path.name}: the hook never wrote (a hook that wrote nothing proves nothing)"}
    if not parsed["observed"]:
        return {"connects": None, "observed": 0, "by_arm": {},
                "detail": f"not measured: {path.name} shows the hook loaded but no connect at all: the instrument was blind, so this is not a zero"}
    by_arm = attribute_egress(parsed, marks, ref_epoch)
    hook_v = sum(1 for e in parsed["entries"] if e["violation"])
    total = hook_v + ss_peers
    split = ", ".join(f"{a} {d['observed']}" + (f" ({d['violations']} non-loopback)" if d["violations"] else "") for a, d in sorted(by_arm.items()))
    peers = sorted({e["what"] for e in parsed["entries"] if e["violation"]})[:3]
    detail = (f"{total} non-loopback connects over {parsed['observed']} observed (hook {hook_v}; ss sampler {ss_peers} peers; by phase: {split})"
              + (f"; first: {'; '.join(peers)}" if peers else ""))
    return {"connects": total, "observed": parsed["observed"], "detail": detail, "by_arm": by_arm}


def hm_glue_lines() -> int:
    """Non-blank, non-comment lines of the HM arm's own glue (``arms/hm.py`` + ``arms/hm_policy.py``): decision-rule G3's 'Zoe layer' for HM (the H arms' ``ZoeLayer`` is not used by HM)."""
    d = Path(__file__).resolve().parent / "arms"
    return sum(1 for f in ("hm.py", "hm_policy.py") for ln in (d / f).read_text().splitlines() if ln.strip() and not ln.strip().startswith("#"))


def _glue_lines(files: "tuple[str, ...]") -> int:
    d = Path(__file__).resolve().parent / "arms"
    return sum(1 for f in files if (d / f).exists() for ln in (d / f).read_text().splitlines() if ln.strip() and not ln.strip().startswith("#"))


def mpa_glue_lines() -> int:
    """Non-blank, non-comment lines of MPA's own glue (``arms/mempalace_agent.py`` + ``arms/hm_policy.py``): the ``mpa_G3_glue_lines`` gate item."""
    return _glue_lines(("mempalace_agent.py", "hm_policy.py"))


def hma_glue_lines() -> int:
    """HMA's glue: MPA's files plus the combination's own (``arms/hma.py`` when it exists)."""
    return _glue_lines(("mempalace_agent.py", "hm_policy.py", "hma.py"))


def zma_glue_lines() -> int:
    """ZMA's glue: ``arms/zma.py`` (Zoe's own MemoryService is not glue we add) + MPA's files."""
    return _glue_lines(("zma.py", "mempalace_agent.py", "hm_policy.py"))


def git_commit(win: Any) -> str:
    r = win.host.run(["git", "-C", str(Path(__file__).resolve().parents[3]), "rev-parse", "--short", "HEAD"], mutating=False)
    return r.out.strip()[:12] if r.rc == 0 else "unknown"


def write_report(ctx: Ctx, win: Any, seeds: tuple, store: list, by_id: dict) -> dict:
    from . import artifact
    from .arms.hindsight import zoe_layer_lines
    from .bakeoff import consolidation_caveat
    cfg, repo = win.cfg, Path(__file__).resolve().parents[3]
    z0_axes = gates.aggregate_axes(ctx.z0)
    z0_off_axes = gates.aggregate_axes(ctx.z0_off)
    z0e_axes = gates.aggregate_axes(ctx.z0e) if ctx.z0e else {}
    eg = egress_summary(cfg.bakeoff_dir / f"egress-{win.run_id}.log", ctx.marks, ctx.ref_epoch, len(ctx.sampler.nonloopback) if ctx.sampler else 0)
    viol = eg["connects"]
    dl = sum(len((repo / "services/zoe-data" / f).read_text().splitlines()) for f in gates.DELETABLE_FILES if (repo / "services/zoe-data" / f).exists()) + 2500
    arms: "dict[str, dict]" = {}
    for v in cfg.arms:
        m = ctx.measure.setdefault(v, {})
        m["rss"] = ctx.sampler.summary(f"{v}:") if ctx.sampler else {}
        if v == "HM" and m.get("hm_driver") and m["rss"].get("steady_mb") is not None:
            # the verbatim tier hosted in-process under adoption adds what the driver's PSS grew by (the interpreter itself is zoe-data's, not an addition)
            add, peak = float(m["hm_driver"].get("verbatim_added_mb") or 0.0), float(m["hm_driver"].get("peak_added_mb") or 0.0)
            m["rss"] = {**m["rss"], "steady_mb": round(m["rss"]["steady_mb"] + add, 1), "burst_mb": round(m["rss"]["burst_mb"] + max(add, peak), 1),
                        "note": f"the servers' PSS during the HM phase + the verbatim tier's own growth on a WARM shared embedder (+{add:g} MB steady, +{max(add, peak):g} MB burst; "
                                    f"gross incl. a cold embedder load zoe-data already pays: +{m['hm_driver'].get('gross_added_mb', '?')} MB)"}
        if v == "ZMA" and m.get("mpa_driver"):
            # Z0 runs in-process in the driver and MemPalace is a child process: the servers' RSS + Z0's in-process delta is the whole footprint (the Hindsight stack idles, not counted)
            d = m["mpa_driver"]
            srv_s, srv_p, add = d.get("server_rss_steady_mb"), d.get("server_rss_peak_mb"), d.get("pss_added_mb")
            if srv_s is not None and srv_p is not None and add is not None:
                m["rss"] = {**m["rss"], "steady_mb": round(float(srv_s) + float(add), 1), "burst_mb": round(float(srv_p) + float(add), 1),
                            "note": f"the MemPalace servers' RSS (+{float(srv_s):g} steady / +{float(srv_p):g} peak) + Z0's in-process delta (+{float(add):g} MB); the shared embedder saves "
                                    f"{d.get('embedder_shared_mb', '?')} MB"}
        if v in ("MPA", "HMA") and m.get("mpa_driver"):
            # the MemPalace servers (one per palace) are separate child processes the sampler does not see: the sampler's PSS of the window's servers during the arm's phase
            # (for HMA that IS the HM-like Hindsight + shim + scratch Postgres stack) + the driver's own server RSS
            d = m["mpa_driver"]
            srv_s, srv_p = d.get("server_rss_steady_mb"), d.get("server_rss_peak_mb")
            if srv_s is not None and srv_p is not None:
                st0, bu0 = m["rss"].get("steady_mb"), m["rss"].get("burst_mb")
                m["rss"] = {**m["rss"], "steady_mb": round((st0 or 0.0) + float(srv_s), 1), "burst_mb": round((bu0 or 0.0) + float(srv_p), 1),
                            "note": f"the servers' PSS during the {v} phase ({'no samples' if st0 is None else str(st0) + ' MB steady'}) + the MemPalace servers' own RSS "
                                    f"(+{float(srv_s):g} MB steady, +{float(srv_p):g} MB peak over {d.get('servers', '?')} server(s)): conservative, the Hindsight stack idles during MPA"}
                if v == "HMA":
                    m["hma_rss_parts"] = {"stack_steady_mb": st0, "stack_burst_mb": bu0, "mempalace_steady_mb": float(srv_s), "mempalace_peak_mb": float(srv_p)}
        m["nonloopback_connects"] = viol
        m["egress"] = {"observed": eg["observed"], "detail": eg["detail"], "by_arm": eg["by_arm"]}
        m["mem_available_floor_mb"] = None if win.mem_floor == float("inf") else round(win.mem_floor, 0)
        m["layer_lines"] = (0 if v == "H0" else hm_glue_lines() if v == "HM" else mpa_glue_lines() if v == "MPA" else hma_glue_lines() if v == "HMA"
                            else zma_glue_lines() if v == "ZMA" else zoe_layer_lines())          # HM / MPA / HMA do not use the H arms' ZoeLayer: their glue is their own files
        if v in ("MPA", "HMA", "ZMA"):
            m["mpa_glue_lines"] = m["layer_lines"]
        m["deletable_lines"], m["deletable_basis"] = dl, "wc -l of the 10 files in decision record 3.1 + its 2,500-line memory_service estimate"
        m["forgetting"] = {"t0": ctx.forget[v].t0, "t6": ctx.forget[v].t6} if v in ctx.forget else m.get("forgetting", {})
        pin = m.get("prompt_in_tokens_max")
        m["prompt_fits"] = None if pin is None else (pin + 2048 < gates.RULE["slot_tokens"])
        m["prompt_detail"] = f"max prompt {pin} tokens + 2,048 output cap < {gates.RULE['slot_tokens']}"
        arms[v] = gates.evaluate_arm(v, ctx.seed_runs.get(v, {}), m)
    decision = gates.decide(arms, z0_axes, z0e_axes)
    instr = win.collect_hindsight_instrument() if hasattr(win, "collect_hindsight_instrument") else {}
    now = dt.datetime.now()
    meta = {"date": now.strftime("%Y-%m-%d"), "started": ctx.started_at, "finished": now.strftime("%Y-%m-%d %H:%M"), "wall_min": round(win.elapsed_min(), 1),
            "cap_min": cfg.cap_min, "commit": git_commit(win), "hindsight_version": ctx.version, "embed_model": ctx.embed_model, "clone_model": win.clone.get("model", "?"),
            "seeds": list(seeds), "arms_run": [a for a in cfg.arms if ctx.seed_runs.get(a)], "aborted": ctx.aborted, "restore": "pending (restore runs after this report is written)"}
    pg = getattr(win, "pg_state", None) or {}
    if pg:
        meta["scratch_postgres"] = ("KEPT (BAKEOFF_KEEP_PG=1): CONFOUNDED by the previous window's banks and jobs" if pg.get("kept")
                                    else f"fresh (wiped {pg.get('wiped_mb') if pg.get('wiped_mb') is not None else '?'} MB)")
    if getattr(win, "queue_at_open", None) is not None:
        meta["hindsight_queue_at_open"] = win.queue_at_open
    if instr.get("log_readable"):
        meta["hindsight_consolidation_prompt_tokens_max"] = instr.get("hindsight_consolidation_prompt_tokens_max")
        meta["hindsight_consolidation_failed_calls"] = instr.get("hindsight_consolidation_failed_calls")
    hooks = [h for h in ((f"BAKEOFF_SKIP_BRAIN_STOP=1: the live brain was NOT stopped and no clone ran; the brain slot was shared with the household, so no timing in it is a bake-off number."
                          if cfg.skip_brain_stop else ""),
                         (f"BAKEOFF_SMOKE_CELLS={cfg.smoke_cells}: one seed, {cfg.smoke_cells} cells per arm, a handful of validity / slot calls." if cfg.smoke_cells else "")) if h]
    if hooks:
        meta["test_hook"] = " ".join(hooks)
    notes = list(ctx.notes) + [
        "the real Hindsight extraction quality on the B cells (the unit tests use a rule-based test double)",
        "net RSS (the Chroma/ONNX that adoption frees inside zoe-data was NOT subtracted: the figure is gross, so conservative)",
        "G3 deletable lines is an ESTIMATE of files judged deletable, never proven",
        "cells an arm cannot run (H0 has no Zoe layer: no nightly conflict pass, no A8 people graph; an arm without the scratch Postgres: no F5/F6 disk scan) "
        "are SKIPs with the reason; hard ones keep `hard_cells_all_ran` red by the pre-registered rule: not a pass, and not an engine result either",
        "F5/F6 (physical erase) and A8 (people graph) on H1/H2 run the Zoe layer's scrub and Zoe's own graph over Hindsight's real Postgres files; the engine's "
        "own writes were only MODELLED in the pre-window probe, so these are the first contact with the real rows (the adapter control `physical_erase` OFF must be red here)",
        "H2 and H0 ran one seed each by design (H1 first, three seeds): the rule needs three, so they can only be INCOMPLETE",
        "the capability axes (J exact words, K reflection, L long-range recall, M protocol) ran on seed 1 only and each arm ran the ones it is the evidence for "
        "(H1 / HM: J, L, M; H2 / H0: K): the other arms' cells are `planner cut` or capability SKIPs, never passes; H2's and H0's latency / slot phases and the concise "
        "validity phase ran only if time remained (their G0 / G1 items are NA otherwise)",
        "the lab half of M (the protocol) is a scripted stand-in for each protocol's text and never decides; its brain half is declared and did not run, so M is `no data`; "
        "K on Z0 scripts the nightly model (K2 / K3 SKIP, K1 / K4 / K5 measure what Z0's store keeps of a night's proposals)",
        "the capability cells' thresholds were written before any Hindsight arm ran them; Z0e's long-range numbers were seen afterwards",
        "all arms share one Hindsight server and one egress log: the per-arm egress split is by wall-clock phase, the gate reads the whole window"]
    if "HM" in cfg.arms:
        notes.append("HM runs ONE seed by design (H1 keeps three): the rule needs three, so HM is INCOMPLETE, measured for the comparison; its verbatim tier is the REAL MemPalace "
                     f"library ({ctx.measure['HM'].get('hm_library') or 'not run'}) in the bake-off venv and its distilled tier the window's Hindsight; HM-G3a (the verbatim tier replaces zoe-data's own "
                     "palace) is a design review, not a measurement; its t+6 min forgetting check runs on a virtual clock (the ledger is durable, there is no TTL to wait out)")
    if "ZMA" in cfg.arms:
        notes.append("ZMA = Zoe's live stack (Z0e: MemoryService over Chroma + MiniLM, the authority classes, the forget ledger, the nightly passes) with MemPalace integrated; ONE seed by "
                     f"design, INCOMPLETE; its write path has no model call so its store cells run all axes A-M on seed 1; it can win only by beating the maintained candidate AND Z0e on at least "
                     f"two capability axes; shared embedder: {ctx.measure['ZMA'].get('zma_embedder') or 'not run'}")
    if "MPA" in cfg.arms or "HMA" in cfg.arms:
        notes.append("MPA / HMA run ONE seed by design (H1 keeps three): INCOMPLETE, measured for the comparison. Their J / K / L / M cells are measured with the CLONE BRAIN operating "
                     "MemPalace's tools: the brain is the instrument, a different brain would move them; MPA's protocol text costs extra prompt tokens (reported as the max prompt, "
                     "gate `mpa_G1_prompt_fits`); K (reflection) comes from the closet pass summaries the clone brain makes" + (" and, for HMA, from Hindsight's observations" if "HMA" in cfg.arms else "")
                     + "; they run L0 only (no 100 / 300 filler: each filler turn is a brain call); the MemPalace servers' RAM is the driver's own measurement")
    caveat = consolidation_caveat(instr)
    if caveat:
        notes.append(caveat + f" ({instr.get('hindsight_consolidation_failed_calls')} consolidation call(s) failed on a context-size error, {instr.get('hindsight_failed_attempts')} further retries logged)")
    if instr and not instr.get("log_readable") and not win.dry:
        notes.append("hindsight_consolidation_prompt_tokens_max could not be read (hindsight-api's journal was unavailable: " + str(instr.get("log_note", "no reason given")) + "): whether consolidation fits the live slot is NOT measured")
    if pg.get("kept"):
        notes.append("BAKEOFF_KEEP_PG=1: this window started on the previous window's scratch Postgres (its Hindsight banks and queued consolidation jobs), so every arm's cells share the clone's single slot with that work: CONFOUNDED, not a verdict")
    notes.append("Hindsight's background consolidation cannot be paused in the installed version (0.10.2: the worker claims any pending operation; the per-type slot setting is a reserved FLOOR, not a cap; there is no pause flag). "
                 "The window therefore starts on a fresh database with an empty job queue, H1 / H2 / HMA run no auto-consolidation (H2 / HMA trigger it explicitly in their own step), H0's auto-consolidation is that arm's native behaviour, "
                 "and an arm that STARTS with queued work is flagged in these notes")
    if ctx.reflect is not None:
        notes.append("the reflection variants (H2@32k, HMA@32k, H2@12B, HMA@12B) are VARIANTS, never contest entrants: they show whether the 8k slot limits K and what a 12B brain does with the same "
                     "cells; the K1 precision veto applies to them; the 12B model is the parked deep-brain unit's, generated from its text and never enabled")
    md = gates.render_markdown(meta, arms, decision, z0_axes, z0_off_axes, notes, z0e_axes, reflect=ctx.reflect)
    docs = cfg.docs_dir or (repo / "docs" / "research")
    try:
        docs.mkdir(parents=True, exist_ok=True)
        md_path = docs / f"bakeoff-run-{win.run_id}.md"
        md_path.write_text(md, encoding="utf-8")
    except OSError:
        md_path = cfg.bakeoff_dir / f"bakeoff-run-{win.run_id}.md"
        md_path.write_text(md, encoding="utf-8")
    payload = {"run_id": win.run_id, "meta": meta, "decision": {k: v for k, v in decision.items() if k != "compare"}, "compare": decision["compare"],
               "arms": {v: {k: val for k, val in a.items()} for v, a in arms.items()}, "z0": {s: {k: r[k] for k in ("axes", "hard_violations", "instrument")} for s, r in ctx.z0.items()},
               "z0_off": {s: {k: r[k] for k in ("axes", "hard_violations")} for s, r in ctx.z0_off.items()}, "z0e": {s: r["axes"] for s, r in ctx.z0e.items()},
               "seed_runs": ctx.seed_runs, "reflect": ctx.reflect,
               "measure": ctx.measure, "notes": notes, "docs_path": str(md_path), "instruments": instr, "scratch_postgres": pg}
    artifact.write_json(cfg.bakeoff_dir / f"run-{win.run_id}.json", payload)
    win.log(f"VERDICT: {decision['verdict']} - {decision['text']}")
    win.log(f"report: {md_path}  artifact: {cfg.bakeoff_dir}/run-{win.run_id}.json")
    return payload


def measure(win: Any) -> dict:
    from . import spec
    make_factories(win)
    cfg, host, log = win.cfg, win.host, win.log
    everything = spec.load_cells()
    by_id = {c.id: c for c in everything}
    store = [c for c in everything if c.tier == "store"]
    seeds = seeds_for(win.run_id)
    sampler = Sampler(win)
    sampler.start()
    ctx = Ctx(win, sampler)
    ctx.started_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    ctx.version = ctx.embed_model = ""
    try:
        ctx.version = str(win.client_factory().call("version", "GET", "/version").get("api_version") or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        h = json.loads(host.run(["curl", "-sf", "-m", "3", f"http://127.0.0.1:{cfg.shim_port}/health"], mutating=False).out or "{}")
        ctx.embed_model = f"{h.get('model', '?')} ({h.get('dim', '?')}-d)"
    except ValueError:
        ctx.embed_model = "unknown"
    def instrument_of(seed: str) -> dict:
        return (ctx.z0.get(seed) or {}).get("instrument") or {"ok": False, "lab_controls_red": "?"}
    tail_s = TAIL_MIN * 60.0
    budget = plan_budget(cfg, store)
    log("budget: " + "; ".join(f"{a} {budget.seeds[a]} seed(s) x <= {budget.box_min[a]:g} min (~{budget.cells_in_box(a)}/{budget.runnable_for(a)} cells)"
                               for a in budget.arms) + f"; {budget.avail_min:.1f} min available, planned {budget.total_min():.1f}")
    concise_arms = tuple(a for a in cfg.arms if a not in ("H1", "MPA", "ZMA"))          # MPA / ZMA have no extraction call: its tool-call validity is its own gate item
    try:
        ctx.label("lab")
        sweep_stale_banks(ctx)
        phase_z0(ctx, seeds, store, by_id)
        for v in (a for a in cfg.arms if a not in DRIVER_ARMS):          # HM's / MPA's / HMA's forgetting is their own cells (F1 / F2), run in their drivers
            ctx.forget[v] = ForgetProbe(ctx, v)
            ctx.label(f"{v}:forget")
            ctx.forget[v].start()
            log(f"forgetting probe {v}: t+0 {ctx.forget[v].t0}")
        phase_arm_controls(ctx, store)
        for v in budget.arms:                 # H1 first and complete, then H2, then H0: a later arm only gets what the earlier one left
            check_hindsight_idle(ctx, v)
            for k in range(1 if cfg.smoke_cells else budget.seeds[v]):
                box = budget.seed_box_s(v, win.time_left_s() - tail_s, first=(k == 0))
                if box >= 60.0 and v == "HM":
                    phase_hm(ctx, seeds[k], box, store, by_id, instrument_of)
                elif box >= 60.0 and v == "MPA":
                    phase_mpa(ctx, seeds[k], box, store, by_id, instrument_of)
                elif box >= 60.0 and v == "HMA":
                    phase_hma(ctx, seeds[k], box, store, by_id, instrument_of)
                elif box >= 60.0 and v == "ZMA":
                    phase_zma(ctx, seeds[k], box, store, by_id, instrument_of)
                elif box >= 60.0:
                    run_arm_seed(ctx, v, seeds[k], box, store, by_id, instrument_of, first=(k == 0))
                if k == 0:
                    if win.time_left_s() > tail_s + 90 and v not in DRIVER_ARMS:
                        phase_latency(ctx, v)
                    mode_arms = ("H1",) if v == "H1" else (concise_arms if "concise" not in ctx.validity_done else ())
                    if mode_arms and win.time_left_s() > tail_s + 300:
                        mode = "verbatim" if v == "H1" else "concise"
                        phase_validity(ctx, mode, mode_arms, min(600.0, win.time_left_s() - tail_s))
                        ctx.validity_done.add(mode)
                    if win.time_left_s() > tail_s + 90 and v not in DRIVER_ARMS:
                        phase_slot(ctx, v)
        phase_reflect(ctx, seeds[0], store, by_id, instrument_of)          # optional, K only, against a restarted clone: skips itself when off / no time / a precondition fails
        for p in ctx.forget.values():            # a real t+6 min: wait out whatever is left, never skip it
            while p.t6 is None:
                win.guard()
                if p.due():
                    p.finish()
                else:
                    host.sleep(5.0)
    except Exception as exc:  # noqa: BLE001 - Aborted included: whatever was measured is still reported, as INCOMPLETE
        ctx.aborted = f"{type(exc).__name__}: {exc}"
        log(f"measurement stopped: {ctx.aborted}")
    finally:
        sampler.stop()
        for p in ctx.forget.values():
            p.close()
    payload = write_report(ctx, win, seeds, store, by_id)
    if ctx.aborted:
        raise Aborted(ctx.aborted)
    return payload
