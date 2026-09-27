"""setup_qr — panel QR images for the QR→phone setup flows, with no secret in a URL.

The phone setup links keep their one-time token in the ``#fragment`` so it never
reaches a server log. The panel's ``<img src=…/qr?token=…>`` undid that: nginx
logs every query string, so the same 15-minute token sat in the access log
(auth audit 2026-09-27).

Instead the panel gets an opaque, SINGLE-USE, short-lived handle bound to its
flow. The QR route redeems it on the first successful image fetch and renders
the QR from the payload held here, in process. A handle copied out of an access
log is already spent (the panel's own fetch redeemed it before the log line was
written) and expires in ``QR_HANDLE_TTL_S`` regardless. A redraw or failed load
does not need a reusable URL: the panel card renews by asking the flow's
existing ``/start`` route for a fresh handle (touch/home.html, once per modal).

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


def redeem(kind: str, handle: str) -> Optional[dict[str, Any]]:
    """Spend ``handle`` (single use). Returns its payload, or None if unknown,
    expired, already spent, or issued for a different ``kind`` (a wrong-kind
    probe does not spend it)."""
    now = time.time()
    _prune(now)
    entry = _HANDLES.get(str(handle or ""))
    if entry is None or entry[0].get("kind") != kind:
        return None
    _HANDLES.pop(handle, None)
    payload, exp = entry
    return payload if exp >= now else None


def render_svg(url: str) -> bytes:
    """The QR image (SVG) encoding ``url`` — the one style every setup QR uses."""
    import segno

    buf = io.BytesIO()
    segno.make(url, error="m").save(
        buf, kind="svg", scale=1, border=2, dark="#0b1020", light="#ffffff")
    return buf.getvalue()
