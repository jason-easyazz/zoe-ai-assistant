"""The supervised gate's positive tests are marked ``needs_bwrap``. On a box that HAS a working bwrap they must run, never
skip silently, and the proof pipe must really cross the sandbox: bwrap keeps inherited (non-close-on-exec) file descriptors
open for the command it runs, which is what ``Popen(pass_fds=...)`` relies on (review of PR 2464)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from selfheal import sandbox


def _bwrap_works() -> bool:
    exe = shutil.which("bwrap")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "true"],
                              capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


def test_a_box_with_a_working_bwrap_does_not_skip_the_sandbox_tests():
    """If bwrap works here, the suite's skip condition must be false: otherwise every supervised test would be skipped and
    the lane would look green while proving nothing."""
    if not _bwrap_works():
        pytest.skip("no working bwrap on this box (CI): the needs_bwrap tests are skipped by design, and say so")
    assert sandbox.best_backend()[0] == "bwrap"
    from test_self_heal_sandbox import BWRAP

    assert BWRAP is True


def test_an_inherited_fd_survives_bwrap_and_carries_the_childs_bytes():
    """The fact the supervisor depends on: a pipe passed with pass_fds is still open inside bwrap, and what the sandboxed
    process writes arrives. If a bwrap ever closed it, every genuine pass would come back as ``no_proof``."""
    if not _bwrap_works():
        pytest.skip("no working bwrap on this box")
    r, w = os.pipe()
    try:
        proc = subprocess.run(
            [shutil.which("bwrap"), "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--unshare-pid",
             sys.executable, "-I", "-c", f"import os; os.write({w}, b'proof-crossed')"],
            pass_fds=(w,), capture_output=True, timeout=30)
        os.close(w)
        w = -1
        assert proc.returncode == 0, proc.stderr.decode()[-300:]
        assert os.read(r, 64) == b"proof-crossed"
    finally:
        os.close(r)
        if w >= 0:
            os.close(w)
