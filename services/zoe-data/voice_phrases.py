"""One voice for every tier: the fixed lines Zoe speaks outside the brain, read from ``voice_phrases.json``.

The fast tiers, the brain fallbacks, the error paths and the empty-transcript path used to carry their own hard-coded wording,
so Zoe sounded like several robots. The words now live in one data file, drafted once in the brain's manner (the rules are in the
file's ``_doc``); this module only loads it and picks a variant.

The pick is DETERMINISTIC: ``sha256(phrase_id + key)`` over the variants, where ``key`` is the turn id (or session id). The same
turn always hears the same line, so a replay is stable; different turns spread across the variants. No key means variant 0.

``say`` never raises on the speaking path: a missing file, a missing id or a bad template returns ``LAST_RESORT`` and logs, because
these lines are exactly the ones spoken when something else already failed. ``tests/test_voice_phrases.py`` pins that every id used
in code exists, that sentinel ids have one variant, and that the scanned paths carry no new hard-coded line.
"""
from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

PATH = Path(__file__).with_name("voice_phrases.json")
#: spoken only when the phrase file itself is broken; kept in the manner (no exclamation, no "Please try again")
LAST_RESORT = "Sorry, something went wrong there."


@lru_cache(maxsize=1)
def _table() -> dict[str, dict[str, Any]]:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    return dict(data.get("phrases") or {})


def ids() -> tuple[str, ...]:
    return tuple(_table())


def entry(phrase_id: str) -> dict[str, Any]:
    """The raw file entry (``variants`` plus flags such as ``sentinel`` / ``brain_fallback``); KeyError when absent."""
    return _table()[phrase_id]


def variants(phrase_id: str) -> tuple[str, ...]:
    return tuple(entry(phrase_id).get("variants") or ())


def pick_index(phrase_id: str, key: Optional[str], n: int) -> int:
    """Variant index for ``key``: stable across processes and runs (sha256, not ``hash()``)."""
    if n <= 1 or not key:
        return 0
    digest = hashlib.sha256(f"{phrase_id}\x00{key}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % n


def say(phrase_id: str, key: Optional[str] = None, **params: Any) -> str:
    """The line for ``phrase_id``, variant chosen by ``key``, ``{field}`` filled from ``params``. Never raises."""
    try:
        options = variants(phrase_id)
        if not options:
            raise KeyError(phrase_id)
        text = options[pick_index(phrase_id, None if key is None else str(key), len(options))]
        return text.format(**params) if params else text
    except Exception as exc:  # noqa: BLE001 - this IS the fallback path
        logger.warning("voice_phrases: %r unusable (%s); speaking the last-resort line", phrase_id, exc)
        return LAST_RESORT


def brain_fallback_texts() -> tuple[str, ...]:
    """Every line that means "the brain did not answer" (for verdicts and struggle detection)."""
    try:
        return tuple(v for e in _table().values() if e.get("brain_fallback") for v in e.get("variants") or ())
    except Exception:  # noqa: BLE001
        return (LAST_RESORT,)
