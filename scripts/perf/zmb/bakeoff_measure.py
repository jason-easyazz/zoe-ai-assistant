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
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from . import bakeoff_gates as gates
from .bakeoff import PG_CONTAINER, UNITS, Aborted

#: What run 1 (20261006-0935, ``run-20261006-0935.log``) MEASURED; the planner budgets from these, not from hope.
#: minutes per fixed phase (Z0 lab + forgetting start + adapter controls; per-arm latency / slot; per-mode extraction validity)
PHASE_MIN = {"lab": 2.4, "latency": {"H1": 1.0, "H2": 2.5, "H0": 5.1, "HM": 0.0}, "slot": {"H1": 3.1, "H2": 5.2, "H0": 5.2, "HM": 0.0},
             "validity": {"verbatim": 6.0, "concise": 10.0},
             #: Z0e (real Chroma + MiniLM) on the D cells x 3 seeds, measured 2026-10-06 first contact: 4 cells x ~6.5 s x 3 seeds = 1.3 min
             #: ... plus the two long-range cells (L1 9 s, L2 17 s per seed measured 2026-10-07): 2.7 min
             "z0e": 2.7,
             #: the HM cells on the real tiers (real library + real Hindsight, ``--controls real-tier``): 217 s measured at first contact (2026-10-06), with headroom
             "hm_cells": 5.0}
#: seconds per cell that RAN, seed 1: H1 112 cells in 414 s, H2 55 in 662 s, H0 18 in 362 s
S_PER_CELL = {"H1": 3.7, "H2": 12.0, "H0": 20.0, "HM": 1.3}      # HM: 131 cells ran in 171 s on the real tiers at first contact (a verbatim write is no model call; most cells never distil)
#: seeds per arm. H1 is the preferred arm and needs all three (the rule); H2 and H0 get one each and are INCOMPLETE by design
SEEDS_PER_ARM = {"H1": 3, "H2": 1, "H0": 1, "HM": 1}
ARM_ORDER = ("H1", "H2", "HM", "H0")              # priority: an earlier arm is finished before a later one starts (HM is a candidate, H0 only the native baseline)
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
CAP_PLANNED = {"H1": ("exact_words", "multi_hop", "protocol"), "H2": ("reflection",), "H0": ("reflection",), "HM": ("exact_words", "multi_hop", "protocol")}
#: what an arm DECLARES (it could run it) and the plan drops: H1 and HM have no observation layer, so reflection is a capability skip for them, not a cut
CAP_CUT = {"H1": (), "HM": (), "H2": ("exact_words", "multi_hop", "protocol"), "H0": ("exact_words", "multi_hop", "protocol")}
#: retained model calls one seed-1 play makes per axis (counted from the corpora: filler that is stored + the facts taught) and seconds per call
#: (``slot_s_per_turn``: H1 1.52, H2 2.56 at run 1), plus the observation consolidation (measured by nobody yet: 90 s is a guess, the box is a ceiling)
CAP_RETAINS = {"exact_words": 45, "multi_hop": 260, "protocol": 14, "reflection": 37}
S_PER_RETAIN = {"H1": 1.52, "H2": 2.56, "H0": 2.56}
CONSOLIDATE_S = 90.0
HM_CAP_MIN = 2.0                                  # HM: the verbatim write is no model call; the same corpora took ~100 s on the real tiers in the lab
H1_BOX_MARGIN = 1.25                             # the new cells (D recall, A3, C temporal) are unmeasured on real Hindsight: headroom over run 1's rate
OPEN_MIN, TAIL_MIN = 0.5, 5.0                    # steps 1-6 of the window; the t+6 min wait + report at the end
LATENCY_FACTS, LATENCY_QUERIES = 16, 50
VALIDITY_CALLS = 104
SMOKE_VALIDITY_CALLS, SMOKE_SLOT_RETAINS = 10, 4          # BAKEOFF_SMOKE_CELLS: a handful of brain calls, never a measurement
SLOT_RETAINS, SLOT_TURNS_PER_CHUNK = 12, 10
FORGET_WAIT_S = 360.0
PROBE_USERS = {v: "demo_bar_" + hashlib.sha1(f"zmb-probe-{v}".encode()).hexdigest()[:8] for v in ("H0", "H1", "H2")}
HM_DRIVER = Path(__file__).resolve().parent / "hm_window.py"

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
        drv = h.run(["pgrep", "-f", r"^\S*python\S* .*hm_window\.py"], mutating=False)       # the HM driver is a child process of the window: its sockets count too
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
        self.measure: "dict[str, dict]" = {v: {} for v in ("H0", "H1", "H2", "HM")}
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


