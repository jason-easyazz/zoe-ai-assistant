"""HM verbatim-tier RAM probe (RAM lab, 2026-10-06): what the driver process holds when MemPalace's tier runs on its OWN ONNX session (the bench's default) against the SHARED
embedder (``ZMB_HM_EMBEDDER_URL`` = the loopback shim the Hindsight server already uses).

The real adapter (``MemPalaceVerbatimArm`` over the real MemPalace 3.10.0 library in a scratch palace) runs the shape of ``hm_window.py``'s driver: open one store and use it once
(the "warm" point: the library, Chroma and, when local, the ONNX session are loaded), then ingest ``--turns`` turns, answer ``--queries`` recalls (timed), forget a name (the palace
rebuild in a clean child) and close. PSS is read from ``/proc/self/smaps_rollup`` at each point; the peak is VmHWM. Run it through ``mp_run.sh`` (scrubbed HOME, no network):

    bash /home/zoe/.zoe/bakeoff-2026-10/mp_run.sh scripts/perf/zmb/pilot/hm_ram_probe.py                       # its own MiniLM session
    ZMB_HM_EMBEDDER_URL=http://127.0.0.1:11511 bash .../mp_run.sh scripts/perf/zmb/pilot/hm_ram_probe.py      # the shared embedder (the shim must be up)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

from zmb import needles  # noqa: E402
from zmb.arms.base import Turn  # noqa: E402
from zmb.arms.hm_policy import Controls  # noqa: E402
from zmb.arms.mempalace_verbatim import MemPalaceVerbatimArm, hardened_heap  # noqa: E402

USER = "demo_bar_00000001"


def mem() -> "dict[str, float]":
    out = {}
    for line in Path("/proc/self/smaps_rollup").read_text().splitlines():
        if line.startswith("Pss:"):
            out["pss_mb"] = int(line.split()[1]) / 1024.0
        elif line.startswith("Pss_Anon:"):
            out["anon_mb"] = int(line.split()[1]) / 1024.0
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            out["hwm_mb"] = int(line.split()[1]) / 1024.0
    return {k: round(v, 1) for k, v in out.items()}


def pct(xs: "list[float]", q: float) -> float:
    s = sorted(xs)
    return s[int((len(s) - 1) * q)] if s else 0.0


def two_tier(url: str, turns: "list[Turn]", nd) -> "dict":
    """The real HM arm (Hindsight over HTTP + the MemPalace library): per-ingest cost as the store grows, then the packet lanes' wall clocks."""
    from zmb.arms.hm import HMArm, HindsightDistilledTier
    c = Controls()
    arm = HMArm(distilled=HindsightDistilledTier(base_url=url, pg=None), verbatim=MemPalaceVerbatimArm(controls=c), controls=c, real_latency=True)
    out: "dict" = {}
    try:
        arm.reset(USER)
        step_ms: "list[float]" = []
        for i, t in enumerate(turns):
            t0 = time.perf_counter()
            arm.ingest([t])
            step_ms.append((time.perf_counter() - t0) * 1000.0)
        n = len(step_ms)
        out["ingest_per_turn_ms"] = {"first_20_p50": round(pct(step_ms[:20], 0.5), 1), "last_20_p50": round(pct(step_ms[-20:], 0.5), 1), "n": n}
        t0 = time.perf_counter()
        arm._refresh_cache(USER)
        out["cache_refresh_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        out["cache_rows"] = len(arm._cache)
        qs = [x.direct for x in nd] + [x.paraphrase for x in nd]
        chat, voice = [], []
        for i in range(120):
            q = qs[i % len(qs)]
            t0 = time.perf_counter()
            arm.packet(q, 5)
            chat.append((time.perf_counter() - t0) * 1000.0)
        for i in range(120):
            q = qs[i % len(qs)]
            t0 = time.perf_counter()
            arm.packet(q, 5, lane="voice")
            voice.append((time.perf_counter() - t0) * 1000.0)
        rm = arm.real_ms
        out["chat_packet_ms"] = {"p50": round(pct(chat, 0.5), 1), "p95": round(pct(chat, 0.95), 1)}
        out["voice_packet_ms"] = {"p50": round(pct(voice, 0.5), 3), "p95": round(pct(voice, 0.95), 3)}
        out["lookup_ms"] = {k: {"p50": round(pct(v, 0.5), 1), "p95": round(pct(v, 0.95), 1), "n": len(v)} for k, v in rm.items() if v}
        d95, v95, b95 = pct(rm["distilled"], 0.95), pct(rm["verbatim"], 0.95), pct(rm["both"], 0.95)
        out["second_lookup_over_slower_p95_ms"] = round(b95 - max(d95, v95) - arm.latency.merge, 1)       # what asking both costs over asking the slower tier alone
        out["hm_l2_added_p95_ms"] = round(b95 - d95 - arm.latency.merge, 1)                              # HM-L2's own definition: both vs the distilled tier alone (gate <= 25 ms)
    finally:
        arm.close()
    return out


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--turns", type=int, default=200)
    ap.add_argument("--queries", type=int, default=200)
    ap.add_argument("--url", default="", help="a loopback Hindsight server: also build the REAL two-tier HM arm on it and time the packet lanes (chat = both tiers, voice = the cache)")
    a = ap.parse_args(argv)
    out: "dict" = {"embedder": os.environ.get("ZMB_HM_EMBEDDER_URL") or "local MiniLM session", "hardened_heap": hardened_heap(), "start": mem()}
    import chromadb  # noqa: F401
    import mempalace  # noqa: F401
    out["after_imports"] = mem()
    nd = needles.corpus("hm-ram")
    turns = [Turn(text=n.fact, speaker="owner_voice_verified") for n in nd]
    turns += [Turn(text=c["text"], speaker="owner_voice_verified" if i % 2 else "owner_typed") for i, c in enumerate(needles.chatter("hm-ram", 900)) if c["kind"] != "chatter"]
    turns = turns[: a.turns]
    arm = MemPalaceVerbatimArm(controls=Controls())
    try:
        arm.reset(USER)
        store = arm._need()
        store.add("warm", USER, "chat", "warm up the embedder", {})
        store.search("warm", USER, 1, None)
        out["warm"] = mem()
        t0 = time.perf_counter()
        arm.ingest(turns)
        out["ingest_s"] = round(time.perf_counter() - t0, 2)
        out["after_ingest"] = mem()
        qs = [n.direct for n in nd] + [n.paraphrase for n in nd]
        lat, hits = [], 0
        for i in range(a.queries):
            q = qs[i % len(qs)]
            t0 = time.perf_counter()
            rows = arm.recall(q, 5)
            lat.append((time.perf_counter() - t0) * 1000.0)
            if i < len(qs):
                hits += any(nd[i % len(nd)].answer.lower() in r["text"].lower() for r in rows[:5])
        out["recall"] = {"n": len(lat), "p50_ms": round(pct(lat, 0.5), 1), "p95_ms": round(pct(lat, 0.95), 1), "hit_at_5": f"{hits}/{len(qs)}"}
        out["after_recalls"] = mem()
        t0 = time.perf_counter()
        arm.forget(nd[0].subject)
        out["forget_rebuild_s"] = round(time.perf_counter() - t0, 2)
        out["after_forget"] = mem()
    finally:
        arm.close()
    if a.url:
        out["two_tier"] = two_tier(a.url, turns, nd)
    out["end"] = mem()
    out["added_vs_warm_mb"] = round(out["after_forget"]["pss_mb"] - out["warm"]["pss_mb"], 1)
    out["gross_added_vs_start_mb"] = round(out["after_forget"]["pss_mb"] - out["start"]["pss_mb"], 1)
    out["peak_hwm_mb"] = out["end"]["hwm_mb"]
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
