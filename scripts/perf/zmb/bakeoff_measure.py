"""What a bake-off window MEASURES (called by ``bakeoff.Window`` once the clone, Hindsight, the shim and the scratch DB are up).

Order of work, by value, because 90 minutes cannot hold everything at full size (the schedule is printed by ``--dry-run``):

    1  Z0 and Z0-off in the lab on every seed (no brain)                   the control, and the negative control
    2  forgetting probes START (t+0 check); the real t+6 min check is run later, between cells
    3  adapter negative controls on the real server (a bypassed gate must write the intruder row)
    4  seed 1 of H1, H2, H0: the store-tier cells, time-boxed
    5  recall latency (n=50), extraction JSON validity (>=100 calls, per mode), brain-slot seconds per retained turn
    6  seeds 2 and 3 of each arm, in whatever time is left; then the t+6 min forgetting verdicts

A cell the time box did not reach is a SKIP with the reason, never a pass; an arm with fewer than three seeds is INCOMPLETE and cannot be
adopted (bakeoff_gates). Counts and labels only go into the artifact: no household text.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from . import bakeoff_gates as gates
from .bakeoff import UNITS, Aborted

#: per-(arm, seed) wall-clock boxes in minutes; later seeds get 60% of the first (the first seed is the baseline)
BOX_MIN = {"H1": 9.0, "H2": 11.0, "H0": 6.0}
LATENCY_FACTS, LATENCY_QUERIES = 16, 50
VALIDITY_CALLS = 104
SLOT_RETAINS, SLOT_TURNS_PER_CHUNK = 12, 10
FORGET_WAIT_S = 360.0
PROBE_USERS = {v: "demo_bar_" + hashlib.sha1(f"zmb-probe-{v}".encode()).hexdigest()[:8] for v in ("H0", "H1", "H2")}

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
        for line in ss.splitlines():
            parts = line.split()
            if len(parts) < 4 or not any(f"pid={p}," in line for p in pids):
                continue
            peer = parts[3].rsplit(":", 1)[0].strip("[]")
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
    NAME, KEEP = "Marisol", "Priya"

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
        self.arm.ingest([Turn(f"User's friend {self.NAME} lives in Hobart.", "owner_taught"),
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
        self.measure: "dict[str, dict]" = {v: {} for v in ("H0", "H1", "H2")}
        self.forget: "dict[str, ForgetProbe]" = {}
        self.notes: "list[str]" = []
        self.aborted = ""

    def new_arm(self, variant: str, **kw: Any) -> Any:
        return self.win.arm_factory(variant, **kw)

    def slot_seconds(self) -> "Optional[float]":
        r = self.host.run(["curl", "-sf", "-m", "5", f"http://127.0.0.1:{self.cfg.clone_port}/metrics"], mutating=False)
        return parse_metrics_seconds(r.out) if r.rc == 0 else None

    def label(self, name: str) -> None:
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


def phase_arm_controls(ctx: Ctx, store: list) -> None:
    """Instrument checks of the ADAPTER on the real server: with a Zoe-layer protection switched off the cell that claims it must go RED."""
    from . import cells as cellmod
    from .world import BASELINE_SEED, make_world
    world = make_world(BASELINE_SEED)
    claims = (("authority", "A1.digest.home"), ("identity", "H1.digest"), ("ledger", "F2.late_writer.digest"))
    red = 0
    for off, cid in claims:
        cell = next((c for c in store if c.id == cid), None)
        if cell is None:
            continue
        arm = ctx.new_arm("H1", off=frozenset({off}))
        try:
            o = cellmod.run_cell(cell.rendered(world), world, arm)
        finally:
            arm.close()
        red += 1 if o.verdict == "FAIL" else 0
        ctx.log(f"adapter control: H1 with `{off}` OFF on {cid} -> {o.verdict} (must be FAIL)")
    ctx.measure["H1"]["arm_controls"] = f"{red}/{len(claims)}"
    for v in ("H0", "H2"):
        ctx.measure[v]["arm_controls"] = ctx.measure["H1"]["arm_controls"]
    ctx.arm_controls_ok = red == len(claims)


def run_arm_seed(ctx: Ctx, variant: str, seed: str, box_s: float, store: list, by_id: dict, instrument_of: "Callable[[str], dict]") -> None:
    from . import artifact, cells as cellmod, runner
    from .world import make_world
    world = make_world(seed)
    arm = ctx.new_arm(variant)
    ctx.label(f"{variant}:{seed}")
    t_end, rows, unreachable, t0 = ctx.host.mono() + box_s, [], 0, ctx.host.mono()
    try:
        for cell in interleave(store):
            ctx.win.guard()
            ctx.due_probes()
            if ctx.host.mono() >= t_end or unreachable >= 3:
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
    ran = sum(1 for r in rows if r["verdict"] != "SKIP")
    ctx.seed_runs.setdefault(variant, {})[seed] = {
        "axes": artifact.axis_stats(rows, by_id, inst["ok"]), "hard_violations": artifact.hard_violations(rows, by_id),
        "hard_skipped": hard_skipped, "instrument": {"ok": inst["ok"] and getattr(ctx, "arm_controls_ok", False),
                                                      "lab_controls_red": inst["lab_controls_red"], "arm_controls": ctx.measure[variant].get("arm_controls")},
        "cells_ran": ran, "cells_selected": len(rows), "duration_s": round(ctx.host.mono() - t0, 1), "cells": _strip(rows),
        "retain": arm.measure()}
    ctx.log(f"{variant} {seed}: {ran}/{len(rows)} cells ran in {ctx.host.mono() - t0:.0f}s; hard violations {len(ctx.seed_runs[variant][seed]['hard_violations'])}"
            f"; hard skipped {hard_skipped}")


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
    for s in probe_sentences(VALIDITY_CALLS):
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
        rows = t.get("items") or t.get("requests") or []
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
    for i in range(SLOT_RETAINS):
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

def plan_table(cfg: Any, seeds: tuple) -> "list[tuple[str, float, str]]":
    arms = list(cfg.arms)
    rows = [("Z0 + Z0-off in the lab on 3 seeds (no brain)", 1.0, "control and negative control"),
            ("forgetting probes start (t+0 check)", 1.0, "H arms; real t+6 min check runs later between cells"),
            ("adapter negative controls on the real server", 1.0, "bypassed gate must write the intruder row")]
    rows += [(f"{a} seed 1 ({seeds[0]}): store-tier cells", BOX_MIN[a], "time-boxed, interleaved across axes") for a in arms]
    rows += [("recall latency n=50 per arm", 1.5 * len(arms), "p50/p95 through the shim and Postgres"),
             ("extraction JSON validity, concise and verbatim", 9.0, f">= 100 retain calls per mode"),
             ("brain-slot seconds per retained turn", 1.0 * len(arms), "idle retain of 10-turn chunks")]
    rows += [(f"{a} seeds 2 and 3", 2 * BOX_MIN[a] * 0.6, "only if time remains; fewer seeds => INCOMPLETE") for a in arms]
    rows += [("t+6 min forgetting verdicts, report", 1.0, "")]
    return rows


def dry_plan(win: Any) -> dict:
    cfg = win.cfg
    seeds = seeds_for(win.run_id)
    win.log("PLAN (the measurement phases; est. minutes):")
    total = 0.0
    for name, mins, note in plan_table(cfg, seeds):
        total += mins
        win.log(f"  {mins:5.1f}  {name}" + (f"   [{note}]" if note else ""))
    win.log(f"  {total:5.1f}  total planned work; hard cap {cfg.cap_min:.0f} min; {cfg.reserve_min:.0f} min always kept for restore + report")
    win.log("seeds: " + ", ".join(seeds))
    win.log(f"outputs: {cfg.bakeoff_dir}/run-{win.run_id}.log, run-{win.run_id}.json, <docs>/bakeoff-run-{win.run_id}.md")
    win.log("RESTORE (always, on every exit path): stop zoe-bakeoff-hindsight/-gemma/-embed, docker compose down, "
            f"systemctl --user start {cfg.unit}, poll :{cfg.live_port}/health, release the lock")
    return {"dry_run": True}


def make_factories(win: Any) -> None:
    """Real arm / client factories (tests replace them with ones over ``FakeHindsight``)."""
    from .arms.hindsight import HindsightArm, HindsightClient
    base = f"http://127.0.0.1:{win.cfg.hs_port}"
    if not hasattr(win, "arm_factory"):
        win.arm_factory = lambda variant, **kw: HindsightArm(variant, base_url=base, **kw)
    if not hasattr(win, "client_factory"):
        win.client_factory = lambda: HindsightClient(base)


def count_violations(path: Path) -> "Optional[int]":
    """Non-loopback connects the in-process egress hook logged. None = the hook wrote nothing: that is NOT zero, it is not measured."""
    try:
        lines = [ln for ln in path.read_text(errors="ignore").splitlines() if ln.strip()]
    except OSError:
        return None
    return sum(1 for ln in lines if " VIOLATION " in ln) if lines else None


def git_commit(win: Any) -> str:
    r = win.host.run(["git", "-C", str(Path(__file__).resolve().parents[3]), "rev-parse", "--short", "HEAD"], mutating=False)
    return r.out.strip()[:12] if r.rc == 0 else "unknown"


def write_report(ctx: Ctx, win: Any, seeds: tuple, store: list, by_id: dict) -> dict:
    from . import artifact
    from .arms.hindsight import zoe_layer_lines
    cfg, repo = win.cfg, Path(__file__).resolve().parents[3]
    z0_axes = gates.aggregate_axes(ctx.z0)
    z0_off_axes = gates.aggregate_axes(ctx.z0_off)
    hook = count_violations(cfg.bakeoff_dir / f"egress-{win.run_id}.log")
    viol = None if hook is None else hook + (len(ctx.sampler.nonloopback) if ctx.sampler else 0)
    dl = sum(len((repo / "services/zoe-data" / f).read_text().splitlines()) for f in gates.DELETABLE_FILES if (repo / "services/zoe-data" / f).exists()) + 2500
    arms: "dict[str, dict]" = {}
    for v in cfg.arms:
        m = ctx.measure.setdefault(v, {})
        m["rss"] = ctx.sampler.summary(f"{v}:") if ctx.sampler else {}
        m["nonloopback_connects"] = viol
        m["mem_available_floor_mb"] = None if win.mem_floor == float("inf") else round(win.mem_floor, 0)
        m["layer_lines"] = zoe_layer_lines() if v != "H0" else 0
        m["deletable_lines"], m["deletable_basis"] = dl, "wc -l of the 10 files in decision record 3.1 + its 2,500-line memory_service estimate"
        m["forgetting"] = {"t0": ctx.forget[v].t0, "t6": ctx.forget[v].t6} if v in ctx.forget else {}
        pin = m.get("prompt_in_tokens_max")
        m["prompt_fits"] = None if pin is None else (pin + 2048 < gates.RULE["slot_tokens"])
        m["prompt_detail"] = f"max prompt {pin} tokens + 2,048 output cap < {gates.RULE['slot_tokens']}"
        arms[v] = gates.evaluate_arm(v, ctx.seed_runs.get(v, {}), m)
    decision = gates.decide(arms, z0_axes)
    now = dt.datetime.now()
    meta = {"date": now.strftime("%Y-%m-%d"), "started": ctx.started_at, "finished": now.strftime("%Y-%m-%d %H:%M"), "wall_min": round(win.elapsed_min(), 1),
            "cap_min": cfg.cap_min, "commit": git_commit(win), "hindsight_version": ctx.version, "embed_model": ctx.embed_model, "clone_model": win.clone.get("model", "?"),
            "seeds": list(seeds), "arms_run": [a for a in cfg.arms if ctx.seed_runs.get(a)], "aborted": ctx.aborted, "restore": "pending (restore runs after this report is written)"}
    notes = list(ctx.notes) + [
        "the real Hindsight extraction quality on the B cells (the unit tests use a rule-based test double)",
        "net RSS (the Chroma/ONNX that adoption frees inside zoe-data was NOT subtracted: the figure is gross, so conservative)",
        "G3 deletable lines is an ESTIMATE of files judged deletable, never proven",
        "A3 provenance honesty, A8 graph edges, poisoning I1-I6, temporal C and recall D cells do not exist yet",
        "arms share one Hindsight server and one egress log: the non-loopback count is for the whole window, not per arm"]
    md = gates.render_markdown(meta, arms, decision, z0_axes, z0_off_axes, notes)
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
               "z0_off": {s: {k: r[k] for k in ("axes", "hard_violations")} for s, r in ctx.z0_off.items()}, "seed_runs": ctx.seed_runs,
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
    tail_s = 5 * 60.0
    try:
        ctx.label("lab")
        phase_z0(ctx, seeds, store, by_id)
        for v in cfg.arms:
            ctx.forget[v] = ForgetProbe(ctx, v)
            ctx.label(f"{v}:forget")
            ctx.forget[v].start()
            log(f"forgetting probe {v}: t+0 {ctx.forget[v].t0}")
        phase_arm_controls(ctx, store)
        for v in cfg.arms:
            box = min(BOX_MIN[v] * 60.0, win.time_left_s() - tail_s)
            if box >= 60.0:
                run_arm_seed(ctx, v, seeds[0], box, store, by_id, instrument_of)
        for v in cfg.arms:
            if win.time_left_s() > tail_s + 90:
                phase_latency(ctx, v)
        if win.time_left_s() > tail_s + 300:
            modes = [("concise", tuple(a for a in cfg.arms if a != "H1")), ("verbatim", tuple(a for a in cfg.arms if a == "H1"))]
            for mode, vs in modes:
                if vs:
                    phase_validity(ctx, mode, vs, min(600.0, win.time_left_s() - tail_s))
        for v in cfg.arms:
            if win.time_left_s() > tail_s + 90:
                phase_slot(ctx, v)
        for si in (1, 2):
            for v in cfg.arms:
                box = min(BOX_MIN[v] * 0.6 * 60.0, win.time_left_s() - tail_s)
                if box >= 90.0:
                    run_arm_seed(ctx, v, seeds[si], box, store, by_id, instrument_of)
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
