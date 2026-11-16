"""The suite never points the Zoe OS shadow client at the live install (~/.zoe/os-shadow): pinned in conftest before any import,
per test, and guarded on every test's teardown. These are the guard's own checks, with its negative controls."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from _shadow_guard import shadow_dir_violation
import os_shadow

pytestmark = pytest.mark.ci_safe


def test_every_test_runs_on_its_own_empty_shadow_dir(tmp_path):
    assert os_shadow.state_dir() == tmp_path / "os-shadow" and not os_shadow.shadow_state_exists()
    assert os_shadow.mode() == "off" and shadow_dir_violation() == ""


@pytest.mark.asyncio
async def test_a_member_forget_in_a_default_test_touches_no_kernel_and_writes_nothing(tmp_path):
    assert await os_shadow.forget_user("member-a") == 0
    assert not (tmp_path / "os-shadow").exists()                     # no park file, no lock file, no directory even


@pytest.mark.parametrize("value,bad", [("", True), ("~/.zoe/os-shadow", True), ("~/.zoe", True), ("~/.zoe/os-shadow/sub", True),
                                       ("TMP", False)])
def test_the_guard_flags_a_home_path_negative_control(monkeypatch, tmp_path, value, bad):
    if value == "":
        monkeypatch.delenv("ZOE_OS_SHADOW_DIR")
    else:
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(tmp_path) if value == "TMP" else os.path.expanduser(value))
    assert bool(shadow_dir_violation()) is bad
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(tmp_path / "os-shadow"))      # the teardown guard must not fire for this test


def test_the_collection_time_pin_precedes_the_import():
    src = (Path(__file__).parent / "conftest.py").read_text("utf-8")
    pin = src.index('os.environ["ZOE_OS_SHADOW_DIR"]')
    assert pin < src.index("import pytest  # noqa: E402")             # before conftest imports anything that imports the service