def phase_z0(ctx: Ctx, seeds: tuple, store: list, by_id: dict) -> None:
    from . import artifact, runner
    from .arms import make_arm
    from .lab_driver import CONTROLS
    from .world import make_world
    for seed in seeds:
        world = make_world(seed)
        cp = runner.control_pass(store, world, frozenset(CONTROLS))
        inst = runner.instrument_block(cp, store)
        for label, arm_name, sink in (("Z0", "Z0", ctx.z0), ("Z0-off", "Z0-off", ctx.z0_off)):
            arm = make_arm(arm_name)
            try:
                rows = runner.run_cells(store, world, arm)
            finally:
                arm.close()
            sink[seed] = {"axes": artifact.axis_stats(rows, by_id, inst["ok"]), "hard_violations": artifact.hard_violations(rows, by_id),
                          "instrument": inst, "cells": _strip(rows)}
        ctx.log(f"Z0 seed {seed}: controls red {inst['lab_controls_red']} ok={inst['ok']}")
        phase_z0e(ctx, seed, world, store, by_id, inst)


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
        "retain": arm.measure()}
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

    def runnable_for(self, arm: str) -> int:
        if arm == "HM" and self.runnable_hm >= 0:
            return self.runnable_hm
        return self.runnable_h0 if arm == "H0" and self.runnable_h0 >= 0 else self.runnable

    def fixed_min(self, arm: str) -> float:
        """Budgeted latency + slot minutes for the arm (the extraction-validity phase is per MODE, see ``validity_min``). 0 for an optional arm.
        HM has neither phase (its latency is measured inside its cells, on the real tiers): its fixed time is the HM cells."""
        if arm == "HM":
            return PHASE_MIN["hm_cells"]
        return 0.0 if arm in OPTIONAL_PHASES else PHASE_MIN["latency"][arm] + PHASE_MIN["slot"][arm]

    def validity_min(self) -> float:
        return ((PHASE_MIN["validity"]["verbatim"] if "H1" in self.arms and "verbatim" not in OPTIONAL_VALIDITY else 0.0)
                + (PHASE_MIN["validity"]["concise"] if any(a != "H1" for a in self.arms) and "concise" not in OPTIONAL_VALIDITY else 0.0))

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
        return min(2.0 * planned, left * (2.0 / 3.0 if later else 1.0))


