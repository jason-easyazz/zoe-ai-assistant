"""scripts/landing/night_busy.sh - the pause windows, the voice-day-ok date gate and the lander deadline.

The script's contract is its EXIT CODE: 0 = this kind of work must PAUSE now, 1 = it may run. Clock injected via
ZOE_LANDING_NOW_EPOCH, systemd faked on PATH, TZ pinned UTC (GitHub runners are UTC; the Jetson is not).
"""
import pytest

from tests.unit.landing_fakes import NEEDS_TOOLS, Rig, epoch

pytestmark = [pytest.mark.ci_safe, NEEDS_TOOLS]

DAY = "2026-10-29"
PAUSE, RUN = 0, 1


@pytest.fixture()
def rig(tmp_path):
    return Rig(tmp_path)


def busy(rig, kind, hhmm, day=DAY, **extra):
    return rig.run("night_busy.sh", kind, ZOE_LANDING_NOW_EPOCH=str(epoch(day, hhmm)), **extra).returncode


def day_ok(rig, value):
    (rig.home / "voice-day-ok").write_text(value + "\n")


# ---- voice: the daytime pause and the owner's one-day lift ------------------------------------------------------

@pytest.mark.parametrize("hhmm", ["07:00", "12:00", "21:59"])
def test_voice_paused_by_day_without_ok(rig, hhmm):
    assert busy(rig, "voice", hhmm) == PAUSE


@pytest.mark.parametrize("hhmm", ["07:00", "12:00", "21:59"])
def test_voice_day_ok_for_today_lifts_the_daytime_pause(rig, hhmm):
    day_ok(rig, DAY)
    assert busy(rig, "voice", hhmm) == RUN


def test_voice_day_ok_for_another_date_does_not_lift_it(rig):
    day_ok(rig, "2026-10-28")
    assert busy(rig, "voice", "12:00") == PAUSE


def test_voice_day_ok_is_exact_date_not_prefix(rig):
    day_ok(rig, "2026-10-2")
    assert busy(rig, "voice", "12:00") == PAUSE


@pytest.mark.parametrize("hhmm", ["06:59", "22:00", "23:30", "00:30", "01:54"])
def test_voice_runs_in_the_evening_and_early_morning_slots(rig, hhmm):
    assert busy(rig, "voice", hhmm) == RUN


def test_voice_day_ok_does_not_open_the_night_window(rig):
    day_ok(rig, DAY)
    assert busy(rig, "voice", "02:30") == PAUSE


# ---- voice: the night window ------------------------------------------------------------------------------------

@pytest.mark.parametrize("hhmm", ["01:55", "02:30", "02:50", "03:29"])
def test_voice_paused_from_0155_until_the_fallback_when_window_never_ran(rig, hhmm):
    assert busy(rig, "voice", hhmm) == PAUSE


def test_voice_fallback_releases_at_0330_if_window_never_finished(rig):
    assert busy(rig, "voice", "03:30") == RUN
    assert busy(rig, "voice", "03:59") == RUN


def test_voice_released_early_when_tonights_window_finished_ok(rig):
    kw = dict(FAKE_NW_EXIT_TS=f"Thu {DAY} 02:58:00 UTC", FAKE_NW_STATUS="0")
    assert busy(rig, "voice", "03:10", **kw) == RUN


def test_voice_stays_paused_when_the_window_was_REFUSED(rig):
    # 2026-10-26: a refused window (exit 2) used to count as finished and released every lander
    kw = dict(FAKE_NW_EXIT_TS=f"Thu {DAY} 02:51:00 UTC", FAKE_NW_STATUS="2")
    assert busy(rig, "voice", "03:10", **kw) == PAUSE


def test_voice_stays_paused_when_the_last_exit_was_yesterday(rig):
    kw = dict(FAKE_NW_EXIT_TS="Wed 2026-10-28 02:58:00 UTC", FAKE_NW_STATUS="0")
    assert busy(rig, "voice", "03:10", **kw) == PAUSE


def test_voice_paused_while_the_window_is_running(rig):
    assert busy(rig, "voice", "23:00", FAKE_ACTIVE="zoe-night-window.service") == PAUSE


@pytest.mark.parametrize("hhmm", ["04:00", "04:30", "04:55"])
def test_voice_paused_while_the_nightly_regression_owns_the_artifact_slot(rig, hhmm):
    assert busy(rig, "voice", hhmm) == PAUSE


def test_voice_resumes_after_0455(rig):
    assert busy(rig, "voice", "04:56") == RUN


def test_voice_paused_while_the_regression_service_is_active(rig):
    assert busy(rig, "voice", "05:30", FAKE_ACTIVE="zoe-voice-regression.service") == PAUSE


# ---- deploy / test ----------------------------------------------------------------------------------------------

def test_deploy_windows(rig):
    assert busy(rig, "deploy", "02:44") == RUN
    assert busy(rig, "deploy", "02:45") == PAUSE
    assert busy(rig, "deploy", "03:29") == PAUSE
    assert busy(rig, "deploy", "03:30") == RUN
    assert busy(rig, "deploy", "12:00") == RUN


def test_deploy_released_when_window_finished_ok(rig):
    kw = dict(FAKE_NW_EXIT_TS=f"Thu {DAY} 02:55:00 UTC", FAKE_NW_STATUS="0")
    assert busy(rig, "deploy", "03:00", **kw) == RUN
    assert busy(rig, "deploy", "03:00", FAKE_NW_EXIT_TS=f"Thu {DAY} 02:55:00 UTC", FAKE_NW_STATUS="2") == PAUSE


def test_test_kind_pauses_for_backup_and_window_only(rig):
    assert busy(rig, "test", "12:00") == RUN
    assert busy(rig, "test", "12:00", FAKE_ACTIVE="zoe-backup.service") == PAUSE
    assert busy(rig, "test", "12:00", FAKE_ACTIVE="zoe-night-window.service") == PAUSE


# ---- deadline ---------------------------------------------------------------------------------------------------

def deadline(rig, hhmm, day=DAY):
    r = rig.run("night_busy.sh", "deadline", ZOE_LANDING_NOW_EPOCH=str(epoch(day, hhmm)))
    assert r.returncode == 0, r.stderr
    return int(r.stdout.strip())


def test_deadline_is_the_next_0240(rig):
    assert deadline(rig, "12:00") == epoch("2026-10-30", "02:40")
    assert deadline(rig, "23:59") == epoch("2026-10-30", "02:40")
    assert deadline(rig, "00:05") == epoch(DAY, "02:40")
    assert deadline(rig, "01:50") == epoch(DAY, "02:40")


def test_deadline_rolls_to_tomorrow_at_exactly_0240(rig):
    assert deadline(rig, "02:39") == epoch(DAY, "02:40")
    assert deadline(rig, "02:40") == epoch("2026-10-30", "02:40")


def test_deadline_crosses_month_end(rig):
    assert deadline(rig, "12:00", day="2026-10-31") == epoch("2026-11-01", "02:40")


def test_deadline_ignores_an_active_night_window(rig):
    # `deadline` answers before the busy checks - a lander computing its deadline must always get a number
    r = rig.run("night_busy.sh", "deadline", ZOE_LANDING_NOW_EPOCH=str(epoch(DAY, "02:55")),
                FAKE_ACTIVE="zoe-night-window.service")
    assert r.returncode == 0 and r.stdout.strip() == str(epoch("2026-10-30", "02:40"))
