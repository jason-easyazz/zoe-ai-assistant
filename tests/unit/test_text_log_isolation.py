"""Guard (PR #2110): no test may resolve the household's REAL text-log paths.

``MemoryService.delete_user`` and the forget cascade erase these household-wide; a root-suite run on the Zoe host with the paths
unpinned would strip the live router text and empty the live intent-miss log. ``tests/conftest.py`` pins them to a temp dir.
Break: delete the pins in tests/conftest.py -> every assertion here fails.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))


def test_text_log_paths_are_not_the_real_ones():
    import local_text_logs as ltl

    home = Path.home().resolve()
    live = {
        (home / "training" / "data" / "intent-misses.jsonl").resolve(),
        (Path(ltl.__file__).resolve().parent / "data" / "router_head_shadow.jsonl"),
    }
    for log in ltl.LOGS:
        assert ltl.log_path(log).resolve() not in live, f"{log.name} resolves to the live log"
        assert "zoe-test-stores-" in str(ltl.log_path(log).resolve())
    assert "zoe-test-stores-" in str(ltl.adhoc_backup_root().resolve())
    assert ltl.adhoc_backup_root().resolve() != (home / ".zoe" / "backups").resolve()
