"""auth_handoff — ONE engine for "connect an app or account: QR on the panel, finish on the phone".

VISION principle 8 (B7.5): the panel shows a QR, the person finishes on their
phone, and the panel card reflects completion LIVE. Each provider flow keeps its
own phone page and its own phone token; this module owns what they used to lack
or copy: the handoff record, its status, the panel's QR, the Telegram "send to my
phone" link, and the live push back to the panel.

    start(kind, …)      → the record + an opaque single-use QR handle (setup_qr)
    status(id)          → pending | awaiting_phone | completing | done | error
    send_to_phone(id)   → the phone link to the member's linked Telegram chat
    complete(id, …)     → a provider flow reports its outcome
    mark_ref / complete_ref — the same, addressed by the provider token's nonce
                          (the phone endpoints only ever hold the token)

Rules that are load-bearing:
  * The phone URL carries the provider token in its ``#fragment`` and lives in
    PROCESS MEMORY ONLY (``_LINKS``). It is never persisted, broadcast, logged or
    returned to the panel; the panel gets the QR by handle. After a restart the
    QR / send-to-phone for an in-flight handoff honestly reports "start again".
  * ``done`` is final, and so is an expiry. Any other ``error`` (a cancelled or
    failed sign-in) can be superseded while the link is still valid, because the
    person may simply retry on the phone with the same token.
  * Status never moves backwards (a phone reload must not undo "completing").
  * Bookkeeping never breaks a provider flow: ``mark_ref``/``complete_ref``
    swallow and log every failure.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import secrets
import time
from typing import Any, Awaitable, Callable, Optional

import setup_qr

logger = logging.getLogger(__name__)

STATUSES = ("pending", "awaiting_phone", "completing", "done", "error")
_RANK = {s: i for i, s in enumerate(STATUSES)}
_KEEP_S = 24 * 3600  # finished rows are swept after a day

# kind -> (module, function) returning {"token", "ref", "path", "ttl"} for a new
# phone link. Explicit table, no import-time registration.
_KINDS: dict[str, tuple[str, str]] = {"music": ("music_setup", "handoff_link")}

_LINKS: dict[str, tuple[str, float]] = {}  # handoff id -> (phone URL, expiry); token-bearing, memory only
_BG: set[asyncio.Task] = set()

OnDone = Callable[[bool, str], Awaitable[None]]


class HandoffError(ValueError):
    """A caller error (unknown kind, no member) — the route maps it to 400."""


def _db_ctx():
    from database import get_db_ctx  # late: keeps the module importable without a pool

    return get_db_ctx()


def phone_base_url(request: Any) -> str:
    """The LAN origin the phone will open — the host the panel loaded from (same
    Wi-Fi), or ``ZOE_PUBLIC_URL``. Never a hardcoded IP."""
    override = os.environ.get("ZOE_PUBLIC_URL", "").strip()
    if override:
        return override.rstrip("/")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    if host:
        return f"{scheme}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _view(row: Any) -> dict[str, Any]:
    """The public, secret-free shape — the only thing a panel ever sees."""
    exp = float(row["expires_at"])
    return {"id": row["id"], "kind": row["kind"], "provider": row["provider"],
            "status": row["status"], "detail": row["detail"] or "", "reason": row["reason"] or "",
            "expires_at": exp, "expires_in": max(0, int(exp - time.time()))}


async def _row(db: Any, handoff_id: str) -> Any:
    cur = await db.execute("SELECT * FROM auth_handoffs WHERE id = ?", (str(handoff_id or ""),))
    return await cur.fetchone()


async def start(kind: str, *, actor_user: dict, panel_id: str, provider: str,
                base_url: str) -> dict[str, Any]:
    """Mint a handoff for ``actor_user`` on ``panel_id``. Returns the public view
    plus ``qr_handle``/``qr_path`` and (engine-internal) ``phone_url``."""
    if kind not in _KINDS:
        raise HandoffError("unknown kind")
    user_id = str((actor_user or {}).get("user_id") or "")
    if not user_id or user_id == "guest" or (actor_user or {}).get("role") in (None, "guest"):
        raise HandoffError("a signed-in member is required")
    panel_id = str(panel_id or "").strip()
    if not panel_id:
        raise HandoffError("panel_id required")
    mod, fn = _KINDS[kind]
    link = getattr(importlib.import_module(mod), fn)(provider, user_id)
    handoff_id = secrets.token_urlsafe(12)
    now = time.time()
    async with _db_ctx() as db:
        await db.execute("DELETE FROM auth_handoffs WHERE expires_at < ?", (now - _KEEP_S,))
        await db.execute(
            """INSERT INTO auth_handoffs (id, kind, provider, ref, user_id, panel_id, status,
                   detail, reason, created_at, updated_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', '', '', ?, ?, ?)""",
            (handoff_id, kind, provider, str(link["ref"]), user_id, panel_id, now, now,
             now + float(link["ttl"])))
        await db.commit()
        row = await _row(db, handoff_id)
    for stale in [k for k, (_, exp) in _LINKS.items() if exp < now]:
        _LINKS.pop(stale, None)
    phone_url = f"{base_url.rstrip('/')}{link['path']}"
    _LINKS[handoff_id] = (phone_url, now + float(link["ttl"]))
    handle = setup_qr.issue("handoff", handoff_id=handoff_id)
    return {**_view(row), "qr_handle": handle, "qr_path": f"/api/handoff/{handoff_id}/qr/{handle}",
            "phone_url": phone_url,
            "channels": ["qr"] + (["telegram"] if await telegram_available(user_id) else [])}


