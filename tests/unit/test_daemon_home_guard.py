"""The autouse guard in tests/unit/conftest.py (``_daemon_writes_stay_in_tmp``) is EXACT: it fails a test that writes one of the
panel daemon's real ~/.zoe-voice files, and only that. A test that points HOME at a temp dir (and maybe writes there) passes - its
own monkeypatch is still in force when the guard's teardown runs, so the guard must compare the SAME absolute files it resolved
before the test (the 2026-11-16 CI failure: it re-resolved ``~`` at teardown and every HOME-redirecting test errored)."""
import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe


def _guard():
    spec = importlib.util.spec_from_file_location("_unit_conftest_guard", Path(__file__).with_name("conftest.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_test_that_redirects_home_is_not_flagged(tmp_path, monkeypatch):
    g = _guard()
    real = tmp_path / "real-home"
    (real / ".zoe-voice").mkdir(parents=True)
    (real / ".zoe-voice" / "face_profiles.json").write_text("{}")
    monkeypatch.setenv("HOME", str(real))
    paths = g._home_files()
    before = g._home_snapshot(paths)
    redirected = tmp_path / "test-home"                       # what the test under guard does
    monkeypatch.setenv("HOME", str(redirected))
    (redirected / ".zoe-voice").mkdir(parents=True)
    (redirected / ".zoe-voice" / "face_profiles.json").write_text('{"x": 1}')
    assert g._home_snapshot(paths) == before


def test_negative_control_a_real_write_is_still_caught(tmp_path, monkeypatch):
    g = _guard()
    real = tmp_path / "real-home"
    (real / ".zoe-voice").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(real))
    paths = g._home_files()
    before = g._home_snapshot(paths)
    (real / ".zoe-voice" / "speaker_shadow_metrics.jsonl").write_text('{"claimed": "member-a"}\n')
    assert g._home_snapshot(paths) != before, "a write to the real file must change the snapshot"
    (real / ".zoe-voice" / "speaker_shadow_metrics.jsonl").unlink()
    (real / ".zoe-voice" / "face_profiles.json.prune").write_text("")
    assert g._home_snapshot(paths) != before, "a left-behind prune file is caught too"
