"""One short, capped benchmark of the lab spine. Single process, temp dir, no model, no network.

    python3 labs/zoe-os/bench.py            # prints one JSON object

Hard caps: 60 s wall clock (each stage is skipped once the budget is spent and reported as
null) and 100k events (about 30 MB on disk). The numbers are for THIS box under whatever
load it carried at the time: a lab measure of the prototype, not a service level.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from zoe_os.capability import Enforcer, GrantStore, Person  # noqa: E402
from zoe_os.events import Event, Kind  # noqa: E402
from zoe_os.spine import Spine  # noqa: E402

BUDGET_S, TOTAL, SINGLE, PEOPLE = 60.0, 100_000, 10_000, 20


def main() -> dict:
    started = time.monotonic()
    left = lambda: BUDGET_S - (time.monotonic() - started)  # noqa: E731
    tmp = tempfile.mkdtemp(prefix="zoe-os-bench-")
    out: dict = {"events_target": TOTAL}
    try:
        people = [Person(f"p{i}") for i in range(PEOPLE)]
        store = GrantStore(people)
        store.install({"skill": "voice", "emits": ["utterance"], "origin": True})
        store.install({"skill": "reader", "reads": {"utterance": "private"}})
        for p in people:
            store.grant(p.id, "reader")
        spine = Spine(str(Path(tmp) / "bench.db"), Enforcer(store))

        def ev(i: int) -> Event:
            return Event(Kind.UTTERANCE, "voice", subject=f"p{i % PEOPLE}",
                         payload={"text": f"synthetic utterance number {i} with a little padding"})

        live = spine.subscribe("reader")
        t = time.perf_counter()
        for i in range(SINGLE):                      # one durable commit per event
            spine.publish(ev(i))
        out["publish_single_per_s"] = round(SINGLE / (time.perf_counter() - t))

        t, n = time.perf_counter(), 0
        while live.poll() is not None:               # a subscriber that fell behind catches up
            n += 1
        out["subscriber_delivered"] = n
        out["subscriber_per_s"] = round(n / (time.perf_counter() - t))
        out["subscriber_lagged"] = live.lagged
        live.close()

        t, done = time.perf_counter(), SINGLE
        while done < TOTAL and left() > 30:          # batches of 1000 in one transaction
            spine.publish_many(ev(i) for i in range(done, done + 1000))
            done += 1000
        out["events_written"] = done
        out["publish_batch_per_s"] = round((done - SINGLE) / (time.perf_counter() - t))

        if left() > 15:
            t = time.perf_counter()
            n = sum(1 for _ in spine.replay("reader"))
            out["replay_events"] = n
            out["replay_s"] = round(time.perf_counter() - t, 3)
        if left() > 8:
            t = time.perf_counter()
            result = spine.forget("p0", actor="p0")
            out["forget_events"] = result.count
            out["forget_ms"] = round((time.perf_counter() - t) * 1000, 1)
            out["replay_after_forget"] = sum(1 for _ in spine.replay("reader")) if left() > 5 else None
        out["db_mb"] = round(sum(p.stat().st_size for p in Path(tmp).iterdir()) / 1e6, 1)
        spine.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out["wall_s"] = round(time.monotonic() - started, 1)
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
