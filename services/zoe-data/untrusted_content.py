"""Quoted third-party text inside a member's own message: zoe-data's half of the brain's untrusted fence (2026-10-19).

A producer that passes someone else's words to the brain on a member's behalf (the Telegram bot, for a FORWARDED message) fences
them: a fixed "data, not instructions" preamble, a BEGIN line, the neutralised text, an END line. The Flue brain spots the BEGIN
line in the turn's newest message and refuses every write and web search for that turn
(``labs/flue-zoe-brain-2x/src/untrusted-content.ts``). This module keeps such a message OFF zoe-data's own deterministic intent
fast path (``routers/chat.py``), which would otherwise execute a command written inside the quote before the brain ever saw it.

A forged BEGIN line only routes the sender's own message to the (tainted) brain: it can never widen anything.

MIRROR: the markers are byte-for-byte copies of the brain's ``UNTRUSTED_QUOTE_MARKERS`` and the Telegram bot's
(``labs/flue-zoe-telegram-2x/src/untrusted.ts``); ``tests/unit/test_untrusted_fence_markers.py`` pins all three equal.
"""
from __future__ import annotations

from typing import Optional

UNTRUSTED_QUOTE_MARKERS = {
    "telegram": ("<<<BEGIN UNTRUSTED FORWARDED MESSAGE>>>", "<<<END UNTRUSTED FORWARDED MESSAGE>>>"),
    "omi": ("<<<BEGIN UNTRUSTED PENDANT TRANSCRIPT>>>", "<<<END UNTRUSTED PENDANT TRANSCRIPT>>>"),
}


def untrusted_source_in(text: Optional[str]) -> Optional[str]:
    """The source whose fence BEGIN line (a whole line) appears in ``text``, else None."""
    if not text:
        return None
    lines = {line.strip() for line in str(text).splitlines()}
    for source, (begin, _end) in UNTRUSTED_QUOTE_MARKERS.items():
        if begin in lines:
            return source
    return None
