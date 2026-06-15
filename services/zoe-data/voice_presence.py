"""Fast presence helpers for Zoe voice wake handling.

Wake acknowledgement is deliberately separate from reasoning: a wake event
should be acknowledged immediately and must not enter Skybridge, Pi, Graphify,
or the Zoe Agent path.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

_WAKE_TEXT_RE = re.compile(r"^\s*(?:hey|hi|hello)\s+zoe[.!?\s]*$", re.I)


def is_wake_text(text: str | None) -> bool:
    """Return True when a transcript is only a Zoe wake phrase."""
    return bool(_WAKE_TEXT_RE.match(str(text or "")))


def is_wake_payload(payload: Mapping[str, Any] | None) -> bool:
    """Return True for explicit wake payloads or text payload wake phrases."""
    if not isinstance(payload, Mapping):
        return False
    payload_type = str(payload.get("type") or "").strip().lower()
    if payload_type == "wake":
        return True
    if payload_type == "text":
        return is_wake_text(str(payload.get("message") or ""))
    return False


def wake_ack_phrase(env: Mapping[str, str] | None = None) -> str:
    """Resolve the optional short phrase Zoe can display/speak on wake."""
    values = env if env is not None else os.environ
    return str(values.get("ZOE_WAKE_ACK_PHRASE") or "").strip()


def wake_presence_events(*, ack_phrase: str = "") -> list[dict[str, Any]]:
    """Build the instant websocket events for a wake turn.

    The event list intentionally has no audio by default. Audio synthesis can be
    layered on by a caller after these events are sent, but the visible/listening
    acknowledgement must stay cheap and deterministic.
    """
    events: list[dict[str, Any]] = [
        {"type": "state", "state": "wake"},
    ]
    phrase = str(ack_phrase or "").strip()
    if phrase:
        events.append({"type": "transcript", "role": "zoe", "text": phrase})
    events.extend(
        [
            {"type": "state", "state": "listening"},
            {"type": "done"},
        ]
    )
    return events
