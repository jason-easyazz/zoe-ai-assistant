"""This lane runs under the home-write guard (tools/ci/home_write_guard.py; the mechanism is tested in
tests/unit/test_home_write_guard.py). A lane that stopped installing it would silently stop guarding."""
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe


def test_the_zoe_data_lane_installed_the_guard_and_its_fixture_is_active(request):
    guard = sys.modules.get("zoe_home_write_guard")
    assert guard is not None and guard._state().enabled
    assert os.path.realpath(Path(__file__).resolve().parents[3]) in guard._state().checkouts
    assert "_no_writes_under_the_real_home_or_the_checkout" in request.fixturenames


def test_the_music_prefs_file_is_per_test(tmp_path):
    import music_service
    assert str(music_service._PREFS_PATH).startswith(str(tmp_path))
    music_service.set_preferred_player_id("speaker-1")
    assert music_service.get_preferred_player_id() == "speaker-1" and guard_is_quiet()


def guard_is_quiet() -> bool:
    return sys.modules["zoe_home_write_guard"].take() == []
