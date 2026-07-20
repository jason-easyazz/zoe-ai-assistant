"""Tests for services/zoe-data/logging_setup.py.

``configure_logging`` mutates the **root logger**, which is process-global and
shared with every other suite in the run. The ``restore_root_logger`` fixture
snapshots the handler list and level and puts the *same objects* back
afterwards — identity-restore, not reconstruction — per
``docs/knowledge/test-isolation-playbook.md``, which traces five prior
cross-test leaks to exactly this bug class.

Every test writes into ``tmp_path``; nothing touches ``~/.zoe-logs``.

The load-bearing test here is ``test_info_is_dropped_without_configuration``:
it reproduces the production blackout this module exists to fix. If that test
ever passes trivially, the rest prove nothing.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from logging_setup import (  # noqa: E402
    DEFAULT_BACKUP_COUNT,
    DEFAULT_MAX_BYTES,
    HANDLER_NAME,
    configure_logging,
)

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def restore_root_logger():
    """Isolate the root logger: strip our handler on the way in, restore on the way out.

    Stripping at *setup* matters as much as restoring at teardown. Another
    module in the same pytest process may already have called
    ``configure_logging`` (the FastAPI lifespan does, and several suites spin up
    a TestClient). ``configure_logging`` is idempotent, so it would hand this
    test that pre-existing handler — pointed at a different directory — and the
    assertions would silently measure the wrong file. That is precisely the
    passes-alone-fails-in-full-run signature from the isolation playbook.

    Handlers are restored by identity, not reconstructed.
    """
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level

    root.handlers[:] = [h for h in saved_handlers if getattr(h, "name", None) != HANDLER_NAME]
    try:
        yield root
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


@pytest.fixture(autouse=True)
def clear_log_env(monkeypatch):
    for var in (
        "ZOE_LOG_LEVEL",
        "ZOE_LOG_DIR",
        "ZOE_LOG_MAX_BYTES",
        "ZOE_LOG_BACKUP_COUNT",
    ):
        monkeypatch.delenv(var, raising=False)


def _read_log(tmp_path: Path) -> str:
    log = tmp_path / "zoe-data.app.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_info_is_dropped_without_configuration(tmp_path, restore_root_logger):
    """NEGATIVE CONTROL: reproduce the production blackout.

    With no handler on root, ``logger.info`` falls through to
    ``logging.lastResort`` (WARNING-only) and is discarded. This asserts the
    broken behaviour, so the positive tests below cannot pass vacuously.
    """
    root = restore_root_logger
    root.handlers[:] = []
    root.setLevel(logging.WARNING)

    logging.getLogger("zoe.negative.control").info("this must not be recorded")

    assert _read_log(tmp_path) == ""


def test_info_reaches_the_log_file(tmp_path):
    """The fix: INFO records now land on disk."""
    handler = configure_logging(log_dir=tmp_path)
    assert handler is not None

    logging.getLogger("zoe.some.module").info("hello from a module logger")
    handler.flush()

    assert "hello from a module logger" in _read_log(tmp_path)


def test_record_is_datable_and_attributed(tmp_path):
    """WARNING+ records were surviving but undatable — lastResort has no formatter."""
    handler = configure_logging(log_dir=tmp_path)
    logging.getLogger("zoe.kanban_adapter").warning("pipeline sync failed")
    handler.flush()

    line = _read_log(tmp_path).strip().splitlines()[-1]

    assert "WARNING" in line
    assert "zoe.kanban_adapter" in line
    assert "pipeline sync failed" in line
    # A leading ISO-8601 date is what makes a line correlatable after the fact.
    assert line[:4].isdigit() and line[4] == "-", f"no ISO timestamp: {line!r}"


def test_is_idempotent(tmp_path):
    """Repeat calls must not duplicate handlers or double-write records."""
    first = configure_logging(log_dir=tmp_path)
    second = configure_logging(log_dir=tmp_path)

    assert first is second

    root = logging.getLogger()
    ours = [h for h in root.handlers if getattr(h, "name", None) == HANDLER_NAME]
    assert len(ours) == 1

    logging.getLogger("zoe.dupe").info("only once please")
    first.flush()
    assert _read_log(tmp_path).count("only once please") == 1


def test_level_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_LOG_LEVEL", "WARNING")
    handler = configure_logging(log_dir=tmp_path)

    logging.getLogger("zoe.quiet").info("suppressed at WARNING")
    logging.getLogger("zoe.quiet").warning("kept at WARNING")
    handler.flush()

    body = _read_log(tmp_path)
    assert "suppressed at WARNING" not in body
    assert "kept at WARNING" in body


def test_unrecognised_level_falls_back_to_info(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_LOG_LEVEL", "LOUDER")
    handler = configure_logging(log_dir=tmp_path)

    assert handler.level == logging.INFO


def test_rotation_is_bounded(tmp_path, monkeypatch):
    """systemd's append: never rotates; this handler must, or disk grows unbounded."""
    monkeypatch.setenv("ZOE_LOG_MAX_BYTES", "2048")
    monkeypatch.setenv("ZOE_LOG_BACKUP_COUNT", "2")
    handler = configure_logging(log_dir=tmp_path)

    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.maxBytes == 2048
    assert handler.backupCount == 2

    log = logging.getLogger("zoe.chatty")
    for i in range(400):
        log.info("padding line %03d %s", i, "x" * 80)
    handler.flush()

    produced = sorted(p.name for p in tmp_path.iterdir())
    assert "zoe-data.app.log" in produced
    # Rotation happened, and retention capped the total at backupCount + 1.
    assert len(produced) > 1, f"no rotation occurred: {produced}"
    assert len(produced) <= 3, f"retention exceeded backupCount+1: {produced}"


def test_garbage_rotation_env_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_LOG_MAX_BYTES", "not-a-number")
    monkeypatch.setenv("ZOE_LOG_BACKUP_COUNT", "-3")
    handler = configure_logging(log_dir=tmp_path)

    assert handler.maxBytes == DEFAULT_MAX_BYTES
    assert handler.backupCount == DEFAULT_BACKUP_COUNT


def test_unwritable_dir_returns_none_and_does_not_raise(tmp_path):
    """A bad log path must never take zoe-data down at import time."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("I am a file", encoding="utf-8")

    handler = configure_logging(log_dir=blocker / "nested")

    assert handler is None
    root = logging.getLogger()
    assert not [h for h in root.handlers if getattr(h, "name", None) == HANDLER_NAME]


def test_root_level_is_lowered_to_admit_records(tmp_path):
    """Root gates records before handlers see them; INFO must get through."""
    root = logging.getLogger()
    root.setLevel(logging.CRITICAL)

    configure_logging(log_dir=tmp_path)

    assert root.level <= logging.INFO


def test_env_log_dir_is_honoured(tmp_path, monkeypatch):
    target = tmp_path / "from-env"
    monkeypatch.setenv("ZOE_LOG_DIR", str(target))

    handler = configure_logging()

    assert handler is not None
    assert Path(handler.baseFilename).parent == target
