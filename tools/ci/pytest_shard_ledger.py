"""pytest plugin: write a LEDGER of what this pytest process selected and what it actually ran.

Loaded with ``-p pytest_shard_ledger`` (``tools/ci`` on ``PYTHONPATH``) and armed by
``ZOE_SHARD_LEDGER=<path>``; unset, it does nothing. Used by ``.github/workflows/validate.yml``:

* every shard of a split lane writes the node ids pytest-split handed it (``selected``, recorded at
  ``pytest_collection_finish`` — after ``-m ci_safe`` and after the split) and the ids that produced a
  report (``ran``);
* the ``collect`` job writes the same lane's FULL ``--collect-only`` selection;
* ``tools/ci/shard_ledger.py verify`` (run by the required ``validate`` aggregator) proves the shards
  partition the full selection exactly — nothing dropped, nothing run twice — and that every selected
  test reported.

Pure stdlib; no effect on test behaviour.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_ENV = "ZOE_SHARD_LEDGER"


class _Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.selected: list[str] = []
        self.ran: set[str] = set()
        self.collect_only = False

    def pytest_collection_finish(self, session) -> None:
        self.selected = [item.nodeid for item in session.items]
        self.collect_only = bool(session.config.getoption("collectonly", False))
        self._write(exitstatus=None)

    def pytest_runtest_logreport(self, report) -> None:
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
