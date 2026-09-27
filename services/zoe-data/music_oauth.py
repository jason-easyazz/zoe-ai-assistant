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

MA 2.10+ (music_service.ma_server_version): the `auth` action above is gone.
OAuth is a SETUP FLOW — `config/providers/setup` (first connect) or
`config/providers/reconfigure` (re-auth, in place) — whose EXTERNAL step carries
the authorize URL; MA's own callback advances the flow. Completion is read from
MA's `setup_flow_updated` WS events for THIS flow_id (the FINISH/ABORT step is
pushed there). Polling `config/flows/get` cannot see it: MA drops a flow from its
registry the moment it ends, so a poll after the callback just errors, and
provider state (new instance, cleared last_error) is shared and ambiguous. No
terminal event (socket lost, timeout) = failure + best-effort abort, never a
guess. Form steps are answered with their own prefilled/default values (bounded).
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


async def _ws_auth(ws: Any) -> bool:
    """Read the server-info greeting and authenticate (events are only sent to
    an authenticated client). True on success."""
    await ws.recv()  # server info
    token = os.environ.get("MUSIC_ASSISTANT_TOKEN", "")
    if not token:
        return True
    await ws.send(json.dumps({"command": "auth", "message_id": "auth", "args": {"token": token}}))
    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=6))
    return not ack.get("error_code")


async def _run_setup_flow(oauth_id: str, provider: str) -> None:
    """The MA 2.10+ OAuth path (see the module docstring)."""
    flow = _flows[oauth_id]
    flow_id: Optional[str] = None
    import websockets  # local import: only needed when an OAuth attempt runs
    try:
        # Unreadable provider config is NOT "no instance": starting a first-connect
        # setup for an already-configured provider would mint a duplicate.
        cfgs = await music_service._ma("config/providers")
        if not isinstance(cfgs, list):
            flow.update(state="failed", error="couldn't reach the music engine")
            return
        existing = next((c.get("instance_id") or c.get("id") for c in cfgs
                         if isinstance(c, dict) and c.get("domain") == provider), None)
        async with websockets.connect(_ma_ws_url(), open_timeout=8, max_size=2 ** 22) as ws:
            if not await _ws_auth(ws):
                flow.update(state="failed", error="music engine auth failed")
                return
            start_id = "zoe-flow-" + secrets.token_hex(4)
            command, args = (("config/providers/reconfigure", {"instance_id": existing}) if existing
                             else ("config/providers/setup", {"provider_domain": provider}))
            await ws.send(json.dumps({"command": command, "message_id": start_id, "args": args}))
            pending_ids = {start_id}
            early: dict[str, dict[str, Any]] = {}  # flow events seen before we knew our flow_id
            forms = 0
            # MA publishes each step as an event AND returns it as the command's
            # reply, in either order: handle every distinct step exactly once, or
            # a form would be submitted twice.
            seen_steps: set[str] = set()
            deadline = time.time() + OAUTH_ATTEMPT_TTL_S
            while time.time() < deadline:
                try:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                except asyncio.TimeoutError:
                    continue
                step: Optional[dict[str, Any]] = None
                if m.get("message_id") in pending_ids:  # a command's own reply
                    pending_ids.discard(m.get("message_id"))
                    if m.get("error_code"):
                        logger.info("music oauth flow command failed (%s): %s", provider, m.get("details"))
                        break
                    step = m.get("result") if isinstance(m.get("result"), dict) else None
                    if step and flow_id is None:
                        flow_id = step.get("flow_id")
                        step = early.pop(str(flow_id), None) or step
                elif m.get("event") == "setup_flow_updated" and isinstance(m.get("data"), dict):
                    if flow_id is None:
                        early[str(m.get("object_id"))] = m["data"]
                        continue
                    if m.get("object_id") != flow_id:
                        continue  # someone else's flow
                    step = m["data"]
                if not step:
                    continue
                step_key = json.dumps(step, sort_keys=True, default=str)
                if step_key in seen_steps:
                    continue
                seen_steps.add(step_key)
                kind = step.get("type")
                if kind == "finish":
                    flow["state"] = "connected"
                    return
                if kind == "abort":
                    logger.info("music oauth flow aborted (%s): %s", provider, step.get("reason"))
                    flow.update(state="failed", error="sign-in didn't complete")
                    return
                if kind == "external" and step.get("url") and flow.get("auth_url") != step["url"]:
                    flow["auth_url"] = step["url"]
                    flow["event"].set()  # release start_oauth to return the URL
                elif kind == "form" and not step.get("errors") and forms < music_service._FLOW_MAX_FORMS:
                    forms += 1
                    sub_id = "zoe-flow-" + secrets.token_hex(4)
                    pending_ids.add(sub_id)
                    await ws.send(json.dumps({"command": "config/flows/submit", "message_id": sub_id,
                                              "args": {"flow_id": flow_id, "values": {}}}))
                elif kind == "form":
                    logger.info("music oauth flow form not completable (%s): %s",
                                provider, step.get("errors") or step.get("step_id"))
                    break
        flow.update(state="failed", error="couldn't confirm the sign-in — please try again")
    except Exception as exc:  # noqa: BLE001 — a failed attempt must never crash the app
        logger.info("music oauth flow failed (%s): %s", provider, exc)
        flow.update(state="failed", error="couldn't reach the music engine")
    finally:
        if flow.get("state") != "connected":
            # MA may still hold the flow; a late callback must not complete an
            # attempt the phone was told had failed.
            await music_service._abort_setup_flow(flow_id)
        flow["event"].set()


async def _run_flow(oauth_id: str, provider: str) -> None:
    api = await music_service._ma_api_for_write()
    if api is None:
        _flows[oauth_id].update(state="failed", error="couldn't reach the music engine")
        _flows[oauth_id]["event"].set()
        return
    if api:
        await _run_setup_flow(oauth_id, provider)
        return
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
        # Reconnect IN PLACE when an instance already exists (e.g. re-authing a
        # degraded Spotify/Tidal/Deezer), or MA mints a duplicate provider and
        # leaves the broken original — the same instance_id fix setup_save uses.
        existing = await music_service.provider_instance_id(provider)
        saved = await music_service.save_provider(provider, values, instance_id=existing)
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
