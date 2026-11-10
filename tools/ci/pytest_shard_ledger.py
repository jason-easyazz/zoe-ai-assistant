"""pytest plugin: deterministic weighted SHARDING plus a LEDGER of what this pytest process selected and ran.

Loaded with ``-p pytest_shard_ledger`` (``tools/ci`` on ``PYTHONPATH``). Both halves are off unless asked for.

Sharding — ``--shard-count N --shard-index I`` (1-based) keeps one CONTIGUOUS slice of the ordered selection (after
``-m``/``-k`` deselection), so a shard runs the tests in exactly the order the unsharded lane ran them. Slices are cut
by estimated time, not test count: ``--shard-weights=PATH`` (always with ``=``) (``tools/ci/shard_weights.json``) maps a test FILE to its
measured seconds, spread evenly over the tests that file contributes now; a file it does not list costs
``--shard-default-s`` per test. The cut is positional, so it is stable even though a few tests mint random parametrize
ids per process, and a stale weights file only makes the slices less even — it can never drop or repeat a test.

Ledger — ``PYTEST_SHARD_LEDGER=<path>`` writes the node ids this process selected (``pytest_collection_finish``,
after deselection and sharding), the ids that produced a report, per-test durations, and the exit status.
``tools/ci/shard_ledger.py verify`` (run by the required ``validate`` aggregator) proves the shards partition the
``collect`` job's full selection exactly; ``tools/ci/shard_ledger.py weights`` turns shard ledgers into a fresh
weights file.

Pure stdlib; no effect on what any test does.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_ENV = "PYTEST_SHARD_LEDGER"


def pytest_addoption(parser) -> None:
    g = parser.getgroup("zoe-shards")
    g.addoption("--shard-count", type=int, default=0, help="split the selection into N contiguous time-weighted slices")
    g.addoption("--shard-index", type=int, default=0, help="1-based slice to keep")
    g.addoption("--shard-weights", default="", help="JSON {test file: seconds} used to cut the slices")
    g.addoption("--shard-default-s", type=float, default=0.05, help="estimated seconds per test of an unlisted file")


def item_file(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def estimate(nodeids: list[str], weights: dict[str, float], default_s: float) -> list[float]:
    """Per-test estimated seconds: a listed file's weight spread evenly over its tests in THIS selection."""
    per_file: dict[str, int] = {}
    for n in nodeids:
        f = item_file(n)
        per_file[f] = per_file.get(f, 0) + 1
    out = []
    for n in nodeids:
        f = item_file(n)
        w = weights.get(f)
        out.append(max(float(w), 0.0) / per_file[f] if w is not None else default_s)
    return out


def slice_bounds(costs: list[float], count: int) -> list[tuple[int, int]]:
    """``count`` contiguous [start, end) slices of ``costs`` with near-equal sums; every slice non-empty when
    ``len(costs) >= count``. Pure function of its inputs, so every shard computes the same cut."""
    n = len(costs)
    if count < 1:
        raise ValueError("count must be >= 1")
    total = sum(costs)
    cuts, i, acc = [0], 0, 0.0
    for k in range(1, count):
        target = total * k / count
        while i < n and acc + costs[i] / 2 < target:   # an item goes to the slice holding its midpoint
            acc += costs[i]
            i += 1
        # every slice keeps >= 1 test while enough tests remain for the slices still to come
        lo = cuts[-1] + 1 if n - cuts[-1] > count - k else cuts[-1]
        hi = max(n - (count - k), lo)
        c = min(max(i, lo), hi, n)
        if c != i:
            i, acc = c, sum(costs[:c])
        cuts.append(c)
    cuts.append(n)
    return list(zip(cuts[:-1], cuts[1:]))


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items) -> None:
    count = config.getoption("shard_count")
    if not count:
        return
    index = config.getoption("shard_index")
    if not 1 <= index <= count:
        raise pytest.UsageError(f"--shard-index must be in 1..{count}, got {index}")
    weights: dict[str, float] = {}
    path = config.getoption("shard_weights")
    if path and "--shard-weights" in [str(a) for a in config.invocation_params.args]:
        # `--shard-weights PATH` (space) is read as a positional TEST path while pytest picks its rootdir, which can
        # shift every node id; refuse rather than shard a different tree than the collect job saw.
        raise pytest.UsageError("pass the weights as --shard-weights=PATH (with '='), not as a separate argument")
    if path:
        weights = json.loads(Path(path).read_text(encoding="utf-8")).get("files", {})
    costs = estimate([i.nodeid for i in items], weights, config.getoption("shard_default_s"))
    start, end = slice_bounds(costs, count)[index - 1]
    keep, drop = items[start:end], items[:start] + items[end:]
    items[:] = keep
    if drop:
        config.hook.pytest_deselected(items=drop)
    tr = config.pluginmanager.get_plugin("terminalreporter")
    if tr is not None:
        tr.write_line(f"[zoe-shards] shard {index}/{count}: {len(keep)} tests, estimated {sum(costs[start:end]):.0f}s "
                      f"of {sum(costs):.0f}s ({len(weights)} weighted files)")


class _Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.selected: list[str] = []
        self.ran: set[str] = set()
        self.durations: dict[str, float] = {}
        self.collect_only = False

    def pytest_collection_finish(self, session) -> None:
        self.selected = [item.nodeid for item in session.items]
        self.collect_only = bool(session.config.getoption("collectonly", False))
        self._write(exitstatus=None)

    def pytest_runtest_logreport(self, report) -> None:
        self.durations[report.nodeid] = self.durations.get(report.nodeid, 0.0) + float(report.duration or 0.0)
        # A test "ran" once it produced its call report, or was skipped/errored before reaching it.
        if report.when == "call" or (report.when == "setup" and not report.passed):
            self.ran.add(report.nodeid)

    def pytest_sessionfinish(self, session, exitstatus) -> None:
        self._write(exitstatus=int(exitstatus))

    def _write(self, exitstatus) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "selected": self.selected,
            "ran": sorted(self.ran),
            "durations": {k: round(v, 4) for k, v in sorted(self.durations.items())},
            "collect_only": self.collect_only,
            "exitstatus": exitstatus,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=0), encoding="utf-8")
        tmp.replace(self.path)


def pytest_configure(config) -> None:
    target = os.environ.get(_ENV, "").strip()
    if target:
        config.pluginmanager.register(_Ledger(Path(target)), "zoe-shard-ledger")
