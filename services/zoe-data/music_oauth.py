"""music_oauth — drive Music Assistant's OAuth (Spotify etc.) from Zoe.

MA's streaming OAuth (`auth` action) is coupled to a live WebSocket session: MA
emits the provider's authorize URL over the socket, then blocks up to ~60s
waiting for its hosted callback (music-assistant.io/callback) to relay the code
back. Proven live: Zoe opens the MA WS, authenticates, invokes the auth action,
and captures the Spotify authorize URL — no MA frontend, no dev app.

Flow per attempt:
  1. start_oauth(provider) opens a MA WS, invokes the auth action, and returns
     the authorize URL the moment MA emits it (the phone opens it).
  2. A background task keeps the socket open until the auth action's response
     arrives (callback completed) — it carries the config values with the token
     filled — then saves the provider into MA. State: pending → connected/failed.
  3. oauth_status(oauth_id) is polled by the phone page to show success.

Everything is best-effort and time-bounded; a stalled/abandoned attempt just
expires. No secret ever leaves the LAN — Zoe only relays the URL and the token
goes straight into MA.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from typing import Any, Optional

import music_service

logger = logging.getLogger(__name__)

# MA blocks ~60s on the callback; give the whole attempt a little more headroom.
OAUTH_ATTEMPT_TTL_S = int(os.environ.get("ZOE_MUSIC_OAUTH_TTL_S", "150"))
# How long start_oauth waits for MA to emit the authorize URL before returning.
_URL_WAIT_S = 12.0
_HIDDEN = {"label", "divider", "action"}

# oauth_id -> {state, auth_url, provider, error, created, event, task}
_flows: dict[str, dict[str, Any]] = {}


def _ma_ws_url() -> str:
    base = os.environ.get("MUSIC_ASSISTANT_URL", "http://localhost:8095").rstrip("/")
    return base.replace("http://", "ws://").replace("https://", "wss://") + "/ws"


def _prune() -> None:
    now = time.time()
    for oid in [k for k, f in _flows.items() if now - f.get("created", 0) > OAUTH_ATTEMPT_TTL_S + 60]:
        f = _flows.pop(oid, None)
        task = (f or {}).get("task")
        if task and not task.done():
            task.cancel()


def _values_from_entries(result: Any) -> dict[str, Any]:
    """Pull the filled config values (incl. the freshly-minted token) out of the
    auth action's returned config entries."""
    values: dict[str, Any] = {}
    for e in result if isinstance(result, list) else []:
        if not isinstance(e, dict):
            continue
        key, etype, val = e.get("key"), e.get("type"), e.get("value")
        if key and etype not in _HIDDEN and val is not None:
            values[key] = val
    return values


async def _run_flow(oauth_id: str, provider: str) -> None:
    flow = _flows[oauth_id]
    import websockets  # local import: only needed when an OAuth attempt runs
    token = os.environ.get("MUSIC_ASSISTANT_TOKEN", "")
    session_id = "zoe-" + secrets.token_hex(6)
    msg_id = "zoe-auth-" + secrets.token_hex(4)
    try:
        async with websockets.connect(_ma_ws_url(), open_timeout=8, max_size=2 ** 22) as ws:
            await ws.recv()  # server info
            if token:
                await ws.send(json.dumps({"command": "auth", "message_id": "auth", "args": {"token": token}}))
                ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=6))
                if ack.get("error_code"):
                    flow.update(state="failed", error="music engine auth failed")
                    flow["event"].set()
                    return
            await ws.send(json.dumps({
                "command": "config/providers/get_entries", "message_id": msg_id,
                "args": {"provider_domain": provider, "action": "auth", "values": {"session_id": session_id}},
            }))
            deadline = time.time() + OAUTH_ATTEMPT_TTL_S
            values: Optional[dict[str, Any]] = None
            while time.time() < deadline:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                except asyncio.TimeoutError:
                    continue
                data = m.get("data")
                # AUTH_SESSION event carries the provider's authorize URL.
                if isinstance(data, str) and data.startswith("http") and flow.get("auth_url") is None:
                    flow["auth_url"] = data
                    flow["event"].set()  # release start_oauth to return the URL
                    continue
                if m.get("message_id") == msg_id:
                    if m.get("error_code"):
                        flow.update(state="failed", error="sign-in didn't complete")
                        flow["event"].set()
                        return
                    values = _values_from_entries(m.get("result"))
                    break
            if values is None:
                flow.update(state="failed", error="sign-in timed out")
                flow["event"].set()
                return
        # Persist the connected provider (outside the WS ctx — save uses HTTP).
        saved = await music_service.save_provider(provider, values)
        flow["state"] = "connected" if saved else "failed"
        if not saved:
            flow["error"] = "couldn't save the connection"
    except Exception as exc:  # noqa: BLE001 — a failed attempt must never crash the app
        logger.info("music oauth flow failed (%s): %s", provider, exc)
        flow.update(state="failed", error="couldn't reach the music engine")
    finally:
        flow["event"].set()


async def start_oauth(provider: str) -> dict[str, Any]:
    """Begin an OAuth attempt; returns {oauth_id, auth_url, state}. auth_url is
    None if MA didn't emit it in time (state='failed')."""
    _prune()
    oauth_id = secrets.token_urlsafe(12)
    flow: dict[str, Any] = {"state": "pending", "auth_url": None, "provider": provider,
                            "error": None, "created": time.time(), "event": asyncio.Event()}
    _flows[oauth_id] = flow
    flow["task"] = asyncio.create_task(_run_flow(oauth_id, provider))
    try:
        await asyncio.wait_for(flow["event"].wait(), timeout=_URL_WAIT_S)
    except asyncio.TimeoutError:
        pass
    flow["event"].clear()  # reset so status polling can await completion later if needed
    return {"oauth_id": oauth_id, "auth_url": flow.get("auth_url"),
            "state": flow["state"] if flow.get("auth_url") else "failed",
            "error": flow.get("error")}


def oauth_status(oauth_id: str) -> dict[str, Any]:
    _prune()
    flow = _flows.get(oauth_id)
    if flow is None:
        return {"state": "unknown"}
    return {"state": flow["state"], "provider": flow.get("provider"), "error": flow.get("error")}
