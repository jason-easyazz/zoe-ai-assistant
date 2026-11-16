"""Make ``zoe_os`` importable for the lab's own tests (the directory name has a hyphen), and opt them into CI.

CI selection is by marker (``validate.yml`` runs ``... labs/zoe-os/tests tests/unit -m ci_safe``), so the lab's tests
get ``ci_safe`` here, in ONE place, instead of 32 hand-added ``pytestmark`` lines. Every test in this tree is
hosted-runner safe by construction: stdlib only, temp dirs, loopback-free Unix sockets, no model, no network, no
household data. The two that cannot run on a hosted runner are NOT dropped: the second-OS-user tests carry their
own ``skipif(geteuid() != 0)`` (a hosted runner is not root), so they show up as skipped with that reason. The
brain-lab-app Node test (``brain-lab-app/src/*.test.mjs``) is not Python and not in this lane (a follow-up).
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]


def pytest_collection_modifyitems(config, items):
    # This hook sees EVERY collected item in the session, so only touch this tree's own.
    for item in items:
        if HERE in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.ci_safe)
