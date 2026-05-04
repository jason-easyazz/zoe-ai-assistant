"""
APScheduler wrapper (Tier 1 — precision scheduling).

Uses SQLite job store so jobs survive service restarts.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from database import DB_PATH

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        raise RuntimeError("Proactive scheduler not started — call start_scheduler() first")
    return _scheduler


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    jobstore_url = f"sqlite:///{DB_PATH}"
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


def cancel_job(job_id: str) -> bool:
    """Cancel a scheduled job. Returns True if found and removed."""
    scheduler = get_scheduler()
    try:
        scheduler.remove_job(job_id)
        log.debug("Cancelled job %s", job_id)
        return True
    except Exception:
        return False
