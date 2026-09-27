"""setup_qr — panel QR images for the QR→phone setup flows, with no secret in a URL.

The phone setup links keep their one-time token in the ``#fragment`` so it never
reaches a server log. The panel's ``<img src=…/qr?token=…>`` undid that: nginx
logs every query string, so the same 15-minute token sat in the access log
(auth audit 2026-09-27).

Instead the panel gets an opaque, short-lived handle bound to its flow. The QR
route looks it up and renders the QR from the payload held here, in process.
The handle may be re-fetched until it expires (``QR_HANDLE_TTL_S``, 2 min) so a
failed image load or a card redraw still shows the code; after that it 404s
and the panel mints a fresh one through the flow's existing ``/start`` route.
What an access log can leak is therefore a 2-minute handle, not the
15-minute setup token.

In-process is fine for the same reason as ``music_setup``'s ledger: zoe-data is
one process and a handle lives for seconds.
"""
from __future__ import annotations

import io
import os
import secrets
import time
from typing import Any, Optional

QR_HANDLE_TTL_S = int(os.environ.get("ZOE_SETUP_QR_HANDLE_TTL_S", "120"))

_HANDLES: dict[str, tuple[dict[str, Any], float]] = {}


def _prune(now: float) -> None:
    for handle in [h for h, (_, exp) in _HANDLES.items() if exp < now]:
        _HANDLES.pop(handle, None)


def issue(kind: str, **payload: Any) -> str:
    """Hold ``payload`` for one QR render of ``kind``; return the opaque handle."""
    now = time.time()
    _prune(now)
    handle = secrets.token_urlsafe(18)
    _HANDLES[handle] = ({"kind": kind, **payload}, now + QR_HANDLE_TTL_S)
    return handle


def lookup(kind: str, handle: str) -> Optional[dict[str, Any]]:
    """The payload behind ``handle`` while it is live. None if unknown, expired,
    or issued for a different ``kind``. Re-fetchable within the TTL."""
    now = time.time()
    _prune(now)
    entry = _HANDLES.get(str(handle or ""))
    if entry is None or entry[0].get("kind") != kind:
        return None
    payload, exp = entry
    return payload if exp >= now else None


def render_svg(url: str) -> bytes:
    """The QR image (SVG) encoding ``url`` — the one style every setup QR uses."""
    import segno

    buf = io.BytesIO()
    segno.make(url, error="m").save(
        buf, kind="svg", scale=1, border=2, dark="#0b1020", light="#ffffff")
    return buf.getvalue()
