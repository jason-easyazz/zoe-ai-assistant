"""
Derive short, human-readable chat session titles from message content.
"""
from __future__ import annotations

import re
from typing import Optional

_MAX_TITLE_LEN = 56
# Greetings / fillers that make a poor sidebar label — allow assistant reply to rename.
_WEAK_TITLE = re.compile(
    r"^(hi|hey|hello|hiya|yo|sup|thanks?|thank\s+you|ok(ay)?|yes|no|yh|yea|yeah|u\s+there)"
    r"[!?.…]*\s*$",
    re.IGNORECASE,
)


def title_is_weak(title: Optional[str]) -> bool:
    t = (title or "").strip()
    if not t or t.lower() == "new chat":
        return True
    if _WEAK_TITLE.match(t):
        return True
    if len(t) <= 10:
        return True
    return False


def derive_session_title(text: str, *, max_chars: int = _MAX_TITLE_LEN) -> str:
    """
    Turn the first line / opening of a message into a sidebar-friendly title.
    Strips markdown/code noise, collapses whitespace, truncates on a word boundary.
    """
    if not text or not str(text).strip():
        return "Chat"

    t = str(text)
    # Drop fenced code blocks (titles shouldn't be "```python…")
    t = re.sub(r"```[\s\S]*?```", " ", t)
    t = re.sub(r"`[^`\n]+`", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"#{1,6}\s*", "", t)
    t = re.sub(r"\*+|_+", "", t)
    t = t.replace("\r\n", "\n").replace("\r", "\n")

    line = t.strip().split("\n", 1)[0].strip()
    # Trim leading assistant filler
    line = re.sub(
        r"^(sure[!,.]?|of course[!,.]?|okay[!,.]?|ok[!,.]?|great question[!,.]?|absolutely[!,.]?)\s+",
        "",
        line,
        flags=re.IGNORECASE,
    )

    # Prefer first clause if the line is very long
    if len(line) > max_chars + 15 and ". " in line[: max_chars + 40]:
        first = line.split(". ", 1)[0].strip()
        if len(first) >= 12:
            line = first

    line = re.sub(r"\s+", " ", line).strip()
    line = line.rstrip(",;:—-")

    if len(line) > max_chars:
        cut = line[: max_chars + 1]
        sp = cut.rfind(" ", 0, max_chars)
        if sp > max_chars // 3:
            line = cut[:sp].rstrip(",.;:!?—-") + "…"
        else:
            line = cut[:max_chars].rstrip() + "…"

    return line or "Chat"