def _allowed(current: str, reason: str, new: str, new_reason: str) -> bool:
    if current == "done" or (current == "error" and reason == "expired"):
        return False  # final
    if current == "error":  # a retry on the phone (same valid link), or its expiry
        return new in ("completing", "done") or new_reason == "expired"
    return _RANK[new] >= _RANK[current]  # forward only (same status = detail update)


async def _transition(handoff_id: str, new: str, detail: str = "", reason: str = "") -> Optional[dict]:
    """Apply one status change; broadcast it. Returns the new view, or None when
    the record is unknown or the change is not allowed."""
    if new not in _RANK:
        raise HandoffError(f"bad status {new!r}")
    async with _db_ctx() as db:
        row = await _row(db, handoff_id)
        if row is None or not _allowed(row["status"], row["reason"] or "", new, reason):
            return None
        if new == row["status"] and detail == (row["detail"] or "") and reason == (row["reason"] or ""):
            return _view(row)  # no change, no broadcast
        # Conditional on the state just read: a concurrent transition that got
        # there first wins, and this one is refused rather than overwriting it.
        cur = await db.execute(
            """UPDATE auth_handoffs SET status = ?, detail = ?, reason = ?, updated_at = ?
               WHERE id = ? AND status = ? AND reason = ?""",
            (new, detail[:200], reason[:40], time.time(), handoff_id, row["status"], row["reason"]))
        await db.commit()
        if cur.rowcount == 0:
            return None
        row = await _row(db, handoff_id)
    view = _view(row)
    if new == "done" or reason == "expired":
        _LINKS.pop(handoff_id, None)  # the link is spent; nothing may re-send it
    await _notify_panel(row["panel_id"], view, wake=new in ("done", "error"))
    return view


async def status(handoff_id: str) -> Optional[dict[str, Any]]:
    """The current public view, expiring a stale non-final record on read."""
    async with _db_ctx() as db:
        row = await _row(db, handoff_id)
    if row is None:
        return None
    if row["status"] != "done" and (row["reason"] or "") != "expired" and float(row["expires_at"]) < time.time():
        return await _transition(handoff_id, "error", "This link expired — start again.", "expired") or _view(row)
    return _view(row)


async def owner_of(handoff_id: str) -> Optional[str]:
    async with _db_ctx() as db:
        row = await _row(db, handoff_id)
    return row["user_id"] if row is not None else None


async def complete(handoff_id: str, outcome: dict[str, Any]) -> Optional[dict[str, Any]]:
    """A provider flow reports its outcome: ``{"ok": bool, "detail": str}``."""
    ok = bool((outcome or {}).get("ok"))
    detail = str((outcome or {}).get("detail") or ("Connected" if ok else "That didn't work — please try again."))
    return await _transition(handoff_id, "done" if ok else "error", detail, "" if ok else "failed")


async def _id_for_ref(kind: str, ref: Any) -> Optional[str]:
    if not ref:
        return None
    async with _db_ctx() as db:
        cur = await db.execute("SELECT id FROM auth_handoffs WHERE kind = ? AND ref = ?", (kind, str(ref)))
        row = await cur.fetchone()
    return row["id"] if row is not None else None


async def mark_ref(kind: str, ref: Any, new: str, detail: str = "") -> None:
    """Best-effort progress by provider-token nonce (never raises)."""
    try:
        hid = await _id_for_ref(kind, ref)
        if hid:
            await _transition(hid, new, detail)
    except Exception as exc:  # noqa: BLE001 — bookkeeping must never break the flow
        logger.info("auth_handoff: mark %s/%s -> %s skipped: %s", kind, str(ref)[:6], new, exc)


async def complete_ref(kind: str, ref: Any, outcome: dict[str, Any]) -> None:
    """Best-effort completion by provider-token nonce (never raises)."""
    try:
        hid = await _id_for_ref(kind, ref)
        if hid:
            await complete(hid, outcome)
    except Exception as exc:  # noqa: BLE001
        logger.info("auth_handoff: complete %s/%s skipped: %s", kind, str(ref)[:6], exc)


def reporter(kind: str, ref: Any, name: str = "") -> OnDone:
    """An ``on_done(ok, detail)`` callback a background provider flow can call."""
    async def _done(ok: bool, detail: str) -> None:
        await complete_ref(kind, ref, {"ok": ok, "detail": (f"{name} is connected" if ok and name
                                                             else detail)})
    return _done


def qr_url(handoff_id: str, handle: str) -> Optional[str]:
    """Redeem a single-use QR handle for ``handoff_id`` → the phone URL to encode."""
    held = setup_qr.redeem("handoff", handle)
    if held is None or held.get("handoff_id") != handoff_id:
        return None
    return _link(handoff_id)


