"""The Zoe OS shadow test guard (used by conftest's per-test teardown and by test_os_shadow_test_isolation.py)."""
from __future__ import annotations

import os


def shadow_dir_violation() -> str:
    """'' when the Zoe OS shadow client points at a test directory; else what is wrong. The real install is ~/.zoe/os-shadow (and
    anything under ~/.zoe); a test's own directory is under the session store or pytest's tmp."""
    raw = os.environ.get("ZOE_OS_SHADOW_DIR", "")
    if not raw:
        return "ZOE_OS_SHADOW_DIR unset: the client falls back to ~/.zoe/os-shadow (the live install)"
    where = os.path.realpath(raw)
    live = os.path.realpath(os.path.join(os.path.expanduser("~"), ".zoe"))
    if where == live or where.startswith(live + os.sep):
        return "ZOE_OS_SHADOW_DIR points under ~/.zoe (the live install)"
    return ""
