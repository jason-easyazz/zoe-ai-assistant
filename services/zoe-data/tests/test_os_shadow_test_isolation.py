"""The suite never points the Zoe OS shadow client at the live install (~/.zoe/os-shadow): pinned in conftest before any import,
per test, and guarded on every test's teardown. These are the guard's own checks, with its negative controls."""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

import os_shadow

pytestmark = pytest.mark.ci_safe


def test_every_test_runs_on_its_own_empty_shadow_dir(tmp_path, shadow_dir_violation):
    assert os_shadow.state_dir() == tmp_path / "os-shadow" and not os_shadow.shadow_state_exists()
    assert os_shadow.mode() == "off" and shadow_dir_violation() == ""


@pytest.mark.asyncio
async def test_a_member_forget_in_a_default_test_touches_no_kernel_and_writes_nothing(tmp_path):
    assert await os_shadow.forget_user("member-a") == 0
    assert not (tmp_path / "os-shadow").exists()                     # no park file, no lock file, no directory even


@pytest.mark.parametrize("value,bad", [("", True), ("~/.zoe/os-shadow", True), ("~/.zoe", True), ("~/.zoe/os-shadow/sub", True),
                                       ("TMP", False)])
def test_the_guard_flags_a_home_path_negative_control(monkeypatch, tmp_path, value, bad, shadow_dir_violation):
    if value == "":
        monkeypatch.delenv("ZOE_OS_SHADOW_DIR")
    else:
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(tmp_path) if value == "TMP" else os.path.expanduser(value))
    assert bool(shadow_dir_violation()) is bad
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(tmp_path / "os-shadow"))      # the teardown guard must not fire for this test


def _pin_before_import(source):
    tree = ast.parse(source)
    pins = [n for n in tree.body if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute)
        and isinstance(t.value.value, ast.Name) and t.value.value.id == "os"
        and t.value.attr == "environ" and isinstance(t.slice, ast.Constant)
        and t.slice.value == "ZOE_OS_SHADOW_DIR" for t in n.targets)]
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
               and any(a.name == "pytest" for a in n.names)]
    return bool(pins and imports and min(n.lineno for n in pins) < min(n.lineno for n in imports))


def _without_pin(source, replacement):
    tree = ast.parse(source)
    pin = next(n for n in tree.body if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
        and t.slice.value == "ZOE_OS_SHADOW_DIR" for t in n.targets))
    lines = source.splitlines(keepends=True)
    lines[pin.lineno - 1:pin.end_lineno] = [replacement + "\n"]
    return "".join(lines)


def test_the_collection_time_pin_precedes_the_import():
    source = (Path(__file__).parent / "conftest.py").read_text("utf-8")
    assert _pin_before_import(source)
    for replacement in ('# os.environ["ZOE_OS_SHADOW_DIR"] = "safe"',
                        repr('os.environ["ZOE_OS_SHADOW_DIR"] = "safe"')):
        assert not _pin_before_import(_without_pin(source, replacement))
    moved = _without_pin(source, "pass") + '\nos.environ["ZOE_OS_SHADOW_DIR"] = "safe"\n'
    assert not _pin_before_import(moved)


ROOT = Path(__file__).resolve().parents[3]
CONFTESTS = [Path(__file__).parent / "conftest.py", ROOT / "tests" / "unit" / "conftest.py"]


def _run_pin(conftest: Path, monkeypatch, inherited) -> dict:
    """Run a conftest's shadow pin block (between its ``shadow-pin-start`` / ``-end`` markers) against an INHERITED environment, in
    a copy of os.environ, and return what it leaves."""
    import tempfile

    src = conftest.read_text("utf-8")
    block = src[src.index("# shadow-pin-start"):src.index("# shadow-pin-end")]
    env = dict(os.environ)
    if inherited is None:
        env.pop("ZOE_OS_SHADOW_DIR", None)
    else:
        env["ZOE_OS_SHADOW_DIR"] = inherited
    monkeypatch.setattr(os, "environ", env)
    exec(block, {"os": os, "tempfile": tempfile, "ZOE_TEST_STORE_DIR": tempfile.gettempdir()})
    return env


@pytest.mark.parametrize("conftest", CONFTESTS, ids=["zoe-data", "unit"])
@pytest.mark.parametrize("inherited", [os.path.expanduser("~/.zoe/os-shadow"), "", None], ids=["live", "empty", "unset"])
def test_an_inherited_live_or_empty_shadow_dir_never_survives_the_pin(conftest, monkeypatch, inherited):
    env = _run_pin(conftest, monkeypatch, inherited)
    assert env["ZOE_OS_SHADOW_DIR"] and env["ZOE_OS_LIST_SHADOW"] == "off"
    live = os.path.realpath(os.path.expanduser("~/.zoe"))
    assert not os.path.realpath(env["ZOE_OS_SHADOW_DIR"]).startswith(live)


def _assignment_not_setdefault(source):
    tree = ast.parse(source)
    has_pin = any(isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute)
        and isinstance(t.value.value, ast.Name) and t.value.value.id == "os"
        and t.value.attr == "environ" and isinstance(t.slice, ast.Constant)
        and t.slice.value == "ZOE_OS_SHADOW_DIR" for t in n.targets) for n in tree.body)
    return has_pin and not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                              and n.func.attr == "setdefault" for n in ast.walk(tree))


def test_the_pin_is_an_assignment_never_a_setdefault():
    for conftest in CONFTESTS:
        source = conftest.read_text("utf-8")
        block = source[source.index("# shadow-pin-start"):source.index("# shadow-pin-end")]
        assert _assignment_not_setdefault(block), conftest
        for replacement in ('# os.environ["ZOE_OS_SHADOW_DIR"] = "safe"',
                            repr('os.environ["ZOE_OS_SHADOW_DIR"] = "safe"'),
                            'os.environ.setdefault("ZOE_OS_SHADOW_DIR", "safe")'):
            assert not _assignment_not_setdefault(_without_pin(block, replacement))



@pytest.mark.parametrize("value", ["", "~/.zoe/os-shadow", None], ids=["empty", "live", "unset"])
def test_the_guard_records_a_client_use_against_a_real_dir_negative_control(monkeypatch, value, shadow_guard_uses):
    """A USE of the client while the env points at the live install is recorded (and would fail the test at teardown); clearing the
    env alone, as the flag-default smoke test does, is not."""
    if value is None:
        monkeypatch.delenv("ZOE_OS_SHADOW_DIR")
    else:
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", os.path.expanduser(value))
    assert shadow_guard_uses == []                                       # cleared env, client untouched: nothing recorded
    os_shadow.state_dir()                                                # the client reads its dir: recorded
    assert len(shadow_guard_uses) == 1
    del shadow_guard_uses[:]                                             # this test's own control must not fail its teardown
