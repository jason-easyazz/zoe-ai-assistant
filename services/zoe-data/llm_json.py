"""Shared tolerance for the JSON a local model wraps in a markdown code fence.

The night 12B/4B jobs ask for bare JSON and sometimes answer with a fenced block anyway. Strip ONE
surrounding fence and nothing else: the parse after it stays strict (garbage is still garbage).
"""
from __future__ import annotations

import re

# A fence opening (optionally tagged json) and a closing fence at the very end.
_FENCE_OPEN = re.compile(r"^```[ \t]*(?:json)?[ \t]*\r?\n?", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\r?\n?[ \t]*```\s*$")


def strip_code_fence(text: str) -> str:
    """``text`` without a surrounding json / plain markdown fence; unchanged when it is not fenced.

    A reply cut off by max_tokens has an opening fence and no closing one: the opening is still
    removed, so the caller's salvage sees the JSON itself.
    """
    s = str(text or "").strip()
    if not s.startswith("```"):
        return s
    s = _FENCE_OPEN.sub("", s, count=1)
    return _FENCE_CLOSE.sub("", s, count=1).strip()
