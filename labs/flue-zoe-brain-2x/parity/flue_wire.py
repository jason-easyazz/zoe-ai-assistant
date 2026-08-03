"""The Flue 2.x sidecar wire, in one place.

WHY THIS MODULE EXISTS. Three parity gates (`parity_check`, `tool_reliability`,
`recall_reliability`) each hand-rolled the same request against the beta wire:

    POST /agents/zoe/<sid>?wait=result      body: {"message": "<text>"}
    -> 200 {"result": {"text": "..."}}

Every part of that is gone on Flue 2.x, and not gently:

  * ``?wait=result`` is not merely dropped — the runtime REJECTS any ``wait``
    query param, any value, with ``InvalidRequestError``: "Agent prompts are
    fire-and-forget and do not support ``?wait=result``. Await completion with
    the SDK client's ``wait()``, or read the conversation stream (GET this URL)."
  * the body is no longer ``{"message": <string>}``. It is a DeliveredMessage at
    the TOP LEVEL: ``{"kind": "user", "body": "<text>"}``. Note this contradicts
    the upstream migration guide, which shows the object nested under a
    ``message`` key; that shape is refused with HTTP 400. Measured, not inferred.
  * the response is a 202 admission — ``{streamUrl, offset, submissionId, uid}``
    — not a result.

So there is no synchronous "ask and get the answer" call left. Getting a reply
means following the admission: read the conversation stream, or use the SDK's
``wait()``.

WHAT THIS USES, AND WHY IT IS THE RIGHT REFERENCE. Rather than poll the
conversation-read endpoint, `ask` uses the sidecar's OWN Seam-A NDJSON streaming
mode (``Accept: application/x-ndjson``), which upgrades the 202 into the live
text-delta + sentinel stream and terminates with ``{"done": true}`` or
``{"error": ...}``. That is deliberate on two counts: it restores a single
request/response shape for the gates, and it exercises the exact path
``services/zoe-data/zoe_flue_client.py`` already uses for voice — so this
function doubles as the reference implementation for the Phase-2 client change.

Set ZOE_BRAIN_TOKEN when the sidecar is token-gated (it is, unless
ZOE_BRAIN_OPEN=1); the gate is fail-closed, so an unset token means 401.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

TOOL_SENTINEL_PREFIX = "__TOOL__:"
THINKING_SENTINEL_PREFIX = "__THINKING__:"


def base_url() -> str:
    """Sidecar base URL. Override for a throwaway instance — NEVER default to :3578 in a test."""
    return os.environ.get("ZOE_BRAIN_URL", "http://127.0.0.1:3578")


def _headers(stream: bool) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if stream:
        headers["Accept"] = "application/x-ndjson"
    token = os.environ.get("ZOE_BRAIN_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def user_message(text: str) -> dict:
    """The 2.x DeliveredMessage body. Fields are TOP LEVEL, not nested."""
    return {"kind": "user", "body": text}


def admit(sid: str, text: str, timeout: float = 120.0, base: str | None = None) -> dict:
    """Fire-and-forget admission. Returns the 202 receipt (streamUrl/submissionId/uid/offset)."""
    url = f"{base or base_url()}/agents/zoe/{sid}"
    req = urllib.request.Request(
        url, data=json.dumps(user_message(text)).encode(), headers=_headers(stream=False)
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ask(
    sid: str, text: str, timeout: float = 120.0, base: str | None = None
) -> tuple[str, list[str], float]:
    """Send a turn and read it to completion over the NDJSON stream.

    Returns ``(reply_text, sentinels, elapsed_ms)``. ``sentinels`` holds the raw
    ``__TOOL__:``/``__THINKING__:`` chunks in arrival order, so a gate can assert
    on which tools actually ran without a second request.

    Raises RuntimeError when the turn terminated with an error line, so a gate
    can never silently score a failed turn as an empty answer.
    """
    url = f"{base or base_url()}/agents/zoe/{sid}"
    req = urllib.request.Request(
        url, data=json.dumps(user_message(text)).encode(), headers=_headers(stream=True)
    )
    parts: list[str] = []
    sentinels: list[str] = []
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line:
                continue
            chunk = json.loads(line)
            if isinstance(chunk, dict):
                if chunk.get("error"):
                    raise RuntimeError(f"brain turn failed: {chunk['error']}")
                if chunk.get("done"):
                    break
                continue
            if chunk.startswith(TOOL_SENTINEL_PREFIX) or chunk.startswith(
                THINKING_SENTINEL_PREFIX
            ):
                sentinels.append(chunk)
            else:
                parts.append(chunk)
    return "".join(parts).strip(), sentinels, (time.time() - t0) * 1000.0


def tool_names(sentinels: list[str]) -> list[str]:
    """Tool names from ``__TOOL__`` phase=start sentinels, in order, deduped."""
    seen: list[str] = []
    for sentinel in sentinels:
        if not sentinel.startswith(TOOL_SENTINEL_PREFIX):
            continue
        try:
            payload = json.loads(sentinel[len(TOOL_SENTINEL_PREFIX) :])
        except json.JSONDecodeError:
            continue
        if payload.get("phase") == "start" and payload.get("name") not in seen:
            seen.append(payload["name"])
    return seen
