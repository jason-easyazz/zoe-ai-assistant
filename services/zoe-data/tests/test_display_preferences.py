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


# ── every advertised pref must actually be persisted ─────────────────────────

def test_every_advertised_pref_is_persisted_by_the_put():
    """Regression + guard for the whole bug class.

    `sleep_enabled`/`sleep_seconds` were added to `_DEFAULT_DISPLAY_PREFS` and read
    by the panel at boot (home.html sizes its idle-sleep window from them), but the
    table had no columns and the PUT's INSERT enumerates columns EXPLICITLY — so
    every write silently dropped them and the operator's sleep choice could never
    be saved. It hid because a GET looked correct: `_row_to_prefs` falls back to the
    defaults, which is exactly what the caller expected to see.

    An enumerated column list can't be trusted to keep up with the defaults dict by
    hand, so assert the invariant instead of two specific columns: anything the API
    advertises must be persisted. This fails the day someone adds the next pref
    without a column.
    """
    import inspect
    import re
    from routers import system

    src = inspect.getsource(system.put_display_preferences)
    m = re.search(r"INSERT INTO display_preferences \(\s*(.*?)\)\s*VALUES", src, re.S)
    assert m, "couldn't find the display_preferences INSERT — did the writer move?"
    persisted = {c.strip() for c in m.group(1).replace("\n", " ").split(",") if c.strip()}

    advertised = set(system._DEFAULT_DISPLAY_PREFS)
    missing = advertised - persisted
    assert not missing, (
        f"advertised in _DEFAULT_DISPLAY_PREFS but never written by the PUT: "
        f"{sorted(missing)} — a PUT would silently drop them and the setting "
        f"would always read back as its default"
    )


def test_put_updates_sleep_on_conflict_not_just_insert():
    """The INSERT is an upsert: a device that already has a row takes the
    ON CONFLICT path. Listing a column in the INSERT but not the DO UPDATE set-list
    means the FIRST write persists and every later change is silently ignored —
    a nastier version of the same bug.
    """
    import inspect
    import re
    from routers import system

    src = inspect.getsource(system.put_display_preferences)
    m = re.search(r"ON CONFLICT\(device_id\) DO UPDATE SET(.*?)\"\"\"", src, re.S)
    assert m, "couldn't find the ON CONFLICT set-list"
    updated = m.group(1)
    for key in ("sleep_enabled", "sleep_seconds"):
        assert f"{key}=excluded.{key}" in updated, (
            f"{key} is inserted but not refreshed on conflict — the first write "
            f"would stick and later edits would be dropped"
        )
