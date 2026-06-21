"""Fast presence helpers for Zoe voice wake handling.

Wake acknowledgement is deliberately separate from reasoning: a wake event
should be acknowledged immediately and must not enter Skybridge, Pi, Graphify,
or the Zoe Agent path.
"""

from __future__ import annotations

import base64
from datetime import datetime
import mimetypes
import os
import re
import threading
from pathlib import Path
from typing import Any, Mapping

_DEFAULT_PROCESSING_ACK_PHRASES = "Let me check.|One moment.|I will check that."
_WAKE_TEXT_RE = re.compile(r"^\s*(?:hey|hi|hello)\s+zoe[.!?\s]*$", re.I)
_AUDIO_CACHE: dict[str, Any] = {}
_VARIANT_CURSOR = 0
_PROCESSING_ACK_CURSOR = 0
_VARIANT_LOCK = threading.Lock()


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


def _split_variants(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split("|") if item.strip()]


def _env_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def wake_ack_phrases(env: Mapping[str, str] | None = None) -> list[str]:
    """Resolve configured wake phrases, preserving single-phrase compatibility."""
    values = env if env is not None else os.environ
    phrases = _split_variants(values.get("ZOE_WAKE_ACK_PHRASES"))
    if phrases:
        return phrases
    phrase = str(values.get("ZOE_WAKE_ACK_PHRASE") or "").strip()
    return [phrase] if phrase else []


def wake_ack_audio_paths(env: Mapping[str, str] | None = None) -> list[str]:
    """Resolve configured wake audio paths, preserving single-path compatibility."""
    values = env if env is not None else os.environ
    paths = _split_variants(values.get("ZOE_WAKE_ACK_AUDIO_PATHS"))
    if paths:
        return paths
    audio_path = str(values.get("ZOE_WAKE_ACK_AUDIO_PATH") or "").strip()
    return [audio_path] if audio_path else []


def wake_ack_variant_labels(env: Mapping[str, str] | None = None) -> list[str]:
    """Resolve optional pipe-aligned labels for time-aware wake variants."""
    values = env if env is not None else os.environ
    return [label.lower() for label in _split_variants(values.get("ZOE_WAKE_ACK_VARIANT_LABELS"))]


def processing_ack_phrases(env: Mapping[str, str] | None = None) -> list[str]:
    """Resolve short transition phrases for slow voice turns."""
    values = env if env is not None else os.environ
    phrases = _split_variants(values.get("ZOE_PROCESSING_ACK_PHRASES"))
    if phrases:
        return phrases
    phrase = str(values.get("ZOE_PROCESSING_ACK_PHRASE") or "").strip()
    if phrase:
        return [phrase]
    if _env_bool(values.get("ZOE_PROCESSING_ACK_DEFAULT_ENABLED"), default=True):
        return _split_variants(_DEFAULT_PROCESSING_ACK_PHRASES)
    return []


def wake_ack_time_period(now: datetime | None = None) -> str:
    """Return the coarse local period used for deterministic wake acks."""
    current = now if now is not None else datetime.now().astimezone()
    hour = current.hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 24:
        return "evening"
    return "night"


def _label_matches(label: str, period: str) -> bool:
    return period in {part.strip() for part in label.split(",") if part.strip()}


def _select_wake_ack_index(labels: list[str], variant_count: int, now: datetime | None = None) -> int:
    global _VARIANT_CURSOR
    period = wake_ack_time_period(now)
    candidates = [idx for idx, label in enumerate(labels[:variant_count]) if _label_matches(label, period)]
    if not candidates:
        candidates = [idx for idx, label in enumerate(labels[:variant_count]) if _label_matches(label, "default")]
    with _VARIANT_LOCK:
        if candidates:
            selected = candidates[_VARIANT_CURSOR % len(candidates)]
        else:
            selected = _VARIANT_CURSOR % variant_count
        _VARIANT_CURSOR += 1
    return selected


def wake_ack_phrase(env: Mapping[str, str] | None = None) -> str:
    """Resolve the optional short phrase Zoe can display/speak on wake."""
    phrases = wake_ack_phrases(env)
    return phrases[0] if phrases else ""


