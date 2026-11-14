"""A test run never reads or writes the household's real forget-epoch file (2026-11-14: test runs that reached a real forget
path wrote five entries into it, one for a real member id, and a later test changed result depending on run order).

Two layers: the conftest gives every test its own empty store, and the module refuses the real path while pytest is running.
"""
from pathlib import Path

import pytest

import forget_session

pytestmark = pytest.mark.ci_safe


def test_every_test_gets_its_own_empty_store(tmp_path):
    assert Path(forget_session._PATH) != forget_session._REAL_PATH
    assert str(forget_session._PATH).startswith(str(tmp_path))
    assert forget_session._EPOCH == {} and forget_session.sidecar_session("member-x", "sess-7") == "sess-7"


def test_a_forget_in_a_test_lands_in_the_temporary_store_only(tmp_path):
    forget_session.note_forget("member-x")
    assert forget_session.sidecar_session("member-x", "sess-7").startswith("sess-7~f")
    assert (tmp_path / "forget_session_epochs.json").exists()


def test_the_module_refuses_the_real_path_under_pytest(tmp_path, monkeypatch):
    """With the conftest layer taken away (the path pointed back at "the real file"), nothing is read and nothing is written."""
    real = tmp_path / "home" / "forget_session_epochs.json"
    real.parent.mkdir()
    real.write_text('{"member-y": 1794643054242}')
    before = real.read_bytes()
    monkeypatch.setattr(forget_session, "_REAL_PATH", real)
    monkeypatch.setattr(forget_session, "_PATH", real)
    monkeypatch.setattr(forget_session, "_EPOCH", {})
    monkeypatch.setattr(forget_session, "_LOADED", False)
    assert forget_session._real_store_in_a_test()
    assert forget_session.sidecar_session("member-y", "sess-7") == "sess-7"      # the real file was not read
    forget_session.note_forget("member-x")
    assert real.read_bytes() == before                                           # and not written


def test_negative_control_outside_pytest_the_same_path_is_read_and_written(tmp_path, monkeypatch):
    real = tmp_path / "home" / "forget_session_epochs.json"
    real.parent.mkdir()
    real.write_text('{"member-y": 1794643054242}')
    monkeypatch.setattr(forget_session, "_REAL_PATH", real)
    monkeypatch.setattr(forget_session, "_PATH", real)
    monkeypatch.setattr(forget_session, "_EPOCH", {})
    monkeypatch.setattr(forget_session, "_LOADED", False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert not forget_session._real_store_in_a_test()
    assert forget_session.sidecar_session("member-y", "sess-7") == "sess-7~f1794643054242"
    forget_session.note_forget("member-x")
    assert "member-x" in real.read_text()