def plan_budget(cfg: Any, store: "Optional[list]" = None, runnable: "Optional[int]" = None) -> Budget:
    """Three seeds of H1 inside the cap, one seed each for H2 and H0, the hard cap kept. ``store`` = the store-tier cells; the cells an H arm
    can run are those whose required capabilities it declares (the rest SKIP with the reason and cost nothing)."""
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
    avail = cfg.cap_min - cfg.reserve_min - TAIL_MIN - OPEN_MIN
    seeds = {a: SEEDS_PER_ARM[a] for a in arms}
    h1 = round(max(5.0, runnable * S_PER_CELL["H1"] * H1_BOX_MARGIN / 60.0) * 2) / 2.0          # to the half minute
    box = {"H1": h1} if "H1" in arms else {}
    extra = {a: cap_extra_min(a) for a in arms}
    draft = Budget(avail, len(ordinary), runnable, arms, seeds, {a: 0.0 for a in arms}, runnable_h0, runnable_hm, extra)
    spare = (avail - PHASE_MIN["lab"] - PHASE_MIN["z0e"] - draft.validity_min() - sum(draft.fixed_min(a) for a in arms) - seeds.get("H1", 0) * box.get("H1", 0.0)
             - sum(extra.values()))
    lower = [a for a in arms if a != "H1"]
    weights = {2: (2.0, 1.0), 3: (2.0, 1.5, 0.5)}.get(len(lower), (1.0,) * len(lower))      # H2 the biggest share, then HM (a candidate), H0 (the native baseline) the least
    for a, w in zip(lower, weights):
        box[a] = math.floor(max(1.0, spare * w / sum(weights)) * 2) / 2.0               # rounded DOWN: the plan must fit the cap, not just touch it
    return Budget(avail, len(ordinary), runnable, arms, seeds, {a: box.get(a, 0.0) for a in arms}, runnable_h0, runnable_hm, extra)


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
        rows.append((f"{a} seed 1 ({seeds[0]}): store-tier cells", b.box_min[a],
                     f"ceiling; ~{b.cells_in_box(a)} of {b.runnable_for(a)} runnable cells at {S_PER_CELL[a]:g} s/cell (run 1)"))
        if b.extra_min.get(a):
            rows.append((f"{a} seed 1: capability cells ({', '.join(CAP_PLANNED[a])})", b.extra_min[a],
                         "exact words / reflection / long-range recall / protocol, seed 1 only: " + ("no model call on the verbatim write" if a == "HM"
                                                                                                     else f"~{sum(CAP_RETAINS[x] for x in CAP_PLANNED[a])} retained calls x {S_PER_RETAIN[a]:g} s"
                                                                                                     + (f" + {CONSOLIDATE_S:g} s consolidation" if "reflection" in CAP_PLANNED[a] else ""))))
        opt = a in OPTIONAL_PHASES
        why = f"only if time remains; ~{PHASE_MIN['latency'][a]:g} min at run 1's rate, not budgeted" if opt else "p50/p95 through the shim and Postgres"
        if a != "HM":           # HM's latency and slot are measured inside its cells (wall clocks on the real tiers): no separate phase
            rows.append((f"{a} recall latency n=50", 0.0 if opt else PHASE_MIN["latency"][a], why))
        mode = "verbatim" if a == "H1" else "concise"
        if a == "H1" or not concise_done:
            opt_v = mode in OPTIONAL_VALIDITY
            rows.append((f"extraction JSON validity, {mode} (>= 100 retain calls)", 0.0 if opt_v else PHASE_MIN["validity"][mode],
                         (f"only if time remains; ~{PHASE_MIN['validity'][mode]:g} min, not budgeted (CUT for the capability cells; H2 / H0 are INCOMPLETE by design)" if opt_v
                          else "shared by H0 and H2" if mode == "concise" else "")))
            concise_done = concise_done or mode == "concise"
        if a != "HM":
            rows.append((f"{a} brain-slot seconds per retained turn", 0.0 if opt else PHASE_MIN["slot"][a],
                         f"only if time remains; ~{PHASE_MIN['slot'][a]:g} min at run 1's rate, not budgeted" if opt else "idle retain of 10-turn chunks"))
        for k in range(2, b.seeds[a] + 1):
            rows.append((f"{a} seed {k} ({seeds[k - 1]}): store-tier cells", b.box_min[a], "ceiling, same box as seed 1 (the capability cells ran on seed 1)"))
    rows.append(("t+6 min forgetting verdicts, report", 0.0, f"inside the {TAIL_MIN:g} min tail, not counted"))
    return rows


def dry_plan(win: Any) -> dict:
    cfg = win.cfg
    seeds = seeds_for(win.run_id)
    b = plan_budget(cfg)
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
    return head if head in ("H0", "H1", "H2", "HM") else "shared"


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


def git_commit(win: Any) -> str:
    r = win.host.run(["git", "-C", str(Path(__file__).resolve().parents[3]), "rev-parse", "--short", "HEAD"], mutating=False)
    return r.out.strip()[:12] if r.rc == 0 else "unknown"


