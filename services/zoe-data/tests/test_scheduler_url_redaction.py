"""The APScheduler jobstore DSN must never reach the app log with its password.

2026-09-25 audit §2.7: `proactive/scheduler.py` logged the full
`postgresql+psycopg2://user:PASSWORD@host/db` on every start, so the Postgres
password sat in plaintext in `zoe-data.app.log` for every restart ever.

Negative control: with `redact_url` bypassed (the pre-fix log line), the
secret IS in caplog — proving the assertion below can fail.

The fixture secret is generated at test time (`secrets.token_hex`) and the DSN
is assembled from parts, so no credential-looking literal ever exists in git —
ggshield scans branch HISTORY and flagged the previous hard-coded value
("Generic Password"), which cost this PR a clean re-branch.
"""
from __future__ import annotations

import logging
import secrets

import pytest

import proactive.scheduler as scheduler

pytestmark = pytest.mark.ci_safe

_PW = secrets.token_hex(8)  # random per run; only the redaction matters
_DSN = "postgresql+psycopg2://" + "zoe:" + _PW + "@127.0.0.1:5432/zoe"


class _FakeJobStore:
    def __init__(self, url: str):
        self.url = url


class _FakeScheduler:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False

    def start(self):
        self.started = True


@pytest.fixture
def _isolated_scheduler(monkeypatch):
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "SQLAlchemyJobStore", _FakeJobStore)
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", _FakeScheduler)
    monkeypatch.setenv("POSTGRES_APSCHEDULER_URL", _DSN)
    yield
    scheduler._scheduler = None


def test_redact_url_hides_password_and_keeps_everything_else():
    out = scheduler.redact_url(_DSN)
    assert _PW not in out
    assert out == "postgresql+psycopg2://zoe:***@127.0.0.1:5432/zoe"


def test_redact_url_regex_fallback_when_sqlalchemy_cannot_parse(monkeypatch):
    """A URL SQLAlchemy rejects still never leaks its secret."""
    import sqlalchemy.engine.url as url_mod

    def _boom(_url):
        raise ValueError("unparseable")

    monkeypatch.setattr(url_mod, "make_url", _boom)
    out = scheduler.redact_url("weird://" + "zoe:" + _PW + "@host/db")
    assert _PW not in out
    assert out == "weird://zoe:***@host/db"


def test_redact_url_leaves_sqlite_path_alone():
    assert scheduler.redact_url("sqlite:////home/zoe/zoe.db") == "sqlite:////home/zoe/zoe.db"


def test_start_scheduler_never_logs_the_password(_isolated_scheduler, caplog):
    caplog.set_level(logging.DEBUG, logger=scheduler.__name__)

    scheduler.start_scheduler()

    assert "Proactive APScheduler started" in caplog.text
    assert _PW not in caplog.text
    assert "zoe:***@127.0.0.1" in caplog.text
    # The jobstore itself still receives the REAL url — redaction is log-only.
    assert scheduler._scheduler.kwargs["jobstores"]["default"].url == _DSN


def test_negative_control_unredacted_log_line_does_leak(_isolated_scheduler, caplog, monkeypatch):
    """Guard the guard: bypass the redactor and the same assertion goes red."""
    caplog.set_level(logging.DEBUG, logger=scheduler.__name__)
    monkeypatch.setattr(scheduler, "redact_url", lambda url: url)

    scheduler.start_scheduler()

    assert _PW in caplog.text
