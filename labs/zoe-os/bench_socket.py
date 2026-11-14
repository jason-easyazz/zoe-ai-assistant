"""Per-request latency of the socket hop. One kernel process, one client, no concurrency.

    python3 labs/zoe-os/bench_socket.py [requests-per-op]     # prints one JSON object

Each op is timed request by request (send, kernel work, reply) with ``time.perf_counter_ns``
and reported as p50 / p95 / p99 / max in microseconds. ``publish_in_process`` is the same
publish with no socket and no second process, for the difference. The numbers are for the box
this ran on under whatever load it carried: NOT a service level and NOT the target hardware
unless it was run there.
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]

from procs import HELPER, Box  # noqa: E402
from world import SAM, emit, household  # noqa: E402

from zoe_os.client import Client  # noqa: E402
from zoe_os.events import Kind  # noqa: E402


def timed(n: int, fn) -> dict:
    us = []
    for i in range(n):
        t = time.perf_counter_ns()
        fn(i)
        us.append((time.perf_counter_ns() - t) / 1000)
    q = statistics.quantiles(us, n=100)
    return {"n": n, "p50_us": round(statistics.median(us)), "p95_us": round(q[94]),
            "p99_us": round(q[98]), "max_us": round(max(us))}


def main(n: int) -> dict:
    out: dict = {}
    box = Box()
    try:
        panel, voice, helper = box.client("panel"), box.client("voice"), box.client(HELPER)
        panel.grant(SAM, HELPER)
        text = {"text": "synthetic utterance with a little padding to sixty bytes"}
        cause = voice.publish("utterance", subject=SAM, payload=text).seq
        sub = helper.subscribe(kinds=["utterance"])
        out["publish"] = timed(n, lambda i: voice.publish("utterance", subject=SAM, payload=text))
        out["poll_one_event"] = timed(n, lambda i: helper.poll(sub))
        out["poll_empty"] = timed(n, lambda i: helper.poll(sub))
        out["explain_standing"] = timed(n, lambda i: helper.explain())
        out["refused_publish"] = timed(n, lambda i: _refused(helper))
        out["act_with_fsync_device"] = timed(max(n // 10, 20), lambda i: helper.act(
            "light.toggle", SAM, {"room": "hall"}, caused_by=[cause]))
        cred = bytes.fromhex(box.creds[HELPER])
        out["connect_and_handshake"] = timed(max(n // 10, 20), lambda i: Client(
            box.socket, HELPER, cred).close())
    finally:
        box.close()
    with tempfile.TemporaryDirectory() as tmp:
        k = household(Path(tmp))
        out["publish_in_process"] = timed(n, lambda i: emit(k, Kind.UTTERANCE, "voice", SAM, **text))
        k.close()
    return out


def _refused(client) -> None:
    try:
        client.publish("utterance", subject=SAM, payload={"text": "not in its manifest"})
    except Exception:
        pass


if __name__ == "__main__":
    print(json.dumps(main(int(sys.argv[1]) if len(sys.argv) > 1 else 2000), indent=1))
