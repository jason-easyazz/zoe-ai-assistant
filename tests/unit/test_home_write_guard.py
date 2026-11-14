"""The home-write guard (tools/ci/home_write_guard.py): a test that writes under the real home directory or into the
checkout fails. These tests drive the guard against a temporary directory registered as a guarded root, so nothing here
touches the real home.

Negative controls: a write outside every guarded root, a read, and an allowlisted cache path are NOT recorded; with the
guard's recording switched off the same write is not seen (so the positive tests are the guard's own work).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
guard = sys.modules["zoe_home_write_guard"]          # loaded and installed by tests/conftest.py


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A directory the guard treats as a home directory for this test only."""
    state = guard._state()
    home = tmp_path / "home"
    home.mkdir()
    guard.take()                                      # start from a clean slate
    monkeypatch.setattr(state, "homes", state.homes + (str(home),))
    yield home
    guard.take()                                      # what this test wrote on purpose is not the lane fixture's business


def _paths(violations):
    return {(event, os.path.relpath(path, start=os.path.dirname(path))) for event, path, _thread in violations}


def test_this_lane_installed_the_guard_and_guards_the_checkout():
    state = guard._state()
    assert state is not None and state.enabled
    assert os.path.realpath(REPO) in state.checkouts
    assert any(os.path.realpath(os.path.expanduser("~")) == h for h in state.homes) or os.environ.get("HOME") is None


def test_every_kind_of_write_under_a_guarded_home_is_recorded(fake_home):
    (fake_home / ".zoe-state").mkdir()
    (fake_home / ".zoe-state" / "state.json").write_text("{}")
    with open(fake_home / ".zoe-state" / "state.json", "a") as fh:
        fh.write("x")
    os.rename(fake_home / ".zoe-state" / "state.json", fake_home / ".zoe-state" / "moved.json")
    os.symlink("moved.json", fake_home / ".zoe-state" / "link")
    sqlite3.connect(fake_home / ".zoe-state" / "db.sqlite").close()
    os.remove(fake_home / ".zoe-state" / "link")
    shutil.rmtree(fake_home / ".zoe-state")                 # removes relative to a directory fd: still resolved to the real path
    got = guard.take()
    events = {e for e, _p, _t in got}
    assert {"os.mkdir", "open for writing", "os.rename", "os.symlink", "sqlite3.connect", "os.remove", "os.rmdir"} <= events
    assert all(p.startswith(str(fake_home) + os.sep) for _e, p, _t in got)
    assert ("os.remove", "moved.json") in _paths(got)                  # the rmtree leg, by its full path
    assert "moved.json" not in {p for e, p, _t in got if e == "os.symlink"}   # a symlink's TARGET is only named, not written


def test_a_write_from_another_thread_is_recorded_with_the_thread_name(fake_home):
    import threading
    t = threading.Thread(target=lambda: (fake_home / "late.txt").write_text("x"), name="late-saver")
    t.start()
    t.join(10)
    assert [(e, os.path.basename(p), th) for e, p, th in guard.take()] == [("open for writing", "late.txt", "late-saver")]


def test_reads_caches_and_writes_elsewhere_are_not_recorded(fake_home, tmp_path):
    (tmp_path / "elsewhere.txt").write_text("fine")                    # outside every guarded root
    (fake_home / ".cache").mkdir()
    (fake_home / ".cache" / "tool.bin").write_text("cache")            # an allowlisted cache
    (fake_home / "pkg" / "__pycache__").mkdir(parents=True)
    (fake_home / "pkg" / "__pycache__" / "m.cpython-310.pyc").write_bytes(b"")
    got = guard.take()
    assert [os.path.basename(p) for _e, p, _t in got] == ["pkg"]       # only the real directory under the home
    (fake_home / ".cache" / "zoe").mkdir()
    (fake_home / ".cache" / "zoe" / "voice_probe_selftest_trend.jsonl").write_text("{}")    # Zoe's own record: not a cache
    assert [os.path.basename(p) for _e, p, _t in guard.take()] == ["zoe", "voice_probe_selftest_trend.jsonl"]
    (fake_home / "seen.txt").write_text("x")
    guard.take()
    assert (fake_home / "seen.txt").read_text() == "x"                 # a read
    assert os.path.exists(fake_home / "seen.txt") and guard.take() == []


