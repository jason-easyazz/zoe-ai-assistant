"""CI wrapper: run the sign-in security node harness against the real
index.html / auth.html.

Guards a remotely-triggerable authentication bypass (four catch blocks that
fabricated a localStorage session whenever zoe-auth was unreachable -- the
guest path did so with no credential check at all) and a reflected DOM XSS via
the ?setup= parameter. Repo rule: security-touching changes carry tests.

Unlike the other node harness wrappers, a missing node interpreter FAILS on CI
rather than skipping -- a silently skipped auth-bypass guard is worse than no
guard, because it reads green.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "zoe-ui" / "dist" / "test_auth_no_demo_bypass.js"


def test_auth_no_demo_bypass_node_harness():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        if os.environ.get("CI"):
            pytest.fail(
                "Node.js is required on CI to run the sign-in security harness "
                f"({HARNESS.relative_to(ROOT)}); refusing to skip an auth-bypass guard."
            )
        pytest.skip("Node.js is not installed on this host")

    assert HARNESS.is_file(), f"harness missing: {HARNESS}"

    proc = subprocess.run(
        [node, str(HARNESS)], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, (
        f"sign-in security harness failed:\n{proc.stdout}\n{proc.stderr}"
    )
    assert "checks passed" in proc.stdout
