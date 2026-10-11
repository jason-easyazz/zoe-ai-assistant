"""``zoe-night-mind.py --cells --trace FILE``: every model call's prompt and reply, each pass's threads + changes, each cell's final state - for the lab's invented household only.

A failed cell used to leave only its score; diagnosing the 12B's K9f needed a guess. The trace is a JSONL file: ``kind=call`` (messages, reply), ``kind=plan`` (groups, threads,
mention days, changes) and ``kind=cell_end`` (verdict, final threads, changes), each tagged with its ``run`` and ``cell``. Off by default, refused outside ``--cells`` (a member's
words must never be written to a file), and a sink that raises never changes a night.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "services" / "zoe-data"))
sys.path.insert(0, str(REPO / "scripts" / "perf"))

import night_mind as nm  # noqa: E402
from zmb.night_brain import FakeNightBrain, FakeNightServer  # noqa: E402


def _run_py(args):
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "PYTHONPATH", "TZ", "LANG")}
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, env=env, timeout=240, cwd=str(REPO))


def test_trace_records_every_call_every_plan_and_the_cells_final_threads(tmp_path):
    out = tmp_path / "trace.jsonl"
    srv = FakeNightServer(FakeNightBrain())
    try:
        r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--ctx-tokens", "8192", "--cells", "--only", "K9f", "--runs", "2",
                     "--decode-tok-s", "50", "--prefill-tok-s", "900", "--trace", str(out)])
        assert r.returncode == 0, r.stderr[-800:]
        assert len(r.stdout.strip().splitlines()) == 1                                              # stdout is still the one JSON line
        cells = json.loads(r.stdout)["cells"]
    finally:
        srv.close()
    recs = [json.loads(x) for x in out.read_text().splitlines()]
    assert {r["run"] for r in recs} == {0, 1} and {r["cell"] for r in recs} == {"K9f"}
    calls = [r for r in recs if r["kind"] == "call"]
    assert calls and all(c["messages"][-1]["role"] == "user" and "reply" in c for c in calls)         # the prompt AND the reply of each call
    assert any("TASK: THREADS" in c["messages"][-1]["content"] for c in calls) and any("TASK: MOMENTS" in c["messages"][-1]["content"] for c in calls)
    plans = [r for r in recs if r["kind"] == "plan"]
    assert len(plans) == 2 and all(p["threads"] and p["groups"] and "mention_days" in p and "changes" in p for p in plans)
    ends = [r for r in recs if r["kind"] == "cell_end"]
    assert len(ends) == 2 and all(e["verdict"] in ("PASS", "FAIL") and e["threads"] for e in ends)
    assert [e["verdict"] for e in ends] == cells["votes"]["K9f"]                                      # the trace agrees with the votes the run printed


@pytest.mark.skipif(not os.path.exists("/dev/full"), reason="needs /dev/full (a file whose every write fails)")
def test_a_trace_file_that_stops_accepting_writes_never_loses_a_cells_verdict(tmp_path):
    """Greptile round 4 on #1982: ``emit`` raising inside the cell_end block, then again in its fallback (and ``close()`` flushing into the same dead sink), used to escape
    before the NIGHT_CELL line - a finished cell's vote lost. ``/dev/full`` fails every write with ENOSPC, so this exercises all three sites in a real run."""
    ref, dead = tmp_path / "ref.jsonl", "/dev/full"
    votes = []
    for trace in (str(ref), dead):
        srv = FakeNightServer(FakeNightBrain())
        try:
            r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--model-url", srv.url, "--ctx-tokens", "8192", "--cells", "--only", "K9f", "--runs", "1",
                         "--decode-tok-s", "50", "--prefill-tok-s", "900", "--trace", trace])
            assert r.returncode == 0, (trace, r.stderr[-800:])
            votes.append(json.loads(r.stdout)["cells"]["votes"]["K9f"])
        finally:
            srv.close()
    assert votes[1] == votes[0] and votes[1][0] in ("PASS", "FAIL")                                  # the dead sink recorded the same verdict as a healthy one


def test_trace_is_refused_outside_cells_and_nothing_is_written(tmp_path):
    out = tmp_path / "t.jsonl"
    r = _run_py(["scripts/maintenance/zoe-night-mind.py", "--user", "someone", "--dry-run", "--trace", str(out)])
    assert r.returncode != 0 and "--trace" in (r.stderr + r.stdout) and not out.exists()


def test_the_trace_seam_is_off_by_default_and_a_broken_sink_never_changes_a_night(caplog):
    assert nm._TRACE is None
    seen = []
    prev = nm.set_trace(lambda rec: seen.append(rec))
    try:
        nm._trace({"kind": "call"})
        assert seen == [{"kind": "call"}]
        nm.set_trace(lambda rec: 1 / 0)
        nm._trace({"kind": "call"})                                                                    # swallowed (logged), no exception reaches the night
    finally:
        nm.set_trace(prev)
    assert nm._TRACE is None


def test_k12_labelling_call_writes_a_call_record_and_a_dead_sink_changes_no_label(monkeypatch):
    """Greptile on #1982 (P2): ``Z0Arm.moment_labels`` calls ``_complete`` directly, bypassing the ``kind=call`` record ``_run`` writes, so ``--trace`` had no K12 prompt or reply."""
    from zmb.arms.z0 import Z0Arm  # noqa: E402

    async def fake_complete(messages, max_tokens, cfg, usage, *, schema=None):
        return json.dumps({"moments": [{"ids": ["m1"], "quote": "I am so proud of finishing the course.", "kind": "other", "who": [], "feeling": "joy",
                                        "weight": 3, "later": "na"}]}, separators=(",", ":"))
    monkeypatch.setattr(nm, "_complete", fake_complete)
    recs = []
    prev = nm.set_trace(lambda rec: recs.append(rec))
    arm = Z0Arm(name="Z0n", night=True, night_url="http://127.0.0.1:1", night_ctx=8192)
    try:
        labels = arm.moment_labels(["I am so proud of finishing the course."])
        calls = [r for r in recs if r["kind"] == "call"]
        assert len(calls) == 1 and "reply" in calls[0] and calls[0]["messages"][-1]["role"] == "user" and "TASK: MOMENTS" in calls[0]["messages"][-1]["content"]
        assert calls[0]["max_tokens"] > 0 and "proud of finishing" in calls[0]["reply"]
        nm.set_trace(lambda rec: 1 / 0)                                                                # a dead sink never changes a verdict
        assert arm.moment_labels(["I am so proud of finishing the course."]) == labels and labels
    finally:
        arm.close()
        nm.set_trace(prev)