def _link(handoff_id: str) -> Optional[str]:
    held = _LINKS.get(handoff_id)
    return held[0] if held is not None and held[1] >= time.time() else None


# ── the panel: live push + best-effort wake ───────────────────────────────────

async def _notify_panel(panel_id: str, view: dict[str, Any], *, wake: bool) -> None:
    try:
        from push import broadcaster

        await broadcaster.broadcast_to_panel(panel_id, "ui_action", {
            "panel_id": panel_id,
            # A ``push_`` id: delivered by either panel push path, never acked into
            # the ui-action ledger (it is not a ledger action).
            "action": {"id": f"push_handoff_{view['id']}_{time.time_ns()}", "action_type": "handoff_update",
                       "panel_id": panel_id, "payload": view}})
    except Exception as exc:  # noqa: BLE001 — the card also polls as a fallback
        logger.warning("auth_handoff: panel broadcast failed (%s): %s", panel_id, exc)
    if wake:
        task = asyncio.create_task(_wake_panel(panel_id))
        _BG.add(task)
        task.add_done_callback(_BG.discard)


async def _wake_panel(panel_id: str) -> None:
    """Brighten a dozing panel so the result is visible (panel agent ``/wake``)."""
    try:
        import httpx
        from agent_safety import is_allowed_panel_host

        host = os.environ.get("ZOE_PI_HOST", "192.168.1.61")
        async with _db_ctx() as db:
            cur = await db.execute("SELECT pi_host FROM display_preferences WHERE device_id = ?", (panel_id,))
            row = await cur.fetchone()
        if row is not None and row["pi_host"]:
            host = row["pi_host"]
        if not is_allowed_panel_host(host):
            logger.info("auth_handoff: wake skipped — %r is not an allowed panel host", host)
            return
        port = int(os.environ.get("ZOE_PANEL_AGENT_PORT", "8765"))
        async with httpx.AsyncClient(timeout=2.0) as c:
            await c.post(f"http://{host}:{port}/wake", json={"hold_s": 20})
    except Exception as exc:  # noqa: BLE001 — best effort; the push already landed
        logger.info("auth_handoff: panel %s wake unreachable: %s", panel_id, exc)


# ── send to my phone (Telegram) ───────────────────────────────────────────────

def _bot_token() -> str:
    return os.environ.get("ZOE_TELEGRAM_BOT_TOKEN", "").strip()


async def _telegram_chat(user_id: str) -> Optional[str]:
    """The member's linked Telegram id (a private chat id), set by telegram_link."""
    from user_prefs import read_prefs

    async with _db_ctx() as db:
        prefs = await read_prefs(db, user_id)
    tid = prefs.get("telegram_id")
    return tid if isinstance(tid, str) and tid.isdigit() else None


async def telegram_available(user_id: str) -> bool:
    try:
        return bool(_bot_token()) and bool(await _telegram_chat(user_id))
    except Exception as exc:  # noqa: BLE001
        logger.info("auth_handoff: telegram availability unknown: %s", exc)
        return False


async def _telegram_send(chat_id: str, text: str, url: str) -> bool:
    """Bot API sendMessage with a link button; plain text if Telegram refuses the
    button (it rejects some LAN hostnames as button URLs)."""
    import httpx

    api = f"https://api.telegram.org/bot{_bot_token()}/sendMessage"
    body = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": [[{"text": "Open on this phone", "url": url}]]}}
    async with httpx.AsyncClient(timeout=8.0) as c:
        r = await c.post(api, json=body)
        if r.status_code == 400:
            body.pop("reply_markup")
            body["text"] = f"{text}\n{url}"
            r = await c.post(api, json=body)
    return r.status_code == 200


async def send_to_phone(handoff_id: str) -> dict[str, Any]:
    """Deliver the phone link to the owner's linked Telegram chat. Returns
    ``{"ok", "status": "sent"|"unavailable"|"failed", "detail"}``."""
    async with _db_ctx() as db:
        row = await _row(db, handoff_id)
    if row is None or row["status"] == "done" or float(row["expires_at"]) < time.time():
        return {"ok": False, "status": "unavailable", "detail": "This link has finished — start again."}
    url = _link(handoff_id)
    chat = await _telegram_chat(row["user_id"]) if _bot_token() else None
    if not url or not chat:
        return {"ok": False, "status": "unavailable",
                "detail": "Scan the code instead." if url else "Start again to get a fresh link."}
    what = str(row["provider"] or "your account")
    try:
        sent = await _telegram_send(chat, f"Tap to finish connecting {what} to Zoe (home Wi-Fi only; "
                                          f"expires in {max(1, int(float(row['expires_at']) - time.time()) // 60)} min).",
                                    url)
    except Exception as exc:  # noqa: BLE001
        logger.info("auth_handoff: telegram send failed: %s", exc)
        sent = False
    if not sent:
        return {"ok": False, "status": "failed", "detail": "Couldn't reach Telegram — scan the code instead."}
    await _transition(handoff_id, row["status"], "Sent to your phone — open it there.")
    return {"ok": True, "status": "sent", "detail": "Sent to your phone"}
