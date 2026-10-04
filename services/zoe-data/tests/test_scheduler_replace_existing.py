"""replace_existing=True must not provoke a failed INSERT (PostgreSQL logs it as an ERROR).

2026-10-04 container log review: zoe-database logged ``duplicate key value violates unique
constraint "apscheduler_jobs_pkey"`` three times per zoe-data start (7 starts in one evening
= 21 ERRORs + pickled STATEMENTs), because APScheduler implements replace_existing as
INSERT -> ConflictingIdError -> UPDATE. The instrument here is the jobstore itself: it counts
the ConflictingIdErrors its own add_job raises, i.e. exactly the statements PostgreSQL logs.
A control proves the instrument sees the stock behaviour, so a green result is not silence.
"""
import asyncio
import time

import pytest
from apscheduler.jobstores.base import ConflictingIdError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import proactive.scheduler as scheduler

pytestmark = pytest.mark.ci_safe


class _CountingStore(SQLAlchemyJobStore):
    """Records every ConflictingIdError, i.e. every failed INSERT the server would log."""

    conflicts = 0

    def add_job(self, job):
        try:
            return super().add_job(job)
        except ConflictingIdError:
            type(self).conflicts += 1
            raise


def _run(factory, tmp_path):
    """Register the same standing job twice (two 'starts'); return (conflicts, jobs)."""
    _CountingStore.conflicts = 0
    url = f"sqlite:///{tmp_path / 'jobs.sqlite'}"

    async def main():
        sched = factory(jobstores={"default": _CountingStore(url=url)}, timezone="UTC")
        sched.start()
        try:
            for hours in (1, 2):  # second registration also changes the trigger
                sched.add_job(
                    time.time, trigger="interval", hours=hours, id="standing_job",
                    replace_existing=True, coalesce=True, max_instances=1,
                )
            return sched.get_jobs()
        finally:
            sched.shutdown(wait=False)

    jobs = asyncio.run(main())
    return _CountingStore.conflicts, jobs


def test_stock_apscheduler_does_conflict_on_replace_existing(tmp_path):
    # Control: the instrument sees the failed INSERT that PostgreSQL logs as an ERROR.
    conflicts, jobs = _run(AsyncIOScheduler, tmp_path)
    assert conflicts == 1
    assert len(jobs) == 1


def test_replace_existing_is_idempotent_without_a_failed_insert(tmp_path):
    conflicts, jobs = _run(scheduler._build_scheduler, tmp_path)
    assert conflicts == 0, "replace_existing still provokes a duplicate-key INSERT"
    assert len(jobs) == 1 and jobs[0].id == "standing_job"
    # replaced, not kept: the second registration's trigger won
    assert jobs[0].trigger.interval.total_seconds() == 2 * 3600


def test_without_replace_existing_a_duplicate_id_is_still_rejected(tmp_path):
    async def main():
        sched = scheduler._build_scheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{tmp_path / 'j.sqlite'}")},
            timezone="UTC",
        )
        sched.start()
        try:
            sched.add_job(time.time, trigger="interval", hours=1, id="dup")
            with pytest.raises(ConflictingIdError):
                sched.add_job(time.time, trigger="interval", hours=1, id="dup")
        finally:
            sched.shutdown(wait=False)

    asyncio.run(main())


def test_a_job_that_does_not_exist_yet_is_simply_added(tmp_path):
    async def main():
        sched = scheduler._build_scheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{tmp_path / 'n.sqlite'}")},
            timezone="UTC",
        )
        sched.start()
        try:
            sched.add_job(time.time, trigger="interval", hours=1, id="fresh", replace_existing=True)
            return [j.id for j in sched.get_jobs()]
        finally:
            sched.shutdown(wait=False)

    assert asyncio.run(main()) == ["fresh"]


def _survivor_after_failed_replace(factory, tmp_path):
    """Register a standing job, then try to replace it with one that cannot be stored."""
    url = f"sqlite:///{tmp_path / 'surv.sqlite'}"

    async def main():
        sched = factory(jobstores={"default": SQLAlchemyJobStore(url=url)}, timezone="UTC")
        sched.start()
        try:
            sched.add_job(time.time, trigger="interval", hours=1, id="standing_job",
                          replace_existing=True)
            failed = False
            try:
                # a lambda in kwargs cannot be pickled -> the jobstore rejects the write,
                # the same shape as a transient Postgres failure between DELETE and INSERT
                sched.add_job(time.time, trigger="interval", hours=2, id="standing_job",
                              replace_existing=True, kwargs={"cb": lambda: 1})
            except Exception:
                failed = True
            return failed, sched.get_jobs()
        finally:
            sched.shutdown(wait=False)

    return asyncio.run(main())


def test_stock_apscheduler_keeps_the_old_job_when_the_replacement_cannot_be_stored(tmp_path):
    # Control: this is the behaviour the fix must not regress below.
    failed, jobs = _survivor_after_failed_replace(AsyncIOScheduler, tmp_path)
    assert failed and len(jobs) == 1
    assert jobs[0].trigger.interval.total_seconds() == 3600


def test_a_failed_replace_leaves_the_standing_job_in_place(tmp_path):
    # Review of #1850: remove-then-add dropped the job on a failed add (reproduced on a real
    # SQLAlchemyJobStore). The replacement now goes through the store's single UPDATE.
    failed, jobs = _survivor_after_failed_replace(scheduler._build_scheduler, tmp_path)
    assert failed, "the unpicklable replacement should still raise"
    assert len(jobs) == 1 and jobs[0].id == "standing_job", "the standing job was dropped"
    assert jobs[0].trigger.interval.total_seconds() == 3600, "old definition must be untouched"


def test_replace_before_start_registers_once_without_a_conflict(tmp_path):
    # Jobs added while the scheduler is stopped sit in the pending list and are written at
    # start() through the same hook; a re-registered standing job must still replace cleanly.
    _CountingStore.conflicts = 0
    url = f"sqlite:///{tmp_path / 'pend.sqlite'}"

    async def main():
        s1 = scheduler._build_scheduler(jobstores={"default": _CountingStore(url=url)}, timezone="UTC")
        s1.add_job(time.time, trigger="interval", hours=1, id="p", replace_existing=True)
        s1.start()
        s1.shutdown(wait=False)
        s2 = scheduler._build_scheduler(jobstores={"default": _CountingStore(url=url)}, timezone="UTC")
        s2.add_job(time.time, trigger="interval", hours=3, id="p", replace_existing=True)
        s2.start()
        try:
            return s2.get_jobs()
        finally:
            s2.shutdown(wait=False)

    jobs = asyncio.run(main())
    assert _CountingStore.conflicts == 0
    assert len(jobs) == 1 and jobs[0].trigger.interval.total_seconds() == 3 * 3600
