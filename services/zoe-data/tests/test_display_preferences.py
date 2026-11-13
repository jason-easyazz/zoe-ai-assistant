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


def test_every_advertised_pref_is_refreshed_on_conflict():
    """Same invariant as the INSERT test, applied to the upsert's set-list.

    The INSERT is an upsert, so a device that already has a row takes the
    ON CONFLICT path. A column listed in the INSERT but missing from the DO UPDATE
    set-list persists the FIRST write and silently ignores every later edit — a
    nastier variant of the bug this PR fixes, because it looks like it works once.

    Asserting the invariant (everything advertised must be refreshed) rather than
    naming sleep_enabled/sleep_seconds: hardcoding the two columns would be exactly
    the narrowness that let the original bug through, and would pass unchanged the
    day someone adds the next pref.
    """
    import inspect
    import re
    from routers import system

    src = inspect.getsource(system.put_display_preferences)
    m = re.search(r"ON CONFLICT\(device_id\) DO UPDATE SET(.*?)\"\"\"", src, re.S)
    assert m, "couldn't find the ON CONFLICT set-list — did the writer move?"
    refreshed = {mm.group(1) for mm in re.finditer(r"(\w+)\s*=\s*excluded\.", m.group(1))}

    advertised = set(system._DEFAULT_DISPLAY_PREFS)
    missing = advertised - refreshed
    assert not missing, (
        f"advertised in _DEFAULT_DISPLAY_PREFS but not refreshed on conflict: "
        f"{sorted(missing)} — the first write would stick and every later edit "
        f"would be silently dropped for any device that already has a row"
    )


# ── "All panels (default)" is read by a panel that has no row of its own ────────────────────────

class _Rows(dict):
    def keys(self):
        return super().keys()


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, sql, params):
        row = self.rows.get(params[0])

        class _Cur:
            async def fetchone(self_inner):
                return row
        return _Cur()


def _get(db, device_id):
    import asyncio

    from routers import system
    return asyncio.run(system.get_display_preferences(device_id=device_id, db=db))


def test_a_panel_without_its_own_row_reads_the_saved_default():
    db = _FakeDb({"default": _Rows(off_enabled=True, day_brightness=40, pi_host="10.0.0.9")})
    out = _get(db, "panel-kitchen")
    assert out["inherited"] is True and out["preferences"]["off_enabled"] is True and out["preferences"]["day_brightness"] == 40
    from routers import system
    assert out["pi_host"] == system._DEFAULT_PI_HOST          # the reload host is never inherited


def test_a_panels_own_row_wins_over_the_default_and_no_rows_means_code_defaults():
    db = _FakeDb({"default": _Rows(day_brightness=40), "panel-kitchen": _Rows(day_brightness=75)})
    own = _get(db, "panel-kitchen")
    assert own["inherited"] is False and own["preferences"]["day_brightness"] == 75
    none = _get(_FakeDb({}), "panel-kitchen")
    assert none["inherited"] is False and none["preferences"]["off_enabled"] is False     # control: the code default
    assert _get(db, "default")["inherited"] is False
