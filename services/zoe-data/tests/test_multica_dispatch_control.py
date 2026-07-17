import pytest
from multica_dispatch_control import (
    dispatch_is_paused,
    pause_dispatch,
    pause_reason,
    resume_dispatch,
)

pytestmark = pytest.mark.ci_safe


def test_runtime_dispatch_pause_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "paused"
    monkeypatch.setenv("ZOE_MULTICA_DISPATCH_PAUSE_FILE", str(path))

    assert dispatch_is_paused() is False
    pause_dispatch("production investigation")
    assert dispatch_is_paused() is True
    assert pause_reason() == "production investigation"
    assert resume_dispatch() is True
    assert resume_dispatch() is False
    assert dispatch_is_paused() is False


def test_resume_tolerates_pause_file_disappearing(tmp_path, monkeypatch):
    path = tmp_path / "paused"
    monkeypatch.setenv("ZOE_MULTICA_DISPATCH_PAUSE_FILE", str(path))
    path.write_text("pause\n")

    original_exists = type(path).exists

    def exists_then_remove(self):
        exists = original_exists(self)
        if exists:
            self.unlink()
        return exists

    monkeypatch.setattr(type(path), "exists", exists_then_remove)
    assert resume_dispatch() is True
