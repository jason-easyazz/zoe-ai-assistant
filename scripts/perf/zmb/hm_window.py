#!/usr/bin/env python3
"""The HM arm's measurement inside a bake-off window: the REAL MemPalace 3.10.0 library (verbatim tier) + the REAL Hindsight server (distilled tier).

Why a separate process. ``bakeoff.py`` / ``bakeoff_measure.py`` run in the zoe-data interpreter, whose ``mempalace`` is an older release; the verbatim tier needs
the bake-off venv (``/home/zoe/.zoe/bakeoff-2026-10/mempalace-venv``: mempalace 3.10.0, chromadb 1.5.9) under a scrubbed HOME (``mp_env.sh``: the live palace and the
live chroma cache are never opened). The window starts this script through ``mp_run.sh`` and reads ONE JSON file back; the Hindsight tier talks to the window's
server over loopback HTTP, the scratch Postgres is reached through ``docker exec`` (the same ``ScratchPostgres`` the H arms use).

Three things are measured, each its own key in the JSON:

* ``hm_cells``  - ``hm_cells.run_all`` on the real tiers (``--controls real-tier``: the four protections whose effect runs through a real tier are broken one at a
                  time and must turn their cell red; ``none`` = measurement only): latencies are WALL CLOCKS (two lookups in two threads), not the model.
* ``generic``   - the spec's store-tier cells on the HM arm for ONE seed, time-boxed (``--box-s``), in the runner's row format; the window scores them into the
                  axes table and the winner clause exactly like an H arm's seed. Cells HM cannot answer (no conflict pass, no people graph, no disk) SKIP with the reason.
* ``driver``    - what the verbatim tier adds to the process: PSS before opening any store, after the workload, the peak RSS (HM-G0a), and the model calls the
                  background distiller made.

With ``--quiet-since EPOCH`` (the test hook: the live brain is shared with the household) the script stops when the panel has had a voice turn since that time.
Nothing here starts a service. ``ZMB_HM_LIVE_BRAIN=1`` is not read: the Hindsight server decides which brain it talks to.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

os.environ["ORT_DISABLE_TELEMETRY"] = "1"      # the bake-off venv's onnxruntime 1.30 uploads telemetry to Microsoft from C (first contact 2026-10-06): before anything loads it
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zmb import cells as cellmod, hm_cells, spec  # noqa: E402
from zmb.arms.hindsight import HindsightClient  # noqa: E402
from zmb.arms.hm import HindsightDistilledTier, HMArm  # noqa: E402
from zmb.arms.hm_policy import Controls  # noqa: E402
from zmb.arms.mempalace_verbatim import MemPalaceVerbatimArm, library_available  # noqa: E402
from zmb.arms.pg_store import PG_CONTAINER, ScratchPostgres  # noqa: E402
from zmb.runner import _row  # noqa: E402
from zmb.world import make_world  # noqa: E402

PANEL_GREP = "grep -a 'Wake word detected\\|Follow-up speech detected' /home/pi/.zoe-voice/voice.log | tail -1 | cut -c1-19"


def pss_mb(path: str = "/proc/self/smaps_rollup") -> float:
    try:
        for line in Path(path).read_text().splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def hwm_mb() -> float:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


class PanelGuard:
    """Stop when the panel had a voice turn after ``since`` (epoch). Polled at most every 30 s; an unreachable panel says nothing."""

    def __init__(self, since: "float | None", host: str = "zoe-pi", runner=subprocess.run):
        self.since, self.host, self.run, self._at = since, host, runner, -1e9

    def __call__(self) -> None:
        if self.since is None or time.monotonic() - self._at < 30.0:
            return
        self._at = time.monotonic()
        try:
            r = self.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", self.host, PANEL_GREP], capture_output=True, text=True, timeout=12)
            last = (r.stdout or "").strip().splitlines()[-1] if r.returncode == 0 and (r.stdout or "").strip() else ""
            when = time.mktime(time.strptime(last[:19], "%Y-%m-%d %H:%M:%S")) if last else 0.0
        except Exception:  # noqa: BLE001
            return
        if when > self.since - 30.0:
            raise SystemExit(f"a voice turn happened at {last} after the window started: stopping (the live brain is shared)")


def warm_shared_embedder() -> None:
    """Open one throwaway verbatim store, write and read one chunk, close it: loads the MiniLM session and the library's imports. Under adoption the verbatim tier is
    hosted in zoe-data, whose own MiniLM session is already loaded and SHARED (HM-G0a: 'in-process with the shared embedder <= 50 MB'), so the verbatim tier's RAM is what
    it adds on top of a warm embedder, not the cold load of an embedder zoe-data already has. The cold figure is reported beside it (``gross_added_mb``)."""
    arm = MemPalaceVerbatimArm(controls=Controls())
    try:
        arm.reset("demo_bar_00000000")
        store = arm._need()
        store.add("warm", "demo_bar_00000000", "chat", "warm up the embedder", {})
        store.search("warm", "demo_bar_00000000", 1, None)
    finally:
        arm.close()


def build_arm(url: str, pg: "ScratchPostgres | None", controls: "Controls | None" = None) -> HMArm:
    c = controls or Controls()
    return HMArm(distilled=HindsightDistilledTier(base_url=url, pg=pg), verbatim=MemPalaceVerbatimArm(controls=c), controls=c, real_latency=True)


def run_generic(url: str, pg: "ScratchPostgres | None", seed: str, box_s: float, smoke: int, guard) -> dict:
    from zmb.bakeoff_measure import interleave, pick_smoke
    from zmb import cells as cellmod
    store = [c for c in spec.load_cells() if c.tier == "store" and not cellmod.z0_only(c)]          # not Zoe's own quote-retirement cells (S10x): Z0 only
    world = make_world(seed)
    arm = build_arm(url, pg)
    ordered = pick_smoke(store, smoke) if smoke else interleave(store)
    t0 = time.monotonic()
    rows, unreachable = [], 0
    try:
        for cell in ordered:
            guard()
            if time.monotonic() - t0 >= box_s or unreachable >= 3:
                o = cellmod.Outcome("SKIP", reason="time box reached" if unreachable < 3 else "Hindsight unreachable")
            else:
                o = cellmod.run_cell(cell.rendered(world), world, arm)
                unreachable = unreachable + 1 if (o.verdict == "SKIP" and "cannot reach Hindsight" in o.reason) else 0
            rows.append(_row(cell, o))
    finally:
        arm.close()
    ran = sum(1 for r in rows if r["verdict"] != "SKIP")
    return {"seed": seed, "rows": rows, "cells_ran": ran, "cells_selected": len(rows), "duration_s": round(time.monotonic() - t0, 1), "unreachable": unreachable >= 3}


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:18888")
    ap.add_argument("--seed", default="zmb-v1")
    ap.add_argument("--box-s", type=float, default=300.0, help="time box of the generic store cells (0 = skip them)")
    ap.add_argument("--smoke", type=int, default=0, help="test hook: this many generic cells spread over the axes")
    ap.add_argument("--controls", choices=("all", "real-tier", "none"), default="real-tier")
    ap.add_argument("--only", default=None, help="run only the HM cells whose id contains this")
    ap.add_argument("--no-pg", action="store_true", help="no scratch Postgres handle (HM-F8 SKIPs)")
    ap.add_argument("--quiet-since", type=float, default=None)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    out: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "url": a.url}
    if not library_available():
        out["skipped"] = "the real MemPalace library is not importable in this interpreter (run through mp_run.sh: the bake-off venv)"
        a.out.write_text(json.dumps(out))
        return 0
    import mempalace
    out["library"] = f"mempalace {getattr(mempalace, '__version__', '?')}"
    guard = PanelGuard(a.quiet_since)
    pg = None if a.no_pg else ScratchPostgres(PG_CONTAINER)
    cold = pss_mb()
    try:
        warm_shared_embedder()
    except Exception as exc:  # noqa: BLE001 - a failed warm-up only makes the RAM figure gross
        out["warm_up"] = f"{type(exc).__name__}: {exc}"
    out["driver"] = {"pss_before_mb": round(pss_mb(), 1), "pss_cold_mb": round(cold, 1)}
    try:
        if not HindsightClient(a.url).health():
            raise RuntimeError("the Hindsight server does not answer /health")
        res = hm_cells.run_all("library", only=a.only, distilled=lambda: HindsightDistilledTier(base_url=a.url, pg=pg), real_latency=True,
                               controls=a.controls, guard=guard)
        out["hm_cells"] = res
        out["driver"]["pss_after_cells_mb"] = round(pss_mb(), 1)
        if a.box_s > 0:
            out["generic"] = run_generic(a.url, pg, a.seed, a.box_s, a.smoke, guard)
        out["driver"].update(pss_after_mb=round(pss_mb(), 1), peak_rss_mb=round(hwm_mb(), 1),
                             verbatim_added_mb=round(max(0.0, pss_mb() - out["driver"]["pss_before_mb"]), 1),
                             gross_added_mb=round(max(0.0, pss_mb() - out["driver"]["pss_cold_mb"]), 1),
                             peak_added_mb=round(max(0.0, hwm_mb() - out["driver"]["pss_before_mb"]), 1))
    except SystemExit as exc:
        out["aborted"] = str(exc)
    except NotImplementedError as exc:
        out["skipped"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - the window reads whatever was measured
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    a.out.write_text(json.dumps(out, default=str))
    print(f"hm_window: wrote {a.out} ({'aborted: ' + out['aborted'] if out.get('aborted') else 'error: ' + out['error'] if out.get('error') else 'ok'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
