"""
APScheduler wrapper (Tier 1 — precision scheduling).

Uses PostgreSQL job store (via SQLAlchemy psycopg2) so jobs survive service restarts.
Falls back to SQLite if POSTGRES_APSCHEDULER_URL is not set.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import datetime
from enum import Enum
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import ConflictingIdError, JobLookupError
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


_URL_USERINFO_RE = re.compile(r"://([^/@:]+):[^@]*@")


def redact_url(url: str) -> str:
    """Return `url` with any password replaced by `***`, for logging ONLY.

    The jobstore URL is a full DSN (`postgresql+psycopg2://user:PASSWORD@host/db`),
    and the 2026-09-25 audit found it written verbatim to the app log on every
    start. SQLAlchemy's own renderer is the authority on its URL grammar; the
    regex is the fallback so a URL SQLAlchemy cannot parse is still never logged
    with its secret intact.
    """
    try:
        from sqlalchemy.engine.url import make_url
        return make_url(url).render_as_string(hide_password=True)
    except Exception:
        return _URL_USERINFO_RE.sub(r"://\1:***@", url)


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        raise RuntimeError("Proactive scheduler not started — call start_scheduler() first")
    return _scheduler


class _ReplaceExistingMixin:
    """Make ``add_job(..., replace_existing=True)`` idempotent WITHOUT a failed INSERT.

    APScheduler implements replace_existing as "try the INSERT, catch ConflictingIdError,
    then UPDATE". Against PostgreSQL the failed INSERT is logged by the SERVER as
    ``ERROR: duplicate key value violates unique constraint "apscheduler_jobs_pkey"`` (plus
    the whole pickled STATEMENT) on every zoe-data start for every standing job -- 21 such
    ERRORs in one evening of restarts (2026-10-04 container log review), which buries a
    real database error.

    The fix looks the job up FIRST and, when it exists, goes straight to the stock UPDATE
    path (``store.update_job``) by making this one INSERT raise ``ConflictingIdError``
    without touching the database. It is deliberately NOT remove-then-add: that is
    destructive when the add fails (an unpicklable argument, a transient Postgres error
    between the DELETE and the INSERT) and would silently drop a standing job until the
    next restart, whereas stock APScheduler -- and this -- leave the old row untouched
    because a single UPDATE either replaces it or does not.

    Hooked at ``_real_add_job`` rather than ``add_job`` so jobs registered before
    ``start()`` (the pending list) take the same path. Runs under APScheduler's own
    ``_jobstores_lock``.
    """

    def _real_add_job(self, job, jobstore_alias, replace_existing):
        store = None
        if replace_existing:
            try:
                store = self._lookup_jobstore(jobstore_alias)
                exists = store.lookup_job(job.id) is not None
            except Exception:  # unreadable store: let APScheduler's own path decide
                exists = False
            if not exists:
                store = None
        if store is None:
            return super()._real_add_job(job, jobstore_alias, replace_existing)

        def _conflict(conflicting_job):
            raise ConflictingIdError(conflicting_job.id)

        store.add_job = _conflict  # instance attribute shadows the method for this one call
        try:
            return super()._real_add_job(job, jobstore_alias, replace_existing)
        finally:
            store.__dict__.pop("add_job", None)


def _build_scheduler(**kwargs):
    """The configured scheduler class, looked up at call time, plus the replace mixin."""
    return type("ReplaceExistingScheduler", (_ReplaceExistingMixin, AsyncIOScheduler), {})(**kwargs)


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    jobstore_url = _jobstore_url()
    _scheduler = _build_scheduler(
        jobstores={"default": SQLAlchemyJobStore(url=jobstore_url)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        timezone="UTC",
    )
    _scheduler.start()
    log.info("Proactive APScheduler started (jobstore: %s)", redact_url(jobstore_url))
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


def job_exists(job_id: str) -> bool:
    """True if a live job with this id is present in the scheduler/jobstore.

    Used by startup reconciliation to distinguish a reminder that still has a
    scheduled job (leave it alone) from one whose job was dropped (re-register).
    """
    scheduler = get_scheduler()
    try:
        return scheduler.get_job(job_id) is not None
    except Exception:
        return False


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


# --------------------------------------------------------------------------- #
# run_blocking() — SQLAlchemyJobStore (psycopg2/sqlite) is a SYNCHRONOUS
# jobstore, so add_job/remove_job/get_job (register_job/cancel_job/job_exists
# above) perform blocking DB I/O. Calling them directly from a coroutine that
# runs on the event loop (request handlers, reconcile) stalls every concurrent
# request/WebSocket for the duration of that I/O. Callers in async code paths
# should offload via this helper: `await run_blocking(register_job, **kw)`.
# A full async-jobstore migration is out of scope; this is the smallest
# correct mitigation. Kept as a thin helper (not a same-named async twin of
# each function) so call sites still invoke the exact module-level names
# (`register_job`, `cancel_job`, `job_exists`) that existing unit tests
# monkeypatch.
# --------------------------------------------------------------------------- #
async def run_blocking(_fn: Callable, *args, **kwargs):
    """Run a blocking callable in a thread executor.

    Takes the target callable positionally (as `_fn`, not `func`) so it never
    collides with a `func=` keyword the callable itself expects (e.g.
    `register_job(func=..., ...)`).
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(None, lambda: _fn(*args, **kwargs))
    return await loop.run_in_executor(None, _fn, *args)
