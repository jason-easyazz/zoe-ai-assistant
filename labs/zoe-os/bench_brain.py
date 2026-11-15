"""Latency the kernel hop adds to one brain action. One kernel process, one brain adapter.

    python3 labs/zoe-os/bench_brain.py [actions]        # default 300; prints one JSON object

``propose`` is timed end to end from the brain's side: argument check, the ``act`` frame over the
socket, the kernel's authorisation, the write-ahead intent, the driver, the read-back, the receipt
append (SQLite commit) and the reply. ``direct_driver`` is the same driver call made in-process
with none of that, i.e. what a tool that writes a store directly costs. The difference is the hop.
``record_utterance`` is the voice surface's own publish that every turn already needs: it is not
added by the bridge. Numbers are for this box under whatever load it carried: NOT a service level.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]

from brain_rig import BrainBox  # noqa: E402
from world import ALEX, SAM  # noqa: E402

from zoe_os.brain.drivers import Lists  # noqa: E402


def stats(us: list[float]) -> dict:
    q = statistics.quantiles(us, n=100)
    return {"n": len(us), "p50_us": round(statistics.median(us)), "p95_us": round(q[94]),
            "p99_us": round(q[98]), "max_us": round(max(us))}


def main(n: int) -> dict:
    box = BrainBox()
    try:
        box.household()
        bridge = box.bridge()
        bridge.tools()                                  # the catalog is fetched once, like a warm brain
        calls = [("list_add", {"item": "milk"}), ("remind_set", {"note": "stretch", "in_minutes": 5}),
                 ("alarm_set", {"time": "06:30"})]
        utter, hop, by_tool = [], [], {name: [] for name, _ in calls}
        for i in range(n):
            who = (ALEX, SAM)[i % 2]
            t = time.perf_counter_ns()
            turn = box.say(who, f"synthetic request number {i}")
            utter.append((time.perf_counter_ns() - t) / 1000)
            tool, args = calls[i % 3]
            t = time.perf_counter_ns()
            out = bridge.propose(turn, tool, args)
            us = (time.perf_counter_ns() - t) / 1000
            assert out.status == "done", out
            hop.append(us)
            by_tool[tool].append(us)
        lists, direct = Lists(), []
        for i in range(n):
            t = time.perf_counter_ns()
            lists.add(ALEX, {"item": "milk", "asked": i})
            direct.append((time.perf_counter_ns() - t) / 1000)
        return {"propose_all": stats(hop), **{f"propose_{k}": stats(v) for k, v in by_tool.items()},
                "record_utterance": stats(utter), "direct_driver_in_process": stats(direct)}
    finally:
        box.close()


if __name__ == "__main__":
    print(json.dumps(main(int(sys.argv[1]) if len(sys.argv) > 1 else 300), indent=1))
