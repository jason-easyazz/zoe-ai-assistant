"""Display-preferences defaults — the panel must never power the screen fully
off; it drifts to a dimmed sleep screen instead.

Regression: `off_enabled` used to default True, so after off_seconds the
panel-agent ramped the backlight to 0 (black screen). No `display_preferences`
rows are stored, so the default governs the live panel.
"""
import pytest

pytestmark = pytest.mark.ci_safe

from routers.system import _row_to_prefs, _DEFAULT_DISPLAY_PREFS


def test_default_never_powers_screen_off():
    prefs = _row_to_prefs(None)          # no stored row → code defaults
    assert prefs["off_enabled"] is False


def test_default_enables_dimmed_sleep_drift():
    prefs = _row_to_prefs(None)
    assert prefs["sleep_enabled"] is True
    assert prefs["sleep_seconds"] == 180
    # the idle DIM (not off) still applies before sleep
    assert prefs["idle_enabled"] is True
    assert 0 < prefs["idle_brightness"] < 100


def test_stored_row_can_re_enable_off():
    # off_enabled stays a togglable pref: a stored True is honoured.
    prefs = _row_to_prefs({"off_enabled": True})
    assert prefs["off_enabled"] is True
