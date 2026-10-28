"""Guards the pytz hold in requirements-py312.txt (pytz==2026.3).

APScheduler 3.11.3 reads ``obj._offset`` for a pytz tzinfo with no zone name
(``pytz.FixedOffset``); pytz 2026.4/2026.5 renamed that field, so restoring a
pickled fixed-offset trigger raises AttributeError and the jobstore silently
drops the job. This test round-trips such a trigger through pickle exactly the
way SQLAlchemyJobStore persists it, so lifting the hold without an APScheduler
release carrying the fix fails here instead of in the live jobstore.

It only means something on the 3.12 dependency set (APScheduler >= 3.11); the
3.10.4 lane CI installs never converts pytz zones, so it skips there.
"""
from __future__ import annotations

import pickle

import pytest

pytestmark = pytest.mark.ci_safe

pytz = pytest.importorskip("pytz")
apscheduler = pytest.importorskip("apscheduler")


def _aps_version() -> tuple[int, ...]:
    return tuple(int(p) for p in apscheduler.__version__.split(".")[:2] if p.isdigit())


_needs_311 = pytest.mark.skipif(
    _aps_version() < (3, 11), reason="pytz->ZoneInfo conversion only exists in APScheduler 3.11+"
)


@_needs_311
def test_pickled_fixed_offset_interval_trigger_restores():
    from apscheduler.triggers.interval import IntervalTrigger

    trigger = IntervalTrigger(minutes=5, timezone=pytz.FixedOffset(480))
    restored = pickle.loads(pickle.dumps(trigger))
    assert restored.interval == trigger.interval
    assert restored.timezone.utcoffset(None).total_seconds() == 480 * 60


@_needs_311
def test_fixed_offset_cron_trigger_accepts_pytz_zone():
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger(hour=7, timezone=pytz.FixedOffset(480))
    restored = pickle.loads(pickle.dumps(trigger))
    assert restored.timezone.utcoffset(None).total_seconds() == 480 * 60