def write_report(ctx: Ctx, win: Any, seeds: tuple, store: list, by_id: dict) -> dict:
    from . import artifact
    from .arms.hindsight import zoe_layer_lines
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
        m["nonloopback_connects"] = viol
        m["egress"] = {"observed": eg["observed"], "detail": eg["detail"], "by_arm": eg["by_arm"]}
        m["mem_available_floor_mb"] = None if win.mem_floor == float("inf") else round(win.mem_floor, 0)
        m["layer_lines"] = 0 if v == "H0" else hm_glue_lines() if v == "HM" else zoe_layer_lines()      # HM does not use the H arms' ZoeLayer: its glue is hm.py + hm_policy.py
        m["deletable_lines"], m["deletable_basis"] = dl, "wc -l of the 10 files in decision record 3.1 + its 2,500-line memory_service estimate"
        m["forgetting"] = {"t0": ctx.forget[v].t0, "t6": ctx.forget[v].t6} if v in ctx.forget else m.get("forgetting", {})
        pin = m.get("prompt_in_tokens_max")
        m["prompt_fits"] = None if pin is None else (pin + 2048 < gates.RULE["slot_tokens"])
        m["prompt_detail"] = f"max prompt {pin} tokens + 2,048 output cap < {gates.RULE['slot_tokens']}"
        arms[v] = gates.evaluate_arm(v, ctx.seed_runs.get(v, {}), m)
    decision = gates.decide(arms, z0_axes, z0e_axes)
    now = dt.datetime.now()
    meta = {"date": now.strftime("%Y-%m-%d"), "started": ctx.started_at, "finished": now.strftime("%Y-%m-%d %H:%M"), "wall_min": round(win.elapsed_min(), 1),
            "cap_min": cfg.cap_min, "commit": git_commit(win), "hindsight_version": ctx.version, "embed_model": ctx.embed_model, "clone_model": win.clone.get("model", "?"),
            "seeds": list(seeds), "arms_run": [a for a in cfg.arms if ctx.seed_runs.get(a)], "aborted": ctx.aborted, "restore": "pending (restore runs after this report is written)"}
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
    md = gates.render_markdown(meta, arms, decision, z0_axes, z0_off_axes, notes, z0e_axes)
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
               "seed_runs": ctx.seed_runs,
               "measure": ctx.measure, "notes": notes, "docs_path": str(md_path)}
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
    concise_arms = tuple(a for a in cfg.arms if a != "H1")
    try:
        ctx.label("lab")
        sweep_stale_banks(ctx)
        phase_z0(ctx, seeds, store, by_id)
        for v in (a for a in cfg.arms if a != "HM"):          # HM's forgetting is its own two cells (F1 / F2) on the real tiers, run in the HM driver
            ctx.forget[v] = ForgetProbe(ctx, v)
            ctx.label(f"{v}:forget")
            ctx.forget[v].start()
            log(f"forgetting probe {v}: t+0 {ctx.forget[v].t0}")
        phase_arm_controls(ctx, store)
        for v in budget.arms:                 # H1 first and complete, then H2, then H0: a later arm only gets what the earlier one left
            for k in range(1 if cfg.smoke_cells else budget.seeds[v]):
                box = budget.seed_box_s(v, win.time_left_s() - tail_s, first=(k == 0))
                if box >= 60.0 and v == "HM":
                    phase_hm(ctx, seeds[k], box, store, by_id, instrument_of)
                elif box >= 60.0:
                    run_arm_seed(ctx, v, seeds[k], box, store, by_id, instrument_of, first=(k == 0))
                if k == 0:
                    if win.time_left_s() > tail_s + 90 and v != "HM":
                        phase_latency(ctx, v)
                    mode_arms = ("H1",) if v == "H1" else (concise_arms if "concise" not in ctx.validity_done else ())
                    if mode_arms and win.time_left_s() > tail_s + 300:
                        mode = "verbatim" if v == "H1" else "concise"
                        phase_validity(ctx, mode, mode_arms, min(600.0, win.time_left_s() - tail_s))
                        ctx.validity_done.add(mode)
                    if win.time_left_s() > tail_s + 90 and v != "HM":
                        phase_slot(ctx, v)
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