def test_a_test_that_points_home_at_a_temporary_directory_is_not_affected(tmp_path, monkeypatch):
    """The real home is fixed when the guard is installed: the hermetic shape (HOME on tmp_path) stays free to write."""
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    guard.take()
    target = Path(os.path.expanduser("~")) / ".zoe" / "x.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}")
    assert guard.take() == []


def test_a_write_into_the_checkout_is_refused_wherever_the_checkout_sits(tmp_path, monkeypatch):
    state = guard._state()
    checkout = tmp_path / "checkout"
    (checkout / "services" / "zoe-data" / "data").mkdir(parents=True)
    guard.take()
    monkeypatch.setattr(state, "checkouts", [*state.checkouts, str(checkout)])
    (checkout / "services" / "zoe-data" / "data" / "music_prefs.json").write_text("{}")
    (checkout / ".pytest_cache").mkdir()                               # pytest's own cache is not a leak
    got = guard.take()
    assert [os.path.basename(p) for _e, p, _t in got] == ["music_prefs.json"]


def test_negative_control_with_recording_off_the_same_write_is_not_seen(fake_home, monkeypatch):
    monkeypatch.setattr(guard._state(), "enabled", False)
    (fake_home / "unseen.txt").write_text("x")
    assert guard.take() == []
    monkeypatch.setattr(guard._state(), "enabled", True)
    (fake_home / "seen.txt").write_text("x")
    assert len(guard.take()) == 1


def test_the_lane_fixture_fails_the_test_that_wrote_and_names_the_path(pytester_like, fake_home):
    """End to end in a child pytest: the clean test is untouched, the leaking one is an ERROR that names the path, and the
    run exits non-zero."""
    rc, result = pytester_like(fake_home)
    assert rc == 1 and "1 error" in result and "ERROR at teardown of test_leaks" in result, result
    assert "test_clean" not in result.split("short test summary")[-1]
    assert "leaked.json" in result and "wrote under the real home directory or the checkout during this test" in result


@pytest.fixture
def pytester_like(tmp_path):
    def run(home):
        suite = tmp_path / "suite"
        suite.mkdir()
        (suite / "conftest.py").write_text(
            "import importlib.util, os, sys, pytest\n"
            f"spec = importlib.util.spec_from_file_location('g', {str(REPO / 'tools' / 'ci' / 'home_write_guard.py')!r})\n"
            "g = importlib.util.module_from_spec(spec); spec.loader.exec_module(g); g.install()\n"
            "@pytest.fixture(autouse=True)\n"
            "def _guard():\n    yield from g.fixture_body()\n")
        (suite / "test_x.py").write_text(
            "import os, pathlib\n"
            "def test_clean(tmp_path):\n    (tmp_path / 'ok.txt').write_text('x')\n"
            "def test_leaks():\n    pathlib.Path(os.path.expanduser('~'), 'leaked.json').write_text('{}')\n")
        import subprocess
        env = {**os.environ, "HOME": str(home), "TMPDIR": str(tmp_path / "t"), "PYTHONDONTWRITEBYTECODE": "1"}
        (tmp_path / "t").mkdir()
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(suite), str(suite)],
                              capture_output=True, text=True, env=env, cwd=suite, timeout=120)
        return proc.returncode, proc.stdout + proc.stderr
    return run


def test_a_temp_dir_inside_the_home_is_exempt_but_a_home_inside_the_temp_dir_is_still_guarded(fake_home, monkeypatch):
    """A CI runner's temp sits under its home (writes there are fine); a scratch home or checkout under /tmp is still guarded.
    The second half is the bug this guard shipped with for an hour: every path under the system temp dir was exempt, so a
    checkout in /tmp was not guarded at all."""
    state = guard._state()
    runner_temp = fake_home / "work" / "_temp"
    runner_temp.mkdir(parents=True)
    guard.take()
    monkeypatch.setattr(state, "temp_roots", (*state.temp_roots, str(runner_temp)))
    (runner_temp / "ledger.json").write_text("{}")
    assert guard.take() == []
    assert any(str(fake_home).startswith(t + os.sep) for t in state.temp_roots)        # the fake home IS under the temp dir ...
    (fake_home / "state.json").write_text("{}")
    assert [os.path.basename(p) for _e, p, _t in guard.take()] == ["state.json"]       # ... and is guarded all the same
