"""Credentials that ride in a URL query string never reach zoe-data's logs (security fix 2026-10-19).

A browser cannot set headers on a WebSocket, so the push / lists / voice sockets carry the caller's session id in the query
(``/ws/push?channel=all&session_id=...``), and uvicorn logs every handshake with its full path + query: ``"WebSocket <path>"
[accepted]`` on ``uvicorn.error``, and HTTP requests on ``uvicorn.access``. A session id is a bearer credential, so the log was a
credential store. This filter rewrites any credential-named query parameter's value to ``<redacted>`` in every record those two
loggers emit, before any handler formats it. The request itself is untouched (the client keeps working); only the log changes.
nginx's access log gets the same treatment in ``services/zoe-ui/nginx.conf`` (it logs the path without the query string).

Installed by ``logging_setup.configure_logging`` (the first thing the lifespan runs, before the first request is served).
"""
from __future__ import annotations

import logging
import re

REDACTED = "<redacted>"
#: Query parameters whose VALUE is a credential (case-insensitive). Name-anchored so ``channel=`` / ``limit=`` stay readable.
SECRET_PARAMS = (
    "session_id", "sessionid", "session", "token", "access_token", "refresh_token", "id_token", "device_token",
    "jukebox_token", "ticket", "api_key", "apikey", "key", "secret", "password", "code", "auth",
)
_SECRET_QUERY = re.compile(r"([?&](?:" + "|".join(SECRET_PARAMS) + r")=)[^&\s\"'#]*", re.IGNORECASE)
_LOGGERS = ("uvicorn.access", "uvicorn.error")


def redact_query_secrets(text: str) -> str:
    """``text`` with the value of every credential-named query parameter replaced by ``<redacted>``."""
    return _SECRET_QUERY.sub(lambda m: m.group(1) + REDACTED, text)


class QuerySecretFilter(logging.Filter):
    """Redact credential query values in a record's message and string args. Never drops a record.

    The ARGS are rewritten element by element, never collapsed into the message: uvicorn's ``AccessFormatter`` unpacks
    ``record.args`` positionally (client, method, path, version, status), so a record must keep its shape."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_query_secrets(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(redact_query_secrets(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: redact_query_secrets(v) if isinstance(v, str) else v for k, v in record.args.items()}
        except Exception:  # noqa: BLE001 - logging must never fail because of this filter
            pass
        return True


_FILTER = QuerySecretFilter()


def install() -> None:
    """Attach the filter to uvicorn's access + error loggers. Idempotent."""
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