def wake_ack_variant(
    env: Mapping[str, str] | None = None,
    *,
    index: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pick one configured wake acknowledgement variant.

    Multiple variants are index-aligned across ZOE_WAKE_ACK_PHRASES and
    ZOE_WAKE_ACK_AUDIO_PATHS. Optional ZOE_WAKE_ACK_VARIANT_LABELS entries can
    prefer local-time variants such as morning, afternoon, evening, or default.
    """
    phrases = wake_ack_phrases(env)
    audio_paths = wake_ack_audio_paths(env)
    labels = wake_ack_variant_labels(env)
    variant_count = max(len(phrases), len(audio_paths), 1)
    if index is None:
        selected = _select_wake_ack_index(labels, variant_count, now)
    else:
        selected = index % variant_count
    phrase = phrases[selected] if selected < len(phrases) else ""
    audio_path = audio_paths[selected] if selected < len(audio_paths) else ""
    label = labels[selected] if selected < len(labels) else ""
    return {"phrase": phrase, "audio_path": audio_path, "index": selected, "label": label}


def processing_ack_variant(
    env: Mapping[str, str] | None = None,
    *,
    index: int | None = None,
) -> dict[str, Any]:
    """Pick a short acknowledgement for slow voice processing.

    These phrases are for perceived latency only. They do not imply completion,
    memory writes, tool success, or authority to execute anything.
    """
    global _PROCESSING_ACK_CURSOR
    values = env if env is not None else os.environ
    phrases = processing_ack_phrases(values)
    audio_paths = _split_variants(values.get("ZOE_PROCESSING_ACK_AUDIO_PATHS"))
    if not audio_paths:
        audio_path = str(values.get("ZOE_PROCESSING_ACK_AUDIO_PATH") or "").strip()
        audio_paths = [audio_path] if audio_path else []
    variant_count = max(len(phrases), len(audio_paths), 1)
    if index is None:
        with _VARIANT_LOCK:
            selected = _PROCESSING_ACK_CURSOR % variant_count
            _PROCESSING_ACK_CURSOR += 1
    else:
        selected = index % variant_count
    phrase = phrases[selected] if selected < len(phrases) else ""
    audio_path = audio_paths[selected] if selected < len(audio_paths) else ""
    return {"phrase": phrase, "audio_path": audio_path, "index": selected}


def wake_ack_audio_payload(env: Mapping[str, str] | None = None, *, audio_path: str | None = None) -> dict[str, Any] | None:
    """Return cached wake acknowledgement audio from a pre-generated file.

    This is intentionally file/cache only. Live TTS belongs outside the wake
    hot path because a wake acknowledgement should be available immediately.
    """
    values = env if env is not None else os.environ
    if audio_path is None:
        paths = wake_ack_audio_paths(values)
        audio_path = paths[0] if paths else ""
    audio_path = str(audio_path or "").strip()
    if not audio_path:
        return None

    path = Path(audio_path).expanduser()
    try:
        stat = path.stat()
    except OSError:
        return None

    cache_key = str(path)
    cache_sig = (stat.st_mtime_ns, stat.st_size)
    cached = _AUDIO_CACHE.get(cache_key)
    if cached and cached.get("signature") == cache_sig:
        return dict(cached["payload"])

    try:
        raw = path.read_bytes()
    except OSError:
        return None

    content_type = mimetypes.guess_type(str(path))[0] or "audio/wav"
    payload = {
        "audio_base64": base64.b64encode(raw).decode("ascii"),
        "content_type": content_type,
        "source": "cached_wake_ack",
    }
    _AUDIO_CACHE[cache_key] = {"signature": cache_sig, "payload": payload}
    return dict(payload)


def processing_ack_audio_payload(
    env: Mapping[str, str] | None = None,
    *,
    audio_path: str | None = None,
) -> dict[str, Any] | None:
    """Return cached processing acknowledgement audio from a file only."""
    payload = wake_ack_audio_payload(env, audio_path=audio_path)
    if payload:
        payload["source"] = "cached_processing_ack"
    return payload


def wake_presence_events(*, ack_phrase: str = "", ack_audio: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Build the instant websocket events for a wake turn.

    Audio is only included when a caller provides cached/pre-generated bytes.
    Live synthesis must stay out of this path so the acknowledgement remains
    cheap and deterministic.
    """
    events: list[dict[str, Any]] = [
        {"type": "state", "state": "wake"},
    ]
    phrase = str(ack_phrase or "").strip()
    if phrase:
        events.append({"type": "transcript", "role": "zoe", "text": phrase})
    if ack_audio:
        audio_b64 = str(ack_audio.get("audio_base64") or "").strip()
        if audio_b64:
            events.append({
                "type": "audio",
                "audio_base64": audio_b64,
                "content_type": str(ack_audio.get("content_type") or "audio/wav"),
                "source": str(ack_audio.get("source") or "cached_wake_ack"),
            })
    events.extend(
        [
            {"type": "state", "state": "listening"},
            {"type": "done"},
        ]
    )
    return events


def wake_ack_events(env: Mapping[str, str] | None = None, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Build wake presence events from the selected configured variant."""
    variant = wake_ack_variant(env, now=now)
    audio = wake_ack_audio_payload(env, audio_path=variant.get("audio_path"))
    return wake_presence_events(ack_phrase=str(variant.get("phrase") or ""), ack_audio=audio)


def processing_ack_event(env: Mapping[str, str] | None = None, *, index: int | None = None) -> dict[str, Any] | None:
    """Build one data-channel event for the slow-turn intent buffer."""
    variant = processing_ack_variant(env, index=index)
    phrase = str(variant.get("phrase") or "").strip()
    audio = processing_ack_audio_payload(env, audio_path=str(variant.get("audio_path") or ""))
    if not phrase and not audio:
        return None
    event: dict[str, Any] = {"type": "voice:processing_ack", "text": phrase, "source": "intent_buffer"}
    if audio and audio.get("audio_base64"):
        event["audio_base64"] = audio["audio_base64"]
        event["content_type"] = str(audio.get("content_type") or "audio/wav")
        event["audio_source"] = str(audio.get("source") or "cached_processing_ack")
    return event
