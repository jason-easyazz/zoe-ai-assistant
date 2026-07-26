"""Hermes provider mechanics for the chat router (W4-C3).

Hermes payload/header/stream plumbing cut verbatim out of ``routers/chat.py``
— no lane decisions, no router state. ``routers/chat.py`` re-exports the
callable names below (the ``voice_tts`` contract, applied to chat): existing
importers and test monkeypatches keep targeting ``routers.chat``. The
``_HERMES_*`` / ``_ZOE_SOUL_HERMES`` constants are deliberately NOT
re-exported (per ``docs/architecture/chat-split-and-typed-config-plan.md``).

Hermes is a retirement target: this module MOVES its plumbing without
extending it, so the eventual Hermes deletion PR is smaller and safer.
"""
import asyncio
import json
import logging
import os

from ag_ui.core import (
    CustomEvent,
    EventType,
    StateSnapshotEvent,
)

logger = logging.getLogger(__name__)


_HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642")
_HERMES_MODEL   = os.environ.get("HERMES_MODEL", "hermes-agent")
_HERMES_API_KEY = (
    os.environ.get("HERMES_API_KEY")
    or os.environ.get("API_SERVER_KEY")
    or ""
)

_ZOE_SOUL_HERMES = (
    "You are Zoe — a warm, curious, genuinely present AI companion. "
    "You know the person you're talking to well. You speak naturally, "
    "not as a task executor but as someone who cares about them. "
    "Draw on the context provided to give responses that feel personal and considered."
)


def _build_hermes_payload(
    message: str,
    *,
    username: str = "",
    portrait: str = "",
    facts: str = "",
    stream: bool,
) -> tuple[dict, int]:
    _zoe_compact = _load_zoe_self_compact_for_chat()
    _ctx_parts = [_ZOE_SOUL_HERMES]
    if _zoe_compact:
        _ctx_parts.append(f"[System context: {_zoe_compact}]")
    if username:
        _ctx_parts.append(f"[Talking to: {username}]")
    if portrait:
        _ctx_parts.append(f"[About this person: {portrait}]")
    if facts:
        _ctx_parts.append(f"[Memory context:\n{facts}]")
    _enhanced_message = "\n".join(_ctx_parts) + "\n\n" + message
    return (
        {
            "model": _HERMES_MODEL,
            "messages": [{"role": "user", "content": _enhanced_message}],
            "stream": stream,
        },
        len(_enhanced_message) // 4,
    )


def _hermes_progress_message(event_name: str, payload) -> tuple[str, str]:
    if not isinstance(payload, dict):
        text = str(payload or event_name)
        return "Hermes", text
    tool = (
        payload.get("tool")
        or payload.get("tool_name")
        or payload.get("name")
        or payload.get("skill")
        or "Hermes"
    )
    detail = (
        payload.get("message")
        or payload.get("detail")
        or payload.get("status")
        or payload.get("phase")
        or payload.get("step")
        or event_name
    )
    return str(tool), str(detail)


def _hermes_progress_events(event_name: str, payload) -> list:
    tool, detail = _hermes_progress_message(event_name, payload)
    label = f"{tool}: {detail}" if tool and tool != "Hermes" else detail
    return [
        StateSnapshotEvent(
            type=EventType.STATE_SNAPSHOT,
            snapshot={
                "status": "generating",
                "phase": "hermes_tool" if tool and tool != "Hermes" else "hermes",
                "model": "Hermes Agent",
                "detail": label,
                "event": event_name,
            },
        ),
        CustomEvent(
            name="zoe.run_log",
            value={
                "level": "info",
                "message": label,
                "source": "hermes",
                "phase": "hermes_tool" if tool and tool != "Hermes" else "hermes",
                "event": event_name,
                "payload": payload if isinstance(payload, dict) else {"value": payload},
            },
        ),
    ]


