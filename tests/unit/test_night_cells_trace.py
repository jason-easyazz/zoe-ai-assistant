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
