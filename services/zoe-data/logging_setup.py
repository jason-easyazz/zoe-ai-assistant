"""Application logging for zoe-data.

Without this module the service has no application logging at all. The reason
is subtle enough to be worth recording:

* ``main:app`` is started by ``uvicorn`` with no ``--log-config``, and nothing
  in the codebase calls ``logging.basicConfig`` / ``dictConfig``. uvicorn
  configures only its **own** loggers (``uvicorn``, ``uvicorn.access``,
  ``uvicorn.error``), so those emit normally.
* Every application module uses ``logger = logging.getLogger(__name__)``. Those
  records propagate to the **root** logger, which has no handler — so they fall
  through to :data:`logging.lastResort`.
* ``logging.lastResort`` is a bare stderr handler fixed at ``WARNING`` with **no
  formatter**. Consequences: every ``logger.info()`` in the service is silently
  discarded, and the WARNING/ERROR records that do survive carry no timestamp,
  level, or logger name — which makes them undatable after the fact.

That blackout is why runtime questions about this service have had to be
answered with database forensics rather than by reading a log.

Handlers are attached to the **root** logger, so this fixes every module at
once without touching call sites. uvicorn's own loggers are deliberately left
alone: their records already reach the systemd ``append:`` files, and adding a
root handler would duplicate every access line into the app log.

Rotation is not optional here. systemd's ``StandardOutput=append:`` never
rotates, and the existing log directory had grown to ~146 MB unbounded. This
module writes to its own :class:`~logging.handlers.RotatingFileHandler` with a
hard ceiling of ``maxBytes * (backupCount + 1)``.

Deliberately dependency-free (stdlib only, no ``typed_env``) so it can be
called as the very first statement in ``main.py`` with no import-order risk.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

__all__ = ["configure_logging", "DEFAULT_LOG_DIR", "HANDLER_NAME"]

DEFAULT_LOG_DIR = "~/.zoe-logs"
DEFAULT_LEVEL = "INFO"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB
DEFAULT_BACKUP_COUNT = 5  # ⇒ 60 MiB ceiling for the app log

#: Identifies our handler so repeat calls are a no-op rather than a duplicate.
HANDLER_NAME = "zoe-data-app-log"

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def _int_from_env(name: str, default: int) -> int:
    """Read a positive int from the environment, falling back on any garbage."""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _resolve_level(raw: str | None) -> int:
    """Map a level name (or number) to a logging level, defaulting to INFO."""
    if not raw:
        raw = DEFAULT_LEVEL
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    resolved = logging.getLevelName(raw.upper())
    # getLevelName returns the string "Level X" for anything unrecognised.
    return resolved if isinstance(resolved, int) else logging.INFO


def configure_logging(*, log_dir: str | os.PathLike[str] | None = None) -> logging.Handler | None:
    """Attach a rotating file handler to the root logger. Idempotent.

    Returns the handler, or ``None`` if logging could not be configured (an
    unwritable log directory must never take the service down — a Zoe that
    answers without logs beats a Zoe that refuses to boot).

    Environment:
        ``ZOE_LOG_LEVEL``       level name or number (default ``INFO``)
        ``ZOE_LOG_DIR``         directory for the app log (default ``~/.zoe-logs``)
        ``ZOE_LOG_MAX_BYTES``   per-file size before rotation
        ``ZOE_LOG_BACKUP_COUNT`` rotated files to retain
    """
    root = logging.getLogger()

    existing = next((h for h in root.handlers if getattr(h, "name", None) == HANDLER_NAME), None)
    if existing is not None:
        return existing

    level = _resolve_level(os.environ.get("ZOE_LOG_LEVEL"))

    # The root logger gates every record before handlers see it, so it has to
    # be at least as permissive as the handler or nothing arrives.
    root.setLevel(min(root.level, level) if root.level else level)

    directory = Path(log_dir or os.environ.get("ZOE_LOG_DIR") or DEFAULT_LOG_DIR).expanduser()

    try:
        directory.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            directory / "zoe-data.app.log",
            maxBytes=_int_from_env("ZOE_LOG_MAX_BYTES", DEFAULT_MAX_BYTES),
            backupCount=_int_from_env("ZOE_LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT),
            encoding="utf-8",
        )
    except OSError as exc:  # unwritable dir, full disk, bad mount
        logging.lastResort.handle(
            logging.LogRecord(
                name=__name__,
                level=logging.ERROR,
                pathname=__file__,
                lineno=0,
                msg="zoe-data app logging disabled: could not open log dir %s (%s)",
                args=(directory, exc),
                exc_info=None,
            )
        )
        return None

    handler.name = HANDLER_NAME
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(handler)

    logging.getLogger(__name__).info(
        "app logging configured: level=%s file=%s",
        logging.getLevelName(level),
        handler.baseFilename,  # type: ignore[attr-defined]
    )
    return handler