def _hermes_request_headers(*, session_id: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if _HERMES_API_KEY:
        headers["Authorization"] = f"Bearer {_HERMES_API_KEY}"
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id
    return headers


async def _iter_hermes_stream_events(
    message: str,
    session_id: str,
    user_id: str,
    *,
    username: str = "",
    portrait: str = "",
    facts: str = "",
):
    """Yield Hermes stream events; callers own AG-UI lifecycle and persistence."""
    import aiohttp

    full_text: list[str] = []
    _t0 = asyncio.get_event_loop().time()
    _hermes_error = False
    payload, _hermes_prompt_tokens = _build_hermes_payload(
        message,
        username=username,
        portrait=portrait,
        facts=facts,
        stream=True,
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_HERMES_API_URL}/v1/chat/completions",
                json=payload,
                headers=_hermes_request_headers(session_id=session_id),
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                sse_event = ""
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if line.startswith("event:"):
                        sse_event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    if sse_event and sse_event != "message":
                        try:
                            progress_payload = json.loads(data_str)
                        except json.JSONDecodeError:
                            progress_payload = {"message": data_str}
                        yield {"kind": "progress", "event": sse_event, "payload": progress_payload}
                        sse_event = ""
                        continue
                    sse_event = ""
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content", "")
                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue
                    if token:
                        full_text.append(token)
                        yield {"kind": "token", "text": token}
    except Exception as exc:
        _hermes_error = True
        error_token = f"\n\n*[Hermes Agent error: {exc}]*"
        full_text.append(error_token)
        yield {"kind": "token", "text": error_token}
    finally:
        # Log to llm_call_log so evolution_notice can include Hermes in health checks.
        # latency_ms < 0 is used as the error signal by evolution_notice.py.
        _latency_ms = int((asyncio.get_event_loop().time() - _t0) * 1000)
        _completion_tokens = len("".join(full_text)) // 4

        async def _log_hermes_call():
            try:
                from db_pool import get_db_ctx as _get_pg_db  # type: ignore[import]
                import uuid as _uuid, time as _time
                async with _get_pg_db() as _db:
                    await _db.execute(
                        """INSERT INTO llm_call_log
                           (id, agent_tier, model, session_id, user_id,
                            latency_ms, prompt_tokens, completion_tokens, estimated_cost_usd, ts)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                        _uuid.uuid4().hex, "hermes", _HERMES_MODEL,
                        session_id, user_id,
                        _latency_ms if not _hermes_error else -1,
                        _hermes_prompt_tokens, _completion_tokens,
                        0.0,
                        _time.time(),
                    )
            except Exception as _exc:
                logger.warning(
                    "chat: hermes llm_call_log insert failed for session=%s — "
                    "call NOT accounted: %s", session_id, _exc)

        asyncio.ensure_future(_log_hermes_call())


def _load_zoe_self_compact_for_chat() -> str:
    """Load compact Zoe self-description from file; falls back to empty string."""
    try:
        _p = os.path.expanduser("~/.zoe/zoe_self_compact.txt")
        with open(_p) as _f:
            return _f.read().strip()
    except Exception:
        return ""


async def _hermes_completion(
    message: str,
    session_id: str,
    user_id: str,
    *,
    username: str = "",
    portrait: str = "",
    facts: str = "",
) -> str:
    """Return a non-streaming Hermes response with Zoe context attached."""
    import aiohttp
    payload, _ = _build_hermes_payload(
        message,
        username=username,
        portrait=portrait,
        facts=facts,
        stream=False,
    )
    async with aiohttp.ClientSession() as _hses:
        async with _hses.post(
            f"{_HERMES_API_URL}/v1/chat/completions",
            json=payload,
            headers=_hermes_request_headers(),
            timeout=aiohttp.ClientTimeout(total=120),
        ) as _hr:
            _hr.raise_for_status()
            _hj = await _hr.json()
    return _hj.get("choices", [{}])[0].get("message", {}).get("content", "") or "(no response)"
