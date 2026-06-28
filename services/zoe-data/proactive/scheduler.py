"""
APScheduler wrapper (Tier 1 — precision scheduling).

Uses PostgreSQL job store (via SQLAlchemy psycopg2) so jobs survive service restarts.
Falls back to SQLite if POSTGRES_APSCHEDULER_URL is not set.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from enum import Enum
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from database import DB_PATH

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _jobstore_url() -> str:
    """Prefer PostgreSQL jobstore; fall back to SQLite for local dev."""
    pg_url = os.environ.get("POSTGRES_APSCHEDULER_URL", "")
    if pg_url:
        return pg_url
    return f"sqlite:///{DB_PATH}"


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        raise RuntimeError("Proactive scheduler not started — call start_scheduler() first")
    return _scheduler


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    jobstore_url = _jobstore_url()
    _scheduler = AsyncIOScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=jobstore_url)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        timezone="UTC",
    )
    _scheduler.start()
    log.info("Proactive APScheduler started (jobstore: %s)", jobstore_url)
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        # Force state to stopped BEFORE calling shutdown so that any
        # deferred APScheduler callback that fires after we return doesn't
        # raise SchedulerNotRunningError (it's already marked stopped).
        try:
            from apscheduler.schedulers.base import STATE_STOPPED
            _scheduler.state = STATE_STOPPED
        except Exception:
            pass
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
        _scheduler = None
        log.info("Proactive APScheduler stopped")


def register_job(
    func: Callable,
    run_at: datetime,
    job_id: str | None = None,
    kwargs: dict | None = None,
) -> str:
    """Schedule func(**kwargs) at run_at (UTC datetime). Returns job_id."""
    scheduler = get_scheduler()
    job_id = job_id or str(uuid.uuid4())
    scheduler.add_job(
        func,
        trigger="date",
        run_date=run_at,
        id=job_id,
        replace_existing=True,
        kwargs=kwargs or {},
    )
    log.debug("Registered job %s at %s", job_id, run_at.isoformat())
    return job_id


class CancelResult(Enum):
    """Outcome of cancel_job — distinguishes 'gone' from 'failed to cancel'."""
    REMOVED = "removed"   # job was present and removed
    ABSENT = "absent"     # no such job (already fired / never registered)


def cancel_job(job_id: str) -> CancelResult:
    """Cancel a scheduled job.

    Returns:
        CancelResult.REMOVED — the job was present and has been removed.
        CancelResult.ABSENT  — no such job (already fired / never registered);
                               there is no live job to orphan.

    Raises:
        Any non-JobLookupError failure from the scheduler/jobstore (e.g. a
        transient store error) is re-raised so callers never mistake a real
        cancel failure for 'job already gone' and silently orphan a live job.
    """
    scheduler = get_scheduler()
    try:
        scheduler.remove_job(job_id)
        log.debug("Cancelled job %s", job_id)
        return CancelResult.REMOVED
    except JobLookupError:
        log.debug("cancel_job: job %s already absent", job_id)
        return CancelResult.ABSENT
