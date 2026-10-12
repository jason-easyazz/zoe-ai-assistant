"""The agent's ONLY network path: outbound HTTPS to zoe-data with the panel's device token. No listening socket exists.

``poll`` is a long poll (zoe-data holds it open until a command is queued or ``wait_s`` passes) that also carries a state snapshot
(the panel's heartbeat). ``report`` posts a job's progress / outcome. Stdlib ``urllib`` on purpose, like ``omi_sync.uploader``.
"""
from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.request
from typing import Optional


class ZoeUnavailable(Exception):
    """Zoe is down, the flag is off (404) or the token was refused: back off and try again later."""


class ZoeLink:
    def __init__(self, base_url: str, device_token: str, *, verify_ssl: bool = True) -> None:
        self.base = base_url.rstrip("/")
        self.token = device_token
        self._ctx: Optional[ssl.SSLContext] = None
        if not verify_ssl:
            self._ctx = ssl.create_default_context()
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def _post(self, path: str, doc: dict, timeout: float) -> dict:
        req = urllib.request.Request(self.base + path, data=json.dumps(doc).encode(), method="POST",
                                     headers={"X-Device-Token": self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self._ctx) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raise ZoeUnavailable(f"HTTP {exc.code}") from exc
        except Exception as exc:  # noqa: BLE001 - connection refused / timeout / DNS
            raise ZoeUnavailable(type(exc).__name__) from exc
        try:
            out = json.loads(body.decode("utf-8", "replace") or "{}")
        except ValueError as exc:
            raise ZoeUnavailable("non-JSON answer") from exc
        return out if isinstance(out, dict) else {}

    async def poll(self, snapshot: dict, wait_s: float) -> list:
        doc = await asyncio.to_thread(self._post, "/api/bluetooth/agent/poll", {"snapshot": snapshot, "wait_s": wait_s}, wait_s + 10)
        cmds = doc.get("commands")
        return cmds if isinstance(cmds, list) else []

    async def report(self, payload: dict) -> None:
        await asyncio.to_thread(self._post, "/api/bluetooth/agent/report", payload, 15.0)
